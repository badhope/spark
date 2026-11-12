from __future__ import annotations

import atexit
import logging
import subprocess
import threading
import time
import uuid
from typing import Any

from spark.models import ToolResult
from spark.platform_compat import kill_process_tree
from spark.sandbox import WorkdirSandbox
from spark.tools._common import clip_text

logger = logging.getLogger(__name__)

MAX_BUFFER = 200_000
MAX_JOBS = 8

# registry-level job store: id -> job dict
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()

# Optional persistence: set once by the server (or tests) at startup.
_STORE = None
_STORE_LOCK = threading.Lock()


def attach_store(store) -> None:
    """Bind a SessionStore so bg jobs survive server restarts (state marked lost)."""
    global _STORE
    with _STORE_LOCK:
        _STORE = store


_PERSIST_ERRORS: list[str] = []
_PERSIST_MAX_ERRORS = 100  # 最多保留最近 100 条持久化错误（防止无界增长）


def _persist_new(job: dict, cwd: str) -> None:
    with _STORE_LOCK:
        store = _STORE
    if store is None:
        return
    try:
        store.upsert_bg_job(job["id"], job["command"], cwd, job["started_at"])
    except Exception as exc:
        # 修复：append 后限制长度，超过上限丢弃最旧的（只增不减 → 有界环形）
        _PERSIST_ERRORS.append(f"new:{job['id']}:{exc}")
        if len(_PERSIST_ERRORS) > _PERSIST_MAX_ERRORS:
            del _PERSIST_ERRORS[:len(_PERSIST_ERRORS) - _PERSIST_MAX_ERRORS]


def _persist_state(job: dict) -> None:
    with _STORE_LOCK:
        store = _STORE
    if store is None:
        return
    try:
        store.update_bg_job(
            job["id"],
            output=job["output"],
            finished_at=job["finished_at"],
            exit_code=job["exit_code"],
        )
    except Exception as exc:
        # 修复：append 后限制长度，超过上限丢弃最旧的
        _PERSIST_ERRORS.append(f"state:{job['id']}:{exc}")
        if len(_PERSIST_ERRORS) > _PERSIST_MAX_ERRORS:
            del _PERSIST_ERRORS[:len(_PERSIST_ERRORS) - _PERSIST_MAX_ERRORS]


def _kill_all_running() -> None:
    with JOBS_LOCK:
        running = [j for j in JOBS.values() if j["finished_at"] is None]
    for job in running:
        try:
            _kill_job(job)
        except Exception:
            # atexit 回收后台作业失败时记录，不影响退出
            logger.warning("atexit 终止后台作业失败", exc_info=True)


atexit.register(_kill_all_running)


