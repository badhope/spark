"""Outbound HTTP relay for Spark Web.

The relay is a small, dependency-free rendezvous service. A Spark process
connects *out* to the relay (so it works behind NAT), polls for requests, and
forwards them to its loopback Web server. Remote clients submit requests to the
relay and receive the upstream response.

Security model:
- Every channel has a high-entropy id and a separate client/agent key. The relay
  stores only a SHA-256 digest of the key and never logs it.
- Spark still validates its own ``X-Spark-Token`` / paired-device token after a
  request is forwarded. The relay key only protects the channel, not Spark.
- The relay is intentionally plaintext HTTP. Put it behind a TLS-terminating
  reverse proxy; do not expose it directly to the internet.
- Request and response bodies are base64 encoded so JSON and binary payloads
  (for example screenshots) survive the hop.
- Streaming responses (SSE and chunked HTTP) use a separate request/stream
  rendezvous: the agent opens ``httpx.stream`` and posts headers/chunks/end;
  the relay keeps the client connection open and writes chunked HTTP as data
  arrives. Non-streaming calls keep the buffered ``/responses`` path.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import queue
import re
import secrets
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_CALL_TIMEOUT = 120
DEFAULT_AGENT_STALE_SEC = 45
CHANNEL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "accept-encoding",
    "content-encoding",
    "x-relay-key",
    "x-spark-relay",
    "origin",
    "referer",
    "sec-fetch-site",
}


class RelayError(Exception):
    """A relay error that maps to a useful HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class RelayNotFound(RelayError):
    def __init__(self, message: str = "relay channel not found") -> None:
        super().__init__(message, 404)


class RelayUnauthorized(RelayError):
    def __init__(self, message: str = "relay key rejected") -> None:
        super().__init__(message, 403)


class RelayUnavailable(RelayError):
    def __init__(self, message: str = "no relay agent is polling this channel") -> None:
        super().__init__(message, 503)


class RelayTimeout(RelayError):
    def __init__(self, message: str = "relay request timed out") -> None:
        super().__init__(message, 504)


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _check_channel(channel: str) -> str:
    channel = (channel or "").strip()
    if not CHANNEL_ID_RE.fullmatch(channel):
        raise RelayError("channel id must be 8-64 chars: letters, digits, _ or -")
    return channel


def _check_key(key: str) -> str:
    key = key or ""
    if len(key) < 16:
        raise RelayError("relay key must be at least 16 characters")
    return key


