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
    slash = parse_slash(prompt)
    if slash is not None and slash.name != "test":
        # 非流式端点的 plan/build/side/goal：鸭子类型一个 Sink 复用流式分发。
        class _Sink:
            def __init__(self) -> None:
                self.events: list[dict] = []

            def submit_payload(self, payload: dict) -> bool:
                self.events.append(payload)
                return True

            def finish(self) -> bool:
                return True

        sink = _Sink()
        if _dispatch_slash(state, sink, slash):  # type: ignore[arg-type]
            handler._json(200, {"events": sink.events})
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


def _set_agent_mode(state, mode: str) -> str:
    """/plan /build 共用核心：会话内切换只读计划档 / 回到构建档。

    不突变 cfg（与审批档案同一 override 机制）；/build 会重新套用审批档案，
    使被 /plan 临时压制的档案恢复。返回生效模式名。
    """
    loop = state.loop
    if loop is None:
        return str(state.cfg.agent.approval)
    if mode == "plan":
        loop._approval_override = "plan-only"
    else:
        loop._approval_override = None
        try:
            loop._apply_approval_profile(state.cfg)
        except Exception:  # noqa: BLE001 — 档案缺失不应让 /build 失败
            logger.debug("/build 重套审批档案失败", exc_info=True)
    return state._effective_approval()


def _run_mode_slash(state, stream: _SSESession, slash) -> bool:
    """/plan /build：切换 agent 档位；返回 True 表示流已收尾（fatal）。"""
    effective = _set_agent_mode(state, slash.name)
    label = (
        "🧭 已切换到 **plan 模式**（只读探索：写操作与命令将被拒绝，`/build` 返回）"
        if slash.name == "plan"
        else "🔨 已切换到 **build 模式**（恢复常规审批）"
    )
    stream.submit_payload(
        {
            "type": "approval_changed",
            "data": {
                "approval": effective,
                "base": state.cfg.agent.approval,
                "mode": slash.name,
                "label": label,
            },
        }
    )
    stream.finish()
    return True


def _recent_user_context(state, limit: int = 3) -> str:
    """侧聊的极简上下文：主对话最近几条用户消息（只读，不进侧聊历史）。"""
    try:
        rows = state.store.list_messages(state.session_id) or []
    except Exception:  # noqa: BLE001 — 上下文取不到就让侧聊裸答
        return ""
    users = [
        str(r.get("content") or "")[:400]
        for r in rows
        if r.get("role") == "user" and r.get("content")
    ]
    return "\n".join(f"- {u}" for u in users[-limit:])


def _run_side_slash(state, stream: _SSESession, slash) -> bool:
    """/side <问题>：辅助模型一次性作答，不污染主对话历史。fatal 收尾。"""
    from spark.providers.auxiliary import chat_once
    from spark.web.state import run_coroutine_sync

    question = slash.args.strip()
    if not question:
        stream.submit_payload(
            {"type": "turn_error", "text": "用法：`/side <问题>`（一次性旁支提问，不打断主对话）"}
        )
        stream.finish()
        return True
    provider = state.cfg.provider
    model = getattr(provider, "small_model", "") or provider.model
    api_key = provider.api_key or ""
    base_url = provider.base_url.rstrip("/")
    if provider.name == "mock" or (provider.name == "openai_compat" and not api_key):
        stream.submit_payload(
            {
                "type": "side_answer",
                "data": {
                    "question": question,
                    "answer": "（demo 模式）侧聊需要已配置的真实模型端点——主对话可继续用 mock。",
                    "model": "mock",
                    "ok": False,
                },
            }
        )
        stream.finish()
        return True
    context = _recent_user_context(state)
    system = (
        "你是编码会话的旁支助手（side channel）。用用户语言简洁回答，"
        "不要执行任何工具。以下是目前主对话里用户提过的请求，仅作背景参考：\n"
        f"{context or '（暂无）'}"
    )
    try:
        answer = run_coroutine_sync(
            chat_once(
                base_url=base_url,
                api_key=api_key,
                model=model,
                system=system,
                user=question,
                max_tokens=1500,
            ),
            timeout=90.0,
        )
        ok = bool(answer.strip())
    except Exception as exc:  # noqa: BLE001 — 侧聊失败不影响主对话
        logger.warning("/side 调用失败: %s", exc, exc_info=True)
        answer = f"侧聊调用失败：{exc}"
        ok = False
    stream.submit_payload(
        {
            "type": "side_answer",
            "data": {"question": question, "answer": answer, "model": model, "ok": ok},
        }
    )
    stream.finish()
    return True


