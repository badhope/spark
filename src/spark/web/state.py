from __future__ import annotations

import asyncio
import importlib
import logging
import secrets
import threading
import time
import uuid
import weakref
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
from spark.providers.factory import create_provider
from spark.sandbox import WorkdirSandbox
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


class WebApprover:
    """Bridges approval prompts from the agent loop thread to HTTP handlers."""

    def __init__(self) -> None:
        self.pending = False
        self._event = threading.Event()
        self._cancel_event = threading.Event()
        self._action: ApprovalAction = "deny"
        self._tool_call_id = ""

    async def __call__(self, request) -> ApprovalDecision:
        self.pending = True
        self._action = "deny"
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


def _generate_session_meta_async(
    state: SparkWebState, user_text: str, assistant_text: str
) -> None:
    """After a turn completes, ask the model for a short title + keywords and store them."""

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
                state.store.set_title(state.session_id, title)
            if keywords:
                state.store.set_keywords(state.session_id, " ".join(keywords))
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
        self.loop: AgentLoop | None = None
        self.turn_lock = threading.Lock()
        self.settings_lock = threading.Lock()
        self.busy = False
        self.approver = WebApprover() if cfg.agent.approval != "full-auto" else None
        self.question_bridge = WebQuestionBridge()
        self.mcp_bridge: McpBridge | None = None
        self.control_token = secrets.token_urlsafe(32)
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
        self.boot_mcp()
        self.rebuild_loop(reuse_latest=True)
        # Register before starting the thread: if start() ever raises, the
        # teardown registry can still stop/close this instance.
        with _ACTIVE_STATES_LOCK:
            _ACTIVE_STATES.append(weakref.ref(self))
        self.automations.start()

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

    def rebuild_loop(
        self, session_id: str | None = None, reuse_latest: bool = False
    ) -> None:
        tool_ctx = ToolContext(sandbox=self.sandbox, config=self.cfg)
        tool_ctx.question_asker = self.question_bridge.ask
        registry = ToolRegistry(tool_ctx)
        if self.mcp_bridge is not None:
            self.mcp_bridge.register_into(registry)
            tool_ctx.mcp_call = self.mcp_bridge.call
            tool_ctx.mcp_bridge = self.mcp_bridge
        provider = create_provider(self.cfg)
        if self.mcp_bridge is not None:
            # P2：server 反向 sampling 以当前 Provider 为大脑
            self.mcp_bridge.set_sampling_handler(provider)
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
        # Capture the outgoing session *before* reassigning: reading it later
        # always returned the new id, so session_switch hooks never fired and
        # the fork/switch handlers reported the old session in their response.
        previous_session = getattr(self, "session_id", "")
        if session_id is None:
            self.session_id = self.store.create_session(
                self.workdir, self.cfg.provider.model, title="新对话"
            )
        else:
            self.session_id = session_id
        # The approver is created once (in __init__ only when the startup mode
        # was not full-auto). Reusing that decision meant switching full-auto →
        # suggest left approver=None, so every write/shell call was silently
        # denied: the UI promised approvals that could never appear.
        if self.cfg.agent.approval == "full-auto":
            approver = None
        else:
            if self.approver is None:
                self.approver = WebApprover()
            approver = self.approver
        self.loop = AgentLoop(
            workdir=self.workdir,
            cfg=self.cfg,
            provider=provider,
            registry=registry,
            store=self.store,
            session_id=self.session_id,
            approver=approver,
            memory=self.memory,
            checkpointer=self._ensure_checkpointer(),
        )
        if previous_session and previous_session != self.session_id:
            self._notify_hook(
                "session_switch",
                {"from": previous_session, "to": self.session_id},
            )

    def _effective_approval(self) -> str:
        """当前真正生效的审批模式（审批档案可覆盖基线）。

        与 :meth:`AgentLoop._approval_mode` 同一语义；loop 未就绪时回落到配置基线。
        """
        loop = self.loop
        override = getattr(loop, "_approval_override", None) if loop is not None else None
        return str(override or self.cfg.agent.approval)

    def run_due_automations(self) -> list[dict]:
        """执行到期的定时任务；返回本轮结果摘要。

        与用户回合共用 ``turn_lock``：无人值守任务绝不能和正在进行的对话
        并发跑同一个 loop（会话状态、审批队列、文件快照都会互相踩）。
        拿不到锁就标记 skipped 并留到下一拍，而不是排队堆积。
        """
        results: list[dict] = []
        for task in self.automations.run_due():
            aid = str(task.get("id") or "")
            if not aid:
                continue
            if not self.turn_lock.acquire(blocking=False):
                self.store.mark_automation_result(
                    aid, status="skipped (agent busy)", skipped=True
                )
                results.append({"id": aid, "status": "skipped"})
                continue
            try:
                if self.loop is None:
                    self.store.mark_automation_result(
                        aid, status="failed", error="agent not ready"
                    )
                    results.append({"id": aid, "status": "failed"})
                    continue
                session_id = self.store.create_session(
                    self.workdir, self.cfg.provider.model,
                    title=f"[auto] {task.get('name') or aid}",
                )
                previous = self.session_id
                self.session_id = session_id
                try:
                    events = list(self.loop.iter_turn_sync(str(task.get("prompt") or "")))
                finally:
                    self.session_id = previous
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
            finally:
                self.turn_lock.release()
        return results

    def status(self) -> dict:
        # 凭据可来自配置或环境变量（SPARK_* / DEEPSEEK_API_KEY 等）：只读配置字段
        # 会把"环境变量供 key"的部署误显示成未配置。
        key = env_api_key(self.cfg) or self.cfg.provider.api_key or ""
        usage = {
            "used": 0,
            "limit": self.cfg.context.max_context_tokens,
            "percent": 0.0,
        }
        plan: list[dict] = []
        if self.loop is not None:
            try:
                usage = self.loop._usage()
            except Exception:
                # usage 查询失败时降级为 0
                logger.debug("查询 loop usage 失败", exc_info=True)
            plan = list(self.loop.registry.plan)
        return {
            "session_id": self.session_id,
            "session_tokens": self.store.session_token_usage(
                self.session_id, model=self.cfg.provider.model
            ),
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
            "approval": self._effective_approval(),
            "approval_base": self.cfg.agent.approval,
            "base_url": self.cfg.provider.base_url,
            "api_key_masked": mask_secret(key),
            "has_api_key": bool(key),
            "mcp_servers": [s.name for s in self.cfg.mcp_servers],
            "mcp_errors": list(self.mcp_bridge.errors) if self.mcp_bridge else [],
            "context": usage,
            "plan": plan,
            **self._demo_flags(key),
        }

    def _demo_flags(self, key: str) -> dict:
        """Say out loud when the configured provider silently fell back to mock.

        ``create_provider`` degrades to ``MockProvider`` when an openai_compat
        provider has no key, which kept the UI looking like a live model was
        answering while every reply was scripted. The flag is derived from the
        loop's actual provider instance, not from config, so it cannot drift.
        """
        provider_name = str(self.cfg.provider.name)
        live = self.loop.provider if self.loop is not None else None
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
