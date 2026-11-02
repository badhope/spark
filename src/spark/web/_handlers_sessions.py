"""会话生命周期：取消 / 审批回应 / 新建·删除·切换 / 检查点回滚。

从单文件 ``_handlers.py`` 按域拆出，行为不变；导入路径经
``spark.web._handlers`` facade 保持稳定。
"""

from __future__ import annotations

import json
import logging
from typing import Literal, cast

from spark.models import ApprovalAction
from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state

logger = logging.getLogger(__name__)


def _handle_cancel(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    if state.loop:
        state.loop.cancel()
    if state.approver is not None:
        state.approver.cancel()
    handler._json(200, {"ok": True})


def _handle_approval(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    if state.approver is None:
        handler._json(400, {"error": "approval disabled (full-auto)"})
        return
    action = str(body.get("decision") or "").strip()
    if action not in {"allow", "allow_always", "deny"}:
        handler._json(400, {"error": "decision must be allow, allow_always or deny"})
        return
    state.approver.respond(cast(ApprovalAction, action))
    handler._json(200, {"ok": True})


def _handle_sessions_new(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    with state.turn_lock:
        state.rebuild_loop()
    handler._json(200, {"ok": True, "status": state.status()})


def _handle_sessions_delete(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    session_id = str(body.get("session_id") or "").strip()
    if not session_id:
        handler._json(400, {"error": "session_id required"})
        return
    row = state.store.get_session(session_id)
    if not row or row.get("workdir") != str(state.workdir):
        handler._json(404, {"error": "unknown session"})
        return
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    deleting_active = session_id == state.session_id
    state.store.delete_session(session_id)
    if deleting_active:
        with state.turn_lock:
            remaining = [
                r
                for r in state.store.list_sessions()
                if r["workdir"] == str(state.workdir)
            ]
            if remaining:
                state.rebuild_loop(session_id=remaining[0]["id"])
            else:
                state.rebuild_loop()
    handler._json(200, {"ok": True, "status": state.status()})


def _handle_sessions_switch(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    session_id = str(body.get("session_id") or "").strip()
    row = state.store.get_session(session_id)
    if not row or row.get("workdir") != str(state.workdir):
        handler._json(404, {"error": "unknown session"})
        return
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    with state.turn_lock:
        state.rebuild_loop(session_id=session_id)
    handler._json(
        200,
        {
            "ok": True,
            "status": state.status(),
            "messages": _history_messages(state),
        },
    )


def _handle_sessions_rename(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    session_id = str(body.get("session_id") or "").strip()
    title = str(body.get("title") or "").strip()
    if not title:
        handler._json(400, {"error": "title required"})
        return
    if len(title) > 200:
        handler._json(400, {"error": "title too long (max 200)"})
        return
    row = state.store.get_session(session_id)
    if not row or row.get("workdir") != str(state.workdir):
        handler._json(404, {"error": "unknown session"})
        return
    state.store.set_title(session_id, title)
    handler._json(200, {"ok": True, "status": state.status()})


def _handle_sessions_fork(handler: HandlerBase, body: dict) -> None:
    """Fork a session: copy every message into a new session and switch to it.

    The UI needs a branch point ("继续从这条消息往下试另一个方向") without the
    user hand-rolling a new chat and re-pasting context.
    """
    state = _route_state(handler)
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    session_id = str(body.get("session_id") or state.session_id).strip()
    row = state.store.get_session(session_id)
    if not row or row.get("workdir") != str(state.workdir):
        handler._json(404, {"error": "unknown session"})
        return
    forked = state.store.fork(session_id)
    if forked is None:
        handler._json(404, {"error": "unknown session"})
        return
    with state.turn_lock:
        state.rebuild_loop(session_id=str(forked["id"]))
    handler._json(
        200,
        {
            "ok": True,
            "status": state.status(),
            "messages": _history_messages(state),
        },
    )


def _history_messages(state) -> list[dict]:
    """Extract serializable message list from current loop history.

    Each row carries its database id when in-memory history and stored rows are
    index aligned, so the UI can delete or rewind from an exact message instead
    of guessing an index. Ids are omitted (never fabricated) when compaction or
    a race left the two lists out of sync.
    """
    messages: list[dict] = []
    if state.loop is None:
        return messages
    history = list(state.loop.history or [])
    ids: list[int] = []
    try:
        ids = state.store.load_message_ids(state.session_id)
    except Exception:  # pragma: no cover - ids are a convenience, never a blocker
        logger.debug("load_message_ids failed", exc_info=True)
    aligned = len(ids) == len(history)
    for index, msg in enumerate(history):
        row_id = ids[index] if aligned else None
        if msg.role in {"user", "assistant"} and msg.content and not (
            msg.role == "assistant" and msg.tool_calls
        ):
            entry: dict = {"role": msg.role, "content": msg.content}
            if row_id is not None:
                entry["id"] = row_id
            if msg.role == "user" and msg.images:
                entry["images"] = [
                    f"data:{i.media_type};base64,{i.data}" for i in msg.images
                ]
            messages.append(entry)
        elif msg.role == "tool":
            entry = {
                "role": "tool",
                "name": msg.name or "",
                "content": msg.content or "",
            }
            if msg.tool_call_id:
                entry["tool_call_id"] = msg.tool_call_id
            if row_id is not None:
                entry["id"] = row_id
            messages.append(entry)
        elif msg.role == "assistant" and msg.tool_calls:
            # An assistant message that only requested tools has no text, so it
            # was dropped entirely: reloading a session lost the tool arguments
            # (the card rendered `{}`) and merged turns. Emit it as a tool-call
            # carrier so the UI can rebuild the same cards it showed live.
            entry = {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": c.id, "name": c.name, "arguments": c.arguments}
                    for c in msg.tool_calls
                ],
            }
            if row_id is not None:
                entry["id"] = row_id
            messages.append(entry)
    return messages


def _session_markdown(state) -> str:
    """Render the current session as a portable Markdown transcript.

    Same shape opencode/codex use for ``/export``: a heading with the session
    metadata, then one section per turn with the user prompt, the assistant
    answer, and a compact list of the tools that ran (name + first line of the
    result, so the file stays readable).
    """
    from datetime import datetime

    session = state.store.get_session(state.session_id) or {}
    title = session.get("title") or "untitled"
    lines = [
        f"# {title}",
        "",
        f"- 会话：`{state.session_id}`",
        f"- 工作目录：`{state.workdir}`",
        f"- 模型：`{session.get('model') or state.cfg.provider.model}`",
        f"- 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
    ]
    tool_queue: list[dict] = []
    try:
        for row in state.store.list_tool_events(state.session_id):
            tool_queue.append(row)
    except Exception:  # pragma: no cover - export must not fail on a side query
        logger.debug("list_tool_events failed", exc_info=True)

    for msg in state.loop.history if state.loop else []:
        if msg.role == "user" and msg.content:
            lines += ["## 用户", "", msg.content, ""]
        elif msg.role == "assistant" and msg.content:
            lines += ["## Spark", "", msg.content, ""]
        elif msg.role == "tool":
            name = msg.name or "tool"
            # Stored tool content is the full JSON payload; show arguments plus a
            # clipped result instead of dumping escaped JSON at the reader.
            snippet = _tool_snippet(msg.content or "")
            lines += [f"**工具** `{name}`", "", f"```\n{snippet}\n```", ""]
    lines += ["---", "", f"导出工具事件 {len(tool_queue)} 条。", ""]
    return "\n".join(lines)


def _tool_snippet(raw: str, limit: int = 400) -> str:
    """Compact, readable rendering of one stored tool payload."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return raw.strip()[:limit]
    if not isinstance(payload, dict):
        return raw.strip()[:limit]
    args = payload.get("arguments")
    result = payload.get("result")
    parts: list[str] = []
    if isinstance(args, dict) and args:
        parts.append("args: " + json.dumps(args, ensure_ascii=False)[:200])
    if result is not None:
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        parts.append("result: " + text[:limit])
    return "\n".join(parts) if parts else raw.strip()[:limit]


def _handle_session_export(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    fmt = str(body.get("format") or "markdown").strip().lower()
    if fmt not in {"markdown", "md", "json", "html"}:
        handler._json(400, {"error": "format must be markdown, json or html"})
        return
    if fmt == "json":
        payload = {
            "session_id": state.session_id,
            "title": (state.store.get_session(state.session_id) or {}).get("title"),
            "workdir": str(state.workdir),
            "messages": _history_messages(state),
        }
        handler._json(200, {"ok": True, "format": "json", "content": json.dumps(payload, ensure_ascii=False, indent=2)})
        return
    if fmt == "html":
        handler._json(
            200,
            {"ok": True, "format": "html", "content": _session_html(state)},
        )
        return
    handler._json(
        200,
        {"ok": True, "format": "markdown", "content": _session_markdown(state)},
    )


def _session_html(state) -> str:
    """自包含单文件 HTML 分享（opencode /share 的本地无服务版）。

    零 JS、零外链：打开即完整对话记录；工具调用折叠为 <details>；自动适配
    深浅色。内容全部经 html.escape —— 分享文件是"数据"，任何一方的消息
    内容都不能在里面执行。
    """
    import html as _html
    from datetime import datetime

    session = state.store.get_session(state.session_id) or {}
    title = session.get("title") or "untitled"
    messages = _history_messages(state)
    parts: list[str] = []
    for m in messages:
        role = m.get("role")
        content = str(m.get("content") or "")
        if role == "user":
            parts.append(
                f'<div class="msg user"><div class="role">用户</div>'
                f'<div class="text">{_html.escape(content)}</div></div>'
            )
        elif role == "assistant":
            parts.append(
                f'<div class="msg assistant"><div class="role">助手</div>'
                f'<div class="text">{_html.escape(content)}</div></div>'
            )
        else:
            name = _html.escape(str(m.get("name") or "tool"))
            parts.append(
                f'<details class="tool"><summary>工具 {name}</summary>'
                f"<pre>{_html.escape(content[:4000])}</pre></details>"
            )
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_html.escape(str(title))} · Spark 会话分享</title><style>"
        ":root{color-scheme:light dark}"
        "body{margin:0;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;"
        "background:#f6f7f9;color:#1c1e21}"
        "@media(prefers-color-scheme:dark){body{background:#14171c;color:#e6e8eb}}"
        "main{max-width:860px;margin:0 auto;padding:32px 20px 64px}"
        "h1{font-size:22px;margin:0 0 4px}"
        ".meta{color:#8a8f98;font-size:13px;margin-bottom:28px}"
        ".msg{margin:14px 0;padding:12px 16px;border-radius:12px;background:#fff;"
        "border:1px solid #e3e6ea}"
        ".assistant{background:transparent;border-style:dashed}"
        "@media(prefers-color-scheme:dark){.msg{background:#1d2229;border-color:#2c333d}}"
        ".role{font-size:12px;font-weight:600;color:#5a8de0;margin-bottom:6px}"
        ".text{white-space:pre-wrap;word-break:break-word;line-height:1.65;font-size:14.5px}"
        ".tool{margin:10px 0;font-size:13px}"
        ".tool summary{cursor:pointer;color:#8a8f98}"
        ".tool pre{overflow:auto;background:#0f1115;color:#cfe3ff;padding:10px;"
        "border-radius:8px;font-size:12px;max-height:320px}"
        ".foot{margin-top:40px;color:#8a8f98;font-size:12px;text-align:center}"
        "</style></head><body><main>"
        f"<h1>{_html.escape(str(title))}</h1>"
        f'<div class="meta">模型 {_html.escape(str(session.get("model") or ""))}'
        f" · 导出于 {_html.escape(stamp)} · 内容已脱敏（不含本机路径与会话 id）</div>"
        + "".join(parts)
        + '<div class="foot">由 Spark 本地导出 · 数据完全离线</div>'
        "</main></body></html>"
    )


_MAX_IMPORT_MESSAGES = 500
_MAX_IMPORT_CHARS = 1_000_000


def _handle_session_import(handler: HandlerBase, body: dict) -> None:
    """导入会话 JSON（spark 导出格式，round-trip 对齐 /api/sessions/export json）。

    只恢复 user/assistant 文本消息：tool 载体行引用活动 tool_call id，
    脱离原会话恢复它们会产生悬空 tool_call（打挂下一次 provider 请求）。
    """
    state = _route_state(handler)
    raw = body.get("content")
    payload = raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            handler._json(400, {"error": f"content is not valid JSON: {exc}"})
            return
    if not isinstance(payload, dict):
        handler._json(
            400, {"error": "content must be a session export object or JSON string"}
        )
        return
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        handler._json(400, {"error": "messages must be a non-empty list"})
        return
    if len(messages) > _MAX_IMPORT_MESSAGES:
        handler._json(400, {"error": f"too many messages (max {_MAX_IMPORT_MESSAGES})"})
        return
    restored: list[tuple[Literal["user", "assistant"], str]] = []
    skipped = 0
    total = 0
    for m in messages:
        if not isinstance(m, dict):
            skipped += 1
            continue
        role = m.get("role")
        content = str(m.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            skipped += 1
            continue
        typed_role: Literal["user", "assistant"] = (
            "user" if role == "user" else "assistant"
        )
        total += len(content)
        if total > _MAX_IMPORT_CHARS:
            handler._json(400, {"error": "session too large to import"})
            return
        restored.append((typed_role, content))
    if not restored:
        handler._json(400, {"error": "no user/assistant text messages to import"})
        return
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    title = str(payload.get("title") or "导入会话").strip()[:80] or "导入会话"
    with state.turn_lock:
        sid = state.store.create_session(
            state.workdir, state.cfg.provider.model, title=title
        )
        from spark.models import ChatMessage

        for role, content in restored:
            state.store.append_message(sid, ChatMessage(role=role, content=content))
        state.rebuild_loop(session_id=sid)
    handler._json(
        200,
        {
            "ok": True,
            "session_id": sid,
            "imported": len(restored),
            "skipped": skipped,
            "status": state.status(),
            "messages": _history_messages(state),
        },
    )


def _handle_question_answer(handler: HandlerBase, body: dict) -> None:
    """Deliver the user's answer to a pending ``ask_user`` question."""
    state = _route_state(handler)
    bridge = getattr(state, "question_bridge", None)
    if bridge is None or not bridge.pending:
        handler._json(409, {"error": "no pending question"})
        return
    answer = str(body.get("answer") or "")
    bridge.answer(answer)
    handler._json(200, {"ok": True})


def _handle_checkpoints_rollback(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    cp_id = body.get("checkpoint_id")
    if not isinstance(cp_id, int):
        handler._json(400, {"error": "checkpoint_id (int) required"})
        return
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    if state.loop is None:
        handler._json(500, {"error": "agent not ready"})
        return
    try:
        with state.turn_lock:
            report = state.loop.rollback_to_checkpoint(cp_id)
    except ValueError as exc:
        handler._json(404, {"error": str(exc)})
        return
    except Exception as exc:
        handler._json(500, {"error": f"rollback failed: {exc}"})
        return
    handler._json(
        200,
        {
            "ok": True,
            "report": report,
            "status": state.status(),
            "messages": _history_messages(state),
        },
    )


def _handle_messages_delete(handler: HandlerBase, body: dict) -> None:
    """Delete exactly one message (and its tool events) from the current session."""
    state = _route_state(handler)
    message_id = body.get("message_id")
    if not isinstance(message_id, int) or message_id <= 0:
        handler._json(400, {"error": "message_id (int) required"})
        return
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    if state.loop is None:
        handler._json(500, {"error": "agent not ready"})
        return
    with state.turn_lock:
        remaining = state.store.delete_message(state.session_id, message_id)
        if remaining is None:
            handler._json(404, {"error": f"message not found: {message_id}"})
            return
        state.loop._replace_history(remaining)
    handler._json(
        200,
        {
            "ok": True,
            "status": state.status(),
            "messages": _history_messages(state),
        },
    )


def _handle_messages_truncate(handler: HandlerBase, body: dict) -> None:
    """P2 双粒度时间机器：消息级 undo——删除指定消息及其后全部消息。

    与检查点回滚互补：检查点恢复文件快照 + 对话；消息 undo 默认只回退对话
    （文件保持现状），适合"对话跑偏但改动仍想保留"的场景。

    请求带 ``restore_files: true`` 时，会一并把工作区恢复到该消息**之前**
    最近的检查点快照——这才是竞品矩阵里承诺的"按轮把文件+消息一并还原"。
    没有可用快照时不静默跳过：返回 ``files_restored: false`` 和一个原因，
    让 UI 如实告诉用户只回退了对话。
    """
    state = _route_state(handler)
    message_id = body.get("message_id")
    if not isinstance(message_id, int) or message_id <= 0:
        handler._json(400, {"error": "message_id (int) required"})
        return
    restore_files = bool(body.get("restore_files"))
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    if state.loop is None:
        handler._json(500, {"error": "agent not ready"})
        return
    with state.turn_lock:
        files_restored = False
        restore_reason = ""
        restore_counts: dict = {}
        if restore_files:
            record = state.store.nearest_checkpoint_at_or_before(
                state.session_id, message_id
            )
            if record is None:
                restore_reason = "no checkpoint at or before this message"
            else:
                try:
                    import json as _json

                    from spark.core.checkpoints import restore_workdir

                    meta = _json.loads(record["snapshot_json"])
                    snapshot_id = str(meta.get("snapshot_id") or "")
                    if snapshot_id:
                        restore_counts = restore_workdir(state.workdir, snapshot_id)
                        files_restored = True
                    else:
                        restore_reason = "checkpoint has no snapshot id"
                except Exception as exc:  # aqg: top-level boundary
                    # Rolling back the conversation while claiming the files
                    # were restored would be a lie; report the failure instead.
                    logger.warning("message undo file restore failed: %s", exc, exc_info=True)
                    restore_reason = f"file restore failed: {exc}"
        remaining = state.store.truncate_messages(state.session_id, message_id)
        if remaining is None:
            handler._json(404, {"error": f"message not found: {message_id}"})
            return
        state.loop._replace_history(remaining)
    handler._json(
        200,
        {
            "ok": True,
            "status": state.status(),
            "messages": _history_messages(state),
            "files_restored": files_restored,
            "restore_reason": restore_reason,
            "restore_counts": restore_counts,
        },
    )
