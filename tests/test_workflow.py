from __future__ import annotations

import multiprocessing as mp
from pathlib import Path

import pytest

from spark.workflow import WorkflowError, WorkspaceLocks, load_workflow


def _bump(path: str, root: str, rounds: int) -> None:
    from spark.workflow import WorkspaceLocks

    locks = WorkspaceLocks(Path(root), timeout=10)
    target = Path(path)
    for _ in range(rounds):
        with locks.acquire([target]):
            value = int(target.read_text(encoding="utf-8")) if target.exists() else 0
            target.write_text(str(value + 1), encoding="utf-8")


def test_load_workflow_and_order() -> None:
    spec = load_workflow(Path("examples/workflows/review.yaml"))
    assert spec.name == "review-change"
    assert spec.order() == ["plan", "implement", "add-tests", "review"]
    assert [step.id for step in spec.ready(set())] == ["plan"]
    assert [step.id for step in spec.ready({"plan"})] == ["implement", "add-tests"]


def test_cycle_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "cycle.yaml"
    path.write_text(
        """
name: bad
roles:
  r: {agent: lead}
steps:
  - {id: a, role: r, prompt: a, needs: [b]}
  - {id: b, role: r, prompt: b, needs: [a]}
""",
        encoding="utf-8",
    )
    with pytest.raises(WorkflowError, match="循环"):
        load_workflow(path)


def test_unsafe_write_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "escape.yaml"
    path.write_text(
        """
name: bad
roles:
  r: {agent: lead}
steps:
  - {id: a, role: r, prompt: a, writes: ["../outside"]}
""",
        encoding="utf-8",
    )
    with pytest.raises(WorkflowError, match="越过"):
        load_workflow(path)


def test_unknown_role_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "role.yaml"
    path.write_text(
        """
name: bad
roles:
  r: {agent: lead}
steps:
  - {id: a, role: missing, prompt: a}
""",
        encoding="utf-8",
    )
    with pytest.raises(WorkflowError, match="不存在"):
        load_workflow(path)


def test_lock_escape_is_rejected(tmp_path: Path) -> None:
    locks = WorkspaceLocks(tmp_path)
    with pytest.raises(WorkflowError, match="越过"):
        with locks.acquire([tmp_path.parent / "outside.txt"]):
            pass


