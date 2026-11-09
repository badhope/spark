"""本轮排队调用必须响应取消（第二轮深挖回归）。"""
from __future__ import annotations

import pytest

from spark.config import SparkConfig
from spark.core.loop import AgentLoop
from spark.models import ApprovalDecision, ChatDelta, ToolCall, ToolResult
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry


class TwoCallProvider:
    def __init__(self) -> None:
        self.streams = 0

    async def stream(self, messages, tools, **kw):
        self.streams += 1
        yield ChatDelta(
            type="tool_call", tool_call=ToolCall(id="a", name="fake1", arguments={})
        )
        yield ChatDelta(
            type="tool_call", tool_call=ToolCall(id="b", name="fake2", arguments={})
        )
        yield ChatDelta(type="end")


@pytest.mark.asyncio
async def test_cancel_skips_remaining_tool_calls(tmp_path) -> None:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock-model"
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session(tmp_path, "mock-model")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
    provider = TwoCallProvider()

    async def always_allow(request) -> ApprovalDecision:
        return ApprovalDecision(action="allow", tool_call_id=request.tool_call.id)

    loop = AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=provider,  # type: ignore[arg-type]
        registry=ToolRegistry(ctx),
        store=store,
        session_id=sid,
        approver=always_allow,
    )
    executed: list[str] = []

    async def fake_execute(call):
        executed.append(call.name)
        loop.cancel()  # 用户在第一个工具执行中按下停止
        return ToolResult(ok=True, payload={"ran": call.name})

    loop.registry.execute = fake_execute  # type: ignore[method-assign]
    events = [ev async for ev in loop.iter_turn("go")]

    assert executed == ["fake1"], "取消后本轮排队调用不得再执行"
    tool_msgs = [m for m in loop.history if m.role == "tool"]
    assert len(tool_msgs) == 2, "tool_call/result 必须配对，否则下一轮请求 400"
    assert "cancelled before execution" in tool_msgs[1].content
    ends = [ev for ev in events if ev.type == "tool_end"]
    assert len(ends) == 2
    assert ends[0].result.ok and not ends[1].result.ok
    assert any(ev.type == "turn_error" for ev in events)
    assert provider.streams == 1, "取消后不得再发起 LLM 流"
    store.close()
