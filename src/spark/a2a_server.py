"""A2A (Agent2Agent) protocol server — 最小 v1 实现。

A2A 是 Linux Foundation 的 agent-to-agent 开放协议（2026 v1.0 规范，
JSON-RPC 2.0 over HTTP）。Spark 以 A2A Server 形态把自己暴露给其他
agent 编排器（orchestrator），与本已有的 MCP server（agent-to-tool）、
ACP server（agent-to-editor）各占一层，互不重叠。

实现范围（诚实边界）：

* Agent Card 发现：``/.well-known/agent-card.json``（兼答 ``/agent.json``）
* ``message/send``：非流式——跑一个 turn，返回 completed Task + artifact
* ``tasks/get``：查任务状态（进程内登记，按 contextId 分会话）
* ``ping``：连通性探测
* 未声明 ``streaming`` 能力：``message/stream`` 返回 -32601，客户端会
  降级到 message/send（规范允许的能力协商行为）

安全默认：只监听 127.0.0.1；审批模式显式传参（无人值守服务默认
full-auto，调用方自负其责——与 ``spark web --approval`` 同一语义）。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from spark.config import SparkConfig
from spark.errors import SparkError

logger = logging.getLogger(__name__)

A2A_PROTOCOL_VERSION = "0.3.0"

# contextId → 会话的 LRU 上限：A2A 客户端可能为每个上下文开新会话，
# 不设上限的服务器等于一个无界内存泄漏。
_MAX_CONTEXTS = 64


class A2AError(Exception):
    """JSON-RPC 可报告的 A2A 错误（code/message/data 直接进响应）。"""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class A2AState:
    """进程内任务与会话登记簿。

    每个 contextId 对应一个独立会话（独立 AgentLoop），同一 contextId 的
    请求串行执行——A2A 允许并发，但同一会话的 turn 状态绝不能互相踩。
    """

    def __init__(self, workdir: Path, cfg: SparkConfig) -> None:
        self._workdir = workdir
        self._cfg = cfg
        self._loops: OrderedDict[str, dict] = OrderedDict()
        self._tasks: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ loops
    def _loop_for(self, context_id: str) -> dict:
        """取（或创建）contextId 对应的 loop 会话；LRU 上限内复用。"""
        with self._lock:
            entry = self._loops.get(context_id)
            if entry is not None:
                self._loops.move_to_end(context_id)
                return entry
            while len(self._loops) >= _MAX_CONTEXTS:
                _, oldest = self._loops.popitem(last=False)
                try:
                    oldest["store"].close()
                except Exception:  # noqa: BLE001 - 清理失败不应影响新会话
                    logger.debug("关闭最旧 A2A 会话失败", exc_info=True)

            from spark.config import default_home
            from spark.core.loop import AgentLoop
            from spark.providers.factory import create_provider
            from spark.sandbox import WorkdirSandbox
            from spark.store import SessionStore
            from spark.tools.registry import ToolContext, ToolRegistry

            store = SessionStore(default_home() / "sessions.db")
            session_id = store.create_session(self._workdir, self._cfg.provider.model)
            provider = create_provider(self._cfg)
            sandbox = WorkdirSandbox(self._workdir, self._cfg)
            registry = ToolRegistry(ToolContext(sandbox=sandbox, config=self._cfg))
            loop = AgentLoop(
                workdir=self._workdir,
                cfg=self._cfg,
                provider=provider,
                registry=registry,
                store=store,
                session_id=session_id,
            )
            entry = {"loop": loop, "store": store}
            self._loops[context_id] = entry
            return entry

    # ------------------------------------------------------------------ tasks
    def _validate_message(self, params: dict) -> tuple[str, str, str]:
        """校验 message/send|stream 参数，返回 (prompt, context_id, task_id)。"""
        message = params.get("message")
        if not isinstance(message, dict):
            raise A2AError(-32602, "Invalid params: message is required")
        parts = message.get("parts") or []
        texts = [
            p.get("text", "")
            for p in parts
            if isinstance(p, dict) and p.get("kind", "text") == "text"
        ]
        prompt = "\n".join(t for t in texts if t).strip()
        if not prompt:
            raise A2AError(-32602, "Invalid params: message needs a text part")
        context_id = str(
            message.get("contextId") or message.get("context_id") or "default"
        )
        task_id = str(message.get("taskId") or f"task-{int(time.time() * 1000)}")
        return prompt, context_id, task_id

    def _register_task(self, task_id: str, context_id: str) -> None:
        with self._lock:
            self._tasks[task_id] = {
                "id": task_id,
                "contextId": context_id,
                "status": {"state": "working", "timestamp": _now()},
            }

    def _finish_task(self, task_id: str, state_name: str, output: str = "") -> None:
        with self._lock:
            task = self._tasks.setdefault(
                task_id, {"id": task_id, "contextId": "default"}
            )
            task["status"] = {"state": state_name, "timestamp": _now()}
            if state_name == "completed":
                task["artifacts"] = [
                    {
                        "artifactId": f"{task_id}-out",
                        "parts": [{"kind": "text", "text": output}],
                    }
                ]

    def stream_message(self, params: dict, emit: Callable[[dict], None]) -> None:
        """message/stream 共用执行路径：边跑 turn 边回调 A2A 流事件帧。

        ``emit`` 收到的每个 dict 都是一帧 ``SendStreamingResponse`` 的 result
        （TaskStatusUpdateEvent / TaskArtifactUpdateEvent，A2A 规范的
        ``kind`` 判别字段）。``send_message`` 复用本方法收集文本。
        """
        prompt, context_id, task_id = self._validate_message(params)
        loop = self._loop_for(context_id)["loop"]
        self._register_task(task_id, context_id)
        artifact_id = f"{task_id}-out"
        emitted = 0

        def status_event(state_name: str, final: bool) -> dict:
            return {
                "kind": "status-update",
                "taskId": task_id,
                "contextId": context_id,
                "status": {"state": state_name, "timestamp": _now()},
                "final": final,
            }

        def artifact_event(text: str, last: bool, append: bool) -> dict:
            return {
                "kind": "artifact-update",
                "taskId": task_id,
                "contextId": context_id,
                "artifact": {
                    "artifactId": artifact_id,
                    "parts": [{"kind": "text", "text": text}],
                },
                "append": append,
                "lastChunk": last,
            }

        emit(status_event("working", final=False))
        text_parts: list[str] = []
        failed = False

        try:
            import asyncio

            async def _run() -> None:
                nonlocal emitted
                async for event in loop.iter_turn(prompt):
                    chunk = ""
                    if event.type == "text_delta" and event.text:
                        chunk = event.text
                    elif event.type == "turn_error":
                        chunk = f"\n[error] {event.text}"
                    if chunk:
                        emit(artifact_event(chunk, last=False, append=emitted > 0))
                        text_parts.append(chunk)
                        emitted += 1

            asyncio.run(_run())
        except Exception:  # noqa: BLE001 - 任何失败都转成 failed 终态
            logger.warning("A2A turn 执行失败", exc_info=True)
            failed = True
        output = "".join(text_parts).strip()
        if emitted:
            # 终帧用全量替换（append=False, lastChunk=True）：客户端增量渲染
            # 之后收到权威全文，无需担心分块边界。
            emit(artifact_event(output, last=True, append=False))
        emit(status_event("failed" if failed else "completed", final=True))
        self._finish_task(task_id, "failed" if failed else "completed", output)

    def send_message(self, request_id: Any, params: dict) -> dict:
        """message/send：跑一个 turn，返回 A2A Task 对象（复用流式执行路径）。"""
        buffer: dict = {}

        def collect(frame: dict) -> None:
            if frame.get("kind") == "artifact-update":
                buffer["text"] = buffer.get("text", "") + frame["artifact"]["parts"][0][
                    "text"
                ]
            elif frame.get("kind") == "status-update" and frame.get("final"):
                buffer["task_id"] = frame["taskId"]
                buffer["context_id"] = frame["contextId"]
                buffer["state"] = frame["status"]["state"]

        self.stream_message(params, collect)
        return _task_response(
            request_id,
            str(buffer.get("task_id", "")),
            str(buffer.get("context_id", "default")),
            str(buffer.get("state", "failed")),
            str(buffer.get("text", "")),
        )

    def get_task(self, request_id: Any, params: dict) -> dict:
        task_id = str(params.get("id") or "")
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            raise A2AError(-32001, f"Task not found: {task_id}")
        return _rpc_result(request_id, task)

    def close(self) -> None:
        for _, entry in list(self._loops.items()):
            try:
                entry["store"].close()
            except Exception:  # noqa: BLE001
                pass


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _rpc_result(request_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(request_id: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": err}


def _task_response(
    request_id: Any, task_id: str, context_id: str, state: str, output: str = ""
) -> dict:
    task: dict = {
        "id": task_id,
        "contextId": context_id,
        "status": {"state": state, "timestamp": _now()},
        "kind": "task",
    }
    if state == "completed":
        task["artifacts"] = [
            {"artifactId": f"{task_id}-out", "parts": [{"kind": "text", "text": output}]}
        ]
    return _rpc_result(request_id, task)


def _spark_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("spark-agent")
    except PackageNotFoundError:
        from spark import __version__

        return __version__ or "development build"


def agent_card(cfg: SparkConfig, url: str) -> dict:
    """A2A Agent Card（能力自描述）。url 用真实绑定地址。"""
    return {
        "name": "spark",
        "description": (
            "Spark — local-first AI coding agent with auditable approval, "
            "sandboxing and rollback. Bring any OpenAI-compatible endpoint."
        ),
        "url": url,
        "version": _spark_version(),
        "protocolVersion": A2A_PROTOCOL_VERSION,
        "capabilities": {"streaming": True, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "coding",
                "name": "coding agent",
                "description": "Read, edit and run code in a sandboxed workdir",
                "tags": ["coding", "tools", "sandbox"],
            }
        ],
    }


def make_handler(card_fn: Callable[[], dict], state: A2AState) -> type[BaseHTTPRequestHandler]:
    """构建 HTTP handler。card_fn 惰性取卡（绑定端口后 url 才是真实的）。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "spark-a2a"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            logger.debug("a2a %s", fmt % args)

        def _send_json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - http.server 命名
            path = self.path.split("?")[0]
            if path in ("/.well-known/agent-card.json", "/agent.json"):
                self._send_json(200, card_fn())
            elif path == "/health":
                self._send_json(200, {"ok": True})
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            payload: dict = {}
            request_id: Any = None
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                parsed = json.loads(raw or b"{}")
                if not isinstance(parsed, dict) or parsed.get("jsonrpc") != "2.0":
                    self._send_json(200, _rpc_error(None, -32600, "Invalid Request"))
                    return
                payload = parsed
                request_id = payload.get("id")
                method = str(payload.get("method") or "")
                params = payload.get("params") or {}
                if method == "message/send":
                    result = state.send_message(request_id, params)
                elif method == "tasks/get":
                    result = state.get_task(request_id, params)
                elif method == "ping":
                    result = _rpc_result(request_id, {})
                elif method == "message/stream":
                    # SSE 流式：TaskStatusUpdateEvent / TaskArtifactUpdateEvent
                    # 逐帧下发；A2AError（参数错误）仍以普通 JSON-RPC 错误返回。
                    self._send_sse(request_id, state, params)
                    return
                elif method == "tasks/pushNotificationConfig/set":
                    # 未声明的能力按规范拒绝。
                    self._send_json(
                        200,
                        _rpc_error(
                            request_id, -32601, f"Capability not supported: {method}"
                        ),
                    )
                    return
                else:
                    self._send_json(
                        200, _rpc_error(request_id, -32601, "Method not found")
                    )
                    return
                self._send_json(200, result)
            except A2AError as exc:
                self._send_json(
                    200, _rpc_error(request_id, exc.code, exc.message, exc.data)
                )
            except SparkError as exc:
                self._send_json(200, _rpc_error(request_id, -32000, str(exc)))
            except json.JSONDecodeError:
                self._send_json(200, _rpc_error(None, -32700, "Parse error"))
            except Exception:  # noqa: BLE001 - 兜底不让连接挂死
                logger.exception("A2A 请求处理失败")
                self._send_json(200, _rpc_error(request_id, -32603, "Internal error"))

        def _send_sse(self, request_id: Any, a2a_state: A2AState, params: dict) -> None:
            """message/stream：SSE 逐帧下发，每帧一条 SendStreamingResponse。"""
            # 先校验参数再开流：A2AError 要以普通 JSON-RPC 错误返回，
            # 不能写进已经发出的 SSE 流里。
            a2a_state._validate_message(params)

            def write_frame(result: dict) -> None:
                frame = json.dumps(
                    {"jsonrpc": "2.0", "id": request_id, "result": result},
                    ensure_ascii=False,
                )
                self.wfile.write(f"data: {frame}\n\n".encode("utf-8"))
                self.wfile.flush()

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                a2a_state.stream_message(params, write_frame)
            except A2AError as exc:
                # 流开始前的参数错误：退回单帧 JSON-RPC 错误（规范允许）。
                self._send_json(
                    200, _rpc_error(request_id, exc.code, exc.message, exc.data)
                )
            except (BrokenPipeError, ConnectionResetError):
                logger.debug("A2A 流式客户端提前断开")
            except Exception:  # noqa: BLE001
                logger.exception("A2A 流式处理失败")

    return Handler


def serve_a2a(
    workdir: Path,
    cfg: SparkConfig,
    host: str = "127.0.0.1",
    port: int = 0,
) -> tuple[ThreadingHTTPServer, str]:
    """启动 A2A server；返回 (server, base_url)。调用方负责 shutdown/close。

    测试里传 port=0 由系统选口；生产建议显式 --port。
    """
    state = A2AState(workdir, cfg)
    holder: dict = {}

    def card_fn() -> dict:
        srv = holder.get("server")
        if srv is not None:
            bound_host, bound_port = srv.server_address[:2]
            return agent_card(cfg, f"http://{bound_host}:{bound_port}")
        return agent_card(cfg, f"http://{host}:{port}")

    handler = make_handler(card_fn, state)
    server = ThreadingHTTPServer((host, port), handler)
    server.a2a_state = state  # type: ignore[attr-defined]  # 调用方关闭时顺带释放会话
    holder["server"] = server
    bound_host, bound_port = server.server_address[:2]
    host_str = bound_host.decode() if isinstance(bound_host, bytes) else str(bound_host)
    return server, f"http://{host_str}:{bound_port}"
