"""Short-lived device pairing for remote Web access.

The control token printed by ``spark web --host 0.0.0.0`` is a break-glass
credential: long-lived, all-powerful and awkward to type on a phone.  This
module adds a one-time 6-digit pairing code, exchanged for a per-device token
with its own expiry.  Only the SHA-256 digest of a device token is kept in
memory; the plaintext token is returned once at claim time.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

CODE_TTL_SEC = 300
DEVICE_TTL_SEC = 7 * 24 * 3600
MAX_ACTIVE_CODES = 5
MAX_DEVICES = 32
CLAIM_WINDOW_SEC = 60
MAX_CLAIMS_PER_WINDOW = 10
PRESENCE_WINDOW_SEC = 120
ADMIN_TTL_SEC = 1800
AUDIT_MAX_ENTRIES = 500

logger = logging.getLogger(__name__)


class PairingError(Exception):
    """A pairing request that must be rejected with a 4xx response."""


@dataclass
class _Device:
    id: str
    name: str
    token_hash: str
    created_at: float
    expires_at: float
    last_seen_at: float = 0.0
    last_path: str = ""
    last_session_id: str = ""
    user_agent: str = ""
    # RC-05: a device is read/run by default. Local control may grant a
    # one-shot 30-minute device_admin scope; after it expires the same pairing
    # cannot request it again and the phone must re-pair.
    scope: str = "device"
    admin_expires_at: float = 0.0
    admin_granted_at: float = 0.0

    def public(self, now: float) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": int(self.created_at),
            "expires_at": int(self.expires_at),
            "last_seen_at": int(self.last_seen_at) if self.last_seen_at else 0,
            "last_path": self.last_path,
            "last_session_id": self.last_session_id,
            "scope": self.scope,
            "admin_expires_in": (
                max(0, int(self.admin_expires_at - now))
                if self.scope == "device_admin"
                else 0
            ),
            "admin_used": bool(self.admin_granted_at),
        }


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class DevicePairing:
    def __init__(
        self,
        *,
        code_ttl: int = CODE_TTL_SEC,
        device_ttl: int = DEVICE_TTL_SEC,
        admin_ttl: int = ADMIN_TTL_SEC,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.code_ttl = max(30, int(code_ttl))
        self.device_ttl = max(60, int(device_ttl))
        self.admin_ttl = min(max(60, int(admin_ttl)), ADMIN_TTL_SEC)
        self._clock = clock
        self._codes: dict[str, float] = {}
        self._devices: dict[str, _Device] = {}
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._admin_expiry_listener: Callable[[dict[str, Any]], None] | None = None

    # -- pairing codes -------------------------------------------------
    def new_code(self, *, rotate: bool = False) -> dict[str, Any]:
        """Mint a one-time code.

        ``rotate=True`` invalidates every older code. The remote-link UI uses
        that mode so a screenshot of an old QR cannot be reused after the user
        presses "刷新".
        """
        now = self._clock()
        with self._lock:
            if rotate:
                self._codes.clear()
            self._prune_codes(now)
            if len(self._codes) >= MAX_ACTIVE_CODES:
                oldest = min(self._codes, key=lambda key: self._codes[key])
                self._codes.pop(oldest, None)
            code = f"{secrets.randbelow(1_000_000):06d}"
            self._codes[code] = now + self.code_ttl
            return {
                "code": code,
                "expires_at": int(now + self.code_ttl),
                "ttl_sec": self.code_ttl,
            }

    def clear_codes(self) -> int:
        """Invalidate every outstanding pairing code and return the count."""
        with self._lock:
            count = len(self._codes)
            self._codes.clear()
            return count

    def _prune_codes(self, now: float) -> None:
        for code, expires in list(self._codes.items()):
            if expires <= now:
                self._codes.pop(code, None)

    def _rate_limited(self, client: str, now: float) -> bool:
        attempts = [stamp for stamp in self._attempts.get(client, []) if now - stamp < CLAIM_WINDOW_SEC]
        self._attempts[client] = attempts
        return len(attempts) >= MAX_CLAIMS_PER_WINDOW

    # -- claim / verify ------------------------------------------------
    def claim(self, code: str, name: str, *, client: str = "") -> dict[str, Any]:
        now = self._clock()
        clean_code = (code or "").strip()
        if len(clean_code) != 6 or not clean_code.isdigit():
            raise PairingError("pairing code must be 6 digits")
        clean_name = (name or "remote device").strip()[:48] or "remote device"
        with self._lock:
            if self._rate_limited(client or "-", now):
                raise PairingError("too many pairing attempts; wait a minute")
            self._attempts.setdefault(client or "-", []).append(now)
            self._prune_codes(now)
            expires = self._codes.get(clean_code)
            if expires is None or expires <= now:
                raise PairingError("invalid or expired pairing code")
            self._codes.pop(clean_code, None)
            if len(self._devices) >= MAX_DEVICES:
                oldest = min(
                    self._devices, key=lambda key: self._devices[key].created_at
                )
                self._devices.pop(oldest, None)
            token = secrets.token_urlsafe(32)
            device_id = "dev_" + secrets.token_hex(8)
            device = _Device(
                id=device_id,
                name=clean_name,
                token_hash=_digest(token),
                created_at=now,
                expires_at=now + self.device_ttl,
            )
            self._devices[device_id] = device
            return {
                "token": token,
                "device": device.public(now),
                "expires_at": int(device.expires_at),
            }

    def verify(self, token: str) -> dict[str, Any] | None:
        return self.touch(token)

    def touch(
        self,
        token: str,
        *,
        path: str = "",
        session_id: str = "",
        user_agent: str = "",
    ) -> dict[str, Any] | None:
        """Verify a token and refresh its presence heartbeat."""
        if not token:
            return None
        now = self._clock()
        digest = _digest(token)
        with self._lock:
            self._expire_admins(now)
            for device in self._devices.values():
                if device.expires_at <= now:
                    continue
                if secrets.compare_digest(device.token_hash, digest):
                    device.last_seen_at = now
                    if path:
                        device.last_path = path[:160]
                    if session_id:
                        device.last_session_id = session_id[:80]
                    if user_agent:
                        device.user_agent = user_agent[:200]
                    return device.public(now)
        return None

    def device_for_token(self, token: str) -> dict[str, Any] | None:
        """Return the public device record for *token* without duplicating a verify."""
        if not token:
            return None
        now = self._clock()
        digest = _digest(token)
        with self._lock:
            self._expire_admins(now)
            for device in self._devices.values():
                if device.expires_at <= now:
                    continue
                if secrets.compare_digest(device.token_hash, digest):
                    return device.public(now)
        return None

    def devices(self) -> list[dict[str, Any]]:
        now = self._clock()
        with self._lock:
            self._expire_admins(now)
            return [
                device.public(now)
                for device in sorted(self._devices.values(), key=lambda item: item.created_at)
                if device.expires_at > now
            ]

    def presence(self) -> list[dict[str, Any]]:
        """Device roster with an online flag derived from the last API touch."""
        now = self._clock()
        with self._lock:
            self._expire_admins(now)
            records: list[dict[str, Any]] = []
            for device in sorted(self._devices.values(), key=lambda item: item.created_at):
                if device.expires_at <= now:
                    continue
                record = device.public(now)
                record["online"] = bool(
                    device.last_seen_at and now - device.last_seen_at <= PRESENCE_WINDOW_SEC
                )
                record["idle_sec"] = (
                    int(now - device.last_seen_at) if device.last_seen_at else None
                )
                records.append(record)
            return records

    def set_admin_expiry_listener(
        self, listener: Callable[[dict[str, Any]], None] | None
    ) -> None:
        """Register a callback used to audit automatic device_admin expiry."""
        self._admin_expiry_listener = listener

    def _expire_admins(self, now: float) -> None:
        """Downgrade expired admin scopes and notify the audit listener.

        Called while ``_lock`` is held. The listener must only append to the
        audit log (it must not call back into DevicePairing).
        """
        for device in self._devices.values():
            if device.scope != "device_admin" or device.admin_expires_at > now:
                continue
            device.scope = "device"
            device.admin_expires_at = 0.0
            listener = self._admin_expiry_listener
            if listener is not None:
                try:
                    listener(device.public(now))
                except Exception:  # noqa: BLE001 - expiry must not break auth
                    logger.debug("device_admin expiry listener failed", exc_info=True)

    def grant_admin(self, device_id: str, *, ttl: int | None = None) -> dict[str, Any]:
        """Grant a one-shot device_admin scope to one paired device."""
        now = self._clock()
        clean_id = (device_id or "").strip()
        with self._lock:
            device = self._devices.get(clean_id)
            if device is None or device.expires_at <= now:
                raise PairingError("unknown device")
            if device.admin_granted_at:
                raise PairingError(
                    "device_admin already used for this pairing; re-pair the device to request it again"
                )
            device.scope = "device_admin"
            device.admin_granted_at = now
            device.admin_expires_at = now + (
                self.admin_ttl if ttl is None else min(max(60, int(ttl)), ADMIN_TTL_SEC)
            )
            return device.public(now)

    def revoke_admin(self, device_id: str) -> dict[str, Any] | None:
        """End an active device_admin scope early; the pairing cannot reapply."""
        now = self._clock()
        clean_id = (device_id or "").strip()
        with self._lock:
            device = self._devices.get(clean_id)
            if device is None or device.expires_at <= now:
                return None
            device.scope = "device"
            device.admin_expires_at = 0.0
            return device.public(now)

    def admin_scope(self, token: str) -> dict[str, Any] | None:
        """Return the effective scope for a token, pruning expired admin grants."""
        if not token:
            return None
        now = self._clock()
        digest = _digest(token)
        with self._lock:
            self._expire_admins(now)
            for device in self._devices.values():
                if device.expires_at <= now:
                    continue
                if secrets.compare_digest(device.token_hash, digest):
                    return {
                        "scope": device.scope,
                        "admin_expires_in": (
                            max(0, int(device.admin_expires_at - now))
                            if device.scope == "device_admin"
                            else 0
                        ),
                        "admin_used": bool(device.admin_granted_at),
                    }
        return None

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            return self._devices.pop((device_id or "").strip(), None) is not None

    def revoke_all(self) -> int:
        """Revoke every paired device (the big red "stop remote access" button)."""
        with self._lock:
            count = len(self._devices)
            self._devices.clear()
            return count


class RemoteAuditLog:
    """Append-only JSONL audit trail for remote control actions.

    Entries never contain tokens, prompts or file bodies: only actor, action,
    a short target and a timestamp. The file is 0600 and loaded on startup so
    the trail survives a Spark restart.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        max_entries: int = AUDIT_MAX_ENTRIES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path
        self.max_entries = max(50, int(max_entries))
        self._clock = clock
        self._entries: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        if path is not None:
            self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines()[-self.max_entries :]:
                line = line.strip()
                if line:
                    self._entries.append(json.loads(line))
        except Exception:
            logger.debug("could not load remote audit log", exc_info=True)

    def record(
        self,
        actor_id: str,
        actor_name: str,
        action: str,
        *,
        detail: str = "",
        source: str = "device",
    ) -> dict[str, Any]:
        entry = {
            "at": int(self._clock()),
            "actor_id": (actor_id or "")[:80],
            "actor_name": (actor_name or "")[:80],
            "action": (action or "")[:80],
            "detail": (detail or "")[:240],
            "source": source,
        }
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self.max_entries:
                self._entries = self._entries[-self.max_entries :]
            if self.path is not None:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with self.path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    os.chmod(self.path, 0o600)
                except Exception:
                    logger.debug("could not append remote audit log", exc_info=True)
        return entry

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._entries[-max(1, min(int(limit), self.max_entries)) :])
