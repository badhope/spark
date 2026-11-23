from pathlib import Path

from spark.config import SparkConfig
from spark.models import ToolResult
from spark.store import SessionStore
from spark.workbench import (
    build_delivery,
    build_privacy_receipt,
    delivery_markdown,
    workbench_snapshot,
)


def test_delivery_ledger_and_privacy_receipt(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "workbench.db")
    sid = store.create_session(tmp_path, "MiniCPM5-1B", title="fix parser")
    store.append_tool_event(sid, None, "read_file", {"path": "parser.py"}, None, None)
    store.append_tool_event(
        sid,
        None,
        "run_tests",
        {"command": "pytest -q"},
        ToolResult(ok=True, payload={"returncode": 0, "stdout": "3 passed"}),
        "allow",
    )
    cfg = SparkConfig()
    cfg.provider.name = "llama_cpp"
    cfg.provider.model = "MiniCPM5-1B"
    receipt = build_privacy_receipt(store, sid, cfg, workdir=str(tmp_path))
    assert receipt["mode"] == "local"
    assert "parser.py" in receipt["files_read"]
    assert receipt["telemetry"] == "local-only"

    delivery = build_delivery(
        store,
        sid,
        cfg,
        task_id="task-1",
        title="Fix parser",
        summary="done",
        workdir=str(tmp_path),
    )
    assert delivery["id"]
    assert delivery["tests"][0]["ok"] is True
    assert delivery["approvals"][0]["tool"] == "run_tests"
    assert delivery["privacy"]["mode"] == "local"
    assert "Fix parser" in delivery_markdown(delivery)
    assert "## 成本" in delivery_markdown(delivery)

    rows = store.list_deliveries(workdir=str(tmp_path))
    assert len(rows) == 1
    assert rows[0]["task_id"] == "task-1"
    # The old table dropped these on the way back from SQLite; the export then
    # called a local run "remote" and silently lost the diff/metrics.
    assert rows[0]["privacy"]["mode"] == "local"
    assert rows[0]["metrics"] == delivery["metrics"]
    assert "diff" in rows[0]["diff_summary"]

    snap = workbench_snapshot(store, None, session_id=sid, workdir=str(tmp_path), cfg=cfg)
    assert snap["session"]["id"] == sid
    assert snap["deliveries"][0]["id"] == delivery["id"]
    assert snap["review"][0]["id"] == delivery["id"]


def test_delivery_status_update_keeps_evidence(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "workbench.db")
    sid = store.create_session(tmp_path, "mock", title="task")
    cfg = SparkConfig()
    row = build_delivery(store, sid, cfg, title="One", workdir=str(tmp_path))
    row["status"] = "accepted"
    saved = store.save_delivery(row)
    assert saved["status"] == "accepted"
    assert saved["tests"] == []
    assert saved["id"] == row["id"]


def test_delivery_uses_turn_window_and_real_write_tools(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "workbench.db")
    sid = store.create_session(tmp_path, "mock", title="turn window")
    store.append_tool_event(sid, None, "read_file", {"path": "old.py"}, None, None)
    since = store.latest_tool_event_id(sid)
    store.append_tool_event(
        sid,
        None,
        "apply_patch",
        {"path": "new.py"},
        ToolResult(ok=True, payload={"path": "new.py"}),
        "allow",
    )
    store.append_tool_event(
        sid,
        None,
        "run_shell",
        {"command": "ls"},
        ToolResult(ok=True, payload={"returncode": 0, "stdout": "a b"}),
        "allow",
    )
    store.append_tool_event(
        sid,
        None,
        "run_tests",
        {"command": "pytest -q"},
        ToolResult(ok=True, payload={"returncode": 0, "stdout": "1 passed"}),
        "allow",
    )
    delivery = build_delivery(
        store, sid, SparkConfig(), title="one turn", workdir=str(tmp_path), since_id=since
    )
    assert [f["path"] for f in delivery["files"]] == ["new.py"]
    assert delivery["diff_summary"]["attribution"] == "tool_events"
    assert [t["name"] for t in delivery["tests"]] == ["run_tests"]
    assert delivery["privacy"]["files_read"] == []
    assert store.get_delivery(delivery["id"])["tests"][0]["name"] == "run_tests"


def test_tool_event_window_keeps_newest_rows_in_oldest_order(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "events.db")
    sid = store.create_session(tmp_path, "mock", title="events")
    for i in range(510):
        store.append_tool_event(sid, None, "read_file", {"path": f"f{i}.py"}, None, None)
    events = store.list_tool_events(sid, limit=500)
    assert len(events) == 500
    assert events[0]["arguments_json"].endswith('f10.py"}')
    assert events[-1]["arguments_json"].endswith('f509.py"}')


def test_chat_auto_ledger_only_when_workspace_mutated(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from spark.web._handlers_chat import _maybe_auto_ledger

    store = SessionStore(tmp_path / "chat.db")
    sid = store.create_session(tmp_path, "mock", title="chat")
    state = SimpleNamespace(store=store, cfg=SparkConfig(), workdir=tmp_path)
    _maybe_auto_ledger(state, sid, "explain this", "read only", False)
    assert store.list_deliveries(workdir=str(tmp_path)) == []
    _maybe_auto_ledger(state, sid, "fix parser", "changed parser", True)
    rows = store.list_deliveries(workdir=str(tmp_path))
    assert len(rows) == 1
    assert rows[0]["session_id"] == sid
    assert rows[0]["status"] == "review"
