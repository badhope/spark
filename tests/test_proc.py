"""Regression tests for the bounded subprocess helper.

The bug these lock down: on Windows ``subprocess.run(..., timeout=T)`` is not
a hard timeout.  When it expires CPython kills only the direct child and then
calls ``communicate()`` again *without* a timeout, which waits for every
writer of the captured pipe to close it.  A descendant that inherited the pipe
keeps it open forever, so the call — and every agent turn behind it — hangs.

``spark.proc.run_bounded`` must always return within its budget.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from spark.proc import run_bounded

#: Child that spawns a grandchild inheriting stdout, then sleeps.  The
#: grandchild keeps the pipe write end open after the child is killed.
_HOLDER = (
    "import subprocess, sys, time\n"
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "time.sleep(60)\n"
)


def test_run_bounded_returns_output() -> None:
    res = run_bounded([sys.executable, "-c", "print('hello')"], timeout=20.0)
    assert res.timed_out is False
    assert res.ok is True
    assert "hello" in res.stdout


def test_run_bounded_does_not_inherit_stdin() -> None:
    """A child must never be able to consume the parent's stdin.

    The ACP server's stdin *is* the JSON-RPC channel; a child that blocks on
    it deadlocks the whole protocol.
    """
    res = run_bounded(
        [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"],
        timeout=20.0,
    )
    assert res.timed_out is False
    # DEVNULL gives an immediate EOF instead of waiting for input.
    assert "''" in res.stdout


def test_run_bounded_times_out_when_descendant_holds_the_pipe() -> None:
    """The historical hang: a descendant keeps the output pipe open."""
    started = time.monotonic()
    res = run_bounded([sys.executable, "-c", _HOLDER], timeout=2.0)
    elapsed = time.monotonic() - started

    assert res.timed_out is True
    # The whole point: bounded, not hung.  The generous ceiling still catches
    # a regression to the unbounded CPython fallback.
    assert elapsed < 30.0, f"run_bounded took {elapsed:.1f}s - it hung again"


def test_run_bounded_kills_the_process_tree_on_timeout(tmp_path: Path) -> None:
    marker = tmp_path / "tree-marker.txt"
    grandchild = tmp_path / "grandchild.py"
    grandchild.write_text(
        "import time\n"
        "time.sleep(60)\n"
        f"open({str(marker)!r}, 'w').write('x')\n",
        encoding="utf-8",
    )
    child = tmp_path / "child.py"
    child.write_text(
        "import subprocess, sys\n"
        f"subprocess.Popen([sys.executable, {str(grandchild)!r}])\n"
        "import time\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    res = run_bounded([sys.executable, str(child)], timeout=2.0)
    assert res.timed_out is True
    # The grandchild must not survive to write the marker.
    time.sleep(1.0)
    assert not marker.exists()


def test_run_bounded_supports_stdin_payload() -> None:
    res = run_bounded(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        input_text="abc",
        timeout=20.0,
    )
    assert res.timed_out is False
    assert "ABC" in res.stdout


def test_run_bounded_reports_non_zero_exit() -> None:
    res = run_bounded([sys.executable, "-c", "import sys; sys.exit(3)"], timeout=20.0)
    assert res.returncode == 3
    assert res.ok is False


def test_run_bounded_raises_on_missing_executable() -> None:
    try:
        run_bounded(["spark-command-that-does-not-exist"], timeout=5.0)
    except OSError:
        return
    raise AssertionError("expected OSError for a missing executable")


def test_run_bounded_uses_cwd_and_env(tmp_path: Path) -> None:
    res = run_bounded(
        [
            sys.executable,
            "-c",
            "import os, sys; sys.stdout.write(os.getcwd()); sys.stdout.write('|'); "
            "sys.stdout.write(os.environ.get('SPARK_PROC_TEST', ''))",
        ],
        cwd=tmp_path,
        env={**os.environ, "SPARK_PROC_TEST": "ok"},
        timeout=20.0,
    )
    assert res.stdout.endswith("|ok")


def test_run_bounded_check_raises_on_failure() -> None:
    try:
        run_bounded([sys.executable, "-c", "import sys; sys.exit(2)"], timeout=20.0, check=True)
    except subprocess.CalledProcessError:
        return
    raise AssertionError("expected CalledProcessError")
