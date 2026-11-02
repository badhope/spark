"""对话端点：非流式 /api/chat 与 SSE 流式 /api/chat/stream（含 /test slash）。

从单文件 ``_handlers.py`` 按域拆出，行为不变；导入路径经
``spark.web._handlers`` facade 保持稳定。
"""

from __future__ import annotations

import base64 as _b64mod
import json as _json
import logging
import socket
import threading

from spark.models import ImageRef
from spark.slash import handle_test, parse_slash
from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state
from spark.web.sse import SSE_DISCONNECT_ERRORS, _SSESession
from spark.web.state import _generate_session_meta_async
from spark.web.utils import _event_payload

logger = logging.getLogger(__name__)


def _handle_chat(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        handler._json(400, {"error": "prompt required"})
        return
    if state.loop is None:
        handler._json(500, {"error": "agent not ready"})
        return
    try:
        with state.turn_lock:
            # Drive the turn on the shared runtime loop: the durable turn
            # checkpointer's SQLite connection is bound to that loop, so a
            # per-request asyncio.run() would fail with "Future attached to a
            # different loop" as soon as crash recovery is enabled.
            events = list(state.loop.iter_turn_sync(prompt))
    except Exception as exc:  # aqg: top-level boundary
        handler._json(500, {"error": str(exc)})
        return
    handler._json(200, {"events": [_event_payload(e) for e in events]})


def _parse_image_payloads(body: dict) -> list[dict]:
    """校验并收集 data:image/ base64 项；非法项记 debug 日志后跳过。"""
    images: list[dict] = []
    for item in body.get("images") or []:
        if not isinstance(item, str) or not item.startswith("data:image/"):
            continue
        try:
            head, _, b64part = item.partition(",")
            media_type = (
                head.removeprefix("data:").split(";")[0] or "image/png"
            )
            _b64mod.b64decode(b64part, validate=True)
            images.append({"media_type": media_type, "data": b64part})
        except Exception:  # aqg: top-level boundary
            logger.debug("图片 base64 校验失败，跳过该项", exc_info=True)
            continue
    return images


def _send_sse_headers(handler: HandlerBase) -> bool:
    """写出 SSE 响应头；客户端已断开时返回 False。"""
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "close")
        handler.end_headers()
    except SSE_DISCONNECT_ERRORS:
        handler.close_connection = True
        return False
    return True


def _run_test_slash(state, stream: _SSESession, slash) -> tuple[object | None, bool]:
    """执行 /test；返回 (slash_result, fatal)。fatal=True 表示流已收尾，调用方直接返回。"""
    if not stream.submit_payload(
        {"type": "text_delta", "text": "⏳ 正在运行测试…\n"}
    ):
        return None, True
    try:
        return handle_test(state.sandbox, slash.args), False
    except Exception as exc:  # noqa: BLE001 — slash 失败不拖垮整条 SSE  # aqg: top-level boundary
        logger.warning("/test 执行失败: %s", exc, exc_info=True)
        stream.submit_payload(
            {"type": "turn_error", "text": f"/test 执行失败: {exc}"}
        )
        stream.finish()
        return None, True


def _expand_turn_prompt(state, prompt: str) -> str:
    """H5：自定义命令展开（.spark/commands/*.md）；展开失败回落原文。"""
    from spark.slash import expand_command

    try:
        expanded = expand_command(str(state.sandbox.root), prompt)
    except Exception:  # aqg: top-level boundary
        logger.debug("自定义命令展开失败", exc_info=True)
        return prompt
    return expanded or prompt


