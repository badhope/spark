"""Read-only Git change view for the Workbench Changes panel.

Only ``git status`` / ``git diff`` against the current worktree are exposed.
There is deliberately no add/commit/push surface here: those remain agent
tools with their own approval path.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

MAX_STATUS_LINES = 400
MAX_DIFF_CHARS = 400_000


def _run(cwd: Path, *args: str, timeout: int = 15) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "--no-pager", "--no-optional-locks", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_PAGER": "cat",
                "GIT_TERMINAL_PROMPT": "0",
                "LC_ALL": "C",
            },
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return 127, ""


def _parse_status(raw: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in raw.splitlines()[:MAX_STATUS_LINES]:
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:].strip()
        if " -> " in path:
            old, new = path.split(" -> ", 1)
            entries.append({"code": code, "path": new.strip(), "old_path": old.strip()})
        else:
            entries.append({"code": code, "path": path})
    return entries


def git_changes(workdir: str) -> dict[str, Any]:
    """Return branch, porcelain status and a bounded unified diff.

    Non-repositories return ``repo: false`` rather than an error: the caller is
    a UI panel, and "not a git repo" is a normal state (a plain scratch dir).
    """
    try:
        cwd = Path(workdir).resolve()
    except OSError:
        return {"repo": False, "branch": "", "entries": [], "diff": "", "truncated": False}
    if not cwd.is_dir():
        return {"repo": False, "branch": "", "entries": [], "diff": "", "truncated": False}
    code, _ = _run(cwd, "rev-parse", "--is-inside-work-tree")
    if code != 0:
        return {"repo": False, "branch": "", "entries": [], "diff": "", "truncated": False}

    branch_code, branch_raw = _run(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch_raw.strip().splitlines()[0] if branch_code == 0 else ""
    _, status_raw = _run(cwd, "status", "--porcelain=v1")
    entries = _parse_status(status_raw)

    diff_code, diff_raw = _run(cwd, "diff", "--no-color", "--no-ext-diff", "HEAD")
    if diff_code != 0:
        # Unborn HEAD (fresh repo) has no HEAD to diff against; show both
        # working-tree and index diffs instead of an empty panel.
        _, work = _run(cwd, "diff", "--no-color", "--no-ext-diff")
        _, staged = _run(cwd, "diff", "--cached", "--no-color", "--no-ext-diff")
        diff_raw = work + staged

    truncated = len(diff_raw) > MAX_DIFF_CHARS
    diff = diff_raw[:MAX_DIFF_CHARS]
    return {
        "repo": True,
        "branch": branch,
        "entries": entries,
        "diff": diff,
        "truncated": truncated,
        "status_count": len(status_raw.splitlines()),
    }
