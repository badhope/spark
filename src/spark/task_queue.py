"""Idle agent-task queue.

The queue is deliberately boring: one SQLite table, one worker thread, one task
at a time.  It is not a distributed job system.  A task runs only when no agent
turn is busy; pause is persistent across restarts; a process crash requeues the
``running`` row on the next start instead of losing it.

Queue entries are **agent prompts**, not shell commands.  Shell jobs already
have their own ``bg_start`` tool and must stay separate: mixing them would let a
queued prompt inherit shell sandbox semantics it never asked for.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class QueueOutcome:
    """What the runner reports back to the queue."""

    status: str = "ok"  # ok / failed / requeue
    summary: str = ""
    error: str = ""
    session_id: str = ""
    cost_usd: float = 0.0


Runner = Callable[[dict[str, Any]], QueueOutcome]


class TaskQueue:
    def __init__(
        self,
        store,
        runner: Runner,
        *,
        is_busy: Callable[[], bool] | None = None,
        poll_sec: float = 10.0,
        max_attempts: int = 3,
        daily_limit: int = 0,
        daily_cost_usd: float = 0.0,
        task_timeout_sec: int = 0,
        workers: int = 1,
    ) -> None:
        self._store = store
        self._runner = runner
        self._is_busy = is_busy or (lambda: False)
        self._poll = max(1.0, float(poll_sec))
        self._max_attempts = max(1, int(max_attempts))
        self._daily_limit = max(0, int(daily_limit))
        self._daily_cost_usd = max(0.0, float(daily_cost_usd or 0.0))
        self._task_timeout_sec = max(0, int(task_timeout_sec))
        self._workers = max(1, min(int(workers), 4))
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []

    # ---- lifecycle -----------------------------------------------------
    def start(self) -> None:
        if any(thread.is_alive() for thread in self._threads):
            return
        self._store.recover_agent_queue()
        self._stop.clear()
        self._wake.clear()
        self._threads = [
            threading.Thread(
                target=self._loop,
                name=f"spark-task-queue-{index + 1}",
                daemon=True,
            )
            for index in range(self._workers)
        ]
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for thread in self._threads:
            thread.join(timeout=2)
        self._threads = []

    # ---- control -------------------------------------------------------
    @property
    def paused(self) -> bool:
        return self._store.queue_paused()

    def pause(self) -> None:
        self._store.set_queue_paused(True)
        self._wake.set()

    def resume(self) -> None:
        self._store.set_queue_paused(False)
        self._wake.set()

    def set_daily_limit(self, limit: int) -> None:
        """Update the local-day completion quota without restarting the worker."""
        self._daily_limit = max(0, int(limit))
        self._wake.set()

    def set_daily_cost_limit(self, limit_usd: float) -> None:
        """Update the local-day USD cap without restarting the worker."""
        self._daily_cost_usd = max(0.0, float(limit_usd or 0.0))
        self._wake.set()

    def set_task_timeout(self, seconds: int) -> None:
        """Update the per-task wall-clock timeout for future tasks."""
        self._task_timeout_sec = max(0, int(seconds))

    def enqueue(self, prompt: str, *, title: str = "", workdir: str = "", priority: int = 0) -> dict:
        task = self._store.enqueue_agent_task(
            prompt, title=title, workdir=workdir, priority=priority
        )
        self._wake.set()
        return task

    def cancel(self, task_id: str) -> bool:
        return self._store.cancel_agent_task(task_id)

    def list_tasks(self, limit: int = 50) -> list[dict]:
        return self._store.list_agent_tasks(limit)

    def status(self) -> dict:
        return {
            "paused": self.paused,
            "daily_limit": self._daily_limit,
            "completed_today": self._completed_today(),
            "daily_cost_usd": self._daily_cost_usd,
            "completed_cost_today": self._completed_cost_today(),
            "task_timeout_sec": self._task_timeout_sec,
            "workers": self._workers,
            "counts": self._store.queue_counts(),
            "tasks": self.list_tasks(),
        }

    def _completed_today(self) -> int:
        now = time.time()
        local = time.localtime(now)
        start = time.mktime(
            (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
        )
        return int(self._store.queue_completed_since(start))

    def _completed_cost_today(self) -> float:
        now = time.time()
        local = time.localtime(now)
        start = time.mktime(
            (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
        )
        return float(self._store.queue_cost_since(start))

    # ---- worker --------------------------------------------------------
    def run_once(self) -> dict | None:
        """Claim and run one task. Returns the task row, or None if nothing ran.

        Public so tests and a future "run now" endpoint do not need to reach
        into the worker thread.  It refuses to run while paused or after the
        local-day completion quota has been reached.
        """
        if self.paused:
            return None
        if self._daily_limit and self._completed_today() >= self._daily_limit:
            return None
        if self._daily_cost_usd and self._completed_cost_today() >= self._daily_cost_usd:
            return None
        task = self._store.claim_next_agent_task()
        if task is None:
            return None
        task["_timeout_sec"] = self._task_timeout_sec
        task_id = str(task.get("id") or "")
        try:
            outcome = self._runner(task)
        except Exception as exc:  # aqg: top-level boundary — never kill the worker
            logger.warning("queued task %s failed", task_id, exc_info=True)
            outcome = QueueOutcome(status="failed", error=str(exc))
        if not isinstance(outcome, QueueOutcome):
            outcome = QueueOutcome(status="ok", summary=str(outcome or ""))
        attempts = int(task.get("attempts") or 0)
        if outcome.status == "blocked":
            # A permanent configuration/runtime mismatch: do not retry it three
            # times, and do not leave it running forever.
            self._store.finish_agent_task(
                task_id,
                status="failed",
                error=outcome.error,
                session_id=outcome.session_id,
                cost_usd=outcome.cost_usd,
            )
            result = "failed"
        elif outcome.status in {"requeue", "failed"} and attempts < self._max_attempts:
            self._store.requeue_agent_task(
                task_id, outcome.error, cost_usd=outcome.cost_usd
            )
            result = "requeued"
        elif outcome.status in {"ok", "failed", "requeue"}:
            final = "failed" if outcome.status in {"failed", "requeue"} else "ok"
            self._store.finish_agent_task(
                task_id,
                status=final,
                result=outcome.summary,
                error=outcome.error,
                session_id=outcome.session_id,
                cost_usd=outcome.cost_usd,
            )
            result = final
        else:
            # Unknown status is a runner bug; fail closed, don't leave it running.
            self._store.finish_agent_task(
                task_id,
                status="failed",
                error=f"unknown queue status {outcome.status!r}",
                cost_usd=outcome.cost_usd,
            )
            result = "failed"
        task["status"] = result
        return task

    def _loop(self) -> None:
        while not self._stop.is_set():
            # Wake immediately after enqueue/pause/resume; otherwise poll on a
            # bounded interval so an idle queue costs almost nothing.
            self._wake.wait(self._poll)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                if self.paused:
                    continue
                if self._is_busy():
                    continue
                self.run_once()
            except Exception:  # pragma: no cover - worker must not die
                logger.warning("task queue tick failed", exc_info=True)
