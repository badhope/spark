from __future__ import annotations

import asyncio
import importlib
import logging
import os
import secrets
import threading
import time
import uuid
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from spark.asyncutil import run_coroutine_sync
from spark.config import (
    ModelProfile,
    SparkConfig,
    env_api_key,
    mask_secret,
    save_config,
)
from spark.core.loop import AgentLoop
from spark.memory.service import MemoryService
from spark.memory.store import MemoryStore
from spark.models import ApprovalAction, ApprovalDecision
from spark.providers.errors import provider_preflight
from spark.providers.factory import create_provider
from spark.sandbox import WorkdirSandbox
from spark.task_queue import QueueOutcome, TaskQueue
from spark.tools import bg as bg_tools
from spark.tools.mcp_bridge import McpBridge
from spark.tools.registry import ToolContext, ToolRegistry
from spark.web.utils import MCP_REDACTED_HEADER, probe_env, redact_mcp_headers

logger = logging.getLogger(__name__)

# Weak registry of live web states. Production teardown and the test suite both
# need a single place to stop schedulers / close Chroma clients; without it a
# state created by a test kept its scheduler thread and native client alive into
# the next test (the full-suite Chroma access violation reproduced this way).
_ACTIVE_STATES: list[weakref.ReferenceType["SparkWebState"]] = []
_ACTIVE_STATES_LOCK = threading.Lock()


class WebQuestionBridge:
    """Carries an ``ask_user`` question from the agent thread to the browser.

    Same shape as :class:`WebApprover`: the tool side awaits ``ask()`` while the
    HTTP side calls ``answer()``. The event is emitted by the loop so the UI can
    render the card; this class only owns the rendezvous.
    """

    def __init__(self) -> None:
        self.pending = False
        self._event = threading.Event()
        self._answer = ""
        self._question = ""
        self._options: list[str] = []

    async def ask(self, question: str, options: list[str] | None = None) -> str:
        self.pending = True
        self._question = question
        self._options = list(options or [])
        self._answer = ""
        self._event = threading.Event()
        deadline = time.monotonic() + 600.0
        try:
            while not self._event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.1, remaining))
        finally:
            self.pending = False
        return self._answer

    def payload(self) -> dict:
        return {"question": self._question, "options": self._options}

    def answer(self, text: str) -> None:
        self._answer = str(text or "")
        self._event.set()

    def cancel(self) -> None:
        self._answer = ""
        self._event.set()

    def pending_payload(self) -> dict | None:
        if not self.pending:
            return None
        return self.payload()


class WebApprover:
    """Bridges approval prompts from the agent loop thread to HTTP handlers."""

    def __init__(self) -> None:
        self.pending = False
        self._event = threading.Event()
        self._cancel_event = threading.Event()
        self._action: ApprovalAction = "deny"
        self._tool_call_id = ""
        self._summary = ""
        self._diff = ""
        self._tool = ""
        self._arguments: dict = {}

    async def __call__(self, request) -> ApprovalDecision:
        self.pending = True
        self._action = "deny"
        self._summary = str(getattr(request, "summary", "") or "")
        self._diff = str(getattr(request, "diff", "") or "")
        call = getattr(request, "tool_call", None)
        self._tool = str(getattr(call, "name", "") or "")
        args = getattr(call, "arguments", {}) or {}
        self._arguments = args if isinstance(args, dict) else {}
        cancelled = self._cancel_event.is_set()
        if cancelled:
            self._cancel_event.clear()
        self._event = threading.Event()
        self._tool_call_id = request.tool_call.id
        deadline = time.monotonic() + 600.0
        try:
            while (
                not self._event.is_set()
                and not cancelled
                and not self._cancel_event.is_set()
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.1, remaining))
        finally:
            self.pending = False
            if self._cancel_event.is_set():
                cancelled = True
            self._cancel_event.clear()
        if cancelled:
            self._action = "deny"
        return ApprovalDecision(tool_call_id=self._tool_call_id, action=self._action)

    def respond(self, action: ApprovalAction) -> None:
        self._action = action
        self._event.set()

    def cancel(self) -> None:
        self._cancel_event.set()
        self._event.set()

    def pending_payload(self) -> dict | None:
        if not self.pending:
            return None
        return {
            "summary": self._summary,
            "diff": self._diff,
            "tool": self._tool,
            "arguments": self._arguments,
            "tool_call_id": self._tool_call_id,
        }


