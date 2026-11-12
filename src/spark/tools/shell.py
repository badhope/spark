from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

logger = logging.getLogger(__name__)

from pydantic import BaseModel

from spark.errors import PathEscapeError
from spark.models import ToolResult
from spark.platform_compat import kill_process_tree, popen_kwargs
from spark.sandbox import SandboxPolicyError, WorkdirSandbox
from spark.tools._common import clip_text

MAX_SHELL_OUTPUT_BYTES = 4 * 1024 * 1024
_READ_CHUNK_BYTES = 8192

# ---------------------------------------------------------------------------
# OS 级沙箱（bubblewrap 探测 + 降级）
# Claude Code sandboxing 范式：workspace 模式下命令跑在命名空间隔离内——
# 系统路径只读、仅 workdir 可写。bwrap 未安装时静默降级到策略检查
# （check_shell 仍是第一道防线，bwrap 是第二道纵深）。
# ---------------------------------------------------------------------------
_BWRAP_CACHE: dict[str, str | bool | None] = {"path": None, "probed": False}


def _bwrap_probe_runnable(path: str) -> bool:
    """跑一次最小命名空间自检，验证 bwrap 不仅*存在*而且*可用*。

    ``which("bwrap")`` 命中不等于沙箱能建起来：非特权容器（seccomp/无
    CAP_SYS_ADMIN）里 ``--proc`` 挂载会报 ``Operation not permitted``，
    此时若照常下发隔离命令，workspace 模式下**每条** shell 都会以
    exit=1 失败——不是"没隔离"，而是"全挂"。与本模块的既有约定一致
    （"能力探测 -> 不可用就降级 + 如实回报"），探测失败即返回 False，
    调用方降级为纯策略检查（check_shell 仍是第一道防线）。

    探测用最小可信组合（userns + proc + dev）：它失败时完整命令必然
    失败；它通过而完整命令因个别绑定路径缺失失败的极端情况，只会多
    降级、不会假隔离。
    """
    try:
        completed = subprocess.run(
            [
                path,
                "--unshare-all",
                "--ro-bind", "/", "/",
                "--proc", "/proc",
                "--dev", "/dev",
                "/bin/sh", "-c", "exit 0",
            ],
            capture_output=True,
            timeout=15,
        )
        return completed.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _bwrap_path() -> str | None:
    if _BWRAP_CACHE["probed"]:
        cached = _BWRAP_CACHE["path"]
        return cached if isinstance(cached, str) else None
    path = shutil.which("bwrap")
    if path is not None and not _bwrap_probe_runnable(path):
        logger.info(
            "bwrap 已安装但当前环境无法建立沙箱命名空间（常见于非特权容器），降级为策略检查"
        )
        path = None
    _BWRAP_CACHE["path"] = path
    _BWRAP_CACHE["probed"] = True
    return path


def bwrap_command(
    cwd: str,
    command: str,
    workdir: str,
    home: str | None = None,
    *,
    allow_network: bool = True,
) -> list[str] | None:
    """构造 bubblewrap 命名空间隔离命令；环境不支持时返回 None（调用方降级）。

    仅保护文件系统视图：系统路径只读、workdir 与 HOME 读写、/tmp 读写、
    proc/dev 挂载。

    ``allow_network=False`` 时**不**传 ``--share-net``：``--unshare-all`` 会
    建一个空的网络命名空间，进程只剩 loopback。这正是 codex
    ``sandbox_workspace_write.network_access = false`` 的语义——离线跑构建
    与测试，防止仓库里的脚本外连。

    能力边界要如实：bwrap 不可用时（Windows / 未安装）本函数返回 None，
    网络开关不会生效，调用方必须把这一点回报给用户，而不是假装已断网。
    """
    if os.name == "nt" or not _bwrap_path():
        return None
    home_dir = home or os.path.expanduser("~")
    bindings = [
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",
        "--ro-bind", "/etc", "/etc",
        "--proc", "/proc",
        "--dev", "/dev",
        "--bind", workdir, workdir,
    ]
    if home_dir and os.path.isdir(home_dir):
        bindings += ["--bind", home_dir, home_dir]
    if os.path.isdir("/tmp"):
        bindings += ["--bind", "/tmp", "/tmp"]
    net_args = ["--share-net"] if allow_network else []
    return [
        _bwrap_path(),  # type: ignore[list-item]
        "--unshare-all",
        # P0：--unshare-all 会连网络命名空间一起隔离。默认保留网络，否则
        # workspace 模式下 pip/npm 等直接失败；allow_network=False 时才刻意
        # 让它落在空网络命名空间里。
        *net_args,
        "--die-with-parent",
        *bindings,
        "--chdir", cwd,
        "/bin/sh", "-c", command,
    ]


