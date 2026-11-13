"""写后自动格式化（对应 opencode 的 formatter、codex 的 apply_patch 后处理）。

设计取舍：

* **默认关闭**。格式化会改用户文件，是"有副作用"的动作；本项目定位是本地
  可控，任何隐式改写都该由用户显式打开（`agent.format_on_write = true`）。
* **只调用项目里已存在的工具链**。不内联一个格式化器，也不装新依赖：
  Python 用 ruff（本项目自身依赖），TS/JS 用项目内的 prettier（若已安装）。
  找不到工具就静默跳过——绝不能因为"没装 prettier"让写文件失败。
* **永不抛错**。格式化失败时保留未格式化的文件，并在返回值里如实说明，
  让模型和用户都知道发生了什么，而不是假装成功。
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

from spark.proc import run_bounded

logger = logging.getLogger(__name__)

#: 每种扩展名对应的格式化命令（工具名, 参数）。命令以文件路径结尾。
_FORMATTERS: dict[str, tuple[str, list[str]]] = {
    ".py": ("ruff", ["format", "--quiet"]),
    ".pyi": ("ruff", ["format", "--quiet"]),
    ".ts": ("prettier", ["--write", "--log-level", "silent"]),
    ".tsx": ("prettier", ["--write", "--log-level", "silent"]),
    ".js": ("prettier", ["--write", "--log-level", "silent"]),
    ".jsx": ("prettier", ["--write", "--log-level", "silent"]),
    ".json": ("prettier", ["--write", "--log-level", "silent"]),
    ".css": ("prettier", ["--write", "--log-level", "silent"]),
    ".md": ("prettier", ["--write", "--log-level", "silent"]),
}

_FORMAT_TIMEOUT_SEC = 20


def _resolve_tool(name: str, root: Path) -> str | None:
    """找一个可用的格式化器；优先项目内的，其次 PATH。

    `ruff` 走 `python -m ruff` 更稳（本项目装在 venv 里，不一定在 PATH）。
    刻意不用 `npx`：那会联网下载或执行仓库里的任意 bin，等于给写文件路径
    开了一条执行通道。
    """
    if name == "ruff":
        return f"{sys.executable} -m ruff" if _has_module("ruff") else None
    local = root / "node_modules" / ".bin" / name
    if local.exists():
        return str(local)
    return shutil.which(name)


def _has_module(module: str) -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def format_file(path: Path, root: Path, *, enabled: bool) -> dict:
    """格式化 *path*；返回 `{"formatted": bool, "tool": str, "error": str}`。

    永不抛错——写文件的成功与否不该由格式化器决定。
    """
    result: dict = {"formatted": False, "tool": "", "error": ""}
    if not enabled:
        return result
    formatter = _FORMATTERS.get(path.suffix.lower())
    if formatter is None:
        return result
    name, args = formatter
    command = _resolve_tool(name, root)
    if not command:
        result["error"] = f"{name} not available"
        return result
    try:
        parts = command.split() if name == "ruff" else [command]
        completed = run_bounded(
            [*parts, *args, str(path)],
            cwd=root,
            timeout=_FORMAT_TIMEOUT_SEC,
        )
        if completed.timed_out:
            result["error"] = f"{name} timed out"
            return result
        if completed.returncode == 0:
            result["formatted"] = True
            result["tool"] = name
        else:
            detail = (completed.stderr or completed.stdout or "").strip()
            result["error"] = detail[:300] or f"{name} exited {completed.returncode}"
            logger.debug("formatter %s failed on %s: %s", name, path, detail)
    except OSError as exc:
        # 工具崩溃、权限问题：都只记录，不影响已写入的文件。
        result["error"] = str(exc)[:300]
        logger.debug("formatter %s raised on %s", name, path, exc_info=True)
    return result
