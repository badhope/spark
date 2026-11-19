from __future__ import annotations

import json
from pathlib import Path

from spark.web.routes import _workflow_state


def _write(workdir: Path, name: str, payload: dict) -> Path:
    base = workdir / ".spark" / "workflow-runs"
    base.mkdir(parents=True, exist_ok=True)
    path = base / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_workflow_state_reads_graph(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "latest.json",
        {
            "workflow": "demo",
            "statuses": {"a": "completed"},
            "graph": {
                "name": "demo",
                "nodes": [{"id": "a", "role": "lead", "status": "completed"}],
                "edges": [],
            },
        },
    )
    payload = _workflow_state(str(tmp_path), "latest.json")
    assert payload["name"] == "demo"
    assert payload["graph"]["nodes"][0]["id"] == "a"


def test_workflow_state_rejects_escape_and_bad_name(tmp_path: Path) -> None:
    assert "error" in _workflow_state(str(tmp_path), "../secret.json")
    assert "error" in _workflow_state(str(tmp_path), "missing.json")
    assert "error" in _workflow_state(str(tmp_path), "not-json.txt")
