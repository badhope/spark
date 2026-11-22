"""RC-04: per-session device lease checks shared by HTTP handlers."""
from __future__ import annotations

from typing import Any

from spark.web._routing import _route_state


def caller_id(handler: Any) -> str:
    """Return ``local`` or ``dev:<id>`` for the authenticated caller."""
    role = getattr(handler, "_auth_role", lambda: None)()
    if role == "control":
        return "local"
    if role != "device":
        return ""
    state = _route_state(handler)
    pairing = getattr(state, "pairing", None)
    if pairing is None:
        return ""
    token = str(getattr(handler, "headers", {}).get("X-Spark-Token") or "")
    device = pairing.device_for_token(token)
    if device is None:
        return ""
    return f"dev:{device.get('id') or ''}"


def lease_reject(
    handler: Any,
    runtime: Any,
    *,
    acquire: bool = False,
    takeover: bool = False,
) -> dict[str, Any] | None:
    """Validate a session write.

    Local control always passes. A device may acquire a free lease, renew its
    own lease, or explicitly take over another device's lease. Anything else
    gets a 409 with the current lease snapshot.
    """
    if runtime is None:
        owner = caller_id(handler)
        if owner == "local":
            return None
        return {"status": 500, "body": {"error": "session runtime is unavailable"}}
    owner = caller_id(handler)
    if owner == "local":
        return None
    if not owner:
        return {"status": 403, "body": {"error": "device identity is unavailable"}}
    if acquire:
        result = runtime.lease_claim(owner, takeover=takeover)
        if result.get("ok"):
            return None
    elif runtime.lease_allows(owner):
        return None
    else:
        result = runtime.lease_snapshot()
    return {
        "status": 409,
        "body": {
            "error": "this session is leased to another device",
            "code": "lease_held",
            "caller": owner,
            "lease": result,
            "hint": "POST /api/session/lease with {\"action\":\"takeover\"} to take control.",
        },
    }
