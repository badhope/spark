from __future__ import annotations

import json
import time
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
from spark.models import ToolCall, TurnEvent


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


def test_in_flight_turn_is_persisted_before_model_call(tmp_path: Path) -> None:
    """A crash during the model call must leave a resumable in-flight marker."""
    state = _state(tmp_path)
    path = tmp_path / "state.json"
    seen: list[dict] = []

    def turns(prompt: str) -> AutopilotTurn:
        seen.append(json.loads(path.read_text(encoding="utf-8")))
        return AutopilotTurn(ok=True, summary="implemented")

    verifier = Sequence([Verification(ok=True, command="pytest", output="ok")])
    diff = Sequence(
        [
            DiffProbe(changed=False, digest="base"),
            DiffProbe(changed=True, digest="changed"),
            DiffProbe(changed=True, digest="changed"),
        ]
    )
    result = AutopilotRunner(
        state,
        turn_runner=turns,
        verifier=verifier,
        diff_probe=diff,
        limits=AutopilotLimits(max_turns=2, heartbeat_interval_sec=1),
        state_path=path,
    ).run()

    assert result.status == "passed"
    assert seen[0]["turn_in_progress"] is True
    assert seen[0]["active_prompt"]
    assert seen[0]["pid"] > 0
    final = json.loads(path.read_text(encoding="utf-8"))
    assert final["turn_in_progress"] is False
    assert final["active_prompt"] == ""


def test_heartbeat_touches_state_file_during_slow_turn(tmp_path: Path) -> None:
    """A long model call must leave an observable liveness timestamp."""
    import time

    state = _state(tmp_path)
    path = tmp_path / "state.json"
    observed: list[tuple[int, int]] = []

    def turns(prompt: str) -> AutopilotTurn:
        before = path.stat().st_mtime_ns
        time.sleep(1.2)
        after = path.stat().st_mtime_ns
        observed.append((before, after))
        return AutopilotTurn(ok=True, summary="implemented")

    result = AutopilotRunner(
        state,
        turn_runner=turns,
        verifier=Sequence([Verification(ok=True, command="pytest", output="ok")]),
        diff_probe=Sequence(
            [
                DiffProbe(changed=False, digest="base"),
                DiffProbe(changed=True, digest="changed"),
                DiffProbe(changed=True, digest="changed"),
            ]
        ),
        limits=AutopilotLimits(max_turns=2, heartbeat_interval_sec=1),
        state_path=path,
    ).run()

    assert result.status == "passed"
    assert observed
    assert observed[0][1] > observed[0][0]


def _crash_child(state: AutopilotState, path: Path, started: Path) -> None:
    """Child process used by the real SIGKILL recovery test."""

    def turns(prompt: str) -> AutopilotTurn:
        started.write_text(prompt, encoding="utf-8")
        time.sleep(30)
        return AutopilotTurn(ok=True, summary="never reached")

    AutopilotRunner(
        state,
        turn_runner=turns,
        verifier=lambda: Verification(ok=False, command="pytest", output="failure"),
        diff_probe=lambda: DiffProbe(changed=True, digest="base"),
        limits=AutopilotLimits(max_turns=3, heartbeat_interval_sec=1),
        state_path=path,
    ).run()


