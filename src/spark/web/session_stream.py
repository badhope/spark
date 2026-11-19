"""Read-only SSE tail for an already-running session turn.

``POST /api/chat/stream`` starts a turn; if the browser reloads mid-turn it
loses that HTTP response. This endpoint replays the runtime's ring buffer and
then tails new events, so approval/question recovery has a live channel again.
"""

from __future__ import annotations

import json
import time

from spark.web.sse import SSE_DISCONNECT_ERRORS

HEARTBEAT_SECS = 15.0
POLL_SECS = 0.15
MAX_STREAM_SECS = 600.0


def serve_session_events(handler, state, session_id: str, after_seq: int = 0) -> None:
    runtime = state.runtime_for(session_id)
    if runtime is None:
        handler._json(404, {"error": "no active turn"})
        return

    from spark.web._handlers_chat import _send_sse_headers

    if not _send_sse_headers(handler):
        return

    cursor = max(0, int(after_seq))
    deadline = time.monotonic() + MAX_STREAM_SECS
    last_write = time.monotonic()
    done = False
    try:
        while not done and time.monotonic() < deadline:
            events, reset = runtime.events_after(cursor)
            if reset:
                payload = {"type": "reset_required", "session_id": session_id}
                _write_event(handler, 0, payload)
                break
            if cursor == 0 and events:
                last_turn = str(events[-1].get("turn_id") or "")
                if last_turn:
                    events = [e for e in events if str(e.get("turn_id") or "") == last_turn]
            for event in events:
                seq = int(event.get("seq") or 0)
                _write_event(handler, seq, event)
                cursor = max(cursor, seq)
                last_write = time.monotonic()
                if str(event.get("type") or "") == "done":
                    done = True
                    break
            if done:
                break
            if events:
                continue
            if not runtime.busy and time.monotonic() - last_write > 2.0:
                # The turn finished without a `done` frame (older producer);
                # don't leave a browser tab hanging forever.
                break
            if time.monotonic() - last_write > HEARTBEAT_SECS:
                handler.wfile.write(b": keep-alive\n\n")
                handler.wfile.flush()
                last_write = time.monotonic()
            time.sleep(POLL_SECS)
    except SSE_DISCONNECT_ERRORS:
        handler.close_connection = True
    finally:
        try:
            handler.wfile.flush()
        except SSE_DISCONNECT_ERRORS:
            pass


def _write_event(handler, seq: int, payload: dict) -> None:
    handler.wfile.write(f"id: {seq}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
    handler.wfile.flush()
