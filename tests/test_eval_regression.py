from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_eval_regression.py"


def _report(path: Path, ids: list[str], failing: set[str] | None = None) -> Path:
    failing = failing or set()
    results = [{"id": i, "passed": i not in failing} for i in ids]
    path.write_text(
        json.dumps({"total": len(ids), "passed": len(ids) - len(failing), "results": results}),
        encoding="utf-8",
    )
    return path


def _run(current: Path, baseline: Path, max_drop: int = 2) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(current), str(baseline), str(max_drop)],
        text=True,
        capture_output=True,
    )


def test_eval_gate_accepts_same_passes(tmp_path: Path) -> None:
    ids = ["a", "b", "c"]
    base = _report(tmp_path / "base.json", ids)
    cur = _report(tmp_path / "cur.json", ids)
    result = _run(cur, base)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_eval_gate_rejects_scenario_regression(tmp_path: Path) -> None:
    base = _report(tmp_path / "base.json", ["a", "b", "c"])
    cur = _report(tmp_path / "cur.json", ["a", "b", "c"], failing={"c"})
    result = _run(cur, base)
    assert result.returncode == 1
    assert "c" in result.stderr


def test_eval_gate_rejects_drop_beyond_limit(tmp_path: Path) -> None:
    base = _report(tmp_path / "base.json", ["a", "b", "c", "d", "e"])
    cur = _report(tmp_path / "cur.json", ["a", "b"])
    result = _run(cur, base, max_drop=2)
    assert result.returncode == 1
    assert "dropped" in result.stderr


def test_eval_gate_allows_small_aggregate_drop_without_old_regression(tmp_path: Path) -> None:
    base = _report(tmp_path / "base.json", ["a", "b", "c"])
    cur = _report(tmp_path / "cur.json", ["a", "b", "d"])
    result = _run(cur, base)
    # c disappeared (a regression by id), even though d replaced it.
    assert result.returncode == 1
