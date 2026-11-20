from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.core.loop import AgentLoop
from spark.models import ChatDelta, ChatMessage, ToolCall
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry


class ScriptedProvider:
    """Replays a fixed list of delta batches; repeats the last batch when exhausted."""

    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = 0

    async def stream(self, messages: list[ChatMessage], tools: list[dict]):
        index = min(self.calls, len(self.batches) - 1)
        self.calls += 1
        for delta in self.batches[index]:
            yield delta


def text_delta(text: str) -> ChatDelta:
    return ChatDelta(type="text", text=text)


def tool_delta(name: str, args: dict, call_id: str = "c1") -> ChatDelta:
    return ChatDelta(
        type="tool_call", tool_call=ToolCall(id=call_id, name=name, arguments=args)
    )


def make_loop(tmp_path: Path, provider, **agent_overrides):
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    for key, value in agent_overrides.items():
        setattr(cfg.agent, key, value)
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
    store = SessionStore(tmp_path / "reliability.db")
    sid = store.create_session(tmp_path, "mock", title="t")
    loop = AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=provider,
        registry=ToolRegistry(ctx),
        store=store,
        session_id=sid,
    )
    return loop, store


async def collect(loop, text="go"):
    return [event async for event in loop.iter_turn(text)]


# ---------- P0 circuit breaker ----------


@pytest.mark.asyncio
async def test_repeated_identical_tool_call_trips_circuit_breaker(
    tmp_path: Path,
) -> None:
    call = tool_delta("read_file", {"path": "same.py"})
    provider = ScriptedProvider([[call, text_delta("again")]] * 6)
    loop, store = make_loop(tmp_path, provider, max_repeat_calls=3)
    events = await collect(loop)
    errors = [event for event in events if event.type == "turn_error"]
    assert errors
    assert errors[-1].data["error"]["code"] == "tool_repeat_loop"
    store.close()


@pytest.mark.asyncio
async def test_distinct_tool_calls_do_not_trip_circuit_breaker(tmp_path: Path) -> None:
    batches = [
        [tool_delta("read_file", {"path": f"f{i}.py"}, call_id=f"c{i}")]
        for i in range(4)
    ]
    batches.append([text_delta("finished")])
    provider = ScriptedProvider(batches)
    loop, store = make_loop(tmp_path, provider, max_repeat_calls=2)
    events = await collect(loop)
    assert not [event for event in events if event.type == "turn_error"]
    assert [event for event in events if event.type == "turn_end"]
    store.close()


@pytest.mark.asyncio
async def test_circuit_breaker_reports_progress_in_context_event(
    tmp_path: Path,
) -> None:
    call = tool_delta("read_file", {"path": "same.py"})
    provider = ScriptedProvider([[call, text_delta("again")]] * 6)
    loop, store = make_loop(tmp_path, provider, max_repeat_calls=3)
    events = await collect(loop)
    guard = [
        event
        for event in events
        if event.type == "context" and (event.data or {}).get("circuit_breaker")
    ]
    assert guard
    store.close()


# ---------- P0 token budget ----------


@pytest.mark.asyncio
async def test_token_budget_stops_turn(tmp_path: Path) -> None:
    # first round: a tool call (no text), second round: huge text pushes over budget
    batches = [
        [tool_delta("read_file", {"path": "a.py"}, call_id="c0")],
        [text_delta("x " * 6000), text_delta("y " * 6000)],
        [text_delta("z " * 6000)],
    ]
    provider = ScriptedProvider(batches)
    loop, store = make_loop(tmp_path, provider, max_turn_tokens=200)
    events = await collect(loop)
    errors = [event.text for event in events if event.type == "turn_error"]
    assert errors, "expected budget stop"
    assert "token budget" in errors[-1].lower()
    store.close()


@pytest.mark.asyncio
async def test_token_budget_zero_disables_limit(tmp_path: Path) -> None:
    provider = ScriptedProvider([[text_delta("x " * 4000)]])
    loop, store = make_loop(tmp_path, provider, max_turn_tokens=0)
    events = await collect(loop)
    assert not [event for event in events if event.type == "turn_error"]
    assert [event for event in events if event.type == "turn_end"]
    store.close()


@pytest.mark.asyncio
async def test_usage_event_exposes_token_spend(tmp_path: Path) -> None:
    provider = ScriptedProvider([[text_delta("hello")]])
    loop, store = make_loop(tmp_path, provider)
    events = await collect(loop)
    usage = [
        event
        for event in events
        if event.type == "context" and "turn_tokens" in (event.data or {})
    ]
    assert usage
    assert usage[-1].data["turn_tokens"] > 0
    store.close()


# ---------- P1 subagent cancellation ----------


class BlockingProvider:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = False

    async def stream(self, messages: list[ChatMessage], tools: list[dict]):
        self.entered.set()
        while not self.release:
            await asyncio.sleep(0.01)
        yield ChatDelta(type="end")


@pytest.mark.asyncio
async def test_parent_cancel_stops_running_subtask(tmp_path: Path) -> None:
    provider = BlockingProvider()
    loop, store = make_loop(tmp_path, provider)
    events_task = asyncio.create_task(collect(loop))
    await asyncio.wait_for(provider.entered.wait(), timeout=5)
    loop.cancel()
    provider.release = True
    events = await asyncio.wait_for(events_task, timeout=10)
    errors = [event.text for event in events if event.type == "turn_error"]
    assert errors
    store.close()


@pytest.mark.asyncio
async def test_subtask_surfaces_provider_failure(tmp_path: Path) -> None:
    class BoomProvider:
        async def stream(self, messages, tools):
            raise RuntimeError("provider exploded")
            yield ChatDelta(type="end")

    loop, store = make_loop(tmp_path, BoomProvider())
    events = [event async for event in loop.spawn_subtask("do it")]
    assert [event for event in events if event.type == "turn_end"]
    assert any("provider exploded" in (event.text or "") for event in events)
    store.close()
