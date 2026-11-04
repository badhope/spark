"""平台 OS 级进程沙箱 v1 —— 三平台能力差异如实分层：

* **Linux**：bubblewrap 命名空间（见 ``tools/shell.bwrap_command``）——
  文件系统视图 + 网络命名空间，最强，既有实现不动。
* **Windows**：Job Object——``KILL_ON_JOB_CLOSE``（Spark 退出即杀光子进程，
  杜绝孤儿进程树）+ 进程内存上限；**不能**做文件系统/网络隔离（Windows
  没有用户态等价物，AppContainer 需要 IT 管理员级别的 SID 配置，超出
  本项目范围）——诚实标注，绝不让 UI 以为已断网。
* **macOS**：``sandbox-exec``——deny file-write* 全局，白名单放行 workdir
  与系统临时目录；``allow_network=False`` 时 deny network*。
  （sandbox-exec 在新版 macOS 标记 deprecated，但语义稳定、仍被 codex/
  等项目使用；不可用时返回 None 降级，与 bwrap 同一约定。）

全部路径都走"能力探测 → 不可用就降级 + 如实回报"，与 bwrap 的既有
约定一致：宁可不隔离并说清楚，也不假装隔离。
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Windows Job Object 内存上限：8 GiB——构建/测试工具链（node、链接器）
# 的合理上界；超出即整树被杀，防止失控进程吃光内存。
_WINDOWS_JOB_MEMORY_LIMIT = 8 * 1024 * 1024 * 1024

_JOBS: dict[int, Any] = {}  # pid -> job HANDLE（保活，GC 关句柄会杀掉子进程）
_JOBS_LOCK = threading.Lock()


# --------------------------------------------------------------------------- windows
def _job_object_available() -> bool:
    return sys.platform == "win32"


def _make_job_object(memory_limit: int = _WINDOWS_JOB_MEMORY_LIMIT) -> Any:
    """创建带 KILL_ON_JOB_CLOSE + 内存上限的 Job Object，返回句柄。"""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(wintypes.ULONG)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    JobObjectExtendedLimitInformation = 9

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError("CreateJobObjectW failed")
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = (
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_PROCESS_MEMORY
    )
    info.ProcessMemoryLimit = memory_limit
    if not kernel32.SetInformationJobObject(
        job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
    ):
        code = ctypes.get_last_error()  # type: ignore[attr-defined]  # 仅 win32 typeshed
        ctypes.windll.kernel32.CloseHandle(job)  # type: ignore[attr-defined]
        raise OSError(f"SetInformationJobObject failed: {code}")
    return job


def assign_windows_job(process: Any, memory_limit: int = _WINDOWS_JOB_MEMORY_LIMIT) -> bool:
    """把已启动的子进程（及其后代）纳入 Job Object。成功返回 True。"""
    if sys.platform != "win32":
        return False
    import ctypes

    try:
        job = _make_job_object(memory_limit)
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        if not kernel32.AssignProcessToJobObject(job, int(process._handle)):  # noqa: SLF001
            raise OSError(f"AssignProcessToJobObject failed: {ctypes.get_last_error()}")
        with _JOBS_LOCK:
            _JOBS[process.pid] = job
        return True
    except Exception:  # noqa: BLE001 — 沙箱失败必须降级为无沙箱，不能炸掉命令
        logger.debug("Job Object 分配失败，命令将以无 OS 沙箱方式执行", exc_info=True)
        return False


def release_windows_job(pid: int) -> None:
    """进程结束后关掉 Job 句柄（kill-on-close 此时已无害）。"""
    import ctypes

    with _JOBS_LOCK:
        job = _JOBS.pop(pid, None)
    if job:
        try:
            ctypes.windll.kernel32.CloseHandle(job)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            logger.debug("CloseHandle 失败", exc_info=True)


# --------------------------------------------------------------------------- macos
def macos_profile_text(
    workdir: str, *, allow_network: bool = True, allow_tmp: bool = True
) -> str:
    """生成 sandbox-exec 的 SchemeLisp profile 文本（可单测，不依赖 macOS）。"""
    lines = [
        "(version 1)",
        "(allow default)",
        # 全局拒写，再白名单放行——写面 = workdir + 临时目录（构建必需）
        "(deny file-write*)",
        f'(allow file-write* (subpath "{workdir}"))',
    ]
    if allow_tmp:
        tmp = tempfile.gettempdir()
        lines.append(f'(allow file-write* (subpath "{tmp}"))')
        lines.append('(allow file-write* (subpath "/private/tmp"))')
        lines.append('(allow file-write* (subpath "/private/var/folders"))')
    if not allow_network:
        lines.append("(deny network*)")
    return "\n".join(lines)


def macos_sandbox_command(
    cwd: str,
    command: str,
    workdir: str,
    *,
    allow_network: bool = True,
) -> list[str] | None:
    """构造 sandbox-exec 命令；非 macOS / sandbox-exec 不存在时返回 None。"""
    if sys.platform != "darwin":
        return None
    sb = shutil.which("sandbox-exec")
    if not sb:
        return None
    profile = macos_profile_text(workdir, allow_network=allow_network)
    fd, profile_path = tempfile.mkstemp(prefix="spark-sandbox-", suffix=".sb")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(profile)
    except OSError:
        return None
    Path(profile_path).chmod(0o644)
    return [sb, "-f", profile_path, "--", "/bin/bash", "-c", command]


def sandbox_kind_for(platform_windows: bool, darwin: bool) -> str:  # pragma: no cover - 语义标注
    """给 UI/文档的能力分层标注（Linux 由 bwrap 自己标注）。"""
    if platform_windows:
        return "job-object"
    if darwin:
        return "sandbox-exec"
    return "none"
