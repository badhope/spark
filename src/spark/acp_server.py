"""ACP (Agent Client Protocol) surface —— Spark 作为 ACP agent 被 Zed 等 IDE 拉起。

协议事实来源：https://agentclientprotocol.com（SDK：agent-client-protocol）。
本模块是 L4 surface：组合 L1-L3（providers/tools/store/core），禁止被它们反向依赖。
stdout 是 JSON-RPC 通道，一切日志只走 stderr（logging 默认行为）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

from acp import PROTOCOL_VERSION, AgentSideConnection, stdio_streams
from acp.exceptions import RequestError
from acp.schema import (
    AcpMcpServer,
    AgentCapabilities,
    AllowedOutcome,
    HttpMcpServer,
    Implementation,
    InitializeResponse,
    LoadSessionResponse,
    McpServerStdio,
    NewSessionResponse,
    PermissionOption,
    PromptCapabilities,
    PromptResponse,
    RequestPermissionResponse,
    SessionMode,
    SessionModeState,
    SseMcpServer,
    ToolCallProgress,
    ToolCallStart,
)

from spark import __version__
from spark.acp_compat import (
    plan_entry,
    text_block,
    tool_content,
    update_agent_message,
    update_agent_thought,
    update_plan,
    update_user_message,
)
from spark.asyncutil import run_coroutine_sync
from spark.config import McpServerConfig, McpTransportConfig, SparkConfig
from spark.core.loop import AgentLoop, Approver
from spark.models import ApprovalDecision, ApprovalRequest, ImageRef, ToolResult
from spark.providers.factory import create_provider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.mcp_bridge import McpBridge
from spark.tools.registry import ToolContext, ToolRegistry

logger = logging.getLogger(__name__)

__all__ = ["SparkAcpAgent", "SparkAcpSession", "build_session", "run_stdio", "DEFAULT_SESSION_MODES"]

# 会话模式（审批六档的 ACP 投影；set_mode 落到 cfg.agent.approval）
DEFAULT_SESSION_MODES: list[SessionMode] = [
    SessionMode(id="suggest", name="Suggest", description="写/执行工具逐项询问后执行"),
    SessionMode(id="auto-edit", name="Auto Edit", description="编辑文件自动执行，shell 逐项询问"),
    SessionMode(id="full-auto", name="Full Auto", description="全部工具自动执行（沙箱仍生效）"),
    SessionMode(id="plan-only", name="Plan Only", description="只读调研并产出计划，写/执行被禁用"),
    SessionMode(id="approve-plan", name="Approve Plan", description="计划批准后按 auto-edit 语义执行"),
]

# Spark 工具名 → ACP ToolKind（未命中走缺省 execute）
_TOOL_KIND: dict[str, str] = {
    "read_file": "read",
    "glob": "search",
    "grep": "search",
    "list_dir": "read",
    "write_file": "edit",
    "apply_patch": "edit",
    "notebook_edit": "edit",
    "run_shell": "execute",
    "fetch": "fetch",
    "update_plan": "think",
    "task": "think",
    "diagnostics": "read",
    "mcp_resources": "read",
    "mcp_prompts": "read",
    "mcp_call": "execute",
}

_TOOL_OUTPUT_MAX_CHARS = 16_000

_APPROVAL_OPTIONS = [
    PermissionOption(option_id="allow_once", name="Allow once", kind="allow_once"),
    PermissionOption(option_id="allow_always", name="Always allow", kind="allow_always"),
    PermissionOption(option_id="reject_once", name="Reject", kind="reject_once"),
]


def tool_kind(name: str) -> str:
    """Spark 工具名映射为 ACP ToolKind（未知工具按执行类处理）。"""
    return _TOOL_KIND.get(name, "execute")


class _ToolIdMap:
    """spark 内部 call id ↔ ACP tool_call_id 的映射（审批桥与事件桥共用）。

    register 幂等：同一 spark call id 永远映射到同一个 ACP id。这一点在
    LangGraph custom stream 下至关重要——审批请求在节点内先于 tool_start
    事件被客户端消费，映射必须在两侧都可见且稳定。
    """

    def __init__(self) -> None:
        self.to_acp: dict[str, str] = {}

    def acp_id(self, spark_call_id: str) -> str:
        return self.to_acp.get(spark_call_id, spark_call_id)

    def register(self, spark_call_id: str) -> str:
        acp_id = self.to_acp.get(spark_call_id)
        if acp_id is None:
            acp_id = f"tool_{uuid.uuid4().hex[:12]}"
            self.to_acp[spark_call_id] = acp_id
        return acp_id


class SparkAcpSession:
    """一次 ACP 会话：持有一个 AgentLoop 与工具调用 id 映射。"""

    def __init__(
        self,
        *,
        session_id: str,
        workdir: Path,
        cfg: SparkConfig,
        store: SessionStore,
        sandbox: WorkdirSandbox,
        loop: AgentLoop,
        mcp_bridge: McpBridge | None = None,
    ) -> None:
        self.session_id = session_id
        self.workdir = workdir
        self.cfg = cfg
        self.store = store
        self.sandbox = sandbox
        self.loop = loop
        self.mcp_bridge = mcp_bridge
        self.tool_ids = _ToolIdMap()
        self.busy = False

    def cancel(self) -> None:
        self.loop.cancel()

    async def close(self) -> None:
        if self.mcp_bridge is not None:
            try:
                await self.mcp_bridge.close()
            except Exception:  # pragma: no cover
                logger.debug("mcp bridge close failed", exc_info=True)


def build_session(
    *,
    session_id: str,
    workdir: Path,
    cfg: SparkConfig,
    store: SessionStore,
    approver: Approver | None,
    provider_factory: Callable[[SparkConfig], Any] | None = None,
) -> SparkAcpSession:
    """组装 AgentLoop（与 web/tui 同一构建路径，不跨 surface import）。"""
    sandbox = WorkdirSandbox(workdir, cfg)
    tool_ctx = ToolContext(sandbox=sandbox, config=cfg)
    registry = ToolRegistry(tool_ctx)
    mcp_bridge: McpBridge | None = None
    if cfg.mcp_servers:
        bridge = McpBridge()
        run_coroutine_sync(bridge.start(cfg.mcp_servers, registry))
        mcp_bridge = bridge
        tool_ctx.mcp_call = bridge.call
        tool_ctx.mcp_bridge = bridge
    provider = (provider_factory or create_provider)(cfg)
    loop = AgentLoop(
        workdir=workdir,
        cfg=cfg,
        provider=provider,
        registry=registry,
        store=store,
        session_id=session_id,
        approver=approver if cfg.agent.approval != "full-auto" else None,
    )
    if mcp_bridge is not None:
        # P2：server 反向 sampling 以当前 Provider 为大脑
        mcp_bridge.set_sampling_handler(provider)
    return SparkAcpSession(
        session_id=session_id,
        workdir=workdir,
        cfg=cfg,
        store=store,
        sandbox=sandbox,
        loop=loop,
        mcp_bridge=mcp_bridge,
    )


# ---------------------------------------------------------------------------
# 审批桥：Spark ApprovalRequest → ACP session/request_permission
# ---------------------------------------------------------------------------

def make_acp_approver(conn: AgentSideConnection, session: SparkAcpSession) -> Approver:
    """构造 bridging approver：等 client 的 outcome，映射回 ApprovalDecision。

    与 stream_turn 共用 ``session.tool_ids``，保证审批里的 tool_call_id
    与 tool_start 通知里的同一个。
    """

    async def approver(request: ApprovalRequest) -> ApprovalDecision:
        # LangGraph custom stream 下 tool_start 事件的消费可能晚于本回调，
        # 因此这里主动 ensure 注册（幂等），保证审批与事件流共用同一 ACP id。
        tool_call = tool_call_update(
            title=request.summary or request.tool_call.name,
            kind=tool_kind(request.tool_call.name),
            status="pending",
            raw_input=request.tool_call.arguments,
            result=None,
            acp_id=session.tool_ids.register(request.tool_call.id),
            diff=request.diff,
        )
        try:
            response: RequestPermissionResponse = await conn.request_permission(
                session_id=session.session_id,
                tool_call=tool_call,
                options=_APPROVAL_OPTIONS,
            )
        except Exception:
            # 通道断了（client 退出/取消）：按拒绝处理，保住 agent 循环不悬挂
            logger.warning("request_permission 通道异常，按 deny 处理", exc_info=True)
            return ApprovalDecision(tool_call_id=request.tool_call.id, action="deny")
        outcome = response.outcome
        option_id = getattr(outcome, "option_id", None)
        if isinstance(outcome, AllowedOutcome) and option_id in {"allow_once", "allow_always"}:
            action: Literal["allow", "allow_always"] = (
                "allow_always" if option_id == "allow_always" else "allow"
            )
            return ApprovalDecision(tool_call_id=request.tool_call.id, action=action)
        return ApprovalDecision(tool_call_id=request.tool_call.id, action="deny")

    return approver


# ---------------------------------------------------------------------------
# 工具调用展示：TurnEvent → ToolCallStart / ToolCallProgress
# ---------------------------------------------------------------------------

def _tool_output_text(result: ToolResult | None) -> str:
    if result is None:
        return ""
    payload = result.payload
    for key in ("output", "stdout", "content", "text", "error", "reason"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value[:_TOOL_OUTPUT_MAX_CHARS]
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)[:_TOOL_OUTPUT_MAX_CHARS]
    except (TypeError, ValueError):
        return ""


def tool_call_update(
    *,
    title: str,
    kind: str,
    status: str,
    raw_input: dict[str, Any] | None,
    result: ToolResult | None,
    acp_id: str,
    diff: str | None = None,
) -> ToolCallProgress:
    """构造 ToolCallProgress（tool_end 用）；审批桥复用其字段构造。"""
    content: list[Any] = []
    body = diff if diff else _tool_output_text(result)
    if body:
        content.append(tool_content(text_block(body)))
    return ToolCallProgress(
        session_update="tool_call_update",
        tool_call_id=acp_id,
        title=title,
        kind=kind,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        content=content or None,
        raw_input=raw_input,
        raw_output=result.payload if result is not None else None,
    )


def plan_entries(steps: list[dict[str, Any]]) -> list[Any]:
    """registry.plan（title/status）→ ACP PlanEntry（content/priority/status）。"""
    entries: list[Any] = []
    for step in steps:
        title = str(step.get("title", "")) if isinstance(step, dict) else str(step)
        if not title:
            continue
        status = str(step.get("status", "pending")) if isinstance(step, dict) else "pending"
        if status not in {"pending", "in_progress", "completed"}:
            status = "pending"
        entries.append(
            plan_entry(
                title,
                status=cast(Any, status),
            )
        )
    return entries


def map_error_stop_reason(message: str, code: str | None = None) -> str:
    """Spark turn_error 文案/错误码 → ACP StopReason。"""
    lowered = message.lower()
    if code == "tool_loop_limit" or "max_tool_rounds" in message:
        return "max_turn_requests"
    if code in {"tool_repeat_loop", "circuit_breaker"}:
        return "max_turn_requests"
    if "cancelled" in lowered:
        return "cancelled"
    if "budget exceeded" in lowered:
        return "max_tokens"
    return "refusal"


# ---------------------------------------------------------------------------
# 事件泵：Spark TurnEvent → ACP session/update 通知
# ---------------------------------------------------------------------------

async def stream_turn(
    session: SparkAcpSession,
    conn: AgentSideConnection,
    user_text: str,
    images: list[ImageRef] | None,
) -> str:
    """运行一轮对话：TurnEvent → session/update；返回 PromptResponse 的 stopReason。"""
    sid = session.session_id
    stop_reason = "end_turn"
    # ACP owns its own loop and its approver must call back into the connection
    # on *that* loop, so the turn runs here directly instead of being marshalled
    # through the shared runtime loop (which deadlocked request_permission).
    async for event in session.loop.iter_turn(user_text, images=images):
        if event.type == "text_delta" and event.text:
            await conn.session_update(sid, update_agent_message(text_block(event.text)))
        elif event.type == "reasoning_delta" and event.text:
            await conn.session_update(sid, update_agent_thought(text_block(event.text)))
        elif event.type == "tool_start" and event.tool_call is not None:
            call = event.tool_call
            acp_id = session.tool_ids.register(call.id)
            await conn.session_update(
                sid,
                ToolCallStart(
                    session_update="tool_call",
                    tool_call_id=acp_id,
                    title=call.name,
                    kind=tool_kind(call.name),  # type: ignore[arg-type]
                    status="pending",
                    raw_input=call.arguments,
                ),
            )
        elif event.type == "tool_end" and event.tool_call is not None:
            call = event.tool_call
            await conn.session_update(
                sid,
                tool_call_update(
                    title=call.name,
                    kind=tool_kind(call.name),
                    status="completed" if (event.result and event.result.ok) else "failed",
                    raw_input=call.arguments,
                    result=event.result,
                    acp_id=session.tool_ids.acp_id(call.id),
                ),
            )
        elif event.type == "plan" and isinstance(event.data, dict):
            await conn.session_update(
                sid, update_plan(plan_entries(event.data.get("steps") or []))
            )
        elif event.type == "turn_end":
            stop_reason = "end_turn"
            break
        elif event.type == "turn_error":
            message = event.text or "turn failed"
            error_payload = (event.data or {}).get("error") if isinstance(event.data, dict) else None
            code = error_payload.get("code") if isinstance(error_payload, dict) else None
            stop_reason = map_error_stop_reason(message, code)
            if stop_reason == "refusal":
                # 真错误以文本形式透出，客户端可见
                await conn.session_update(
                    sid, update_agent_message(text_block(f"Error: {message}"))
                )
            break
    return stop_reason


# ---------------------------------------------------------------------------
# prompt 内容块 → Spark 输入
# ---------------------------------------------------------------------------

def prompt_to_input(blocks: list[Any]) -> tuple[str, list[ImageRef]]:
    """把 ACP ContentBlock 列表折叠成 (text, images)。"""
    parts: list[str] = []
    images: list[ImageRef] = []
    for block in blocks:
        btype = getattr(block, "type", None)
        if btype == "text":
            parts.append(block.text)
        elif btype == "image":
            images.append(ImageRef(media_type=block.mime_type, data=block.data))
        elif btype == "resource_link":
            parts.append(
                f"<resource_link uri=\"{block.uri}\">{getattr(block, 'name', '') or ''}</resource_link>"
            )
        elif btype == "resource":
            resource = getattr(block, "resource", None)
            text = getattr(resource, "text", None)
            uri = getattr(resource, "uri", "") if resource is not None else ""
            if text:
                parts.append(f"<resource uri=\"{uri}\">\n{text}\n</resource>")
            else:
                parts.append(f"<resource uri=\"{uri}\"/>")
    return "\n".join(p for p in parts if p), images


def acp_mcp_to_config(server: Any) -> McpServerConfig | None:
    """ACP mcpServers 参数 → Spark McpServerConfig；acp 代理型暂不支持返回 None。"""
    name = server.name
    if isinstance(server, McpServerStdio):
        env: dict[str, str] = {}
        for item in server.env or []:
            if getattr(item, "name", None) and getattr(item, "value", None) is not None:
                env[item.name] = item.value
        # Spark stdio MCP 以 shell 方式拉起；env 通过 env(1) 前缀注入命令字符串
        command = server.command
        if env:
            exported = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
            command = f"env {exported} {command}"
        return McpServerConfig(name=name, command=command, args=list(server.args or []))
    stype = getattr(server, "type", None)
    if stype == "http" and isinstance(server, HttpMcpServer):
        return McpServerConfig(
            name=name,
            transport="streamable_http",
            http=McpTransportConfig(
                url=server.url, headers={h.name: h.value for h in server.headers or []}
            ),
        )
    if stype == "sse" and isinstance(server, SseMcpServer):
        return McpServerConfig(
            name=name,
            transport="sse",
            http=McpTransportConfig(
                url=server.url, headers={h.name: h.value for h in server.headers or []}
            ),
        )
    if isinstance(server, AcpMcpServer):
        logger.warning("ACP 代理型 MCP server 暂不支持，已忽略: %s", name)
    return None


# ---------------------------------------------------------------------------
# Agent 实现
# ---------------------------------------------------------------------------

class SparkAcpAgent:
    """ACP Agent 协议实现（SDK 的 Agent Protocol 是鸭子类型，无需显式继承）。"""

    def __init__(
        self,
        *,
        workdir: Path,
        cfg: SparkConfig,
        store: SessionStore,
        provider_factory: Callable[[SparkConfig], Any] | None = None,
    ) -> None:
        self._workdir = workdir
        self._base_cfg = cfg
        self._store = store
        self._provider_factory = provider_factory
        self._conn: AgentSideConnection | None = None
        self._sessions: dict[str, SparkAcpSession] = {}

    # -- lifecycle ---------------------------------------------------------

    def on_connect(self, conn: AgentSideConnection) -> None:
        self._conn = conn

    def _require_conn(self) -> AgentSideConnection:
        if self._conn is None:
            raise RequestError(-32603, "agent not connected")
        return self._conn

    # -- ACP methods ---------------------------------------------------------

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: Any = None,
        client_info: Any = None,
        **_: Any,
    ) -> InitializeResponse:
        del client_capabilities, client_info
        # 客户端版本可用且不高于我们支持的最高版 → 沿用之；否则回落到我们支持的最高版
        negotiated = (
            protocol_version
            if 1 <= protocol_version <= PROTOCOL_VERSION
            else PROTOCOL_VERSION
        )
        return InitializeResponse(
            protocol_version=negotiated,
            agent_capabilities=AgentCapabilities(
                load_session=True,
                prompt_capabilities=PromptCapabilities(image=True),
            ),
            agent_info=Implementation(name="spark", version=__version__),
            auth_methods=[],
        )

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[Any] | None = None,
        **_: Any,
    ) -> NewSessionResponse:
        conn = self._require_conn()
        workdir = self._resolve_cwd(cwd)
        cfg = self._session_cfg(mcp_servers)
        session_id = self._store.create_session(workdir, cfg.provider.model, title="acp session")
        await self._build_and_register(conn, session_id, workdir, cfg)
        return NewSessionResponse(session_id=session_id, modes=mode_state(cfg))

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        **_: Any,
    ) -> LoadSessionResponse | None:
        conn = self._require_conn()
        workdir = self._resolve_cwd(cwd)
        record = self._store.get_session(session_id)
        if record is None or Path(str(record.get("workdir", ""))) != workdir:
            raise RequestError(-32602, f"session not found or cwd mismatch: {session_id}")
        cfg = self._session_cfg(mcp_servers)
        await self._build_and_register(conn, session_id, workdir, cfg)
        await self._replay_history(session_id, conn)
        return LoadSessionResponse(modes=mode_state(cfg))

    async def prompt(
        self,
        session_id: str,
        prompt: list[Any],
        **_: Any,
    ) -> PromptResponse:
        conn = self._require_conn()
        session = self._sessions.get(session_id)
        if session is None:
            raise RequestError(-32602, f"unknown session: {session_id}")
        if session.busy:
            raise RequestError(-32603, f"session is busy: {session_id}")
        text, images = prompt_to_input(prompt)
        if not text and not images:
            raise RequestError(-32602, "empty prompt")
        session.busy = True
        try:
            stop_reason = await stream_turn(session, conn, text, images or None)
        finally:
            session.busy = False
        return PromptResponse(stop_reason=stop_reason)  # type: ignore[arg-type]

    async def cancel(self, session_id: str, **_: Any) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.cancel()

    # -- helpers ---------------------------------------------------------

    async def _build_and_register(
        self,
        conn: AgentSideConnection,
        session_id: str,
        workdir: Path,
        cfg: SparkConfig,
    ) -> SparkAcpSession:
        session = build_session(
            session_id=session_id,
            workdir=workdir,
            cfg=cfg,
            store=self._store,
            approver=None,
            provider_factory=self._provider_factory,
        )
        # 审批桥与事件泵共享同一张 id 映射（session.tool_ids）
        if cfg.agent.approval != "full-auto":
            session.loop.approver = make_acp_approver(conn, session)
        old = self._sessions.get(session_id)
        if old is not None:
            old.cancel()
            await old.close()
        self._sessions[session_id] = session
        return session

    def _resolve_cwd(self, cwd: str) -> Path:
        path = Path(cwd).expanduser()
        if not path.is_absolute():
            raise RequestError(-32602, f"cwd must be absolute: {cwd}")
        if not path.exists():
            raise RequestError(-32602, f"cwd does not exist: {cwd}")
        return path.resolve()

    def _session_cfg(self, mcp_servers: list[Any] | None) -> SparkConfig:
        cfg = self._base_cfg
        if not mcp_servers:
            return cfg
        extra = [c for c in (acp_mcp_to_config(s) for s in mcp_servers) if c is not None]
        if not extra:
            return cfg
        cfg = cfg.model_copy(deep=True)
        cfg.mcp_servers = list(cfg.mcp_servers) + extra
        return cfg

    async def _replay_history(self, session_id: str, conn: AgentSideConnection) -> None:
        """load_session 回放历史：user/assistant 文本消息逐条通知回客户端。"""
        for message in self._store.load_messages(session_id):
            role = message.role
            content = message.content or ""
            if not content:
                continue
            if role == "user":
                await conn.session_update(session_id, update_user_message(text_block(content)))
            elif role == "assistant":
                await conn.session_update(session_id, update_agent_message(text_block(content)))


def mode_state(cfg: SparkConfig) -> SessionModeState:
    return SessionModeState(
        current_mode_id=str(cfg.agent.approval),
        available_modes=list(DEFAULT_SESSION_MODES),
    )


# ---------------------------------------------------------------------------
# stdio 入口
# ---------------------------------------------------------------------------

async def _serve(workdir: Path, cfg: SparkConfig) -> None:
    reader, writer = await stdio_streams()
    store = SessionStore(_default_sessions_db())
    agent = SparkAcpAgent(workdir=workdir, cfg=cfg, store=store)
    # listening=False：由 listen() 统一驱动收循环（默认 True 会双读 stdin）
    conn = AgentSideConnection(agent, writer, reader, listening=False)
    agent.on_connect(conn)
    try:
        await conn.listen()
    finally:
        store.close()


def _default_sessions_db() -> Path:
    from spark.config import default_home

    return default_home() / "sessions.db"


def run_stdio(workdir: Path, cfg: SparkConfig) -> None:
    """spark acp 入口：stdio 上跑 JSON-RPC，直到 client 断开。"""
    import sys

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    asyncio.run(_serve(Path(workdir).resolve(), cfg))


# 引用保活：acp_mcp_to_config 对未知类型的兜底分支（协议演进时显式处理）
_ = (AcpMcpServer,)
