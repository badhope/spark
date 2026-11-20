from __future__ import annotations

import base64
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from spark.relay import (
    RelayAgent,
    RelayError,
    build_relay_server,
    relay_call,
)


class _UpstreamHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: ANN002
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/status":
            body = json.dumps(
                {"ok": True, "token": self.headers.get("X-Spark-Token", "")}
            ).encode()
            self.send_response(200)
        elif self.path == "/api/missing":
            body = b'{"error":"missing"}'
            self.send_response(404)
        else:
            body = b'{"error":"not found"}'
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        received = self.rfile.read(length)
        body = json.dumps({"received": base64.b64encode(received).decode()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start(server) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _stop(server) -> None:
    server.shutdown()
    server.server_close()


def test_relay_roundtrip_and_token_injection() -> None:
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
    upstream.token = "secret"
    _start(upstream)
    relay, hub = build_relay_server("127.0.0.1", 0)
    _start(relay)
    channel = secrets.token_urlsafe(18)
    key = secrets.token_urlsafe(24)
    hub.register(channel, key)
    agent = RelayAgent(
        f"http://127.0.0.1:{relay.server_address[1]}",
        channel,
        key,
        f"http://127.0.0.1:{upstream.server_address[1]}",
        token=upstream.token,
        poll_wait=1,
    )
    stop = threading.Event()
    agent_thread = threading.Thread(target=agent.run, args=(stop,), daemon=True)
    try:
        agent_thread.start()
        assert hub.wait_for_agent(channel, timeout=3)
        result = relay_call(
            f"http://127.0.0.1:{relay.server_address[1]}",
            channel,
            key,
            path="/api/status",
        )
        assert result["status"] == 200
        assert json.loads(result["body"])["token"] == "secret"

        missing = relay_call(
            f"http://127.0.0.1:{relay.server_address[1]}",
            channel,
            key,
            path="/api/missing",
        )
        assert missing["status"] == 404
        assert b"missing" in missing["body"]
    finally:
        stop.set()
        agent.close()
        agent_thread.join(timeout=3)
        _stop(relay)
        _stop(upstream)


def test_relay_rejects_bad_key_and_unknown_channel() -> None:
    relay, hub = build_relay_server("127.0.0.1", 0)
    _start(relay)
    channel = secrets.token_urlsafe(18)
    key = secrets.token_urlsafe(24)
    hub.register(channel, key)
    base = f"http://127.0.0.1:{relay.server_address[1]}"
    try:
        with pytest.raises(RelayError) as bad_key:
            relay_call(base, channel, "x" * 16)
        assert bad_key.value.status == 403
        with pytest.raises(RelayError) as missing:
            relay_call(base, "unknown-channel", key)
        assert missing.value.status == 404
    finally:
        _stop(relay)


def test_relay_reports_no_agent() -> None:
    relay, hub = build_relay_server("127.0.0.1", 0)
    _start(relay)
    channel = secrets.token_urlsafe(18)
    key = secrets.token_urlsafe(24)
    hub.register(channel, key)
    base = f"http://127.0.0.1:{relay.server_address[1]}"
    try:
        with pytest.raises(RelayError) as unavailable:
            relay_call(base, channel, key)
        assert unavailable.value.status == 503
    finally:
        _stop(relay)


def test_relay_channel_validation() -> None:
    hub = build_relay_server("127.0.0.1", 0)[1]
    with pytest.raises(RelayError, match="channel id"):
        hub.register("short", "x" * 16)
    with pytest.raises(RelayError, match="relay key"):
        hub.register("valid-channel", "short")
