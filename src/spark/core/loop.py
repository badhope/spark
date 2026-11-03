from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from importlib.resources import files
from pathlib import Path
from stat import S_ISREG
from typing import TYPE_CHECKING, Any, cast

from spark.config import ApprovalMode, SparkConfig
from spark.core.context import (
    build_messages,
    invalidate_system_prompt_cache,
    load_agents_md,
    system_prompt,
)
from spark.core.tokens import estimate_message_tokens, estimate_tool_overhead
from spark.hooks import HookRegistry
from spark.models import (
    ApprovalDecision,
    ApprovalRequest,
    ChatMessage,
    ToolCall,
    ToolResult,
    TurnEvent,
)
from spark.policy import (
    NEVER_PERSIST_ALLOW,
    READONLY_TOOLS,
    SHELL_TOOLS,
    WRITE_TOOLS,
    call_signature,
    decide,
)
from spark.providers.base import Provider
from spark.sandbox import safe_bare_filename
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry

if TYPE_CHECKING:  # pragma: no cover - 仅类型引用
    from spark.core.turn_store import MemoryTurnCheckpointer, TurnCheckpointer

Approver = Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]

logger = logging.getLogger(__name__)


class AgentLoop:
    def __init__(
        self,
        *,
        workdir: Path,
        cfg: SparkConfig,
        provider: Provider,
        registry: ToolRegistry,
        store: SessionStore,
        session_id: str,
        approver: Approver | None = None,
        memory=None,
        checkpointer: "TurnCheckpointer | MemoryTurnCheckpointer | None" = None,
    ) -> None:
        self.workdir = workdir
        self.cfg = cfg
        self.provider = provider
        self.registry = registry
        self.store = store
        self.session_id = session_id
        self.approver = approver
        self.memory = memory
        # turn 级检查点（A10）：主会话可注入持久 TurnCheckpointer；
        # 缺省进程内 Memory（子代理/短生命周期场景），审批挂起仍可工作。
        if checkpointer is None:
            from spark.core.turn_store import MemoryTurnCheckpointer

            checkpointer = MemoryTurnCheckpointer()
        self._checkpointer = checkpointer
        self.allow_always: set[str] = set()
        self.cancelled = False
        # approve-plan 模式：计划已产出并经用户批准后才自动执行写/执行工具
        self._plan_approved: bool | None = False
        self.registry.ctx.task_runner = self.spawn_subtask
        self.registry.ctx.task_runner_parallel = self.spawn_subtasks_parallel
        self.history: list[ChatMessage] = store.load_messages(session_id)
        self._history_source = self.history
        self._history_tokens = self._calculate_history_tokens(self.history)
        self._history_length = len(self.history)
        self._history_tail = self.history[-1] if self.history else None
        self._system_prompt_resource = files("spark.prompts").joinpath("system.md")
        self._system_prompt_cache_key: tuple | None = None
        self._system_prompt_tokens = 0
        self._agents_cache_key: tuple = ()
        self._agents_tokens = 0
        self._tool_schema_revision = -1
        self._tool_overhead_tokens = 0
        self._call_window: deque[str] = deque(maxlen=self._breaker_window())
        self._call_counts: dict[str, int] = {}
        # (tool_call.id, name, arguments) → verdict, so an interrupted approve
        # node cannot re-run hooks on resume. The id alone is not a safe key:
        # openai_compat falls back to "call_0" when a provider omits ids, so two
        # different tools in one round would share a verdict — and a stale
        # "allow" from a read-only tool would silently authorise a write.
        self._classification_cache: dict[str, dict[str, Any]] = {}
        self._turn_tokens = 0
        # P2 上下文合同：turn 级 usage 聚合 + 自动检查点 id（replay flush 用）
        self._turn_usage_totals: dict[str, int | str] | None = None
        self._last_checkpoint_id: int | None = None
        self._turn_graph = None  # LangGraph 状态机（首次 iter_turn 编译缓存）
        self._last_round: dict[str, Any] = {"tool_calls": [], "text": "", "error": None}
        self._otel_span: Any = None  # 当前 turn 的观测 span（trace=turn）
        self._active_children: list[AgentLoop] = []
        # P1-4: 声明式子代理的专属 system prompt（body 注入 system 槽，而非用户消息）
        self._extra_system_prompt: str = ""
        # H4：审批档案覆盖（不突变 cfg，重启/切档案语义一致）
        self._approval_override: str | None = None
        self._apply_approval_profile(cfg)
        # Provider 协议为鸭子类型：测试与第三方 provider 可能不接收 effort 参数。
        # 探测一次签名，仅在支持时透传 effort（保持向后兼容）。
        self._stream_extra: dict[str, str] = {}
        if self.provider is not None:
            try:
                import inspect

                sig = inspect.signature(self.provider.stream)
                if "effort" in sig.parameters:
                    self._stream_extra = {"effort": getattr(self.cfg.agent, "effort", "medium")}
            except (TypeError, ValueError, AttributeError):
                self._stream_extra = {}
        self.hooks = HookRegistry.from_config(
            [hook.model_dump() for hook in getattr(cfg, "hooks", [])], workdir
        )
        # Lazy: spark.core.compact pulls litellm + llama_index (~1.7 s of import
        # time) and is only needed when a turn actually has to compact.
        self._compactor: Any = None
        # --- lifecycle hooks (best-effort) ---
        self._notify_safe("app_start", {"workdir": str(workdir)})
        if len(self.history) == 0:
            self._notify_safe("session_create", {"session_id": session_id})
        else:
            self._notify_safe("session_load", {"session_id": session_id})

    def _notify_safe(self, event: str, payload: dict) -> None:
        """Fire hook event; swallows all errors to protect the main loop."""
        try:
            self.hooks.notify(event, payload)
        except Exception:
            # hook 失败不应阻塞主循环，但需保留调试信息
            logger.warning("hook %s 执行失败", event, exc_info=True)

    def _knowledge_context(self, query: str) -> str | None:
        """按本轮用户输入检索项目知识库；任何异常都降级为「无知识库」。"""
        try:
            if not getattr(self.cfg.context, "knowledge_enabled", True):
                return None
            from spark.config import ensure_home
            from spark.knowledge import KnowledgeBase, build_context_block, project_key

            kb = KnowledgeBase(ensure_home() / "knowledge" / project_key(self.workdir))
            if not kb.docs():
                return None
            hits = kb.search(
                query,
                top_k=int(getattr(self.cfg.context, "knowledge_top_k", 6)),
            )
            if not hits:
                return None
            block = build_context_block(hits, max_chars=self.cfg.context.knowledge_max_chars)
            return block or None
        except Exception as exc:  # noqa: BLE001 — 知识库失败不能影响对话
            logger.debug("Knowledge retrieve failed: %s", exc)
            return None

    def _end_otel_turn(self, started: float, *, resumed: bool) -> None:
        """turn 终态观测：补 usage/延迟 attributes 并结束 span（NoOp 安全）。"""
        from spark.otel import end_turn_span

        span = self._otel_span
        self._otel_span = None
        if span is None:
            return
        reported = getattr(self, "_reported_usage", None)
        if reported:
            in_tok = int(reported.get("prompt_tokens") or 0)
            out_tok = int(reported.get("completion_tokens") or 0)
            reasoning_tok = int(reported.get("reasoning_tokens") or 0)
            source = "reported"
        else:
            in_tok, out_tok, reasoning_tok = self._turn_tokens, 0, 0
            source = "estimated"
        end_turn_span(
            span,
            input_tokens=in_tok,
            output_tokens=out_tok,
            reasoning_tokens=reasoning_tok,
            latency_ms=int((time.monotonic() - started) * 1000),
            source=source,
        )

    def cancel(self) -> None:
        self.cancelled = True
        for child in list(self._active_children):
            child.cancel()

    def _breaker_window(self) -> int:
        """Window must hold at least max_repeat_calls+1 entries, or the breaker
        can never observe a repeat when the limit exceeds the window size."""
        limit = int(getattr(self.cfg.agent, "max_repeat_calls", 0) or 0)
        return max(32, limit + 1)

    def _fingerprint(self, call: ToolCall) -> str:
        payload = json.dumps(
            {"name": call.name, "arguments": call.arguments},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]

    def _record_call(self, call: ToolCall) -> tuple[str, int] | None:
        """Track a tool-call signature; return the over-limit signature when the
        same (name, arguments) pair repeats past max_repeat_calls. 0 disables the guard."""
        limit = getattr(self.cfg.agent, "max_repeat_calls", 0) or 0
        if limit <= 0:
            return None
        fingerprint = self._fingerprint(call)
        if len(self._call_window) == self._call_window.maxlen:
            required = self._breaker_window()
            if required != self._call_window.maxlen:
                self._call_window = deque(self._call_window, maxlen=required)
        self._call_window.append(fingerprint)
        count = sum(1 for item in self._call_window if item == fingerprint)
        self._call_counts[fingerprint] = count
        if count > limit:
            return fingerprint, count
        return None

    def _charge_tokens(self, text_parts: list[str], reasoning_parts: list[str]) -> int:
        added = estimate_message_tokens(
            ChatMessage(role="assistant", content="".join(text_parts) or None)
        ) + estimate_message_tokens(
            ChatMessage(role="assistant", content="".join(reasoning_parts) or None)
        )
        self._turn_tokens += added
        return added

    def _persist_usage(
        self,
        message_id: int | None,
        messages: list[ChatMessage],
        text_parts: list[str],
        reasoning_parts: list[str],
    ) -> None:
        """Write per-turn token spend into the SQLite audit table.

        P1：优先使用 provider 流式上报的真实 usage（``source="reported"``，
        来自 ChatDelta(type="end", usage=...)）；未上报时回退启发式估算
        （``source="estimated"``）。
        """
        try:
            input_est = sum(estimate_message_tokens(m) for m in messages)
            output_est = estimate_message_tokens(
                ChatMessage(role="assistant", content="".join(text_parts) or None)
            )
            reasoning_est = estimate_message_tokens(
                ChatMessage(role="assistant", content="".join(reasoning_parts) or None)
            )
            source = "estimated"
            ru = getattr(self, "_reported_usage", None)
            if ru:
                in_tok = int(ru.get("prompt_tokens") or 0)
                out_tok = int(ru.get("completion_tokens") or 0)
                reasoning_tok = int(ru.get("reasoning_tokens") or 0)
                if in_tok or out_tok or reasoning_tok:
                    input_est, output_est, reasoning_est = in_tok, out_tok, reasoning_tok
                    source = "reported"
            self.store.append_token_usage(
                self.session_id,
                message_id,
                input_est,
                output_est,
                reasoning_est,
                source=source,
            )
            # P2 replay：turn 级聚合（replay flush 用，失败不影响主流程）
            totals = getattr(self, "_turn_usage_totals", None)
            if totals is not None:
                try:
                    totals["input_tokens"] = int(totals.get("input_tokens", 0)) + int(input_est)
                    totals["output_tokens"] = int(totals.get("output_tokens", 0)) + int(output_est)
                    totals["reasoning_tokens"] = int(totals.get("reasoning_tokens", 0)) + int(reasoning_est)
                    if source == "reported":
                        totals["source"] = "reported"
                except (TypeError, ValueError):
                    pass
        except Exception:
            logger.debug("persist token usage failed", exc_info=True)

    def _budget_exceeded(self) -> bool:
        limit = getattr(self.cfg.agent, "max_turn_tokens", 0) or 0
        return limit > 0 and self._turn_tokens >= limit

    def _budget_state(self) -> dict:
        limit = getattr(self.cfg.agent, "max_turn_tokens", 0) or 0
        return {
            "turn_tokens": self._turn_tokens,
            "turn_token_limit": limit or None,
        }

    async def run(self, user_text: str, images=None) -> list[TurnEvent]:
        self.cancelled = False
        events: list[TurnEvent] = []
        async for event in self.iter_turn(user_text, images=images):
            events.append(event)
        return events

    def iter_turn_sync(self, user_text: str, images=None):
        """Drive a turn from a non-async thread.

        Runs on the process-wide :class:`~spark.core.runtime.AgentRuntime` loop
        rather than a fresh loop per turn: the turn checkpointer's SQLite
        connection is bound to its constructing loop, and creating a new loop
        per turn made a persistent checkpointer impossible ("Future attached to
        a different loop"), which is why crash recovery silently degraded to
        in-memory state.
        """
        from spark.core.runtime import get_runtime

        self.cancelled = False
        yield from get_runtime().iterate(self.iter_turn(user_text, images=images))

    async def iter_turn_managed(self, user_text: str, images=None):
        """Drive a turn from a surface that owns its own asyncio loop (TUI/ACP).

        The turn body still runs on the shared runtime loop (where the
        checkpointer's SQLite connection lives); this coroutine only forwards
        events, so the caller's loop is never blocked by agent work.
        """
        from spark.core.runtime import get_runtime

        self.cancelled = False
        async for event in get_runtime().iterate_async(
            self.iter_turn(user_text, images=images)
        ):
            yield event

    @staticmethod
    def _calculate_history_tokens(history: list[ChatMessage]) -> int:
        return sum(
            estimate_message_tokens(message)
            for message in history
            if message.role != "system"
        )

    def _set_history_cache(self) -> None:
        self._history_source = self.history
        self._history_tokens = self._calculate_history_tokens(self.history)
        self._history_length = len(self.history)
        self._history_tail = self.history[-1] if self.history else None

    def _sync_history_tokens(self) -> None:
        tail = self.history[-1] if self.history else None
        if (
            self.history is self._history_source
            and len(self.history) == self._history_length
            and tail is self._history_tail
        ):
            return
        self._set_history_cache()

    def _replace_history(self, history: list[ChatMessage]) -> None:
        self.history = history
        self._set_history_cache()

    def _append_history(self, message: ChatMessage) -> None:
        self._sync_history_tokens()
        self.history.append(message)
        if message.role != "system":
            self._history_tokens += estimate_message_tokens(message)
        self._history_length = len(self.history)
        self._history_tail = message

    def _system_prompt_file_key(self) -> tuple | None:
        try:
            stat_result = self._system_prompt_resource.stat()  # type: ignore[attr-defined]
        except (OSError, NotImplementedError):
            return None
        return (
            "ok",
            stat_result.st_mtime_ns,
            stat_result.st_size,
            getattr(stat_result, "st_ino", 0),
        )

    def _agents_file_key(self) -> tuple:
        name = self.cfg.context.agents_md
        base = self.workdir.resolve()
        prefix = (name, self.cfg.context.max_fragment_chars, str(base))
        if not name or not safe_bare_filename(name):
            return prefix + ("invalid",)
        try:
            resolved = (base / name).resolve()
            stat_result = resolved.stat()
        except OSError as exc:
            return prefix + ("error", type(exc).__name__)
        if not resolved.is_relative_to(base) or not S_ISREG(stat_result.st_mode):
            return prefix + ("not_file", str(resolved))
        return prefix + (
            "ok",
            str(resolved),
            stat_result.st_mtime_ns,
            stat_result.st_size,
            getattr(stat_result, "st_ino", 0),
        )

    def _prompt_tokens(self) -> int:
        """Token estimate for the prompt side (system + agents.md + memory slot).

        Uses :func:`spark.core.context.system_prompt` — the exact prompt
        ``build_messages`` sends — so the usage meter can never drift from
        what is actually charged (environment block, mode hint and skills
        injection included).
        """
        system_key = self._system_prompt_file_key()
        if system_key is None or system_key != self._system_prompt_cache_key:
            self._system_prompt_tokens = estimate_message_tokens(
                ChatMessage(
                    role="system", content=system_prompt(self.workdir, self.cfg)
                )
            )
            self._system_prompt_cache_key = system_key
        agents_key = self._agents_file_key()
        if agents_key != self._agents_cache_key:
            agents = load_agents_md(self.workdir, self.cfg)
            self._agents_tokens = (
                estimate_message_tokens(ChatMessage(role="user", content=agents))
                if agents
                else 0
            )
            self._agents_cache_key = agents_key
        return self._system_prompt_tokens + self._agents_tokens + 512

    def _usage(self) -> dict:
        self._sync_history_tokens()
        limit = self.cfg.context.max_context_tokens
        total = self._prompt_tokens() + self._history_tokens + self._tool_overhead()
        return {
            "used": total,
            "limit": limit,
            "percent": round(total * 100 / max(1, limit), 1),
        }

    def _tool_overhead(self) -> int:
        revision = self.registry.schema_revision
        if revision != self._tool_schema_revision:
            self._tool_overhead_tokens = estimate_tool_overhead(self.registry.schemas())
            self._tool_schema_revision = revision
        return self._tool_overhead_tokens

    def _get_compactor(self):
        """Construct the compactor on first use (see __init__)."""
        if self._compactor is None:
            from spark.core.compact import Compactor

            self._compactor = Compactor(provider=self.provider, cfg=self.cfg)
        return self._compactor

    def _compact_boundary(self) -> int:
        """Index of the first message to keep, or -1 when no safe cut exists.

        Cutting mid tool-exchange leaves an orphan ``role="tool"`` message whose
        ``assistant(tool_calls)`` partner was summarized away, which every
        OpenAI-compatible endpoint rejects with a 400 — so the cut is pulled
        back to a turn boundary that cannot split a call/result pair.
        """
        keep = max(2, self.cfg.context.keep_recent_messages)
        boundary = len(self.history) - keep
        if boundary <= 0:
            return -1
        for i in range(boundary, len(self.history)):
            if self.history[i].role == "user":
                boundary = i
                break
        while boundary > 0 and (
            self.history[boundary].role == "tool"
            or (boundary - 1 >= 0 and self.history[boundary - 1].tool_calls)
        ):
            boundary -= 1
        return boundary if 0 < boundary < len(self.history) else -1

    async def _maybe_compact(self):
        """Auto-compact: when usage crosses threshold, summarize old turns into one summary message."""
        usage = self._usage()
        limit = self.cfg.context.max_context_tokens
        threshold = self.cfg.context.compact_threshold
        keep = max(2, self.cfg.context.keep_recent_messages)
        if usage["used"] <= limit * threshold or len(self.history) <= keep:
            return None
        boundary = self._compact_boundary()
        if boundary < 0:
            return None
        old_part = self.history[:boundary]
        if all(m.role == "summary" for m in old_part):
            return None
        compactor = self._get_compactor()
        summary_text = await compactor.summarize(old_part)
        if not summary_text:
            return None
        summary_msg = ChatMessage(
            role="summary",
            content=f"<conversation_summary>\n{summary_text}\n</conversation_summary>",
        )
        # The cursor must point at the *first retained message*: pointing it at
        # the summary row (which always has the newest id) filters the retained
        # tail out of load_messages() and wipes the recent conversation.
        row_ids = self.store.load_message_ids(self.session_id)
        aligned = len(row_ids) == len(self.history)
        if not aligned:
            logger.warning(
                "session %s: history (%d) and stored rows (%d) are not index-aligned; "
                "compacting in memory only",
                self.session_id,
                len(self.history),
                len(row_ids),
            )
        self.store.append_message(self.session_id, summary_msg)
        if aligned:
            self.store.set_compact_from(self.session_id, row_ids[boundary])
        before_tokens = usage["used"]
        if aligned:
            self._replace_history(self.store.load_messages(self.session_id))
        else:
            self._replace_history([summary_msg, *self.history[boundary:]])
        after_usage = self._usage()
        self._notify_safe(
            "compaction",
            {
                "message_count": len(old_part),
                "before_tokens": before_tokens,
                "after_tokens": after_usage["used"],
                "limit": limit,
            },
        )
        return {
            "before_tokens": before_tokens,
            "after_tokens": after_usage["used"],
            "limit": limit,
            "summarized_messages": len(old_part),
            "usage": after_usage,
        }

    async def iter_turn(self, user_text: str, images=None):
        """公开 turn 入口：包一层 replay 记录（P2 上下文合同），实现见
        :meth:`_iter_turn_impl`。记录器永不阻塞主循环。"""
        recorder = self._replay_recorder_for(user_text or "", images, resumed=False)
        if recorder is None:
            async for event in self._iter_turn_impl(user_text, images=images):
                yield event
            return
        async for event in self._drive_recorded(
            recorder, self._iter_turn_impl(user_text, images=images)
        ):
            yield event

    def _replay_recorder_for(self, prompt: str, images, *, resumed: bool):
        cfg_replay = getattr(self.cfg, "replay", None)
        if cfg_replay is None or not cfg_replay.enabled:
            return None
        try:
            from spark.core.replay import TurnReplayRecorder

            return TurnReplayRecorder(
                session_id=self.session_id,
                workdir=str(self.workdir),
                model=self.cfg.provider.model,
                prompt=prompt,
                image_count=len(images or []) if images else 0,
                resumed=resumed,
            )
        except Exception:
            logger.debug("replay recorder init failed", exc_info=True)
            return None

    async def _drive_recorded(self, recorder, impl):
        """事件观测 + approver 决策回填 + 终态 flush（含审批恢复/崩溃场景）。"""
        outer_approver = self.approver
        if outer_approver is not None:

            async def recording_approver(request):
                decision = await outer_approver(request)
                recorder.record_approval(request, decision)
                return decision

            self.approver = recording_approver
        try:
            async for event in impl:
                recorder.observe(event)
                yield event
        finally:
            self.approver = outer_approver
            try:
                recorder.flush(
                    usage=(
                        dict(self._turn_usage_totals)
                        if self._turn_usage_totals
                        else None
                    ),
                    context_after=self._usage(),
                    messages_after=len(self.history),
                    checkpoint_id=self._last_checkpoint_id,
                )
            except Exception:
                logger.debug("replay flush failed", exc_info=True)

    async def _iter_turn_impl(self, user_text: str, images=None):
        sid_prefix = self.session_id[:8]
        logger.info("turn_start session=%s message=%s", sid_prefix, user_text[:200])
        self._notify_safe(
            "turn_start",
            {"session_id": self.session_id, "message": user_text},
        )
        # 观测（trace=turn）：显式管理 span 生命周期（async generator 跨 yield）
        from spark.otel import start_turn_span

        self._otel_span = start_turn_span(
            session_id=self.session_id,
            model=self.cfg.provider.model,
            provider_name=str(self.cfg.provider.name),
            workdir=str(self.workdir),
        )
        turn_started = time.monotonic()

        compacted = await self._maybe_compact()
        if compacted:
            yield TurnEvent(type="compaction", data=compacted)
        usage = self._usage()
        yield TurnEvent(type="context", data={"usage": usage})
        threshold = self.cfg.context.compact_threshold
        if usage["percent"] >= threshold * 100:
            self._notify_safe(
                "context_threshold",
                {
                    "used": usage["used"],
                    "limit": usage["limit"],
                    "percent": usage["percent"],
                    "threshold": threshold,
                },
            )
        memory_block = None
        if self.memory is not None:
            try:
                memory_block = self.memory.retrieve_context(user_text)
                self._notify_safe(
                    "memory_extract",
                    {"action": "retrieve", "query": user_text},
                )
            except Exception as exc:
                logger.debug("Memory retrieve failed: %s", exc)
                memory_block = None
        # 项目知识库：与记忆同槽注入，但独立配置、独立开关。检索失败只降级，
        # 不能影响本轮对话（知识库是增强项，不是依赖）。
        knowledge_block = self._knowledge_context(user_text)
        if knowledge_block:
            memory_block = f"{memory_block}\n\n{knowledge_block}" if memory_block else knowledge_block
        user = ChatMessage(role="user", content=user_text, images=images)
        self._append_history(user)
        self.store.append_message(self.session_id, user)
        session = self.store.get_session(self.session_id)
        if session and session.get("title") == "untitled":
            self.store.touch(self.session_id, title=user_text[:80])

        self._turn_tokens = 0
        # P2 上下文合同：每个 turn 重置 usage 聚合与检查点 id
        self._turn_usage_totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "source": "estimated",
        }
        self._last_checkpoint_id = None
        self._call_window = deque(maxlen=self._breaker_window())
        self._call_counts.clear()
        self._classification_cache.clear()
        # 控制流移交 LangGraph 状态机（graph.py = 唯一真相源）：
        # llm →(有工具) approve →(interrupt 审批) execute → llm → … → finalize / END。
        if self._turn_graph is None:
            from spark.core.graph import build_turn_graph

            self._turn_graph = build_turn_graph(self, checkpointer=self._checkpointer.saver)
        assert self._turn_graph is not None
        thread_id = f"{self.session_id}:t{uuid.uuid4().hex[:12]}"
        config = {"configurable": {"thread_id": thread_id}}
        await self._checkpointer.register(
            thread_id, self.session_id, str(self.workdir), self.cfg.provider.model
        )
        try:
            async for event in self._drive_graph(
                {"rounds": 0, "tool_calls": [], "verdicts": [], "memory_block": memory_block, "error": None},
                config,
            ):
                yield event
        finally:
            self._end_otel_turn(turn_started, resumed=False)
            # 正常/错误终态 → 清理 checkpoint 与登记；审批挂起等异常退出
            # （崩溃/取消）时 astream 未走完，注册保留供恢复。
            snapshot = await self._turn_graph.aget_state(config)
            if not snapshot.next:
                await self._checkpointer.unregister(thread_id)
        logger.info("turn_end session=%s", sid_prefix)

    async def _drive_graph(self, graph_input: Any, config: dict):
        """驱动状态机直至终态；审批 interrupt 在此统一处理。

        - ``custom`` 通道：TurnEvent 实时透传；
        - ``updates`` 通道：感知 ``__interrupt__``——构造 approval_needed
          事件、触发 hook 通知、调用 approver 回调、resume 继续。
          （approve 节点 interrupt 前无副作用，resume 重跑零重复。）
        """
        from langgraph.types import Command

        assert self._turn_graph is not None
        pending_input = graph_input
        while True:
            interrupt_requests: list[ApprovalRequest] = []
            async for mode, chunk in self._turn_graph.astream(
                pending_input, config, stream_mode=["custom", "updates"]
            ):
                if mode == "custom":
                    yield chunk
                elif isinstance(chunk, dict) and "__interrupt__" in chunk:
                    for intr in chunk["__interrupt__"]:
                        payload = intr.value if isinstance(intr.value, dict) else {}
                        approval_data = payload.get("approval") or {}
                        interrupt_requests.append(ApprovalRequest.model_validate(approval_data))
            if not interrupt_requests:
                return
            pending_input = None
            for request in interrupt_requests:
                self._notify_safe(
                    "approval_needed",
                    {
                        "tool_call": request.tool_call.name,
                        "arguments": request.tool_call.arguments,
                        "summary": request.summary,
                    },
                )
                yield TurnEvent(type="approval_needed", approval=request, tool_call=request.tool_call)
                if self.approver is None:
                    verdict = {"action": "deny", "reason": "no approver"}
                else:
                    decision = await self.approver(request)
                    verdict = {
                        "action": decision.action,
                        # 与旧路径 approver-deny 的 payload 文案保持一致
                        "reason": "denied" if decision.action == "deny" else "",
                    }
                pending_input = Command(resume=verdict)

    async def resume_pending_turn(self, thread_id: str):
        """恢复一个挂起的 turn（审批 interrupt 位点或执行中断点）。

        从 checkpointer 重建轮次状态后继续驱动；挂起在审批的 turn 会重新
        走 approval_needed → approver → resume 循环。事件流与 ``iter_turn``
        同构（tool/plan/approval_needed/turn_end…），并同样记录 replay。
        """
        recorder = self._replay_recorder_for("[resumed turn]", None, resumed=True)
        if recorder is not None:
            async for event in self._drive_recorded(
                recorder, self._resume_pending_turn_impl(thread_id)
            ):
                yield event
            return
        async for event in self._resume_pending_turn_impl(thread_id):
            yield event

    async def _resume_pending_turn_impl(self, thread_id: str):
        if self._turn_graph is None:
            from spark.core.graph import build_turn_graph

            self._turn_graph = build_turn_graph(self, checkpointer=self._checkpointer.saver)
        assert self._turn_graph is not None
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await self._turn_graph.aget_state(config)
        if snapshot is None or not snapshot.next:
            raise ValueError(f"no pending turn at thread: {thread_id}")
        from spark.otel import start_turn_span

        self._otel_span = start_turn_span(
            session_id=self.session_id,
            model=self.cfg.provider.model,
            provider_name=str(self.cfg.provider.name),
            workdir=str(self.workdir),
            resumed=True,
        )
        turn_started = time.monotonic()
        try:
            async for event in self._drive_graph(None, config):
                yield event
        finally:
            self._end_otel_turn(turn_started, resumed=True)
            snapshot = await self._turn_graph.aget_state(config)
            if not snapshot.next:
                await self._checkpointer.unregister(thread_id)

    async def list_pending_turns(self) -> list[dict[str, Any]]:
        """列出本 checkpointer 上所有挂起 turn（崩溃恢复入口）。"""
        return await self._checkpointer.list_pending()

    async def discard_pending_turn(self, thread_id: str) -> None:
        """丢弃一个挂起 turn（清理 checkpoint 与登记）。"""
        await self._checkpointer.unregister(thread_id)

    async def _llm_round(self, memory_block: str | None, rounds: int):
        """一轮 LLM 调用（原 while 循环体上半段，事件语义逐字节等价）。

        Yield TurnEvent；终值写入 ``self._last_round``：
        {"tool_calls": [...], "text": str, "error": str | None}。
        """
        self._last_round = {"tool_calls": [], "text": "", "error": None}
        if self.cancelled:
            yield TurnEvent(type="turn_error", text="Turn cancelled")
            self._last_round["error"] = "cancelled"
            return
        if rounds >= self.cfg.agent.max_tool_rounds:
            yield TurnEvent(
                type="turn_error",
                text="Reached max_tool_rounds",
                data=self._budget_state(),
            )
            self._last_round["error"] = "max_rounds"
            return
        if self._budget_exceeded():
            yield TurnEvent(
                type="turn_error",
                text=(
                    "Token budget exceeded for this turn "
                    f"({self._turn_tokens}/{self.cfg.agent.max_turn_tokens})"
                ),
                data=self._budget_state(),
            )
            self._last_round["error"] = "budget"
            return
        messages = build_messages(
            workdir=self.workdir,
            cfg=self.cfg,
            history=self.history,
            memory_block=memory_block,
            _compactor=self._compactor,
        )
        if self._extra_system_prompt and messages and messages[0].role == "system":
            base_system = messages[0].content or ""
            messages[0] = messages[0].model_copy(
                update={"content": f"{base_system}\n\n{self._extra_system_prompt}"}
            )
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        self._reported_usage: dict[str, int] | None = None  # P1：本轮流式真实 usage
        try:
            async for delta in self.provider.stream(
                messages, self.registry.schemas(), **self._stream_extra
            ):
                if self.cancelled:
                    yield TurnEvent(type="turn_error", text="Turn cancelled")
                    self._last_round["error"] = "cancelled"
                    return
                if delta.type == "reasoning" and delta.text:
                    reasoning_parts.append(delta.text)
                    # show_thinking=False 时不下发 SSE（节省带宽），仍计入 token 用量
                    if self.cfg.agent.show_thinking:
                        yield TurnEvent(type="reasoning_delta", text=delta.text)
                elif delta.type == "text" and delta.text:
                    text_parts.append(delta.text)
                    yield TurnEvent(type="text_delta", text=delta.text)
                elif delta.type == "tool_call" and delta.tool_call:
                    tool_calls.append(delta.tool_call)
                elif delta.type == "end" and delta.usage:
                    # P1：provider 上报的真实 usage，覆盖估算口径
                    self._reported_usage = delta.usage
        except Exception as exc:
            # Distinguish a provider/transport failure from a logic error: the
            # former can be retried with a different model, the latter cannot.
            self._notify_safe(
                "provider_error",
                {
                    "model": self.cfg.provider.model,
                    "provider": str(self.cfg.provider.name),
                    "error": str(exc)[:500],
                },
            )
            self._notify_safe("turn_error", {"error": str(exc)[:500], "stage": "llm"})
            yield TurnEvent(type="turn_error", text=str(exc))
            self._last_round["error"] = str(exc)
            return

        self._charge_tokens(text_parts, reasoning_parts)
        self._persist_usage(None, messages, text_parts, reasoning_parts)

        if tool_calls:
            tripped = None
            for call in tool_calls:
                guard = self._record_call(call)
                if guard is not None:
                    tripped = (call.name, guard[1])
                    break
            if tripped is not None:
                name, count = tripped
                yield TurnEvent(
                    type="context",
                    data={
                        "usage": self._usage(),
                        "circuit_breaker": {
                            "tool": name,
                            "repeats": count,
                            "limit": self.cfg.agent.max_repeat_calls,
                        },
                        **self._budget_state(),
                    },
                )
                yield TurnEvent(
                    type="turn_error",
                    text=(
                        f"Circuit breaker: tool '{name}' repeated with identical "
                        f"arguments {count} times (limit "
                        f"{self.cfg.agent.max_repeat_calls})"
                    ),
                    data=self._budget_state(),
                )
                self._last_round["error"] = "circuit_breaker"
                return
        self._last_round = {"tool_calls": tool_calls, "text": "".join(text_parts), "error": None}

    async def _decide_calls(self, tool_calls: list[ToolCall]) -> list[dict[str, Any]]:
        """审批阶段判定（approve 节点专用，可被 interrupt 挂起）。

        返回逐 call 的判定列表，元素形如：
        ``{"action": "allow"|"allow_always"|"deny"|"hook_block"|"plan_only", "reason": str}``。

        prompt 档经 LangGraph ``interrupt`` 挂起等待外层审批；本方法**不得
        emit 事件或触发 hook**——resume 时 approve 节点从头重跑，interrupt
        之前的副作用会重复（graph.py 模块 docstring）。
        """
        from langgraph.types import interrupt

        verdicts: list[dict[str, Any]] = []
        for call in tool_calls:
            verdict = self._classify_call(call)
            if verdict["action"] == "prompt":
                # 审批 interrupt：挂起点（有 checkpointer 时位点落盘，A10）。
                summary, diff = self.registry.approval_summary(call)
                request = ApprovalRequest(tool_call=call, summary=summary, diff=diff)
                resumed = interrupt(
                    {"approval": request.model_dump(mode="json")}
                )
                action = str(resumed.get("action", "deny")) if isinstance(resumed, dict) else "deny"
                reason = str(resumed.get("reason", "")) if isinstance(resumed, dict) else ""
                verdicts.append({"action": action, "reason": reason})
                continue
            verdicts.append(verdict)
        return verdicts

    def _classify_call(self, call: ToolCall) -> dict[str, Any]:
        """公共判定段（graph 与直调两条路径共用，消除重复）。

        依次：hooks 前置结论 → plan-only 门 → 规则/模式决策。返回：
        ``{"action": "hook_block"|"plan_only"|"deny"|"prompt"|"allow", "reason": str}``。

        "prompt" 的落地方式由调用方决定：graph 路径走 LangGraph
        ``interrupt``（_decide_calls），直调路径走 approver 回调
        （_run_tool_body）——两者随后的执行分派完全一致。
        """
        key = self._classification_key(call)
        cached = self._classification_cache.get(key)
        if cached is not None:
            # The approve node re-runs from the top when an interrupt resumes
            # (LangGraph semantics), so a classification already decided for
            # this exact call must not repeat its side effects — pre_tool hooks
            # fire once per call, not once per resume. A cached "prompt" still
            # reaches interrupt() below, which returns the resume value again.
            return dict(cached)
        if self.hooks.enabled:
            hook_verdict = self.hooks.notify(
                "pre_tool", {"tool": call.name, "arguments": call.arguments}
            )
            if not hook_verdict.allow:
                verdict = {"action": "hook_block", "reason": hook_verdict.reason}
                self._classification_cache[key] = verdict
                return verdict
        if self._approval_mode() == "plan-only":
            readonly = READONLY_TOOLS | self.registry.readonly_mcp
            if call.name not in readonly:
                verdict = {"action": "plan_only", "reason": ""}
                self._classification_cache[key] = verdict
                return verdict
        decision = decide(
            self._approval_mode(),
            call,
            allow_always=self.allow_always,
            readonly_mcp=self.registry.readonly_mcp,
            rules=self.cfg.permission_rules or None,
            plan_approved=self._plan_approved is True,
            scopes=getattr(self.cfg, "permission_scopes", None) or None,
        )
        if decision == "deny":
            verdict = {"action": "deny", "reason": "denied by permission rule"}
        else:
            verdict = {"action": decision, "reason": ""}
        self._classification_cache[key] = verdict
        return dict(verdict)

    def _classification_key(self, call: ToolCall) -> str:
        """Cache key for an approve-node verdict.

        ``call.id`` alone is unsafe: ``openai_compat`` emits ``"call_0"`` for
        every call when a provider omits ids, so a read-only verdict could be
        handed to a later write in the same round and skip its approval prompt.
        The key therefore covers id + name + arguments, which is exactly the
        identity LangGraph replays on resume.
        """
        return hashlib.sha1(
            json.dumps(
                {"id": call.id, "name": call.name, "arguments": call.arguments},
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            ).encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()

    def _approval_mode(self) -> ApprovalMode:
        """生效审批模式：H4 档案覆盖 > 配置基线（零配置突变）。"""
        if self._approval_override:
            return cast(ApprovalMode, self._approval_override)
        return self.cfg.agent.approval

    def _apply_approval_profile(self, cfg: SparkConfig) -> None:
        """激活的审批档案：approval 覆盖 + 工具 glob 白名单 + system prompt。"""
        profiles = getattr(cfg, "approval_profiles", None) or []
        if not profiles:
            return
        profile = next(
            (
                p
                for p in profiles
                if p.id == getattr(cfg, "active_approval_profile_id", "")
            ),
            None,
        )
        if profile is None:
            return
        if profile.approval:
            self._approval_override = profile.approval
        if profile.tools:
            import fnmatch as _fnmatch

            names = {
                str((s.get("function") or {}).get("name", ""))
                for s in self.registry.schemas()
            }
            allow: set[str] = set()
            for pattern in profile.tools:
                allow.update(n for n in names if n and _fnmatch.fnmatch(n, pattern))
            if allow:
                self.registry.tool_allowlist = allow
        if profile.system_prompt.strip():
            self._extra_system_prompt = profile.system_prompt.strip()

    async def _execute_round(
        self,
        tool_calls: list[ToolCall],
        verdicts: list[dict[str, Any]],
        text: str = "",
    ):
        """一轮工具执行（execute 节点）：assistant 入历史 → 逐个执行 → rounds 由 graph 推进。

        判定由 approve 节点预先生成（*verdicts*），执行阶段只负责副作用与
        事件发射——resume 重跑发生在 approve，本节点天然无重放风险。
        """
        assistant = ChatMessage(
            role="assistant",
            content=text or None,
            tool_calls=tool_calls,
        )
        self._append_history(assistant)
        msg_id = self.store.append_message(self.session_id, assistant)
        for call, verdict in zip(tool_calls, verdicts):
            async for event in self._run_tool(call, msg_id, verdict=verdict):
                yield event
            if call.name == "update_plan" and self.registry.plan:
                self._notify_safe("plan_update", {"steps": self.registry.plan})
                yield TurnEvent(type="plan", data={"steps": self.registry.plan})
                # approve-plan：计划一经产出即进入计划审批门
                async for event in self._maybe_approve_plan(call):
                    yield event

    async def _finalize_turn(self, text: str = ""):
        """收尾（原 while 循环体无工具分支，事件语义逐字节等价）。"""
        assistant = ChatMessage(role="assistant", content=text)
        self._append_history(assistant)
        assistant_id = self.store.append_message(self.session_id, assistant)
        self._maybe_auto_checkpoint(assistant_id)
        yield TurnEvent(
            type="context",
            data={"usage": self._usage(), **self._budget_state()},
        )
        if self._budget_exceeded():
            yield TurnEvent(
                type="turn_error",
                text=(
                    "Token budget exceeded for this turn "
                    f"({self._turn_tokens}/{self.cfg.agent.max_turn_tokens})"
                ),
                data=self._budget_state(),
            )
            return
        self._notify_safe("turn_end", {"content": assistant.content or ""})
        yield TurnEvent(type="turn_end", text=assistant.content)

    def _maybe_auto_checkpoint(self, assistant_id: int) -> None:
        """Snapshot the workdir + conversation after turns that touched files or ran shell."""
        snapshot_id: str | None = None
        try:
            from spark.core.checkpoints import make_checkpoint_record

            recent_tools = [m.name for m in self.history[-12:] if m.role == "tool"]
            if not any(
                name in {"write_file", "apply_patch", "run_shell", "notebook_edit"}
                for name in recent_tools
            ):
                return
            snapshot_id, files_hash = make_checkpoint_record(self.workdir)
            last_user = next(
                (m for m in reversed(self.history) if m.role == "user"), None
            )
            label = (
                (last_user.content or "")[:60].strip() if last_user else "checkpoint"
            )
            checkpoint_id = self.store.add_checkpoint(
                self.session_id,
                label,
                assistant_id,
                json.dumps(
                    {
                        "snapshot_id": snapshot_id,
                        "files_hash": files_hash,
                        "message_id": assistant_id,
                    }
                ),
            )
            self._last_checkpoint_id = checkpoint_id
        except Exception as exc:
            logger.warning("Failed to persist checkpoint %s: %s", snapshot_id, exc)

    def rollback_to_checkpoint(self, checkpoint_id: int) -> dict:
        """Restore files and conversation history to a stored checkpoint."""
        from spark.core.checkpoints import restore_workdir

        record = self.store.get_checkpoint(self.session_id, checkpoint_id)
        if record is None:
            raise ValueError(f"checkpoint not found: {checkpoint_id}")
        meta = json.loads(record["snapshot_json"])
        files = restore_workdir(self.workdir, str(meta.get("snapshot_id") or ""))
        removed = self.store.delete_messages_after(
            self.session_id, int(record["message_id"])
        )
        try:
            cid = int(record["message_id"])
            if self.store.get_compact_from(self.session_id) > cid:
                self.store.set_compact_from(self.session_id, 0)
        except Exception:
            # compact_from 重置失败不影响回滚主流程
            logger.warning("重置 compact_from 失败 (session=%s)", self.session_id, exc_info=True)
        self._replace_history(self.store.load_messages(self.session_id))
        return {
            "checkpoint_id": checkpoint_id,
            "label": record["label"],
            "files": files,
            "removed_messages": removed,
        }

    async def _maybe_approve_plan(self, call: ToolCall):
        """approve-plan 模式的计划审批门：计划产出后等待用户批准一次。

        批准（allow/allow_always）后 ``_plan_approved=True``，后续写/执行
        工具按 auto-edit 语义自动执行；拒绝或无人审批则停留在 prompt 档。
        """
        if self._approval_mode() != "approve-plan" or self._plan_approved is True:
            return
        request = ApprovalRequest(
            tool_call=call,
            summary="plan",
            diff=None,
        )
        self._notify_safe(
            "approval_needed",
            {
                "tool_call": "plan",
                "arguments": {"steps": self.registry.plan or []},
                "summary": "plan",
            },
        )
        yield TurnEvent(type="approval_needed", approval=request, tool_call=call)
        if self.approver is None:
            self._plan_approved = False
            return
        verdict = await self.approver(request)
        self._plan_approved = verdict.action in ("allow", "allow_always")

    async def _run_tool(
        self, call: ToolCall, message_id: int, verdict: dict[str, Any] | None = None
    ):
        """执行单个工具（观测包装：span=tool），主体见 ``_run_tool_body``。"""
        from spark.otel import start_tool_span

        span = start_tool_span(
            self._otel_span,
            tool_name=call.name,
            args_summary=json.dumps(call.arguments, ensure_ascii=False, default=str)[:200],
        )
        started = time.monotonic()
        final_event: TurnEvent | None = None
        try:
            async for event in self._run_tool_body(call, message_id, verdict=verdict):
                if event.type == "tool_end":
                    final_event = event
                yield event
        finally:
            from spark.otel import end_tool_span

            result = final_event.result if final_event is not None else None
            # approval 取判定层结论（graph 路径可靠；旧路径未判定时记 auto）
            approval_label = str(verdict.get("action")) if verdict else "auto"
            end_tool_span(
                span,
                ok=bool(result.ok) if result is not None else False,
                approval_label=approval_label,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

    async def _maybe_review(self, call: ToolCall) -> dict[str, Any] | None:
        """自动审查（Guardian 语义）：只在开启且是"写/执行"类动作时触发。

        返回 None 表示"不适用/已关闭"，调用方据此完全跳过（零成本、零日志噪音）。
        审查器不可用时返回 degraded 结果并放行——不能因为审查器坏了把用户卡死，
        但也绝不声称"已通过审查"。
        """
        if not getattr(self.cfg.agent, "auto_review", False):
            return None
        from spark.core.review import REVIEWED_TOOLS, review_action

        if call.name not in REVIEWED_TOOLS:
            return None
        try:
            result = await review_action(
                self.provider,
                call.name,
                dict(call.arguments or {}),
                str(self.workdir),
            )
        except Exception as exc:  # aqg: top-level boundary
            logger.warning("auto review failed", exc_info=True)
            return {
                "verdict": "allow",
                "reason": f"reviewer error: {type(exc).__name__}",
                "reviewed": False,
                "degraded": True,
            }
        if result.get("verdict") == "block":
            from spark import audit

            try:
                audit.record(
                    "review_blocked",
                    tool=call.name,
                    reason=result.get("reason", ""),
                )
            except Exception:  # noqa: BLE001 - 审计不得影响主流程
                logger.debug("audit write failed", exc_info=True)
        return result

    async def _resolve_direct_verdict(
        self, call: ToolCall
    ) -> tuple[dict[str, Any], TurnEvent | None]:
        """直调路径（测试/兼容）：公共判定后按路径落定 prompt——

        审批经 approver 回调（graph 路径则由 approve 节点 interrupt）。
        返回 (verdict, approval_event)：event 需由调用方在判定后补发。
        """
        classified = self._classify_call(call)
        if classified["action"] != "prompt":
            return classified, None
        summary, diff = self.registry.approval_summary(call)
        request = ApprovalRequest(tool_call=call, summary=summary, diff=diff)
        self._notify_safe(
            "approval_needed",
            {
                "tool_call": call.name,
                "arguments": call.arguments,
                "summary": summary,
            },
        )
        event = TurnEvent(type="approval_needed", approval=request, tool_call=call)
        if self.approver is None:
            return {"action": "deny", "reason": "no approver"}, event
        decision = await self.approver(request)
        verdict = {
            "action": decision.action,
            # 与 graph 路径 approver-deny 的 payload 文案保持一致
            "reason": "denied" if decision.action == "deny" else "",
        }
        return verdict, event

    def _refusal_result(self, call: ToolCall, verdict: dict[str, Any]) -> ToolResult:
        """hook_block / plan_only / deny 三种拒绝的统一结果构造；deny 记结构化审计。"""
        action = verdict["action"]
        if action == "hook_block":
            return ToolResult(
                ok=False, payload={"error": verdict.get("reason") or "blocked by hook"}
            )
        if action == "plan_only":
            return ToolResult(
                ok=False,
                payload={
                    "error": "plan-only: write/execute tools are disabled; output the plan and ask the user to switch to auto modes",
                    "tool": call.name,
                },
            )
        result = ToolResult(
            ok=False,
            payload={
                "error": verdict.get("reason") or "denied by permission rule",
                "tool": call.name,
            },
        )
        # Security-relevant refusal: record it outside the human log so the
        # user (or a collector) can answer "what was blocked and why".
        from spark import audit

        audit.record(
            "tool_denied",
            session=self.session_id,
            tool=call.name,
            reason=str(verdict.get("reason") or "permission rule"),
            arguments=call.arguments,
        )
        return result

    @staticmethod
    def _parse_question_args(arguments: dict) -> tuple[str, list[str]]:
        question = str(arguments.get("question") or "").strip()
        raw_options = arguments.get("options")
        options = (
            [str(o) for o in raw_options if str(o).strip()]
            if isinstance(raw_options, list)
            else []
        )
        return question, options

    @staticmethod
    def _question_result(
        question: str, answer: str, exc: Exception | None
    ) -> ToolResult:
        if exc is not None:
            return ToolResult(ok=False, payload={"error": f"question failed: {exc}"})
        if answer:
            return ToolResult(ok=True, payload={"question": question, "answer": answer})
        return ToolResult(ok=False, payload={"error": "user dismissed the question"})

    @staticmethod
    def _single_task_params(item) -> tuple[str, str, list[str] | None, str]:
        """单个子任务派发参数：prompt / system_extra / allowlist / model。"""
        from spark.agents import subagent_system_prompt

        prompt, agent = item
        system_extra = subagent_system_prompt(agent) if agent else ""
        allowlist = list(agent.tools) if agent and agent.tools else None
        model = agent.model if agent and agent.model else ""
        return prompt, system_extra, allowlist, model

    @staticmethod
    def _parallel_task_params(
        items,
    ) -> tuple[list[str], list[str], list[list[str] | None], list[str]]:
        """并行子任务派发参数（最多 4 路）。"""
        from spark.agents import subagent_system_prompt

        if len(items) > 4:
            items = items[:4]
        prompts = [p for p, _ in items]
        system_extras = [
            subagent_system_prompt(a) if a else "" for _, a in items
        ]
        allowlists = [
            list(a.tools) if a and a.tools else None for _, a in items
        ]
        models = [a.model if a and a.model else "" for _, a in items]
        return prompts, system_extras, allowlists, models

    @staticmethod
    def _single_task_result(summaries: list[str], errors: list[str]) -> ToolResult:
        if errors:
            return ToolResult(ok=False, payload={"error": errors[-1]})
        return ToolResult(
            ok=True, payload={"summary": summaries[-1] if summaries else ""}
        )

    def _collect_task_items(self, call_arguments: dict) -> tuple[list, str]:
        """P1-4：声明式子代理（.claude/agents/*.md）参数解析。

        agent 为该文件的 name，其 body 作为子代理的 system prompt，
        tools 字段收敛子代理工具集。返回 (items, error)。
        """
        from spark.agents import AgentFileError, DeclaredAgent, get_declared_agent

        items: list[tuple[str, DeclaredAgent | None]] = []
        task_error = ""

        def _resolve_agent(agent_name: str) -> tuple[DeclaredAgent | None, str]:
            try:
                resolved = get_declared_agent(self.workdir, agent_name)
            except AgentFileError as exc:
                return None, f"invalid agent file '{agent_name}': {exc}"
            if resolved is None:
                return None, f"unknown declared agent: {agent_name}"
            return resolved, ""

        raw_tasks = call_arguments.get("tasks")
        if isinstance(raw_tasks, list):
            for t in raw_tasks:
                if not isinstance(t, dict):
                    continue
                prompt = str(t.get("prompt") or "").strip()
                if not prompt:
                    continue
                agent = None
                agent_name = str(t.get("agent") or "").strip()
                if agent_name:
                    agent, err = _resolve_agent(agent_name)
                    if err:
                        task_error = err
                        break
                items.append((prompt, agent))
        else:
            prompt = str(call_arguments.get("prompt") or "").strip()
            agent = None
            agent_name = str(call_arguments.get("agent") or "").strip()
            if prompt and agent_name:
                agent, task_error = _resolve_agent(agent_name)
            if prompt and not task_error:
                items.append((prompt, agent))
        return items, task_error

    _SUBTASK_FORWARD_TYPES = frozenset(
        {
            "tool_start",
            "tool_end",
            "plan",
            "approval_needed",
            "subtask_start",
            "subtask_end",
            "subtask_progress",
            "subtask_trace",
        }
    )

    async def _forward_subtask_events(
        self, stream, summaries: list[str], errors: list[str]
    ):
        """实时转发子任务事件；turn_end/turn_error 分别收集（错误取最后一条）。"""
        async for sub_event in stream:
            if sub_event.type in self._SUBTASK_FORWARD_TYPES:
                yield sub_event
            elif sub_event.type == "turn_end":
                summaries.append(sub_event.text or "")
            elif sub_event.type == "turn_error":
                errors.append(sub_event.text or "")

    def _finish_tool_execution(
        self, call: ToolCall, result: ToolResult, started: float, args_summary: str
    ) -> None:
        """执行收尾：错误通知 / post_tool 通知 / 系统提示缓存失效 / 访问日志。"""
        if not result.ok:
            self._notify_safe(
                "tool_error",
                {
                    "tool_call": call.name,
                    "arguments": call.arguments,
                    "error": result.payload.get("error", ""),
                },
            )
        elapsed = time.monotonic() - started
        self._notify_safe(
            "post_tool",
            {
                "tool": call.name,
                "ok": result.ok,
                "elapsed_ms": int(elapsed * 1000),
            },
        )
        if result.ok and (
            call.name in WRITE_TOOLS
            or call.name in SHELL_TOOLS
            or call.name.startswith(("worktree_", "task_", "bg_"))
        ):
            # 工作区可能已变化：失效系统提示缓存（含 repo_map / skills / subagents）
            invalidate_system_prompt_cache()
        logger.info(
            "tool_call name=%s args=%s elapsed=%.3fs ok=%s",
            call.name,
            args_summary,
            elapsed,
            result.ok,
        )

    async def _run_tool_body(
        self, call: ToolCall, message_id: int, verdict: dict[str, Any] | None = None
    ):
        """工具执行主体。

        *verdict* 由 approve 节点预先生成（graph 路径）：
        ``{"action": "allow"|"allow_always"|"deny"|"hook_block"|"plan_only", "reason": str}``。
        提供时跳过判定段（判定已完成，本方法只做副作用与事件）。
        缺省（None，测试/兼容直调路径）走 :meth:`_resolve_direct_verdict` 公共判定，
        prompt 经 approver 回调落定，随后与 graph 路径共用同一执行分派。
        """
        yield TurnEvent(type="tool_start", tool_call=call)
        started = time.monotonic()
        args_summary = json.dumps(call.arguments, ensure_ascii=False, default=str)[:200]
        if verdict is None:
            verdict, approval_event = await self._resolve_direct_verdict(call)
            if approval_event is not None:
                yield approval_event
        # ── 统一执行分派（两条路径在此收敛：判定已完成，只做副作用与事件）──
        if verdict["action"] in {"hook_block", "plan_only", "deny"}:
            result = self._refusal_result(call, verdict)
            await self._persist_tool(call, message_id, result, verdict["action"])
            yield TurnEvent(type="tool_end", tool_call=call, result=result)
            return

        # 自动审查（Guardian 语义）：在"已获批准、即将落地"这一刻再问一次
        # 独立模型。放在这里而不是 _classify_call，是因为审查本身是异步调用，
        # 且它审的是"真正要执行的动作"，而不是一个可能被缓存/被 resume 的判定。
        review = await self._maybe_review(call)
        if review is not None:
            yield TurnEvent(
                type="review",
                data={
                    "verdict": review.get("verdict"),
                    "reason": review.get("reason", ""),
                    "degraded": bool(review.get("degraded")),
                },
            )
            if review.get("verdict") == "block":
                result = ToolResult(
                    ok=False,
                    payload={
                        "error": "blocked by automatic review",
                        "review": review,
                    },
                )
                await self._persist_tool(call, message_id, result, "review_block")
                yield TurnEvent(type="tool_end", tool_call=call, result=result)
                return
        approval_label = verdict["action"]
        if approval_label == "allow_always" and call.name not in NEVER_PERSIST_ALLOW:
            self.allow_always.add(call_signature(call))
        if call.name == "ask_user" and self.registry.ctx.question_asker is not None:
            question, options = self._parse_question_args(call.arguments)
            # Emit first: the browser needs the question to render its card,
            # and the asker blocks until the user answers.
            yield TurnEvent(
                type="question",
                data={"question": question, "options": options, "tool_id": call.id},
            )
            answer = ""
            ask_error: Exception | None = None
            try:
                answer = await self.registry.ctx.question_asker(question, options)
            except Exception as exc:  # noqa: BLE001
                ask_error = exc
            result = self._question_result(question, answer, ask_error)
            await self._persist_tool(call, message_id, result, "ask")
            yield TurnEvent(type="tool_end", tool_call=call, result=result)
            return
        if call.name == "task" and self.registry.ctx.task_runner is not None:
            items, task_error = self._collect_task_items(call.arguments)
            summaries: list[str] = []
            sub_errors: list[str] = []
            if task_error:
                result = ToolResult(ok=False, payload={"error": task_error})
            elif not items:
                result = ToolResult(ok=False, payload={"error": "prompt required"})
            elif len(items) == 1:
                prompt, system_extra, allowlist, model = self._single_task_params(items[0])
                async for sub_event in self._forward_subtask_events(
                    self.spawn_subtask(
                        prompt,
                        system_extra=system_extra,
                        tools_allowlist=allowlist,
                        model=model,
                    ),
                    summaries,
                    sub_errors,
                ):
                    yield sub_event
                result = self._single_task_result(summaries, sub_errors)
            else:
                prompts, system_extras, allowlists, models = self._parallel_task_params(items)
                agent_names = [a.name if a else "" for _, a in items[:4]]
                async for sub_event in self._forward_subtask_events(
                    self.spawn_subtasks_parallel(
                        prompts,
                        system_extras=system_extras,
                        tools_allowlists=allowlists,
                        models=models,
                        agent_names=agent_names,
                    ),
                    summaries,
                    sub_errors,
                ):
                    yield sub_event
                result = ToolResult(ok=True, payload={"summaries": summaries})
        else:
            result = await self.registry.execute(call)
        self._finish_tool_execution(call, result, started, args_summary)
        await self._persist_tool(call, message_id, result, approval_label)
        yield TurnEvent(type="tool_end", tool_call=call, result=result)

    async def _persist_tool(
        self, call: ToolCall, message_id: int, result: ToolResult, approval: str
    ) -> None:
        tool_msg = ChatMessage(
            role="tool",
            name=call.name,
            tool_call_id=call.id,
            content=json.dumps(result.payload, ensure_ascii=False, default=str),
        )
        self._append_history(tool_msg)
        self.store.append_message(self.session_id, tool_msg)
        self.store.append_tool_event(
            self.session_id, message_id, call.name, call.arguments, result, approval
        )

    def _make_sub_context(self) -> ToolContext:
        sub_cfg = self.cfg.model_copy(deep=True)
        sub_cfg.agent.max_tool_rounds = min(15, max(5, sub_cfg.agent.max_tool_rounds))
        sub_ctx = ToolContext(sandbox=self.registry.ctx.sandbox, config=sub_cfg)
        sub_ctx.mcp_call = self.registry.ctx.mcp_call
        sub_ctx.mcp_bridge = self.registry.ctx.mcp_bridge
        return sub_ctx

    def _inherit_registry(self, sub_registry: ToolRegistry) -> None:
        for schema in self.registry.schemas():
            name = str(((schema.get("function") or {}).get("name")) or "")
            if name.startswith("mcp__"):
                sub_registry.add_mcp_schema(schema)
        sub_registry.readonly_mcp |= set(self.registry.readonly_mcp)

    async def spawn_subtask(
        self,
        prompt: str,
        *,
        system_extra: str = "",
        tools_allowlist: list[str] | None = None,
        model: str = "",
    ):
        """Run a self-contained sub-agent with a fresh context; yield its events, final turn_end carries the summary.

        P1-4: ``system_extra`` becomes the sub-agent's own system-prompt slot
        (declared-agent body); ``tools_allowlist`` restricts its tool schemas.

        ``model`` lets a declared specialist run on its own model. The provider
        is rebuilt only when the requested model differs from the main one, and
        the credentials come from the parent config (same endpoint, same key) —
        a sub-agent never introduces a new credential.
        """
        from pathlib import Path as _Path

        sub_ctx = self._make_sub_context()
        sub_registry = ToolRegistry(sub_ctx)
        self._inherit_registry(sub_registry)
        if tools_allowlist:
            sub_registry.tool_allowlist = set(tools_allowlist)
        sub_cfg = sub_ctx.config
        provider = self.provider
        if model and model != sub_cfg.provider.model:
            sub_cfg.provider.model = model
            try:
                from spark.providers.factory import create_provider

                provider = create_provider(sub_cfg)
            except Exception as exc:  # aqg: top-level boundary
                # A bad model id must not kill the whole turn: fall back to the
                # parent provider and say so in the log.
                logger.warning(
                    "sub-agent model %r unavailable (%s); using the parent model",
                    model,
                    exc,
                )
                sub_cfg.provider.model = self.cfg.provider.model
                provider = self.provider
        sub_store = SessionStore(_Path(":memory:"))
        sid = sub_store.create_session(
            self.workdir, sub_cfg.provider.model, title="subtask"
        )
        sub_loop = AgentLoop(
            workdir=self.workdir,
            cfg=sub_cfg,
            provider=provider,
            registry=sub_registry,
            store=sub_store,
            session_id=sid,
            approver=self.approver,
            memory=None,
        )
        sub_loop.allow_always = set(self.allow_always)
        sub_loop._extra_system_prompt = system_extra
        self._active_children.append(sub_loop)
        final_text = ""
        try:
            async for event in sub_loop.iter_turn(prompt):
                if event.type in {"tool_start", "tool_end", "plan", "approval_needed"}:
                    yield event
                elif event.type == "turn_end":
                    final_text = event.text or ""
                elif event.type == "turn_error":
                    final_text = f"subtask failed: {event.text}"
                    yield TurnEvent(type="turn_end", text=final_text)
                    return
            yield TurnEvent(type="turn_end", text=final_text)
        finally:
            self._active_children.remove(sub_loop)
            sub_store.close()

    async def spawn_subtasks_parallel(
        self,
        prompts: list[str],
        *,
        system_extras: list[str] | None = None,
        tools_allowlists: list[list[str] | None] | None = None,
        models: list[str] | None = None,
        agent_names: list[str] | None = None,
    ):
        """Run several sub-agents concurrently; each gets its own context. Yields each sub-agent's
        tool/plan events live, then one combined turn_end carrying all summaries.

        P1-4: ``system_extras`` / ``tools_allowlists`` align 1:1 with prompts
        (declared-agent body + tool allowlist per parallel item); defaults are
        empty, preserving the pre-P1-4 behavior exactly. ``models`` likewise
        aligns 1:1 and carries each specialist's optional model override.
        """
        import asyncio as _asyncio

        if system_extras is None:
            system_extras = [""] * len(prompts)
        if tools_allowlists is None:
            tools_allowlists = [None] * len(prompts)
        if models is None:
            models = [""] * len(prompts)
        if len(prompts) == 1:
            async for event in self.spawn_subtask(
                prompts[0],
                system_extra=system_extras[0],
                tools_allowlist=tools_allowlists[0],
                model=models[0] if models else "",
            ):
                yield event
            return

        queue: _asyncio.Queue[tuple[int, TurnEvent | None]] = _asyncio.Queue()
        summaries: dict[int, str] = {}

        async def runner(index: int, prompt: str) -> tuple[int, str]:
            try:
                summary = await self._collect_subtask_events(
                    index,
                    prompt,
                    queue,
                    system_extra=system_extras[index],
                    tools_allowlist=tools_allowlists[index],
                    model=models[index] if index < len(models) else "",
                )
                return index, summary
            except Exception as exc:
                await queue.put((index, None))
                return index, f"subtask failed: {exc}"

        tasks = [_asyncio.create_task(runner(i, p)) for i, p in enumerate(prompts)]
        for i, prompt in enumerate(prompts):
            # 带上声明式子代理名：UI 靠它区分"并行跑的到底是哪几个专家"，
            # 否则三行都只写着 subtask 1/2/3，看起来一模一样。
            extra: dict = {}
            declared_name = agent_names[i] if agent_names and i < len(agent_names) else ""
            if declared_name:
                extra["agent"] = declared_name
            yield TurnEvent(
                type="subtask_start",
                data={"index": i, "prompt": prompt[:200], "total": len(prompts), **extra},
            )
        try:
            # Drive the whole fan-out from the queue so a sub-agent that is waiting
            # on approval or a long tool still delivers its events immediately;
            # waiting on task completion alone can deadlock on the approval path.
            remaining = len(tasks)
            while remaining > 0:
                index, event = await queue.get()
                if event is not None:
                    # Tag tool events with the child that produced them so the
                    # execution graph can nest them under their sub-agent
                    # instead of drawing every tool as a sibling of `task`.
                    if event.type in {"tool_start", "tool_end"} and event.data is None:
                        event = event.model_copy(update={"data": {"subtask_index": index}})
                    yield event
                    continue
                remaining -= 1
                task = tasks[index] if index < len(tasks) else None
                if task is not None and task.done():
                    try:
                        done_index, summary = task.result()
                        summaries[done_index] = summary
                    except Exception as exc:
                        summaries[index] = f"subtask failed: {exc}"
                elif task is not None:
                    try:
                        done_index, summary = await task
                        summaries[done_index] = summary
                    except Exception as exc:
                        summaries[index] = f"subtask failed: {exc}"
            while not queue.empty():
                index, event = queue.get_nowait()
                if event is not None:
                    if event.type in {"tool_start", "tool_end"} and event.data is None:
                        event = event.model_copy(update={"data": {"subtask_index": index}})
                    yield event
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await _asyncio.gather(*tasks, return_exceptions=True)
        for i in range(len(prompts)):
            label = f"[子任务 {i + 1}]"
            yield TurnEvent(
                type="subtask_end",
                text=f"{label} {summaries.get(i, 'no output')}",
                data={"index": i, "summary": summaries.get(i, "")},
            )
            yield TurnEvent(
                type="turn_end", text=f"{label} {summaries.get(i, 'no output')}"
            )

    async def _collect_subtask_events(
        self,
        index: int,
        prompt: str,
        queue,
        *,
        system_extra: str = "",
        tools_allowlist: list[str] | None = None,
        model: str = "",
    ) -> str:
        """Run one subtask; stream its tool events into the queue tagged by index; return summary."""
        from pathlib import Path as _Path

        sub_ctx = self._make_sub_context()
        sub_registry = ToolRegistry(sub_ctx)
        self._inherit_registry(sub_registry)
        if tools_allowlist:
            sub_registry.tool_allowlist = set(tools_allowlist)
        sub_cfg = sub_ctx.config
        provider = self.provider
        if model and model != sub_cfg.provider.model:
            sub_cfg.provider.model = model
            try:
                from spark.providers.factory import create_provider

                provider = create_provider(sub_cfg)
            except Exception as exc:  # aqg: top-level boundary
                logger.warning(
                    "sub-agent model %r unavailable (%s); using the parent model",
                    model,
                    exc,
                )
                sub_cfg.provider.model = self.cfg.provider.model
                provider = self.provider
        sub_store = SessionStore(_Path(":memory:"))
        sid = sub_store.create_session(
            self.workdir, sub_cfg.provider.model, title=f"subtask-{index}"
        )
        sub_loop = AgentLoop(
            workdir=self.workdir,
            cfg=sub_cfg,
            provider=provider,
            registry=sub_registry,
            store=sub_store,
            session_id=sid,
            approver=self.approver,
            memory=None,
        )
        sub_loop.allow_always = set(self.allow_always)
        sub_loop._extra_system_prompt = system_extra
        self._active_children.append(sub_loop)
        final_text = ""
        thought_chars = 0
        text_chars = 0
        tool_count = 0
        try:
            async for event in sub_loop.iter_turn(prompt):
                if event.type in {"tool_start", "tool_end", "plan", "approval_needed"}:
                    if event.type == "tool_start":
                        tool_count += 1
                    await queue.put((index, event))
                elif event.type == "reasoning_delta" and event.text:
                    # Forward a counter, not the raw stream: the parent turn is
                    # one bubble, so the trace summary is what the graph and the
                    # card can actually show without duplicating the transcript.
                    thought_chars += len(event.text)
                    await queue.put(
                        (
                            index,
                            TurnEvent(
                                type="subtask_progress",
                                data={
                                    "index": index,
                                    "phase": "thinking",
                                    "chars": thought_chars,
                                },
                            ),
                        )
                    )
                elif event.type == "text_delta" and event.text:
                    text_chars += len(event.text)
                    await queue.put(
                        (
                            index,
                            TurnEvent(
                                type="subtask_progress",
                                data={
                                    "index": index,
                                    "phase": "answering",
                                    "chars": text_chars,
                                },
                            ),
                        )
                    )
                elif event.type == "turn_end":
                    final_text = event.text or ""
                elif event.type == "turn_error":
                    final_text = f"subtask failed: {event.text}"
            await queue.put(
                (
                    index,
                    TurnEvent(
                        type="subtask_trace",
                        data={
                            "index": index,
                            "thinking_chars": thought_chars,
                            "answer_chars": text_chars,
                            "tools": tool_count,
                        },
                    ),
                )
            )
            await queue.put((index, None))
            return final_text
        finally:
            self._active_children.remove(sub_loop)
            sub_store.close()
