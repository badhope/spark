"""定时任务的 HTTP 层回归测试。

之前只测了 ``automations.py`` 的纯逻辑（cron/调度），完全没测
``_handlers_automations.py``——覆盖率只有 8%。这类"逻辑有测试、接口没测试"
的缺口最危险：逻辑是对的，但字段名拼错、action 分发漏分支，用户一样用不了。
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
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture()
def web(tmp_path: Path, monkeypatch):
    import spark.web.server as server_module

    monkeypatch.setattr(server_module, "save_config", lambda cfg: None)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock-model"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "auto-web.db")
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
        # 先停调度线程，否则它会在 store 关闭后继续查询
        state.automations.stop()
        server.shutdown()
        server.server_close()
        store.close()


def test_list_starts_empty(web) -> None:
    base, _ = web
    status, body = _request(base, "/api/automations", {"action": "list"})
    assert status == 200
    assert body["automations"] == []


def test_list_is_the_default_action(web) -> None:
    """不带 action 时按 list 处理——前端首屏就是这样调的。"""
    base, _ = web
    status, body = _request(base, "/api/automations", {})
    assert status == 200
    assert body["automations"] == []


def test_create_returns_the_record(web) -> None:
    base, state = web
    status, body = _request(
        base,
        "/api/automations",
        {"action": "create", "name": "daily", "prompt": "check deps", "cron": "0 9 * * *"},
    )
    assert status == 200, body
    rec = body["automation"]
    assert rec["name"] == "daily"
    assert rec["enabled"] is True
    assert rec["next_run_at"] > 0
    assert [r["id"] for r in state.store.list_automations()] == [rec["id"]]


def test_create_rejects_bad_cron(web) -> None:
    base, state = web
    status, _body = _request(
        base,
        "/api/automations",
        {"action": "create", "name": "x", "prompt": "p", "cron": "not a cron"},
    )
    assert status == 400
    assert state.store.list_automations() == []


def test_create_rejects_empty_name_or_prompt(web) -> None:
    base, state = web
    for payload in (
        {"action": "create", "name": "", "prompt": "p", "cron": "0 9 * * *"},
        {"action": "create", "name": "n", "prompt": "", "cron": "0 9 * * *"},
    ):
        status, _ = _request(base, "/api/automations", payload)
        assert status == 400
    assert state.store.list_automations() == []


def _create(base: str, **overrides) -> dict:
    payload = {"action": "create", "name": "t", "prompt": "p", "cron": "0 9 * * *"}
    payload.update(overrides)
    status, body = _request(base, "/api/automations", payload)
    assert status == 200, body
    return body["automation"]


def test_update_changes_fields_and_reschedules(web) -> None:
    base, state = web
    rec = _create(base)
    old_next = rec["next_run_at"]
    status, body = _request(
        base,
        "/api/automations",
        {"action": "update", "id": rec["id"], "name": "renamed", "cron": "30 10 * * *"},
    )
    assert status == 200, body
    row = state.store.get_automation(rec["id"])
    assert row["name"] == "renamed"
    assert row["cron"] == "30 10 * * *"
    assert row["next_run_at"] != old_next


def test_update_rejects_bad_cron(web) -> None:
    base, state = web
    rec = _create(base)
    status, _body = _request(
        base, "/api/automations", {"action": "update", "id": rec["id"], "cron": "bad"}
    )
    assert status == 400
    assert state.store.get_automation(rec["id"])["cron"] == "0 9 * * *"


def test_update_requires_name_and_prompt(web) -> None:
    base, state = web
    rec = _create(base)
    status, _ = _request(
        base, "/api/automations", {"action": "update", "id": rec["id"], "name": ""}
    )
    assert status == 400
    assert state.store.get_automation(rec["id"])["name"] == "t"


def test_toggle_flips_enabled(web) -> None:
    base, state = web
    rec = _create(base)
    status, body = _request(base, "/api/automations", {"action": "toggle", "id": rec["id"]})
    assert status == 200, body
    assert state.store.get_automation(rec["id"])["enabled"] == 0
    _request(base, "/api/automations", {"action": "toggle", "id": rec["id"]})
    assert state.store.get_automation(rec["id"])["enabled"] == 1


def test_delete_removes_the_record(web) -> None:
    base, state = web
    rec = _create(base)
    status, body = _request(base, "/api/automations", {"action": "delete", "id": rec["id"]})
    assert status == 200, body
    assert state.store.list_automations() == []


def test_unknown_id_returns_404(web) -> None:
    base, _ = web
    for action in ("update", "toggle", "delete", "run_now"):
        status, body = _request(base, "/api/automations", {"action": action, "id": "nope"})
        assert status == 404, (action, body)


def test_missing_id_returns_404(web) -> None:
    base, _ = web
    status, _ = _request(base, "/api/automations", {"action": "delete"})
    assert status == 404


def test_run_now_marks_due_without_executing(web) -> None:
    """run_now 只把 next_run_at 拨到过去，交给调度器；不在请求线程里跑 turn。"""
    base, state = web
    rec = _create(base)
    status, body = _request(base, "/api/automations", {"action": "run_now", "id": rec["id"]})
    assert status == 200, body
    assert body["queued"] is True
    assert state.store.get_automation(rec["id"])["next_run_at"] == 0


def test_unknown_action_rejected(web) -> None:
    base, _ = web
    status, body = _request(base, "/api/automations", {"action": "explode"})
    assert status == 400
    assert "action must be" in body["error"]


def test_cron_check_valid_returns_next_run(web) -> None:
    base, _ = web
    status, body = _request(base, "/api/automations/cron_check", {"cron": "0 9 * * *"})
    assert status == 200
    assert body["valid"] is True
    assert body["next_run_at"] > 0
    assert "T" in body["next_run_iso"]


def test_cron_check_invalid(web) -> None:
    base, _ = web
    status, body = _request(base, "/api/automations/cron_check", {"cron": "nope"})
    assert status == 200, "UI needs a 200 with valid:false, not an error status"
    assert body["valid"] is False
    assert body["error"]


def test_cron_check_empty(web) -> None:
    base, _ = web
    status, body = _request(base, "/api/automations/cron_check", {})
    assert status == 200
    assert body["valid"] is False
