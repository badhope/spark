"""P1：诊断反馈工具——编辑后把 lint/type 诊断喂回上下文（SWE-agent ACI 范式）。

不做完整 LSP 客户端（语言服务器依赖因环境而异），先内置两类零配置诊断源：
- Python：``ruff check --output-format json``（项目自身即依赖 ruff）
- TS/JS：``tsc --noEmit``（检测到 workdir 有 tsconfig 且 PATH 上有全局 tsc 时才跑；
  刻意不用 npx，避免执行仓库自带的 node_modules/.bin/tsc）

设计：只读工具；路径默认整个 workdir；输出结构化（file/line/col/code/message），
由 AgentLoop 在编辑后主动调用或在提示词中引导模型调用，形成"改完即诊断"闭环。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from spark.models import ToolResult
from spark.proc import run_bounded
from spark.sandbox import WorkdirSandbox

MAX_TIMEOUT = 60
MAX_DIAGNOSTICS = 60


class DiagnosticsArgs(BaseModel):
    paths: list[str] = Field(default_factory=list, description="限定检查的相对路径，留空=整个 workdir")
    python: bool = Field(default=True, description="运行 ruff（python）诊断")
    typescript: bool = Field(default=True, description="运行 tsc（若可用）诊断")


def _clip_extras(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return items[:MAX_DIAGNOSTICS]


def _safe_targets(sandbox: WorkdirSandbox, paths: list[str]) -> list[str] | None:
    """Resolve tool-supplied paths to workdir-relative targets, or None if unsafe.

    ``diagnostics`` is classified as read-only and therefore runs without an
    approval prompt, so a target must never be able to change ruff's behaviour.
    A path such as ``--fix`` would be parsed as a flag and silently rewrite the
    user's files, so any target starting with ``-`` is rejected outright.
    """
    root = sandbox.root
    resolved: list[str] = []
    for raw in paths:
        text = str(raw).strip()
        if not text or text.startswith("-"):
            return None
        try:
            path = sandbox.resolve(text)
            resolved.append(str(path.relative_to(root)))
        except Exception:
            return None
    return resolved


def _ruff_diagnostics(root: Path, targets: list[str]) -> list[dict[str, Any]]:
    # PATH 里的 ruff 优先；否则用当前解释器 -m ruff（同 venv 安装时最稳）
    ruff = shutil.which("ruff")
    # `-P` keeps the working directory off sys.path: without it, `python -m ruff`
    # inside a cloned repo can import the repo's own `ruff/` package and execute
    # it — while diagnostics is classified as read-only (no approval prompt).
    cmd = (
        [ruff, "check"]
        if ruff
        else [sys.executable, "-P", "-m", "ruff", "check"]
    )
    # --no-fix: the tool is read-only; -- : terminate option parsing so a target
    # can never be interpreted as a flag.
    cmd += ["--output-format", "json", "--no-cache", "--no-fix", "--", *(targets or ["."])]
    try:
        proc = run_bounded(cmd, cwd=root, timeout=MAX_TIMEOUT)
    except OSError:
        return []
    if proc.timed_out:
        return []
    try:
        raw = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return []
    out: list[dict[str, Any]] = []
    for msg in raw:  # ruff JSON 为扁平结构：每项即一条诊断
        out.append(
            {
                "file": msg.get("filename", ""),
                "line": msg.get("location", {}).get("row"),
                "col": msg.get("location", {}).get("column"),
                "code": msg.get("code"),
                "message": msg.get("message", "")[:200],
                "severity": msg.get("severity"),
                "source": "ruff",
            }
        )
    return out


def sys_exec_prefix() -> str:
    import sys

    return sys.exec_prefix


def _tsc_executable() -> str | None:
    """Resolve a tsc that the *project* cannot substitute.

    ``npx --no-install tsc`` — and any PATH lookup that starts with the repo —
    would run ``node_modules/.bin/tsc`` from the cloned repository. This tool is
    classified as read-only (no approval prompt), so executing a repo-supplied
    binary here would be an unapproved code-execution path. Only a global tsc
    is accepted; projects that install tsc locally simply do not get JS
    diagnostics (they can still run ``npm test`` behind the approval gate).
    """
    for name in ("tsc", "tsc.cmd", "tsc.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _tsc_diagnostics(root: Path) -> list[dict[str, Any]]:
    tsc = _tsc_executable()
    if not (root / "tsconfig.json").exists() or not tsc:
        return []
    try:
        proc = run_bounded(
            [tsc, "--noEmit", "--pretty", "false"],
            cwd=root,
            timeout=MAX_TIMEOUT,
        )
    except OSError:
        return []
    if proc.timed_out:
        return []
    out: list[dict[str, Any]] = []
    # tsc 人类可读输出：file(line,col): error TSxxxx: message
    for line in (proc.stdout or "").splitlines():
        if " error TS" not in line and " error " not in line:
            continue
        head, _, msg = line.partition(": error ")
        if "(" in head and head.endswith(")"):
            f, _, pos = head[:-1].rpartition("(")
            row, _, col = pos.partition(",")
        else:
            f, pos = head.rsplit(":", 1) if head.count(":") >= 1 else (head, "")
            row, _, col = pos.partition(":")
        code = ""
        if msg.startswith("TS"):
            code, _, msg = msg.partition(": ")
        out.append(
            {
                "file": f.strip(),
                "line": int(row) if row.strip().isdigit() else None,
                "col": int(col) if col.strip().isdigit() else None,
                "code": code.strip(),
                "message": msg.strip()[:200],
                "source": "tsc",
            }
        )
    return out


def diagnostics_tool(
    sandbox: WorkdirSandbox, args: DiagnosticsArgs
) -> ToolResult:
    root = sandbox.root
    if args.paths:
        targets = _safe_targets(sandbox, args.paths)
        if targets is None:
            return ToolResult(
                ok=False,
                payload={
                    "error": (
                        "invalid diagnostics path: paths must be workdir-relative "
                        "and may not start with '-'"
                    ),
                    "paths": args.paths,
                },
            )
    else:
        targets = []
    diagnostics: list[dict[str, Any]] = []
    if args.python:
        diagnostics += _ruff_diagnostics(root, targets)
    if args.typescript and not targets:
        diagnostics += _tsc_diagnostics(root)
    diagnostics = _clip_extras(diagnostics)
    return ToolResult(
        ok=True,
        payload={
            "diagnostics": diagnostics,
            "count": len(diagnostics),
            "hint": (
                "修复以上诊断后可再次运行 diagnostics 验证；无诊断即通过。"
                if diagnostics
                else "当前无诊断问题。"
            ),
        },
    )


def diagnostics_diff_hint(before: int, after: int) -> str:
    """编辑前后诊断数的提示文案（供 loop/前端复用）。"""
    if after == 0:
        return "diagnostics clean"
    if after < before:
        return f"diagnostics improved: {before} -> {after}"
    if after > before:
        return f"diagnostics regressed: {before} -> {after}"
    return f"diagnostics unchanged: {after}"
