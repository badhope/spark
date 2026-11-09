"""LangGraph 驱动的 ReAct 控制流——**唯一真相源**。

生产路径（CLI / TUI / Web / ACP 的 ``AgentLoop.iter_turn``）直接驱动本模块
构建的状态机；手写 while 循环已删除（A11 消除）。职责切分：

- **graph（本模块）**：轮次控制流——``llm → approve → execute → llm → … →
  finalize``。审批（prompt 档）在 approve 节点经 LangGraph ``interrupt``
  挂起，外层消费 ``__interrupt__`` 调 approver 回调后 resume。
- **loop（loop.py）**：外围职责——历史/存储、记忆、压缩、取消、token 计账、
  hooks、检查点快照、子代理。

事件泵：节点内通过 LangGraph custom stream（:func:`get_stream_writer`）实时
推送 ``TurnEvent``；``iter_turn`` 以 ``astream(stream_mode=["custom",
"updates"])`` 消费，``updates`` 通道用于感知 ``__interrupt__``。

**interrupt 语义约束（节点重跑）**：resume 时节点从头重跑，interrupt 之前
的副作用会重复。因此 approve 节点在 interrupt 前**不 emit 任何事件、不触发
任何 hook**——``tool_start`` / ``approval_needed`` 事件与 hook 通知全部在
execute 节点或外层发出，保证恢复重跑零重复。

Checkpointer：``build_turn_graph(loop, checkpointer=...)``——主会话传
``TurnCheckpointer.saver``（AsyncSqliteSaver，审批挂起跨进程可恢复，A10），
子代理/短生命周期场景传 MemorySaver。

下一批：Web UI 恢复入口接线（后端 API 已就绪）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph

from spark.models import ToolCall

if TYPE_CHECKING:  # pragma: no cover - 仅类型引用，避免运行时循环依赖
    from spark.core.loop import AgentLoop

logger = logging.getLogger(__name__)

__all__ = ["AgentTurnState", "build_turn_graph", "route_after_llm"]


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class AgentTurnState(TypedDict, total=False):
    """单次 turn 的可序列化轮次状态。

    只放控制流需要的轻量字段；不可序列化的协作者（provider/registry/store）
    全部经闭包绑定到节点，LangGraph 永远不尝试序列化它们。
    """

    rounds: int                    # 已完成的工具执行轮数
    tool_calls: list[ToolCall]     # 本轮 LLM 产出、待执行的调用
    verdicts: list[dict[str, Any]]  # approve 节点产出：逐 call 的执行判定
    assistant_text: str            # 本轮 LLM 可见文本：随 checkpoint 落盘，崩溃恢复不丢
    memory_block: str | None       # turn 开始时检索一次的记忆上下文
    error: str | None              # 非空 = 致命停止（turn_error 已发出）


# ---------------------------------------------------------------------------
# Nodes（闭包绑定 loop 实例）
# ---------------------------------------------------------------------------


def _make_node_llm(loop: "AgentLoop"):
    async def node_llm(state: AgentTurnState) -> dict[str, Any]:
        """一轮 LLM 调用：停止条件检查 → 流式调模型 → 计账 → 熔断检查。"""
        writer = get_stream_writer()

        async for event in loop._llm_round(
            state.get("memory_block"), int(state.get("rounds", 0))
        ):
            writer(event)

        result = loop._last_round
        return {
            "rounds": int(state.get("rounds", 0)),
            "tool_calls": result.get("tool_calls", []),
            "assistant_text": result.get("text", ""),
            "error": result.get("error"),
        }

    return node_llm


def _make_node_approve(loop: "AgentLoop"):
    async def node_approve(state: AgentTurnState) -> dict[str, Any]:
        """审批阶段：逐 call 判定（hooks 前置结论/plan-only/策略/interrupt）。

        本节点**不 emit 事件、不触发 hook**——resume 时节点从头重跑，
        interrupt 前的任何副作用都会重复（见模块 docstring）。
        """
        tool_calls = state.get("tool_calls") or []
        verdicts = await loop._decide_calls(tool_calls)
        return {"verdicts": verdicts}

    return node_approve


def _make_node_execute(loop: "AgentLoop"):
    async def node_execute(state: AgentTurnState) -> dict[str, Any]:
        """执行阶段：assistant 入历史 → 逐个跑工具（事件实时透传）→ rounds+1。"""
        writer = get_stream_writer()
        tool_calls = state.get("tool_calls") or []
        verdicts = state.get("verdicts") or []

        # 优先取 state 里的本轮文本：跨进程崩溃恢复时 loop._last_round
        # 是新进程的初始值（text=""），模型这轮的可见文本会静默丢失；
        # state 随 checkpoint 落盘，恢复后仍是原值。旧 checkpoint 无该
        # 键时回退旧行为，不产生新失败面。
        round_text = state.get("assistant_text", loop._last_round.get("text", ""))
        async for event in loop._execute_round(tool_calls, verdicts, round_text):
            writer(event)

        return {
            "rounds": int(state.get("rounds", 0)) + 1,
            "tool_calls": [],
            "verdicts": [],
            "error": None,
        }

    return node_execute


def _make_node_finalize(loop: "AgentLoop"):
    async def node_finalize(state: AgentTurnState) -> dict[str, Any]:
        """收尾：assistant 入历史 → 检查点 → context/预算 → turn_end。"""
        writer = get_stream_writer()

        round_text = state.get("assistant_text", loop._last_round.get("text", ""))
        async for event in loop._finalize_turn(round_text):
            writer(event)

        return {"error": None}

    return node_finalize


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def route_after_llm(state: AgentTurnState) -> str:
    """llm 节点后的转移：致命停止 → END；有工具调用 → approve；否则收尾。"""
    if state.get("error"):
        return END
    if state.get("tool_calls"):
        return "approve"
    return "finalize"


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------


def build_turn_graph(loop: "AgentLoop", checkpointer: Any = None) -> Any:
    """为 *loop* 构建单 turn 的 ReAct 状态机（CompiledStateGraph）。

    graph 跨 turn 复用（无 turn 间状态，state 每次新建）；节点闭包绑定
    loop 实例。*checkpointer* 提供时审批 interrupt 位点落盘（turn 级崩溃
    恢复，A10）；缺省 MemorySaver 语义由调用方保证——interrupt 必须有
    checkpointer 才能工作。
    """
    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver

        checkpointer = MemorySaver()

    graph = StateGraph(AgentTurnState)
    graph.add_node("llm", _make_node_llm(loop))
    graph.add_node("approve", _make_node_approve(loop))
    graph.add_node("execute", _make_node_execute(loop))
    graph.add_node("finalize", _make_node_finalize(loop))

    graph.set_entry_point("llm")
    graph.add_conditional_edges(
        "llm", route_after_llm, ["approve", "finalize", END]
    )
    graph.add_edge("approve", "execute")
    graph.add_edge("execute", "llm")

    return graph.compile(checkpointer=checkpointer)
