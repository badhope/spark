"""OTel 观测（GenAI 语义约定）测试：trace=turn、span=tool、metric；默认关语义。

in-memory 模式（SPARK_OTEL_ENDPOINT=inmemory）下断言 span 树与 attributes；
未启用时断言全链路无 span 产生（默认关）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from spark.config import SparkConfig
from spark.core.loop import AgentLoop
from spark.models import ApprovalDecision, ApprovalRequest, ChatDelta, ToolCall
from spark.otel import (
    in_memory_exporter,
    in_memory_log_exporter,
    in_memory_metric_reader,
    record_event,
    reset_for_tests,
    telemetry_enabled,
)
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry


@pytest.fixture(autouse=True)
def _otel_env(monkeypatch):
    """每个用例独立 otel 状态；in-memory 模式由参数控制。"""
    monkeypatch.setenv("SPARK_OTEL_ENDPOINT", "inmemory")
    monkeypatch.delenv("SPARK_OTEL_ENABLED", raising=False)
    reset_for_tests()
    yield
    reset_for_tests()


def _tool_provider() -> MockProvider:
    return MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(id="c1", name="list_dir", arguments={"path": "."}),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="done"), ChatDelta(type="end")],
        ]
    )


def _loop(
    tmp_path: Path,
    provider: Any,
    approval: str = "full-auto",
    approver=None,
    checkpointer=None,
) -> AgentLoop:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = approval  # type: ignore[assignment]
    store = SessionStore(Path(":memory:"))
    sid = store.create_session(tmp_path, cfg.provider.model, title="t")
    registry = ToolRegistry(
        ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg)
    )
    return AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=provider,
        registry=registry,
        store=store,
        session_id=sid,
        approver=approver,
        checkpointer=checkpointer,
    )


def _finished_spans():
    from spark.otel import flush

    flush()  # BatchSpanProcessor 批量导出（默认 5s 延迟），测试需显式冲刷
    exporter = in_memory_exporter()
    assert exporter is not None, "in-memory exporter 未就绪"
    exporter.force_flush()
    return list(exporter.get_finished_spans())


async def test_default_off_produces_no_spans(tmp_path: Path, monkeypatch) -> None:
    """默认关：未设置 endpoint 时全链路零 span（无 in-memory exporter）。"""
    monkeypatch.delenv("SPARK_OTEL_ENDPOINT", raising=False)
    assert not telemetry_enabled()
    reset_for_tests()

    loop = _loop(tmp_path, _tool_provider())
    events = [e async for e in loop.iter_turn("go")]
    assert events[-1].type == "turn_end"
    assert in_memory_exporter() is None  # 未启用 → 根本没有 exporter


async def test_turn_and_tool_spans_with_genai_attrs(tmp_path: Path) -> None:
    """trace=turn + span=tool：GenAI 语义 attributes 齐备，tool 为 turn 子级。"""
    loop = _loop(tmp_path, _tool_provider())
    events = [e async for e in loop.iter_turn("list files")]
    assert events[-1].type == "turn_end"

    spans = _finished_spans()
    names = [s.name for s in spans]
    assert "spark.turn" in names
    assert "spark.tool" in names

    turn = next(s for s in spans if s.name == "spark.turn")
    assert turn.attributes["gen_ai.system"] == "mock"
    assert turn.attributes["gen_ai.request.model"] == "deepseek-chat"
    assert turn.attributes["gen_ai.conversation.id"] == loop.session_id
    # 估算口径 usage（mock provider 无真实上报）
    assert turn.attributes["spark.usage.source"] == "estimated"
    assert turn.attributes["gen_ai.usage.input_tokens"] > 0
    assert "spark.turn.latency_ms" in turn.attributes

    tool = next(s for s in spans if s.name == "spark.tool")
    assert tool.attributes["gen_ai.tool.name"] == "list_dir"
    assert tool.attributes["spark.tool.ok"] is True
    assert tool.attributes["spark.tool.duration_ms"] >= 0
    # parent 关系：tool span 的 parent 即 turn span
    assert tool.parent is not None and tool.parent.span_id == turn.context.span_id


async def test_tool_failure_span_marks_error(tmp_path: Path) -> None:
    """工具失败：tool span 置 ERROR 状态。"""
    loop = _loop(tmp_path, _tool_provider())
    # 让 registry 的 list_dir 在不存在路径上失败
    from spark.models import ToolResult

    original = loop.registry.execute

    async def failing(call):
        if call.name == "list_dir":
            return ToolResult(ok=False, payload={"error": "boom"})
        return await original(call)

    loop.registry.execute = failing  # type: ignore[method-assign]
    [e async for e in loop.iter_turn("list files")]

    spans = _finished_spans()
    tool = next(s for s in spans if s.name == "spark.tool")
    assert tool.attributes["spark.tool.ok"] is False
    assert tool.status.is_ok is False


async def test_no_span_leak_after_cancel(tmp_path: Path) -> None:
    """turn 异常退出也必须 end span（无泄漏——NoOp/Recording 语义一致）。"""
    loop = _loop(tmp_path, _tool_provider())
    loop.cancel()
    events = [e async for e in loop.iter_turn("go")]
    assert events[-1].type == "turn_error"
    spans = _finished_spans()
    assert "spark.turn" in [s.name for s in spans]


async def test_resumed_turn_span(tmp_path: Path) -> None:
    """恢复 turn：span 带 spark.turn.resumed=True。"""
    from spark.core.turn_store import TurnCheckpointer

    cp = TurnCheckpointer(tmp_path / "cp.db")
    await cp.start()
    try:
        loop_a = _loop(
            tmp_path,
            _tool_provider(),
            approval="suggest",
            checkpointer=cp,
        )
        # suggest 下 list_dir 是只读工具不触发审批——用 run_shell 触发
        provider = MockProvider(
            rounds=[
                [
                    ChatDelta(
                        type="tool_call",
                        tool_call=ToolCall(id="c1", name="run_shell", arguments={"command": "echo x"}),
                    ),
                    ChatDelta(type="end"),
                ],
                [ChatDelta(type="text", text="done"), ChatDelta(type="end")],
            ]
        )
        loop_a.provider = provider
        async for event in loop_a.iter_turn("run"):
            if event.type == "approval_needed":
                break
        pending = await cp.list_pending()
        assert len(pending) == 1

        async def allow(request: ApprovalRequest) -> ApprovalDecision:
            return ApprovalDecision(tool_call_id=request.tool_call.id, action="allow")

        loop_b = _loop(
            tmp_path,
            provider,
            approval="suggest",
            approver=allow,
            checkpointer=cp,
        )
        [e async for e in loop_b.resume_pending_turn(pending[0]["thread_id"])]

        spans = _finished_spans()
        turn_spans = [s for s in spans if s.name == "spark.turn"]
        assert any(s.attributes.get("spark.turn.resumed") is True for s in turn_spans)
    finally:
        await cp.close()


async def test_metrics_and_audit_logs_export_in_memory(tmp_path: Path) -> None:
    """metric reader 与 log exporter 真实产出，不只是 span。"""
    loop = _loop(tmp_path, _tool_provider())
    events = [e async for e in loop.iter_turn("list files")]
    assert events[-1].type == "turn_end"
    record_event("spark.test.audit", {"decision": "allow"}, body="approved")

    from spark.otel import flush

    flush()
    reader = in_memory_metric_reader()
    assert reader is not None
    data = reader.get_metrics_data()
    names = {
        metric.name
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert "spark.turn.tokens" in names
    assert "spark.turn.latency_ms" in names

    exporter = in_memory_log_exporter()
    assert exporter is not None
    exporter.force_flush()
    records = list(exporter.get_finished_logs())
    assert records
    record = records[-1].log_record
    assert record.body == "approved"
    assert record.attributes["event.name"] == "spark.test.audit"


def test_record_event_redacts_credentials() -> None:
    """审计事件不能把凭据写进 OTLP。"""
    from spark.otel import init_telemetry

    init_telemetry()
    record_event(
        "spark.test.secret",
        {"api_key": "sk-live-123", "input_tokens": 12, "nested": {"password": "p"}},
        body="check",
    )
    from spark.otel import flush

    flush()
    exporter = in_memory_log_exporter()
    assert exporter is not None
    exporter.force_flush()
    record = list(exporter.get_finished_logs())[-1].log_record
    assert record.attributes["api_key"] == "[REDACTED]"
    assert record.attributes["input_tokens"] == 12
    assert record.attributes["nested"]["password"] == "[REDACTED]"


def test_stdlib_logging_bridge_exports(monkeypatch) -> None:
    """显式开启后，stdlib logging 真实进入 OTel log exporter。"""
    import logging

    monkeypatch.setenv("SPARK_OTEL_BRIDGE_LOGGING", "1")
    from spark.otel import flush, reset_for_tests

    from spark.otel import init_telemetry

    reset_for_tests()
    init_telemetry()
    logging.getLogger("spark.test.bridge").warning("bridge hello")
    flush()
    exporter = in_memory_log_exporter()
    assert exporter is not None
    exporter.force_flush()
    bodies = [r.log_record.body for r in exporter.get_finished_logs()]
    assert "bridge hello" in bodies
