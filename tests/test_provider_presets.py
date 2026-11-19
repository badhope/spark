"""W2: native provider presets, prompt-cache markers and cost ledger."""

from __future__ import annotations

from pathlib import Path

from spark.config import PROVIDER_NAMES
from spark.model_catalog import _FALLBACK_SNAPSHOT, estimate_cost
from spark.providers.openai_compat import (
    OpenAICompatProvider,
    _apply_anthropic_cache,
    _apply_anthropic_tool_cache,
)
from spark.providers.presets import NATIVE_PRESETS, PRESETS, get_preset, presets_payload
from spark.store import SessionStore


def test_all_native_names_are_valid_provider_names() -> None:
    assert {"anthropic", "openai", "gemini", "azure", "bedrock"} <= set(PROVIDER_NAMES)
    assert {p.provider for p in NATIVE_PRESETS} == {
        "anthropic", "openai", "gemini", "azure", "bedrock"
    }


def test_presets_are_serialisable_and_addressable() -> None:
    rows = presets_payload()
    assert len(rows) == len(PRESETS)
    assert {row["id"] for row in rows} >= {"deepseek", "anthropic", "bedrock"}
    assert get_preset("ANTHROPIC") is not None
    assert get_preset("not-a-provider") is None
    assert len(presets_payload(native_only=True)) == 5


def test_native_prefix_is_added_only_for_bare_models() -> None:
    bare = OpenAICompatProvider("", "claude-sonnet-4-5", "k", native="anthropic")
    assert bare._litellm_model() == ("anthropic/claude-sonnet-4-5", None)
    already = OpenAICompatProvider("", "anthropic/claude-sonnet-4-5", "k", native="anthropic")
    assert already._litellm_model()[0] == "anthropic/claude-sonnet-4-5"
    compat = OpenAICompatProvider("https://api.deepseek.com/v1", "deepseek-chat", "k")
    assert compat._litellm_model()[0] == "deepseek-chat"
    responses = OpenAICompatProvider("", "gpt-5", "k", native="openai", responses_api=True)
    assert responses._litellm_model() == ("openai/responses/gpt-5", None)


def test_anthropic_cache_marker_only_touches_system_prefix() -> None:
    messages = [
        {"role": "system", "content": "stable rules"},
        {"role": "user", "content": "changing"},
    ]
    marked = _apply_anthropic_cache(messages)
    assert marked[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert marked[1] == messages[1]
    # The input list must not be mutated in place (callers reuse history).
    assert messages[0]["content"] == "stable rules"


def test_anthropic_tool_cache_marks_last_schema_without_mutating_input() -> None:
    tools = [{"type": "function", "function": {"name": "read_file"}}]
    marked = _apply_anthropic_tool_cache(tools)
    assert marked[-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in tools[-1]


def test_estimate_cost_discounts_cache_read_subset() -> None:
    catalog = _FALLBACK_SNAPSHOT
    no_cache = estimate_cost(catalog, "anthropic", "claude-sonnet-4-5", 1000, 100)
    with_cache = estimate_cost(
        catalog, "anthropic", "claude-sonnet-4-5", 1000, 100, cache_read_tokens=500
    )
    assert no_cache is not None and with_cache is not None
    assert with_cache < no_cache


def test_session_ledger_keeps_model_and_cache(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session(tmp_path, "deepseek-chat", "t")
    store.append_token_usage(
        sid,
        None,
        1000,
        100,
        0,
        source="reported",
        model="deepseek-chat",
        provider="deepseek",
        cache_read_tokens=400,
    )
    usage = store.session_token_usage(sid, model="deepseek-chat")
    assert usage["cache_read_tokens"] == 400
    assert usage["cache_hit_rate"] == 0.4
    assert usage["cost_usd"] and usage["cost_usd"] > 0
    rows = store.session_usage_by_model(sid)
    assert rows[0]["model"] == "deepseek-chat"
    assert rows[0]["cache_read_tokens"] == 400
