from __future__ import annotations

from pathlib import Path

import pytest

from spark.store import SessionStore
from spark.task_queue import QueueOutcome, TaskQueue


def _queue(tmp_path: Path, runner=None, **kw) -> tuple[SessionStore, TaskQueue]:
    store = SessionStore(tmp_path / "queue.db")
    q = TaskQueue(store, runner or (lambda task: QueueOutcome(summary="done")), **kw)
    return store, q


def test_queue_pause_resume_and_priority(tmp_path: Path) -> None:
    seen: list[str] = []
    store, q = _queue(tmp_path, lambda task: (seen.append(task["title"]) or QueueOutcome(summary=task["title"])))
    q.enqueue("low", title="low")
    q.enqueue("high", title="high", priority=10)
    q.pause()
    assert q.paused is True
    assert q.run_once() is None
    q.resume()
    assert q.run_once()["title"] == "high"
    assert q.run_once()["title"] == "low"
    assert q.run_once() is None
    assert seen == ["high", "low"]
    assert store.queue_counts() == {"ok": 2}


def test_queue_failure_requeues_then_fails(tmp_path: Path) -> None:
    def boom(task):
        raise RuntimeError("provider down")

    store, q = _queue(tmp_path, boom, max_attempts=2)
    q.enqueue("do it")
    first = q.run_once()
    assert first["status"] == "requeued"
    assert store.list_agent_tasks()[0]["status"] == "queued"
    second = q.run_once()
    assert second["status"] == "failed"
    row = store.list_agent_tasks()[0]
    assert row["status"] == "failed"
    assert row["attempts"] == 2
    assert "provider down" in row["error"]


def test_queue_cancel_only_queued_and_recover_running(tmp_path: Path) -> None:
    store, q = _queue(tmp_path)
    task = q.enqueue("cancel me")
    assert q.cancel(task["id"]) is True
    assert store.list_agent_tasks()[0]["status"] == "cancelled"
    assert q.cancel(task["id"]) is False

    running = q.enqueue("running")
    claimed = store.claim_next_agent_task()
    assert claimed is not None and claimed["id"] == running["id"]
    assert store.list_agent_tasks()[0]["status"] == "running"
    assert store.recover_agent_queue() == 1
    assert store.list_agent_tasks()[0]["status"] == "queued"


def test_queue_autopilot_row_roundtrips_kind_and_options(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "queue.db")
    try:
        task = store.enqueue_agent_task(
            "add calc.double",
            title="autopilot",
            kind="autopilot",
            options={"max_turns": 3, "max_cost_usd": 0.5},
        )
        row = store.list_agent_tasks()[0]
        assert task["kind"] == "autopilot"
        assert row["kind"] == "autopilot"
        assert row["options"] == {"max_turns": 3, "max_cost_usd": 0.5}
        with pytest.raises(ValueError):
            store.enqueue_agent_task("x", kind="shell")
    finally:
        store.close()


def test_queue_rejects_empty_prompt(tmp_path: Path) -> None:
    _, q = _queue(tmp_path)
    with pytest.raises(ValueError):
        q.enqueue("   ")


def test_blocked_outcome_is_permanent(tmp_path: Path) -> None:
    store, q = _queue(
        tmp_path,
        lambda task: QueueOutcome(status="blocked", error="workdir mismatch"),
    )
    q.enqueue("never run")
    result = q.run_once()
    assert result["status"] == "failed"
    row = store.list_agent_tasks()[0]
    assert row["status"] == "failed"
    assert row["attempts"] == 1
    assert "workdir mismatch" in row["error"]


def test_pause_survives_store_reopen(tmp_path: Path) -> None:
    db = tmp_path / "queue.db"
    store = SessionStore(db)
    store.set_queue_paused(True)
    store.close()
    reopened = SessionStore(db)
    try:
        assert reopened.queue_paused() is True
        reopened.set_queue_paused(False)
        assert reopened.queue_paused() is False
    finally:
        reopened.close()


def test_queue_daily_limit_stops_claiming_after_quota(tmp_path: Path) -> None:
    seen: list[str] = []
    store, q = _queue(
        tmp_path,
        lambda task: (seen.append(task["title"]) or QueueOutcome(summary=task["title"])),
        daily_limit=1,
    )
    q.enqueue("first", title="first")
    q.enqueue("second", title="second")

    first = q.run_once()
    assert first is not None and first["status"] == "ok"
    assert q.run_once() is None
    assert seen == ["first"]
    assert q.status()["completed_today"] == 1
    assert store.list_agent_tasks()[0]["status"] == "queued"


def test_queue_passes_task_timeout_to_runner(tmp_path: Path) -> None:
    seen: list[int] = []
    store, q = _queue(
        tmp_path,
        lambda task: (seen.append(int(task.get("_timeout_sec") or 0)) or QueueOutcome(summary="ok")),
        task_timeout_sec=12,
    )
    q.enqueue("timed")
    assert q.run_once() is not None
    assert seen == [12]


def test_queue_daily_cost_cap_stops_claiming(tmp_path: Path) -> None:
    seen: list[str] = []
    store, q = _queue(
        tmp_path,
        lambda task: (
            seen.append(task["title"])
            or QueueOutcome(summary=task["title"], cost_usd=0.25)
        ),
        daily_cost_usd=0.2,
    )
    q.enqueue("first", title="first")
    q.enqueue("second", title="second")

    assert q.run_once()["status"] == "ok"
    assert q.run_once() is None
    assert seen == ["first"]
    assert q.status()["daily_cost_usd"] == 0.2
    assert q.status()["completed_cost_today"] == pytest.approx(0.25)
    assert store.list_agent_tasks()[0]["status"] == "queued"


def test_queue_cost_accumulates_on_requeue(tmp_path: Path) -> None:
    calls = {"n": 0}

    def runner(task):
        calls["n"] += 1
        if calls["n"] == 1:
            return QueueOutcome(status="requeue", error="not done", cost_usd=0.2)
        return QueueOutcome(summary="ok", cost_usd=0.3)

    store, q = _queue(tmp_path, runner, max_attempts=2, daily_cost_usd=0.6)
    q.enqueue("costly")
    assert q.run_once()["status"] == "requeued"
    assert q.run_once()["status"] == "ok"
    row = store.list_agent_tasks()[0]
    assert row["status"] == "ok"
    assert row["cost_usd"] == pytest.approx(0.5)


def test_queue_cost_cap_ignores_zero_unpriced(tmp_path: Path) -> None:
    store, q = _queue(tmp_path, lambda task: QueueOutcome(summary="free"), daily_cost_usd=0.1)
    q.enqueue("free", title="free")
    assert q.run_once()["status"] == "ok"
    # An unpriced/local model reports 0, so the cap must not block forever.
    assert store.list_agent_tasks()[0]["cost_usd"] == 0


def test_queue_workers_are_bounded_and_start(tmp_path: Path) -> None:
    store, q = _queue(tmp_path, workers=9)
    assert q.status()["workers"] == 4
    q.start()
    try:
        assert len(q._threads) == 4
        assert all(t.is_alive() for t in q._threads)
    finally:
        q.stop()
    assert q._threads == []
