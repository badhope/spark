from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.core.context import build_messages, history_token_usage
from spark.core.loop import AgentLoop
from spark.core.tokens import estimate_tokens
from spark.models import ChatDelta, ChatMessage, ToolCall
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry


def test_estimate_tokens_cjk_vs_ascii() -> None:
    assert estimate_tokens("") == 0
    # Exact counts depend on the tokenizer (tiktoken o200k_base vs the
    # heuristic fallback); assert the properties that matter for budgeting.
    assert 1 <= estimate_tokens("abcd") <= 2
    assert 3 <= estimate_tokens("四个中文字符") <= 8
    mixed = "use pnpm 四个中文字符"
    assert estimate_tokens(mixed) > 0
    assert estimate_tokens(mixed, model="gpt-4o") > 0


def test_summary_role_roundtrip(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session(tmp_path, "mock")
    store.append_message(sid, ChatMessage(role="user", content="old"))
    store.append_message(sid, ChatMessage(role="assistant", content="old answer"))
    summary = ChatMessage(role="summary", content="<conversation_summary>digest</conversation_summary>")
    summary_id = store.append_message(sid, summary)
    store.set_compact_from(sid, summary_id)
    store.append_message(sid, ChatMessage(role="user", content="new question"))
    loaded = store.load_messages(sid)
    assert [m.role for m in loaded] == ["summary", "user"]
    assert "digest" in (loaded[0].content or "")


def test_build_messages_includes_summary(tmp_path: Path) -> None:
    cfg = SparkConfig()
    history = [
        ChatMessage(role="summary", content="<conversation_summary>digest</conversation_summary>"),
        ChatMessage(role="user", content="hello"),
    ]
    out = build_messages(workdir=tmp_path, cfg=cfg, history=history)
    roles = [m.role for m in out]
    assert roles[0] == "system"
    assert "summary" in roles
    assert roles[-1] == "user"


def test_history_token_usage_shape(tmp_path: Path) -> None:
    cfg = SparkConfig()
    history = [ChatMessage(role="user", content="a" * 400)]
    usage = history_token_usage(cfg=cfg, history=history, workdir=tmp_path, tool_overhead_tokens=100)
    assert usage["limit"] == cfg.context.max_context_tokens
    assert usage["used"] > 100
    assert 0 < usage["percent"] <= 100


def _loop(tmp_path: Path, provider: MockProvider, **overrides) -> AgentLoop:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    for key, value in overrides.items():
        setattr(cfg.context, key, value)
    store = SessionStore(tmp_path / "s.db")
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
async def test_auto_compaction_triggers(tmp_path: Path) -> None:
    provider = MockProvider(rounds=[[ChatDelta(type="text", text="chunk summary"), ChatDelta(type="end")]])
    loop = _loop(tmp_path, provider, max_context_tokens=200, compact_threshold=0.5, keep_recent_messages=2)
    for i in range(12):
        loop.store.append_message(loop.session_id, ChatMessage(role="user", content=f"question {i} " + "x" * 300))
        loop.store.append_message(loop.session_id, ChatMessage(role="assistant", content=f"answer {i} " + "y" * 300))
    loop.history = loop.store.load_messages(loop.session_id)
    events = await loop.run("new question")
    types = [e.type for e in events]
    assert "compaction" in types
    compaction = next(e for e in events if e.type == "compaction")
    assert compaction.data["before_tokens"] > compaction.data["after_tokens"]
    assert compaction.data["summarized_messages"] > 0
    # Compaction must not swallow the recent tail: the cursor is the id of the
    # first *retained* message. Asserting retention here is what keeps the
    # summary-from-own-id regression from coming back.
    keep = loop.cfg.context.keep_recent_messages
    assert any(m.role == "summary" for m in loop.history)
    assert len([m for m in loop.history if m.role != "summary"]) >= keep
    assert loop.history[-1].role == "assistant"
    assert events[-1].type == "turn_end"
    reloaded = loop.store.load_messages(loop.session_id)
    assert any(m.role == "summary" for m in reloaded)
    assert len([m for m in reloaded if m.role != "summary"]) >= keep
    assert any(m.role == "user" and m.content == "new question" for m in reloaded)
    # The summary is still hoisted ahead of the retained tail in the prompt that
    # actually reaches the provider (spark.core.context.build_messages).
    sent = build_messages(workdir=tmp_path, cfg=loop.cfg, history=loop.history)
    roles = [m.role for m in sent]
    assert "summary" in roles
    assert roles.index("summary") < min(
        i for i, r in enumerate(roles) if r in {"user", "assistant"}
    )


@pytest.mark.asyncio
async def test_plan_tool_updates_registry(tmp_path: Path) -> None:
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="p1",
                        name="update_plan",
                        arguments={"steps": [{"title": "step one", "status": "in_progress"}, {"title": "step two"}]},
                    ),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="planned"), ChatDelta(type="end")],
        ]
    )
    loop = _loop(tmp_path, provider)
    events = await loop.run("make a plan")
    plan_events = [e for e in events if e.type == "plan"]
    assert plan_events
    assert plan_events[0].data["steps"][0]["status"] == "in_progress"
    assert plan_events[0].data["steps"][1]["status"] == "pending"
    assert loop.registry.plan[0]["title"] == "step one"


def test_provider_summary_mapping() -> None:
    from spark.providers.openai_compat import _to_openai

    msgs = [ChatMessage(role="summary", content="digest"), ChatMessage(role="user", content="hi")]
    out = _to_openai(msgs)
    assert out[0]["role"] == "user"
    assert "<conversation_summary>" in out[0]["content"]
    assert out[1] == {"role": "user", "content": "hi"}
