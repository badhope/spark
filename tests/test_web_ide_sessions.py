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
    store = SessionStore(tmp_path / "ide.db")
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
        with urllib.request.urlopen(req, timeout=30) as resp:
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


def test_isolated_session_does_not_steal_current_pointer(web) -> None:
    base, state, _ = web
    before = state.session_id
    status, body = _request(base, "/api/sessions/new", {"isolated": True, "title": "VS Code"})
    assert status == 200
    sid = body["session_id"]
    assert sid != before
    assert state.session_id == before
    assert state.runtime_for(sid) is not None
    # The isolated runtime is live without making the Web UI jump.
    isolated = _request(base, f"/api/status?session_id={sid}")[1]
    assert isolated["session_id"] == sid
    assert isolated["workdir"] == str(state.workdir)


def test_isolated_session_rejects_unknown_status(web) -> None:
    base, _, _ = web
    status, body = _request(base, "/api/status?session_id=does-not-exist")
    assert status == 404
    assert body.get("error") == "unknown session"


def test_chat_stream_can_target_isolated_session(web) -> None:
    base, state, _ = web
    before = state.session_id
    _, created = _request(base, "/api/sessions/new", {"isolated": True})
    sid = created["session_id"]
    req = urllib.request.Request(
        base + "/api/chat/stream",
        data=json.dumps({"prompt": "hello", "session_id": sid}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        assert resp.status == 200
        payload = resp.read().decode("utf-8", "replace")
    assert "data:" in payload
    assert state.session_id == before
    # The turn belongs to the isolated session, not to the Web UI's session.
    assert state.store.get_session(sid) is not None


def test_chat_stream_rejects_unknown_session_without_running(web) -> None:
    base, state, _ = web
    before = state.session_id
    status, body = _request(
        base, "/api/chat/stream", {"prompt": "hello", "session_id": "does-not-exist"}
    )
    assert status == 404
    assert state.session_id == before
