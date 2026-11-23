from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.core.loop import AgentLoop
from spark.models import ChatDelta
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry


def _loop(tmp_path: Path, provider: MockProvider) -> AgentLoop:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    cfg.agent.show_metrics = True
    cfg.agent.max_tool_rounds = 4
    store = SessionStore(tmp_path / "metrics.db")
    sid = store.create_session(tmp_path, "mock")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
    return AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=provider,
        registry=ToolRegistry(ctx),
        store=store,
        session_id=sid,
    )


@pytest.mark.asyncio
async def test_metrics_event_and_persistence(tmp_path: Path) -> None:
    provider = MockProvider(
        rounds=[
            [ChatDelta(type="text", text="done"), ChatDelta(type="end")],
        ]
    )
    loop = _loop(tmp_path, provider)
    events = await loop.run("say done")
    assert events[-1].type == "metrics"
    data = events[-1].data or {}
    assert data["version"] == 1
    assert data["rounds"] == 1
    assert data["ttft_ms"] is not None
    assert data["total_ms"] >= data["ttft_ms"]
    assert data["render_ms"] is None
    rows = loop.store.list_turn_metrics(loop.session_id)
    assert len(rows) == 1
    assert rows[0]["metrics"]["version"] == 1
