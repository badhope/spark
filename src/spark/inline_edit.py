"""Inline edit / next-edit-suggestion engine (Phase 3).

This is deliberately *not* full Tab completion. It handles two editor-native
actions with an explicit user intent:

- ``edit``: the selected code plus a natural-language instruction -> a
  replacement for that selection;
- ``nes``: the text around the caret -> a small next edit suggestion.

The model is asked to return code only. Spark still treats the output as untrusted:
the extension shows it as an inline diff and only applies it after an explicit
accept, so a hallucinated block cannot silently overwrite a file.
"""
from __future__ import annotations

import re
import time
from typing import Any

MAX_SELECTION_CHARS = 12_000
MAX_CONTEXT_CHARS = 6_000
MAX_OUTPUT_CHARS = 20_000

_SYSTEM = """You are Spark's inline code editor. You return only the code that should replace the selection or be inserted at the caret.
Rules:
- Output code only. No Markdown fences, no prose, no explanations.
- Preserve the file's language, indentation and naming conventions.
- For an edit request, return the complete replacement for the selected text, not a fragment.
- For a next-edit request, return the smallest useful insertion or replacement; if no edit is warranted, output nothing.
- Never invent imports or APIs that are not present or clearly required.
"""

_FENCE = re.compile(r"^```[a-zA-Z0-9_+.-]*\s*\n(.*?)\n```$", re.S)


def _clean(text: str) -> str:
    text = (text or "").strip()
    m = _FENCE.match(text)
    if m:
        text = m.group(1).strip()
    return text[:MAX_OUTPUT_CHARS]


def _clip(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "\n... [truncated]"


def _messages(request: dict) -> list[Any]:
    from spark.models import ChatMessage

    mode = str(request.get("mode") or "edit")
    language = str(request.get("language") or "")
    path = str(request.get("path") or "")
    selected = _clip(str(request.get("selected") or ""), MAX_SELECTION_CHARS)
    prefix = _clip(str(request.get("prefix") or ""), MAX_CONTEXT_CHARS)
    suffix = _clip(str(request.get("suffix") or ""), MAX_CONTEXT_CHARS)
    instruction = str(request.get("instruction") or "").strip()
    if mode == "nes":
        prompt = (
            f"File: {path}\nLanguage: {language}\n\n"
            "Text before the caret:\n<prefix>\n"
            f"{prefix}\n</prefix>\n\nText after the caret:\n<suffix>\n{suffix}\n</suffix>\n\n"
            "Suggest the next edit. Output only the code to insert at the caret."
        )
    else:
        context = ""
        if prefix or suffix:
            context = (
                "\n\nSurrounding file context (do not repeat it in the output):"
                "\n<before_selection>\n"
                f"{prefix}\n</before_selection>\n"
                "<after_selection>\n"
                f"{suffix}\n</after_selection>\n"
            )
        prompt = (
            f"File: {path}\nLanguage: {language}\nInstruction: {instruction}\n"
            f"{context}\n"
            "Selected text:\n<selection>\n"
            f"{selected}\n</selection>\n\n"
            "Return the complete replacement for the selection."
        )
    return [
        ChatMessage(role="system", content=_SYSTEM),
        ChatMessage(role="user", content=prompt),
    ]


async def generate_inline_edit(provider: Any, request: dict) -> dict:
    """Run one bounded model call; raise only for provider/transport failures."""
    started = time.monotonic()
    mode = "nes" if str(request.get("mode") or "") == "nes" else "edit"
    messages = _messages({**request, "mode": mode})
    chunks: list[str] = []
    usage = None
    async for delta in provider.stream(messages, [], effort="low"):
        if getattr(delta, "text", None):
            chunks.append(str(delta.text))
        if getattr(delta, "usage", None):
            usage = delta.usage
        if sum(len(x) for x in chunks) > MAX_OUTPUT_CHARS * 2:
            break
    text = _clean("".join(chunks))
    latency_ms = round((time.monotonic() - started) * 1000, 1)
    return {
        "ok": bool(text) or mode == "nes",
        "mode": mode,
        "text": text,
        "latency_ms": latency_ms,
        "usage": usage or {},
    }
