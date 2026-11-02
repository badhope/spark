"""Structured security audit trail.

The audit report (F-014) found that refusals — a blocked shell command, a denied
approval, a sandbox escape attempt — only reached the debug log. That is
invisible unless someone turns logging up, and it is not machine-readable, so
neither the user nor an OTLP collector could answer "what was refused today?".

Design constraints:

* **Append-only JSONL**, one object per line, so it is greppable and trivially
  shipped to a collector.
* **Disabled by default.** A local agent should not write a second file unless
  asked; enable with ``SPARK_AUDIT_LOG=1`` (path defaults to
  ``$SPARK_HOME/audit.jsonl``) or ``SPARK_AUDIT_LOG=<path>``.
* **Never raises.** An audit write must not break a turn (same rule as hooks).
* **Never records secrets.** Values pass through the same redaction used for
  memory extraction before serialisation.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()

#: Event kinds that are security relevant. Keeping the list explicit stops the
#: log from silently filling with routine chatter.
AUDIT_KINDS = frozenset(
    {
        "tool_denied",       # policy/approval refused a tool call
        "approval_denied",   # the user clicked deny
        "sandbox_blocked",   # path/shell guard refused an operation
        "hook_blocked",      # a pre_tool hook denied the call
        "question_dismissed",
        "config_recovered",  # a broken config was backed up and rebuilt
        "selfheal_applied",
        # Security-boundary edits made through the Web API. Changing a rule or
        # an approval profile changes what the agent is allowed to do, so it
        # belongs in the same trail as the refusals those rules produce.
        "permission_rules_written",
        "approval_profile_written",
        "approval_profile_deleted",
        "approval_profile_activated",
        "review_blocked",       # 自动审查拒绝了即将落地的动作
    }
)


def audit_path() -> Path | None:
    """Resolve the audit destination, or None when audit logging is off."""
    raw = (os.environ.get("SPARK_AUDIT_LOG") or "").strip()
    if not raw:
        return None
    if raw.lower() in {"0", "false", "off", "no"}:
        return None
    if raw.lower() in {"1", "true", "on", "yes"}:
        from spark.config import default_home

        return default_home() / "audit.jsonl"
    return Path(raw).expanduser()


def _redact(value: Any) -> Any:
    """Best-effort redaction of anything string-shaped inside the payload."""
    from spark.sanitize import redact_secrets

    if isinstance(value, str):
        return redact_secrets(value)[:2000]
    if isinstance(value, dict):
        return {str(k): _redact(v) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [_redact(v) for v in value[:40]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redact(str(value))


def record(kind: str, **fields: Any) -> None:
    """Append one audit line. Silently no-ops when audit logging is disabled."""
    if kind not in AUDIT_KINDS:
        # Programming error, not a runtime condition: make it loud in dev but
        # still never break the caller's turn.
        logger.warning("unknown audit kind %r (ignored)", kind)
        return
    path = audit_path()
    if path is None:
        return
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "epoch": int(time.time()),
        "kind": kind,
        **_redact(fields),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        with _LOCK:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:  # noqa: BLE001 - audit must never break a turn
        logger.debug("audit write failed", exc_info=True)


def read_recent(limit: int = 50) -> list[dict]:
    """Return the newest *limit* entries (for the diagnostics endpoint)."""
    path = audit_path()
    if path is None or not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out