def _dispatch_turn(
    state,
    stream: _SSESession,
    turn_loop,
    turn_prompt: str,
    image_refs,
    slash_result,
    collect_event,
    web_enabled: bool,
) -> None:
    """串行执行本轮对话；断连在取锁前后各查一次，退出时复位 busy 与工具门。"""
    if stream.disconnected:
        return
    with state.turn_lock:
        if stream.disconnected:
            return
        if turn_loop is not state.loop:
            stream.submit_payload({"type": "turn_error", "text": "settings changed; retry the request"})
            return
        state.busy = True
        # Always apply the per-turn gate. It used to be skipped whenever a
        # tool_allowlist was active, so the switch silently did nothing for
        # users running an approval profile with a tool whitelist.
        turn_loop.registry.disabled_tools = (
            set() if web_enabled else {"web_search", "web_fetch"}
        )
        try:
            if slash_result is not None:
                stream.submit_payload(
                    {"type": "text_delta", "text": slash_result.summary + "\n"}
                )
                if slash_result.ok or not slash_result.feedback:
                    stream.submit_payload(
                        {"type": "turn_end", "text": slash_result.summary}
                    )
                else:
                    # 回灌失败输出给 agent loop，让其读 traceback 自行修正
                    for event in turn_loop.iter_turn_sync(
                        slash_result.feedback, images=None
                    ):
                        collect_event(event)
                        if not stream.submit(event):
                            break
                    stream.submit_payload(
                        {"type": "turn_end", "text": slash_result.summary}
                    )
            else:
                for event in turn_loop.iter_turn_sync(
                    turn_prompt, images=image_refs
                ):
                    collect_event(event)
                    if not stream.submit(event):
                        break
        finally:
            state.busy = False
            turn_loop.registry.disabled_tools = set()


def _stream_failure_exit(
    stream: _SSESession, exc: Exception, cancel_turn, *, cancel_on_disconnect: bool
) -> None:
    """异常/断连收尾：已断连→abort（可选取消本轮）；否则回灌 turn_error。"""
    if stream.disconnected:
        if cancel_on_disconnect:
            cancel_turn()
        stream.abort()
        return
    if stream.submit_payload({"type": "turn_error", "text": str(exc)}):
        stream.finish()
    else:
        stream.abort()


def _spawn_memory_recorder(state, prompt: str, turn_text: str, tool_summary: str) -> None:
    """记忆抽取在后台线程执行，失败只告警。"""
    if not (
        state.cfg.memory.enabled
        and state.cfg.memory.auto_extract
        and state.memory is not None
        and state.cfg.provider.name != "mock"
    ):
        return

    def run_memory() -> None:
        try:
            state.memory.record_turn(
                user_text=prompt,
                assistant_text=turn_text,
                tool_summary=tool_summary,
                session_id=state.session_id,
            )
        except Exception:  # aqg: top-level boundary
            logger.warning("SSE 后台 memory 写入失败", exc_info=True)

    threading.Thread(target=run_memory, daemon=True).start()


