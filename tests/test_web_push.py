from __future__ import annotations

import json
import os
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
from spark.web.web_push import WebPushManager


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(base: str, path: str, body: object | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(
        base + path,
        data=data,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _subscription() -> dict:
    return {
        "endpoint": "https://push.example.test/v1/abc",
        "expirationTime": None,
        "keys": {"p256dh": "B" + "x" * 80, "auth": "y" * 22},
    }


def test_manager_creates_private_key_and_subscription(tmp_path: Path) -> None:
    manager = WebPushManager(tmp_path)
    status = manager.status()
    assert status["public_key"]
    assert len(status["public_key"]) > 50
    if os.name != "nt":
        assert (tmp_path / "webpush.json").stat().st_mode & 0o777 == 0o600

    record = manager.subscribe(_subscription(), device_id="dev_1", device_name="phone")
    assert record["device_name"] == "phone"
    assert manager.status()["subscriptions"] == 1
    assert manager.unsubscribe(_subscription()["endpoint"]) is True
    assert manager.status()["subscriptions"] == 0


def test_manager_rejects_non_https_push_endpoint(tmp_path: Path) -> None:
    manager = WebPushManager(tmp_path)
    bad = {**_subscription(), "endpoint": "http://example.com/push"}
    with pytest.raises(Exception):
        manager.subscribe(bad)


def test_push_api_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SPARK_HOME", str(tmp_path))
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock"
    store = SessionStore(tmp_path / "push.db")
    port = _free_port()
    server, state = build_web_server(
        workdir=tmp_path,
        cfg=cfg,
        store=store,
        host="127.0.0.1",
        port=port,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            _request(base, "/api/push/status")
            break
        except OSError:
            time.sleep(0.05)
    try:
        status, body = _request(base, "/api/push/status")
        assert status == 200, body
        assert body["available"] is True
        assert body["subscriptions"] == 0

        status, body = _request(
            base, "/api/push/subscribe", {"subscription": _subscription(), "events": ["approval"]}
        )
        assert status == 200, body
        assert body["subscription"]["events"] == ["approval"]

        status, body = _request(base, "/api/push/status")
        assert status == 200, body
        assert body["subscriptions"] == 1
        assert body["devices"][0]["device_id"] == "local"

        status, body = _request(
            base, "/api/push/unsubscribe", {"endpoint": _subscription()["endpoint"]}
        )
        assert status == 200, body
        assert body["status"]["subscriptions"] == 0
    finally:
        server.shutdown()
        server.server_close()
        state.close()
        store.close()


def test_notify_delivers_and_prunes_gone_subscription(tmp_path: Path, monkeypatch) -> None:
    import pywebpush

    manager = WebPushManager(tmp_path)
    manager.subscribe(_subscription(), device_id="dev_1", device_name="phone")
    calls: list[dict] = []

    def fake_webpush(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    assert manager.notify("approval", "title", "body") == 1
    deadline = time.time() + 3
    while time.time() < deadline and not calls:
        time.sleep(0.02)
    assert len(calls) == 1
    assert calls[0]["subscription_info"]["endpoint"] == _subscription()["endpoint"]
    assert "vapid_private_key" in calls[0]

    class _Gone:
        status_code = 410

    def gone(**kwargs):
        raise pywebpush.WebPushException("gone", _Gone())

    monkeypatch.setattr(pywebpush, "webpush", gone)
    assert manager.notify("approval", "title", "body") == 1
    deadline = time.time() + 3
    while time.time() < deadline and manager.status()["subscriptions"]:
        time.sleep(0.02)
    assert manager.status()["subscriptions"] == 0
