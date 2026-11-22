from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, cast

from spark.config import ApprovalMode, SparkConfig
from spark.models import ApprovalRequest, ChatMessage, ToolCall, TurnEvent
from spark.policy import READONLY_TOOLS, decide

logger = logging.getLogger(__name__)


class TurnControlMixin:
    _plan_approved: bool | None
    async def _decide_calls(self: Any, tool_calls: list[ToolCall]) -> list[dict[str, Any]]:
        """审批阶段判定（approve 节点专用，可被 interrupt 挂起）。

        返回逐 call 的判定列表，元素形如：
        ``{"action": "allow"|"allow_always"|"deny"|"hook_block"|"plan_only", "reason": str}``。

        prompt 档经 LangGraph ``interrupt`` 挂起等待外层审批；本方法**不得
        emit 事件或触发 hook**——resume 时 approve 节点从头重跑，interrupt
        之前的副作用会重复（graph.py 模块 docstring）。
        """
        from langgraph.types import interrupt

        verdicts: list[dict[str, Any]] = []
        schemas = self.registry.schemas()
        for call in tool_calls:
            invalid = self._validation_error(call, schemas=schemas)
            if invalid is not None:
                verdicts.append(
                    {
                        "action": "invalid",
                        "reason": invalid.get("error", "invalid tool call"),
                        "validation": invalid,
                    }
                )
                continue
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

    def _classify_call(self: Any, call: ToolCall) -> dict[str, Any]:
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

    def _classification_key(self: Any, call: ToolCall) -> str:
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

    def _approval_mode(self: Any) -> ApprovalMode:
        """生效审批模式：H4 档案覆盖 > 配置基线（零配置突变）。"""
        if self._approval_override:
            return cast(ApprovalMode, self._approval_override)
        return self.cfg.agent.approval

    def _goal_block(self: Any) -> str:
        """Goal 长程模式：活跃目标存在时注入目标契约（目标/进展/规则）。

        每回合动态读取（goal_update 工具会改 checklist，注入必须反映最新
        状态）；读取失败静默跳过——目标系统故障不能阻断普通对话。
        """
        try:
            goal = self.store.get_active_goal(self.session_id)
        except Exception:  # noqa: BLE001
            logger.debug("读取活跃目标失败", exc_info=True)
            return ""
        if not goal:
            return ""
        checklist = goal.get("checklist") or []
        lines = [
            "## 活跃目标（Goal 长程模式）",
            f"目标：{goal['objective']}",
            "",
        ]
        if checklist:
            for c in checklist:
                mark = "[x]" if c.get("done") else "[ ]"
                ev = f" — 证据：{c['evidence']}" if c.get("evidence") else ""
                lines.append(f"{mark} {c['item']}{ev}")
        else:
            lines.append("（清单为空：请先用 goal_update 拆解出可核查的步骤条目）")
        if goal.get("note"):
            lines.append(f"备注：{goal['note']}")
        lines += [
            "",
            "规则：",
            "1. 每回合结束前用 goal_update 工具回报最新 checklist（整表替换，别丢条目）。",
            "2. 标记 done 的条目必须附可核查证据（测试输出/文件路径/命令结果）。",
            "3. 全部条目 done 且证据充分后才可 complete=true；证据不足会被拒绝。",
            "4. 目标由用户用 /goal done|cancel 收口，工具里的 complete 只是申请。",
        ]
        return "\n".join(lines)

    def _apply_approval_profile(self: Any, cfg: SparkConfig) -> None:
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
        self: Any,
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
        for call, verdict in zip(tool_calls, verdicts, strict=True):
            update_ok: bool | None = None
            async for event in self._run_tool(call, msg_id, verdict=verdict):
                if event.type == "tool_end" and event.result is not None:
                    update_ok = event.result.ok
                yield event
            if call.name == "update_plan" and update_ok is not False:
                # plan 事件与「本次更新成功」绑定：
                # - 失败（参数不合法/被拒绝）时不再重发旧计划——此前旧计划
                #   非空会触发重发并再次进入审批门（重复打扰用户）；
                # - 成功时即使计划为空也要发——否则 UI 永远保留过期计划，
                #   模型无法用空 steps 清屏。
                self._notify_safe("plan_update", {"steps": self.registry.plan})
                yield TurnEvent(type="plan", data={"steps": self.registry.plan})
                # approve-plan：计划一经产出即进入计划审批门（空计划无物可批）
                if self.registry.plan:
                    async for event in self._maybe_approve_plan(call):
                        yield event

    async def _finalize_turn(self: Any, text: str = ""):
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

    def _maybe_auto_checkpoint(self: Any, assistant_id: int) -> None:
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

    def rollback_to_checkpoint(self: Any, checkpoint_id: int) -> dict:
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

    async def _maybe_approve_plan(self: Any, call: ToolCall):
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