class RunShellArgs(BaseModel):
    command: str
    cwd: str | None = None


def run_shell(
    sandbox: WorkdirSandbox, args: RunShellArgs, timeout_sec: int, max_output_chars: int
) -> ToolResult:
    if not sandbox.shell_allowed:
        return ToolResult(
            ok=False,
            payload={"error": "run_shell is disabled: sandbox-only access mode"},
        )
    try:
        cwd = sandbox.resolve(args.cwd or ".")
    except (PathEscapeError, SandboxPolicyError) as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    if not cwd.exists() or not cwd.is_dir():
        return ToolResult(ok=False, payload={"error": f"cwd not found: {args.cwd}"})
    # Check after resolving cwd so relative paths are judged from where the
    # process will actually run (`../.git/hooks/x` from a subdirectory).
    reason = sandbox.check_shell(args.command, cwd=cwd)
    if reason:
        return ToolResult(ok=False, payload={"error": reason, "command": args.command})
    output_limit = max(0, int(max_output_chars))
    byte_limit = min(max(0, int(MAX_SHELL_OUTPUT_BYTES)), output_limit * 4)
    process_kwargs: dict = {
        "shell": True,
        "cwd": str(cwd),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    process_kwargs.update(popen_kwargs(new_group=True))

    # OS 级沙箱：workspace 模式且平台能力可用时走 OS 级隔离；
    # 不可用/更高权限档位时维持原路径（check_shell 仍在前面把关）。
    # Linux=bwrap 命名空间（最强）；Windows=Job Object（进程树硬清理+内存上限，
    # 不做文件系统/网络隔离——能力边界如实标注在 payload.sandbox）；macOS=
    # sandbox-exec（写面收敛 workdir + 临时目录，可断网）。
    network_blocked = False
    network_note = ""
    sandbox_kind = ""
    cfg = getattr(sandbox, "cfg", None)
    allow_network = bool(getattr(getattr(cfg, "agent", None), "network_access", True))
    in_workspace_mode = (
        not sandbox.full_access
        and not sandbox.unrestricted
        and sandbox.sandbox_mode == "workspace"
    )
    if os.name != "nt" and in_workspace_mode:
        isolated = bwrap_command(
            str(cwd), args.command, str(sandbox.root), allow_network=allow_network
        )
        if isolated is not None:
            process_kwargs["shell"] = False
            command_to_run: list[str] = isolated
            network_blocked = not allow_network
            sandbox_kind = "bwrap"
        elif sys.platform == "darwin":
            from spark.tools.os_sandbox import macos_sandbox_command

            mac = macos_sandbox_command(
                str(cwd), args.command, str(sandbox.root), allow_network=allow_network
            )
            if mac is not None:
                process_kwargs["shell"] = False
                command_to_run = mac
                network_blocked = not allow_network
                sandbox_kind = "sandbox-exec"
            else:
                command_to_run = args.command  # type: ignore[assignment]
        else:
            command_to_run = args.command  # type: ignore[assignment]
    elif os.name == "nt" and in_workspace_mode:
        # Windows：Job Object 在 Popen 之后挂（见下方 assign），不做命令替换。
        command_to_run = args.command  # type: ignore[assignment]
        sandbox_kind = "job-object"
    else:
        command_to_run = args.command  # type: ignore[assignment]
        sandbox_kind = ""

    if not allow_network and not network_blocked:
        # 配置要求断网但当前平台无法真隔离：必须如实告知，不能假装已断网。
        network_note = (
            "network_access=false was requested but cannot be enforced here "
            "(bubblewrap unavailable on this platform / access mode); "
            "the command may still reach the network"
        )

    process = subprocess.Popen(command_to_run, **process_kwargs)  # type: ignore[arg-type]
    if sandbox_kind == "job-object":
        from spark.tools.os_sandbox import assign_windows_job

        if not assign_windows_job(process):
            sandbox_kind = ""  # 分配失败降级为无 OS 沙箱（check_shell 仍在把关）
    stdout_result: list[tuple[bytes, bool]] = []
    stderr_result: list[tuple[bytes, bool]] = []

    def read_stdout() -> None:
        try:
            stdout_result.append(_read_bounded(process.stdout, byte_limit))
        except (OSError, ValueError):
            stdout_result.append((b"", False))

    def read_stderr() -> None:
        try:
            stderr_result.append(_read_bounded(process.stderr, byte_limit))
        except (OSError, ValueError):
            stderr_result.append((b"", False))

    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_tree(process)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
    for thread in (stdout_thread, stderr_thread):
        thread.join(timeout=1)
    if sandbox_kind == "job-object":
        from spark.tools.os_sandbox import release_windows_job

        release_windows_job(process.pid)
    if timed_out or stdout_thread.is_alive() or stderr_thread.is_alive():
        _close_process_streams(process)
    if timed_out:
        return ToolResult(
            ok=False, payload={"error": "timeout", "command": args.command}
        )
    stdout_bytes, stdout_was_truncated = (
        stdout_result[0] if stdout_result else (b"", False)
    )
    stderr_bytes, stderr_was_truncated = (
        stderr_result[0] if stderr_result else (b"", False)
    )
    stdout_text = stdout_bytes.decode("utf-8", errors="replace")
    stderr_text = stderr_bytes.decode("utf-8", errors="replace")
    stdout_was_clipped = len(stdout_text) > output_limit
    stderr_was_clipped = len(stderr_text) > output_limit
    stdout = clip_text(stdout_text, output_limit)
    stderr = clip_text(stderr_text, output_limit)
    stdout_truncated = stdout_was_truncated or stdout_was_clipped
    stderr_truncated = stderr_was_truncated or stderr_was_clipped
    payload: dict[str, Any] = {
        "command": args.command,
        "exit_code": process.returncode,
        "sandbox": {"kind": sandbox_kind, "network_blocked": network_blocked},
        "stdout": stdout,
        "stderr": stderr,
        "truncated": stdout_truncated or stderr_truncated,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }
    if network_blocked:
        payload["network"] = "blocked"
    if network_note:
        payload["network_warning"] = network_note
    return ToolResult(ok=process.returncode == 0, payload=payload)


def _read_bounded(stream, limit: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    remaining = max(0, limit)
    truncated = False
    while True:
        chunk = stream.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        if len(chunk) > remaining:
            truncated = True
        if remaining:
            kept = chunk[:remaining]
            chunks.append(kept)
            remaining -= len(kept)
    return b"".join(chunks), truncated


def _close_process_streams(process: subprocess.Popen) -> None:
    for stream in (process.stdout, process.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            pass


def _kill_process_tree(process: subprocess.Popen) -> None:
    # Platform split (taskkill /T vs killpg) lives in platform_compat.
    kill_process_tree(process.pid, process)


_ENV_PROBE_CACHE: dict[str, dict] = {}


def detect_env(workdir) -> dict:
    """Probe the default runtime environment so it can be stated in the system prompt.

    各运行时探测并行执行：Windows 上每个子进程启动约 2-3 秒，串行 6 个会让
    每个进程的首次 system prompt 构建多付 ~12 秒。

    Cached per process: the result depends only on PATH, which does not change
    while the process runs. Without the cache, every ``build_web_server()`` (each
    web test fixture) respawned six subprocesses — that alone pushed the CI
    matrix from 3 to 20+ minutes on Windows runners.
    """
    cached = _ENV_PROBE_CACHE.get("value")
    if cached is not None:
        return dict(cached)
    probes = {
        "python3": ["python3", "--version"],
        "python": ["python", "--version"],
        "pip": ["pip3", "--version"],
        "node": ["node", "--version"],
        "npm": ["npm", "--version"],
        "git": ["git", "--version"],
    }

    def _probe(item: tuple[str, list[str]]) -> tuple[str, str] | None:
        name, cmd = item
        exe = shutil.which(cmd[0])
        if exe is None:
            # Spark is running under *some* interpreter, even when it was
            # launched by absolute path (venv, embedded distribution, CI). A
            # PATH-only probe reported "no python" and made selfheal claim the
            # runtime was broken. Fall back to the current interpreter for the
            # two probes that can answer through it.
            if name == "python":
                cmd = [sys.executable, "--version"]
            elif name == "pip":
                cmd = [sys.executable, "-m", "pip", "--version"]
            else:
                return None
        try:
            completed = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5, cwd=workdir
            )
            version = (completed.stdout or completed.stderr).strip().splitlines()
            return name, (version[0][:60] if version else "installed")
        except Exception:
            # 版本命令执行失败时降级为 "installed"（仅展示用）
            logger.debug("检测 %s 版本失败", name, exc_info=True)
            return name, "installed"

    result: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(probes), thread_name_prefix="spark-envprobe") as pool:
        for outcome in pool.map(_probe, probes.items()):
            if outcome is not None:
                result[outcome[0]] = outcome[1]
    result["path"] = str(workdir)
    _ENV_PROBE_CACHE["value"] = dict(result)
    return result


def _clip(text: str, limit: int) -> str:
    # 保留旧名以免外部引用断裂；实现收敛到 tools._common.clip_text
    return clip_text(text, limit)
