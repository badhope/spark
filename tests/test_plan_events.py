"""plan 事件语义回归（第三轮深挖）：成功即发（含清空）、失败不重发。"""
from __future__ import annotations

import pytest

from spark.config import SparkConfig
from spark.core.loop import AgentLoop
from spark.models import ChatDelta, ToolCall
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry


class ScriptedProvider:
    def __init__(self, rounds) -> None:
        self.rounds = rounds
        self.streams = 0

    async def stream(self, messages, tools, **kw):
        batch = (
            self.rounds[self.streams]
            if self.streams < len(self.rounds)
            else [ChatDelta(type="text", text="done"), ChatDelta(type="end")]
        )
        self.streams += 1
        for delta in batch:
            yield delta


def _make_loop(tmp_path, provider):
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock-model"
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session(tmp_path, "mock-model")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
    loop = AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=provider,  # type: ignore[arg-type]
        registry=ToolRegistry(ctx),
        store=store,
        session_id=sid,
    )
    return loop, store


def _plan_call(cid: str, steps) -> ChatDelta:
    return ChatDelta(
        type="tool_call",
        tool_call=ToolCall(id=cid, name="update_plan", arguments={"steps": steps}),
    )


@pytest.mark.asyncio
async def test_empty_plan_update_emits_clear_event(tmp_path) -> None:
    provider = ScriptedProvider(
        [
            [
                _plan_call("p1", [{"title": "a", "status": "pending"}]),
                _plan_call("p2", []),
                ChatDelta(type="end"),
            ],
        ]
    )
    loop, store = _make_loop(tmp_path, provider)
    events = [ev async for ev in loop.iter_turn("go")]
    plans = [ev for ev in events if ev.type == "plan"]
    assert len(plans) == 2, "空计划也是合法更新，UI 需要它来清屏"
    assert plans[-1].data["steps"] == []
    assert loop.registry.plan == []
    store.close()


@pytest.mark.asyncio
async def test_failed_plan_update_does_not_reemit(tmp_path) -> None:
    provider = ScriptedProvider(
        [
            [
                _plan_call("p1", [{"title": "a", "status": "pending"}]),
                _plan_call("p2", "not-a-list"),
                ChatDelta(type="end"),
            ],
        ]
    )
    loop, store = _make_loop(tmp_path, provider)
    events = [ev async for ev in loop.iter_turn("go")]
    plans = [ev for ev in events if ev.type == "plan"]
    assert len(plans) == 1, "失败的更新不得重发旧计划（会重复触发审批门）"
    assert plans[0].data["steps"][0]["title"] == "a"
    store.close()
