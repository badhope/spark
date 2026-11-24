from __future__ import annotations

from pathlib import Path

from spark.autopilot import (
    AutopilotLimits,
    AutopilotRunner,
    make_agent_turn_runner,
    make_git_diff_probe,
    make_test_verifier,
    new_state,
)
from spark.config import SparkConfig
from spark.core.loop import AgentLoop
from spark.models import ChatDelta, ToolCall
from spark.proc import run_bounded
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry


def _git(work: Path, *args: str) -> None:
    run_bounded(["git", *args], cwd=work, timeout=30)


def _init_repo(work: Path) -> None:
    _git(work, "init")
    _git(work, "config", "user.email", "autopilot@example.test")
    _git(work, "config", "user.name", "Autopilot Test")
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


def test_autopilot_runs_real_agent_loop_and_real_tests(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    cfg = SparkConfig()
    cfg.agent.approval = "full-auto"
    cfg.agent.sandbox_mode = "workspace"
    cfg.agent.network_access = False
    cfg.provider.model = "mock"
    store = SessionStore(tmp_path / "sessions.db")
    sid = store.create_session(tmp_path, "mock", title="autopilot e2e")
    sandbox = WorkdirSandbox(tmp_path, cfg)
    registry = ToolRegistry(ToolContext(sandbox=sandbox, config=cfg))
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="write-1",
                        name="write_file",
                        arguments={"path": "calc.py", "content": "def double(x):\n    return x * 2\n"},
                    ),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="Implemented double."), ChatDelta(type="end")],
            [ChatDelta(type="text", text="Audit complete."), ChatDelta(type="end")],
        ]
    )
    loop = AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=provider,
        registry=registry,
        store=store,
        session_id=sid,
        approver=None,
        memory=None,
    )
    state = new_state(
        run_id=sid,
        session_id=sid,
        workdir=tmp_path,
        requirement="add calc.double",
    )
    result = AutopilotRunner(
        state,
        turn_runner=make_agent_turn_runner(loop, timeout_sec=120),
        verifier=make_test_verifier(sandbox, timeout_sec=120),
        diff_probe=make_git_diff_probe(tmp_path),
        limits=AutopilotLimits(max_turns=3, task_timeout_sec=240),
        state_path=tmp_path / "state.json",
    ).run()
    try:
        assert result.status == "passed"
        assert result.turn == 2
        assert result.last_test_ok is True
        assert (tmp_path / "calc.py").read_text(encoding="utf-8") == (
            "def double(x):\n    return x * 2\n"
        )
        assert result.last_diff_digest != result.baseline_digest
    finally:
        store.close()
