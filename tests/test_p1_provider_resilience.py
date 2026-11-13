"""P1 新能力回归：usage 真实回填 / fallback 链 / 凭据池 / small_model。

不触网：litellm.acompletion 全部 monkeypatch 为脚本化假流。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from spark.config import SparkConfig
from spark.models import ChatMessage
from spark.providers.openai_compat import OpenAICompatProvider


def _chunk(content: str | None = None, usage: dict | None = None) -> SimpleNamespace:
    delta = SimpleNamespace(content=content, tool_calls=None, reasoning_content=None)
    choice = SimpleNamespace(delta=delta)
    return SimpleNamespace(choices=[choice], usage=usage)


def _usage_chunk(prompt: int, completion: int, reasoning: int = 0) -> SimpleNamespace:
    details = SimpleNamespace(reasoning_tokens=reasoning) if reasoning else None
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            completion_tokens_details=details,
        ),
    )


class _FakeStream:
    def __init__(self, chunks: list[SimpleNamespace]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._chunks:
            return self._chunks.pop(0)
        raise StopAsyncIteration


def _patch_litellm(monkeypatch: pytest.MonkeyPatch, responses: list[Any]) -> dict:
    """替换 litellm.acompletion；responses 元素为 chunk 列表或抛出的异常。"""
    calls: dict[str, Any] = {"kwargs": []}

    async def fake_acompletion(**kwargs):
        calls["kwargs"].append(kwargs)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeStream(list(item))

    monkeypatch.setattr("litellm.acompletion", fake_acompletion)
    return calls


@pytest.mark.asyncio
async def test_stream_reports_usage_on_end_delta(monkeypatch):
    p = OpenAICompatProvider("https://x.example/v1", "m1", "k1", max_retries=0)
    calls = _patch_litellm(
        monkeypatch,
        [[_chunk("hi"), _usage_chunk(prompt=11, completion=7, reasoning=3)]],
    )
    deltas = [d async for d in p.stream([ChatMessage(role="user", content="q")], tools=[])]
    end = deltas[-1]
    assert end.type == "end"
    assert end.usage == {"prompt_tokens": 11, "completion_tokens": 7, "reasoning_tokens": 3}
    # usage 请求参数已下发
    assert calls["kwargs"][0]["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_fallback_chain_switches_model(monkeypatch):
    p = OpenAICompatProvider(
        "https://x.example/v1", "main-model", "k1",
        max_retries=0, fallback_models=["backup-model"],
    )
    calls = _patch_litellm(
        monkeypatch,
        [
            RuntimeError("boom: 500"),  # 主模型失败（不可重试路径→切 fallback）
            [_chunk("ok from backup")],
        ],
    )
    deltas = [d async for d in p.stream([ChatMessage(role="user", content="q")], tools=[])]
    texts = "".join(d.text or "" for d in deltas if d.type == "text")
    assert texts == "ok from backup"
    models = [kw["model"] for kw in calls["kwargs"]]
    assert models == ["main-model", "backup-model"]


@pytest.mark.asyncio
async def test_all_targets_fail_raises_sanitized(monkeypatch):
    p = OpenAICompatProvider(
        "https://x.example/v1", "main-model", "secret-key-1",
        max_retries=0, fallback_models=["backup-model"],
    )
    _patch_litellm(
        monkeypatch,
        [RuntimeError("auth failed for secret-key-1"), RuntimeError("nope")],
    )
    with pytest.raises(RuntimeError) as ei:
        async for _ in p.stream([ChatMessage(role="user", content="q")], tools=[]):
            pass
    assert "secret-key-1" not in str(ei.value)
    assert "main-model, backup-model" in str(ei.value)


@pytest.mark.asyncio
async def test_credential_pool_rotates(monkeypatch):
    p = OpenAICompatProvider(
        "https://x.example/v1", "m", "k1", max_retries=1, api_keys=["k2"],
    )
    calls = _patch_litellm(
        monkeypatch,
        [
            TimeoutError("transient"),  # 可重试异常 → 下一次尝试轮换密钥
            [_chunk("ok")],
        ],
    )
    _ = [d async for d in p.stream([ChatMessage(role="user", content="q")], tools=[])]
    keys = [kw["api_key"] for kw in calls["kwargs"]]
    assert keys == ["k1", "k2"]  # 失败后轮换到池内下一把


@pytest.mark.asyncio
async def test_drops_reasoning_effort_after_400(monkeypatch):
    class _BadRequest(Exception):
        status_code = 400

    p = OpenAICompatProvider("https://x.example/v1", "m", "k", max_retries=0)
    calls = _patch_litellm(
        monkeypatch,
        [
            _BadRequest("invalid request"),  # gateway rejects reasoning_effort
            [_chunk("ok")],
        ],
    )
    deltas = [
        d
        async for d in p.stream(
            [ChatMessage(role="user", content="q")], tools=[], effort="medium"
        )
    ]
    texts = "".join(d.text or "" for d in deltas if d.type == "text")
    assert texts == "ok"
    assert calls["kwargs"][0]["reasoning_effort"] == "medium"
    assert "reasoning_effort" not in calls["kwargs"][1]


def test_small_model_config_and_compactor_routing(tmp_path):
    from spark.core.compact import Compactor
    from spark.providers.mock import MockProvider

    cfg = SparkConfig()
    cfg.provider.small_model = "cheap-model"
    c = Compactor(provider=MockProvider(), cfg=cfg)
    assert c._summary_model == "cheap-model"
    # 留空 = 主模型
    cfg2 = SparkConfig()
    c2 = Compactor(provider=MockProvider(), cfg=cfg2)
    assert c2._summary_model == cfg2.provider.model


def test_provider_config_fields():
    cfg = SparkConfig()
    assert cfg.provider.api_keys == []
    assert cfg.provider.fallback_models == []
    assert cfg.provider.small_model == ""
    cfg.provider.fallback_models = ["m2", "m2"]
    cfg.provider.api_keys = ["a", "a"]
    assert cfg.provider.fallback_models == ["m2", "m2"]  # 原样保存，去重在 provider 内部