def _run_goal_slash(state, stream: _SSESession, slash) -> bool:
    """/goal：设立/查看/收尾长程目标；返回 True 表示流已收尾。"""
    import time as _time

    store = state.store
    args = slash.args.strip()
    sub, _, rest = args.partition(" ")
    sub = (sub or "status").lower()
    if sub in ("status", ""):
        goal = store.get_active_goal(state.session_id)
        payload = {"type": "goal_status", "data": {"goal": goal}}
        stream.submit_payload(payload)
        stream.finish()
        return True
    if sub == "cancel":
        goal = store.get_active_goal(state.session_id)
        if goal:
            store.close_goal(goal["id"], "cancelled")
            stream.submit_payload(
                {
                    "type": "goal_status",
                    "data": {"goal": {**goal, "status": "cancelled"}},
                }
            )
        else:
            stream.submit_payload(
                {"type": "text_delta", "text": "当前会话没有活跃目标。"}
            )
        stream.finish()
        return True
    if sub == "done":
        goal = store.get_active_goal(state.session_id)
        if goal:
            store.close_goal(goal["id"], "done")
            stream.submit_payload(
                {"type": "goal_status", "data": {"goal": {**goal, "status": "done"}}}
            )
        else:
            stream.submit_payload(
                {"type": "text_delta", "text": "当前会话没有活跃目标。"}
            )
        stream.finish()
        return True
    # /goal <objective>：设立目标（同一会话同时只允许一个活跃目标）
    objective = args.strip()
    if not objective:
        stream.submit_payload(
            {"type": "text_delta", "text": "用法：`/goal <目标>` · `/goal status` · `/goal done` · `/goal cancel`"}
        )
        stream.finish()
        return True
    existing = store.get_active_goal(state.session_id)
    if existing:
        stream.submit_payload(
            {
                "type": "text_delta",
                "text": f"已有活跃目标：{existing['objective']}\n先 `/goal done` 或 `/goal cancel` 再设立新目标。",
            }
        )
        stream.finish()
        return True
    goal = store.upsert_goal(
        {
            "id": f"goal-{int(_time.time() * 1000)}",
            "session_id": state.session_id,
            "objective": objective,
            "status": "active",
            "checklist": [],
            "note": "",
        }
    )
    stream.submit_payload(
        {
            "type": "goal_status",
            "data": {
                "goal": goal,
                "label": "🎯 目标已设立——agent 每回合会收到目标契约（目标/证据/验收），"
                "并用 goal_update 工具回报进展；完成需附证据。",
            },
        }
    )
    stream.finish()
    return True


def _dispatch_slash(state, stream: _SSESession, slash) -> bool:
    """/test 之外的内置 slash 分发；返回 True 表示已收尾（调用方直接 return）。"""
    if slash.name in ("plan", "build"):
        return _run_mode_slash(state, stream, slash)
    if slash.name == "side":
        return _run_side_slash(state, stream, slash)
    if slash.name == "goal":
        return _run_goal_slash(state, stream, slash)
    return False


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
        # ask_user 挂起时断开/取消：不唤醒 asker，turn 协程最长要空等
        # 600s 超时才能退出（loop.cancelled 只在 llm 回合检查）。
        bridge = getattr(state, "question_bridge", None)
        if bridge is not None:
            bridge.cancel()

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
    elif slash is not None and _dispatch_slash(state, stream, slash):
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
