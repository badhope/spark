from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel

from spark.models import ToolResult
from spark.sandbox import WorkdirSandbox
from spark.tools._common import clip_text

MAX_OUTPUT = 12_000

# Read-only commands the model may invoke directly.
_ALLOWED = {
    "status",
    "diff",
    "log",
    "branch",
}  # Mutating commands exposed as explicit tools below.
_ALLOWED_MUTATING = {"add", "commit"}

# Commands the model must never run through these tools.
BLOCKED = {
    "push",
    "pull",
    "fetch",
    "rebase",
    "merge",
    "reset",
    "checkout",
    "clean",
    "cherry-pick",
    "revert",
    "tag",
    "stash",
}


class GitStatusArgs(BaseModel):
    path: str = "."


class GitDiffArgs(BaseModel):
    path: str = "."
    staged: bool = False
    ref: str | None = None


class GitLogArgs(BaseModel):
    path: str = "."
    max_count: int = 20


class GitBranchArgs(BaseModel):
    path: str = "."


class GitAddArgs(BaseModel):
    paths: list[str]
    path: str = "."


class GitCommitArgs(BaseModel):
    message: str
    path: str = "."
    add_all: bool = False


class WorktreeListArgs(BaseModel):
    path: str = "."


class WorktreeCreateArgs(BaseModel):
    branch: str
    path: str = "."
    base: str | None = None


class WorktreeRemoveArgs(BaseModel):
    worktree: str
    force: bool = False


class RunTestsArgs(BaseModel):
    path: str = "."
    command: str | None = None
    timeout_sec: int = 600


_SAFE_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,100}$")
_TEST_TIMEOUT = 600
MAX_TEST_TIMEOUT_SEC = 1800


def _has_pytest_files(root: Path) -> bool:
    """True when the tree contains pytest-style test modules.

    A plain layout (``test_calc.py`` next to the code, no ``tests/`` dir, no
    ini file) is extremely common — a single-file script or a small project —
    and previously went undetected, so ``run_tests`` refused to run at all.
    """
    for path in root.rglob("test_*.py"):
        if any(part in {".git", "node_modules", ".venv", "venv", "__pycache__"} for part in path.parts):
            continue
        return True
    return (root / "conftest.py").is_file()


def detect_test_command(root: Path) -> str | None:
    """Best-effort test command for a repo; None when no signal exists."""
    if (root / "pyproject.toml").is_file() and (root / "tests").is_dir():
        return "python -m pytest -q"
    if (root / "pytest.ini").is_file() or (root / "tox.ini").is_file():
        return "python -m pytest -q"
    if (root / "setup.cfg").is_file() and _has_pytest_files(root):
        return "python -m pytest -q"
    if _has_pytest_files(root):
        return "python -m pytest -q"
    package = root / "package.json"
    if package.is_file():
        try:
            import json

            scripts = (
                json.loads(package.read_text(encoding="utf-8")).get("scripts") or {}
            )
        except (OSError, ValueError):
            return None
        for name in ("test", "test:unit", "test:ci"):
            if name in scripts:
                return f"npm run {name}"
    if (root / "Makefile").is_file():
        return "make test"
    return None


def _run_git(
    sandbox: WorkdirSandbox, cwd_arg: str, args: list[str], timeout: int = 30
) -> ToolResult:
    """Run git inside the sandbox-resolved repo dir and capture output."""
    try:
        cwd = sandbox.resolve(cwd_arg or ".")
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    if not cwd.is_dir():
        return ToolResult(
            ok=False, payload={"error": f"directory not found: {cwd_arg}"}
        )
    if not (cwd / ".git").exists():
        return ToolResult(
            ok=False, payload={"error": f"not a git repository: {cwd_arg}"}
        )
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(ok=False, payload={"error": f"git {args[0]} timed out"})
    except FileNotFoundError:
        return ToolResult(ok=False, payload={"error": "git is not installed"})
    ok = completed.returncode == 0
    payload: dict = {
        "command": f"git {' '.join(args)}",
        "exit_code": completed.returncode,
    }
    if completed.stdout:
        payload["output"] = clip_text(completed.stdout.strip(), MAX_OUTPUT)
    if completed.stderr:
        payload["stderr"] = clip_text(completed.stderr.strip(), MAX_OUTPUT)
    return ToolResult(ok=ok, payload=payload)


def git_status(sandbox: WorkdirSandbox, args: GitStatusArgs) -> ToolResult:
    return _run_git(sandbox, args.path, ["status", "--short", "--branch"])


_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-^~:\s]*$")


def _validate_ref(ref: str) -> str | None:
    if not ref or ref.startswith("-") or "\n" in ref or "\r" in ref or "\x00" in ref:
        return f"invalid ref: {ref!r}"
    if not _SAFE_REF.match(ref):
        return f"invalid ref: {ref!r}"
    return None


def git_diff(sandbox: WorkdirSandbox, args: GitDiffArgs) -> ToolResult:
    cmd = ["diff"]
    if args.ref:
        err = _validate_ref(args.ref)
        if err:
            return ToolResult(ok=False, payload={"error": err})
        cmd.append(args.ref)
    if args.staged:
        cmd.append("--staged")
    return _run_git(sandbox, args.path, cmd)


def git_log(sandbox: WorkdirSandbox, args: GitLogArgs) -> ToolResult:
    count = max(1, min(100, args.max_count))
    return _run_git(
        sandbox,
        args.path,
        ["log", f"--max-count={count}", "--pretty=format:%h|%an|%ar|%s"],
    )


def git_branch(sandbox: WorkdirSandbox, args: GitBranchArgs) -> ToolResult:
    return _run_git(sandbox, args.path, ["branch", "--show-current"])


