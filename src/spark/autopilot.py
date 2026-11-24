"""Autonomous coding runner: requirement → plan/build → verify → repair → deliver.

This module is deliberately a *runner*, not a second agent loop.  Every turn is
executed by the existing :class:`spark.core.loop.AgentLoop`; the runner only
owns the state machine, independent verification, failure feedback and the
crash-safe run ledger.  Reusing the one loop keeps approvals, sandboxing,
checkpoints and tool validation identical to interactive coding.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable

from spark.proc import run_bounded
from spark.sandbox import WorkdirSandbox
from spark.tools.gitops import RunTestsArgs, run_tests

DEFAULT_MAX_TURNS = 8
DEFAULT_TURN_TIMEOUT_SEC = 900
DEFAULT_TASK_TIMEOUT_SEC = 3600
NO_PROGRESS_LIMIT = 2
MAX_CONSECUTIVE_ERRORS = 2


@dataclass(frozen=True)
class AutopilotLimits:
    """Hard bounds that keep an unattended run from burning tokens forever."""

    max_turns: int = DEFAULT_MAX_TURNS
    turn_timeout_sec: int = DEFAULT_TURN_TIMEOUT_SEC
    task_timeout_sec: int = DEFAULT_TASK_TIMEOUT_SEC
    max_cost_usd: float = 0.0
    no_progress_limit: int = NO_PROGRESS_LIMIT
    max_consecutive_errors: int = MAX_CONSECUTIVE_ERRORS
    require_change: bool = True

    def normalized(self) -> "AutopilotLimits":
        return AutopilotLimits(
            max_turns=max(1, min(int(self.max_turns), 50)),
            turn_timeout_sec=max(30, min(int(self.turn_timeout_sec), 7200)),
            task_timeout_sec=max(60, min(int(self.task_timeout_sec), 86400)),
            max_cost_usd=max(0.0, float(self.max_cost_usd or 0.0)),
            no_progress_limit=max(1, min(int(self.no_progress_limit), 10)),
            max_consecutive_errors=max(1, min(int(self.max_consecutive_errors), 10)),
            require_change=bool(self.require_change),
        )


@dataclass
class AutopilotTurn:
    """One agent turn's externally observable result."""

    ok: bool
    summary: str = ""
    error: str = ""
    cost_usd: float = 0.0
    tool_calls: int = 0


@dataclass
class Verification:
    """Independent test-gate result, never produced by the model itself."""

    ok: bool
    command: str = ""
    output: str = ""
    error: str = ""


@dataclass
class DiffProbe:
    """A cheap, reproducible fingerprint of the worktree."""

    changed: bool
    digest: str
    summary: str = ""


@dataclass
class AutopilotState:
    """Serializable run ledger; written after every turn and after verification."""

    run_id: str
    session_id: str
    workdir: str
    requirement: str
    status: str = "running"  # running / passed / failed / blocked
    phase: str = "build"  # build / repair / audit / done
    turn: int = 0
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    baseline_digest: str = ""
    last_diff_digest: str = ""
    last_diff_summary: str = ""
    last_test_ok: bool = False
    last_test_command: str = ""
    last_test_output: str = ""
    last_summary: str = ""
    last_error: str = ""
    consecutive_errors: int = 0
    consecutive_no_progress: int = 0
    total_cost_usd: float = 0.0
    audit_done: bool = False
    next_prompt: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutopilotState":
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in allowed})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)


TurnRunner = Callable[[str], AutopilotTurn]
Verifier = Callable[[], Verification]
DiffProbeFn = Callable[[], DiffProbe]
EventFn = Callable[[dict[str, Any]], None]


def _prompt(name: str, replacements: dict[str, str]) -> str:
    text = files("spark.prompts").joinpath(name).read_text(encoding="utf-8")
    for key, value in replacements.items():
        text = text.replace(f"<<{key}>>", value)
    return text


