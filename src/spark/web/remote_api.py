"""HTTP surface for remote device pairing, QR links, and LAN access."""
from __future__ import annotations

from typing import Any

from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state
from spark.web.remote import PairingError
from spark.web.remote_access import RemoteAccessError, qr_data_url


def _store(handler: HandlerBase):
    state = _route_state(handler)
    pairing = getattr(state, "pairing", None)
    if pairing is None:
        handler._json(503, {"error": "remote pairing is unavailable"})
        return None, None
    return state, pairing


def _local_control(handler: HandlerBase) -> bool:
    """Administrative remote-access actions are local-control only.

    A request through a configured public origin is deliberately treated as
    non-local even when a reverse proxy dials 127.0.0.1. This check therefore
    sits on ``_is_loopback`` (which already excludes public requests) instead
    of the source address alone.
    """
    is_loopback = getattr(handler, "_is_loopback", lambda: False)
    if not is_loopback():
        handler._json(403, {"error": "remote access can only be managed locally"})
        return False
    role = getattr(handler, "_auth_role", lambda: None)()
    if role != "control":
        handler._json(403, {"error": "remote access requires the local control token"})
        return False
    return True


def handle_pair_code(handler: HandlerBase, body: dict) -> None:
    if not getattr(handler, "_is_loopback", lambda: False)():
        handler._json(403, {"error": "pairing codes can only be minted locally"})
        return
    state, pairing = _store(handler)
    if state is None or pairing is None:
        return
    code = pairing.new_code()
    audit = getattr(state, "audit", None)
    if audit is not None:
        audit.record("local", "本机", "pair.code", detail="一次性配对码", source="local")
    handler._json(200, code)


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
    state = _state
    audit = getattr(state, "audit", None)
    if audit is not None:
        device = result.get("device") or {}
        audit.record(
            str(device.get("id") or ""),
            str(device.get("name") or "remote device"),
            "pair.claimed",
            detail=f"来自 {client or 'unknown'}",
            source="device",
        )
    handler._json(200, result)


def handle_pair_devices(handler: HandlerBase, body: dict) -> None:
    if not getattr(handler, "_is_loopback", lambda: False)():
        handler._json(403, {"error": "device list is local-only"})
        return
    _state, pairing = _store(handler)
    if pairing is None:
        return
    handler._json(200, {"devices": pairing.presence()})


def handle_remote_admin(handler: HandlerBase, body: dict) -> None:
    """Local-only grant/revoke of the one-shot 30-minute device_admin scope."""
    if not _local_control(handler):
        return
    state, pairing = _store(handler)
    if state is None or pairing is None:
        return
    action = str(body.get("action") or "grant").strip().lower()
    device_id = str(body.get("device_id") or "").strip()
    if not device_id:
        handler._json(400, {"error": "device_id required"})
        return
    audit = getattr(state, "audit", None)
    if action == "grant":
        try:
            device = pairing.grant_admin(device_id)
        except PairingError as exc:
            status = 404 if "unknown device" in str(exc) else 409
            handler._json(status, {"error": str(exc)})
            return
        if audit is not None:
            audit.record(
                "local",
                "本机",
                "device.admin.grant",
                detail=(
                    f"{device.get('name') or device_id} · {device_id} · "
                    f"30 分钟 · expires_in {device.get('admin_expires_in')}s"
                ),
                source="local",
            )
        handler._json(
            200,
            {
                "ok": True,
                "action": "grant",
                "device": device,
                "devices": pairing.devices(),
            },
        )
        return
    if action == "revoke":
        device = pairing.revoke_admin(device_id)
        if device is None:
            handler._json(404, {"error": "unknown device"})
            return
        if audit is not None:
            audit.record(
                "local",
                "本机",
                "device.admin.revoke",
                detail=f"{device.get('name') or device_id} · {device_id}",
                source="local",
            )
        handler._json(
            200,
            {
                "ok": True,
                "action": "revoke",
                "device": device,
                "devices": pairing.devices(),
            },
        )
        return
    handler._json(400, {"error": "action must be grant or revoke"})


