"""Queue -> Autopilot dispatch test.

The queue row must run through the real Autopilot state machine and the real
AgentLoop, not a second mock path.  The provider is scripted only at the model
boundary; tools, test verification and the ledger are real.
"""
from __future__ import annotations

from pathlib import Path

from spark.config import SparkConfig
from spark.models import ChatDelta, ToolCall
from spark.proc import run_bounded
from spark.providers.mock import MockProvider
from spark.store import SessionStore
from spark.task_queue import QueueOutcome
from spark.web.state import SparkWebState


def _git(work: Path, *args: str) -> None:
    run_bounded(["git", *args], cwd=work, timeout=30)


def _init_repo(work: Path) -> None:
    work.mkdir(parents=True, exist_ok=True)
    _git(work, "init")
    _git(work, "config", "user.email", "queue@example.test")
    _git(work, "config", "user.name", "Queue Test")
    (work / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\n", encoding="utf-8"
    )
    (work / "tests").mkdir()
    (work / "tests" / "test_calc.py").write_text(
        "import calc\n\n\ndef test_double():\n    assert calc.double(3) == 6\n",
        encoding="utf-8",
    )
    _git(work, "add", ".")
    _git(work, "commit", "-m", "baseline")


def test_queue_runs_real_autopilot_and_records_delivery(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    cfg.agent.sandbox_mode = "workspace"
    cfg.agent.network_access = False
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="write-1",
                        name="write_file",
                        arguments={
                            "path": "calc.py",
                            "content": "def double(x):\n    return x * 2\n",
                        },
                    ),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="Implemented double."), ChatDelta(type="end")],
            [ChatDelta(type="text", text="Audit complete."), ChatDelta(type="end")],
        ]
    )
    monkeypatch.setattr("spark.web.state.create_provider", lambda _cfg: provider)

    store = SessionStore(tmp_path / "queue.db")
    state = SparkWebState(repo, cfg, store)
    try:
        outcome = state._run_queued_autopilot(
            {
                "id": "task-1",
                "title": "add double",
                "prompt": "add calc.double",
                "workdir": str(repo),
                "kind": "autopilot",
                "options": {
                    "max_turns": 3,
                    "turn_timeout_sec": 120,
                    "task_timeout_sec": 300,
                    "test_timeout_sec": 120,
                },
                "attempts": 1,
            }
        )
        assert outcome.status == "ok", outcome.error
        assert (repo / "calc.py").read_text(encoding="utf-8") == (
            "def double(x):\n    return x * 2\n"
        )
        ledger = (repo / ".spark" / "autopilot" / "queue-task-1.json").read_text(
            encoding="utf-8"
        )
        assert '"status": "passed"' in ledger
        deliveries = store.list_deliveries()
        assert deliveries and deliveries[0]["status"] == "review"
    finally:
        state.close()
        store.close()


def test_terminal_failure_retry_archives_old_ledger(tmp_path: Path, monkeypatch) -> None:
    import json

    from spark.autopilot import AutopilotState

    repo = tmp_path / "repo-retry"
    _init_repo(repo)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    cfg.agent.sandbox_mode = "workspace"
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="write-1",
                        name="write_file",
                        arguments={
                            "path": "calc.py",
                            "content": "def double(x):\n    return x * 2\n",
                        },
                    ),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="Implemented double."), ChatDelta(type="end")],
            [ChatDelta(type="text", text="Audit complete."), ChatDelta(type="end")],
        ]
    )
    monkeypatch.setattr("spark.web.state.create_provider", lambda _cfg: provider)
    store = SessionStore(tmp_path / "queue-retry.db")
    state = SparkWebState(repo, cfg, store)
    try:
        state_path = repo / ".spark" / "autopilot" / "queue-task-2.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        old = AutopilotState(
            run_id="task-2",
            session_id="missing",
            workdir=str(repo),
            requirement="add calc.double",
            status="failed",
            last_error="previous attempt failed",
            total_cost_usd=0.01,
        )
        state_path.write_text(json.dumps(old.to_dict()), encoding="utf-8")
        outcome = state._run_queued_autopilot(
            {
                "id": "task-2",
                "title": "retry",
                "prompt": "add calc.double",
                "workdir": str(repo),
                "kind": "autopilot",
                "options": {"max_turns": 3, "task_timeout_sec": 300},
                "attempts": 2,
            }
        )
        assert outcome.status == "ok", outcome.error
        assert state_path.with_name("queue-task-2.attempt1.json").exists()
        assert '"status": "passed"' in state_path.read_text(encoding="utf-8")
    finally:
        state.close()
        store.close()


