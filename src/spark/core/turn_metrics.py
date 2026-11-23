"""Turn-level latency metrics.

The numbers here are deliberately local and cheap: monotonic timestamps, no
network, no vendor telemetry.  A turn is split into the stages a user can
actually feel:

* ``context_ms`` — context assembly / compaction / retrieval before the model;
* ``llm_ms`` — time spent inside provider streams across all tool rounds;
* ``ttft_ms`` — first streamed delta after the turn began (reasoning or text);
* ``first_text_ms`` — first user-visible text delta;
* ``tool_wait_ms`` — tool execution time (reported by the tool runner when
  available, otherwise left null);
* ``total_ms`` — whole turn.

``render_ms`` is intentionally absent: the backend cannot measure the browser
paint.  The Web client may attach it later; do not invent it server-side.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnMetrics:
    started_at: float = field(default_factory=time.monotonic)
    queue_ms: float | None = None
    context_ready_at: float | None = None
    provider_runs: list[dict[str, Any]] = field(default_factory=list)
    first_delta_at: float | None = None
    first_text_at: float | None = None
    first_tool_at: float | None = None
    rounds: int = 0
    tool_calls: int = 0
    _provider_started_at: float | None = None
    _provider_first_delta_at: float | None = None

    def mark_context_ready(self) -> None:
        if self.context_ready_at is None:
            self.context_ready_at = time.monotonic()

    def begin_provider_round(self) -> None:
        self.rounds += 1
        self._provider_started_at = time.monotonic()
        self._provider_first_delta_at = None

    def mark_delta(self) -> None:
        now = time.monotonic()
        if self.first_delta_at is None:
            self.first_delta_at = now
        if self._provider_first_delta_at is None:
            self._provider_first_delta_at = now

    def mark_text(self) -> None:
        self.mark_delta()
        if self.first_text_at is None:
            self.first_text_at = time.monotonic()

    def mark_tool_call(self, count: int = 1) -> None:
        now = time.monotonic()
        if count > 0 and self.first_tool_at is None:
            self.first_tool_at = now
        self.tool_calls += max(0, int(count))

    def end_provider_round(self, *, error: str | None = None) -> None:
        if self._provider_started_at is None:
            return
        started = self._provider_started_at
        ended = time.monotonic()
        self.provider_runs.append(
            {
                "start_ms": _ms(started, self.started_at),
                "end_ms": _ms(ended, self.started_at),
                "duration_ms": round((ended - started) * 1000, 1),
                "first_delta_ms": (
                    round((self._provider_first_delta_at - started) * 1000, 1)
                    if self._provider_first_delta_at is not None
                    else None
                ),
                "error": error,
            }
        )
        self._provider_started_at = None
        self._provider_first_delta_at = None

    def snapshot(self, usage: dict[str, Any] | None = None) -> dict[str, Any]:
        now = time.monotonic()
        context_ms = (
            round((self.context_ready_at - self.started_at) * 1000, 1)
            if self.context_ready_at is not None
            else None
        )
        llm_ms = round(sum(float(r.get("duration_ms") or 0.0) for r in self.provider_runs), 1)
        return {
            "version": 1,
            "queue_ms": self.queue_ms,
            "context_ms": context_ms,
            "llm_ms": llm_ms,
            "ttft_ms": _since(self.first_delta_at, self.started_at),
            "first_text_ms": _since(self.first_text_at, self.started_at),
            "first_tool_ms": _since(self.first_tool_at, self.started_at),
            "total_ms": round((now - self.started_at) * 1000, 1),
            "rounds": self.rounds,
            "tool_calls": self.tool_calls,
            "provider_runs": list(self.provider_runs),
            "render_ms": None,
            "usage": dict(usage or {}),
        }


def _since(at: float | None, started: float) -> float | None:
    return round((at - started) * 1000, 1) if at is not None else None


def _ms(at: float, started: float) -> float:
    return round((at - started) * 1000, 1)
