from __future__ import annotations

import asyncio as _asyncio
import fnmatch
import logging
from typing import Any

from spark.models import TurnEvent
from spark.store import SessionStore
from spark.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def _sub_usage(loop: Any) -> dict[str, Any] | None:
    """Usage totals from a finished child turn, for workflow cost accounting."""
    usage = getattr(loop, "_turn_usage_totals", None)
    if not isinstance(usage, dict):
        return None
    return dict(usage)


def _create_sub_loop(
    owner: Any,
    *,
    title: str,
    system_extra: str,
    tools_allowlist: list[str] | None,
    denied_tools: list[str] | None,
    model: str,
) -> tuple[Any, SessionStore]:
    """Build a fresh child loop and register it for parent cancellation."""
    from pathlib import Path as _Path

    from spark.core.loop import AgentLoop

    sub_ctx = owner._make_sub_context()
    sub_registry = ToolRegistry(sub_ctx)
    owner._inherit_registry(sub_registry)
    if tools_allowlist is not None or denied_tools:
        all_names = {
            str(schema.get("function", {}).get("name") or "")
            for schema in sub_registry.schemas()
        }
        allowed = set(all_names)
        if tools_allowlist is not None:
            allowed &= set(tools_allowlist)
        for pattern in denied_tools or []:
            allowed = {
                name for name in allowed
                if not fnmatch.fnmatch(name, pattern)
            }
        sub_registry.tool_allowlist = allowed
    sub_cfg = sub_ctx.config
    provider = owner.provider
    if model and model != sub_cfg.provider.model:
        sub_cfg.provider.model = model
        try:
            from spark.providers.factory import create_provider

            provider = create_provider(sub_cfg)
        except Exception as exc:  # aqg: top-level boundary
            # A bad model id must not kill the whole turn: fall back to the
            # parent provider and say so in the log.
            logger.warning(
                "sub-agent model %r unavailable (%s); using the parent model",
                model,
                exc,
            )
            sub_cfg.provider.model = owner.cfg.provider.model
            provider = owner.provider
    sub_store = SessionStore(_Path(":memory:"))
    sid = sub_store.create_session(owner.workdir, sub_cfg.provider.model, title=title)
    sub_loop = AgentLoop(
        workdir=owner.workdir,
        cfg=sub_cfg,
        provider=provider,
        registry=sub_registry,
        store=sub_store,
        session_id=sid,
        approver=owner.approver,
        memory=None,
    )
    sub_loop.allow_always = set(owner.allow_always)
    sub_loop._extra_system_prompt = system_extra
    owner._active_children.append(sub_loop)
    return sub_loop, sub_store