def _start_background(sandbox: WorkdirSandbox, command: str, cwd: str | None) -> dict:
    cwd_path = sandbox.resolve(cwd or ".")
    with JOBS_LOCK:
        running_n = sum(1 for j in JOBS.values() if j["finished_at"] is None)
        if running_n >= MAX_JOBS:
            raise RuntimeError(f"too many running background jobs (max {MAX_JOBS})")
    job_id = uuid.uuid4().hex  # 修复：完整 128-bit 熵（hex[:8] 仅 32-bit，碰撞概率过高）
    proc = subprocess.Popen(
        command,
        # shell=True is the tool's purpose (same as run_shell) — the command
        # has already passed sandbox.check_shell above. Job lifecycle is
        # managed via start_new_session + process-group kill.
        shell=True,  # nosec B602
        cwd=str(cwd_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    job: dict[str, Any] = {
        "id": job_id,
        "command": command,
        "process": proc,
        "output": "",
        "started_at": time.time(),
        "finished_at": None,
        "exit_code": None,
    }

    def reader() -> None:
        # Periodic persistence while the job is still running: the store used to
        # receive output only once, after the process exited, so a long-running
        # job showed nothing after a restart and "last persisted output" was
        # always empty. Flush at most every ~0.5 s to keep write amplification
        # bounded.
        last_flush = 0.0
        dirty = False
        try:
            stdout = proc.stdout
            assert stdout is not None
            for line in stdout:
                with JOBS_LOCK:
                    job["output"] = clip_text(job["output"] + line, MAX_BUFFER, keep_tail=True)
                dirty = True
                now = time.time()
                if now - last_flush >= 0.5:
                    if dirty:
                        _persist_state(job)
                        dirty = False
                    last_flush = now
        except Exception:
            # 进程输出流异常时强制标记失败，避免任务卡在 running 状态
            logger.warning("后台作业 %s 输出流读取失败，标记为失败", job.get("id"), exc_info=True)
            job["exit_code"] = job["exit_code"] if job["exit_code"] is not None else -1
        proc.wait()
        job["exit_code"] = proc.returncode
        job["finished_at"] = time.time()
        _persist_state(job)  # final flush: authoritative exit code + full output

    threading.Thread(target=reader, daemon=True, name=f"bg-job-{job_id}").start()
    with JOBS_LOCK:
        JOBS[job_id] = job
        finished = [jid for jid, j in JOBS.items() if j["finished_at"] is not None]
        finished.sort(key=lambda jid: JOBS[jid]["finished_at"] or 0)
        excess = max(0, len(finished) - MAX_JOBS)
        for jid in finished[:excess]:
            JOBS.pop(jid, None)
    _persist_new(job, str(cwd_path))
    return job


def bg_start_tool(sandbox: WorkdirSandbox, args: dict) -> ToolResult:
    if not sandbox.shell_allowed:
        return ToolResult(ok=False, payload={"error": "bg_start is disabled: sandbox-only access mode"})
    command = str(args.get("command") or "").strip()
    if not command:
        return ToolResult(ok=False, payload={"error": "command required"})
    reason = sandbox.check_shell(command)
    if reason:
        return ToolResult(ok=False, payload={"error": reason, "command": command})
    try:
        cwd_path = sandbox.resolve(str(args.get("cwd") or "."))
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    try:
        job = _start_background(sandbox, command, str(args.get("cwd") or "."))
    except RuntimeError as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    return ToolResult(
        ok=True,
        payload={
            "job_id": job["id"],
            "command": job["command"],
            "cwd": str(cwd_path),
            "hint": "use bg_output to poll results, bg_kill to terminate",
        },
    )


def bg_output_tool(args: dict) -> ToolResult:
    job_id = str(args.get("job_id") or "").strip()
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            running = job["finished_at"] is None
            tail_limit = int(args.get("tail") or 8000)
            output = job["output"][-tail_limit:]
            info = {
                "job_id": job_id,
                "command": job["command"],
                "running": running,
                "exit_code": job["exit_code"],
                "elapsed_sec": round((job["finished_at"] or time.time()) - job["started_at"], 1),
                "output": output,
                "truncated": len(job["output"]) > len(output),
            }
            return ToolResult(ok=True, payload=info)
    # in-memory miss: fall back to the persisted record (server restarted since the job ran)
    with _STORE_LOCK:
        store = _STORE
    if store is None:
        return ToolResult(ok=False, payload={"error": f"unknown job: {job_id}"})
    row = store.get_bg_job(job_id)
    if row is None:
        return ToolResult(ok=False, payload={"error": f"unknown job: {job_id}"})
    tail_limit = int(args.get("tail") or 8000)
    stored_output = str(row.get("output") or "")
    finished = row.get("finished_at")
    return ToolResult(
        ok=True,
        payload={
            "job_id": job_id,
            "command": row["command"],
            "running": False,
            "lost": True,
            "exit_code": row.get("exit_code"),
            "elapsed_sec": round((finished or time.time()) - row["started_at"], 1) if finished else None,
            "output": stored_output[-tail_limit:],
            "truncated": len(stored_output) > tail_limit,
            "note": "process state was lost after restart; showing last persisted output",
        },
    )


def _kill_job(job: dict) -> None:
    """Kill the whole process tree so children of `shell -c 'cmd'` die too."""
    proc = job["process"]
    kill_process_tree(proc.pid, proc)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass


def bg_kill_tool(args: dict) -> ToolResult:
    job_id = str(args.get("job_id") or "").strip()
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return ToolResult(ok=False, payload={"error": f"unknown job: {job_id}"})
        finished = job["finished_at"] is not None
    if finished:
        return ToolResult(ok=True, payload={"job_id": job_id, "killed": False, "reason": "already finished"})
    try:
        _kill_job(job)
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    with JOBS_LOCK:
        job["exit_code"] = job["process"].returncode if job["process"].poll() is not None else -15
        if job["finished_at"] is None:
            job["finished_at"] = time.time()
    _persist_state(job)
    return ToolResult(ok=True, payload={"job_id": job_id, "killed": True})


def bg_list_tool() -> ToolResult:
    with JOBS_LOCK:
        jobs = [
            {
                "job_id": jid,
                "command": j["command"],
                "running": j["finished_at"] is None,
                "exit_code": j["exit_code"],
                "elapsed_sec": round((j["finished_at"] or time.time()) - j["started_at"], 1),
            }
            for jid, j in JOBS.items()
        ]
    live_ids = {j["job_id"] for j in jobs}
    with _STORE_LOCK:
        store = _STORE
    if store is not None:
        try:
            for row in store.list_bg_jobs():
                if row["id"] in live_ids:
                    continue
                finished = row.get("finished_at")
                jobs.append(
                    {
                        "job_id": row["id"],
                        "command": row["command"],
                        "running": False,
                        "lost": True,
                        "exit_code": row.get("exit_code"),
                        "elapsed_sec": round((finished or time.time()) - row["started_at"], 1) if finished else None,
                    }
                )
        except Exception:
            # status 查询失败时保留已有 jobs 数据
            logger.warning("后台作业状态查询失败", exc_info=True)
    return ToolResult(ok=True, payload={"jobs": jobs})
