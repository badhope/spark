"""Web Push for remote approvals.

The browser subscribes once; Spark stores the subscription and sends an
encrypted Web Push message when a turn is waiting for approval.  This is
deliberately separate from the desktop ``Notification`` API in ``notify.ts``:
the latter only fires while the tab is alive, which is exactly the moment a
remote phone is not looking at it.

The private VAPID key and subscriptions live under ``$SPARK_HOME`` with mode
0600.  Push payloads contain only a title, a short body and a relative URL --
never tool arguments or file contents.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

KEY_FILE = "webpush.json"
SUBS_FILE = "webpush-subscriptions.json"
MAX_SUBSCRIPTIONS = 16
ALLOWED_EVENTS = {"approval", "turn_end"}


class PushError(Exception):
    """A push request that should be shown to the caller as a 4xx."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _write_private(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


class WebPushManager:
    """Own VAPID keys, subscriptions and best-effort push delivery."""

    def __init__(self, home: Path, *, clock: Any = time.time) -> None:
        self.home = Path(home)
        self._clock = clock
        self._lock = threading.RLock()
        self._private_pem = ""
        self._public_key = ""
        self._subscriptions: list[dict[str, Any]] = []
        self._load()
        self._prune()

    # -- storage --------------------------------------------------------
    def _key_path(self) -> Path:
        return self.home / KEY_FILE

    def _subs_path(self) -> Path:
        return self.home / SUBS_FILE

    def _load(self) -> None:
        try:
            key_data = json.loads(self._key_path().read_text(encoding="utf-8"))
            self._private_pem = str(key_data.get("private_pem") or "")
            self._public_key = str(key_data.get("public_key") or "")
        except (OSError, ValueError, TypeError):
            self._private_pem = ""
            self._public_key = ""
        if not self._private_pem or not self._public_key:
            self._generate_keypair()
        try:
            raw = json.loads(self._subs_path().read_text(encoding="utf-8"))
            items = raw.get("subscriptions") if isinstance(raw, dict) else raw
            if isinstance(items, list):
                self._subscriptions = [item for item in items if isinstance(item, dict)]
        except (OSError, ValueError, TypeError):
            self._subscriptions = []

    def _generate_keypair(self) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        private_key = ec.generate_private_key(ec.SECP256R1())
        self._private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        public_raw = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
        self._public_key = _b64url(public_raw)
        _write_private(
            self._key_path(),
            {"private_pem": self._private_pem, "public_key": self._public_key},
        )

    def _save(self) -> None:
        _write_private(self._subs_path(), {"subscriptions": self._subscriptions})

    # -- subscriptions --------------------------------------------------
    @staticmethod
    def _validate(subscription: Any) -> dict[str, Any]:
        if not isinstance(subscription, dict):
            raise PushError("subscription must be an object")
        endpoint = str(subscription.get("endpoint") or "").strip()
        parsed = urlparse(endpoint)
        if not parsed.scheme or parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise PushError("subscription endpoint must be an absolute URL")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise PushError("push endpoints must use HTTPS")
        keys = subscription.get("keys")
        if not isinstance(keys, dict):
            raise PushError("subscription keys are required")
        p256dh = str(keys.get("p256dh") or "").strip()
        auth = str(keys.get("auth") or "").strip()
        if len(p256dh) < 16 or len(auth) < 8:
            raise PushError("subscription keys are invalid")
        return {
            "endpoint": endpoint,
            "expirationTime": subscription.get("expirationTime"),
            "keys": {"p256dh": p256dh, "auth": auth},
        }

    def subscribe(
        self,
        subscription: Any,
        *,
        device_id: str = "",
        device_name: str = "",
        events: list[str] | None = None,
    ) -> dict[str, Any]:
        clean = self._validate(subscription)
        selected = [e for e in (events or ["approval"]) if e in ALLOWED_EVENTS]
        if not selected:
            selected = ["approval"]
        record = {
            "id": "push_" + hashlib.sha256(clean["endpoint"].encode("utf-8")).hexdigest()[:12],
            "endpoint": clean["endpoint"],
            "expirationTime": clean.get("expirationTime"),
            "keys": clean["keys"],
            "device_id": str(device_id or "")[:64],
            "device_name": str(device_name or "remote device")[:48],
            "events": selected,
            "created_at": int(self._clock()),
            "last_success_at": 0,
        }
        with self._lock:
            self._subscriptions = [
                item for item in self._subscriptions if item.get("endpoint") != clean["endpoint"]
            ]
            self._subscriptions.append(record)
            if len(self._subscriptions) > MAX_SUBSCRIPTIONS:
                self._subscriptions = self._subscriptions[-MAX_SUBSCRIPTIONS:]
            self._save()
        return self.public_record(record)

    def unsubscribe(self, endpoint: str) -> bool:
        endpoint = str(endpoint or "").strip()
        with self._lock:
            before = len(self._subscriptions)
            self._subscriptions = [
                item for item in self._subscriptions if item.get("endpoint") != endpoint
            ]
            if len(self._subscriptions) != before:
                self._save()
                return True
        return False

    def remove_endpoint(self, endpoint: str) -> None:
        self.unsubscribe(endpoint)

    def _prune(self) -> None:
        now = float(self._clock())
        with self._lock:
            before = len(self._subscriptions)
            self._subscriptions = [
                item
                for item in self._subscriptions
                if not (
                    isinstance(item.get("expirationTime"), (int, float))
                    and float(item["expirationTime"]) / 1000.0 <= now
                )
            ]
            if len(self._subscriptions) != before:
                self._save()

    def public_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": record.get("id"),
            "device_id": record.get("device_id"),
            "device_name": record.get("device_name"),
            "events": record.get("events"),
            "created_at": record.get("created_at"),
            "last_success_at": record.get("last_success_at"),
        }

    def status(self) -> dict[str, Any]:
        try:
            import pywebpush  # noqa: F401
        except ImportError:
            available = False
        else:
            available = True
        return {
            "available": available,
            "public_key": self._public_key,
            "subscriptions": len(self._subscriptions),
            "devices": [
                self.public_record(item)
                for item in self._subscriptions
            ],
        }

    # -- delivery -------------------------------------------------------
    def notify(
        self,
        event: str,
        title: str,
        body: str,
        *,
        url: str = "/",
        tag: str = "spark",
    ) -> int:
        """Send to matching subscriptions in a background thread.

        Returns the number of subscriptions selected; delivery itself is
        best-effort and never blocks an SSE turn.
        """
        if event not in ALLOWED_EVENTS:
            return 0
        with self._lock:
            targets = [
                dict(item)
                for item in self._subscriptions
                if event in (item.get("events") or ["approval"])
            ]
        if not targets:
            return 0
        payload = json.dumps(
            {"title": str(title)[:80], "body": str(body)[:180], "url": url, "tag": tag},
            ensure_ascii=False,
        )
        thread = threading.Thread(
            target=self._deliver,
            args=(targets, payload),
            daemon=True,
            name="spark-webpush",
        )
        thread.start()
        return len(targets)

    def _deliver(self, targets: list[dict[str, Any]], payload: str) -> None:
        try:
            from py_vapid import Vapid01
            from pywebpush import WebPushException, webpush
        except ImportError:
            logger.debug("pywebpush is not installed; remote push skipped")
            return
        try:
            vapid = Vapid01.from_pem(self._private_pem.encode("utf-8"))
        except Exception:
            logger.warning("invalid VAPID key; remote push skipped", exc_info=True)
            return
        for item in targets:
            try:
                webpush(
                    subscription_info={
                        "endpoint": item["endpoint"],
                        "keys": item["keys"],
                    },
                    data=payload,
                    vapid_private_key=vapid,
                    vapid_claims={"sub": "mailto:spark@localhost"},
                    timeout=12,
                )
            except WebPushException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in {404, 410}:
                    self.remove_endpoint(item.get("endpoint") or "")
                    logger.info("removed expired push subscription: %s", item.get("device_name"))
                else:
                    logger.warning("web push failed for %s: %s", item.get("device_name"), exc)
            except Exception:  # noqa: BLE001 - notification must never break a turn
                logger.warning("web push delivery failed", exc_info=True)
            else:
                with self._lock:
                    for current in self._subscriptions:
                        if current.get("endpoint") == item.get("endpoint"):
                            current["last_success_at"] = int(self._clock())
                            self._save()
                            break