def handle_pair_revoke(handler: HandlerBase, body: dict) -> None:
    if not getattr(handler, "_is_loopback", lambda: False)():
        handler._json(403, {"error": "device revocation is local-only"})
        return
    _state, pairing = _store(handler)
    if pairing is None:
        return
    device_id = str(body.get("device_id") or "").strip()
    target: dict[str, Any] = next(
        (item for item in pairing.devices() if item["id"] == device_id), {}
    )
    if not pairing.revoke(device_id):
        handler._json(404, {"error": "unknown device"})
        return
    audit = getattr(_state, "audit", None)
    if audit is not None:
        audit.record(
            "local",
            "本机",
            "pair.revoked",
            detail=f"{target.get('name') or device_id} ({device_id})",
            source="local",
        )
    handler._json(200, {"ok": True, "devices": pairing.presence()})


def handle_remote_status(handler: HandlerBase, body: dict) -> None:
    if not _local_control(handler):
        return
    state, pairing = _store(handler)
    if state is None or pairing is None:
        return
    manager = getattr(state, "remote_access", None)
    if manager is None:
        handler._json(503, {"error": "remote access is unavailable"})
        return
    handler._json(200, manager.status(device_count=len(pairing.devices())))


def handle_remote_link(handler: HandlerBase, body: dict) -> None:
    """Mint a one-time code and return a QR/link for a phone."""
    if not _local_control(handler):
        return
    state, pairing = _store(handler)
    if state is None or pairing is None:
        return
    manager = getattr(state, "remote_access", None)
    if manager is None:
        handler._json(503, {"error": "remote access is unavailable"})
        return
    try:
        target = manager.ensure_base(str(body.get("mode") or "auto"))
        # A new QR rotates every older code: refreshing must invalidate the
        # previous screenshot instead of leaving it usable for five minutes.
        code = pairing.new_code(rotate=True)
        base_url = str(target["base_url"]).rstrip("/")
        url = f"{base_url}/#pair={code['code']}"
        qr = qr_data_url(url)
    except RemoteAccessError as exc:
        handler._json(409, {"error": str(exc)})
        return
    audit = getattr(state, "audit", None)
    if audit is not None:
        audit.record(
            "local",
            "本机",
            "remote.link",
            detail=f"{target['mode']} · {base_url}",
            source="local",
        )
    handler._json(
        200,
        {
            "url": url,
            "qr_data_url": qr,
            "code": code["code"],
            "expires_at": code["expires_at"],
            "ttl_sec": code["ttl_sec"],
            "mode": target["mode"],
            "base_url": base_url,
            "lan_url": target.get("lan_url") or "",
            "device_ttl_sec": pairing.device_ttl,
            "status": manager.status(device_count=len(pairing.devices())),
        },
    )


def handle_remote_stop(handler: HandlerBase, body: dict) -> None:
    """Revoke every paired phone and stop the temporary LAN listener."""
    if not _local_control(handler):
        return
    state, pairing = _store(handler)
    if state is None or pairing is None:
        return
    manager = getattr(state, "remote_access", None)
    if manager is None:
        handler._json(503, {"error": "remote access is unavailable"})
        return
    revoked = pairing.revoke_all()
    cleared = pairing.clear_codes()
    manager.stop_lan()
    audit = getattr(state, "audit", None)
    if audit is not None:
        audit.record(
            "local",
            "本机",
            "remote.stop",
            detail=f"revoked {revoked} devices, cleared {cleared} codes",
            source="local",
        )
    status = manager.status(device_count=0)
    status["revoked_devices"] = revoked
    status["cleared_codes"] = cleared
    handler._json(200, {"ok": True, "status": status})


def _push(handler: HandlerBase):
    state = _route_state(handler)
    manager = getattr(state, "push", None)
    if manager is None:
        handler._json(503, {"error": "web push is unavailable"})
        return None, None
    return state, manager


