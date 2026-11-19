"""Token counting: LiteLLM first, tiktoken second, heuristic last.

LiteLLM owns the model tokenizer registry (including vendor aliases such as
``deepseek-chat`` / ``claude-sonnet-4-5``), so a known model is counted there.
tiktoken remains the local fallback for offline/minimal installs, and the old
CJK heuristic is only used when neither tokenizer is importable.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from functools import lru_cache

from spark.models import ChatMessage


def _heuristic_estimate(text: str) -> int:
    """Original fallback: CJK chars ~1 token each, others ~4 chars per token."""
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f":
            cjk += 1
        else:
            other += 1
    return cjk + (other + 3) // 4


#: tiktoken downloads its BPE table on first use. ``requests`` has no timeout
#: there, so an offline machine can hang for minutes on a token estimate. We
#: only construct an encoding whose blob is already cached; otherwise the
#: CJK/ASCII heuristic is used. LiteLLM still owns exact counts for known
#: models, so this only affects the offline fallback path.
_TIKTOKEN_URLS = {
    "o200k_base": "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken",
    "cl100k_base": "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken",
    "p50k_base": "https://openaipublic.blob.core.windows.net/encodings/p50k_base.tiktoken",
    "p50k_edit": "https://openaipublic.blob.core.windows.net/encodings/p50k_edit.tiktoken",
    "r50k_base": "https://openaipublic.blob.core.windows.net/encodings/r50k_base.tiktoken",
    "gpt2": "https://openaipublic.blob.core.windows.net/encodings/r50k_base.tiktoken",
}


def _tiktoken_cache_dir() -> str:
    return (
        os.environ.get("TIKTOKEN_CACHE_DIR")
        or os.environ.get("DATA_GYM_CACHE_DIR")
        or os.path.join(tempfile.gettempdir(), "data-gym-cache")
    )


def _tiktoken_blob_cached(name: str) -> bool:
    url = _TIKTOKEN_URLS.get(name)
    if not url:
        return False
    cache_dir = _tiktoken_cache_dir()
    if not cache_dir:
        return False
    cache_key = hashlib.sha1(url.encode(), usedforsecurity=False).hexdigest()
    return os.path.isfile(os.path.join(cache_dir, cache_key))


@lru_cache(maxsize=16)
def _encoding_for(model: str | None):
    """Return a cached tiktoken encoding, or None when unavailable/offline."""
    try:
        import tiktoken
    except Exception:  # pragma: no cover - optional dependency
        return None
    name = "o200k_base"
    if model:
        try:
            name = tiktoken.encoding_name_for_model(model)
        except Exception:
            # Unknown vendor/model names are normal here (deepseek-chat,
            # qwen3-coder, ...); fall through to the general encoding.
            name = "o200k_base"
    if not _tiktoken_blob_cached(name):
        if name == "o200k_base" and _tiktoken_blob_cached("cl100k_base"):
            name = "cl100k_base"
        else:
            return None
    try:
        return tiktoken.get_encoding(name)
    except Exception:  # pragma: no cover - corrupt install
        return None


def estimate_tokens(text: str, model: str | None = None) -> int:
    """Count tokens in *text* using the model tokenizer when possible."""
    if not text:
        return 0
    if model:
        try:
            import litellm

            return int(litellm.token_counter(model=model, text=text))
        except Exception:
            # Unknown vendor/model or a LiteLLM install without its tokenizer
            # extra: fall through to the local encoding.
            pass
    encoding = _encoding_for(model)
    if encoding is None:
        return _heuristic_estimate(text)
    return len(encoding.encode(text, disallowed_special=()))


def _message_chars(msg: ChatMessage) -> str:
    text = msg.content or ""
    if msg.tool_calls:
        for call in msg.tool_calls:
            text += call.name + str(call.arguments) + "function"
    return text


def estimate_message_tokens(msg: ChatMessage, model: str | None = None) -> int:
    # +4 is the conventional per-message framing overhead used by OpenAI-style
    # chat formats; keep it even when the tokenizer is exact.
    return estimate_tokens(_message_chars(msg), model) + 4


def estimate_history_tokens(
    messages: list[ChatMessage], model: str | None = None
) -> int:
    return sum(estimate_message_tokens(msg, model) for msg in messages)


def estimate_tool_overhead(schemas: list[dict], model: str | None = None) -> int:
    if not schemas:
        return 0
    return estimate_tokens(json.dumps(schemas, ensure_ascii=False), model)
