"""Short-lived device pairing for remote Web access.

The control token printed by ``spark web --host 0.0.0.0`` is a break-glass
credential: long-lived, all-powerful and awkward to type on a phone.  This
module adds a one-time 6-digit pairing code, exchanged for a per-device token
with its own expiry.  Only the SHA-256 digest of a device token is kept in
memory; the plaintext token is returned once at claim time.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

CODE_TTL_SEC = 300
DEVICE_TTL_SEC = 7 * 24 * 3600
MAX_ACTIVE_CODES = 5
MAX_DEVICES = 32
CLAIM_WINDOW_SEC = 60
MAX_CLAIMS_PER_WINDOW = 10


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

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": int(self.created_at),
            "expires_at": int(self.expires_at),
            "last_seen_at": int(self.last_seen_at) if self.last_seen_at else 0,
        }


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class DevicePairing:
    def __init__(
        self,
        *,
        code_ttl: int = CODE_TTL_SEC,
        device_ttl: int = DEVICE_TTL_SEC,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.code_ttl = max(30, int(code_ttl))
        self.device_ttl = max(60, int(device_ttl))
        self._clock = clock
        self._codes: dict[str, float] = {}
        self._devices: dict[str, _Device] = {}
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    # -- pairing codes -------------------------------------------------
    def new_code(self) -> dict[str, Any]:
        now = self._clock()
        with self._lock:
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
                "device": device.public(),
                "expires_at": int(device.expires_at),
            }

    def verify(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        now = self._clock()
        digest = _digest(token)
        with self._lock:
            for device in self._devices.values():
                if device.expires_at <= now:
                    continue
                if secrets.compare_digest(device.token_hash, digest):
                    device.last_seen_at = now
                    return device.public()
        return None

    def devices(self) -> list[dict[str, Any]]:
        now = self._clock()
        with self._lock:
            return [
                device.public()
                for device in sorted(self._devices.values(), key=lambda item: item.created_at)
                if device.expires_at > now
            ]

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            return self._devices.pop((device_id or "").strip(), None) is not None
