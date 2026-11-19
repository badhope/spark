"""Every HTTP route must answer — success or a deliberate 4xx, never a 500.

The web surface is the largest untested area (routes.py / handlers at 12-53%
coverage before this file). A route-sweep test is worth more than many
targeted unit tests here: it exercises the real handler chain, the real
serialisation and the real guards, and it fails loudly when a handler that was
added to the table is never reachable or crashes on a minimal payload.

Deliberate 4xx responses ARE covered behaviour: each one is asserted, so a
handler that starts returning 500, or silently starts succeeding, is caught.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.store import SessionStore
from spark.web.server import build_web_server


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def web(tmp_path: Path, monkeypatch):
    import spark.web.server as server_module

    monkeypatch.setattr(server_module, "save_config", lambda cfg: None)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock-model"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "route.db")
    port = _free_port()
    server, state = build_web_server(
        workdir=tmp_path, cfg=cfg, store=store, host="127.0.0.1", port=port
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/api/status", timeout=2).read()
            break
        except (urllib.error.URLError, OSError):
            time.sleep(0.05)
    try:
        yield base, state, tmp_path
    finally:
        server.shutdown()
        server.server_close()
        store.close()


def _request(base: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except ValueError:
                return resp.status, {"_raw": raw}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"_raw": raw}


# ---------------------------------------------------------------------------
# GET routes
# ---------------------------------------------------------------------------

GET_ROUTES = [
    "/api/status",
    "/api/config",
    "/api/models",
    "/api/agents_md",
    "/api/memory",
    "/api/sessions",
    "/api/checkpoints",
    "/api/replays",
    "/api/timeline",
    "/api/commands",
    "/api/history",
    "/api/fs/tree",
    "/api/git/changes",
    "/api/diagnostics",
]


@pytest.mark.parametrize("path", GET_ROUTES)
def test_get_routes_answer_with_json(web, path: str) -> None:
    base, _, _ = web
    status, body = _request(base, path)
    assert status == 200, (path, status, body)
    assert isinstance(body, dict)
    assert "_raw" not in body, f"{path} did not return JSON"


def test_openapi_route_is_versioned_or_degraded(web) -> None:
    """FastAPI is an optional extra: 200 when present, 503 with a hint when not."""
    base, _, _ = web
    status, body = _request(base, "/api/openapi.json")
    assert status in (200, 503)
    if status == 503:
        assert "hint" in body
    else:
        assert body.get("openapi")


def test_fs_routes_reject_escape(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/fs/tree?path=../../etc")
    assert status == 200
    assert body.get("error"), "path escape must be reported, not served"
    status, body = _request(base, "/api/fs/read?path=../../etc/passwd")
    assert status == 200
    assert body.get("error")


def test_fs_read_roundtrip(web) -> None:
    base, _, workdir = web
    (workdir / "hello.txt").write_text("hi there", encoding="utf-8")
    status, body = _request(base, "/api/fs/read?path=hello.txt")
    assert status == 200
    assert body["content"] == "hi there"
    assert body["name"] == "hello.txt"


def test_fs_read_binary_is_flagged(web) -> None:
    base, _, workdir = web
    (workdir / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    status, body = _request(base, "/api/fs/read?path=blob.bin")
    assert status == 200
    assert body.get("binary") is True


def test_timeline_lists_messages_and_checkpoints(web) -> None:
    base, state, _ = web
    status, body = _request(base, "/api/timeline")
    assert status == 200
    assert body["session_id"] == state.session_id
    assert isinstance(body["messages"], list)
    assert isinstance(body["checkpoints"], list)


def test_replays_rejects_bad_index(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/replays/turn?index=999")
    assert status in (200, 404)
    if status == 404:
        assert "error" in body


# ---------------------------------------------------------------------------
# POST routes — minimal valid and invalid payloads
# ---------------------------------------------------------------------------


def test_post_cancel(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/cancel", {})
    assert status == 200 and body["ok"]


def test_post_sessions_new_switches_to_a_fresh_id(web) -> None:
    base, state, _ = web
    before = state.session_id
    status, body = _request(base, "/api/sessions/new", {})
    assert status == 200
    assert body["status"]["session_id"] != before


def test_post_sessions_rename_and_validation(web) -> None:
    base, state, _ = web
    status, body = _request(
        base, "/api/sessions/rename", {"session_id": state.session_id, "title": "改名了"}
    )
    assert status == 200 and body["ok"]
    assert body["status"]["session_id"] == state.session_id
    status, body = _request(base, "/api/sessions/rename", {"session_id": state.session_id, "title": "  "})
    assert status == 400
    status, body = _request(base, "/api/sessions/rename", {"session_id": "nope", "title": "x"})
    assert status == 404


def test_post_sessions_fork(web) -> None:
    base, state, _ = web
    before = state.session_id
    status, body = _request(base, "/api/sessions/fork", {"session_id": before})
    assert status == 200
    forked_id = body["status"]["session_id"]
    assert forked_id != before
    # The state must have switched too, not just the response body.
    assert state.session_id == forked_id


def test_post_sessions_delete_unknown_is_404(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/sessions/delete", {"session_id": "missing"})
    assert status == 404


def test_post_sessions_switch_unknown_is_404(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/sessions/switch", {"session_id": "missing"})
    assert status == 404


def test_get_sessions_pending_and_events(web) -> None:
    base, state, _ = web
    sid = state.session_id
    runtime = state.current_runtime
    assert runtime is not None
    runtime.record_event(
        {"v": 1, "session_id": sid, "turn_id": "t-1", "seq": 1, "type": "text_delta", "text": "hi"}
    )
    status, body = _request(base, f"/api/sessions/{sid}/pending")
    assert status == 200
    assert body["session_id"] == sid
    assert body["approval"] is None
    assert body["question"] is None
    assert body["busy"] is False
    assert body["last_seq"] == 1
    status, body = _request(base, f"/api/sessions/{sid}/events?after_seq=0")
    assert status == 200
    assert body["session_id"] == sid
    assert body["reset_required"] is False
    assert [event["seq"] for event in body["events"]] == [1]
    assert body["last_seq"] == 1


def test_get_sessions_stream_replays_done(web) -> None:
    base, state, _ = web
    sid = state.session_id
    runtime = state.current_runtime
    assert runtime is not None
    runtime.record_event(
        {"v": 1, "session_id": sid, "turn_id": "t-1", "seq": 1, "type": "text_delta", "text": "hi"}
    )
    runtime.record_event(
        {"v": 1, "session_id": sid, "turn_id": "t-1", "seq": 2, "type": "done"}
    )
    req = urllib.request.Request(base + f"/api/sessions/{sid}/stream?after_seq=0")
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8", "replace")
    assert "data:" in raw
    payloads = [
        json.loads(line[6:])
        for line in raw.splitlines()
        if line.startswith("data: ")
    ]
    assert [p["type"] for p in payloads] == ["text_delta", "done"]
    assert [p["seq"] for p in payloads] == [1, 2]


def test_runtime_seq_is_monotonic_across_turns() -> None:
    from spark.web.state import SessionRuntime

    runtime = SessionRuntime(session_id="s")
    assert runtime.next_seq_start() == 0
    runtime.record_event({"seq": 3, "type": "done"})
    assert runtime.next_seq_start() == 3
    runtime.record_event({"seq": 4, "type": "text_delta"})
    assert runtime.next_seq_start() == 4


def test_get_sessions_pending_reports_busy_and_cursor(web) -> None:
    base, state, _ = web
    sid = state.session_id
    runtime = state.current_runtime
    assert runtime is not None
    runtime.busy = True
    runtime.record_event(
        {"v": 1, "session_id": sid, "turn_id": "t-1", "seq": 6, "type": "text_delta", "text": "hi"}
    )
    status, body = _request(base, f"/api/sessions/{sid}/pending")
    assert status == 200
    assert body["busy"] is True
    assert body["last_seq"] == 6


def test_get_sessions_pending_unknown_is_404(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/sessions/missing/pending")
    assert status == 404
    assert body["error"] == "unknown session"


def test_post_cancel_accepts_session_id(web) -> None:
    base, state, _ = web
    status, body = _request(base, "/api/cancel", {"session_id": state.session_id})
    assert status == 200
    assert body["ok"] is True
    assert body["session_id"] == state.session_id


def test_switch_session_keeps_background_runtime(web) -> None:
    _, state, _ = web
    first = state.current_runtime
    assert first is not None
    first_loop = state.loop
    second_id = state.store.create_session(
        state.workdir, state.cfg.provider.model, title="second"
    )
    assert state.switch_session(second_id)
    assert state.runtime_for(first.session_id) is first
    assert state.loop is not first_loop
    assert state.switch_session(first.session_id)
    assert state.loop is first_loop


def test_runtime_event_ring_reports_reset_for_old_cursor() -> None:
    from spark.web.state import SessionRuntime

    runtime = SessionRuntime(session_id="s", max_events=2)
    for seq in range(1, 5):
        runtime.record_event({"seq": seq, "type": "text_delta", "text": str(seq)})
    assert len(runtime.events) == 2
    events, reset = runtime.events_after(1)
    assert reset is True
    assert events == []
    events, reset = runtime.events_after(2)
    assert reset is False
    assert [event["seq"] for event in events] == [3, 4]


def test_post_messages_delete_validation(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/messages/delete", {"message_id": "x"})
    assert status == 400
    status, body = _request(base, "/api/messages/delete", {"message_id": 999999})
    assert status == 404


def test_post_messages_truncate_validation(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/messages/truncate", {"message_id": 0})
    assert status == 400
    status, body = _request(base, "/api/messages/truncate", {"message_id": 999999})
    assert status == 404


def test_post_approval_without_pending(web) -> None:
    """No approver in full-auto: the endpoint must say so instead of hanging."""
    base, _, _ = web
    status, body = _request(base, "/api/approval", {"decision": "allow"})
    assert status == 400
    assert "approval" in body["error"]


def test_post_question_answer_without_pending(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/question/answer", {"answer": "x"})
    assert status == 409
    assert "pending" in body["error"]


def test_post_question_answer_routes_to_session_runtime(web) -> None:
    base, state, _ = web
    runtime = state.current_runtime
    assert runtime is not None
    bridge = runtime.question_bridge
    bridge.pending = True
    bridge._question = "which file?"
    bridge._options = ["a.py", "b.py"]
    status, body = _request(
        base,
        "/api/question/answer",
        {"answer": "a.py", "session_id": runtime.session_id},
    )
    assert status == 200
    assert body["ok"] is True
    assert body["session_id"] == runtime.session_id
    assert bridge._answer == "a.py"
    assert bridge.pending is True  # answer() awaits the agent side to clear it


def test_post_question_answer_unknown_session_is_404(web) -> None:
    base, _, _ = web
    status, body = _request(
        base,
        "/api/question/answer",
        {"answer": "x", "session_id": "missing"},
    )
    assert status == 404
    assert body["error"] == "unknown session"


def test_post_selfheal_reports_actions(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/selfheal", {})
    assert status == 200
    assert "actions" in body and "rows" in body


def test_post_export_formats(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/sessions/export", {"format": "markdown"})
    assert status == 200 and body["content"].startswith("# ")
    status, body = _request(base, "/api/sessions/export", {"format": "json"})
    assert status == 200 and json.loads(body["content"])["session_id"]
    status, body = _request(base, "/api/sessions/export", {"format": "pdf"})
    assert status == 400


def test_post_agents_md_write_and_read(web) -> None:
    base, _, workdir = web
    status, body = _request(base, "/api/agents_md", {"content": "# 规则\n不要用 legacy/"})
    assert status == 200 and body["chars"] > 0
    assert (workdir / "AGENTS.md").read_text(encoding="utf-8").startswith("# 规则")


def test_post_memory_add_search_delete(web) -> None:
    base, _, _ = web
    status, body = _request(
        base, "/api/memory", {"action": "add", "content": "偏好 pnpm", "type": "preference"}
    )
    assert status == 200 and body["id"] > 0
    mem_id = body["id"]
    status, body = _request(base, "/api/memory?q=pnpm")
    assert status == 200
    status, body = _request(base, "/api/memory", {"action": "delete", "id": mem_id})
    assert status == 200 and body["ok"]
    status, body = _request(base, "/api/memory", {"action": "bogus"})
    assert status == 400


def test_post_models_crud(web) -> None:
    base, state, _ = web
    status, body = _request(
        base,
        "/api/models/save",
        {"name": "p1", "base_url": "https://api.example.com/v1", "model": "m1", "provider": "openai_compat"},
    )
    assert status == 200 and body["id"]
    pid = body["id"]
    status, body = _request(base, "/api/models/activate", {"id": pid})
    assert status == 200
    status, body = _request(base, "/api/models/delete", {"id": pid})
    assert status == 200
    status, body = _request(base, "/api/models/activate", {"id": "nope"})
    assert status == 404
    status, body = _request(base, "/api/models/save", {"name": "x", "base_url": "", "model": ""})
    assert status == 400


def test_post_settings_rejects_bad_values(web) -> None:
    base, _, _ = web
    for payload in (
        {"agent": {"approval": "nonsense"}},
        {"agent": {"sandbox_mode": "nope"}},
        {"agent": {"effort": "extreme"}},
        {"agent": {"shell_timeout_sec": "abc"}},
        {"context": {"compact_threshold": 5}},
    ):
        status, body = _request(base, "/api/settings", payload)
        assert status == 400, payload
        assert "error" in body


def test_post_chat_nonstream_and_history(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/chat", {"prompt": "list files"})
    assert status == 200
    assert isinstance(body["events"], list) and body["events"]
    status, body = _request(base, "/api/history")
    assert status == 200 and body["messages"]


def test_post_chat_requires_prompt(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/chat", {"prompt": "  "})
    assert status == 400


def test_unknown_api_route_is_404(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/does-not-exist")
    assert status == 404


def test_unknown_post_route_is_404(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/nope", {})
    assert status == 404


# ---------------------------------------------------------------------------
# PTY routes (Windows: 501 with an explanation; POSIX: real session)
# ---------------------------------------------------------------------------


def test_pty_open_rejects_cwd_escape(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/pty/open", {"tab_id": "t1", "cwd": "../../../etc"})
    assert status == 400
    assert "cwd" in body["error"]


def test_pty_open_rejects_non_integer_size(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/pty/open", {"tab_id": "t1", "rows": "abc"})
    assert status == 400
    assert "rows" in body["error"]


def test_pty_open_missing_cwd_is_400(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/pty/open", {"tab_id": "t1", "cwd": "no-such-dir"})
    assert status == 400
    assert "not found" in body["error"]


def test_pty_lifecycle(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/pty/open", {"tab_id": "t1", "rows": 30, "cols": 100})
    if status == 501:
        # Windows has no POSIX pty: the endpoint must say so, not crash.
        assert "ptyprocess" in body["error"] or "伪终端" in body["error"]
        return
    assert status == 200 and body["tab_id"] == "t1"
    assert body["rows"] == 30 and body["cols"] == 100
    status, body = _request(base, "/api/pty/write", {"tab_id": "t1", "data": "echo hi\n"})
    assert status == 200 and "ok" in body
    status, body = _request(base, "/api/pty/resize", {"tab_id": "t1", "rows": 40, "cols": 120})
    assert status == 200 and "ok" in body
    status, body = _request(base, "/api/pty/close", {"tab_id": "t1"})
    assert status == 200 and "ok" in body


def test_pty_stream_unknown_tab_is_404(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/pty/stream?tab_id=ghost")
    assert status == 404


def test_pwa_manifest_and_icons_are_served(web) -> None:
    """The installable shell must ship with its manifest and real PNG icons."""
    base, _, _ = web
    status, manifest = _request(base, "/manifest.webmanifest")
    assert status == 200
    assert manifest["name"].startswith("Spark")
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/"
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes
    for icon in ("/icon-192.png", "/icon-512.png", "/icon-180.png"):
        with urllib.request.urlopen(base + icon, timeout=10) as resp:
            assert resp.status == 200
            assert resp.headers.get_content_type() == "image/png"
            assert len(resp.read()) > 1000


def test_remote_pairing_roundtrip(web) -> None:
    """Pair code -> device token -> local device list -> revoke."""
    base, state, _ = web
    status, minted = _request(base, "/api/pair/code", {})
    assert status == 200 and minted["code"]
    status, claimed = _request(
        base, "/api/pair/claim", {"code": minted["code"], "name": "phone"}
    )
    assert status == 200
    token = claimed["token"]
    assert state.authorize_token(token) == "device"
    status, listed = _request(base, "/api/pair/devices")
    assert status == 200
    assert [d["name"] for d in listed["devices"]] == ["phone"]
    status, revoked = _request(
        base, "/api/pair/revoke", {"device_id": claimed["device"]["id"]}
    )
    assert status == 200
    assert state.authorize_token(token) is None


def test_task_queue_http_roundtrip(web) -> None:
    base, _, _ = web
    status, paused = _request(base, "/api/queue", {"action": "pause"})
    assert status == 200 and paused["paused"] is True
    status, created = _request(
        base, "/api/queue", {"action": "create", "prompt": "queued prompt", "title": "q1"}
    )
    assert status == 200
    task_id = created["task"]["id"]
    status, listed = _request(base, "/api/queue", {"action": "list"})
    assert status == 200
    assert listed["paused"] is True
    assert listed["tasks"][0]["status"] == "queued"
    status, cancelled = _request(base, "/api/queue", {"action": "cancel", "id": task_id})
    assert status == 200 and cancelled["ok"] is True
    status, resumed = _request(base, "/api/queue", {"action": "resume"})
    assert status == 200 and resumed["paused"] is False