class _ParallelFanout:
    """Drive a queue-backed fan-out so events surface while children still run."""

    def __init__(
        self,
        owner: Any,
        prompts: list[str],
        *,
        system_extras: list[str],
        tools_allowlists: list[list[str] | None],
        denied_tool_lists: list[list[str] | None],
        models: list[str],
        agent_names: list[str],
    ) -> None:
        self.owner = owner
        self.prompts = prompts
        self.system_extras = system_extras
        self.tools_allowlists = tools_allowlists
        self.denied_tool_lists = denied_tool_lists
        self.models = models
        self.agent_names = agent_names
        self.queue: _asyncio.Queue[tuple[int, TurnEvent | None]] = _asyncio.Queue()
        self.summaries: dict[int, str] = {}
        self.tasks: list[_asyncio.Task] = []

    def _collect_args(self, index: int) -> dict[str, Any]:
        return {
            "system_extra": self.system_extras[index],
            "tools_allowlist": self.tools_allowlists[index],
            "denied_tools": (
                self.denied_tool_lists[index]
                if index < len(self.denied_tool_lists)
                else None
            ),
            "model": self.models[index] if index < len(self.models) else "",
        }

    async def _runner(self, index: int, prompt: str) -> tuple[int, str]:
        try:
            summary = await self.owner._collect_subtask_events(
                index, prompt, self.queue, **self._collect_args(index)
            )
            return index, summary
        except Exception as exc:
            await self.queue.put((index, None))
            return index, f"subtask failed: {exc}"

    def _tag_event(self, index: int, event: TurnEvent) -> TurnEvent:
        if event.type in {"tool_start", "tool_end"} and event.data is None:
            # Tag tool events with the child that produced them so the
            # execution graph can nest them under their sub-agent.
            return event.model_copy(update={"data": {"subtask_index": index}})
        return event

    async def _drain(self):
        remaining = len(self.tasks)
        while remaining > 0:
            index, event = await self.queue.get()
            if event is not None:
                yield self._tag_event(index, event)
                continue
            remaining -= 1
            task = self.tasks[index] if index < len(self.tasks) else None
            if task is not None and task.done():
                try:
                    done_index, summary = task.result()
                    self.summaries[done_index] = summary
                except Exception as exc:
                    self.summaries[index] = f"subtask failed: {exc}"
            elif task is not None:
                try:
                    done_index, summary = await task
                    self.summaries[done_index] = summary
                except Exception as exc:
                    self.summaries[index] = f"subtask failed: {exc}"

    async def _flush(self):
        while not self.queue.empty():
            index, event = self.queue.get_nowait()
            if event is not None:
                yield self._tag_event(index, event)

    def _end_events(self) -> list[TurnEvent]:
        events: list[TurnEvent] = []
        for i in range(len(self.prompts)):
            label = f"[子任务 {i + 1}]"
            text = f"{label} {self.summaries.get(i, 'no output')}"
            events.append(
                TurnEvent(
                    type="subtask_end",
                    text=text,
                    data={"index": i, "summary": self.summaries.get(i, "")},
                )
            )
            events.append(TurnEvent(type="turn_end", text=text))
        return events

    async def _start_events(self):
        for i, prompt in enumerate(self.prompts):
            extra: dict = {}
            declared_name = self.agent_names[i] if i < len(self.agent_names) else ""
            if declared_name:
                extra["agent"] = declared_name
            yield TurnEvent(
                type="subtask_start",
                data={
                    "index": i,
                    "prompt": prompt[:200],
                    "total": len(self.prompts),
                    **extra,
                },
            )

    async def run(self):
        self.tasks = [
            _asyncio.create_task(self._runner(i, prompt))
            for i, prompt in enumerate(self.prompts)
        ]
        async for event in self._start_events():
            yield event
        try:
            async for event in self._drain():
                yield event
            async for event in self._flush():
                yield event
        finally:
            for task in self.tasks:
                if not task.done():
                    task.cancel()
            await _asyncio.gather(*self.tasks, return_exceptions=True)
        for event in self._end_events():
            yield event


