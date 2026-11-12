"""跨平台适配层：Windows 与 Linux/macOS 的行为差异集中在此。

为什么需要集中：同一份 agent 在两套平台上被真实使用（Windows 桌面 EXE +
Linux 服务器 / 手机浏览器访问），而差异不止 subprocess 标志：

* **子进程**：Windows 没有 POSIX 进程组，整树终止要靠 ``taskkill /T``；
  POSIX 用 ``start_new_session`` + ``killpg``。
* **默认 shell**：Windows 是 ``cmd.exe``（``shell=True`` 走 cmd），POSIX 是
  ``/bin/sh``；``&&`` / 引号 / 反斜杠的行为因此不同。
* **可执行名**：``python`` vs ``python3``；``pip`` vs ``pip3``。
* **PTY**：Windows 无 ``ptyprocess``，内置终端只能给出明确的不可用提示。
* **路径与保留名**：``CON``/``PRN``/``AUX``/``NUL`` 等设备名不可作为文件名；
  大小写不敏感的文件系统（NTFS 默认）。
* **编码**：Windows 控制台常见 GBK；文件读写必须显式 UTF-8。
* **控制台窗口**：后台任务在 Windows 上会弹黑框，需要 ``CREATE_NO_WINDOW``。

本模块只放"平台事实"，不放业务判断。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

#: 不弹出控制台窗口。非 Windows 为 0（无副作用）。
CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: 让子进程独立成组，便于整组终止。非 Windows 为 0（POSIX 侧用 start_new_session）。
CREATE_NEW_PROCESS_GROUP: int = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

#: 完全脱离父进程控制台。非 Windows 为 0。
DETACHED_PROCESS: int = getattr(subprocess, "DETACHED_PROCESS", 0)

#: Windows 保留设备名（大小写不敏感；带扩展名同样被占用）。
WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

_RESERVED_CODE_CHARS = sorted(
    {ord(f"{chr(c)}") for c in range(ord("A"), ord("Z") + 1)}
    | {ord(f"{chr(c)}") for c in range(ord("a"), ord("z") + 1)}
)


def shell_executable() -> str | None:
    """The shell ``shell=True`` will use, for prompt text and diagnostics.

    Returns ``None`` when the platform default should be kept.
    """
    if IS_WINDOWS:
        return os.environ.get("COMSPEC") or "cmd.exe"
    return os.environ.get("SHELL") or "/bin/sh"


def is_reserved_filename(name: str) -> bool:
    """True when *name* would be unusable on Windows (``AUX.py``, ``nul.txt``)."""
    stem = name.split(".", 1)[0].strip().lower()
    return stem in WINDOWS_RESERVED_NAMES


def which_python() -> str | None:
    """Resolve the interpreter name that actually exists on PATH."""
    for candidate in ("python", "python3", "py"):
        found = shutil.which(candidate)
        if found:
            return candidate
    return None


def which_pip() -> str | None:
    for candidate in ("pip", "pip3"):
        if shutil.which(candidate):
            return candidate
    return None


def popen_kwargs(*, new_group: bool) -> dict:
    """Platform-correct Popen kwargs for process-tree control.

    ``new_group=True`` makes the child a session/group leader so the caller can
    kill the whole tree (``taskkill /T`` on Windows, ``killpg`` on POSIX).
    """
    if IS_WINDOWS:
        flags = CREATE_NO_WINDOW | (CREATE_NEW_PROCESS_GROUP if new_group else 0)
        return {"creationflags": flags} if flags else {}
    return {"start_new_session": True} if new_group else {}


def _kill_with_psutil(pid: int) -> bool:
    """Terminate a process tree through psutil when it is installed."""
    try:
        import psutil
    except ImportError:  # pragma: no cover - optional dependency
        return False
    try:
        parent = psutil.Process(pid)
        procs = parent.children(recursive=True)
        procs.append(parent)
    except Exception:  # pragma: no cover - process already gone
        return False
    for proc in procs:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001 - best effort
            continue
    try:
        _, alive = psutil.wait_procs(procs, timeout=3)
    except Exception:  # pragma: no cover - psutil platform quirk
        alive = []
    for proc in alive:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001 - best effort
            continue
    return True


def kill_process_tree(pid: int, process: subprocess.Popen | None = None) -> None:
    """Terminate *pid* and its descendants; never raises.

    psutil is the preferred implementation (cross-platform tree walk, graceful
    terminate then kill). The taskkill/killpg paths remain as a fallback for a
    minimal install where psutil is absent.
    """
    if _kill_with_psutil(pid):
        if process is not None:
            try:
                process.wait(timeout=2)
            except Exception:  # noqa: BLE001 - already reaped
                pass
        return
    if IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
                creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        return
    for signal_name in ("SIGTERM", "SIGKILL"):
        try:
            import signal

            # killpg/getpgid exist only on POSIX; getattr keeps mypy honest on
            # Windows typeshed while the call stays unconditional at runtime.
            killpg = getattr(os, "killpg", None)
            getpgid = getattr(os, "getpgid", None)
            if killpg is None or getpgid is None:
                break
            killpg(getpgid(pid), getattr(signal, signal_name))
            return
        except (OSError, AttributeError):
            continue
    if process is not None and process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def default_home_dir() -> Path:
    """``$SPARK_HOME`` / ``~/.spark`` — resolved the same way on both platforms."""
    from spark.config import default_home

    return default_home()


def terminal_size() -> tuple[int, int]:
    """(columns, rows) with a sane fallback when there is no TTY (CI, service)."""
    try:
        size = os.get_terminal_size()
        return max(20, size.columns), max(5, size.lines)
    except OSError:
        return 80, 24


def pty_supported() -> bool:
    """True when a real PTY backend is importable on this platform."""
    if IS_WINDOWS:
        try:
            import winpty  # noqa: F401

            return True
        except ImportError:
            return False
    try:
        import ptyprocess  # noqa: F401

        return True
    except ImportError:
        return False


def normalize_newlines(text: str) -> str:
    """Fold CRLF/CR to LF so edits and diffs compare identically everywhere."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


__all__ = [
    "IS_WINDOWS",
    "IS_MACOS",
    "IS_LINUX",
    "CREATE_NO_WINDOW",
    "CREATE_NEW_PROCESS_GROUP",
    "DETACHED_PROCESS",
    "WINDOWS_RESERVED_NAMES",
    "shell_executable",
    "is_reserved_filename",
    "which_python",
    "which_pip",
    "popen_kwargs",
    "kill_process_tree",
    "default_home_dir",
    "terminal_size",
    "pty_supported",
    "normalize_newlines",
]