def _headers_dict(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RelayError("headers must be an object")
    result: dict[str, str] = {}
    for name, item in value.items():
        name = str(name).strip()
        if not name or name.lower() in _HOP_HEADERS:
            continue
        text = str(item)
        if "\r" in name or "\n" in name or "\r" in text or "\n" in text:
            raise RelayError("header names/values must not contain CR or LF")
        result[name] = text
    return result


def _unb64(value: Any) -> bytes:
    if not value:
        return b""
    try:
        return base64.b64decode(str(value), validate=True)
    except (ValueError, TypeError) as exc:
        raise RelayError("body_b64 is not valid base64") from exc


def _timeout_value(value: Any, default: float = 60.0) -> float:
    try:
        seconds = float(value if value is not None else default)
    except (TypeError, ValueError) as exc:
        raise RelayError("timeout must be a number") from exc
    return max(5.0, min(seconds, float(MAX_CALL_TIMEOUT)))


@dataclass
class _Channel:
    key_hash: str
    requests: "queue.Queue[dict[str, Any]]" = field(default_factory=queue.Queue)
    responses: dict[str, "queue.Queue[dict[str, Any]]"] = field(default_factory=dict)
    streams: dict[str, "queue.Queue[dict[str, Any]]"] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_poll: float = 0.0


class RelayHub:
    """In-memory channel registry and request/response rendezvous."""

    def __init__(self, *, agent_stale_sec: int = DEFAULT_AGENT_STALE_SEC) -> None:
        self.agent_stale_sec = max(10, int(agent_stale_sec))
        self._channels: dict[str, _Channel] = {}
        self._lock = threading.Lock()

    def register(self, channel: str, key: str) -> None:
        channel = _check_channel(channel)
        key = _check_key(key)
        digest = _digest(key)
        with self._lock:
            existing = self._channels.get(channel)
            if existing is not None:
                if not hmac.compare_digest(existing.key_hash, digest):
                    raise RelayUnauthorized("channel is already registered with another key")
                return
            self._channels[channel] = _Channel(key_hash=digest)

    def _channel(self, channel: str, key: str) -> _Channel:
        channel = _check_channel(channel)
        key = _check_key(key)
        with self._lock:
            item = self._channels.get(channel)
        if item is None:
            raise RelayNotFound()
        if not hmac.compare_digest(item.key_hash, _digest(key)):
            raise RelayUnauthorized()
        return item

    def seen(self, channel: str) -> bool:
        """Report whether an agent has polled recently; useful for CLI checks."""
        item = self._channels.get(channel)
        return bool(item and time.time() - item.last_poll <= self.agent_stale_sec)

    def wait_for_agent(self, channel: str, timeout: float = 5.0) -> bool:
        deadline = time.time() + max(0.2, float(timeout))
        while time.time() < deadline:
            if self.seen(channel):
                return True
            time.sleep(0.05)
        return False

    def next_request(self, channel: str, key: str, wait: float = 25.0) -> dict[str, Any] | None:
        item = self._channel(channel, key)
        item.last_poll = time.time()
        try:
            request = item.requests.get(timeout=max(0.5, min(float(wait), 30.0)))
        except queue.Empty:
            return None
        item.last_poll = time.time()
        return request

    def submit(
        self,
        channel: str,
        key: str,
        payload: dict[str, Any],
        *,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        item = self._channel(channel, key)
        if time.time() - item.last_poll > self.agent_stale_sec:
            raise RelayUnavailable()
        request_id = "req_" + secrets.token_hex(10)
        response_queue: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=1)
        with item.lock:
            item.responses[request_id] = response_queue
        request = {
            "request_id": request_id,
            "method": str(payload.get("method") or "GET").upper(),
            "path": str(payload.get("path") or "/"),
            "headers": _headers_dict(payload.get("headers")),
            "body_b64": _b64_payload(payload.get("body_b64")),
        }
        item.requests.put(request)
        try:
            return response_queue.get(timeout=max(1.0, min(float(timeout), MAX_CALL_TIMEOUT)))
        except queue.Empty as exc:
            raise RelayTimeout() from exc
        finally:
            with item.lock:
                item.responses.pop(request_id, None)

    def complete(self, channel: str, key: str, request_id: str, response: dict[str, Any]) -> None:
        item = self._channel(channel, key)
        with item.lock:
            target = item.responses.get(str(request_id))
        if target is None:
            # The caller timed out; never let a late response crash the agent.
            return
        target.put(response)

    def start_stream(
        self,
        channel: str,
        key: str,
        payload: dict[str, Any],
        *,
        timeout: float = 60.0,
    ) -> tuple[str, "queue.Queue[dict[str, Any]]"]:
        """Queue a streaming request and return its (request_id, event queue)."""
        item = self._channel(channel, key)
        if time.time() - item.last_poll > self.agent_stale_sec:
            raise RelayUnavailable()
        request_id = "req_" + secrets.token_hex(10)
        events: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=64)
        with item.lock:
            item.streams[request_id] = events
        item.requests.put(
            {
                "request_id": request_id,
                "method": str(payload.get("method") or "GET").upper(),
                "path": str(payload.get("path") or "/"),
                "headers": _headers_dict(payload.get("headers")),
                "body_b64": _b64_payload(payload.get("body_b64")),
                "stream": True,
                "timeout": max(1.0, min(float(timeout), MAX_CALL_TIMEOUT)),
            }
        )
        return request_id, events

    def stream_event(
        self, channel: str, key: str, request_id: str, event: dict[str, Any]
    ) -> bool:
        """Deliver one headers/chunk/end/error event to a waiting client.

        Returns ``False`` when the client has disconnected or timed out, so the
        agent can stop reading upstream instead of filling memory.
        """
        item = self._channel(channel, key)
        with item.lock:
            target = item.streams.get(str(request_id))
        if target is None:
            return False
        try:
            target.put(event, timeout=10.0)
        except queue.Full as exc:
            raise RelayTimeout("stream consumer is not reading") from exc
        return True

    def drop_stream(self, channel: str, request_id: str) -> None:
        item = self._channels.get(channel)
        if item is None:
            return
        with item.lock:
            item.streams.pop(str(request_id), None)


class RelayHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    block_on_close = False

    hub: RelayHub
    web_channel: str = ""
    web_key: str = ""


