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
from typing import TYPE_CHECKING, Any

from spark.config import SparkConfig
from spark.core.context import build_messages
from spark.core.context_window import ContextWindowMixin
from spark.core.subagent_runner import SubagentRunnerMixin
from spark.core.tokens import estimate_message_tokens
from spark.core.tool_runner import ToolRunnerMixin
from spark.core.turn_control import TurnControlMixin
from spark.hooks import HookRegistry
from spark.models import (
    ApprovalDecision,
    ApprovalRequest,
    ChatMessage,
    ToolCall,
    TurnEvent,
)
from spark.providers.base import Provider
from spark.store import SessionStore
from spark.tools.registry import ToolRegistry

if TYPE_CHECKING:  # pragma: no cover - 仅类型引用
    from spark.core.turn_store import MemoryTurnCheckpointer, TurnCheckpointer

Approver = Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]

logger = logging.getLogger(__name__)


class AgentLoop(ContextWindowMixin, TurnControlMixin, ToolRunnerMixin, SubagentRunnerMixin):
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
        self._turn_usage_totals: dict[str, int | float | str] | None = None
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
        # Plugins are opt-in. Installation alone is inert; only names in
        # cfg.plugins.enabled with the "hooks" permission get a subprocess.
        self.plugin_hook_issues: list[str] = []
        plugin_names = list(
            getattr(getattr(cfg, "plugins", None), "enabled", []) or []
        )
        if plugin_names:
            try:
                from spark.plugins import build_plugin_hooks

                bindings, issues = build_plugin_hooks(plugin_names, workdir)
                for binding in bindings:
                    self.hooks.register(binding.event, binding.hook)
                self.plugin_hook_issues = issues
            except Exception as exc:  # noqa: BLE001 - a plugin must not kill the loop
                self.plugin_hook_issues = [f"plugin runtime: {exc}"]
            try:
                from spark.plugins import build_plugin_tools

                schemas, handlers, tool_issues = build_plugin_tools(
                    plugin_names,
                    workdir,
                    max_output_chars=cfg.agent.max_output_chars,
                )
                self.registry.register_plugin_tools(schemas, handlers)
                self.plugin_hook_issues.extend(tool_issues)
            except Exception as exc:  # noqa: BLE001 - a plugin must not kill the loop
                self.plugin_hook_issues.append(f"plugin tools: {exc}")
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
            cache_read = 0
            cache_write = 0
            ru = getattr(self, "_reported_usage", None)
            if ru:
                in_tok = int(ru.get("prompt_tokens") or 0)
                out_tok = int(ru.get("completion_tokens") or 0)
                reasoning_tok = int(ru.get("reasoning_tokens") or 0)
                cache_read = int(ru.get("cache_read_tokens") or 0)
                cache_write = int(ru.get("cache_write_tokens") or 0)
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
                model=self.cfg.provider.model,
                provider=str(self.cfg.provider.name),
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
            )
            # P2 replay：turn 级聚合（replay flush 用，失败不影响主流程）
            totals = getattr(self, "_turn_usage_totals", None)
            if totals is not None:
                try:
                    totals["input_tokens"] = int(totals.get("input_tokens", 0)) + int(input_est)
                    totals["output_tokens"] = int(totals.get("output_tokens", 0)) + int(output_est)
                    totals["reasoning_tokens"] = int(totals.get("reasoning_tokens", 0)) + int(reasoning_est)
                    totals["cache_read_tokens"] = int(totals.get("cache_read_tokens", 0)) + cache_read
                    totals["cache_write_tokens"] = int(totals.get("cache_write_tokens", 0)) + cache_write
                    if source == "reported":
                        totals["source"] = "reported"
                    call_cost = self.store._estimate_cost(
                        self.cfg.provider.model,
                        {
                            "input_tokens": input_est,
                            "output_tokens": output_est,
                            "cache_read_tokens": cache_read,
                            "cache_write_tokens": cache_write,
                        },
                    )
                    if call_cost is not None:
                        totals["cost_usd"] = round(
                            float(totals.get("cost_usd", 0.0)) + call_cost, 6
                        )
                except (TypeError, ValueError):
                    pass
        except Exception:
            logger.debug("persist token usage failed", exc_info=True)

    def _budget_exceeded(self) -> bool:
        limit = getattr(self.cfg.agent, "max_turn_tokens", 0) or 0
        if limit > 0 and self._turn_tokens >= limit:
            return True
        cost_limit = float(getattr(self.cfg.agent, "max_session_cost_usd", 0) or 0)
        if cost_limit > 0:
            try:
                cost = self.store.session_token_usage(
                    self.session_id, model=self.cfg.provider.model
                ).get("cost_usd")
            except Exception:
                cost = None
            if isinstance(cost, (int, float)) and cost >= cost_limit:
                return True
        return False

    def _budget_state(self) -> dict:
        limit = getattr(self.cfg.agent, "max_turn_tokens", 0) or 0
        session_cost = None
        cost_limit = float(getattr(self.cfg.agent, "max_session_cost_usd", 0) or 0)
        if cost_limit > 0:
            try:
                session_cost = self.store.session_token_usage(
                    self.session_id, model=self.cfg.provider.model
                ).get("cost_usd")
            except Exception:
                session_cost = None
        return {
            "turn_tokens": self._turn_tokens,
            "turn_token_limit": limit or None,
            "session_cost_usd": session_cost,
            "session_cost_limit_usd": cost_limit or None,
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
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": 0.0,
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
            budget = self._budget_state()
            cost_spent = budget.get("session_cost_usd")
            cost_limit = budget.get("session_cost_limit_usd")
            if isinstance(cost_limit, (int, float)) and isinstance(cost_spent, (int, float)):
                message = f"Cost budget exceeded ({cost_spent:.4f}/{cost_limit:.4f} USD)"
            else:
                message = (
                    "Token budget exceeded for this turn "
                    f"({self._turn_tokens}/{self.cfg.agent.max_turn_tokens})"
                )
            yield TurnEvent(
                type="turn_error",
                text=message,
                data=budget,
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
        goal_block = self._goal_block()
        if goal_block and messages and messages[0].role == "system":
            base_system = messages[0].content or ""
            messages[0] = messages[0].model_copy(
                update={"content": f"{base_system}\n\n{goal_block}"}
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
