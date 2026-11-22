from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import pytest

from spark.config import SparkConfig
from spark.store import SessionStore
from spark.web.remote_access import RemoteAccessError, normalize_public_url, qr_data_url
from spark.web.server import build_web_server


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


_MISSING = object()


def _request(
    base: str,
    path: str,
    body: object = _MISSING,
    *,
    token: str = "",
    host: str = "",
    origin: str = "",
) -> tuple[int, dict]:
    data = None if body is _MISSING else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    if token:
        headers["X-Spark-Token"] = token
    if host:
        headers["Host"] = host
    if origin:
        headers["Origin"] = origin
    request = urllib.request.Request(
        base + path,
        data=data,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture()
def public_web(tmp_path: Path):
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock-model"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "remote-access.db")
    port = _free_port()
    server, state = build_web_server(
        workdir=tmp_path,
        cfg=cfg,
        store=store,
        host="127.0.0.1",
        port=port,
        public_url="https://spark.example.com",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            _request(base, "/api/remote/status")
            break
        except (urllib.error.URLError, OSError):
            time.sleep(0.05)
    else:
        server.shutdown()
        server.server_close()
        store.close()
        pytest.fail("server did not start")
    try:
        yield base, state, port
    finally:
        server.shutdown()
        server.server_close()
        state.close()
        store.close()


def test_public_url_validation() -> None:
    assert normalize_public_url(" https://spark.example.com/ ") == "https://spark.example.com"
    assert normalize_public_url("http://spark.example.com:8080") == "http://spark.example.com:8080"
    for value in (
        "https://spark.example.com/path",
        "https://spark.example.com?x=1",
        "https://user:pass@spark.example.com",
        "http://127.0.0.1:8899",
    ):
        with pytest.raises(RemoteAccessError):
            normalize_public_url(value)


def test_qr_data_url_is_self_contained() -> None:
    value = qr_data_url("http://192.168.1.4:8899/#pair=123456")
    assert value.startswith("data:image/svg+xml;base64,")
    assert len(value) > 1000


def test_public_origin_never_gets_the_loopback_control_role(public_web) -> None:
    base, _state, port = public_web
    # A reverse proxy connects from loopback, but the original Host is public.
    public_host = "spark.example.com"
    status, body = _request(base, "/api/status", host=public_host)
    assert status == 401, body
    assert "unauthorized" in body["error"]

    # Administrative remote-access endpoints must not be reachable through the
    # public origin even when the source address is 127.0.0.1.
    status, body = _request(
        base,
        "/api/remote/link",
        {},
        host=public_host,
        origin="https://spark.example.com",
    )
    assert status == 401, body


def test_remote_link_qr_auto_pair_and_stop(public_web) -> None:
    base, _state, _port = public_web
    status, remote = _request(base, "/api/remote/status")
    assert status == 200, remote
    assert remote["mode"] == "public"
    assert remote["public_url"] == "https://spark.example.com"

    status, link = _request(base, "/api/remote/link", {})
    assert status == 200, link
    assert link["url"].startswith("https://spark.example.com/#pair=")
    assert len(link["code"]) == 6 and link["code"].isdigit()
    assert link["qr_data_url"].startswith("data:image/svg+xml;base64,")

    # The link fragment carries the one-time code; claiming through the public
    # Host + Origin is the browser path a phone takes.
    status, claim = _request(
        base,
        "/api/pair/claim",
        {"code": link["code"], "name": "phone"},
        host="spark.example.com",
        origin="https://spark.example.com",
    )
    assert status == 200, claim
    token = claim["token"]

    # A paired phone can use the app but cannot mint another link or stop the
    # whole remote service.
    status, body = _request(
        base,
        "/api/remote/link",
        {},
        token=token,
        host="spark.example.com",
        origin="https://spark.example.com",
    )
    assert status == 403, body
    status, body = _request(
        base,
        "/api/remote/stop",
        {},
        token=token,
        host="spark.example.com",
        origin="https://spark.example.com",
    )
    assert status == 403, body

    status, stopped = _request(base, "/api/remote/stop", {})
    assert status == 200, stopped
    assert stopped["status"]["devices"] == 0
    status, body = _request(
        base,
        "/api/status",
        token=token,
        host="spark.example.com",
    )
    assert status == 401, body


def test_refreshing_a_qr_invalidates_the_previous_code(public_web) -> None:
    base, _state, _port = public_web
    first = _request(base, "/api/remote/link", {})[1]
    second = _request(base, "/api/remote/link", {})[1]
    assert first["code"] != second["code"]
    status, body = _request(
        base,
        "/api/pair/claim",
        {"code": first["code"], "name": "stale phone"},
        host="spark.example.com",
        origin="https://spark.example.com",
    )
    assert status == 400, body
    status, claim = _request(
        base,
        "/api/pair/claim",
        {"code": second["code"], "name": "phone"},
        host="spark.example.com",
        origin="https://spark.example.com",
    )
    assert status == 200, claim


def test_lan_link_claim_uses_the_advertised_lan_host(tmp_path: Path, monkeypatch) -> None:
    # The sandbox has no physical LAN address. Use a synthetic one while still
    # dialing the listener through loopback, so the Host/Origin checks are the
    # real code path described in docs/REMOTE.md.
    from spark.web.remote_access import RemoteAccessManager

    monkeypatch.setattr(
        RemoteAccessManager,
        "_detect_lan_ipv4",
        staticmethod(lambda: "192.168.50.23"),
    )
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock-model"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "lan.db")
    port = _free_port()
    server, state = build_web_server(
        workdir=tmp_path,
        cfg=cfg,
        store=store,
        host="127.0.0.1",
        port=port,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        status, link = _request(base, "/api/remote/link", {})
        assert status == 200, link
        parsed = urlparse(link["lan_url"])
        assert parsed.hostname == "192.168.50.23"
        lan_host = f"192.168.50.23:{parsed.port}"
        status, claim = _request(
            f"http://127.0.0.1:{parsed.port}",
            "/api/pair/claim",
            {"code": link["code"], "name": "phone-on-lan"},
            host=lan_host,
            origin=f"http://{lan_host}",
        )
        assert status == 200, claim
        token = claim["token"]
        status, body = _request(
            f"http://127.0.0.1:{parsed.port}",
            "/api/status",
            token=token,
            host=lan_host,
        )
        assert status == 200, body
    finally:
        server.shutdown()
        server.server_close()
        state.close()
        store.close()
