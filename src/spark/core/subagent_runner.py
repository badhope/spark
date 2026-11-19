from __future__ import annotations

import asyncio as _asyncio
import logging
from typing import Any

from spark.models import TurnEvent
from spark.store import SessionStore
from spark.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class SubagentRunnerMixin:
    async def spawn_subtask(
        self: Any,
        prompt: str,
        *,
        system_extra: str = "",
        tools_allowlist: list[str] | None = None,
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
        from pathlib import Path as _Path

        sub_ctx = self._make_sub_context()
        sub_registry = ToolRegistry(sub_ctx)
        self._inherit_registry(sub_registry)
        if tools_allowlist:
            sub_registry.tool_allowlist = set(tools_allowlist)
        sub_cfg = sub_ctx.config
        provider = self.provider
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
                sub_cfg.provider.model = self.cfg.provider.model
                provider = self.provider
        from spark.core.loop import AgentLoop

        sub_store = SessionStore(_Path(":memory:"))
        sid = sub_store.create_session(
            self.workdir, sub_cfg.provider.model, title="subtask"
        )
        sub_loop = AgentLoop(
            workdir=self.workdir,
            cfg=sub_cfg,
            provider=provider,
            registry=sub_registry,
            store=sub_store,
            session_id=sid,
            approver=self.approver,
            memory=None,
        )
        sub_loop.allow_always = set(self.allow_always)
        sub_loop._extra_system_prompt = system_extra
        self._active_children.append(sub_loop)
        final_text = ""
        try:
            async for event in sub_loop.iter_turn(prompt):
                if event.type in {"tool_start", "tool_end", "plan", "approval_needed"}:
                    yield event
                elif event.type == "turn_end":
                    final_text = event.text or ""
                elif event.type == "turn_error":
                    final_text = f"subtask failed: {event.text}"
                    yield TurnEvent(type="turn_end", text=final_text)
                    return
            yield TurnEvent(type="turn_end", text=final_text)
        finally:
            self._active_children.remove(sub_loop)
            sub_store.close()

    async def spawn_subtasks_parallel(
        self: Any,
        prompts: list[str],
        *,
        system_extras: list[str] | None = None,
        tools_allowlists: list[list[str] | None] | None = None,
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
        import asyncio as _asyncio

        if system_extras is None:
            system_extras = [""] * len(prompts)
        if tools_allowlists is None:
            tools_allowlists = [None] * len(prompts)
        if models is None:
            models = [""] * len(prompts)
        if len(prompts) == 1:
            async for event in self.spawn_subtask(
                prompts[0],
                system_extra=system_extras[0],
                tools_allowlist=tools_allowlists[0],
                model=models[0] if models else "",
            ):
                yield event
            return

        queue: _asyncio.Queue[tuple[int, TurnEvent | None]] = _asyncio.Queue()
        summaries: dict[int, str] = {}

        async def runner(index: int, prompt: str) -> tuple[int, str]:
            try:
                summary = await self._collect_subtask_events(
                    index,
                    prompt,
                    queue,
                    system_extra=system_extras[index],
                    tools_allowlist=tools_allowlists[index],
                    model=models[index] if index < len(models) else "",
                )
                return index, summary
            except Exception as exc:
                await queue.put((index, None))
                return index, f"subtask failed: {exc}"

        tasks = [_asyncio.create_task(runner(i, p)) for i, p in enumerate(prompts)]
        for i, prompt in enumerate(prompts):
            # 带上声明式子代理名：UI 靠它区分"并行跑的到底是哪几个专家"，
            # 否则三行都只写着 subtask 1/2/3，看起来一模一样。
            extra: dict = {}
            declared_name = agent_names[i] if agent_names and i < len(agent_names) else ""
            if declared_name:
                extra["agent"] = declared_name
            yield TurnEvent(
                type="subtask_start",
                data={"index": i, "prompt": prompt[:200], "total": len(prompts), **extra},
            )
        try:
            # Drive the whole fan-out from the queue so a sub-agent that is waiting
            # on approval or a long tool still delivers its events immediately;
            # waiting on task completion alone can deadlock on the approval path.
            remaining = len(tasks)
            while remaining > 0:
                index, event = await queue.get()
                if event is not None:
                    # Tag tool events with the child that produced them so the
                    # execution graph can nest them under their sub-agent
                    # instead of drawing every tool as a sibling of `task`.
                    if event.type in {"tool_start", "tool_end"} and event.data is None:
                        event = event.model_copy(update={"data": {"subtask_index": index}})
                    yield event
                    continue
                remaining -= 1
                task = tasks[index] if index < len(tasks) else None
                if task is not None and task.done():
                    try:
                        done_index, summary = task.result()
                        summaries[done_index] = summary
                    except Exception as exc:
                        summaries[index] = f"subtask failed: {exc}"
                elif task is not None:
                    try:
                        done_index, summary = await task
                        summaries[done_index] = summary
                    except Exception as exc:
                        summaries[index] = f"subtask failed: {exc}"
            while not queue.empty():
                index, event = queue.get_nowait()
                if event is not None:
                    if event.type in {"tool_start", "tool_end"} and event.data is None:
                        event = event.model_copy(update={"data": {"subtask_index": index}})
                    yield event
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await _asyncio.gather(*tasks, return_exceptions=True)
        for i in range(len(prompts)):
            label = f"[子任务 {i + 1}]"
            yield TurnEvent(
                type="subtask_end",
                text=f"{label} {summaries.get(i, 'no output')}",
                data={"index": i, "summary": summaries.get(i, "")},
            )
            yield TurnEvent(
                type="turn_end", text=f"{label} {summaries.get(i, 'no output')}"
            )

    async def _collect_subtask_events(
        self: Any,
        index: int,
        prompt: str,
        queue,
        *,
        system_extra: str = "",
        tools_allowlist: list[str] | None = None,
        model: str = "",
    ) -> str:
        """Run one subtask; stream its tool events into the queue tagged by index; return summary."""
        from pathlib import Path as _Path

        sub_ctx = self._make_sub_context()
        sub_registry = ToolRegistry(sub_ctx)
        self._inherit_registry(sub_registry)
        if tools_allowlist:
            sub_registry.tool_allowlist = set(tools_allowlist)
        sub_cfg = sub_ctx.config
        provider = self.provider
        if model and model != sub_cfg.provider.model:
            sub_cfg.provider.model = model
            try:
                from spark.providers.factory import create_provider

                provider = create_provider(sub_cfg)
            except Exception as exc:  # aqg: top-level boundary
                logger.warning(
                    "sub-agent model %r unavailable (%s); using the parent model",
                    model,
                    exc,
                )
                sub_cfg.provider.model = self.cfg.provider.model
                provider = self.provider
        from spark.core.loop import AgentLoop

        sub_store = SessionStore(_Path(":memory:"))
        sid = sub_store.create_session(
            self.workdir, sub_cfg.provider.model, title=f"subtask-{index}"
        )
        sub_loop = AgentLoop(
            workdir=self.workdir,
            cfg=sub_cfg,
            provider=provider,
            registry=sub_registry,
            store=sub_store,
            session_id=sid,
            approver=self.approver,
            memory=None,
        )
        sub_loop.allow_always = set(self.allow_always)
        sub_loop._extra_system_prompt = system_extra
        self._active_children.append(sub_loop)
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
