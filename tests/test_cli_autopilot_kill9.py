"""Real CLI + AgentLoop crash test: SIGKILL a running turn, then --resume.

This deliberately runs a child Python process instead of calling the CLI
in-process.  A ``CliRunner`` cannot be killed, so it would only prove the
runner's state machine works; this test proves the actual ``spark autopilot``
process leaves a resumable ledger and retries the interrupted turn.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from spark.proc import run_bounded

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="SIGKILL-based recovery is POSIX-only"
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _git(work: Path, *args: str) -> None:
    run_bounded(["git", *args], cwd=work, timeout=30)


def _init_repo(work: Path) -> None:
    work.mkdir(parents=True)
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


_SITECUSTOMIZE = r'''
"""Test-only provider patch for the CLI kill/resume test.

sitecustomize is imported by the child interpreter before spark.cli imports
create_provider, so replacing the factory here affects the real CLI process.
"""
import asyncio
import os

from spark.models import ChatDelta, ToolCall
from spark.providers.mock import MockProvider
import spark.providers.factory as factory

_original = factory.create_provider


class _ScriptedProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, messages, tools, effort=None):
        if os.environ.get("SPARK_KILL_HANG") == "1":
            # The parent waits until turn_in_progress is on disk, then SIGKILLs
            # this process.  Without the kill this turn would never finish.
            await asyncio.sleep(3600)
        self.calls += 1
        if self.calls == 1:
            yield ChatDelta(
                type="tool_call",
                tool_call=ToolCall(
                    id="write-1",
                    name="write_file",
                    arguments={
                        "path": "calc.py",
                        "content": "def double(x):\n    return x * 2\n",
                    },
                ),
            )
            yield ChatDelta(type="end")
            return
        if self.calls == 2:
            yield ChatDelta(type="text", text="Implemented double.")
            yield ChatDelta(type="end")
            return
        yield ChatDelta(type="text", text="Audit complete.")
        yield ChatDelta(type="end")


def create_provider(cfg, mock=None):
    if getattr(getattr(cfg, "provider", None), "name", "") == "mock":
        return _ScriptedProvider()
    return _original(cfg, mock)


factory.create_provider = create_provider
'''


def _read_state(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _wait_for_interrupted_turn(proc: subprocess.Popen[str], state_path: Path) -> dict:
    deadline = time.time() + 40
    while time.time() < deadline:
        state = _read_state(state_path)
        if state and state.get("turn_in_progress"):
            return state
        if proc.poll() is not None:
            stdout, stderr = proc.communicate(timeout=10)
            pytest.fail(
                f"autopilot exited before the kill window (code {proc.returncode})\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}"
            )
        time.sleep(0.2)
    proc.kill()
    proc.wait(timeout=10)
    pytest.fail("timed out waiting for turn_in_progress in the state ledger")


def test_cli_autopilot_sigkill_then_resume(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
    state_path = tmp_path / "autopilot-state.json"

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(site_dir), str(_REPO_ROOT), env.get("PYTHONPATH", "")]
    )
    env["SPARK_KILL_HANG"] = "1"

    cmd = [
        sys.executable,
        "-m",
        "spark",
        "autopilot",
        "add calc.double",
        "--workdir",
        str(repo),
        "--provider",
        "mock",
        "--state-out",
        str(state_path),
        "--max-turns",
        "3",
        "--turn-timeout",
        "60",
        "--task-timeout",
        "300",
        "--test-timeout",
        "60",
        "--heartbeat-interval",
        "5",
        "--json",
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(repo),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        interrupted = _wait_for_interrupted_turn(proc, state_path)
        assert interrupted["turn"] == 1
        assert interrupted["turn_in_progress"] is True
        # Popen.kill() sends SIGKILL on POSIX.  This is the same signal as
        # `kill -9`; kill the process group so any helper does not outlive it.
        os.killpg(proc.pid, 9)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

    resume_env = env.copy()
    resume_env.pop("SPARK_KILL_HANG", None)
    resume_cmd = [
        sys.executable,
        "-m",
        "spark",
        "autopilot",
        "--workdir",
        str(repo),
        "--provider",
        "mock",
        "--resume",
        str(state_path),
        "--max-turns",
        "3",
        "--turn-timeout",
        "60",
        "--task-timeout",
        "300",
        "--test-timeout",
        "60",
        "--heartbeat-interval",
        "5",
        "--json",
    ]
    resumed = subprocess.run(
        resume_cmd,
        cwd=str(repo),
        env=resume_env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert resumed.returncode == 0, resumed.stderr
    final = _read_state(state_path)
    assert final is not None
    assert final["status"] == "passed"
    assert final["recovered_interruptions"] >= 1
    assert final["turn"] == 3
    assert (repo / "calc.py").read_text(encoding="utf-8") == (
        "def double(x):\n    return x * 2\n"
    )
    assert any(item.get("recovered") for item in final["history"])
