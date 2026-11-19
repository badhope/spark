from __future__ import annotations

import logging
from stat import S_ISREG
from typing import Any

from spark.core.context import (
    load_agents_md,
    system_prompt,
)
from spark.core.tokens import estimate_message_tokens, estimate_tool_overhead
from spark.models import ChatMessage
from spark.sandbox import safe_bare_filename

logger = logging.getLogger(__name__)


class ContextWindowMixin:
    @staticmethod
    def _calculate_history_tokens(history: list[ChatMessage]) -> int:
        return sum(
            estimate_message_tokens(message)
            for message in history
            if message.role != "system"
        )

    def _set_history_cache(self: Any) -> None:
        self._history_source = self.history
        self._history_tokens = self._calculate_history_tokens(self.history)
        self._history_length = len(self.history)
        self._history_tail = self.history[-1] if self.history else None

    def _sync_history_tokens(self: Any) -> None:
        tail = self.history[-1] if self.history else None
        if (
            self.history is self._history_source
            and len(self.history) == self._history_length
            and tail is self._history_tail
        ):
            return
        self._set_history_cache()

    def _replace_history(self: Any, history: list[ChatMessage]) -> None:
        self.history = history
        self._set_history_cache()

    def _append_history(self: Any, message: ChatMessage) -> None:
        self._sync_history_tokens()
        self.history.append(message)
        if message.role != "system":
            self._history_tokens += estimate_message_tokens(message)
        self._history_length = len(self.history)
        self._history_tail = message

    def _system_prompt_file_key(self: Any) -> tuple | None:
        try:
            stat_result = self._system_prompt_resource.stat()  # type: ignore[attr-defined]
        except (OSError, NotImplementedError):
            return None
        return (
            "ok",
            stat_result.st_mtime_ns,
            stat_result.st_size,
            getattr(stat_result, "st_ino", 0),
        )

    def _agents_file_key(self: Any) -> tuple:
        name = self.cfg.context.agents_md
        base = self.workdir.resolve()
        prefix = (name, self.cfg.context.max_fragment_chars, str(base))
        if not name or not safe_bare_filename(name):
            return prefix + ("invalid",)
        try:
            resolved = (base / name).resolve()
            stat_result = resolved.stat()
        except OSError as exc:
            return prefix + ("error", type(exc).__name__)
        if not resolved.is_relative_to(base) or not S_ISREG(stat_result.st_mode):
            return prefix + ("not_file", str(resolved))
        return prefix + (
            "ok",
            str(resolved),
            stat_result.st_mtime_ns,
            stat_result.st_size,
            getattr(stat_result, "st_ino", 0),
        )

    def _prompt_tokens(self: Any) -> int:
        """Token estimate for the prompt side (system + agents.md + memory slot).

        Uses :func:`spark.core.context.system_prompt` — the exact prompt
        ``build_messages`` sends — so the usage meter can never drift from
        what is actually charged (environment block, mode hint and skills
        injection included).
        """
        system_key = self._system_prompt_file_key()
        if system_key is None or system_key != self._system_prompt_cache_key:
            self._system_prompt_tokens = estimate_message_tokens(
                ChatMessage(
                    role="system", content=system_prompt(self.workdir, self.cfg)
                )
            )
            self._system_prompt_cache_key = system_key
        agents_key = self._agents_file_key()
        if agents_key != self._agents_cache_key:
            agents = load_agents_md(self.workdir, self.cfg)
            self._agents_tokens = (
                estimate_message_tokens(ChatMessage(role="user", content=agents))
                if agents
                else 0
            )
            self._agents_cache_key = agents_key
        return self._system_prompt_tokens + self._agents_tokens + 512

    def _usage(self: Any) -> dict:
        self._sync_history_tokens()
        limit = self.cfg.context.max_context_tokens
        total = self._prompt_tokens() + self._history_tokens + self._tool_overhead()
        return {
            "used": total,
            "limit": limit,
            "percent": round(total * 100 / max(1, limit), 1),
        }

    def _tool_overhead(self: Any) -> int:
        revision = self.registry.schema_revision
        if revision != self._tool_schema_revision:
            self._tool_overhead_tokens = estimate_tool_overhead(self.registry.schemas())
            self._tool_schema_revision = revision
        return self._tool_overhead_tokens

    def _get_compactor(self: Any):
        """Construct the compactor on first use (see __init__)."""
        if self._compactor is None:
            from spark.core.compact import Compactor

            self._compactor = Compactor(provider=self.provider, cfg=self.cfg, workdir=self.workdir)
        return self._compactor

    def _compact_boundary(self: Any) -> int:
        """Index of the first message to keep, or -1 when no safe cut exists.

        Cutting mid tool-exchange leaves an orphan ``role="tool"`` message whose
        ``assistant(tool_calls)`` partner was summarized away, which every
        OpenAI-compatible endpoint rejects with a 400 — so the cut is pulled
        back to a turn boundary that cannot split a call/result pair.
        """
        keep = max(2, self.cfg.context.keep_recent_messages)
        boundary = len(self.history) - keep
        if boundary <= 0:
            return -1
        for i in range(boundary, len(self.history)):
            if self.history[i].role == "user":
                boundary = i
                break
        while boundary > 0 and (
            self.history[boundary].role == "tool"
            or (boundary - 1 >= 0 and self.history[boundary - 1].tool_calls)
        ):
            boundary -= 1
        return boundary if 0 < boundary < len(self.history) else -1

    async def _maybe_compact(self: Any):
        """Auto-compact: when usage crosses threshold, summarize old turns into one summary message."""
        usage = self._usage()
        limit = self.cfg.context.max_context_tokens
        threshold = self.cfg.context.compact_threshold
        keep = max(2, self.cfg.context.keep_recent_messages)
        if usage["used"] <= limit * threshold or len(self.history) <= keep:
            return None
        boundary = self._compact_boundary()
        if boundary < 0:
            return None
        old_part = self.history[:boundary]
        if all(m.role == "summary" for m in old_part):
            return None
        compactor = self._get_compactor()
        summary_text = await compactor.summarize(old_part)
        if not summary_text:
            return None
        summary_msg = ChatMessage(
            role="summary",
            content=f"<conversation_summary>\n{summary_text}\n</conversation_summary>",
        )
        # The cursor must point at the *first retained message*: pointing it at
        # the summary row (which always has the newest id) filters the retained
        # tail out of load_messages() and wipes the recent conversation.
        row_ids = self.store.load_message_ids(self.session_id)
        aligned = len(row_ids) == len(self.history)
        if not aligned:
            logger.warning(
                "session %s: history (%d) and stored rows (%d) are not index-aligned; "
                "compacting in memory only",
                self.session_id,
                len(self.history),
                len(row_ids),
            )
        self.store.append_message(self.session_id, summary_msg)
        if aligned:
            self.store.set_compact_from(self.session_id, row_ids[boundary])
        before_tokens = usage["used"]
        if aligned:
            self._replace_history(self.store.load_messages(self.session_id))
        else:
            self._replace_history([summary_msg, *self.history[boundary:]])
        after_usage = self._usage()
        self._notify_safe(
            "compaction",
            {
                "message_count": len(old_part),
                "before_tokens": before_tokens,
                "after_tokens": after_usage["used"],
                "limit": limit,
            },
        )
        return {
            "before_tokens": before_tokens,
            "after_tokens": after_usage["used"],
            "limit": limit,
            "summarized_messages": len(old_part),
            "usage": after_usage,
        }