class SubagentRunnerMixin:
    async def spawn_subtask(
        self: Any,
        prompt: str,
        *,
        system_extra: str = "",
        tools_allowlist: list[str] | None = None,
        denied_tools: list[str] | None = None,
        model: str = "",
    ):
        """Run a self-contained sub-agent with a fresh context; yield its events, final turn_end carries the summary.

        P1-4: ``system_extra`` becomes the sub-agent's own system-prompt slot
        (declared-agent body); ``tools_allowlist`` restricts its tool schemas.

        ``model`` lets a declared specialist run on its own model. The provider
        is rebuilt only when the requested model differs from the main one, and
        the credentials come from the parent config (same endpoint, same key) —
        a sub-agent never introduces a new credential.
        """
        sub_loop, sub_store = _create_sub_loop(self,
            title="subtask",
            system_extra=system_extra,
            tools_allowlist=tools_allowlist,
            denied_tools=denied_tools,
            model=model,
        )
        final_text = ""
        try:
            async for event in sub_loop.iter_turn(prompt):
                if event.type in {"tool_start", "tool_end", "plan", "approval_needed"}:
                    yield event
                elif event.type == "turn_end":
                    final_text = event.text or ""
                elif event.type == "turn_error":
                    final_text = f"subtask failed: {event.text}"
                    yield TurnEvent(type="turn_end", text=final_text, data=_sub_usage(sub_loop))
                    return
            yield TurnEvent(type="turn_end", text=final_text, data=_sub_usage(sub_loop))
        finally:
            self._active_children.remove(sub_loop)
            sub_store.close()

    async def spawn_subtasks_parallel(
        self: Any,
        prompts: list[str],
        *,
        system_extras: list[str] | None = None,
        tools_allowlists: list[list[str] | None] | None = None,
        denied_tool_lists: list[list[str] | None] | None = None,
        models: list[str] | None = None,
        agent_names: list[str] | None = None,
    ):
        """Run several sub-agents concurrently; each gets its own context. Yields each sub-agent's
        tool/plan events live, then one combined turn_end carrying all summaries.

        P1-4: ``system_extras`` / ``tools_allowlists`` align 1:1 with prompts
        (declared-agent body + tool allowlist per parallel item); defaults are
        empty, preserving the pre-P1-4 behavior exactly. ``models`` likewise
        aligns 1:1 and carries each specialist's optional model override.
        """
        if system_extras is None:
            system_extras = [""] * len(prompts)
        if tools_allowlists is None:
            tools_allowlists = [None] * len(prompts)
        if models is None:
            models = [""] * len(prompts)
        if denied_tool_lists is None:
            denied_tool_lists = [[] for _ in prompts]
        if len(prompts) == 1:
            async for event in self.spawn_subtask(
                prompts[0],
                system_extra=system_extras[0],
                tools_allowlist=tools_allowlists[0],
                denied_tools=denied_tool_lists[0] if denied_tool_lists else None,
                model=models[0] if models else "",
            ):
                yield event
            return
        fanout = _ParallelFanout(
            self,
            prompts,
            system_extras=system_extras,
            tools_allowlists=tools_allowlists,
            denied_tool_lists=denied_tool_lists,
            models=models,
            agent_names=agent_names or [],
        )
        async for event in fanout.run():
            yield event

    async def _collect_subtask_events(
        self: Any,
        index: int,
        prompt: str,
        queue,
        *,
        system_extra: str = "",
        tools_allowlist: list[str] | None = None,
        denied_tools: list[str] | None = None,
        model: str = "",
    ) -> str:
        """Run one subtask; stream its tool events into the queue tagged by index; return summary."""
        sub_loop, sub_store = _create_sub_loop(self,
            title=f"subtask-{index}",
            system_extra=system_extra,
            tools_allowlist=tools_allowlist,
            denied_tools=denied_tools,
            model=model,
        )
        final_text = ""
        thought_chars = 0
        text_chars = 0
        tool_count = 0
        try:
            async for event in sub_loop.iter_turn(prompt):
                if event.type in {"tool_start", "tool_end", "plan", "approval_needed"}:
                    if event.type == "tool_start":
                        tool_count += 1
                    await queue.put((index, event))
                elif event.type == "reasoning_delta" and event.text:
                    # Forward a counter, not the raw stream: the parent turn is
                    # one bubble, so the trace summary is what the graph and the
                    # card can actually show without duplicating the transcript.
                    thought_chars += len(event.text)
                    await queue.put(
                        (
                            index,
                            TurnEvent(
                                type="subtask_progress",
                                data={
                                    "index": index,
                                    "phase": "thinking",
                                    "chars": thought_chars,
                                },
                            ),
                        )
                    )
                elif event.type == "text_delta" and event.text:
                    text_chars += len(event.text)
                    await queue.put(
                        (
                            index,
                            TurnEvent(
                                type="subtask_progress",
                                data={
                                    "index": index,
                                    "phase": "answering",
                                    "chars": text_chars,
                                },
                            ),
                        )
                    )
                elif event.type == "turn_end":
                    final_text = event.text or ""
                elif event.type == "turn_error":
                    final_text = f"subtask failed: {event.text}"
            await queue.put(
                (
                    index,
                    TurnEvent(
                        type="subtask_trace",
                        data={
                            "index": index,
                            "thinking_chars": thought_chars,
                            "answer_chars": text_chars,
                            "tools": tool_count,
                        },
                    ),
                )
            )
            await queue.put((index, None))
            return final_text
        finally:
            self._active_children.remove(sub_loop)
            sub_store.close()
