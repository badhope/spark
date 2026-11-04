"""Goal 长程模式工具：``goal_update`` — 目标/证据/验收契约的进展回报。

设计（对应 REMAINING 里"没有证据门槛的 goal 只是自动循环"的警告）：

* 用户用 ``/goal <目标>`` 设立目标（存 ``goals`` 表，一会话一活跃目标）；
* 系统提示词注入目标契约（见 ``core/loop.py`` 的注入点）；
* agent 每回合用 ``goal_update`` 回报 checklist 进展；
* **证据门槛**：``complete=true`` 必须至少有一条 ``done`` 且带非空
  ``evidence`` 的条目（测试输出、文件路径等可核查内容），否则拒绝——
  空口宣布完成不算完成。
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from pydantic import BaseModel

from spark.models import ToolResult

logger = logging.getLogger(__name__)

_BRIDGE_LOCK = threading.Lock()
_STORE = None
_GET_SESSION_ID: Callable[[], str] | None = None


def attach_bridges(store, get_session_id: Callable[[], str]) -> None:
    """接线：由宿主（web state / CLI）在创建 loop 后调用一次。"""
    global _STORE, _GET_SESSION_ID
    with _BRIDGE_LOCK:
        _STORE = store
        _GET_SESSION_ID = get_session_id


class ChecklistItem(BaseModel):
    item: str
    done: bool = False
    evidence: str = ""


class GoalUpdateArgs(BaseModel):
    """整表替换 checklist：每次回报都必须带上全部条目的最新状态。"""

    checklist: list[ChecklistItem]
    note: str = ""
    complete: bool = False


def _render_summary(goal: dict) -> str:
    checklist = goal.get("checklist") or []
    if not checklist:
        return f"目标：{goal['objective']}\n（清单为空——请先拆解出可核查的步骤条目）"
    lines = [f"目标：{goal['objective']}（{goal['status']}）"]
    for c in checklist:
        mark = "✅" if c.get("done") else "⬜"
        ev = f"（证据：{c['evidence']}）" if c.get("evidence") else ""
        lines.append(f"{mark} {c['item']}{ev}")
    if goal.get("note"):
        lines.append(f"备注：{goal['note']}")
    return "\n".join(lines)


def goal_update_tool(args: GoalUpdateArgs) -> ToolResult:
    """回报目标进展；complete=true 受证据门槛约束。"""
    with _BRIDGE_LOCK:
        store = _STORE
        get_session_id = _GET_SESSION_ID
    if store is None or get_session_id is None:
        return ToolResult(ok=False, payload={"error": "goal 未接线（宿主未 attach）"})
    session_id = get_session_id()
    goal = store.get_active_goal(session_id)
    if goal is None:
        return ToolResult(
            ok=False,
            payload={"error": "当前会话没有活跃目标（用户可用 /goal <目标> 设立）"},
        )
    checklist = [item.model_dump() for item in args.checklist]
    if args.complete:
        evidence_items = [
            c for c in checklist if c.get("done") and c.get("evidence", "").strip()
        ]
        if not checklist or not evidence_items:
            return ToolResult(
                ok=False,
                payload={
                    "error": (
                        "证据门槛未满足：complete=true 需要至少一条 done=true 且"
                        " evidence 非空的条目（可核查的测试输出/文件路径/命令结果）"
                    ),
                    "goal": goal,
                },
            )
        goal["checklist"] = checklist
        goal["note"] = args.note
        updated = store.close_goal(goal["id"], "done")
        return ToolResult(
            ok=True,
            payload={
                "goal": updated,
                "summary": _render_summary({**goal, "status": "done"}),
                "completed": True,
            },
        )
    goal["checklist"] = checklist
    goal["note"] = args.note
    updated = store.upsert_goal(goal)
    return ToolResult(
        ok=True,
        payload={"goal": updated, "summary": _render_summary(updated), "completed": False},
    )
