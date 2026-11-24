"""HTTP surface for the idle agent-task queue."""
from __future__ import annotations

from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state


def _queue_create(handler: HandlerBase, state, queue, body: dict) -> None:
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        handler._json(400, {"error": "prompt is required"})
        return
    kind = str(body.get("kind") or "agent").strip().lower()
    if kind not in {"agent", "autopilot"}:
        handler._json(400, {"error": "kind must be agent or autopilot"})
        return
    options = body.get("options")
    if options is None:
        options = {}
    if not isinstance(options, dict):
        handler._json(400, {"error": "options must be an object"})
        return
    # Autopilot limits are copied explicitly so the queue row remains
    # self-contained even if the caller later changes its defaults.
    if kind == "autopilot":
        for key in (
            "max_turns", "turn_timeout_sec", "task_timeout_sec",
            "test_timeout_sec", "max_cost_usd", "resume",
        ):
            if key in body and key not in options:
                options[key] = body[key]
    try:
        task = queue.enqueue(
            prompt,
            title=str(body.get("title") or ""),
            workdir=str(body.get("workdir") or state.workdir),
            priority=int(body.get("priority") or 0),
            kind=kind,
            options=options,
        )
    except (ValueError, TypeError) as exc:
        handler._json(400, {"error": str(exc)})
        return
    handler._json(200, {"ok": True, "task": task})


def _handle_queue(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    queue = getattr(state, "queue", None)
    if queue is None:
        handler._json(503, {"error": "task queue unavailable"})
        return
    action = str(body.get("action") or "list").strip()
    if action == "list":
        handler._json(200, queue.status())
        return
    if action == "create":
        _queue_create(handler, state, queue, body)
        return
    if action in {"pause", "resume"}:
        if action == "pause":
            queue.pause()
        else:
            queue.resume()
        handler._json(200, {"ok": True, "paused": queue.paused})
        return
    if action == "cancel":
        task_id = str(body.get("id") or "").strip()
        if not task_id:
            handler._json(400, {"error": "id is required"})
            return
        if not queue.cancel(task_id):
            handler._json(404, {"error": "unknown or already running task"})
            return
        handler._json(200, {"ok": True})
        return
    handler._json(400, {"error": "action must be list, create, pause, resume or cancel"})