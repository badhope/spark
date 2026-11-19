"""HTTP surface for short-lived remote device pairing."""
from __future__ import annotations

from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state
from spark.web.remote import PairingError


def _store(handler: HandlerBase):
    state = _route_state(handler)
    pairing = getattr(state, "pairing", None)
    if pairing is None:
        handler._json(503, {"error": "remote pairing is unavailable"})
        return None, None
    return state, pairing


def handle_pair_code(handler: HandlerBase, body: dict) -> None:
    if not getattr(handler, "_is_loopback", lambda: False)():
        handler._json(403, {"error": "pairing codes can only be minted locally"})
        return
    _state, pairing = _store(handler)
    if pairing is None:
        return
    handler._json(200, pairing.new_code())


def handle_pair_claim(handler: HandlerBase, body: dict) -> None:
    _state, pairing = _store(handler)
    if pairing is None:
        return
    client = str(getattr(handler, "client_address", ("",))[0] or "")
    try:
        result = pairing.claim(
            str(body.get("code") or ""),
            str(body.get("name") or "remote device"),
            client=client,
        )
    except PairingError as exc:
        handler._json(400, {"error": str(exc)})
        return
    handler._json(200, result)


def handle_pair_devices(handler: HandlerBase, body: dict) -> None:
    if not getattr(handler, "_is_loopback", lambda: False)():
        handler._json(403, {"error": "device list is local-only"})
        return
    _state, pairing = _store(handler)
    if pairing is None:
        return
    handler._json(200, {"devices": pairing.devices()})


def handle_pair_revoke(handler: HandlerBase, body: dict) -> None:
    if not getattr(handler, "_is_loopback", lambda: False)():
        handler._json(403, {"error": "device revocation is local-only"})
        return
    _state, pairing = _store(handler)
    if pairing is None:
        return
    device_id = str(body.get("device_id") or "").strip()
    if not pairing.revoke(device_id):
        handler._json(404, {"error": "unknown device"})
        return
    handler._json(200, {"ok": True, "devices": pairing.devices()})
