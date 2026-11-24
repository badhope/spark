from __future__ import annotations

import json
from pathlib import Path

import pytest

from spark.autopilot import (
    AutopilotLimits,
    AutopilotRunner,
    AutopilotState,
    AutopilotTurn,
    DiffProbe,
    Verification,
    make_agent_turn_runner,
    make_git_diff_probe,
    new_state,
)
from spark.models import TurnEvent


class Sequence:
    def __init__(self, items):
        self.items = list(items)
        self.index = 0
        self.calls = 0

    def __call__(self, *args):
        self.calls += 1
        if not self.items:
            raise AssertionError("sequence exhausted")
        item = self.items[min(self.index, len(self.items) - 1)]
        self.index += 1
        return item


def _state(tmp_path: Path, requirement: str = "add a cache") -> AutopilotState:
    return new_state(
        run_id="run-1",
        session_id="session-1",
        workdir=tmp_path,
        requirement=requirement,
    )


def test_build_then_audit_passes(tmp_path: Path) -> None:
    state = _state(tmp_path)
    turns = Sequence(
        [
            AutopilotTurn(ok=True, summary="implemented cache"),
            AutopilotTurn(ok=True, summary="audit found no gaps"),
        ]
    )
    verifier = Sequence([Verification(ok=True, command="pytest", output="2 passed")])
    diff = Sequence(
        [
            DiffProbe(changed=False, digest="base"),
            DiffProbe(changed=True, digest="changed", summary="1 file changed"),
            DiffProbe(changed=True, digest="changed", summary="1 file changed"),
        ]
    )
    runner = AutopilotRunner(
        state,
        turn_runner=turns,
        verifier=verifier,
        diff_probe=diff,
        limits=AutopilotLimits(max_turns=3),
    )
    result = runner.run()

    assert result.status == "passed"
    assert result.turn == 2
    assert result.phase == "done"
    assert result.audit_done is True
    assert len(result.history) == 2
    assert [entry["phase"] for entry in result.history] == ["build", "audit"]


def test_failed_test_is_fed_back_and_repaired(tmp_path: Path) -> None:
    state = _state(tmp_path)
    prompts: list[str] = []

    def turns(prompt: str) -> AutopilotTurn:
        prompts.append(prompt)
        if len(prompts) == 1:
            return AutopilotTurn(ok=True, summary="first attempt")
        if len(prompts) == 2:
            return AutopilotTurn(ok=True, summary="fixed root cause")
        return AutopilotTurn(ok=True, summary="audit complete")

    verifier = Sequence(
        [
            Verification(ok=False, command="pytest", output="AssertionError: boom"),
            Verification(ok=True, command="pytest", output="3 passed"),
            Verification(ok=True, command="pytest", output="3 passed"),
        ]
    )
    diff = Sequence(
        [
            DiffProbe(changed=False, digest="base"),
            DiffProbe(changed=True, digest="attempt-1"),
            DiffProbe(changed=True, digest="attempt-2"),
            DiffProbe(changed=True, digest="attempt-2"),
        ]
    )
    result = AutopilotRunner(
        state,
        turn_runner=turns,
        verifier=verifier,
        diff_probe=diff,
        limits=AutopilotLimits(max_turns=5),
    ).run()

    assert result.status == "passed"
    assert result.turn == 3
    assert "AssertionError: boom" in prompts[1]
    assert "修复轮" in prompts[1] or "测试失败回灌" in prompts[1]
    assert result.history[0]["test_ok"] is False
    assert result.history[1]["test_ok"] is True


def test_no_progress_stops_instead_of_burning_turns(tmp_path: Path) -> None:
    state = _state(tmp_path)
    turns = Sequence([AutopilotTurn(ok=True, summary="same answer")])
    verifier = Sequence(
        [Verification(ok=False, command="pytest", output="same failure")]
    )
    diff = Sequence(
        [
            DiffProbe(changed=False, digest="base"),
            DiffProbe(changed=True, digest="same"),
            DiffProbe(changed=True, digest="same"),
            DiffProbe(changed=True, digest="same"),
        ]
    )
    result = AutopilotRunner(
        state,
        turn_runner=turns,
        verifier=verifier,
        diff_probe=diff,
        limits=AutopilotLimits(max_turns=6, no_progress_limit=2),
    ).run()

    assert result.status == "failed"
    assert result.turn == 3
    assert "无进展" in result.last_error


