"""RC-05: one-shot 30-minute device_admin scope."""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from spark.web.remote import ADMIN_TTL_SEC, DevicePairing, PairingError


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


def test_admin_scope_is_one_shot_and_expires_with_audit_listener() -> None:
    now = [1000.0]
    pairing = DevicePairing(clock=lambda: now[0])
    token = pairing.claim(pairing.new_code()["code"], "phone")["token"]
    device_id = pairing.device_for_token(token)["id"]
    expired: list[dict] = []
    pairing.set_admin_expiry_listener(expired.append)

    granted = pairing.grant_admin(device_id)
    assert granted["scope"] == "device_admin"
    assert granted["admin_expires_in"] == ADMIN_TTL_SEC
    assert granted["admin_used"] is True

    scope = pairing.admin_scope(token)
    assert scope is not None and scope["scope"] == "device_admin"
    assert pairing.admin_scope(token)["admin_expires_in"] == ADMIN_TTL_SEC

    now[0] += ADMIN_TTL_SEC + 1
    downgraded = pairing.admin_scope(token)
    assert downgraded is not None
    assert downgraded["scope"] == "device"
    assert expired and expired[0]["id"] == device_id

    with pytest.raises(PairingError, match="re-pair"):
        pairing.grant_admin(device_id)

    fresh = pairing.claim(pairing.new_code()["code"], "new phone")["token"]
    fresh_id = pairing.device_for_token(fresh)["id"]
    assert pairing.grant_admin(fresh_id)["scope"] == "device_admin"


def test_admin_scope_can_be_revoked_early_and_stays_used() -> None:
    now = [2000.0]
    pairing = DevicePairing(clock=lambda: now[0])
    token = pairing.claim(pairing.new_code()["code"], "phone")["token"]
    device_id = pairing.device_for_token(token)["id"]
    pairing.grant_admin(device_id)
    revoked = pairing.revoke_admin(device_id)
    assert revoked is not None and revoked["scope"] == "device"
    assert pairing.admin_scope(token)["scope"] == "device"
    with pytest.raises(PairingError, match="re-pair"):
        pairing.grant_admin(device_id)


def test_device_admin_http_boundary_and_reaudit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SPARK_HOME", str(tmp_path))
    from spark.config import SparkConfig
    from spark.store import SessionStore
    from spark.web.server import build_web_server

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock"
    store = SessionStore(tmp_path / "admin.db")
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
    host = "spark.example.com"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            _request(base, "/api/remote/status")
            break
        except OSError:
            time.sleep(0.05)
    try:
        status, minted = _request(base, "/api/pair/code", {})
        assert status == 200, minted
        status, claimed = _request(
            base, "/api/pair/claim", {"code": minted["code"], "name": "phone"}
        )
        assert status == 200, claimed
        token = claimed["token"]
        device_id = claimed["device"]["id"]

        # Baseline: an ordinary device cannot change settings or plugins.
        status, body = _request(
            base, "/api/settings", {"agent": {"show_thinking": True}}, token=token, host=host
        )
        assert status == 403, body
        status, body = _request(
            base, "/api/plugins", {"action": "disable", "name": "missing"}, token=token, host=host
        )
        assert status == 403, body

        status, granted = _request(
            base,
            "/api/remote/admin",
            {"action": "grant", "device_id": device_id},
        )
        assert status == 200, granted
        assert granted["device"]["scope"] == "device_admin"
        assert granted["device"]["admin_expires_in"] == ADMIN_TTL_SEC

        status, beat = _request(
            base, "/api/presence/heartbeat", {}, token=token, host=host
        )
        assert status == 200, beat
        assert beat["device"]["scope"] == "device_admin"
        assert beat["device"]["admin_expires_in"] > 1700

        # Non-shell settings are now writable through the same guard.
        status, saved = _request(
            base, "/api/settings", {"agent": {"show_thinking": True}}, token=token, host=host
        )
        assert status == 200, saved
        # Shell/approval/sandbox and execution-oriented fields stay denied.
        for body in (
            {"agent": {"approval": "full-auto"}},
            {"agent": {"sandbox_mode": "danger-full-access"}},
            {"agent": {"network_access": False}},
            {"hooks": []},
        ):
            status, denied = _request(base, "/api/settings", body, token=token, host=host)
            assert status == 403, (body, denied)
            assert denied["code"] == "device_admin_field_denied"

        # Plugins are reachable; the missing name returns 404 rather than 403.
        status, missing = _request(
            base, "/api/plugins", {"action": "disable", "name": "missing"}, token=token, host=host
        )
        assert status == 404, missing

        for path, body in (
            ("/api/shell", {"command": "echo hi"}),
            ("/api/pty/open", {}),
            ("/api/remote/link", {"mode": "auto"}),
            ("/api/remote/admin", {"action": "grant", "device_id": device_id}),
        ):
            status, denied = _request(base, path, body, token=token, host=host)
            assert status == 403, (path, denied)
            assert denied.get("code") == "remote_path_denied"

        # Force the 30-minute clock past its edge: the next device-list read
        # downgrades the scope and records an expiry audit entry.
        state.pairing._devices[device_id].admin_expires_at = time.time() - 1
        status, listed = _request(base, "/api/pair/devices")
        assert status == 200, listed
        assert listed["devices"][0]["scope"] == "device"
        status, body = _request(
            base, "/api/settings", {"agent": {"show_thinking": True}}, token=token, host=host
        )
        assert status == 403, body

        status, audit = _request(base, "/api/remote/audit?limit=20")
        assert status == 200, audit
        assert any(
            entry["action"] == "device.admin.expired"
            for entry in audit["entries"]
        )

        status, revoked = _request(
            base,
            "/api/remote/admin",
            {"action": "revoke", "device_id": device_id},
        )
        assert status == 200, revoked
        assert revoked["device"]["scope"] == "device"
        status, body = _request(
            base, "/api/settings", {"agent": {"show_thinking": False}}, token=token, host=host
        )
        assert status == 403, body

        status, again = _request(
            base,
            "/api/remote/admin",
            {"action": "grant", "device_id": device_id},
        )
        assert status == 409, again
        assert "re-pair" in again["error"]

        status, audit = _request(base, "/api/remote/audit?limit=50")
        assert status == 200, audit
        actions = [entry["action"] for entry in audit["entries"]]
        assert "device.admin.grant" in actions
        assert "device.admin.revoke" in actions
    finally:
        server.shutdown()
        server.server_close()
        state.close()
        store.close()
