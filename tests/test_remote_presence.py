from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from spark.web.remote import DevicePairing, RemoteAuditLog


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(
    base: str,
    path: str,
    body: object | None = None,
    token: str = "",
    host: str = "",
) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    if token:
        headers["X-Spark-Token"] = token
    if host:
        headers["Host"] = host
    request = urllib.request.Request(
        base + path, data=data, headers=headers, method="POST" if data is not None else "GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_presence_online_after_touch() -> None:
    pairing = DevicePairing()
    code = pairing.new_code()
    claimed = pairing.claim(code["code"], "phone")
    device = claimed["device"]
    assert pairing.presence()[0]["online"] is False
    pairing.touch(claimed["token"], path="/api/chat", session_id="s1")
    presence = pairing.presence()[0]
    assert presence["id"] == device["id"]
    assert presence["online"] is True
    assert presence["last_path"] == "/api/chat"
    assert presence["last_session_id"] == "s1"


def test_audit_log_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = RemoteAuditLog(path)
    log.record("dev_1", "phone", "approval", detail="allow", source="device")
    assert path.stat().st_mode & 0o777 == 0o600
    reloaded = RemoteAuditLog(path)
    assert reloaded.tail(1)[0]["action"] == "approval"


def test_remote_write_is_audited_and_device_is_online(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SPARK_HOME", str(tmp_path))
    from spark.config import SparkConfig
    from spark.store import SessionStore
    from spark.web.server import build_web_server

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock"
    store = SessionStore(tmp_path / "presence.db")
    port = _free_port()
    server, state = build_web_server(
        workdir=tmp_path,
        cfg=cfg,
        store=store,
        host="127.0.0.1",
        port=port,
        public_url="https://spark.example.com",
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            _request(base, "/api/remote/status")
            break
        except OSError:
            time.sleep(0.05)
    try:
        status, minted = _request(base, "/api/pair/code", {})
        assert status == 200 and minted["code"]
        status, claimed = _request(
            base, "/api/pair/claim", {"code": minted["code"], "name": "phone"}
        )
        assert status == 200
        token = claimed["token"]

        status, listed = _request(base, "/api/pair/devices")
        assert status == 200
        assert listed["devices"][0]["name"] == "phone"

        status, _ = _request(
            base, "/api/queue", {"action": "pause"}, token=token, host="spark.example.com"
        )
        assert status == 200
        status, beat = _request(
            base,
            "/api/presence/heartbeat",
            {},
            token=token,
            host="spark.example.com",
        )
        assert status == 200, beat
        assert beat["device"]["online"] is True

        status, listed = _request(base, "/api/pair/devices")
        assert listed["devices"][0]["online"] is True
        assert listed["devices"][0]["last_path"] == "/api/presence/heartbeat"

        status, audit = _request(base, "/api/remote/audit?limit=5")
        assert status == 200, audit
        actions = [entry["action"] for entry in audit["entries"]]
        assert "queue" in actions
        assert audit["entries"][-1]["actor_name"] == "phone"
    finally:
        server.shutdown()
        server.server_close()
        state.close()
        store.close()