def git_add(sandbox: WorkdirSandbox, args: GitAddArgs) -> ToolResult:
    paths = [p.strip() for p in args.paths if p.strip()]
    if not paths:
        return ToolResult(ok=False, payload={"error": "paths required"})
    for p in paths:
        if p.startswith("-") or ".." in Path(p).parts:
            return ToolResult(ok=False, payload={"error": f"path rejected: {p}"})
        try:
            sandbox.resolve(p)
        except Exception as exc:
            return ToolResult(ok=False, payload={"error": f"path rejected: {p}: {exc}"})
    return _run_git(sandbox, args.path, ["add", "--", *paths])


def git_commit(sandbox: WorkdirSandbox, args: GitCommitArgs) -> ToolResult:
    message = args.message.strip()
    if not message:
        return ToolResult(ok=False, payload={"error": "message required"})
    if len(message) > 2000:
        return ToolResult(
            ok=False, payload={"error": "message too long (max 2000 chars)"}
        )
    if args.add_all:
        pre = _run_git(sandbox, args.path, ["add", "-A"])
        if not pre.ok:
            return pre
    # refuse an empty commit attempt: nothing staged -> git fails on its own
    return _run_git(sandbox, args.path, ["commit", "-m", message])


def worktree_list(sandbox: WorkdirSandbox, args: WorktreeListArgs) -> ToolResult:
    return _run_git(sandbox, args.path, ["worktree", "list"])


def worktree_create(sandbox: WorkdirSandbox, args: WorktreeCreateArgs) -> ToolResult:
    branch = args.branch.strip()
    if not _SAFE_BRANCH.match(branch):
        return ToolResult(ok=False, payload={"error": f"invalid branch: {branch!r}"})
    base = args.base.strip() if args.base else None
    if base:
        err = _validate_ref(base)
        if err:
            return ToolResult(ok=False, payload={"error": err})
    try:
        repo = sandbox.resolve(args.path or ".")
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    if branch in {
        existing.strip()
        for existing in run_git_capture(repo, "branch", "--list", branch).splitlines()
    }:
        return ToolResult(
            ok=False, payload={"error": f"branch already exists: {branch}"}
        )
    # git worktree add -b <new-branch> <path> [<start-point>]
    # The path is where the worktree lives; base is the optional start commit.
    target = (repo / branch).resolve()
    git_args = ["worktree", "add", "-b", branch, str(target)]
    if base:
        git_args.append(base)
    result = _run_git(sandbox, args.path, git_args)
    if not result.ok:
        return result
    return ToolResult(
        ok=True,
        payload={
            "branch": branch,
            "worktree_path": str(target),
            "output": result.payload.get("output", ""),
        },
    )


def worktree_remove(sandbox: WorkdirSandbox, args: WorktreeRemoveArgs) -> ToolResult:
    target = Path(args.worktree)
    if not target.is_absolute():
        try:
            target = sandbox.resolve(args.worktree)
        except Exception as exc:
            return ToolResult(ok=False, payload={"error": str(exc)})
    try:
        repo_root = sandbox.resolve(".").resolve()
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    try:
        target.relative_to(repo_root)
    except ValueError:
        return ToolResult(
            ok=False, payload={"error": f"worktree outside repo: {target}"}
        )
    git_args = ["worktree", "remove", str(target)]
    if args.force:
        git_args.append("--force")
    return _run_git(sandbox, ".", git_args)


def run_tests(sandbox: WorkdirSandbox, args: RunTestsArgs) -> ToolResult:
    """Run the repo's detected test command and summarize pass/fail for the agent loop.

    The command always comes from repository signals. The model cannot supply an
    arbitrary shell string, and execution never goes through a shell.
    """
    if args.command and args.command.strip():
        return ToolResult(
            ok=False,
            payload={
                "error": (
                    "run_tests does not accept a custom command; it only runs the "
                    "repository's own test entrypoint"
                )
            },
        )
    timeout = min(max(1, int(args.timeout_sec or 0)), MAX_TEST_TIMEOUT_SEC)
    try:
        root = sandbox.resolve(args.path or ".")
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    command = detect_test_command(root)
    if not command:
        return ToolResult(
            ok=False,
            payload={"error": "no test command detected for this repository"},
        )
    argv = shlex.split(command, posix=False)
    if not argv:
        return ToolResult(ok=False, payload={"error": "detected test command is empty"})
    if os.name == "nt" and argv[0] in {"python", "python3"}:
        # Run the same interpreter as Spark. Calling the ambient PATH Python
        # makes an installed agent run a user's repository tests without its
        # own pytest/venv, so task commit gates can fail for the wrong reason.
        argv[0] = sys.executable
    elif (
        os.name != "nt"
        and argv[0] == "python"
        and shutil.which("python") is None
    ):
        # POSIX：`python` 别名缺失（Debian/Ubuntu 未装 python-is-python3 常见）
        # → 回退 PATH 里的 python3（解析为绝对路径，行为可预期）。
        resolved = shutil.which("python3")
        if resolved:
            argv[0] = resolved
    try:
        completed = subprocess.run(
            argv,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(
            ok=False,
            payload={
                "command": command,
                "error": f"tests timed out after {timeout}s",
            },
        )
    except OSError as exc:
        return ToolResult(ok=False, payload={"command": command, "error": str(exc)})
    output = (completed.stdout or "") + (completed.stderr or "")
    return ToolResult(
        ok=completed.returncode == 0,
        payload={
            "command": command,
            "exit_code": completed.returncode,
            "passed": completed.returncode == 0,
            "output": clip_text(output.strip(), MAX_OUTPUT),
        },
    )


def run_git_capture(cwd: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout or ""