@pytest.mark.skipif(__import__("os").name != "posix", reason="requires POSIX signals")
def test_kill_9_leaves_recoverable_in_flight_turn(tmp_path: Path) -> None:
    """Kill a real child process mid-turn, then resume without skipping it."""
    import multiprocessing
    import os
    import signal
    import time

    state = _state(tmp_path)
    path = tmp_path / "state.json"
    started = tmp_path / "turn-started.txt"
    ctx = multiprocessing.get_context("fork")
    proc = ctx.Process(target=_crash_child, args=(state, path, started))
    proc.start()
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            if path.exists() and started.exists():
                break
            time.sleep(0.05)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["turn_in_progress"] is True
        assert data["active_prompt"]
        assert data["pid"] == proc.pid
        os.kill(proc.pid, signal.SIGKILL)
        proc.join(timeout=5)
        assert proc.exitcode == -signal.SIGKILL
    finally:
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=5)

    loaded = AutopilotState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    assert loaded.turn_in_progress is True
    assert loaded.active_prompt
    assert loaded.prepare_resume() is True
    assert loaded.recovered_interruptions == 1
    assert loaded.next_prompt == data["active_prompt"]

    result = AutopilotRunner(
        loaded,
        turn_runner=Sequence([AutopilotTurn(ok=True, summary="implemented")]),
        verifier=Sequence([Verification(ok=True, command="pytest", output="ok")]),
        diff_probe=Sequence(
            [
                DiffProbe(changed=True, digest="changed"),
                DiffProbe(changed=True, digest="changed"),
            ]
        ),
        limits=AutopilotLimits(max_turns=3),
        state_path=path,
    ).run()
    assert result.status == "passed"
    assert result.recovered_interruptions == 1


def test_prepare_resume_marks_interrupted_audit_for_retry(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.turn = 2
    state.phase = "audit"
    state.audit_done = True
    state.turn_in_progress = True
    state.active_prompt = "audit the diff"
    state.history.append({"turn": 1, "phase": "build", "ok": True})

    assert state.prepare_resume() is True
    assert state.recovered_interruptions == 1
    assert state.turn_in_progress is False
    assert state.audit_done is False
    assert state.next_prompt == "audit the diff"
    assert state.history[-1]["recovered"] is True
    assert "中断" in state.last_error
    assert state.status == "running"


def test_tool_digest_tracks_arguments_not_only_call_count() -> None:
    class FakeLoop:
        def __init__(self, value: str) -> None:
            self.value = value

        def iter_turn_sync(self, prompt, images=None, timeout_sec=None):
            yield TurnEvent(
                type="tool_start",
                tool_call=ToolCall(
                    id="call-1",
                    name="write_file",
                    arguments={"path": "a.py", "content": self.value},
                ),
            )
            yield TurnEvent(type="turn_end", text="done")

    first = make_agent_turn_runner(FakeLoop("one"), timeout_sec=30)("do it")
    second = make_agent_turn_runner(FakeLoop("two"), timeout_sec=30)("do it")
    assert first.tool_calls == second.tool_calls == 1
    assert first.tool_digest != second.tool_digest


def test_three_fingerprints_reset_no_progress_when_tools_change(tmp_path: Path) -> None:
    state = _state(tmp_path)
    turns = Sequence(
        [
            AutopilotTurn(ok=True, summary="same words", tool_digest="tool-a"),
            AutopilotTurn(ok=True, summary="same words", tool_digest="tool-b"),
            AutopilotTurn(ok=True, summary="same words", tool_digest="tool-a"),
            AutopilotTurn(ok=True, summary="same words", tool_digest="tool-b"),
        ]
    )
    verifier = Sequence([Verification(ok=False, command="pytest", output="same failure")])
    diff = Sequence(
        [
            DiffProbe(changed=False, digest="base"),
            DiffProbe(changed=True, digest="same"),
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
        limits=AutopilotLimits(max_turns=3, no_progress_limit=2),
    ).run()

    assert result.status == "failed"
    assert result.turn == 3
    assert "最大轮次" in result.last_error
    assert "无进展" not in result.last_error


def test_three_fingerprints_stop_when_tools_also_repeat(tmp_path: Path) -> None:
    state = _state(tmp_path)
    turns = Sequence(
        [AutopilotTurn(ok=True, summary="same words", tool_digest="tool-a")]
    )
    verifier = Sequence([Verification(ok=False, command="pytest", output="same failure")])
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
    assert "无进展" in result.last_error
    assert "工具调用指纹" in result.last_error


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