def _current_device(handler: HandlerBase) -> dict | None:
    state = _route_state(handler)
    pairing = getattr(state, "pairing", None)
    if pairing is None:
        return None
    token = str(getattr(handler, "headers", {}).get("X-Spark-Token") or "")
    return pairing.device_for_token(token) if token else None


def handle_push_status(handler: HandlerBase, body: dict) -> None:
    _state, manager = _push(handler)
    if manager is None:
        return
    handler._json(200, manager.status())


def handle_push_subscribe(handler: HandlerBase, body: dict) -> None:
    state, manager = _push(handler)
    if state is None or manager is None:
        return
    device = _current_device(handler) or {}
    try:
        record = manager.subscribe(
            body.get("subscription"),
            device_id=str(device.get("id") or "local"),
            device_name=str(device.get("name") or "本机浏览器"),
            events=body.get("events") if isinstance(body.get("events"), list) else None,
        )
    except Exception as exc:  # noqa: BLE001 - validation boundary
        handler._json(400, {"error": str(exc)})
        return
    handler._json(200, {"ok": True, "subscription": record, "status": manager.status()})


def handle_push_unsubscribe(handler: HandlerBase, body: dict) -> None:
    _state, manager = _push(handler)
    if manager is None:
        return
    endpoint = str(body.get("endpoint") or "").strip()
    removed = manager.unsubscribe(endpoint)
    handler._json(200, {"ok": True, "removed": removed, "status": manager.status()})


def handle_push_test(handler: HandlerBase, body: dict) -> None:
    if not _local_control(handler):
        return
    _state, manager = _push(handler)
    if manager is None:
        return
    sent = manager.notify(
        "approval",
        "Spark 远程通知测试",
        "如果你在锁屏时看到这条通知，后台审批提醒已经生效。",
        url="/",
        tag="spark-test",
    )
    state = _route_state(handler)
    audit = getattr(state, "audit", None)
    if audit is not None:
        audit.record("local", "本机", "push.test", detail=f"selected {sent}", source="local")
    handler._json(200, {"ok": True, "selected": sent})


def handle_presence_heartbeat(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    pairing = getattr(state, "pairing", None)
    if pairing is None:
        handler._json(503, {"error": "remote pairing is unavailable"})
        return
    token = str(getattr(handler, "headers", {}).get("X-Spark-Token") or "")
    device = pairing.touch(
        token,
        path="/api/presence/heartbeat",
        session_id=str(getattr(state, "session_id", "") or ""),
        user_agent=str(getattr(handler, "headers", {}).get("User-Agent") or ""),
    )
    if device is None:
        handler._json(401, {"error": "unknown or expired device token"})
        return
    # RC-04: a live remote page renews its session lease on the same heartbeat
    # that refreshes presence. Without this the lease expires after 120s.
    session_id = str(body.get("session_id") or getattr(state, "session_id", "") or "")
    runtime = state.runtime_for(session_id) if session_id else None
    lease = None
    if runtime is not None:
        owner = f"dev:{device.get('id') or ''}"
        if runtime.lease_owner == owner:
            runtime.lease_renew(owner)
        lease = runtime.lease_snapshot()
    handler._json(
        200,
        {"ok": True, "device": {**device, "online": True}, "lease": lease},
    )


def handle_remote_audit(handler: HandlerBase, body: dict) -> None:
    if not _local_control(handler):
        return
    state = _route_state(handler)
    audit = getattr(state, "audit", None)
    if audit is None:
        handler._json(503, {"error": "remote audit is unavailable"})
        return
    limit = 50
    raw = str(getattr(handler, "path", "") or "")
    if "?" in raw:
        from urllib.parse import parse_qs, urlparse

        values = parse_qs(urlparse(raw).query).get("limit") or []
        if values:
            try:
                limit = int(values[0])
            except ValueError:
                limit = 50
    handler._json(200, {"entries": audit.tail(limit)})