def test_two_processes_do_not_lose_updates(tmp_path: Path) -> None:
    target = tmp_path / "counter.txt"
    target.write_text("0", encoding="utf-8")
    ctx = mp.get_context("spawn")
    processes = [
        ctx.Process(target=_bump, args=(str(target), str(tmp_path), 25))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    assert target.read_text(encoding="utf-8") == "50"


@pytest.mark.asyncio
async def test_runner_respects_dependencies_and_outputs(tmp_path: Path) -> None:
    from spark.workflow import RoleSpec, StepSpec, WorkflowSpec, run_workflow

    spec = WorkflowSpec(
        name="demo",
        version=1,
        description="",
        roles={"r": RoleSpec(name="r")},
        steps=(
            StepSpec(id="a", role="r", prompt="a"),
            StepSpec(id="b", role="r", prompt="b", needs=("a",)),
            StepSpec(id="c", role="r", prompt="c", needs=("a",)),
            StepSpec(id="d", role="r", prompt="d", needs=("b", "c")),
        ),
    )
    started: list[str] = []

    async def runner(step, deps):
        started.append(step.id)
        await __import__("asyncio").sleep(0.01)
        return "out-" + step.id + "-" + ",".join(sorted(deps))

    run = await run_workflow(spec, runner, max_parallel=2)
    assert run.ok
    assert started[0] == "a"
    assert set(started[1:3]) == {"b", "c"}
    assert started[-1] == "d"
    assert run.outputs["d"].startswith("out-d-b,c")


@pytest.mark.asyncio
async def test_runner_retries_and_blocks_dependents() -> None:
    from spark.workflow import RoleSpec, StepSpec, WorkflowSpec, run_workflow

    spec = WorkflowSpec(
        name="retry",
        version=1,
        description="",
        roles={"r": RoleSpec(name="r")},
        steps=(
            StepSpec(id="bad", role="r", prompt="bad", max_attempts=2),
            StepSpec(id="after", role="r", prompt="after", needs=("bad",)),
            StepSpec(id="independent", role="r", prompt="independent"),
        ),
    )
    calls = {"bad": 0}

    async def runner(step, deps):
        if step.id == "bad":
            calls["bad"] += 1
            raise RuntimeError("boom")
        return "ok"

    run = await run_workflow(spec, runner)
    assert calls["bad"] == 2
    assert run.statuses["bad"] == "failed"
    assert run.statuses["after"] == "blocked"
    assert run.statuses["independent"] == "completed"
    assert not run.ok


@pytest.mark.asyncio
async def test_runner_cancel_marks_pending_cancelled() -> None:
    from spark.workflow import RoleSpec, StepSpec, WorkflowSpec, run_workflow

    spec = WorkflowSpec(
        name="cancel",
        version=1,
        description="",
        roles={"r": RoleSpec(name="r")},
        steps=(StepSpec(id="a", role="r", prompt="a"),),
    )

    async def runner(step, deps):
        return "ok"

    run = await run_workflow(spec, runner, cancel=lambda: True)
    assert run.statuses["a"] == "cancelled"


@pytest.mark.asyncio
async def test_agent_adapter_builds_prompt_and_uses_role_agent() -> None:
    from types import SimpleNamespace

    from spark.workflow import AgentWorkflowRunner, RoleSpec, StepSpec, WorkflowSpec

    class FakeLoop:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        async def spawn_subtask(self, prompt, **kwargs):
            self.calls.append((prompt, kwargs))
            yield SimpleNamespace(type="tool_start", text="")
            yield SimpleNamespace(type="turn_end", text="done:" + kwargs.get("model", ""))

    spec = WorkflowSpec(
        name="adapter",
        version=1,
        description="",
        roles={
            "lead": RoleSpec(name="lead", agent="lead"),
            "expert": RoleSpec(name="expert", agent="expert"),
        },
        steps=(
            StepSpec(id="plan", role="lead", prompt="plan it"),
            StepSpec(
                id="impl",
                role="expert",
                prompt="implement it",
                needs=("plan",),
                writes=("src/",),
            ),
        ),
    )
    loop = FakeLoop()
    events: list[tuple[str, str]] = []
    runner = AgentWorkflowRunner(
        loop,
        spec,
        agents={"expert": SimpleNamespace(body="expert rules", tools=["read_file"], model="cheap")},
        on_event=lambda step_id, event: events.append((step_id, event.type)),
    )
    assert await runner(spec.steps[0], {}) == "done:"
    assert await runner(spec.steps[1], {"plan": "PLAN-OUTPUT"}) == "done:cheap"
    prompt, kwargs = loop.calls[1]
    assert "PLAN-OUTPUT" in prompt and "src/" in prompt
    assert kwargs["system_extra"] == "expert rules"
    assert kwargs["tools_allowlist"] == ["read_file"]
    assert kwargs["model"] == "cheap"


@pytest.mark.asyncio
async def test_pause_and_resume_state_roundtrip(tmp_path: Path) -> None:
    from spark.workflow import (
        RoleSpec,
        StepSpec,
        WorkflowRun,
        WorkflowSpec,
        prepare_resume,
        run_workflow,
    )

    spec = WorkflowSpec(
        name="pause",
        version=1,
        description="",
        roles={"r": RoleSpec(name="r")},
        steps=(
            StepSpec(id="a", role="r", prompt="a"),
            StepSpec(id="b", role="r", prompt="b", needs=("a",)),
            StepSpec(id="c", role="r", prompt="c", needs=("b",)),
        ),
    )
    calls: list[str] = []

    async def runner(step, deps):
        calls.append(step.id)
        return "done-" + step.id

    first = await run_workflow(spec, runner, stop_after={"a"})
    assert first.statuses["a"] == "completed"
    assert first.statuses["b"] == "paused"
    assert first.paused

    state = first.to_state()
    restored = WorkflowRun.from_state(spec, state)
    resumed = prepare_resume(restored)
    assert resumed.statuses["a"] == "completed"
    assert resumed.statuses["b"] == "pending"
    assert resumed.outputs["a"] == "done-a"

    second = await run_workflow(
        spec,
        runner,
        initial_statuses=resumed.statuses,
        initial_outputs=resumed.outputs,
    )
    assert second.ok
    assert calls == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_rerun_single_node_resets_downstream() -> None:
    from spark.workflow import (
        RoleSpec,
        StepSpec,
        WorkflowSpec,
        rerun_steps,
        run_workflow,
    )

    spec = WorkflowSpec(
        name="rerun",
        version=1,
        description="",
        roles={"r": RoleSpec(name="r")},
        steps=(
            StepSpec(id="a", role="r", prompt="a"),
            StepSpec(id="b", role="r", prompt="b", needs=("a",)),
            StepSpec(id="c", role="r", prompt="c", needs=("b",)),
            StepSpec(id="side", role="r", prompt="side"),
        ),
    )

    async def runner(step, deps):
        return f"{step.id}-{len(deps)}"

    first = await run_workflow(spec, runner)
    assert first.ok
    second = rerun_steps(first, {"b"})
    assert second.statuses["a"] == "completed"
    assert second.statuses["b"] == "pending"
    assert second.statuses["c"] == "pending"
    assert second.statuses["side"] == "completed"
    assert "b" not in second.outputs and "c" not in second.outputs


@pytest.mark.asyncio
async def test_budget_marks_pending_steps_exceeded() -> None:
    from spark.workflow import RoleSpec, StepSpec, WorkflowSpec, run_workflow

    spec = WorkflowSpec(
        name="budget",
        version=1,
        description="",
        roles={"r": RoleSpec(name="r")},
        steps=(
            StepSpec(id="a", role="r", prompt="a"),
            StepSpec(id="b", role="r", prompt="b", needs=("a",)),
        ),
    )
    spent = {"usd": 0.0}

    async def runner(step, deps):
        spent["usd"] += 0.6
        return "ok"

    run = await run_workflow(
        spec,
        runner,
        cost_limit_usd=0.5,
        cost_getter=lambda: spent["usd"],
    )
    assert run.statuses["a"] == "completed"
    assert run.statuses["b"] == "budget-exceeded"
    assert any(e["type"] == "run_budget_exceeded" for e in run.events)


@pytest.mark.asyncio
async def test_agent_adapter_reports_cost() -> None:
    from types import SimpleNamespace

    from spark.workflow import AgentWorkflowRunner, RoleSpec, StepSpec, WorkflowSpec

    class FakeLoop:
        async def spawn_subtask(self, prompt, **kwargs):
            yield SimpleNamespace(
                type="turn_end",
                text="done",
                data={"cost_usd": 0.0123, "input_tokens": 10},
            )

    spec = WorkflowSpec(
        name="cost",
        version=1,
        description="",
        roles={"r": RoleSpec(name="r")},
        steps=(StepSpec(id="a", role="r", prompt="a"),),
    )
    runner = AgentWorkflowRunner(FakeLoop(), spec)
    assert await runner(spec.steps[0], {}) == "done"
    assert runner.total_cost_usd == 0.0123
