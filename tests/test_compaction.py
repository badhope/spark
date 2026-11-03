"""Tests for Spark structured compaction (Compactor.summarize / merge_summaries)."""
from unittest.mock import AsyncMock, patch

import pytest

from spark.config import SparkConfig
from spark.core.compact import STRUCTURED_SUMMARY_PROMPT, Compactor
from spark.models import ChatDelta, ChatMessage
from spark.providers.mock import MockProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compactor(tmp_path, responses):
    """Create a Compactor with a MockProvider.

    ``responses`` is a list of strings; each string is returned as the
    ``message.content`` from a mocked ``litellm.acompletion`` call.
    The MockProvider is still wired in so the Compactor initializes cleanly,
    but all LLM calls are intercepted via ``patch`` in the async tests.
    """
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock"
    provider = MockProvider(rounds=[[ChatDelta(type="text", text="stub")]])
    return Compactor(provider=provider, cfg=cfg)


def _mock_litellm(responses):
    """Return a context manager that patches ``spark.core.compact.litellm.acompletion``.

    Each call to ``litellm.acompletion`` pops the next string from *responses*
    (in order) and returns a mock object whose ``.choices[0].message.content``
    equals that string.
    """
    responses_iter = iter(responses)

    async def _fake_acompletion(*args, **kwargs):
        text = next(responses_iter)
        msg = AsyncMock()
        msg.content = text
        choice = AsyncMock()
        choice.message = msg
        resp = AsyncMock()
        resp.choices = [choice]
        return resp

    return patch("spark.core.compact.litellm.acompletion", new=AsyncMock(side_effect=_fake_acompletion))


# ---------------------------------------------------------------------------
# Prompt content
# ---------------------------------------------------------------------------

def test_structured_summary_prompt_exists():
    assert "## Objective" in STRUCTURED_SUMMARY_PROMPT
    assert "## Important Details" in STRUCTURED_SUMMARY_PROMPT
    assert "## Work State" in STRUCTURED_SUMMARY_PROMPT
    assert "## Next Move" in STRUCTURED_SUMMARY_PROMPT
    assert "## Relevant Files" in STRUCTURED_SUMMARY_PROMPT


# ---------------------------------------------------------------------------
# summarize — edge cases (no litellm needed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_summarize_returns_none_for_empty_messages(tmp_path):
    c = _compactor(tmp_path, [])
    result = await c.summarize([])
    assert result is None


@pytest.mark.asyncio
async def test_summarize_all_summary_returns_none(tmp_path):
    c = _compactor(tmp_path, [])
    msgs = [
        ChatMessage(role="summary", content="Prior summary"),
    ]
    result = await c.summarize(msgs)
    assert result is None  # nothing meaningful to summarize


# ---------------------------------------------------------------------------
# summarize — happy path (litellm mocked)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_summarize_produces_output(tmp_path):
    mock_text = (
        "## Objective\nTest objective\n"
        "## Important Details\nN/A\n"
        "## Work State\n- Completed: initial\n- Active: summarizing\n- Blocked: none\n"
        "## Next Move\nRun tests\n"
        "## Relevant Files\n(none)"
    )
    with _mock_litellm([mock_text]):
        c = _compactor(tmp_path, [])
        msgs = [
            ChatMessage(role="user", content="hello"),
            ChatMessage(role="assistant", content="hi there"),
        ]
        result = await c.summarize(msgs)
    assert result is not None
    assert len(result) > 0
    assert "## Objective" in result


@pytest.mark.asyncio
async def test_summarize_with_prior_summary_merges(tmp_path):
    new_summary = "## Objective\nNew objective\n## Important Details\nSome details\n## Work State\n- Completed: prior\n- Active: new work\n- Blocked: none\n## Next Move\nContinue\n## Relevant Files\n(none)"
    merged_summary = "## Objective\nMerged objective\n## Important Details\nMerged details\n## Work State\n- Completed: prior\n- Active: merged work\n- Blocked: none\n## Next Move\nContinue merged\n## Relevant Files\n(none)"

    with _mock_litellm([new_summary, merged_summary]):
        c = _compactor(tmp_path, [])
        msgs = [
            ChatMessage(role="summary", content="## Objective\nOld objective\n## Important Details\nOld details\n## Work State\n- Completed: old\n- Active: old work\n- Blocked: none\n## Next Move\nOld move\n## Relevant Files\n(none)"),
            ChatMessage(role="user", content="next message"),
            ChatMessage(role="assistant", content="response"),
        ]
        result = await c.summarize(msgs)
    assert result is not None
    assert "Merged" in result or "objective" in result.lower()


