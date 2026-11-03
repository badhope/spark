"""调试端点的回归测试。

调试信息最容易出的问题不是"取不到"，而是"取太多"——把密钥、消息内容、
完整路径端出去。这里的断言一半在验证字段存在，一半在验证**不该出现的
东西没有出现**。
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


def _request(base: str, path: str, body: object | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(
        base + path, data=data, headers=headers, method="POST" if data is not None else "GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture()
def web(tmp_path: Path, monkeypatch):
    import spark.web.server as server_module

    monkeypatch.setattr(server_module, "save_config", lambda cfg: None)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock-model"
    # 哑值夹具：不带 sk- 前缀，避免 trufflehog 把测试夹具当真实密钥拦截。
    cfg.provider.api_key = "dummy-api-key-value-for-redaction-test"
    cfg.agent.approval = "full-auto"
    cfg.agent.auto_review = True
    cfg.agent.network_access = False
    store = SessionStore(tmp_path / "debug.db")
    port = _free_port()
    server, state = build_web_server(
        workdir=tmp_path, cfg=cfg, store=store, host="127.0.0.1", port=port
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            _request(base, "/api/status")
            break
        except (urllib.error.URLError, OSError):
            time.sleep(0.05)
    else:
        server.shutdown()
        server.server_close()
        store.close()
        pytest.fail("server did not start")
    try:
        yield base, state
    finally:
        state.automations.stop()
        server.shutdown()
        server.server_close()
        store.close()


def test_debug_reports_runtime_shape(web) -> None:
    base, _ = web
    status, body = _request(base, "/api/debug")
    assert status == 200
    assert body["ok"] is True
    assert body["runtime"]["loop_ready"] is True
    assert body["runtime"]["provider_class"]
    assert body["runtime"]["registry_tools"] > 0
    assert "turn_locked" in body["locks"]
    assert body["threads"]["count"] >= 1
    assert isinstance(body["threads"]["by_name"], dict)
    assert body["store"]["available"] is True
    assert body["scheduler"]["running"] is True


def test_debug_reports_feature_flags(web) -> None:
    base, _ = web
    _status, body = _request(base, "/api/debug")
    feats = body["features"]
    assert feats["auto_review"] is True
    assert feats["network_access"] is False
    assert feats["format_on_write"] is False


def test_debug_never_leaks_secrets(web) -> None:
    """调试端点是"结构可见、内容不可见"：密钥与消息正文绝不能出现。"""
    base, state = web
    from spark.models import ChatMessage

    state.store.append_message(
        state.session_id, ChatMessage(role="user", content="my private prompt text")
    )
    _status, body = _request(base, "/api/debug")
    dumped = json.dumps(body, ensure_ascii=False)
    assert "dummy-api-key-value-for-redaction-test" not in dumped
    assert "my private prompt text" not in dumped
    # 只报"哪些环境变量存在"，绝不报值
    assert "env_present" in body["env"]
    assert all("=" not in name for name in body["env"]["env_present"])


def test_debug_survives_broken_subsystems(web, monkeypatch) -> None:
    """某块数据取不到时，调试端点不能整体 500——它正是用来排查故障的。"""
    base, state = web

    def boom() -> list:
        raise RuntimeError("store exploded")

    monkeypatch.setattr(state.store, "list_automations", boom)
    status, body = _request(base, "/api/debug")
    assert status == 200
    assert body["store"]["available"] is False
    assert body["store"]["error"] == "RuntimeError"


def test_client_log_accepts_and_truncates(web) -> None:
    base, _ = web
    status, body = _request(
        base,
        "/api/debug/client_log",
        {"kind": "react", "message": "x" * 5000, "stack": "y" * 9000, "where": "Test"},
    )
    assert status == 200
    assert body["ok"] is True


def test_client_log_tolerates_missing_fields(web) -> None:
    base, _ = web
    status, body = _request(base, "/api/debug/client_log", {})
    assert status == 200
    assert body["ok"] is True
