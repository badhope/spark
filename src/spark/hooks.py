from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from spark.proc import run_bounded

SHELL_METACHARACTERS = set("&|<>^%!\n\r\x00")


@dataclass
class HookDecision:
    allow: bool
    reason: str = ""


class Hook(Protocol):
    def run(self, payload: dict[str, Any]) -> HookDecision: ...


# Events that are notification-only — they cannot block/interrupt the operation.
NOTIFY_ONLY_EVENTS: frozenset[str] = frozenset({"approval_needed", "tool_error"})


class ShellHook:
    """Runs a configured command directly. Never routed through a shell interpreter."""

    def __init__(
        self,
        name: str,
        command: list[str],
        cwd: Path,
        timeout: int = 15,
        on_deny: str = "block",
    ) -> None:
        self.name = name
        self.command = command
        self.cwd = cwd
        self.timeout = timeout
        self.on_deny = on_deny

    def run(self, payload: dict[str, Any]) -> HookDecision:
        try:
            completed = run_bounded(
                list(self.command),
                cwd=self.cwd,
                input_text=json.dumps(payload, ensure_ascii=False),
                timeout=self.timeout,
                shell=False,
            )
        except OSError as exc:
            return HookDecision(allow=False, reason=f"hook {self.name} failed: {exc}")
        if completed.timed_out:
            return HookDecision(allow=False, reason=f"hook {self.name} timed out")
        if completed.returncode == 0:
            return HookDecision(allow=True)
        reason = (completed.stderr or completed.stdout or "").strip()
        msg = f"hook {self.name} {self.on_deny}: {reason}"
        if self.on_deny == "block":
            return HookDecision(allow=False, reason=msg)
        # on_deny == "warn": do not intercept, allow operation to continue
        return HookDecision(allow=True, reason=msg)


class HookRegistry:
    """Registry of lifecycle hooks keyed by event name. Empty registry is a no-op."""

    EVENTS = (
        # Turn lifecycle
        "turn_start",
        "pre_tool",
        "post_tool",
        "turn_end",
        # Application / session lifecycle
        "app_start",
        "app_stop",
        "session_create",
        "session_load",
        "session_switch",
        # Context and memory
        "compaction",
        "memory_extract",
        "context_threshold",
        # Tooling
        "approval_needed",
        "tool_error",
        "plan_update",
        # Failures
        "turn_error",
        "provider_error",
    )

    def __init__(self) -> None:
        self._hooks: dict[str, list[Hook]] = {}

    @property
    def enabled(self) -> bool:
        return any(self._hooks.values())

    def register(self, event: str, hook: Hook) -> None:
        if event not in self.EVENTS:
            raise ValueError(f"unknown hook event: {event}")
        self._hooks.setdefault(event, []).append(hook)

    def notify(
        self, event: str, payload: dict[str, Any], notify_only: bool = False
    ) -> HookDecision:
        if event not in self.EVENTS:
            raise ValueError(f"unknown hook event: {event}")
        if notify_only or event in NOTIFY_ONLY_EVENTS:
            for hook in self._hooks.get(event, []):
                hook.run(payload)
            return HookDecision(allow=True)
        for hook in self._hooks.get(event, []):
            decision = hook.run(payload)
            if not decision.allow:
                return decision
        return HookDecision(allow=True)

    @classmethod
    def from_config(
        cls, raw: list[dict[str, Any]] | None, cwd: Path
    ) -> HookRegistry:
        registry = cls()
        for item in raw or []:
            event = str(item.get("event") or "")
            command = item.get("command")
            if event not in cls.EVENTS or not isinstance(command, str):
                continue
            args = [str(part) for part in (item.get("args") or [])]
            if not command.strip() or _has_shell_metacharacters(command):
                continue
            if any(_has_shell_metacharacters(part) for part in args):
                continue
            on_deny = str(item.get("on_deny") or "block")
            if on_deny not in ("warn", "block"):
                on_deny = "block"
            registry.register(
                event,
                ShellHook(
                    str(item.get("name") or f"{event}:{command}"),
                    [command, *args],
                    cwd,
                    int(item.get("timeout_sec") or 15),
                    on_deny,
                ),
            )
        return registry


def _has_shell_metacharacters(value: str) -> bool:
    return any(char in SHELL_METACHARACTERS for char in value)
