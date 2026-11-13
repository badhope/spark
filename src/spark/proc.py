"""Bounded subprocess execution with a hard wall-clock guarantee.

Why this module exists
----------------------
``subprocess.run(..., timeout=T)`` does **not** provide a hard timeout on
Windows.  When the timeout expires CPython kills only the direct child and
then calls ``process.communicate()`` *again, without a timeout*, which blocks
until every writer of the captured pipe has closed it.  Tools that spawn
helpers (git spawns ``git-*`` helpers, npm spawns node, hooks spawn shells)
leave those helpers holding the inherited pipe handle, so the call never
returns: the agent hangs forever instead of timing out.

The same class of bug appears whenever a child inherits the parent's stdin.
A child that keeps the parent's stdin pipe open can block the parent's own
protocol loop — for the ACP server that stdin *is* the JSON-RPC channel.

``run_bounded()`` therefore:

* never inherits stdin (``DEVNULL``, or an explicit pipe when ``input_text``
  is supplied);
* redirects stdout/stderr to real files, so there is no pipe EOF to wait for;
* bounds the wait on the process itself and kills the whole tree on timeout;
* returns whatever was captured, plus a ``timed_out`` flag.

This module sits in the L0 foundation layer: it may only use the standard
library (plus the optional :mod:`psutil` dependency).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["BoundedResult", "run_bounded"]


def _feed_stdin(proc: subprocess.Popen[bytes], payload: bytes) -> None:
    """Write *payload* to the child's stdin and close it; never raises."""
    try:
        if proc.stdin is not None:
            proc.stdin.write(payload)
            proc.stdin.flush()
    except Exception:  # noqa: BLE001 - the child may exit early
        pass
    finally:
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass


@dataclass(slots=True)
class BoundedResult:
    """Outcome of a bounded command execution."""

    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def check_returncode(self) -> None:
        if self.timed_out:
            raise subprocess.TimeoutExpired(self.args, 0.0, output=self.stdout)
        if self.returncode != 0:
            raise subprocess.CalledProcessError(self.returncode, self.args, self.stdout, self.stderr)


def _kill_tree(pid: int, process: subprocess.Popen[bytes]) -> None:
    """Terminate the process and every descendant; never raises."""
    try:
        from spark.platform_compat import kill_process_tree

        kill_process_tree(pid, process)
        return
    except Exception:  # noqa: BLE001 - fall through to the plain kill
        pass
    try:
        process.kill()
    except Exception:  # noqa: BLE001 - already gone
        pass


def _read_text(handle: Any, encoding: str | None, errors: str | None) -> str:
    try:
        handle.seek(0)
        raw = handle.read()
    except Exception:  # noqa: BLE001 - a vanished temp file is not fatal
        return ""
    if not isinstance(raw, bytes):  # pragma: no cover - defensive
        return str(raw)
    return raw.decode(encoding or "utf-8", errors or "replace")


def run_bounded(
    argv: Sequence[str] | str,
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = 10.0,
    input_text: str | None = None,
    encoding: str | None = None,
    errors: str | None = None,
    creationflags: int = 0,
    shell: bool = False,
    check: bool = False,
    kill_grace: float = 2.0,
) -> BoundedResult:
    """Run *argv* and return its output, never blocking longer than *timeout*.

    Args:
        argv: Command to execute (list form recommended).
        cwd: Working directory for the child.
        env: Complete environment for the child; ``None`` inherits the parent's.
        timeout: Wall-clock budget in seconds for the child to finish.
        input_text: Text written to the child's stdin (which is then closed).
        encoding: Encoding used to decode the captured output.
        errors: Error handler used while decoding.
        creationflags: Platform ``creationflags`` for :class:`subprocess.Popen`.
        shell: Run through the platform shell.
        check: Raise :class:`subprocess.CalledProcessError` on a non-zero exit.
        kill_grace: Extra seconds granted to a killed tree to exit.

    Returns:
        A :class:`BoundedResult`.  ``timed_out`` is ``True`` when the budget
        was exhausted; the captured output then contains whatever the command
        managed to write before it was killed.
    """
    args = list(argv) if not isinstance(argv, str) else [argv]
    out_file = tempfile.TemporaryFile(mode="w+b")
    err_file = tempfile.TemporaryFile(mode="w+b")
    payload: bytes | None = None
    if input_text is not None:
        payload = input_text.encode(encoding or "utf-8", errors or "replace")
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv is constructed by the caller
            args,
            stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
            stdout=out_file,
            stderr=err_file,
            cwd=os.fspath(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            creationflags=creationflags,
            shell=shell,
        )
    except OSError:
        out_file.close()
        err_file.close()
        raise

    if payload is not None and proc.stdin is not None:
        # Feed the child from a private pipe so it can never consume the
        # parent's own stdin, and close it so the child sees EOF.
        writer = threading.Thread(target=_feed_stdin, args=(proc, payload), daemon=True)
        writer.start()

    timed_out = False
    try:
        proc.wait(timeout=max(0.0, float(timeout)))
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc.pid, proc)
        try:
            proc.wait(timeout=kill_grace)
        except subprocess.TimeoutExpired:  # pragma: no cover - stubborn tree
            pass
    except Exception:  # noqa: BLE001 - a broken child must not kill the caller
        timed_out = True
        _kill_tree(proc.pid, proc)

    stdout = _read_text(out_file, encoding, errors)
    stderr = _read_text(err_file, encoding, errors)
    out_file.close()
    err_file.close()

    result = BoundedResult(
        args=args,
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
    )
    if check:
        result.check_returncode()
    return result
