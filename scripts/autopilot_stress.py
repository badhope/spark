#!/usr/bin/env python3
"""Synthetic stress matrix for the Autopilot state machine.

The runner is deliberately decoupled from the real model so its failure modes
can be exercised deterministically.  Real-model acceptance is a separate gate
(see docs/AUTOPILOT.md); this script proves the harness terminates, persists and
never reports success when the independent test gate failed.

Usage:
    python scripts/autopilot_stress.py --json docs/reviews/2026-11-24-autopilot-stress.json
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from spark.autopilot import (
    AutopilotLimits,
    AutopilotRunner,
    AutopilotTurn,
    DiffProbe,
    Verification,
    new_state,
)


@dataclass
class Case:
    kind: str
    max_turns: int
    no_progress_limit: int
    max_cost_usd: float
    expected: str


def _turns(kind: str, calls: list[int]):
    def run(prompt: str) -> AutopilotTurn:
        calls.append(1)
        n = len(calls)
        if kind == "model_errors":
            return AutopilotTurn(ok=False, error="provider timeout")
        if kind == "budget":
            return AutopilotTurn(ok=True, summary="expensive", cost_usd=2.0)
        if kind == "no_change":
            return AutopilotTurn(ok=True, summary="nothing to do")
        if kind == "pass_first":
            return AutopilotTurn(ok=True, summary=f"turn {n}")
        if kind == "repair_once" and n == 1:
            return AutopilotTurn(ok=True, summary="first attempt")
        if kind == "repair_twice" and n <= 2:
            return AutopilotTurn(ok=True, summary=f"attempt {n}")
        if kind == "error_recover" and n == 1:
            return AutopilotTurn(ok=False, error="transient")
        if kind == "no_progress":
            return AutopilotTurn(ok=True, summary="same answer")
        return AutopilotTurn(ok=True, summary=f"attempt {n}")

    return run


def _verifier(kind: str, calls: list[int]):
    def verify() -> Verification:
        calls.append(1)
        n = len(calls)
        if kind in {"pass_first", "error_recover"}:
            return Verification(ok=True, command="pytest", output="ok")
        if kind == "repair_once":
            return Verification(
                ok=n > 1,
                command="pytest",
                output="ok" if n > 1 else "AssertionError: first attempt failed",
            )
        if kind == "repair_twice":
            return Verification(
                ok=n > 2,
                command="pytest",
                output="ok" if n > 2 else f"failure {n}",
            )
        if kind == "no_progress":
            return Verification(ok=False, command="pytest", output="same failure")
        if kind == "max_turns":
            return Verification(ok=False, command="pytest", output=f"failure {n}")
        return Verification(ok=False, command="pytest", output="failure")

    return verify


def _diff(kind: str, calls: list[int]):
    def probe() -> DiffProbe:
        calls.append(1)
        n = len(calls)
        if kind == "no_change" or kind == "no_progress":
            return DiffProbe(changed=False, digest="base", summary="")
        if kind == "max_turns":
            return DiffProbe(changed=True, digest=f"d{n}", summary=f"change {n}")
        return DiffProbe(
            changed=n > 1,
            digest="changed" if n > 1 else "base",
            summary="1 file changed" if n > 1 else "",
        )

    return probe


def _case_expected(kind: str, max_turns: int) -> str:
    if kind == "pass_first":
        return "passed" if max_turns >= 2 else "failed"
    if kind == "repair_once":
        return "passed" if max_turns >= 3 else "failed"
    if kind == "error_recover":
        return "passed" if max_turns >= 2 else "failed"
    if kind == "repair_twice":
        return "passed" if max_turns >= 4 else "failed"
    return "failed"


def make_cases() -> list[Case]:
    kinds = [
        "pass_first",
        "repair_once",
        "repair_twice",
        "no_progress",
        "max_turns",
        "model_errors",
        "budget",
        "timeout",
        "no_change",
        "error_recover",
    ]
    cases: list[Case] = []
    # 10 kinds × 12 variants = 120 deterministic runs.
    for kind in kinds:
        for i in range(12):
            max_turns = 2 + (i % 7)
            no_progress_limit = 1 + (i % 3)
            max_cost = 0.0 if kind != "budget" else 0.5
            cases.append(
                Case(
                    kind=kind,
                    max_turns=max_turns,
                    no_progress_limit=no_progress_limit,
                    max_cost_usd=max_cost,
                    expected=(
                        "failed" if kind == "timeout" else _case_expected(kind, max_turns)
                    ),
                )
            )
    return cases


def run_case(case: Case) -> dict:
    turns: list[int] = []
    verifies: list[int] = []
    diffs: list[int] = []
    with tempfile.TemporaryDirectory() as tmp:
        state = new_state(
            run_id=f"{case.kind}-{case.max_turns}-{case.no_progress_limit}",
            session_id="s",
            workdir=Path(tmp),
            requirement=f"stress: {case.kind}",
        )
        if case.kind == "timeout":
            state.started_at = 0.0
        result = AutopilotRunner(
            state,
            turn_runner=_turns(case.kind, turns),
            verifier=_verifier(case.kind, verifies),
            diff_probe=_diff(case.kind, diffs),
            limits=AutopilotLimits(
                max_turns=case.max_turns,
                task_timeout_sec=60 if case.kind == "timeout" else 3600,
                no_progress_limit=case.no_progress_limit,
                max_cost_usd=case.max_cost_usd,
                max_consecutive_errors=2,
            ),
        ).run()
    return {
        "kind": case.kind,
        "max_turns": case.max_turns,
        "no_progress_limit": case.no_progress_limit,
        "status": result.status,
        "turns": result.turn,
        "expected": case.expected,
        "history": len(result.history),
        "test_ok": result.last_test_ok,
        "cost_usd": round(result.total_cost_usd, 6),
        "error": result.last_error,
    }


def invariant_errors(row: dict, case: Case) -> list[str]:
    errors: list[str] = []
    if row["status"] != row["expected"]:
        errors.append(f"expected {row['expected']}, got {row['status']}")
    if row["turns"] > case.max_turns:
        errors.append(f"ran {row['turns']} turns > max {case.max_turns}")
    if row["status"] == "passed" and not row["test_ok"]:
        errors.append("passed without independent test success")
    if row["kind"] == "no_progress" and not row["error"]:
        errors.append("no-progress case ended without a reason")
    if row["kind"] == "budget" and row["status"] == "passed":
        errors.append("budget case passed")
    if row["kind"] == "no_change" and row["status"] == "passed":
        errors.append("no-change case passed")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()
    cases = make_cases()
    started = time.monotonic()
    rows: list[dict] = []
    failures: list[dict] = []
    for case in cases:
        row = run_case(case)
        errors = invariant_errors(row, case)
        row["invariant_errors"] = errors
        rows.append(row)
        if errors:
            failures.append(row)
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cases": len(rows),
        "passed_cases": len(rows) - len(failures),
        "failed_cases": len(failures),
        "duration_sec": round(time.monotonic() - started, 3),
        "kinds": sorted({row["kind"] for row in rows}),
        "failures": failures,
        "results": rows,
    }
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        f"autopilot stress: {summary['passed_cases']}/{summary['cases']} passed "
        f"in {summary['duration_sec']}s"
    )
    for row in failures:
        print(f"FAIL {row['kind']}: {row['invariant_errors']}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