def build_relay_server(
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    web_channel: str = "",
    web_key: str = "",
) -> tuple[RelayHTTPServer, RelayHub]:
    hub = RelayHub()
    server = RelayHTTPServer((host, int(port)), _RelayHandler)
    server.hub = hub
    if web_channel:
        # A relay instance can expose one Spark Web at its root. The channel
        # and key are server-side; the browser never receives the relay key.
        server.web_channel = _check_channel(web_channel)
        server.web_key = _check_key(web_key)
        hub.register(server.web_channel, server.web_key)
    elif web_key:
        raise RelayError("web_key requires web_channel")
    return server, hub


def serve_relay(
    host: str = "127.0.0.1",
    port: int = 8788,
    *,
    web_channel: str = "",
    web_key: str = "",
) -> None:
    server, _hub = build_relay_server(
        host,
        port,
        web_channel=web_channel,
        web_key=web_key,
    )
    address = server.server_address
    logger.info("Spark relay listening on http://%s:%s", address[0], address[1])
    if server.web_channel:
        logger.info("relay root proxies Spark Web channel %s", server.web_channel)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length > MAX_BODY_BYTES:
        raise RelayError("request body too large", 413)
    raw = handler.rfile.read(length) if length else b""
    try:
        value = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RelayError("request body must be JSON") from exc
    if not isinstance(value, dict):
        raise RelayError("request body must be a JSON object")
    return value