# ---------------------------------------------------------------------------
# trim_recent — static method, no async / no litellm
# ---------------------------------------------------------------------------

def test_trim_recent_within_budget():
    msgs = [
        ChatMessage(role="user", content="a" * 100),
        ChatMessage(role="assistant", content="b" * 100),
    ]
    result = Compactor.trim_recent(msgs, 1000)
    assert len(result) == 2


def test_trim_recent_exceeds_budget():
    msgs = [
        ChatMessage(role="user", content="a" * 500),
        ChatMessage(role="assistant", content="b" * 500),
        ChatMessage(role="user", content="c" * 500),
        ChatMessage(role="assistant", content="d" * 500),
    ]
    result = Compactor.trim_recent(msgs, 800)
    assert len(result) < 4  # at least one message trimmed


# ---------------------------------------------------------------------------
# Custom compact prompt override (.spark/prompts/compact.md)
# ---------------------------------------------------------------------------

def _write_custom_prompt(workdir, text: str) -> None:
    d = workdir / ".spark" / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "compact.md").write_text(text, encoding="utf-8")


def test_load_custom_compact_prompt_none_when_absent(tmp_path, monkeypatch) -> None:
    from spark.core.compact import load_custom_compact_prompt

    monkeypatch.setenv("SPARK_HOME", str(tmp_path / "home"))
    assert load_custom_compact_prompt(tmp_path) is None
    assert load_custom_compact_prompt(None) is None


def test_load_custom_compact_prompt_project_beats_home(tmp_path, monkeypatch) -> None:
    from spark.core.compact import load_custom_compact_prompt

    home = tmp_path / "home"
    home.mkdir()
    (home / "prompts").mkdir()
    (home / "prompts" / "compact.md").write_text("GLOBAL PROMPT", encoding="utf-8")
    monkeypatch.setenv("SPARK_HOME", str(home))
    # 只有全局：返回全局
    assert load_custom_compact_prompt(tmp_path) == "GLOBAL PROMPT"
    # 项目级出现后覆盖全局
    _write_custom_prompt(tmp_path, "PROJECT PROMPT")
    assert load_custom_compact_prompt(tmp_path) == "PROJECT PROMPT"


def test_load_custom_compact_prompt_ignores_empty_file(tmp_path, monkeypatch) -> None:
    from spark.core.compact import load_custom_compact_prompt

    home = tmp_path / "home"
    (home / "prompts").mkdir(parents=True)
    (home / "prompts" / "compact.md").write_text("   \n", encoding="utf-8")
    monkeypatch.setenv("SPARK_HOME", str(home))
    # 空文件 = 未提供，回退全局/内置
    assert load_custom_compact_prompt(tmp_path) is None
    assert load_custom_compact_prompt(None) is None


def test_compactor_uses_custom_prompt_in_summary(tmp_path) -> None:
    from spark.core.compact import Compactor

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock"
    provider = MockProvider(rounds=[[ChatDelta(type="text", text="stub")]])
    compactor = Compactor(provider=provider, cfg=cfg, workdir=tmp_path)
    _write_custom_prompt(tmp_path, "MY CUSTOM COMPACT RULES")
    assert compactor._summary_prompt().startswith("MY CUSTOM COMPACT RULES")
    # 无自定义文件时回退内置
    compactor_builtin = Compactor(provider=provider, cfg=cfg, workdir=tmp_path / "elsewhere")
    assert compactor_builtin._summary_prompt() == STRUCTURED_SUMMARY_PROMPT


@pytest.mark.asyncio
async def test_merge_summaries_includes_custom_prompt(tmp_path) -> None:
    from spark.core.compact import Compactor

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock"
    provider = MockProvider(rounds=[[ChatDelta(type="text", text="stub")]])
    compactor = Compactor(provider=provider, cfg=cfg, workdir=tmp_path)
    _write_custom_prompt(tmp_path, "MERGE RULES FROM USER")

    captured: dict = {}

    async def fake_provider_completion(prompt_text: str) -> str:
        captured["prompt"] = prompt_text
        return "merged summary"

    with patch.object(compactor, "_provider_completion", side_effect=fake_provider_completion):
        result = await compactor.merge_summaries("prior", "new")
    assert result == "merged summary"
    assert "MERGE RULES FROM USER" in captured["prompt"]
