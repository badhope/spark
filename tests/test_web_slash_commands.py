"""Web 内建 slash 命令：/plan /build /side /goal（第十三轮新能力）。"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.store import SessionStore
from spark.web.server import build_web_server


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture()
def server(tmp_path: Path):
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock-model"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "spark.db")
    port = _free_port()
    srv, state = build_web_server(
        workdir=tmp_path, cfg=cfg, store=store, host="127.0.0.1", port=port
    )
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.3)
    try:
        yield srv, state, port
    finally:
        srv.shutdown()
        srv.server_close()
        store.close()


def _post(port: int, path: str, body: dict) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _events_of(port: int, prompt: str) -> list[dict]:
    return _post(port, "/api/chat", {"prompt": prompt})["events"]


def _types(events: list[dict]) -> list[str]:
    return [e.get("type") for e in events]


def test_plan_build_toggle_roundtrip(server) -> None:
    _, state, port = server
    events = _events_of(port, "/plan")
    assert "approval_changed" in _types(events)
    changed = next(e for e in events if e["type"] == "approval_changed")
    assert changed["data"]["approval"] == "plan-only"
    assert state._effective_approval() == "plan-only"

    events = _events_of(port, "/build")
    changed = next(e for e in events if e["type"] == "approval_changed")
    assert changed["data"]["approval"] == "full-auto"
    assert state._effective_approval() == "full-auto"


def test_plan_mode_blocks_writes_but_reads_pass(server) -> None:
    """plan-only 生效时写工具分类为 plan_only（硬保证），读工具放行。"""
    from spark.models import ToolCall

    _, state, port = server
    _events_of(port, "/plan")
    try:
        loop = state.loop
        assert loop._approval_mode() == "plan-only"
        write_verdict = loop._classify_call(
            ToolCall(id="t1", name="write_file", arguments={"path": "a.txt", "content": "x"})
        )
        assert write_verdict["action"] == "plan_only"
        shell_verdict = loop._classify_call(
            ToolCall(id="t2", name="run_shell", arguments={"command": "echo hi"})
        )
        assert shell_verdict["action"] == "plan_only"
        read_verdict = loop._classify_call(
            ToolCall(id="t3", name="read_file", arguments={"path": "a.txt"})
        )
        assert read_verdict["action"] != "plan_only"
    finally:
        _events_of(port, "/build")


def test_goal_lifecycle_via_slash(server) -> None:
    _, state, port = server
    events = _events_of(port, "/goal 完成登录页重构")
    assert "goal_status" in _types(events)
    goal_ev = next(e for e in events if e["type"] == "goal_status")
    goal = goal_ev["data"]["goal"]
    assert goal["objective"] == "完成登录页重构"
    assert goal["status"] == "active"
    # 会话里同时只允许一个活跃目标
    events = _events_of(port, "/goal 另一个目标")
    assert any("已有活跃目标" in e.get("text", "") for e in events)
    # status 查询
    events = _events_of(port, "/goal status")
    goal_ev = next(e for e in events if e["type"] == "goal_status")
    assert goal_ev["data"]["goal"]["id"] == goal["id"]
    # cancel 收口
    events = _events_of(port, "/goal cancel")
    goal_ev = next(e for e in events if e["type"] == "goal_status")
    assert goal_ev["data"]["goal"]["status"] == "cancelled"
    assert state.store.get_active_goal(state.session_id) is None


def test_goal_evidence_gate(server) -> None:
    """complete=true 无证据必须被拒绝；带证据才放行（证据门槛是硬约束）。"""
    _, state, port = server
    from spark.tools import goal as goal_tools

    goal_tools.attach_bridges(state.store, lambda: state.session_id)
    _events_of(port, "/goal 修好全部测试")
    try:
        # 无证据 complete → 拒绝
        r = goal_tools.goal_update_tool(
            goal_tools.GoalUpdateArgs(
                checklist=[{"item": "跑测试", "done": True, "evidence": ""}],
                complete=True,
            )
        )
        assert not r.ok
        assert "证据门槛" in r.payload["error"]
        # 进展回报（不 complete）→ ok
        r = goal_tools.goal_update_tool(
            goal_tools.GoalUpdateArgs(
                checklist=[
                    {"item": "跑测试", "done": True, "evidence": "12 passed"},
                    {"item": "改样式", "done": False, "evidence": ""},
                ],
            )
        )
        assert r.ok and r.payload["completed"] is False
        # 带证据 complete → 通过，目标收口
        r = goal_tools.goal_update_tool(
            goal_tools.GoalUpdateArgs(
                checklist=[
                    {"item": "跑测试", "done": True, "evidence": "12 passed"},
                    {"item": "改样式", "done": True, "evidence": "diff 已提交"},
                ],
                complete=True,
            )
        )
        assert r.ok and r.payload["completed"] is True
        assert state.store.get_active_goal(state.session_id) is None
    finally:
        goal_tools.attach_bridges(None, None)  # type: ignore[arg-type]


def test_goal_block_injected_into_system(server) -> None:
    """活跃目标存在时，下一回合系统提示词包含目标契约。"""
    _, state, port = server
    _events_of(port, "/goal 注入验证目标")
    try:
        block = state.loop._goal_block()
        assert "注入验证目标" in block
        assert "goal_update" in block and "证据" in block
    finally:
        _events_of(port, "/goal cancel")


def test_side_slash_in_demo_mode(server) -> None:
    """mock provider 下 /side 明确告知需要真实端点，不炸、不污染主对话。"""
    _, state, port = server
    events = _events_of(port, "/side 这个仓库用什么测试框架")
    assert "side_answer" in _types(events)
    ev = next(e for e in events if e["type"] == "side_answer")
    assert ev["data"]["ok"] is False
    assert "demo" in ev["data"]["answer"] or "mock" in ev["data"]["answer"]
    # 侧聊不进主对话历史
    history = state.store.load_messages(state.session_id)
    assert not any("测试框架" in str(m.get("content") or "") for m in history)


def test_unknown_slash_still_passes_through(server) -> None:
    """/nope 不是内建命令：按普通 prompt 透传给模型（不臆造行为）。"""
    _, state, port = server
    events = _events_of(port, "/nope hello")
    assert "approval_changed" not in _types(events)
    assert any(
        e.get("type") in ("text_delta", "turn_end", "tool_start") for e in events
    )
