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
