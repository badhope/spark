"""RC-05: helpers for the temporary device_admin scope.

A paired device is normally read/run only. The local control plane may grant a
one-shot 30-minute ``device_admin`` scope, which unlocks non-shell settings and
plugin toggles. This module centralises the field-level denylist so the HTTP
route guard, settings handler and plugin handler agree on the boundary.
"""
from __future__ import annotations

from typing import Any

ADMIN_TTL_SEC = 1800

#: Top-level settings sections that install code or widen the execution sandbox.
DENIED_SETTINGS_SECTIONS = frozenset(
    {
        "hooks",
        "mcp_servers",
        "permission_rules",
        "permission_scopes",
        "approval_profiles",
        "active_approval_profile_id",
    }
)

#: ``agent`` fields that change approval, sandbox, filesystem or shell policy.
DENIED_AGENT_FIELDS = frozenset(
    {
        "approval",
        "sandbox_mode",
        "network_access",
        "extra_dirs",
        "workdir_only",
        "protected_paths",
        "format_on_write",
        "auto_review",
        "allowed_tools",
        "shell_timeout_sec",
    }
)


def caller_role(handler: Any) -> str:
    """Return the handler's effective role without raising."""
    try:
        role = getattr(handler, "_auth_role", lambda: None)()
    except Exception:  # noqa: BLE001 - a broken auth helper is not an admin grant
        return ""
    return str(role or "")


def is_device_admin(handler: Any) -> bool:
    return caller_role(handler) == "device_admin"


def denied_settings_fields(body: dict[str, Any]) -> list[str]:
    """Return the submitted settings fields a device_admin may not change."""
    denied: list[str] = [key for key in DENIED_SETTINGS_SECTIONS if key in body]
    agent_in = body.get("agent")
    if isinstance(agent_in, dict):
        denied.extend(
            f"agent.{key}" for key in DENIED_AGENT_FIELDS if key in agent_in
        )
    return sorted(denied)


def reject_denied_settings(handler: Any, body: dict[str, Any]) -> bool:
    """Write a 403 when a device_admin submits a shell/security field.

    Returns ``True`` when the request was rejected and the caller should stop.
    Local control is never filtered here.
    """
    if caller_role(handler) != "device_admin":
        return False
    denied = denied_settings_fields(body)
    if not denied:
        return False
    handler._json(
        403,
        {
            "error": "device_admin cannot change shell, approval or sandbox settings",
            "code": "device_admin_field_denied",
            "fields": denied,
            "hint": "ask the local control plane to change these fields",
        },
    )
    return True


def admin_write_paths(path: str) -> bool:
    """Additional mutating endpoints unlocked by ``device_admin``."""
    return path in {
        "/api/settings",
        "/api/plugins",
        "/api/models/save",
        "/api/models/delete",
        "/api/models/activate",
        "/api/local-models/activate",
        "/api/demo",
        "/api/knowledge",
        "/api/wiki",
        "/api/prompts",
        "/api/skills",
        "/api/agents_md",
        "/api/memory",
    }


def explicitly_denied_path(path: str) -> bool:
    """Endpoints a device_admin must never reach, even with a valid scope."""
    return (
        path.startswith("/api/pty/")
        or path.startswith("/api/pair/")
        or path == "/api/shell"
        or path.startswith("/api/remote/link")
        or path.startswith("/api/remote/stop")
        or path == "/api/remote/admin"
        or (path.startswith("/api/debug") and path != "/api/debug/client_log")
    )
