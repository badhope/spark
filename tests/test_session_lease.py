"""RC-04: session owner leases, takeover and two-device conflicts."""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from spark.web.state import SessionRuntime


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


def test_lease_ttl_takeover_and_release() -> None:
    runtime = SessionRuntime("s1")
    first = runtime.lease_claim("dev:a", now=1000.0)
    assert first["ok"] is True and first["owner"] == "dev:a"
    assert first["expires_in"] == 120

    blocked = runtime.lease_claim("dev:b", now=1000.1)
    assert blocked["ok"] is False and blocked["reason"] == "lease_held"
    assert runtime.lease_allows("dev:a", now=1000.1) is True
    assert runtime.lease_allows("dev:b", now=1000.1) is False

    renewed = runtime.lease_claim("dev:a", now=1100.0)
    assert renewed["ok"] is True and renewed["expires_in"] == 120

    assert runtime.lease_allows("dev:a", now=1221.0) is False
    assert runtime.lease_snapshot(now=1221.0)["held"] is False

    second = runtime.lease_claim("dev:b", now=1300.0)
    assert second["ok"] is True and second["owner"] == "dev:b"
    takeover = runtime.lease_claim("dev:c", takeover=True, now=1301.0)
    assert takeover["ok"] is True and takeover["took_over"] == "dev:b"

    runtime.lease_release("dev:a")
    assert runtime.lease_snapshot(now=1302.0)["owner"] == "dev:c"
    runtime.lease_release("dev:c")
    assert runtime.lease_snapshot(now=1302.0)["held"] is False


def test_local_control_bypasses_device_lease() -> None:
    runtime = SessionRuntime("s1")
    runtime.lease_claim("dev:a", now=100.0)
    assert runtime.lease_allows("local", now=100.0) is True
    assert runtime.lease_renew("local") is True
    local_claim = runtime.lease_claim("local", now=100.0)
    assert local_claim["ok"] is True and local_claim["took_over"] == "dev:a"
    assert runtime.lease_snapshot(now=100.0)["owner"] == "local"
    runtime.lease_release("local")
    assert runtime.lease_snapshot(now=100.0)["held"] is False


def test_two_devices_lease_conflict_and_takeover(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SPARK_HOME", str(tmp_path))
    from spark.config import SparkConfig
    from spark.store import SessionStore
    from spark.web.server import build_web_server

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock"
    store = SessionStore(tmp_path / "lease.db")
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
        tokens: dict[str, str] = {}
        for name in ("phone-a", "phone-b"):
            status, minted = _request(base, "/api/pair/code", {})
            assert status == 200 and minted["code"]
            status, claimed = _request(
                base, "/api/pair/claim", {"code": minted["code"], "name": name}
            )
            assert status == 200, claimed
            tokens[name] = claimed["token"]
        a, b = tokens["phone-a"], tokens["phone-b"]

        status, first = _request(
            base, "/api/session/lease", {"action": "acquire"}, token=a, host=host
        )
        assert status == 200, first
        assert first["lease"]["owner"].startswith("dev:")

        status, seen = _request(base, "/api/session/lease", token=b, host=host)
        assert status == 200, seen
        assert seen["caller"].startswith("dev:")
        assert seen["lease"]["held"] is True

        # B is not the owner: every mutating session endpoint is rejected
        # before it can touch the runtime.
        for path, body in (
            ("/api/chat", {"prompt": "hello"}),
            ("/api/chat/stream", {"prompt": "hello"}),
            ("/api/approval", {"decision": "allow"}),
            ("/api/question/answer", {"answer": "yes"}),
            ("/api/cancel", {}),
        ):
            status, denied = _request(base, path, body, token=b, host=host)
            assert status == 409, (path, denied)
            assert denied["code"] == "lease_held"

        # Holder A can write, and a heartbeat extends its lease.
        assert state.current_runtime is not None
        state.current_runtime.lease_until = time.monotonic() + 5
        status, heartbeat = _request(
            base, "/api/presence/heartbeat", {}, token=a, host=host
        )
        assert status == 200, heartbeat
        assert heartbeat["lease"]["held"] is True
        assert heartbeat["lease"]["expires_in"] > 100

        # B's heartbeat refreshes presence but never steals or renews A's lease.
        status, beat_b = _request(
            base, "/api/presence/heartbeat", {}, token=b, host=host
        )
        assert status == 200, beat_b
        assert beat_b["lease"]["owner"] == first["lease"]["owner"]

        status, _ = _request(base, "/api/approval", {"decision": "allow"}, token=a, host=host)
        assert status == 200, status

        # B explicitly takes over; A is now the blocked writer.
        status, takeover = _request(
            base, "/api/session/lease", {"action": "takeover"}, token=b, host=host
        )
        assert status == 200, takeover
        assert takeover["lease"]["owner"].startswith("dev:")
        status, denied = _request(base, "/api/chat", {"prompt": "hello"}, token=a, host=host)
        assert status == 409 and denied["code"] == "lease_held"

        status, audit = _request(base, "/api/remote/audit?limit=20")
        assert status == 200, audit
        assert any(
            entry["action"] == "session.lease.takeover"
            for entry in audit["entries"]
        )

        # Local control is never blocked by a device lease.
        status, local = _request(base, "/api/session/lease")
        assert status == 200, local
        assert local["caller"] == "local" and local["lease"]["held"] is True
        status, cancelled = _request(base, "/api/cancel", {})
        assert status == 200, cancelled
        status, local_approval = _request(base, "/api/approval", {"decision": "deny"})
        assert status == 200, local_approval

        status, released = _request(
            base, "/api/session/lease", {"action": "release"}, token=b, host=host
        )
        assert status == 200, released
        assert released["lease"]["held"] is False
        assert state.current_runtime is not None
        assert state.current_runtime.lease_owner == ""
    finally:
        server.shutdown()
        server.server_close()
        state.close()
        store.close()