def render_build_prompt(state: AutopilotState, limits: AutopilotLimits) -> str:
    contract = _prompt("autopilot_contract.md", {})
    build = _prompt(
        "autopilot_build.md",
        {
            "REQUIREMENT": state.requirement,
            "TURN": str(state.turn + 1),
            "MAX_TURNS": str(limits.max_turns),
            "WORKDIR": state.workdir,
        },
    )
    return f"{contract}\n\n{build}"


def render_repair_prompt(
    state: AutopilotState,
    limits: AutopilotLimits,
    verification: Verification,
    diff: DiffProbe,
) -> str:
    return _prompt(
        "autopilot_repair.md",
        {
            "REQUIREMENT": state.requirement,
            "TURN": str(state.turn + 1),
            "MAX_TURNS": str(limits.max_turns),
            "LAST_SUMMARY": state.last_summary or "（无）",
            "TEST_COMMAND": verification.command or "（未检测到测试命令）",
            "TEST_OUTPUT": (verification.output or verification.error or "（无输出）")[:8000],
        },
    )


def render_audit_prompt(
    state: AutopilotState,
    limits: AutopilotLimits,
    verification: Verification,
    diff: DiffProbe,
) -> str:
    return _prompt(
        "autopilot_verify.md",
        {
            "REQUIREMENT": state.requirement,
            "TURN": str(state.turn + 1),
            "MAX_TURNS": str(limits.max_turns),
            "TEST_STATUS": "通过" if verification.ok else "失败",
            "DIFF_SUMMARY": diff.summary or "（无文件变更）",
        },
    )


def make_test_verifier(
    sandbox: WorkdirSandbox,
    *,
    timeout_sec: int = 1800,
) -> Verifier:
    """Independent test gate using the repository's own test entrypoint."""

    def verify() -> Verification:
        result = run_tests(sandbox, RunTestsArgs(path=".", timeout_sec=timeout_sec))
        payload = result.payload or {}
        return Verification(
            ok=bool(result.ok),
            command=str(payload.get("command") or ""),
            output=str(payload.get("output") or ""),
            error=str(payload.get("error") or ""),
        )

    return verify


def _bounded_git(workdir: Path, args: list[str], timeout: int = 30) -> str:
    try:
        result = run_bounded(["git", *args], cwd=workdir, timeout=timeout)
    except OSError:
        return ""
    if result.timed_out:
        return ""
    return (result.stdout or "") + (result.stderr or "")


_TREE_EXCLUDES = {
    ".git", ".hg", ".svn", ".spark", "node_modules", "dist", "build",
    "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "target",
    "vendor", "coverage", ".next", ".nuxt",
}


def _tree_digest(root: Path, limit: int = 2500) -> str:
    """Fallback fingerprint for non-git workdirs: path + size + mtime."""
    h = hashlib.sha256()
    count = 0
    for path in root.rglob("*"):
        if count >= limit:
            break
        if not path.is_file():
            continue
        if any(part in _TREE_EXCLUDES for part in path.parts):
            continue
        try:
            stat = path.stat()
            rel = path.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        h.update(
            f"{rel}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode(
                "utf-8", errors="replace"
            )
        )
        count += 1
    return h.hexdigest()


def _filter_spark_status(status: str) -> str:
    """Drop Spark's own state/cache entries from porcelain status output."""
    kept: list[str] = []
    for entry in status.split("\0"):
        if not entry:
            continue
        path = entry[3:] if len(entry) > 3 else ""
        if path == ".spark" or path.startswith(".spark/"):
            continue
        kept.append(entry)
    return "\0".join(kept)


