"""LangGraph 生产控制流的 routing 边界测试。

覆盖新状态机（``build_turn_graph``）的路由边界：

* ``route_after_llm`` 三去向（tools / finalize / END）与缺省安全
* 工厂产物（compiled graph，节点齐备）
* ``AgentTurnState`` 字段面（TypedDict，控制流所需轻量字段）

轮次上限（max_tool_rounds）、预算、熔断的**行为**边界由
``test_langgraph_path.py``（graph 真实驱动）与 ``test_loop.py`` /
``test_reliability.py``（iter_turn 端到端）覆盖——旧版"直接喂 state 给
route 函数"的测试随 while 循环一并退役：轮次判断已移入 ``_llm_round``
（读取 cfg 而非 state 字段），单测 state 字典无法再触发它。
"""

from __future__ import annotations

from langgraph.graph import END

from spark.core.graph import AgentTurnState, build_turn_graph, route_after_llm


def test_route_tools_error_state_ends() -> None:
    """error 非空 → END（turn_error 已发出，不得再进 tools/finalize）。"""
    assert route_after_llm({"error": "Network failure"}) == END


def test_route_tools_with_calls_continues() -> None:
    """有工具调用 → approve（审批节点，interrupt 位点所在）。"""
    assert route_after_llm({"error": None, "tool_calls": [{"id": "c1"}]}) == "approve"


def test_route_tools_no_calls_finalizes() -> None:
    """无工具调用 → finalize。"""
    assert route_after_llm({"error": None, "tool_calls": []}) == "finalize"


def test_route_tools_empty_state_safe_default() -> None:
    """缺省 state（无任何键）→ finalize（安全默认）。"""
    assert route_after_llm({}) == "finalize"


def test_route_tools_error_beats_calls() -> None:
    """error 优先级最高：即便残留 tool_calls 也 END。"""
    assert route_after_llm({"error": "x", "tool_calls": [{"id": "c1"}]}) == END


def test_agent_turn_state_fields() -> None:
    """AgentTurnState 是 TypedDict：字段面即控制流契约。"""
    annotations = AgentTurnState.__annotations__
    assert set(annotations) == {
        "rounds",
        "tool_calls",
        "verdicts",
        "memory_block",
        "assistant_text",
        "error",
    }


def test_graph_factory_creates_graph(tmp_path) -> None:
    """build_turn_graph 返回 compiled graph，节点齐备。"""

    class _FakeLoop:
        pass

    graph = build_turn_graph(_FakeLoop())  # type: ignore[arg-type]
    assert hasattr(graph, "nodes")
    node_names = set(graph.nodes)
    assert {"llm", "approve", "execute", "finalize"} <= node_names