def _handle_chat_stream(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    prompt = str(body.get("prompt") or "").strip()
    if not prompt and not body.get("images"):
        handler._json(400, {"error": "prompt required"})
        return
    if state.loop is None:
        handler._json(500, {"error": "agent not ready"})
        return

    images = _parse_image_payloads(body)
    image_refs = [ImageRef.model_validate(i) for i in images] or None
    if not prompt and image_refs:
        prompt = "请分析这些图片。"

    if not _send_sse_headers(handler):
        return

    turn_loop = state.loop
    approver = state.approver

    def close_stream() -> None:
        handler.close_connection = True
        connection = getattr(handler, "connection", None)
        if connection is None:
            return
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except SSE_DISCONNECT_ERRORS:
            try:
                connection.close()
            except SSE_DISCONNECT_ERRORS:
                pass

    def _sse_secrets() -> tuple[str, ...]:
        """SSE 出口脱敏用的已知密钥集（控制令牌 + 模型 API Key）。"""
        api_key = ""
        try:
            api_key = state.cfg.provider.api_key or ""
        except AttributeError:
            pass
        token = getattr(state, "control_token", "") or ""
        return tuple(s for s in (token, api_key) if s)

    def cancel_turn() -> None:
        if turn_loop is not None:
            turn_loop.cancel()
        if approver is not None:
            approver.cancel()

    stream = _SSESession(
        handler.wfile,
        connection=handler.connection,
        on_disconnect=cancel_turn,
        close_connection=close_stream,
        secrets=_sse_secrets(),
    )
    stream.start()

    memory_collector: dict = {"text": "", "tools": []}

    def collect_event(event) -> None:
        if event.type == "text_delta" and event.text:
            memory_collector["text"] += event.text
        elif event.type == "turn_end" and event.text:
            memory_collector["text"] = event.text
        elif event.type == "tool_end" and event.tool_call:
            memory_collector["tools"].append(
                f"{event.tool_call.name}: {_json.dumps(event.result.payload if event.result else {}, ensure_ascii=False)[:200]}"
            )

    # P1-3：后端识别原始 "/test ..." 文本（前端最小实现=直接发送原始 slash），
    # 跑仓库测试；失败时把含 traceback 的输出回灌进同一 agent loop 修正。
    slash = parse_slash(prompt)
    slash_result = None
    if slash is not None and slash.name == "test":
        slash_result, fatal = _run_test_slash(state, stream, slash)
        if fatal:
            return

    turn_prompt = _expand_turn_prompt(state, prompt)

    try:
        _dispatch_turn(
            state, stream, turn_loop, turn_prompt, image_refs, slash_result,
            collect_event, bool(body.get("web_search", True)),
        )
        if stream.disconnected:
            return
        if not stream.finish():
            return
        turn_text = memory_collector["text"]
        if turn_text:
            _generate_session_meta_async(state, prompt, turn_text)
        _spawn_memory_recorder(
            state, prompt, turn_text, " | ".join(memory_collector["tools"])
        )
    except SSE_DISCONNECT_ERRORS as exc:
        _stream_failure_exit(stream, exc, cancel_turn, cancel_on_disconnect=True)
    except Exception as exc:  # aqg: top-level boundary
        _stream_failure_exit(stream, exc, cancel_turn, cancel_on_disconnect=False)
    finally:
        if stream.disconnected:
            stream.abort()


_MAX_INLINE_SHELL_COMMAND = 4000


def _handle_shell(handler: HandlerBase, body: dict) -> None:
    """`!cmd` 内联 shell：用户在输入框直接敲的命令，等价于自己开终端跑。

    复用 run_shell 工具的完整执行核（workdir 限定 + check_shell 策略 +
    bwrap 隔离 + 输出上限），但不走审批 —— 命令是用户亲手输入的，
    与 PTY 终端同一信任级别；不进对话历史，避免污染模型上下文。
    """
    import threading

    from spark.tools.shell import RunShellArgs, run_shell

    state = _route_state(handler)
    command = str(body.get("command") or "").strip()
    if not command:
        handler._json(400, {"error": "command required"})
        return
    if len(command) > _MAX_INLINE_SHELL_COMMAND:
        handler._json(400, {"error": "command too long"})
        return
    try:
        timeout = int(body.get("timeout_sec") or state.cfg.agent.shell_timeout_sec)
    except (TypeError, ValueError):
        timeout = state.cfg.agent.shell_timeout_sec
    timeout = max(1, min(timeout, 3600))
    result: dict = {}

    def work() -> None:
        payload = run_shell(
            state.sandbox,
            RunShellArgs(command=command),
            timeout_sec=timeout,
            max_output_chars=state.cfg.agent.max_output_chars,
        )
        result.update(payload.model_dump())

    thread = threading.Thread(target=work, daemon=True, name="inline-shell")
    thread.start()
    thread.join(timeout=timeout + 10)
    if thread.is_alive():
        handler._json(504, {"error": "shell still running", "command": command})
        return
    if not result:
        handler._json(500, {"error": "shell produced no result"})
        return
    handler._json(
        200,
        {
            "ok": bool(result.get("ok")),
            "result": result.get("payload") or {},
        },
    )