@dataclass
class SessionRuntime:
    """Per-session execution state.

    A web process can have several sessions alive at once: a stream keeps
    running after the user switches to another session, and its approval /
    question rendezvous must not leak into the new one. The runtime owns the
    loop, approver, question bridge, turn lock and replay buffer for exactly
    one session.
    """

    session_id: str
    loop: AgentLoop | None = None
    approver: WebApprover | None = None
    question_bridge: WebQuestionBridge = field(default_factory=WebQuestionBridge)
    turn_lock: threading.Lock = field(default_factory=threading.Lock)
    busy: bool = False
    events: list[dict] = field(default_factory=list)
    max_events: int = 512
    _events_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _seq_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_seq: int = 0

    def next_seq_start(self) -> int:
        """Return the last sequence already used by this session.

        A new SSE writer starts numbering at this value + 1, so replay cursors
        stay monotonic across turns instead of restarting at 1.
        """
        with self._seq_lock:
            return self._last_seq

    def record_event(self, envelope: dict) -> None:
        seq = int(envelope.get("seq") or 0)
        if seq <= 0:
            return
        with self._seq_lock:
            self._last_seq = max(self._last_seq, seq)
        with self._events_lock:
            self.events.append(dict(envelope))
            if len(self.events) > self.max_events:
                del self.events[: len(self.events) - self.max_events]

    def events_after(self, after_seq: int) -> tuple[list[dict], bool]:
        """Return (events, reset_required).

        ``reset_required`` is True when the requested cursor predates the
        retained ring buffer; the client must refetch history instead of
        pretending it caught up.
        """
        with self._events_lock:
            if not self.events:
                return [], False
            first_seq = int(self.events[0].get("seq") or 0)
            if after_seq and after_seq < first_seq - 1:
                return [], True
            return [
                dict(item)
                for item in self.events
                if int(item.get("seq") or 0) > after_seq
            ], False


def _generate_session_meta_async(
    state: SparkWebState,
    user_text: str,
    assistant_text: str,
    *,
    session_id: str | None = None,
) -> None:
    """After a turn completes, ask the model for a short title + keywords and store them."""
    sid = str(session_id or state.session_id or "")

    def run() -> None:
        import json as _json
        import re

        from spark.asyncutil import run_coroutine_sync
        from spark.providers.auxiliary import chat_once

        base_url = state.cfg.provider.base_url.rstrip("/")
        # Ensure the URL ends with /v1 for OpenAI-compatible APIs. Users may
        # configure "https://api.deepseek.com" without the "/v1" suffix.
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        api_key = state.cfg.provider.api_key or ""
        # 小模型分工：标题/关键词是廉价辅助调用，配置了 small_model 就不走主模型
        model = (
            getattr(state.cfg.provider, "small_model", "") or state.cfg.provider.model
        )
        if not model or (state.cfg.provider.name == "openai_compat" and not api_key):
            return
        try:
            convo = (
                f"[user]\n{user_text[:2000]}\n\n[assistant]\n{assistant_text[:2000]}"
            )
            prompt = (
                "为这段对话生成元信息。严格输出一行 JSON，格式："
                '{"title": "不超过12字的标题", "keywords": ["关键词1", "关键词2", "关键词3"]}。'
                "标题概括用户核心意图，关键词提炼 2-4 个主题词。只输出 JSON，不要其他内容。\n\n"
                f"对话：\n{convo}"
            )
            text = run_coroutine_sync(
                chat_once(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    user=prompt,
                    temperature=0.2,
                    max_tokens=120,
                ),
                timeout=60.0,
            )
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return
            meta = _json.loads(match.group(0))
            title = str(meta.get("title") or "").strip().strip("\"'")[:40]
            raw_kws = meta.get("keywords")
            keywords = (
                [str(k).strip()[:20] for k in raw_kws if str(k).strip()][:4]
                if isinstance(raw_kws, list)
                else []
            )
            if title:
                state.store.set_title(sid, title)
            if keywords:
                state.store.set_keywords(sid, " ".join(keywords))
        except Exception as exc:
            logger.warning("Failed to update session meta: %s", exc)

    threading.Thread(target=run, daemon=True, name="session-meta").start()


