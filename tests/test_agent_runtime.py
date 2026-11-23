"""Tests for the shared agent runtime and durable turn checkpointer.

These cover the wiring that makes A10 crash recovery real: turns must run on a
single long-lived loop (so an aiosqlite-backed saver stays loop-affine), and a
fresh process must be able to list turns that were pending when the previous
one died.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.core.loop import AgentLoop
from spark.core.runtime import AgentRuntime
from spark.core.turn_store import (
    TurnCheckpointer,
    reset_persistent_checkpointer,
)
from spark.models import ApprovalDecision, ChatDelta, ToolCall
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry


def _loop(work: Path, provider: MockProvider, checkpointer=None, approver=None) -> AgentLoop:
    cfg = SparkConfig()
    cfg.agent.approval = "suggest"
    store = SessionStore(work / "sessions.db")
    sid = store.create_session(work, "mock")
    ctx = ToolContext(sandbox=WorkdirSandbox(work, cfg), config=cfg)
    return AgentLoop(
        workdir=work,
        cfg=cfg,
        provider=provider,
        registry=ToolRegistry(ctx),
        store=store,
        session_id=sid,
        approver=approver,
        checkpointer=checkpointer,
    )


def test_runtime_iterate_yields_in_order() -> None:
    async def gen():
        for i in range(5):
            yield i

    runtime = AgentRuntime("test-runtime-order")
    try:
        assert list(runtime.iterate(gen())) == [0, 1, 2, 3, 4]
    finally:
        runtime.close()


def test_runtime_iterate_propagates_errors() -> None:
    async def gen():
        yield 1
        raise ValueError("boom")

    runtime = AgentRuntime("test-runtime-err")
    try:
        seen = []
        with pytest.raises(ValueError, match="boom"):
            for item in runtime.iterate(gen()):
                seen.append(item)
        assert seen == [1]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_runtime_iterate_async_matches_sync() -> None:
    async def gen():
        for i in range(3):
            await asyncio.sleep(0)
            yield i

    runtime = AgentRuntime("test-runtime-async")
    try:
        out = [item async for item in runtime.iterate_async(gen())]
        assert out == [0, 1, 2]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_iter_turn_managed_runs_turn_on_shared_loop() -> None:
    work = Path(tempfile.mkdtemp())
    provider = MockProvider(rounds=[[ChatDelta(type="text", text="hi"), ChatDelta(type="end")]])
    loop = _loop(work, provider)
    events = [ev async for ev in loop.iter_turn_managed("hello")]
    assert any(ev.type == "turn_end" for ev in events)
    # turn_end repeats the final answer, so only count text_delta fragments.
    assert "".join(e.text or "" for e in events if e.type == "text_delta") == "hi"


def test_durable_checkpointer_survives_restart() -> None:
    """A turn parked on approval must be visible to a *new* checkpointer."""
    from spark.core.runtime import get_runtime, shutdown_runtime

    work = Path(tempfile.mkdtemp())
    db = work / "checkpoints.db"
    # Use the shared singleton so the loop's lifetime matches the checkpointer's
    # SQLite connection; shutting it down mid-test invalidates that connection.
    runtime = get_runtime()
    cp = TurnCheckpointer(db)
    runtime.run(cp.start(), timeout=15)
    try:
        provider = MockProvider(
            rounds=[
                [
                    ChatDelta(
                        type="tool_call",
                        tool_call=ToolCall(
                            id="c1",
                            name="write_file",
                            arguments={"path": "pending.txt", "content": "x"},
                        ),
                    ),
                    ChatDelta(type="end"),
                ],
            ]
        )
        loop = _loop(work, provider, checkpointer=cp)

        async def drive_then_abandon():
            agen = loop.iter_turn("write something")
            async for ev in agen:
                if ev.type == "approval_needed":
                    break
            await agen.aclose()  # the process "dies" here

        runtime.run(drive_then_abandon(), timeout=60)
        assert runtime.run(cp.list_pending(), timeout=15), "pending turn not recorded"
    finally:
        # Close only the checkpointer; the runtime is process-wide and owned by
        # conftest's reset hook.
        shutdown_runtime()

    # Fresh checkpointer over the same file = simulated restart.
    cp2 = TurnCheckpointer(db)
    loop2 = asyncio.new_event_loop()
    try:
        loop2.run_until_complete(cp2.start())
        pending = loop2.run_until_complete(cp2.list_pending())
    finally:
        loop2.run_until_complete(cp2.close())
        loop2.close()
    assert pending, "pending turn lost across restart"


def test_hook_fires_once_per_call_across_approval_resume() -> None:
    """LangGraph re-runs the approve node on resume; pre_tool must not repeat."""
    from spark.hooks import HookDecision, HookRegistry

    work = Path(tempfile.mkdtemp())
    calls = {"hook": 0}

    class CountingHook:
        def run(self, payload):
            calls["hook"] += 1
            return HookDecision(allow=True)

    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="c1", name="write_file", arguments={"path": "a.txt", "content": "hi"}
                    ),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="done"), ChatDelta(type="end")],
        ]
    )

    async def approver(request):
        return ApprovalDecision(tool_call_id=request.tool_call.id, action="allow")

    loop = _loop(work, provider, approver=approver)
    loop.hooks = HookRegistry()
    loop.hooks.register("pre_tool", CountingHook())

    from spark.core.runtime import get_runtime

    list(get_runtime().iterate(loop.iter_turn("write a file")))
    assert calls["hook"] == 1, "pre_tool hook ran more than once for one tool call"
    assert (work / "a.txt").exists()


def test_approval_deny_still_blocks_after_cache() -> None:
    work = Path(tempfile.mkdtemp())
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="d1", name="write_file", arguments={"path": "denied.txt", "content": "x"}
                    ),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="ok"), ChatDelta(type="end")],
        ]
    )

    async def approver(request):
        return ApprovalDecision(tool_call_id=request.tool_call.id, action="deny")

    loop = _loop(work, provider, approver=approver)
    from spark.core.runtime import get_runtime

    list(get_runtime().iterate(loop.iter_turn("write")))
    assert not (work / "denied.txt").exists()


def test_duplicate_tool_call_ids_cannot_share_a_verdict() -> None:
    """Repeated ids must not let a read-only verdict authorise a write.

    ``openai_compat`` emits ``call_0`` for every call when a provider omits ids.
    Keying the approve-node cache on the id alone meant the second call (a
    write) inherited the first call's "allow" and skipped its approval prompt
    entirely — reproduced before the key was widened to id+name+arguments.
    """
    work = Path(tempfile.mkdtemp())
    (work / "seed.txt").write_text("x", encoding="utf-8")
    asked: list[str] = []

    async def approver(request):
        asked.append(request.tool_call.name)
        return ApprovalDecision(tool_call_id=request.tool_call.id, action="deny")

    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(id="call_0", name="read_file", arguments={"path": "seed.txt"}),
                ),
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="call_0", name="write_file", arguments={"path": "sneaky.txt", "content": "x"}
                    ),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="done"), ChatDelta(type="end")],
        ]
    )
    loop = _loop(work, provider, approver=approver)

    from spark.core.runtime import get_runtime

    list(get_runtime().iterate(loop.iter_turn("go")))

    assert "write_file" in asked, "write never prompted for approval"
    assert not (work / "sneaky.txt").exists(), "write executed without approval"


def teardown_module(module) -> None:  # noqa: D103 - pytest hook
    reset_persistent_checkpointer()


def test_closed_runtime_fails_fast() -> None:
    """A dead runtime must raise immediately, not block for the full timeout.

    Regression for the Windows CI hang: ``reset_persistent_checkpointer`` runs in
    every test teardown and called ``get_runtime().run(..., timeout=10)``. When
    the runtime had been closed, each of ~690 teardowns blocked 10 s — the job
    looked hung for hours with no output.
    """
    import time as _time

    from spark.core.runtime import AgentRuntime, AgentRuntimeClosed

    rt = AgentRuntime("closed-fast")
    rt.start()
    rt.close()

    started = _time.monotonic()
    with pytest.raises(AgentRuntimeClosed):
        rt.run(asyncio.sleep(0), timeout=10)
    elapsed = _time.monotonic() - started
    assert elapsed < 1.0, f"closed runtime blocked for {elapsed:.1f}s"


def test_get_runtime_replaces_dead_singleton() -> None:
    from spark.core.runtime import get_runtime, shutdown_runtime

    first = get_runtime()
    assert first.running
    shutdown_runtime()
    second = get_runtime()
    try:
        assert second.running
        assert second is not first, "a dead runtime must be replaced, not reused"
    finally:
        shutdown_runtime()


def test_iterate_total_runtime_timeout_cancels_slow_generator() -> None:
    async def slow() -> object:
        yield "first"
        await asyncio.sleep(0.5)
        yield "second"

    runtime = AgentRuntime("test-total-timeout")
    try:
        with pytest.raises(TimeoutError, match="total runtime"):
            list(runtime.iterate(slow(), timeout=2.0, max_seconds=0.1))
    finally:
        runtime.close()
