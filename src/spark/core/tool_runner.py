from __future__ import annotations

import json
import logging
import time
from typing import Any

from spark.core.context import invalidate_system_prompt_cache
from spark.models import ApprovalRequest, ChatMessage, ToolCall, ToolResult, TurnEvent
from spark.policy import (
    NEVER_PERSIST_ALLOW,
    SHELL_TOOLS,
    WRITE_TOOLS,
    call_signature,
)
from spark.tools.registry import ToolContext, ToolRegistry

logger = logging.getLogger(__name__)


class ToolRunnerMixin:
    async def _run_tool(
        self: Any, call: ToolCall, message_id: int, verdict: dict[str, Any] | None = None
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

    async def _maybe_review(self: Any, call: ToolCall) -> dict[str, Any] | None:
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

    def _validation_error(
        self: Any, call: ToolCall, *, schemas: list[dict[str, Any]] | None = None
    ) -> dict[str, Any] | None:
        """Return a structured validation error, with one correction attempt.

        Invalid calls never reach policy/approval or a tool handler. The first
        failure is marked retryable so the model can repair the arguments; a
        second failure for the same tool in one turn is terminal for that tool.
        """
        error = self.registry.validation_error(call, schemas=schemas)
        if error is None:
            return None
        attempts = getattr(self, "_tool_validation_failures", None)
        if attempts is None:
            attempts = {}
            self._tool_validation_failures = attempts
        name = str(call.name or "")
        if attempts.get(name, 0) >= 1:
            return {
                **error,
                "code": f"{error.get('code', 'invalid_tool_arguments')}_retry_exhausted",
                "error": f"{error.get('error', 'invalid tool call')}（已重试一次，本轮不再执行）",
                "retryable": False,
                "retry_limit": 1,
            }
        attempts[name] = attempts.get(name, 0) + 1
        return {**error, "retryable": True, "retry_limit": 1}

    async def _resolve_direct_verdict(
        self: Any, call: ToolCall
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

    def _refusal_result(self: Any, call: ToolCall, verdict: dict[str, Any]) -> ToolResult:
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
    def _single_task_params(
        item,
    ) -> tuple[str, str, list[str] | None, list[str] | None, str]:
        """单个子任务派发参数：prompt / system_extra / allowlist / deny / model."""
        from spark.agents import subagent_system_prompt

        prompt, agent = item
        system_extra = subagent_system_prompt(agent) if agent else ""
        allowlist = list(agent.tools) if agent and agent.tools else None
        denied = list(agent.denied_tools) if agent and agent.denied_tools else None
        model = agent.model if agent and agent.model else ""
        return prompt, system_extra, allowlist, denied, model

    @staticmethod
    def _parallel_task_params(
        items,
    ) -> tuple[
        list[str], list[str], list[list[str] | None],
        list[list[str] | None], list[str],
    ]:
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
        denylists = [
            list(a.denied_tools) if a and a.denied_tools else None for _, a in items
        ]
        models = [a.model if a and a.model else "" for _, a in items]
        return prompts, system_extras, allowlists, denylists, models

    @staticmethod
    def _single_task_result(summaries: list[str], errors: list[str]) -> ToolResult:
        if errors:
            return ToolResult(ok=False, payload={"error": errors[-1]})
        return ToolResult(
            ok=True, payload={"summary": summaries[-1] if summaries else ""}
        )

    def _collect_task_items(self: Any, call_arguments: dict) -> tuple[list, str]:
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
        self: Any, stream, summaries: list[str], errors: list[str]
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
        self: Any, call: ToolCall, result: ToolResult, started: float, args_summary: str
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

    async def _run_ask_tool(self: Any, call: ToolCall, message_id: int):
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

    async def _run_task_tool(
        self: Any, call: ToolCall, message_id: int, approval_label: str,
        started: float, args_summary: str,
    ):
        items, task_error = self._collect_task_items(call.arguments)
        summaries: list[str] = []
        sub_errors: list[str] = []
        if task_error:
            result = ToolResult(ok=False, payload={"error": task_error})
        elif not items:
            result = ToolResult(ok=False, payload={"error": "prompt required"})
        elif len(items) == 1:
            prompt, system_extra, allowlist, denied, model = self._single_task_params(items[0])
            async for sub_event in self._forward_subtask_events(
                self.spawn_subtask(
                    prompt,
                    system_extra=system_extra,
                    tools_allowlist=allowlist,
                    denied_tools=denied,
                    model=model,
                ),
                summaries,
                sub_errors,
            ):
                yield sub_event
            result = self._single_task_result(summaries, sub_errors)
        else:
            prompts, system_extras, allowlists, denylists, models = self._parallel_task_params(items)
            agent_names = [a.name if a else "" for _, a in items[:4]]
            async for sub_event in self._forward_subtask_events(
                self.spawn_subtasks_parallel(
                    prompts,
                    system_extras=system_extras,
                    tools_allowlists=allowlists,
                    denied_tool_lists=denylists,
                    models=models,
                    agent_names=agent_names,
                ),
                summaries,
                sub_errors,
            ):
                yield sub_event
            result = ToolResult(ok=True, payload={"summaries": summaries})
        self._finish_tool_execution(call, result, started, args_summary)
        await self._persist_tool(call, message_id, result, approval_label)
        yield TurnEvent(type="tool_end", tool_call=call, result=result)

    async def _run_tool_body(
        self: Any, call: ToolCall, message_id: int, verdict: dict[str, Any] | None = None
    ):
        """工具执行主体。

        *verdict* 由 approve 节点预先生成（graph 路径）：
        ``{"action": "allow"|"allow_always"|"deny"|"hook_block"|"plan_only", "reason": str}``。
        提供时跳过判定段（判定已完成，本方法只做副作用与事件）。
        缺省（None，测试/兼容直调路径）走 :meth:`_resolve_direct_verdict` 公共判定，
        prompt 经 approver 回调落定，随后与 graph 路径共用同一执行分派。
        """
        yield TurnEvent(type="tool_start", tool_call=call)
        if self.cancelled:
            # 停止必须真的停下来：判定可能早已是 allow，但用户已按下取消——
            # 本轮尚未执行的排队调用不再执行，只补一条明确的取消结果。
            # tool_call/result 配对不变量由 _persist_tool 维持，否则下一轮
            # 请求会因孤儿 tool 消息被 OpenAI 兼容端点直接 400。
            result = ToolResult(
                ok=False,
                payload={"error": "cancelled before execution", "tool": call.name},
            )
            await self._persist_tool(call, message_id, result, "cancelled")
            yield TurnEvent(type="tool_end", tool_call=call, result=result)
            return
        started = time.monotonic()
        args_summary = json.dumps(call.arguments, ensure_ascii=False, default=str)[:200]
        invalid: dict[str, Any] | None = None
        if verdict is not None:
            invalid = verdict.get("validation")
            if verdict.get("action") == "invalid" and not isinstance(invalid, dict):
                invalid = {
                    "error": verdict.get("reason") or "invalid tool call",
                    "code": "invalid_tool_arguments",
                }
        else:
            invalid = self._validation_error(call)
        if invalid is not None:
            result = ToolResult(ok=False, payload=invalid)
            self._finish_tool_execution(call, result, started, args_summary)
            await self._persist_tool(call, message_id, result, "invalid")
            yield TurnEvent(type="tool_end", tool_call=call, result=result)
            return
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
            async for event in self._run_ask_tool(call, message_id):
                yield event
            return
        if call.name == "task" and self.registry.ctx.task_runner is not None:
            async for event in self._run_task_tool(
                call, message_id, approval_label, started, args_summary
            ):
                yield event
            return
        else:
            result = await self.registry.execute(call)
        self._finish_tool_execution(call, result, started, args_summary)
        await self._persist_tool(call, message_id, result, approval_label)
        yield TurnEvent(type="tool_end", tool_call=call, result=result)

    async def _persist_tool(
        self: Any, call: ToolCall, message_id: int, result: ToolResult, approval: str
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

    def _make_sub_context(self: Any) -> ToolContext:
        sub_cfg = self.cfg.model_copy(deep=True)
        sub_cfg.agent.max_tool_rounds = min(15, max(5, sub_cfg.agent.max_tool_rounds))
        sub_ctx = ToolContext(sandbox=self.registry.ctx.sandbox, config=sub_cfg)
        sub_ctx.mcp_call = self.registry.ctx.mcp_call
        sub_ctx.mcp_bridge = self.registry.ctx.mcp_bridge
        return sub_ctx

    def _inherit_registry(self: Any, sub_registry: ToolRegistry) -> None:
        for schema in self.registry.schemas():
            name = str(((schema.get("function") or {}).get("name")) or "")
            if name.startswith("mcp__"):
                sub_registry.add_mcp_schema(schema)
        sub_registry.readonly_mcp |= set(self.registry.readonly_mcp)
