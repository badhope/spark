from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_perf_budget.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_perf_budget", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_percentile_uses_nearest_rank() -> None:
    mod = _load()
    assert mod.percentile([], 0.95) == 0.0
    assert mod.percentile([1, 2, 3, 4], 0.50) == 3
    assert mod.percentile([1, 2, 3, 4], 0.95) == 4


def test_bundle_budget_reports_entry_and_fails_when_over(tmp_path: Path, monkeypatch) -> None:
    mod = _load()
    assets = tmp_path / "src" / "spark" / "web" / "dist" / "assets"
    assets.mkdir(parents=True)
    # Varied bytes keep gzip from shrinking the sample under the 1KB budget.
    (assets / "index-abc.js").write_bytes(bytes(range(256)) * 800)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    report, failures = mod.check_bundle(
        {"first_screen_js_gzip_kb": 1, "first_screen_js_target_kb": 1}
    )
    assert report["entry"] == "index-abc.js"
    assert failures
    assert "first-screen gzip" in failures[0]
