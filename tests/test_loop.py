from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.core.loop import AgentLoop
from spark.models import ApprovalDecision, ApprovalRequest, ChatDelta, ToolCall
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry


def _loop(tmp_path: Path, provider: MockProvider, approval: str = "full-auto", max_rounds: int = 30, approver=None) -> AgentLoop:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = approval
    cfg.agent.max_tool_rounds = max_rounds
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session(tmp_path, "mock")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
    registry = ToolRegistry(ctx)
    return AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=provider,
        registry=registry,
        store=store,
        session_id=sid,
        approver=approver,
    )


@pytest.mark.asyncio
async def test_loop_tool_then_final(tmp_path: Path) -> None:
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(type="tool_call", tool_call=ToolCall(id="c1", name="list_dir", arguments={"path": "."})),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="listed"), ChatDelta(type="end")],
        ]
    )
    loop = _loop(tmp_path, provider)
    events = await loop.run("list files")
    types = [e.type for e in events]
    assert "tool_start" in types
    assert "tool_end" in types
    assert types[-1] == "turn_end"
    assert provider.calls == 2
    messages = loop.store.load_messages(loop.session_id)
    assert any(m.role == "tool" for m in messages)


@pytest.mark.asyncio
async def test_max_tool_rounds(tmp_path: Path) -> None:
    call = ChatDelta(type="tool_call", tool_call=ToolCall(id="c1", name="list_dir", arguments={"path": "."}))
    provider = MockProvider(rounds=[[call, ChatDelta(type="end")] for _ in range(5)])
    loop = _loop(tmp_path, provider, max_rounds=2)
    events = await loop.run("loop forever")
    assert events[-1].type == "turn_error"
    assert (events[-1].data or {}).get("error", {}).get("code") == "tool_loop_limit"


@pytest.mark.asyncio
async def test_deny_continues_loop(tmp_path: Path) -> None:
    async def deny(_req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(tool_call_id=_req.tool_call.id, action="deny")

    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(id="c1", name="write_file", arguments={"path": "x.txt", "content": "no"}),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="ok skipped"), ChatDelta(type="end")],
        ]
    )
    loop = _loop(tmp_path, provider, approval="suggest", approver=deny)
    events = await loop.run("write x")
    assert not (tmp_path / "x.txt").exists()
    denied = [e for e in events if e.type == "tool_end"][0]
    assert denied.result and denied.result.payload.get("error") == "denied"
    assert events[-1].type == "turn_end"
