"""Token counting backed by tiktoken, with a heuristic fallback.

The old implementation estimated ``CJK chars + non-CJK chars / 4``. That was
cheap but systematically wrong for compact prompts and for models with a
different tokenizer. We now use tiktoken's ``o200k_base`` by default (a good
general-purpose encoding) and ``encoding_for_model`` when the caller knows the
model. If tiktoken is unavailable or the model is unknown, the old heuristic is
kept as a last resort so the agent still works in a minimal install.
"""
from __future__ import annotations

import json
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


@lru_cache(maxsize=16)
def _encoding_for(model: str | None):
    """Return a cached tiktoken encoding, or None when tiktoken is unavailable."""
    try:
        import tiktoken
    except Exception:  # pragma: no cover - optional dependency
        return None
    if model:
        try:
            return tiktoken.encoding_for_model(model)
        except Exception:
            # Unknown vendor/model names are normal here (deepseek-chat,
            # qwen3-coder, ...); fall through to the general encoding.
            pass
    try:
        return tiktoken.get_encoding("o200k_base")
    except Exception:  # pragma: no cover - corrupt install
        return None


def estimate_tokens(text: str, model: str | None = None) -> int:
    """Count tokens in *text* using the model tokenizer when possible."""
    if not text:
        return 0
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
