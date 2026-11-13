"""Per-task git memory: task-as-branch worktree isolation + aider-style auto-commit.

心智模型（"任务即分支"）：

    每个任务 = 一个独立分支 `task/<slug>` + 一个独立 worktree。

    - :func:`task_begin`    从主树切出任务分支与隔离工作区，主树工作区零触碰。
    - :func:`task_step_commit` 在 worktree 内检测变更；默认「测试通过才提交」
      （aider 范式），把可运行的中间状态落到分支上。
    - :func:`task_finish`   成功：保留 worktree 并给出合并回主分支的指引
      （本层不自动 merge/push）；失败：强制移除 worktree 并弃掉分支，
      主树零污染。

本编排层**复用** :mod:`spark.tools.gitops` 已有的 worktree / commit / run_tests
能力，不重写它们的行为，只在其上串起 per-task 自动循环。
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from spark.models import ToolResult
from spark.proc import run_bounded
from spark.sandbox import WorkdirSandbox
from spark.tools import gitops

# 与 gitops._SAFE_BRANCH 对齐的分支名校验，保持一致的安全约束。
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,100}$")


class TaskBeginArgs(BaseModel):
    """Open a per-task worktree. ``task`` is a free-form id, slugged to a branch."""

    task: str
    path: str = "."
    base: str | None = None


class TaskStepCommitArgs(BaseModel):
    """Auto-commit one step inside the task worktree, gated by tests."""

    worktree: str
    step: str
    run_tests: bool = True
    timeout_sec: int = 600


class TaskFinishArgs(BaseModel):
    """Close a task: keep on success, force-abandon on failure."""

    worktree: str
    branch: str
    success: bool = True
    # 失败时是否一并删除任务分支。默认 None -> True（整体遗弃）。
    abandon_branch: bool | None = None


def slug_branch(task: str) -> str:
    """Turn a free-form task id into a safe branch name under ``task/``.

    Raises ``ValueError`` when the id collapses to nothing usable.
    """
    t = re.sub(r"[^A-Za-z0-9._/\-]+", "-", task.strip()).strip(".-/")
    if not t:
        raise ValueError("task identifier is empty after slugify")
    if not re.match(r"^[A-Za-z0-9]", t):
        t = "t" + t
    if not t.startswith("task/"):
        t = "task/" + t
    return t[:100]


def _git(cwd: Path, *args: str, timeout: int = 60) -> tuple[int, str, str]:
    """Run git in an arbitrary directory; return ``(returncode, stdout, stderr)``.

    The directory is one we created ourselves through the sandbox-validated
    worktree API, so running read/lightweight git against it is safe here.
    """
    try:
        completed = run_bounded(
            ["git", *args],
            cwd=cwd,
            timeout=timeout,
        )
    except OSError as exc:  # git missing etc.
        return 127, "", str(exc)
    if completed.timed_out:
        return 124, "", f"git {' '.join(args)} timed out"
    return completed.returncode, (completed.stdout or ""), (completed.stderr or "")


def _resolve_worktree(sandbox: WorkdirSandbox, raw: str) -> Path:
    p = Path(raw)
    if not p.is_absolute():
        p = sandbox.resolve(raw)
    p = p.resolve()
    # In a linked worktree `.git` is a file; in the main checkout it is a dir.
    if not p.is_dir() or not (p / ".git").exists():
        raise ValueError(f"not a git worktree: {raw}")
    return p


def task_begin(sandbox: WorkdirSandbox, args: TaskBeginArgs) -> ToolResult:
    """Create the per-task branch + isolated worktree off the main tree."""
    try:
        branch = slug_branch(args.task)
    except ValueError as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    if not _BRANCH_RE.match(branch):
        return ToolResult(ok=False, payload={"error": f"invalid branch: {branch!r}"})

    created = gitops.worktree_create(
        sandbox,
        gitops.WorktreeCreateArgs(branch=branch, path=args.path or ".", base=args.base),
    )
    if not created.ok:
        return created
    wt_path = Path(str(created.payload["worktree_path"]))
    return ToolResult(
        ok=True,
        payload={
            "branch": branch,
            "worktree_path": str(wt_path),
            "message": f"task branch {branch} ready at {wt_path}",
        },
    )


def task_step_commit(sandbox: WorkdirSandbox, args: TaskStepCommitArgs) -> ToolResult:
    """Detect worktree changes, optionally gate on green tests, then auto-commit.

    Policy (aider-style "tests green before commit"):

    - No changes  -> no-op, ``committed=False``.
    - ``run_tests=True`` and a test entrypoint is detected -> run it. On failure
      the working tree is left **uncommitted** for the next repair loop; the
      caller gets the test output.
    - ``run_tests=True`` but no test entrypoint exists -> cannot gate, commit
      anyway and label the payload ``test_gate="not-detected"``.
    - ``run_tests=False`` -> commit immediately (step-level checkpoint).
    """
    step = args.step.strip()
    if not step:
        return ToolResult(ok=False, payload={"error": "step description required"})
    try:
        wt = _resolve_worktree(sandbox, args.worktree)
    except ValueError as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})

    # 1) Detect changes in the worktree (porcelain ignores the branch header).
    rc, out, _ = _git(wt, "status", "--porcelain")
    if rc != 0:
        return ToolResult(ok=False, payload={"error": "git status failed in worktree"})
    changes = [ln for ln in out.splitlines() if ln.strip()]
    if not changes:
        return ToolResult(
            ok=True,
            payload={
                "committed": False,
                "reason": "no changes",
                "worktree": str(wt),
            },
        )

    # 2) Optional test gate, reusing gitops.run_tests (no custom command).
    test_gate = "disabled"
    if args.run_tests:
        tr = gitops.run_tests(
            sandbox,
            gitops.RunTestsArgs(path=str(wt), timeout_sec=args.timeout_sec),
        )
        if tr.ok:
            test_gate = "passed"
        elif "no test command" in str(tr.payload.get("error", "")):
            test_gate = "not-detected"  # cannot gate; commit anyway
        else:
            return ToolResult(
                ok=False,
                payload={
                    "committed": False,
                    "reason": "tests failed; working tree left uncommitted",
                    "test_gate": "failed",
                    "tests": tr.payload,
                },
            )

    # 3) Stage-all and commit with the step as the subject.
    body = "Auto-committed by spark git-memory"
    body += " after green tests" if test_gate == "passed" else ""
    message = f"{step}\n\n({body})"
    commit = gitops.git_commit(
        sandbox,
        gitops.GitCommitArgs(message=message, path=str(wt), add_all=True),
    )
    if not commit.ok:
        return commit

    rc, head, _ = _git(wt, "rev-parse", "HEAD")
    rc2, branch_out, _ = _git(wt, "branch", "--show-current")
    return ToolResult(
        ok=True,
        payload={
            "committed": True,
            "worktree": str(wt),
            "branch": branch_out.strip() if rc2 == 0 else None,
            "commit": head.strip() if rc == 0 else None,
            "test_gate": test_gate,
            "files_changed": len(changes),
        },
    )


def task_finish(sandbox: WorkdirSandbox, args: TaskFinishArgs) -> ToolResult:
    """Close a task.

    Success -> keep the worktree and return merge-back guidance (this layer
    never auto-merges or pushes). Failure -> force-remove the worktree and, by
    default, delete the abandoned branch, so the main tree is untouched.
    """
    try:
        repo_root = sandbox.resolve(".").resolve()
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})

    if args.success:
        return ToolResult(
            ok=True,
            payload={
                "kept": True,
                "branch": args.branch,
                "worktree": args.worktree,
                "merge_hint": (
                    f"在主树合并：git -C {repo_root} merge {args.branch}；"
                    f"合并验收后再 git -C {repo_root} worktree remove "
                    f"<worktree>。本层不自动 merge/push。"
                ),
            },
        )

    # Failure: abandon. Capture the branch tip before destroying it.
    rc, tip, _ = _git(repo_root, "rev-parse", "--verify", args.branch)
    discarded_tip = tip.strip() if rc == 0 else None

    removed = gitops.worktree_remove(
        sandbox, gitops.WorktreeRemoveArgs(worktree=args.worktree, force=True)
    )
    if not removed.ok:
        # Be tolerant if the worktree was already gone; still try to drop branch.
        gone = "not a git worktree" in str(removed.payload) or (
            "does not have" in str(removed.payload)
        )
        if not gone:
            return ToolResult(
                ok=False,
                payload={
                    "error": "worktree remove failed",
                    "detail": removed.payload,
                },
            )

    abandon = args.abandon_branch if args.abandon_branch is not None else True
    branch_deleted: bool | None = None
    if abandon:
        rc, _, err = _git(repo_root, "branch", "-D", args.branch)
        branch_deleted = rc == 0
        if rc != 0 and "not found" not in err and "not a valid" not in err:
            return ToolResult(
                ok=False,
                payload={
                    "error": "worktree removed but branch delete failed",
                    "branch": args.branch,
                    "stderr": err.strip(),
                },
            )
        branch_deleted = True if "not found" in err or "not a valid" in err else (branch_deleted)

    return ToolResult(
        ok=True,
        payload={
            "kept": False,
            "abandoned": True,
            "branch": args.branch,
            "branch_deleted": branch_deleted,
            "discarded_tip": discarded_tip,
            "message": "task abandoned; worktree removed and branch dropped",
        },
    )
