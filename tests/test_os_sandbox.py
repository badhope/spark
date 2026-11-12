"""OS 级沙箱 v1：Windows Job Object（真实 OS 行为）+ macOS profile（纯文本单测）。"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.sandbox import WorkdirSandbox
from spark.tools.os_sandbox import (
    assign_windows_job,
    macos_profile_text,
    macos_sandbox_command,
    release_windows_job,
)
from spark.tools.shell import RunShellArgs, run_shell


def _workspace_sandbox(tmp_path: Path) -> WorkdirSandbox:
    cfg = SparkConfig()
    cfg.agent.approval = "full-auto"
    cfg.agent.sandbox_mode = "workspace"
    (tmp_path / "wd").mkdir()
    return WorkdirSandbox(tmp_path / "wd", cfg)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")
def test_run_shell_uses_job_object_in_workspace_mode(tmp_path: Path) -> None:
    sb = _workspace_sandbox(tmp_path)
    r = run_shell(sb, RunShellArgs(command="echo ok"), timeout_sec=30, max_output_chars=1000)
    assert r.ok, r.payload
    assert r.payload["sandbox"]["kind"] == "job-object"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")
def test_full_access_mode_skips_job_object(tmp_path: Path) -> None:
    cfg = SparkConfig()
    cfg.agent.approval = "full-auto"
    cfg.agent.sandbox_mode = "full-access"
    (tmp_path / "wd").mkdir()
    sb = WorkdirSandbox(tmp_path / "wd", cfg)
    r = run_shell(sb, RunShellArgs(command="echo ok"), timeout_sec=30, max_output_chars=1000)
    assert r.ok
    assert r.payload["sandbox"]["kind"] == ""


@pytest.mark.skipif(sys.platform != "win32", reason="真实 OS 行为")
def test_job_object_kill_on_close(tmp_path: Path) -> None:
    """关掉 Job 句柄 → 被纳管的子进程必须死（kill-on-close 真实语义）。"""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(60)"],
        stdout=subprocess.PIPE,
    )
    try:
        assert proc.stdout is not None
        proc.stdout.readline()  # 等它真正起来
        assert assign_windows_job(proc, memory_limit=2**31)
        release_windows_job(proc.pid)  # close handle → kill-on-close
        assert proc.wait(timeout=15) is not None, "关句柄后子进程必须退出"
    finally:
        if proc.poll() is None:
            proc.kill()


@pytest.mark.skipif(sys.platform != "win32", reason="真实 OS 行为")
def test_job_object_memory_limit_kills_hog(tmp_path: Path) -> None:
    """内存上限真实生效：先挂 job，再超额提交内存 → 进程被杀（非零退出）。

    时序要点：限额只对入 job 之后的内存提交生效，必须在分配前挂载。
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(1); x = bytearray(512 * 1024 * 1024); print('allocated', flush=True); time.sleep(30)"],
        stdout=subprocess.PIPE,
    )
    try:
        assert assign_windows_job(proc, memory_limit=64 * 1024 * 1024)
        code = proc.wait(timeout=30)
        assert code != 0, "超额分配必须被 Job Object 终止"
    finally:
        release_windows_job(proc.pid)
        if proc.poll() is None:
            proc.kill()


@pytest.mark.skipif(sys.platform != "win32", reason="真实 OS 行为")
def test_job_object_child_processes_die_with_job(tmp_path: Path) -> None:
    """子进程树整体纳管：父进程活着，但 close job → 整棵树被清理。"""
    inner = tmp_path / "inner.py"
    pidfile = tmp_path / "child.pid"
    inner.write_text(
        "import subprocess, sys, time\n"
        # Give the test process time to assign the Job Object before the
        # grandchild is created. A tight 0.8s window failed under full-suite
        # load even though the production path was fine.
        "time.sleep(3.0)\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open(r'{pidfile}', 'w').write(str(p.pid))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    proc = subprocess.Popen([sys.executable, str(inner)], stdout=subprocess.DEVNULL)
    try:
        # job 挂载必须早于孙进程派生（成员资格只传给挂载之后派生的进程）
        assert assign_windows_job(proc, memory_limit=2**31)
        for _ in range(100):
            if pidfile.exists():
                break
            time.sleep(0.1)
        assert pidfile.exists(), "孙进程应已派生"
        child_pid = int(pidfile.read_text().strip())

        def alive(pid: int) -> bool:
            import ctypes

            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            k32.CloseHandle(h)  # type: ignore[attr-defined]
            return True

        assert alive(child_pid), "孙进程应先活着"
        release_windows_job(proc.pid)  # close → kill-on-close 清理整树
        assert proc.wait(timeout=15) is not None, "父进程必须退出"
        deadline = time.time() + 15
        while alive(child_pid) and time.time() < deadline:
            time.sleep(0.2)
        assert not alive(child_pid), "孙进程必须随 job 一起被清理"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_macos_profile_text_gates_writes_and_network(tmp_path: Path) -> None:
    profile = macos_profile_text("/Users/dev/proj", allow_network=False)
    assert "(deny file-write*)" in profile
    assert '(allow file-write* (subpath "/Users/dev/proj"))' in profile
    assert "(deny network*)" in profile
    # 允许联网时不含 deny network*
    profile_net = macos_profile_text("/Users/dev/proj", allow_network=True)
    assert "(deny network*)" not in profile_net


def test_macos_sandbox_command_none_off_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert macos_sandbox_command(".", "ls", "/tmp") is None


def test_macos_sandbox_command_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil as _shutil

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(_shutil, "which", lambda name: None)
    assert macos_sandbox_command(".", "ls", "/tmp") is None
