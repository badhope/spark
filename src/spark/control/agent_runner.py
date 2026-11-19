"""Run a control-plane task through the same AgentLoop as every other surface.

The control plane historically owned a second, thinner model path (one-shot
``ModelGateway.complete`` for office summaries). That split meant enterprise
tasks could not use tools, approvals, the sandbox or checkpoints. This runner
is the bridge: it creates/uses a normal Agent session, drives one turn through
``AgentLoop.iter_turn_sync`` and returns the final text plus the session id.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from spark.config import SparkConfig
from spark.core.loop import AgentLoop, Approver
from spark.core.turn_store import persistent_checkpointer
from spark.providers.factory import create_provider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class ControlRunResult:
    session_id: str
    text: str = ""
    status: str = "completed"
    event_count: int = 0
    error: str = ""
    events: list[dict] = field(default_factory=list)


class ControlAgentRunner:
    """One control task -> one AgentLoop turn.

    The runner deliberately takes ``workdir`` per run: a tenant/task directory
    keeps files, checkpoints and tool side effects scoped to the task instead
    of letting every enterprise task share the server's process directory.
    """

    def __init__(
        self,
        cfg: SparkConfig,
        store: SessionStore,
        *,
        workdir: Path,
        approver: Approver | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.workdir = Path(workdir)
        self.approver = approver

    def run(
        self,
        prompt: str,
        *,
        title: str = "control task",
        session_id: str = "",
        workdir: Path | None = None,
    ) -> ControlRunResult:
        task_workdir = Path(workdir or self.workdir).expanduser().resolve()
        task_workdir.mkdir(parents=True, exist_ok=True)

        if session_id:
            session = self.store.get_session(session_id)
            if session is None:
                raise ValueError(f"agent session not found: {session_id}")
            stored_workdir = str(session.get("workdir") or "")
            if stored_workdir and Path(stored_workdir).expanduser().resolve() != task_workdir:
                raise ValueError("agent session workdir does not match this control task")
            sid = session_id
        else:
            sid = self.store.create_session(
                task_workdir,
                self.cfg.provider.model,
                title=(title or "control task")[:80],
            )

        text_parts: list[str] = []
        events: deque[dict] = deque(maxlen=200)
        event_count = 0
        error = ""
        final_text = ""
        try:
            tool_ctx = ToolContext(
                sandbox=WorkdirSandbox(task_workdir, self.cfg),
                config=self.cfg,
            )
            registry = ToolRegistry(tool_ctx)
            provider = create_provider(self.cfg)
            loop = AgentLoop(
                workdir=task_workdir,
                cfg=self.cfg,
                provider=provider,
                registry=registry,
                store=self.store,
                session_id=sid,
                approver=self.approver,
                checkpointer=persistent_checkpointer(),
            )
            for event in loop.iter_turn_sync(prompt):
                event_count += 1
                events.append({"type": event.type, "text": event.text or ""})
                if event.type == "text_delta" and event.text:
                    text_parts.append(event.text)
                elif event.type == "turn_end" and event.text:
                    final_text = event.text
                elif event.type == "turn_error":
                    error = event.text or "turn failed"
        except Exception as exc:  # noqa: BLE001 - return the session id on failure
            logger.exception("control_agent_run_failed session=%s", sid)
            error = f"{type(exc).__name__}: {str(exc)[:400]}"

        text = final_text or "".join(text_parts)
        status = "failed" if error else "completed"
        logger.info(
            "control_agent_run session=%s status=%s events=%d chars=%d",
            sid,
            status,
            len(events),
            len(text),
        )
        return ControlRunResult(
            session_id=sid,
            text=text,
            status=status,
            event_count=event_count,
            error=error,
            events=list(events),
        )