def make_git_diff_probe(workdir: Path) -> DiffProbeFn:
    """Fingerprint tracked + untracked changes without relying on the model."""

    initial_digest: str | None = None

    def probe() -> DiffProbe:
        nonlocal initial_digest
        inside = _bounded_git(workdir, ["rev-parse", "--is-inside-work-tree"]).strip()
        if inside.lower() != "true":
            digest = _tree_digest(workdir)
            if initial_digest is None:
                initial_digest = digest
            return DiffProbe(
                changed=digest != initial_digest,
                digest=digest,
                summary="non-git workdir",
            )
        status = _filter_spark_status(
            _bounded_git(workdir, ["status", "--porcelain=v1", "-z"]).strip()
        )
        diff = _bounded_git(workdir, ["diff", "--stat"]).strip()
        staged = _bounded_git(workdir, ["diff", "--cached", "--stat"]).strip()
        payload = "\n".join(part for part in (status, diff, staged) if part)
        # ``git diff --stat`` only says "1 file changed"; two edits with the
        # same line count would look identical. Include the working-tree
        # content digest so a real edit always changes the fingerprint.
        digest = hashlib.sha256(
            (payload + "\0" + _tree_digest(workdir)).encode("utf-8")
        ).hexdigest()
        if initial_digest is None:
            initial_digest = digest
        summary = (diff or staged or status).strip()[:400]
        return DiffProbe(
            changed=digest != initial_digest,
            digest=digest,
            summary=summary,
        )

    return probe


def make_agent_turn_runner(loop: Any, *, timeout_sec: int) -> TurnRunner:
    """Adapt AgentLoop's sync iterator into a single bounded autonomous turn."""

    def run(prompt: str) -> AutopilotTurn:
        final = ""
        error = ""
        tool_calls = 0
        try:
            for event in loop.iter_turn_sync(prompt, timeout_sec=timeout_sec):
                if event.type == "tool_start":
                    tool_calls += 1
                elif event.type == "turn_end" and event.text:
                    final = event.text
                elif event.type == "turn_error":
                    error = event.text or "turn_error"
        except TimeoutError as exc:
            return AutopilotTurn(
                ok=False,
                summary=final,
                error=str(exc) or f"turn timed out after {timeout_sec}s",
                cost_usd=_loop_cost(loop),
                tool_calls=tool_calls,
            )
        except Exception as exc:  # noqa: BLE001 - runner boundary
            return AutopilotTurn(
                ok=False,
                summary=final,
                error=f"{type(exc).__name__}: {exc}",
                cost_usd=_loop_cost(loop),
                tool_calls=tool_calls,
            )
        return AutopilotTurn(
            ok=not error,
            summary=final,
            error=error,
            cost_usd=_loop_cost(loop),
            tool_calls=tool_calls,
        )

    return run


def _loop_cost(loop: Any) -> float:
    usage = getattr(loop, "_turn_usage_totals", None) or {}
    try:
        return float(usage.get("cost_usd") or 0.0)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class _TurnSnapshot:
    turn: AutopilotTurn
    verification: Verification
    diff: DiffProbe
    prev_diff_digest: str
    prev_test_output: str
    prev_summary: str