class SparkWebState:
    def __init__(self, workdir: Path, cfg: SparkConfig, store: Any) -> None:
        self.workdir = workdir
        self.cfg = cfg
        self.store = store
        self.sandbox = WorkdirSandbox(workdir, cfg)
        self.session_id = ""
        self._runtimes: dict[str, SessionRuntime] = {}
        self._current_runtime: SessionRuntime | None = None
        self._fallback_turn_lock = threading.Lock()
        self._fallback_question_bridge = WebQuestionBridge()
        self._orphan_loop: AgentLoop | None = None
        self.settings_lock = threading.Lock()
        self.mcp_bridge: McpBridge | None = None
        self.control_token = secrets.token_urlsafe(32)
        from spark.web.remote import DevicePairing

        self.pairing = DevicePairing()
        #: Compatibility switch for pre-v1 SSE clients. Set
        #: ``SPARK_WEB_LEGACY=1`` to emit bare ``data:`` frames again while the
        #: rest of the server keeps the per-session runtime and replay API.
        self.legacy_sse = os.environ.get("SPARK_WEB_LEGACY", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        self.test_probe_semaphore = threading.BoundedSemaphore(2)
        self.memory_store = MemoryStore(
            importlib.import_module("spark.web.server").default_home()
            / "memory.db",
            cfg.memory,
        )
        self.memory = MemoryService(self.memory_store, cfg)
        self.env_info = self._detect_env()
        bg_tools.attach_store(self.store)
        from spark.tools import goal as goal_tools

        goal_tools.attach_bridges(self.store, lambda: self.session_id)
        # 定时任务：调度器只判"到点了"，执行走 run_due_automations（由
        # 后台线程调，且与普通回合共用 turn_lock，保证不会和用户抢会话）。
        from spark.automations import AutomationScheduler

        self.automations = AutomationScheduler(self.store, on_due=self.run_due_automations)
        # 空闲任务队列：只在没有任何 runtime 忙碌时跑一条，进程崩溃后恢复 queued。
        self.queue = TaskQueue(
            self.store,
            runner=self._run_queued_task,
            is_busy=self._queue_is_busy,
            poll_sec=10.0,
        )
        self.boot_mcp()
        self.rebuild_loop(reuse_latest=True)
        # Register before starting the thread: if start() ever raises, the
        # teardown registry can still stop/close this instance.
        with _ACTIVE_STATES_LOCK:
            _ACTIVE_STATES.append(weakref.ref(self))
        self.automations.start()
        self.queue.start()

    @property
    def current_runtime(self) -> SessionRuntime | None:
        return self._current_runtime

    @property
    def loop(self) -> AgentLoop | None:
        runtime = self._current_runtime
        if runtime is not None:
            return runtime.loop
        return self._orphan_loop

    @loop.setter
    def loop(self, value: AgentLoop | None) -> None:
        runtime = self._current_runtime
        if runtime is not None:
            runtime.loop = value
        else:
            self._orphan_loop = value

    def authorize_token(self, token: str) -> str | None:
        """Return ``control``/``device`` for a valid token, else ``None``.

        Loopback callers never reach this method (the guard short-circuits), so
        it only protects non-local access.  Device tokens are scoped to the
        remote Web UI and expire independently of the break-glass control token.
        """
        if not token:
            return None
        if self.control_token and secrets.compare_digest(token, self.control_token):
            return "control"
        if self.pairing.verify(token) is not None:
            return "device"
        return None

    @property
    def approver(self) -> WebApprover | None:
        runtime = self._current_runtime
        return runtime.approver if runtime is not None else None

    @approver.setter
    def approver(self, value: WebApprover | None) -> None:
        runtime = self._current_runtime
        if runtime is not None:
            runtime.approver = value

    @property
    def question_bridge(self) -> WebQuestionBridge:
        runtime = self._current_runtime
        return runtime.question_bridge if runtime is not None else self._fallback_question_bridge

    @property
    def turn_lock(self) -> threading.Lock:
        runtime = self._current_runtime
        return runtime.turn_lock if runtime is not None else self._fallback_turn_lock

    @property
    def busy(self) -> bool:
        runtime = self._current_runtime
        return bool(runtime.busy) if runtime is not None else False

    @busy.setter
    def busy(self, value: bool) -> None:
        runtime = self._current_runtime
        if runtime is not None:
            runtime.busy = bool(value)

    def runtime_for(self, session_id: str | None = None) -> SessionRuntime | None:
        sid = str(session_id or self.session_id or "")
        if not sid:
            return None
        return self._runtimes.get(sid)

    def get_runtime(self, session_id: str, *, create: bool = True) -> SessionRuntime | None:
        sid = str(session_id or "").strip()
        if not sid:
            return None
        runtime = self._runtimes.get(sid)
        if runtime is None and create:
            runtime = SessionRuntime(session_id=sid)
            self._runtimes[sid] = runtime
        return runtime

    def switch_session(self, session_id: str) -> bool:
        """Switch the current pointer without stopping the outgoing session.

        Returns False when the session is unknown. The old runtime stays in the
        registry and any in-flight stream keeps using its own loop/approver.
        """
        sid = str(session_id or "").strip()
        row = self.store.get_session(sid) if sid else None
        if not row or row.get("workdir") != str(self.workdir):
            return False
        previous = self.session_id
        runtime = self.get_runtime(sid)
        if runtime is None:
            return False
        if runtime.loop is None:
            self.rebuild_loop(session_id=sid)
            return True
        self._current_runtime = runtime
        self.session_id = sid
        if previous and previous != sid:
            self._notify_hook("session_switch", {"from": previous, "to": sid})
        return True

    def drop_runtime(self, session_id: str) -> None:
        sid = str(session_id or "").strip()
        runtime = self._runtimes.pop(sid, None)
        if runtime is not None and self._current_runtime is runtime:
            self._current_runtime = None
            self.session_id = ""

    def pending_for(self, session_id: str) -> dict:
        runtime = self.runtime_for(session_id)
        if runtime is None:
            return {
                "session_id": session_id,
                "approval": None,
                "question": None,
                "busy": False,
                "last_seq": 0,
            }
        approval = runtime.approver.pending_payload() if runtime.approver else None
        question = runtime.question_bridge.pending_payload()
        return {
            "session_id": runtime.session_id,
            "approval": approval,
            "question": question,
            "busy": bool(runtime.busy),
            "last_seq": runtime.next_seq_start(),
        }

    def events_for(self, session_id: str, after_seq: int = 0) -> dict:
        runtime = self.runtime_for(session_id)
        if runtime is None:
            return {
                "session_id": session_id,
                "events": [],
                "reset_required": False,
                "last_seq": 0,
            }
        events, reset = runtime.events_after(after_seq)
        last_seq = int(events[-1].get("seq") or 0) if events else int(
            (runtime.events[-1].get("seq") or 0) if runtime.events else 0
        )
        return {
            "session_id": runtime.session_id,
            "events": events,
            "reset_required": reset,
            "last_seq": last_seq,
        }

    def close(self) -> None:
        """Stop background workers and release owned native resources.

        Idempotent: tests and ``serve_web`` may both call it. The SessionStore is
        owned by the caller and is intentionally left open; the caller decides
        when to close it.
        """
        try:
            self.automations.stop()
        except Exception:  # pragma: no cover - teardown must not raise
            logger.debug("automation scheduler stop failed", exc_info=True)
        try:
            self.queue.stop()
        except Exception:  # pragma: no cover - teardown must not raise
            logger.debug("task queue stop failed", exc_info=True)

        bridge = self.mcp_bridge
        self.mcp_bridge = None
        if bridge is not None:
            try:
                run_coroutine_sync(bridge.close())
            except Exception:  # pragma: no cover - teardown must not raise
                logger.debug("MCP bridge close failed", exc_info=True)

        try:
            self.memory_store.close()
        except Exception:  # pragma: no cover - teardown must not raise
            logger.debug("memory store close failed", exc_info=True)

        # Language servers are child processes. Close every registry owned by
        # this web state so a reload/teardown cannot orphan them.
        loops = [runtime.loop for runtime in self._runtimes.values()]
        if self._orphan_loop is not None:
            loops.append(self._orphan_loop)
        for loop in loops:
            registry = getattr(loop, "registry", None) if loop is not None else None
            close = getattr(registry, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # pragma: no cover - teardown must not raise
                    logger.debug("tool registry close failed", exc_info=True)

    @classmethod
    def close_all(cls) -> None:
        """Close every live state created in this process (used by tests)."""
        with _ACTIVE_STATES_LOCK:
            refs = list(_ACTIVE_STATES)
            _ACTIVE_STATES.clear()
        for ref in refs:
            state = ref()
            if state is not None:
                state.close()

    @staticmethod
    def _detect_env() -> dict:
        from spark.tools.shell import detect_env

        try:
            return detect_env(Path.cwd())
        except Exception:
            # 环境检测失败不影响状态页
            logger.debug("环境检测失败，返回空字典", exc_info=True)
            return {}

    def boot_mcp(self) -> list[str]:
        if self.mcp_bridge is not None:
            run_coroutine_sync(self.mcp_bridge.close())
            self.mcp_bridge = None
        if not self.cfg.mcp_servers:
            return []
        bridge = McpBridge()
        dummy = ToolRegistry(ToolContext(sandbox=self.sandbox, config=self.cfg))
        run_coroutine_sync(bridge.start(self.cfg.mcp_servers, dummy))
        self.mcp_bridge = bridge
        return list(bridge.errors)

    def prepare_mcp(self, servers: list) -> McpBridge | None:
        if not servers:
            return None
        bridge = McpBridge()
        try:
            registry = ToolRegistry(ToolContext(sandbox=self.sandbox, config=self.cfg))
            run_coroutine_sync(bridge.start(servers, registry))
            if bridge.startup_errors:
                raise RuntimeError("MCP server startup failed")
            return bridge
        except BaseException:  # aqg: top-level boundary — cleanup then re-raise
            try:
                run_coroutine_sync(bridge.close())
            except Exception:  # aqg: top-level boundary — cleanup must not mask the original
                logger.warning("Failed to close candidate MCP bridge", exc_info=True)
            raise

    def _ensure_checkpointer(self):
        """One persistent turn checkpointer per process (A10 crash recovery).

        Turns run on the process-wide runtime loop, so a single aiosqlite
        connection can outlive a turn and survive a restart: approvals pending
        at crash time are listed from ``$SPARK_HOME/checkpoints.db`` and can be
        resumed. Falls back to in-memory state when the durable store fails.
        """
        from spark.core.turn_store import persistent_checkpointer

        return persistent_checkpointer()

    def _notify_hook(self, event: str, payload: dict) -> None:
        """Best-effort lifecycle notification (never breaks the request)."""
        try:
            loop = self.loop
            if loop is not None:
                loop._notify_safe(event, payload)
        except Exception:
            logger.debug("hook notify failed: %s", event, exc_info=True)

    def _prepare_runtime(self, runtime: SessionRuntime) -> None:
        """Build a loop for one runtime without touching the current pointer.

        Used by ``rebuild_loop`` (which then selects it) and by the isolated
        session API used by IDE clients (which must not steal the Web UI's
        current session while the user is chatting in both surfaces).
        """
        # The approver is per-session: a background stream must keep its own
        # approval rendezvous after the user switches away. Full-auto removes
        # it for this runtime only.
        if self.cfg.agent.approval == "full-auto":
            runtime.approver = None
        elif runtime.approver is None:
            runtime.approver = WebApprover()
        runtime.loop = self._build_loop(runtime)

    def ensure_runtime(self, session_id: str) -> SessionRuntime | None:
        """Return a runtime with a live loop, creating it without switching."""
        sid = str(session_id or "").strip()
        if not sid:
            return None
        runtime = self.get_runtime(sid)
        if runtime is None:
            return None
        if runtime.loop is None:
            self._prepare_runtime(runtime)
        return runtime

    def rebuild_loop(
        self, session_id: str | None = None, reuse_latest: bool = False
    ) -> None:
        previous_session = getattr(self, "session_id", "")
        if session_id is None and reuse_latest:
            latest = next(
                (
                    row
                    for row in self.store.list_sessions()
                    if row["workdir"] == str(self.workdir)
                ),
                None,
            )
            session_id = latest["id"] if latest else None
        if session_id is None:
            session_id = self.store.create_session(
                self.workdir, self.cfg.provider.model, title="新对话"
            )
        runtime = self.get_runtime(session_id)
        if runtime is None:  # pragma: no cover - get_runtime(create=True) always returns
            return

        self._prepare_runtime(runtime)
        self._current_runtime = runtime
        self.session_id = session_id
        if previous_session and previous_session != session_id:
            self._notify_hook(
                "session_switch",
                {"from": previous_session, "to": session_id},
            )

    def _build_loop(self, runtime: SessionRuntime) -> AgentLoop:
        """Build a loop bound to one runtime (no current-pointer side effects)."""
        tool_ctx = ToolContext(sandbox=self.sandbox, config=self.cfg)
        tool_ctx.question_asker = runtime.question_bridge.ask
        registry = ToolRegistry(tool_ctx)
        if self.mcp_bridge is not None:
            self.mcp_bridge.register_into(registry)
            tool_ctx.mcp_call = self.mcp_bridge.call
            tool_ctx.mcp_bridge = self.mcp_bridge
        provider = create_provider(self.cfg)
        if self.mcp_bridge is not None:
            # P2：server 反向 sampling 以当前 Provider 为大脑
            self.mcp_bridge.set_sampling_handler(provider)
        return AgentLoop(
            workdir=self.workdir,
            cfg=self.cfg,
            provider=provider,
            registry=registry,
            store=self.store,
            session_id=runtime.session_id,
            approver=runtime.approver,
            memory=self.memory,
            checkpointer=self._ensure_checkpointer(),
        )

    def _effective_approval(self, loop=None) -> str:
        """当前真正生效的审批模式（审批档案可覆盖基线）。

        与 :meth:`AgentLoop._approval_mode` 同一语义；loop 未就绪时回落到配置基线。
        """
        loop = loop or self.loop
        override = getattr(loop, "_approval_override", None) if loop is not None else None
        return str(override or self.cfg.agent.approval)

    def _queue_is_busy(self) -> bool:
        """A queued prompt must not compete with any live turn."""
        if self._current_runtime is not None and self._current_runtime.busy:
            return True
        return any(runtime.busy for runtime in self._runtimes.values())

    def _run_queued_task(self, task: dict) -> QueueOutcome:
        """Execute one queue row in its own session, never the user's current one."""
        prompt = str(task.get("prompt") or "").strip()
        if not prompt:
            return QueueOutcome(status="failed", error="empty prompt")
        workdir = Path(str(task.get("workdir") or self.workdir))
        if workdir.resolve() != Path(self.workdir).resolve():
            return QueueOutcome(
                status="blocked",
                error=f"queue task workdir {workdir} != server workdir {self.workdir}",
            )
        session_id = self.store.create_session(
            workdir,
            self.cfg.provider.model,
            title=str(task.get("title") or "[queue] task")[:120],
        )
        runtime = self.get_runtime(session_id)
        if runtime is None:
            return QueueOutcome(status="failed", error="runtime unavailable")
        runtime.loop = self._build_loop(runtime)
        if self._effective_approval(runtime.loop) == "full-auto":
            runtime.approver = None
        elif runtime.approver is None:
            runtime.approver = WebApprover()
        if not runtime.turn_lock.acquire(blocking=False):
            return QueueOutcome(status="requeue", error="agent busy", session_id=session_id)
        try:
            events = list(runtime.loop.iter_turn_sync(prompt))
        finally:
            runtime.turn_lock.release()
        text = ""
        failed = False
        for event in events:
            kind = getattr(event, "type", "")
            if kind == "turn_end":
                text = (getattr(event, "text", "") or "")[:500]
            elif kind == "turn_error":
                failed = True
                text = (getattr(event, "text", "") or "turn failed")[:500]
        if failed:
            return QueueOutcome(status="failed", error=text, session_id=session_id)
        return QueueOutcome(status="ok", summary=text, session_id=session_id)

    def run_due_automations(self) -> list[dict]:
        """执行到期的定时任务；返回本轮结果摘要。

        每个无人值守任务使用独立的 ``SessionRuntime``：不再临时改
        ``self.session_id`` 欺骗当前 loop，也不会和用户正在跑的会话抢同一个
        turn_lock。调度线程拿不到该 runtime 的锁就跳过本拍。
        """
        results: list[dict] = []
        for task in self.automations.run_due():
            aid = str(task.get("id") or "")
            if not aid:
                continue
            runtime = None
            acquired = False
            try:
                session_id = self.store.create_session(
                    self.workdir,
                    self.cfg.provider.model,
                    title=f"[auto] {task.get('name') or aid}",
                )
                runtime = self.get_runtime(session_id)
                if runtime is None:
                    self.store.mark_automation_result(
                        aid, status="failed", error="runtime unavailable"
                    )
                    results.append({"id": aid, "status": "failed"})
                    continue
                if self.cfg.agent.approval == "full-auto":
                    runtime.approver = None
                elif runtime.approver is None:
                    runtime.approver = WebApprover()
                runtime.loop = self._build_loop(runtime)
                if not runtime.turn_lock.acquire(blocking=False):
                    self.store.mark_automation_result(
                        aid, status="skipped (agent busy)", skipped=True
                    )
                    results.append({"id": aid, "status": "skipped"})
                    continue
                acquired = True
                try:
                    events = list(
                        runtime.loop.iter_turn_sync(str(task.get("prompt") or ""))
                    )
                finally:
                    runtime.turn_lock.release()
                    acquired = False
                text = ""
                for event in events:
                    if getattr(event, "type", "") == "turn_end":
                        text = (getattr(event, "text", "") or "")[:200]
                    elif getattr(event, "type", "") == "turn_error":
                        text = f"error: {getattr(event, 'text', '')}"[:200]
                self.store.mark_automation_result(
                    aid, status="ok", session_id=session_id
                )
                results.append({"id": aid, "status": "ok", "summary": text})
            except Exception as exc:  # aqg: top-level boundary
                logger.warning("automation %s failed", aid, exc_info=True)
                self.store.mark_automation_result(
                    aid, status="failed", error=str(exc)
                )
                results.append({"id": aid, "status": "failed"})
                if acquired and runtime is not None:
                    runtime.turn_lock.release()
                    acquired = False
        return results

    def status(self, session_id: str | None = None) -> dict:
        # 凭据可来自配置或环境变量（SPARK_* / DEEPSEEK_API_KEY 等）：只读配置字段
        # 会把"环境变量供 key"的部署误显示成未配置。
        key = env_api_key(self.cfg) or self.cfg.provider.api_key or ""
        sid = str(session_id or self.session_id or "")
        runtime = self.runtime_for(sid) if session_id else self._current_runtime
        loop = runtime.loop if runtime is not None else self.loop
        usage = {
            "used": 0,
            "limit": self.cfg.context.max_context_tokens,
            "percent": 0.0,
        }
        plan: list[dict] = []
        if loop is not None:
            try:
                usage = loop._usage()
            except Exception:
                # usage 查询失败时降级为 0
                logger.debug("查询 loop usage 失败", exc_info=True)
            plan = list(loop.registry.plan)
        session_tokens = self.store.session_token_usage(
            sid, model=self.cfg.provider.model
        )
        session_cost_usd = session_tokens.get("cost_usd")
        cost_limit = float(getattr(self.cfg.agent, "max_session_cost_usd", 0) or 0)
        return {
            "session_id": sid,
            "session_tokens": session_tokens,
            "session_cost": {
                "spent_usd": session_cost_usd,
                "budget_usd": cost_limit or None,
                "remaining_usd": (
                    round(max(0.0, cost_limit - float(session_cost_usd)), 6)
                    if cost_limit and isinstance(session_cost_usd, (int, float))
                    else None
                ),
                "by_model": self.store.session_usage_by_model(sid),
            },
            "routing": self.cfg.routing_decision,
            "workdir": str(self.workdir),
            "sandbox_mode": self.cfg.agent.sandbox_mode,
            "display": {
                "show_thinking": self.cfg.agent.show_thinking,
                "show_tools": self.cfg.agent.show_tools,
                "show_plan": self.cfg.agent.show_plan,
                "show_context": self.cfg.agent.show_context,
                "show_keywords": self.cfg.agent.show_keywords,
                "show_notices": self.cfg.agent.show_notices,
            },
            "env": self.env_info,
            "model": self.cfg.provider.model,
            "provider": self.cfg.provider.name,
            # 生效值，不是基线值：激活的审批档案会覆盖 agent.approval
            # （见 AgentLoop._approval_mode）。此前这里读基线，档案生效时
            # UI 会显示错误的审批模式。
            "approval": self._effective_approval(loop),
            "approval_base": self.cfg.agent.approval,
            "base_url": self.cfg.provider.base_url,
            "api_key_masked": mask_secret(key),
            "has_api_key": bool(key),
            "mcp_servers": [s.name for s in self.cfg.mcp_servers],
            "mcp_errors": list(self.mcp_bridge.errors) if self.mcp_bridge else [],
            "context": usage,
            "plan": plan,
            "last_error": self.store.get_session_error(sid),
            "model_status": self.model_status(loop),
            **self._demo_flags(key, loop),
        }

    def model_preflight(self, loop=None):
        """Return a structured config issue, or None when a real turn can start."""
        loop = loop or self.loop
        live = loop.provider if loop is not None else None
        return provider_preflight(self.cfg, live)

    def model_status(self, loop=None) -> dict:
        issue = self.model_preflight(loop)
        if issue is None:
            return {"ready": True, "issue": None}
        return {"ready": False, "issue": issue.to_dict()}

    def _demo_flags(self, key: str, loop=None) -> dict:
        """Say out loud when the configured provider silently fell back to mock.

        ``create_provider`` degrades to ``MockProvider`` when an openai_compat
        provider has no key, which kept the UI looking like a live model was
        answering while every reply was scripted. The flag is derived from the
        loop's actual provider instance, not from config, so it cannot drift.
        """
        provider_name = str(self.cfg.provider.name)
        loop = loop or self.loop
        live = loop.provider if loop is not None else None
        is_mock = live is not None and live.__class__.__name__ == "MockProvider"
        if provider_name == "mock":
            return {"demo_mode": True, "demo_reason": "当前使用 Mock 演示模型"}
        if is_mock:
            env_name = self.cfg.provider.api_key_env or "SPARK_API_KEY"
            return {
                "demo_mode": True,
                "demo_reason": f"缺少 API Key（{env_name}），已退回 Mock 演示模型",
            }
        if provider_name == "openai_compat" and not key:
            return {"demo_mode": True, "demo_reason": "缺少 API Key，尚未连接真实模型"}
        return {"demo_mode": False, "demo_reason": ""}

    def ensure_default_profile(self) -> None:
        with self.settings_lock:
            if self.cfg.model_profiles:
                return
            if not self.cfg.provider.model:
                return
            self.cfg.model_profiles = [
                ModelProfile(
                    id=uuid.uuid4().hex[:8],
                    name=self.cfg.provider.model,
                    provider=self.cfg.provider.name,
                    base_url=self.cfg.provider.base_url,
                    model=self.cfg.provider.model,
                    api_key=self.cfg.provider.api_key or "",
                    api_key_env=self.cfg.provider.api_key_env,
                    api_keys=self.cfg.provider.api_keys.copy(),
                    fallback_models=self.cfg.provider.fallback_models.copy(),
                    small_model=self.cfg.provider.small_model,
                )
            ]
            self.cfg.active_profile_id = self.cfg.model_profiles[0].id
            save_config(self.cfg)

    def full_config(self) -> dict:
        return {
            "workdir": str(self.workdir),
            "provider": {
                "name": self.cfg.provider.name,
                "base_url": self.cfg.provider.base_url,
                "model": self.cfg.provider.model,
                "api_key_env": self.cfg.provider.api_key_env,
                "api_key_masked": mask_secret(self.cfg.provider.api_key or ""),
                "has_api_key": bool(self.cfg.provider.api_key),
                # 供应商韧性：凭据池按掩码回显，重新提交即整体替换
                "api_keys": [mask_secret(k) for k in self.cfg.provider.api_keys],
                "api_keys_count": len(self.cfg.provider.api_keys),
                "fallback_models": list(self.cfg.provider.fallback_models),
                "small_model": self.cfg.provider.small_model,
            },
            "agent": {
                # 基线值（设置表单编辑的就是它）；生效值见 effective_approval，
                # 两者不同说明有审批档案在覆盖。
                "approval": self.cfg.agent.approval,
                "effective_approval": self._effective_approval(),
                "workdir_only": self.cfg.agent.workdir_only,
                "sandbox_mode": self.cfg.agent.sandbox_mode,
                "protected_paths": list(self.cfg.agent.protected_paths),
                "shell_timeout_sec": self.cfg.agent.shell_timeout_sec,
                "max_tool_rounds": self.cfg.agent.max_tool_rounds,
                "max_repeat_calls": self.cfg.agent.max_repeat_calls,
                "max_turn_tokens": self.cfg.agent.max_turn_tokens,
                "max_session_cost_usd": self.cfg.agent.max_session_cost_usd,
                "max_output_chars": self.cfg.agent.max_output_chars,
                "effort": self.cfg.agent.effort,
                "system_prompt": self.cfg.agent.system_prompt,
                # 安全相关开关：只回显，写入仍走 /api/settings 的 agent 节。
                "auto_review": self.cfg.agent.auto_review,
                "network_access": self.cfg.agent.network_access,
                "format_on_write": self.cfg.agent.format_on_write,
                "extra_dirs": list(self.cfg.agent.extra_dirs),
                "show_thinking": self.cfg.agent.show_thinking,
                "show_tools": self.cfg.agent.show_tools,
                "show_plan": self.cfg.agent.show_plan,
                "show_context": self.cfg.agent.show_context,
                "show_keywords": self.cfg.agent.show_keywords,
                "show_notices": self.cfg.agent.show_notices,
            },
            "context": {
                "agents_md": self.cfg.context.agents_md,
                "max_fragment_chars": self.cfg.context.max_fragment_chars,
                "history_budget_chars": self.cfg.context.history_budget_chars,
                "max_context_tokens": self.cfg.context.max_context_tokens,
                "compact_threshold": self.cfg.context.compact_threshold,
                "keep_recent_messages": self.cfg.context.keep_recent_messages,
                # The Project tab edits these; without them in the response the
                # inputs read undefined and the toggle silently never persisted.
                "repo_map_enabled": self.cfg.context.repo_map_enabled,
                "repo_map_max_chars": self.cfg.context.repo_map_max_chars,
                # 知识库页可改；此前只在 /api/knowledge 局部暴露，设置保存后回读
                # 拿不到值，刷新即显示默认。
                "knowledge_enabled": self.cfg.context.knowledge_enabled,
                "knowledge_top_k": self.cfg.context.knowledge_top_k,
                "knowledge_max_chars": self.cfg.context.knowledge_max_chars,
            },
            "memory": {
                "enabled": self.cfg.memory.enabled,
                "top_k": self.cfg.memory.top_k,
                "capacity": self.cfg.memory.capacity,
                "embedding_model": self.cfg.memory.embedding_model,
                "auto_extract": self.cfg.memory.auto_extract,
                # 嵌入/重排：密钥只回掩码，明文永不离开后端。
                "embedding_enabled": self.cfg.memory.embedding_enabled,
                "embedding_base_url": self.cfg.memory.embedding_base_url,
                "embedding_api_key_masked": mask_secret(self.cfg.memory.embedding_api_key),
                "has_embedding_api_key": bool(self.cfg.memory.embedding_api_key),
                "rerank_model": self.cfg.memory.rerank_model,
            },
            "replay": {"enabled": bool(self.cfg.replay.enabled)},
            "updater": {"mode": self.cfg.updater.mode},
            # 只读：写路径尚未开放（见 docs/API.md「permission_rules /
            # approval_profiles 写接口设计」），先把现状如实回显给 UI。
            "permission_rules": [
                {"action": rule.action, "resource": rule.resource, "tool": rule.tool}
                for rule in self.cfg.permission_rules
            ],
            "approval_profiles": [
                {
                    "id": profile.id,
                    "name": profile.name,
                    "approval": profile.approval,
                    "tools": list(profile.tools),
                    "system_prompt": profile.system_prompt,
                    "model_profile_id": profile.model_profile_id,
                }
                for profile in self.cfg.approval_profiles
            ],
            "active_approval_profile_id": self.cfg.active_approval_profile_id,
            "env_probe": probe_env(),
            "mcp_servers": [
                {
                    "name": s.name,
                    "command": s.command,
                    "args": s.args,
                    "env": {name: MCP_REDACTED_HEADER for name in s.env},
                    "readonly_tools": s.readonly_tools,
                    "transport": s.transport,
                    "allowed_tools": list(s.allowed_tools),
                    "http": (
                        {
                            "url": s.http.url,
                            "headers": redact_mcp_headers(dict(s.http.headers)),
                            "timeout_sec": s.http.timeout_sec,
                        }
                        if s.http
                        else None
                    ),
                }
                for s in self.cfg.mcp_servers
            ],
            "mcp_errors": list(self.mcp_bridge.errors) if self.mcp_bridge else [],
            "hooks": [
                {
                    "event": h.event,
                    "command": h.command,
                    "args": list(h.args),
                    "name": h.name,
                    "timeout_sec": h.timeout_sec,
                    # 回传 on_deny，否则 UI 保存时会把 warn 重置为 block。
                    "on_deny": h.on_deny,
                }
                for h in self.cfg.hooks
            ],
        }
