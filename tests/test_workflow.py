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
