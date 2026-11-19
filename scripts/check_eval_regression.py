#!/usr/bin/env python3
"""Fail CI when the eval suite regresses beyond the allowed window.

Policy (W8): drop at most 2 absolute passing scenarios vs the committed baseline,
and never let a scenario that used to pass turn into a failure. Aggregate-only
checks hide exactly that: one fixed + one broken keeps 12/12 while shipping a
regression.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def load(path: Path) -> tuple[int, int, set[str], set[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    results = data.get("results") or []
    passed = {str(row.get("id")) for row in results if row.get("passed")}
    failed = {str(row.get("id")) for row in results if not row.get("passed")}
    return int(data.get("total") or len(results)), int(data.get("passed") or len(passed)), passed, failed


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: check_eval_regression.py <current.json> <baseline.json> [max_drop]", file=sys.stderr)
        return 2
    current = Path(argv[1])
    baseline = Path(argv[2])
    max_drop = int(argv[3]) if len(argv) > 3 else 2
    try:
        c_total, c_pass, c_ok, _ = load(current)
        b_total, b_pass, b_ok, _ = load(baseline)
    except (OSError, ValueError) as exc:
        print(f"cannot read eval report: {exc}", file=sys.stderr)
        return 2

    regressions = sorted(b_ok - c_ok)
    delta = c_pass - b_pass
    print(f"eval: baseline {b_pass}/{b_total} -> current {c_pass}/{c_total} (delta {delta:+d}, allowed drop {max_drop})")
    if regressions:
        print("scenario regressions:", ", ".join(regressions), file=sys.stderr)
    if delta < -max_drop:
        print(f"FAIL: pass count dropped by {-delta}, limit {max_drop}", file=sys.stderr)
        return 1
    if regressions:
        print("FAIL: previously passing scenarios now fail", file=sys.stderr)
        return 1
    print("OK: no eval regression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