def _b64_payload(value: Any) -> str:
    """Requests carry body_b64; accept a raw string only for compatibility."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    raise RelayError("body_b64 must be a base64 string")


class _RelayHandler(BaseHTTPRequestHandler):
    server_version = "SparkRelay/0.1"
    protocol_version = "HTTP/1.1"

    @property
    def hub(self) -> RelayHub:
        return self.server.hub  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("relay %s - %s", self.address_string(), fmt % args)

    def _error(self, exc: Exception) -> None:
        if isinstance(exc, RelayError):
            _send_json(self, exc.status, {"error": str(exc)})
            return
        logger.exception("relay handler failed")
        _send_json(self, 500, {"error": "relay internal error"})

    def _key(self) -> str:
        return self.headers.get("X-Relay-Key", "")

    def _write_chunk(self, data: bytes) -> None:
        if not data:
            return
        self.wfile.write(f"{len(data):x}\r\n".encode("ascii") + data + b"\r\n")
        self.wfile.flush()

    def _stream_call(self, channel: str, body: dict[str, Any]) -> None:
        timeout = _timeout_value(body.get("timeout"))
        request_id, events = self.hub.start_stream(
            channel,
            self._key(),
            body,
            timeout=timeout,
        )
        try:
            self._stream_response(events, timeout=timeout)
        finally:
            self.hub.drop_stream(channel, request_id)

    def _stream_response(
        self,
        events: "queue.Queue[dict[str, Any]]",
        *,
        timeout: float,
    ) -> None:
        try:
            first = events.get(timeout=timeout)
        except queue.Empty:
            _send_json(self, 504, {"error": "relay stream headers timed out"})
            return
        if not isinstance(first, dict) or first.get("type") == "error":
            message = str((first or {}).get("error") or "upstream stream failed")
            _send_json(self, 502, {"error": message})
            return
        if first.get("type") != "headers":
            _send_json(self, 502, {"error": "relay agent did not send stream headers"})
            return
        status = int(first.get("status") or 200)
        headers = _headers_dict(first.get("headers"))
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            while True:
                event = events.get(timeout=timeout)
                kind = str(event.get("type") or "")
                if kind == "chunk":
                    self._write_chunk(_unb64(event.get("body_b64")))
                elif kind == "end":
                    break
                elif kind == "error":
                    logger.warning("relay stream error: %s", event.get("error"))
                    break
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except queue.Empty:
            # Headers already went out; terminate the chunked body cleanly
            # rather than leaving the client waiting for a read timeout.
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except OSError:
                pass
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("relay stream client disconnected")

    def _web_binding(self) -> tuple[str, str]:
        channel = str(getattr(self.server, "web_channel", "") or "")
        key = str(getattr(self.server, "web_key", "") or "")
        return channel, key

    def _proxy_web_enabled(self, path: str) -> bool:
        channel, _key = self._web_binding()
        return bool(channel) and path != "/health" and not path.startswith("/v1/")

    def _read_raw_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise RelayError("request body too large", 413)
        if length <= 0:
            return b""
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise RelayError("request body truncated", 400)
        return raw

    def _proxy_web(self, method: str) -> None:
        channel, key = self._web_binding()
        if not channel:
            _send_json(self, 404, {"error": "relay Web entry is not configured"})
            return
        path = self.path
        if not path.startswith("/") or path.startswith("//"):
            raise RelayError("path must start with a single /")
        headers = _headers_dict(dict(self.headers.items()))
        body = self._read_raw_body()
        payload = {
            "method": method,
            "path": path,
            "headers": headers,
            "body_b64": base64.b64encode(body).decode("ascii"),
        }
        streaming = path.rstrip("/").endswith("/stream") or (
            "text/event-stream" in (self.headers.get("Accept") or "").lower()
        )
        timeout = 120.0 if streaming else _timeout_value(self.headers.get("X-Relay-Timeout"))
        if streaming:
            request_id, events = self.hub.start_stream(
                channel,
                key,
                payload,
                timeout=timeout,
            )
            try:
                self._stream_response(events, timeout=timeout)
            finally:
                self.hub.drop_stream(channel, request_id)
            return
        response = self.hub.submit(channel, key, payload, timeout=timeout)
        status = int(response.get("status") or 200)
        out_headers = _headers_dict(response.get("headers"))
        out_body = _unb64(response.get("body_b64"))
        self.send_response(status)
        for name, value in out_headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(out_body)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(out_body)
            self.wfile.flush()

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                _send_json(self, 200, {"ok": True})
                return
            if self._proxy_web_enabled(parsed.path):
                self._proxy_web("GET")
                return
            parts = parsed.path.strip("/").split("/")
            if len(parts) != 4 or parts[0] != "v1" or parts[1] != "channels" or parts[3] != "next":
                _send_json(self, 404, {"error": "not found"})
                return
            wait = float((parse_qs(parsed.query).get("wait") or ["25"])[0])
            request = self.hub.next_request(parts[2], self._key(), wait=wait)
            if request is None:
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            _send_json(self, 200, request)
        except Exception as exc:  # noqa: BLE001 - HTTP boundary
            self._error(exc)

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            if self._proxy_web_enabled(parsed.path):
                self._proxy_web("POST")
                return
            parts = parsed.path.strip("/").split("/")
            if len(parts) != 4 or parts[0] != "v1" or parts[1] != "channels":
                _send_json(self, 404, {"error": "not found"})
                return
            channel, action = parts[2], parts[3]
            body = _read_json(self)
            if action == "register":
                self.hub.register(channel, str(body.get("key") or ""))
                _send_json(self, 201, {"ok": True, "channel": channel})
                return
            if action == "responses":
                self.hub.complete(
                    channel,
                    self._key(),
                    str(body.get("request_id") or ""),
                    {
                        "status": int(body.get("status") or 200),
                        "headers": _headers_dict(body.get("headers")),
                        "body_b64": _b64_payload(body.get("body_b64")),
                    },
                )
                _send_json(self, 200, {"ok": True})
                return
            if action == "stream":
                kind = str(body.get("type") or "")
                event: dict[str, Any] = {"type": kind}
                if kind == "headers":
                    event["status"] = int(body.get("status") or 200)
                    event["headers"] = _headers_dict(body.get("headers"))
                elif kind == "chunk":
                    event["body_b64"] = _b64_payload(body.get("body_b64"))
                elif kind == "error":
                    event["error"] = str(body.get("error") or "stream failed")[:300]
                elif kind != "end":
                    raise RelayError("stream event type must be headers/chunk/end/error")
                if not self.hub.stream_event(
                    channel,
                    self._key(),
                    str(body.get("request_id") or ""),
                    event,
                ):
                    _send_json(self, 410, {"error": "stream consumer is gone"})
                    return
                _send_json(self, 200, {"ok": True})
                return
            if action == "call":
                if bool(body.get("stream")):
                    self._stream_call(channel, body)
                    return
                response = self.hub.submit(
                    channel,
                    self._key(),
                    body,
                    timeout=_timeout_value(body.get("timeout")),
                )
                # Relay transport success is 200; the upstream status travels
                # inside the JSON envelope so a 404/500 body is not discarded.
                _send_json(self, 200, {
                    "status": int(response.get("status") or 200),
                    "headers": _headers_dict(response.get("headers")),
                    "body_b64": _b64_payload(response.get("body_b64")),
                })
                return
            _send_json(self, 404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001 - HTTP boundary
            self._error(exc)

    def _web_method(self, method: str) -> None:
        try:
            parsed = urlparse(self.path)
            if self._proxy_web_enabled(parsed.path):
                self._proxy_web(method)
                return
            _send_json(self, 404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001 - HTTP boundary
            self._error(exc)

    def do_PUT(self) -> None:  # noqa: N802
        self._web_method("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._web_method("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._web_method("DELETE")

    def do_HEAD(self) -> None:  # noqa: N802
        self._web_method("HEAD")


class RelayAgent:
    """Poll a relay channel and forward requests to a local Spark Web server."""

    def __init__(
        self,
        relay_url: str,
        channel: str,
        key: str,
        upstream: str,
        *,
        token: str = "",
        poll_wait: int = 25,
        timeout: int = 60,
    ) -> None:
        self.relay_url = relay_url.rstrip("/")
        self.channel = _check_channel(channel)
        self.key = _check_key(key)
        self.upstream = upstream.rstrip("/")
        self.token = token
        self.poll_wait = max(1, min(int(poll_wait), 30))
        self.timeout = max(5, min(int(timeout), MAX_CALL_TIMEOUT))
        self._relay = httpx.Client(timeout=httpx.Timeout(self.poll_wait + 10, connect=10.0))
        self._upstream = httpx.Client(timeout=httpx.Timeout(self.timeout, connect=10.0), follow_redirects=False)
        self._closed = False

    def close(self) -> None:
        self._closed = True
        self._relay.close()
        self._upstream.close()

    def register(self) -> None:
        response = self._relay.post(
            f"{self.relay_url}/v1/channels/{self.channel}/register",
            json={"key": self.key},
        )
        if response.status_code >= 400:
            raise RelayError(f"relay registration failed: {response.text[:200]}", response.status_code)

    def run(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        self.register()
        while not stop.is_set() and not self._closed:
            try:
                response = self._relay.get(
                    f"{self.relay_url}/v1/channels/{self.channel}/next",
                    params={"wait": self.poll_wait},
                    headers={"X-Relay-Key": self.key},
                )
                if response.status_code == 204:
                    continue
                if response.status_code >= 400:
                    raise RelayError(f"relay poll failed: {response.text[:200]}", response.status_code)
                request = response.json()
                if not isinstance(request, dict):
                    raise RelayError("relay returned a non-object request")
                if bool(request.get("stream")):
                    self.forward_stream(request)
                else:
                    result = self.forward(request)
                    self._relay.post(
                        f"{self.relay_url}/v1/channels/{self.channel}/responses",
                        headers={"X-Relay-Key": self.key},
                        json={"request_id": request.get("request_id"), **result},
                    )
            except httpx.HTTPError as exc:
                logger.warning("relay agent transport error: %s", exc)
                stop.wait(1.0)
            except Exception:  # noqa: BLE001 - keep the agent alive
                logger.exception("relay agent loop failed")
                stop.wait(1.0)

    def _stream_post(self, request_id: str, event: dict[str, Any]) -> None:
        response = self._relay.post(
            f"{self.relay_url}/v1/channels/{self.channel}/stream",
            headers={"X-Relay-Key": self.key},
            json={"request_id": request_id, **event},
        )
        if response.status_code >= 400:
            raise RelayError(
                f"relay stream rejected: {response.text[:200]}",
                response.status_code,
            )

    def forward_stream(self, request: dict[str, Any]) -> None:
        """Forward one request while streaming upstream bytes to the relay."""
        request_id = str(request.get("request_id") or "")
        if not request_id:
            raise RelayError("relay stream request is missing request_id")
        method = str(request.get("method") or "GET").upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
            raise RelayError("unsupported HTTP method")
        path = str(request.get("path") or "/")
        if not path.startswith("/") or path.startswith("//"):
            raise RelayError("path must start with a single /")
        headers = _headers_dict(request.get("headers"))
        headers["X-Spark-Relay"] = "1"
        headers["Accept-Encoding"] = "identity"
        if self.token:
            headers["X-Spark-Token"] = self.token
        body = _unb64(request.get("body_b64"))
        try:
            with self._upstream.stream(
                method,
                self.upstream + path,
                headers=headers,
                content=body,
            ) as response:
                self._stream_post(
                    request_id,
                    {
                        "type": "headers",
                        "status": response.status_code,
                        "headers": {
                            name: value
                            for name, value in response.headers.items()
                            if name.lower() not in _HOP_HEADERS
                        },
                    },
                )
                for chunk in response.iter_raw():
                    if chunk:
                        self._stream_post(
                            request_id,
                            {
                                "type": "chunk",
                                "body_b64": base64.b64encode(chunk).decode("ascii"),
                            },
                        )
            self._stream_post(request_id, {"type": "end"})
        except RelayError:
            raise
        except Exception as exc:  # noqa: BLE001 - report upstream failure to client
            try:
                self._stream_post(
                    request_id,
                    {"type": "error", "error": f"upstream stream failed: {exc}"[:300]},
                )
            except RelayError:
                logger.debug("relay stream client already gone")
            return

    def forward(self, request: dict[str, Any]) -> dict[str, Any]:
        method = str(request.get("method") or "GET").upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
            raise RelayError("unsupported HTTP method")
        path = str(request.get("path") or "/")
        if not path.startswith("/") or path.startswith("//"):
            raise RelayError("path must start with a single /")
        headers = _headers_dict(request.get("headers"))
        headers["X-Spark-Relay"] = "1"
        headers["Accept-Encoding"] = "identity"
        if self.token:
            headers["X-Spark-Token"] = self.token
        body = _unb64(request.get("body_b64"))
        response = self._upstream.request(method, self.upstream + path, headers=headers, content=body)
        return {
            "status": response.status_code,
            "headers": {
                name: value
                for name, value in response.headers.items()
                if name.lower() not in _HOP_HEADERS
            },
            "body_b64": base64.b64encode(response.content).decode("ascii"),
        }


def relay_call(
    relay_url: str,
    channel: str,
    key: str,
    *,
    method: str = "GET",
    path: str = "/api/status",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    timeout: int = 60,
) -> dict[str, Any]:
    """Submit one request through the relay and return its decoded response."""
    method = method.upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
        raise RelayError("unsupported HTTP method")
    if not path.startswith("/") or path.startswith("//"):
        raise RelayError("path must start with a single /")
    payload = {
        "method": method,
        "path": path,
        "headers": headers or {},
        "body_b64": base64.b64encode(body).decode("ascii"),
        "timeout": max(5, min(int(timeout), MAX_CALL_TIMEOUT)),
    }
    response = httpx.post(
        f"{relay_url.rstrip('/')}/v1/channels/{_check_channel(channel)}/call",
        headers={"X-Relay-Key": _check_key(key)},
        json=payload,
        timeout=httpx.Timeout(max(10, int(timeout) + 10), connect=10.0),
    )
    if response.status_code >= 400:
        try:
            detail = response.json().get("error") or response.text[:200]
        except ValueError:
            detail = response.text[:200]
        raise RelayError(str(detail), response.status_code)
    data = response.json()
    return {
        "status": int(data.get("status") or 200),
        "headers": data.get("headers") or {},
        "body": _unb64(data.get("body_b64")),
    }


@contextmanager
def relay_stream(
    relay_url: str,
    channel: str,
    key: str,
    *,
    method: str = "GET",
    path: str = "/api/status",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    timeout: int = 60,
) -> Iterator[httpx.Response]:
    """Submit one request and expose the live HTTP response for SSE/chunked reads.

    The connection stays open while the relay agent forwards upstream chunks;
    callers should iterate ``response.iter_raw()`` and close promptly.
    """
    method = method.upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
        raise RelayError("unsupported HTTP method")
    if not path.startswith("/") or path.startswith("//"):
        raise RelayError("path must start with a single /")
    payload = {
        "method": method,
        "path": path,
        "headers": headers or {},
        "body_b64": base64.b64encode(body).decode("ascii"),
        "timeout": max(5, min(int(timeout), MAX_CALL_TIMEOUT)),
        "stream": True,
    }
    with httpx.stream(
        "POST",
        f"{relay_url.rstrip('/')}/v1/channels/{_check_channel(channel)}/call",
        headers={"X-Relay-Key": _check_key(key)},
        json=payload,
        timeout=httpx.Timeout(max(10, int(timeout) + 10), connect=10.0),
    ) as response:
        if response.status_code >= 400:
            detail = response.read().decode("utf-8", errors="replace")[:200]
            raise RelayError(detail, response.status_code)
        yield response