def test_max_turns_is_a_hard_stop(tmp_path: Path) -> None:
    state = _state(tmp_path)
    turns = Sequence([AutopilotTurn(ok=True, summary="still trying")])
    verifier = Sequence([Verification(ok=False, command="pytest", output="failure")])
    diff = Sequence(
        [
            DiffProbe(changed=False, digest="base"),
            DiffProbe(changed=True, digest="one"),
            DiffProbe(changed=True, digest="two"),
        ]
    )
    result = AutopilotRunner(
        state,
        turn_runner=turns,
        verifier=verifier,
        diff_probe=diff,
        limits=AutopilotLimits(max_turns=2, no_progress_limit=5),
    ).run()

    assert result.status == "failed"
    assert result.turn == 2
    assert "最大轮次" in result.last_error


def test_task_timeout_fails_fast(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.started_at = 0.0
    result = AutopilotRunner(
        state,
        turn_runner=Sequence([AutopilotTurn(ok=True)]),
        verifier=Sequence([Verification(ok=True)]),
        diff_probe=Sequence([DiffProbe(changed=False, digest="base")]),
        limits=AutopilotLimits(task_timeout_sec=60, max_turns=3),
    ).run()

    assert result.status == "failed"
    assert result.turn == 0
    assert "总时限" in result.last_error


def test_cost_budget_is_enforced(tmp_path: Path) -> None:
    state = _state(tmp_path)
    result = AutopilotRunner(
        state,
        turn_runner=Sequence([AutopilotTurn(ok=True, summary="costly", cost_usd=2.5)]),
        verifier=Sequence([Verification(ok=True, command="pytest", output="ok")]),
        diff_probe=Sequence(
            [
                DiffProbe(changed=False, digest="base"),
                DiffProbe(changed=True, digest="changed"),
            ]
        ),
        limits=AutopilotLimits(max_turns=3, max_cost_usd=1.0),
    ).run()

    assert result.status == "failed"
    assert result.turn == 1
    assert result.total_cost_usd == 2.5
    assert "成本上限" in result.last_error


def test_consecutive_model_errors_stop_run(tmp_path: Path) -> None:
    state = _state(tmp_path)
    turns = Sequence(
        [
            AutopilotTurn(ok=False, error="provider timeout"),
            AutopilotTurn(ok=False, error="provider timeout"),
        ]
    )
    result = AutopilotRunner(
        state,
        turn_runner=turns,
        verifier=Sequence([Verification(ok=False, command="pytest", output="failure")]),
        diff_probe=Sequence(
            [
                DiffProbe(changed=False, digest="base"),
                DiffProbe(changed=True, digest="one"),
                DiffProbe(changed=True, digest="two"),
            ]
        ),
        limits=AutopilotLimits(
            max_turns=4, no_progress_limit=5, max_consecutive_errors=2
        ),
    ).run()

    assert result.status == "failed"
    assert result.turn == 2
    assert "连续 2 轮" in result.last_error


def test_passing_tests_without_changes_is_not_a_delivery(tmp_path: Path) -> None:
    state = _state(tmp_path)
    turns = Sequence([AutopilotTurn(ok=True, summary="nothing changed")])
    verifier = Sequence([Verification(ok=True, command="pytest", output="ok")])
    diff = Sequence(
        [DiffProbe(changed=False, digest="base"), DiffProbe(changed=False, digest="base")]
    )
    result = AutopilotRunner(
        state,
        turn_runner=turns,
        verifier=verifier,
        diff_probe=diff,
        limits=AutopilotLimits(max_turns=4),
    ).run()
    assert result.status == "failed"
    assert "没有代码变更" in result.last_error


def test_state_roundtrip_and_resume(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.turn = 2
    state.phase = "repair"
    state.last_test_output = "boom"
    state.next_prompt = "fix it"
    state.history.append({"turn": 1, "phase": "build", "ok": True})
    path = tmp_path / "state.json"
    state.save(path)

    loaded = AutopilotState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    assert loaded.requirement == "add a cache"
    assert loaded.next_prompt == "fix it"
    assert loaded.history == state.history

    loaded.status = "running"
    turns = Sequence([AutopilotTurn(ok=True, summary="fixed")])
    verifier = Sequence([Verification(ok=True, command="pytest", output="ok")])
    diff = Sequence(
        [
            DiffProbe(changed=False, digest="base"),
            DiffProbe(changed=True, digest="changed"),
            DiffProbe(changed=True, digest="changed"),
        ]
    )
    result = AutopilotRunner(
        loaded,
        turn_runner=turns,
        verifier=verifier,
        diff_probe=diff,
        limits=AutopilotLimits(max_turns=4),
        state_path=path,
    ).run()
    assert result.status == "passed"
    assert result.turn == 4


def test_agent_turn_runner_captures_text_and_cost() -> None:
    class FakeLoop:
        _turn_usage_totals = {"cost_usd": 0.12}

        def iter_turn_sync(self, prompt, images=None, timeout_sec=None):
            yield TurnEvent(type="tool_start", tool_call=None)
            yield TurnEvent(type="turn_end", text="done")

    runner = make_agent_turn_runner(FakeLoop(), timeout_sec=30)
    result = runner("do it")
    assert result.ok is True
    assert result.summary == "done"
    assert result.tool_calls == 1
    assert result.cost_usd == pytest.approx(0.12)


def test_agent_turn_runner_surfaces_timeout() -> None:
    class TimeoutLoop:
        def iter_turn_sync(self, prompt, images=None, timeout_sec=None):
            raise TimeoutError("turn timed out after 3s")

    result = make_agent_turn_runner(TimeoutLoop(), timeout_sec=3)("do it")
    assert result.ok is False
    assert "timed out" in result.error


def test_non_git_diff_probe_changes_with_file_content(tmp_path: Path) -> None:
    probe = make_git_diff_probe(tmp_path)
    before = probe()
    assert before.changed is False
    (tmp_path / "work.py").write_text("print('before')\n", encoding="utf-8")
    after = probe()
    assert before.digest != after.digest
    assert after.changed is True
    assert after.summary == "non-git workdir"


def test_non_git_diff_probe_ignores_autopilot_state(tmp_path: Path) -> None:
    """State files must not look like code progress in non-git workdirs."""
    probe = make_git_diff_probe(tmp_path)
    before = probe()
    state_dir = tmp_path / ".spark" / "autopilot"
    state_dir.mkdir(parents=True)
    (state_dir / "run.json").write_text('{"turn": 1}\n', encoding="utf-8")
    after = probe()
    assert after.digest == before.digest
    assert after.changed is False


def test_git_diff_probe_ignores_spark_state(tmp_path: Path) -> None:
    """A real git repo must not look dirty only because .spark was written."""
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    (tmp_path / "work.py").write_text("print('before')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)

    # A workspace that is already dirty must not count as "changed" by itself.
    (tmp_path / "work.py").write_text("print('before, dirty')\n", encoding="utf-8")
    probe = make_git_diff_probe(tmp_path)
    before = probe()
    assert before.changed is False

    state_dir = tmp_path / ".spark" / "autopilot"
    state_dir.mkdir(parents=True)
    (state_dir / "run.json").write_text('{"turn": 1}\n', encoding="utf-8")
    after_state = probe()
    assert after_state.digest == before.digest
    assert after_state.changed is False

    (tmp_path / "work.py").write_text("print('after')\n", encoding="utf-8")
    after_code = probe()
    assert after_code.changed is True
    assert after_code.digest != before.digest


def test_prompt_files_are_rendered_with_requirement(tmp_path: Path) -> None:
    state = _state(tmp_path, requirement="ship a real feature")
    from spark.autopilot import render_build_prompt

    prompt = render_build_prompt(state, AutopilotLimits(max_turns=4))
    assert "ship a real feature" in prompt
    assert "场景矩阵" in prompt
    assert "本轮任务" in prompt