class AutopilotRunner:
    """State machine around an injected turn runner and independent verifier."""

    def __init__(
        self,
        state: AutopilotState,
        *,
        turn_runner: TurnRunner,
        verifier: Verifier,
        diff_probe: DiffProbeFn,
        limits: AutopilotLimits | None = None,
        state_path: Path | None = None,
        on_event: EventFn | None = None,
    ) -> None:
        self.state = state
        self.turn_runner = turn_runner
        self.verifier = verifier
        self.diff_probe = diff_probe
        self.limits = (limits or AutopilotLimits()).normalized()
        self.state_path = state_path
        self.on_event = on_event

    # -- public ---------------------------------------------------------------
    def run(self) -> AutopilotState:
        """Run until pass, hard failure, or a bound; always persist last state."""
        if self.state.status != "running":
            return self.state
        if not self.state.baseline_digest:
            probe = self.diff_probe()
            self.state.baseline_digest = probe.digest
            self.state.last_diff_digest = probe.digest

        while self.state.status == "running":
            reason = self._limit_reason()
            if reason:
                self._finish("failed", reason)
                break
            prompt = self.state.next_prompt or self._prompt_for_phase()
            if not prompt.strip():
                self._finish("failed", "运行器没有可执行的提示词")
                break
            snapshot = self._execute_turn(prompt)
            if self._after_turn(snapshot):
                break

        self.state.updated_at = time.time()
        self._save()
        return self.state

    def _limit_reason(self) -> str:
        if self.state.turn >= self.limits.max_turns:
            return "达到最大轮次，任务未通过独立测试"
        if self._task_timeout_hit():
            return "任务总时限已到，任务未通过独立测试"
        if (
            self.limits.max_cost_usd > 0
            and self.state.total_cost_usd >= self.limits.max_cost_usd
        ):
            return "达到任务成本上限，任务未通过独立测试"
        return ""

    def _execute_turn(self, prompt: str) -> _TurnSnapshot:
        previous = _TurnSnapshot(
            turn=AutopilotTurn(ok=True),
            verification=Verification(ok=self.state.last_test_ok),
            diff=DiffProbe(
                changed=False,
                digest=self.state.last_diff_digest,
                summary=self.state.last_diff_summary,
            ),
            prev_diff_digest=self.state.last_diff_digest,
            prev_test_output=self.state.last_test_output,
            prev_summary=self.state.last_summary,
        )
        self.state.turn += 1
        self.state.next_prompt = ""
        self._emit(
            {
                "type": "turn_start",
                "turn": self.state.turn,
                "phase": self.state.phase,
            }
        )
        turn = self.turn_runner(prompt)
        self.state.last_summary = turn.summary or self.state.last_summary
        self.state.total_cost_usd += max(0.0, float(turn.cost_usd or 0.0))
        if not turn.ok:
            self.state.last_error = turn.error or "turn failed"
            self.state.consecutive_errors += 1
        else:
            self.state.consecutive_errors = 0

        verification = self.verifier()
        diff = self.diff_probe()
        test_output = (verification.output or verification.error)[:8000]
        self.state.last_test_ok = verification.ok
        self.state.last_test_command = verification.command
        self.state.last_test_output = test_output
        self.state.last_diff_digest = diff.digest
        self.state.last_diff_summary = diff.summary
        self._record(
            {
                "turn": self.state.turn,
                "phase": self.state.phase,
                "ok": turn.ok,
                "summary": turn.summary[:2000],
                "error": turn.error[:2000],
                "cost_usd": round(float(turn.cost_usd or 0.0), 6),
                "tool_calls": turn.tool_calls,
                "test_ok": verification.ok,
                "test_command": verification.command,
                "test_output": test_output[:4000],
                "diff_digest": diff.digest,
                "diff_summary": diff.summary[:1000],
            }
        )
        self._save()
        self._emit(
            {
                "type": "turn_end",
                "turn": self.state.turn,
                "ok": turn.ok,
                "test_ok": verification.ok,
                "diff_changed": diff.digest != self.state.baseline_digest,
                "cost_usd": self.state.total_cost_usd,
            }
        )
        return _TurnSnapshot(
            turn=turn,
            verification=verification,
            diff=diff,
            prev_diff_digest=previous.prev_diff_digest,
            prev_test_output=previous.prev_test_output,
            prev_summary=previous.prev_summary,
        )

    def _after_turn(self, snapshot: _TurnSnapshot) -> bool:
        """Apply the transition; return True when the run should stop."""
        if self.state.consecutive_errors >= self.limits.max_consecutive_errors:
            self._finish(
                "failed",
                f"连续 {self.state.consecutive_errors} 轮模型/运行错误，"
                f"最后错误：{self.state.last_error}",
            )
            return True

        if snapshot.verification.ok:
            if not self.state.audit_done:
                self.state.audit_done = True
                self.state.phase = "audit"
                self.state.next_prompt = render_audit_prompt(
                    self.state, self.limits, snapshot.verification, snapshot.diff
                )
                self._save()
                return False
            if self.limits.require_change and not snapshot.diff.changed:
                self._finish(
                    "failed",
                    "独立测试通过但工作区没有代码变更；需求未被实际实现",
                )
                return True
            self.state.phase = "done"
            self._finish("passed", "独立测试通过，交付复核完成")
            return True

        self.state.phase = "repair"
        self.state.next_prompt = render_repair_prompt(
            self.state, self.limits, snapshot.verification, snapshot.diff
        )
        same_diff = snapshot.diff.digest == snapshot.prev_diff_digest
        same_test = bool(self.state.last_test_output.strip()) and (
            self.state.last_test_output.strip() == snapshot.prev_test_output.strip()
        )
        same_summary = bool(snapshot.turn.summary.strip()) and (
            snapshot.turn.summary.strip() == snapshot.prev_summary.strip()
        )
        if same_diff and (same_test or same_summary):
            self.state.consecutive_no_progress += 1
            if self.state.consecutive_no_progress >= self.limits.no_progress_limit:
                self._finish(
                    "failed",
                    f"连续 {self.state.consecutive_no_progress} 轮无进展："
                    "测试仍失败且代码/输出没有变化",
                )
                return True
        else:
            self.state.consecutive_no_progress = 0
        self._save()
        return False

    # -- internals ------------------------------------------------------------
    def _prompt_for_phase(self) -> str:
        if self.state.phase == "build":
            return render_build_prompt(self.state, self.limits)
        if self.state.phase == "repair":
            return render_repair_prompt(
                self.state,
                self.limits,
                Verification(
                    ok=self.state.last_test_ok,
                    command=self.state.last_test_command,
                    output=self.state.last_test_output,
                ),
                DiffProbe(
                    changed=self.state.last_diff_digest != self.state.baseline_digest,
                    digest=self.state.last_diff_digest,
                    summary=self.state.last_diff_summary,
                ),
            )
        if self.state.phase == "audit":
            self.state.audit_done = True
            return render_audit_prompt(
                self.state,
                self.limits,
                Verification(
                    ok=self.state.last_test_ok,
                    command=self.state.last_test_command,
                    output=self.state.last_test_output,
                ),
                DiffProbe(
                    changed=self.state.last_diff_digest != self.state.baseline_digest,
                    digest=self.state.last_diff_digest,
                    summary=self.state.last_diff_summary,
                ),
            )
        return ""

    def _task_timeout_hit(self) -> bool:
        return (time.time() - self.state.started_at) >= self.limits.task_timeout_sec

    def _record(self, entry: dict[str, Any]) -> None:
        entry["at"] = time.time()
        self.state.history.append(entry)

    def _finish(self, status: str, message: str) -> None:
        self.state.status = status
        self.state.last_error = message
        self.state.updated_at = time.time()
        self._save()
        self._emit({"type": "run_end", "status": status, "message": message})

    def _save(self) -> None:
        self.state.updated_at = time.time()
        if self.state_path is not None:
            self.state.save(self.state_path)

    def _emit(self, payload: dict[str, Any]) -> None:
        if self.on_event is not None:
            self.on_event(payload)


def new_state(
    *,
    run_id: str,
    session_id: str,
    workdir: Path,
    requirement: str,
) -> AutopilotState:
    return AutopilotState(
        run_id=run_id,
        session_id=session_id,
        workdir=str(workdir),
        requirement=requirement,
        started_at=time.time(),
    )


__all__ = [
    "AutopilotLimits",
    "AutopilotRunner",
    "AutopilotState",
    "AutopilotTurn",
    "DiffProbe",
    "Verification",
    "make_agent_turn_runner",
    "make_git_diff_probe",
    "make_test_verifier",
    "new_state",
    "render_audit_prompt",
    "render_build_prompt",
    "render_repair_prompt",
]