def test_queue_autopilot_rejects_ledger_escape_and_sanitizes_default(
    tmp_path: Path,
) -> None:
    import json

    from spark.autopilot import AutopilotState

    repo = tmp_path / "repo-path"
    repo.mkdir()
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    store = SessionStore(tmp_path / "queue-path.db")
    state = SparkWebState(repo, cfg, store)
    try:
        for bad in ("../../escape.json", "/tmp/escape.json", "nested/escape.json"):
            outcome = state._queued_autopilot_spec(
                {
                    "id": "task-path",
                    "prompt": "do it",
                    "workdir": str(repo),
                    "options": {"state_path": bad},
                }
            )
            assert outcome.status == "blocked", bad
            assert "state_path" in outcome.error
        assert not (tmp_path / "escape.json").exists()

        spec = state._queued_autopilot_spec(
            {
                "id": "../../evil",
                "prompt": "do it",
                "workdir": str(repo),
                "options": {},
            }
        )
        assert not isinstance(spec, QueueOutcome)
        assert spec.state_path == (
            repo / ".spark" / "autopilot" / "queue-evil.json"
        ).resolve()

        spec = state._queued_autopilot_spec(
            {
                "id": "task-name",
                "prompt": "do it",
                "workdir": str(repo),
                "options": {"state_path": "ledger.json"},
            }
        )
        assert spec.state_path == (repo / ".spark" / "autopilot" / "ledger.json").resolve()

        ledger = state._queued_autopilot_spec(
            {
                "id": "task-first-fail",
                "prompt": "do it",
                "workdir": str(repo),
                "options": {},
            }
        ).state_path
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(
            json.dumps(
                AutopilotState(
                    run_id="task-first-fail",
                    session_id="missing",
                    workdir=str(repo),
                    requirement="do it",
                    status="failed",
                    last_error="first attempt failed",
                ).to_dict()
            ),
            encoding="utf-8",
        )
        loaded, failure = state._load_queued_autopilot_state(
            state._queued_autopilot_spec(
                {
                    "id": "task-first-fail",
                    "prompt": "do it",
                    "workdir": str(repo),
                    "options": {},
                }
            ),
            {"attempts": 1},
        )
        assert loaded is None
        assert failure is not None and failure.status == "failed"
        assert ledger.exists()
        assert not ledger.with_name("queue-task-first-fail.attempt0.json").exists()

        link_repo = tmp_path / "repo-link"
        outside = tmp_path / "outside-ledger"
        link_repo.mkdir()
        outside.mkdir()
        (link_repo / ".spark").mkdir()
        (link_repo / ".spark" / "autopilot").symlink_to(outside, target_is_directory=True)
        escaped = state._queued_autopilot_spec(
            {
                "id": "task-link",
                "prompt": "do it",
                "workdir": str(link_repo),
                "options": {"state_path": "ledger.json"},
            }
        )
        assert escaped.status == "blocked"
        assert not (outside / "ledger.json").exists()

        bad_cost = state._queued_autopilot_spec(
            {
                "id": "task-cost",
                "prompt": "do it",
                "workdir": str(repo),
                "options": {"max_cost_usd": float("nan")},
            }
        )
        assert bad_cost.status == "blocked"
        assert "finite" in bad_cost.error

        clamped = state._queued_autopilot_spec(
            {
                "id": "task-timeout",
                "prompt": "do it",
                "workdir": str(repo),
                "options": {"test_timeout_sec": 999999},
            }
        )
        assert not isinstance(clamped, QueueOutcome)
        assert clamped.test_timeout_sec == 7200
    finally:
        state.close()
        store.close()
