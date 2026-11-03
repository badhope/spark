"""A2A (Agent2Agent) protocol server — Agent Card / message/send / tasks/get。"""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from spark.a2a_server import serve_a2a
from spark.config import SparkConfig


@pytest.fixture()
def a2a(tmp_path: Path):
    """起一个 mock 模式的 A2A server（系统随机端口），测完关闭。"""
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock"
    cfg.agent.approval = "full-auto"
    workdir = tmp_path / "wd"
    workdir.mkdir()
    server, base_url = serve_a2a(workdir, cfg, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield base_url, server
    server.shutdown()
    server.server_close()
    if hasattr(server, "a2a_state"):
        server.a2a_state.close()


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base}{path}", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(base: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{base}/",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_agent_card_discovery(a2a) -> None:
    base, _ = a2a
    for path in ("/.well-known/agent-card.json", "/agent.json"):
        card = _get(base, path)
        assert card["name"] == "spark"
        assert card["url"] == base, "card.url 必须是真实绑定地址"
        assert card["capabilities"]["streaming"] is False
        assert card["skills"], "至少声明一个 skill"


def test_health_and_unknown_path(a2a) -> None:
    base, _ = a2a
    assert _get(base, "/health") == {"ok": True}
    with pytest.raises(urllib.error.HTTPError) as err:
        _get(base, "/nope")
    assert err.value.code == 404


def test_message_send_completes_with_artifact(a2a) -> None:
    base, _ = a2a
    resp = _post(
        base,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "hello"}],
                    "messageId": "m-1",
                    "contextId": "ctx-a",
                }
            },
        },
    )
    assert "error" not in resp, resp
    task = resp["result"]
    assert task["kind"] == "task"
    assert task["status"]["state"] == "completed"
    assert task["contextId"] == "ctx-a"
    artifacts = task.get("artifacts") or []
    assert artifacts and artifacts[0]["parts"][0]["kind"] == "text"


def test_tasks_get_roundtrip(a2a) -> None:
    base, _ = a2a
    resp = _post(
        base,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "hello again"}],
                    "taskId": "task-fixed",
                }
            },
        },
    )
    assert resp["result"]["status"]["state"] == "completed"
    got = _post(
        base,
        {"jsonrpc": "2.0", "id": 3, "method": "tasks/get", "params": {"id": "task-fixed"}},
    )
    assert got["result"]["id"] == "task-fixed"
    assert got["result"]["status"]["state"] == "completed"


def test_tasks_get_unknown_task(a2a) -> None:
    base, _ = a2a
    resp = _post(
        base,
        {"jsonrpc": "2.0", "id": 4, "method": "tasks/get", "params": {"id": "ghost"}},
    )
    assert resp["error"]["code"] == -32001


def test_unsupported_streaming_capability_is_rejected(a2a) -> None:
    base, _ = a2a
    resp = _post(
        base,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "message/stream",
            "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "x"}]}},
        },
    )
    assert resp["error"]["code"] == -32601
    assert "not supported" in resp["error"]["message"]


def test_unknown_method_and_protocol_errors(a2a) -> None:
    base, _ = a2a
    assert _post(base, {"jsonrpc": "2.0", "id": 6, "method": "no/such"})["error"][
        "code"
    ] == -32601
    assert _post(base, {"id": 7, "method": "ping"})["error"]["code"] == -32600
    # ping 正常返回空结果
    assert _post(base, {"jsonrpc": "2.0", "id": 8, "method": "ping"})["result"] == {}


def test_message_send_rejects_empty_text(a2a) -> None:
    base, _ = a2a
    resp = _post(
        base,
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "message/send",
            "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "  "}]}},
        },
    )
    assert resp["error"]["code"] == -32602


def test_same_context_reuses_one_session(tmp_path: Path) -> None:
    """同一 contextId 复用同一会话；不同 contextId 各自独立。"""
    from spark.a2a_server import A2AState

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    state = A2AState(tmp_path, cfg)
    try:
        state.send_message(1, {"message": {"parts": [{"kind": "text", "text": "a"}], "contextId": "c1"}})
        state.send_message(2, {"message": {"parts": [{"kind": "text", "text": "b"}], "contextId": "c1"}})
        state.send_message(3, {"message": {"parts": [{"kind": "text", "text": "c"}], "contextId": "c2"}})
        assert len(state._loops) == 2, "c1 两个请求必须复用一个会话"
    finally:
        state.close()
