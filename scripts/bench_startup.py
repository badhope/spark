#!/usr/bin/env python3
"""PF-01: cold-start + import baseline for Spark.

Usage:
    python scripts/bench_startup.py
    python scripts/bench_startup.py --runs 20 --json-out perf/baseline.json

The script is dependency-free: it shells out to the installed ``spark`` command
and parses ``python -X importtime``. It never imports spark in this process,
so the measured numbers are not polluted by an already-warm interpreter.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_IMPORT_LINE = re.compile(
    r"^import time:\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(.+)$"
)


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    idx = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * pct))))
    return ordered[idx]


def _run_version(spark_cmd: str, runs: int) -> list[float]:
    times: list[float] = []
    for _ in range(runs):
        started = time.perf_counter()
        proc = subprocess.run(
            [spark_cmd, "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"{spark_cmd} --version exited {proc.returncode}")
        times.append((time.perf_counter() - started) * 1000.0)
    return times


def _import_audit(python_cmd: str, module: str) -> list[dict]:
    proc = subprocess.run(
        [python_cmd, "-X", "importtime", "-c", f"import {module}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    rows: list[dict] = []
    for line in proc.stderr.splitlines():
        match = _IMPORT_LINE.match(line.strip())
        if not match:
            continue
        self_us, cumulative_us, name = match.groups()
        rows.append(
            {
                "module": name.strip(),
                "self_ms": round(int(self_us) / 1000.0, 2),
                "cumulative_ms": round(int(cumulative_us) / 1000.0, 2),
            }
        )
    rows.sort(key=lambda item: item["cumulative_ms"], reverse=True)
    return rows[:30]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--spark", default="spark", help="spark executable")
    parser.add_argument("--python", default=sys.executable, help="python for import audit")
    parser.add_argument("--module", default="spark.cli", help="module to audit")
    parser.add_argument("--json-out", default="", help="write JSON baseline here")
    args = parser.parse_args()

    times = _run_version(args.spark, max(1, args.runs))
    imports = _import_audit(args.python, args.module)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "runs": len(times),
        "cli_version_ms": {
            "p50": round(statistics.median(times), 1),
            "p95": round(_percentile(times, 0.95), 1),
            "min": round(min(times), 1),
            "max": round(max(times), 1),
        },
        "import_audit": {
            "module": args.module,
            "top": imports,
        },
    }

    print(json.dumps(payload["cli_version_ms"], indent=2))
    print("\nTop imports (cumulative):")
    for row in imports[:15]:
        print(f"  {row['cumulative_ms']:>8.1f} ms  {row['module']}")

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
