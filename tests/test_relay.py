from __future__ import annotations

import base64
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from spark.relay import (
    RelayAgent,
    RelayError,
    build_relay_server,
    relay_call,
    relay_stream,
)


class _UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: ANN002
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            body = b"<!doctype html><html><title>Spark</title></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in {"/assets/app.js", "/sw.js"}:
            body = b"self.addEventListener('push', () => {});"
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for index in range(3):
                payload = f"data: {index}\n\n".encode()
                self.wfile.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
                self.wfile.flush()
                if index < 2:
                    time.sleep(0.4)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            self.close_connection = True
            return
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


def test_relay_streams_sse_before_upstream_finishes() -> None:
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
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
        poll_wait=1,
    )
    stop = threading.Event()
    agent_thread = threading.Thread(target=agent.run, args=(stop,), daemon=True)
    try:
        agent_thread.start()
        assert hub.wait_for_agent(channel, timeout=3)
        started = time.monotonic()
        first_at = 0.0
        chunks: list[bytes] = []
        with relay_stream(
            f"http://127.0.0.1:{relay.server_address[1]}",
            channel,
            key,
            path="/api/stream",
            timeout=10,
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            for raw in response.iter_raw():
                if raw:
                    if not chunks:
                        first_at = time.monotonic()
                    chunks.append(raw)
        assert b"".join(chunks) == b"data: 0\n\ndata: 1\n\ndata: 2\n\n"
        assert first_at > 0
        assert first_at - started < 0.5
    finally:
        stop.set()
        agent.close()
        agent_thread.join(timeout=3)
        _stop(relay)
        _stop(upstream)


def test_relay_root_proxies_web_static_api_and_sse() -> None:
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
    upstream.token = "secret"
    _start(upstream)
    channel = secrets.token_urlsafe(18)
    key = secrets.token_urlsafe(24)
    relay, hub = build_relay_server(
        "127.0.0.1",
        0,
        web_channel=channel,
        web_key=key,
    )
    _start(relay)
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
    base = f"http://127.0.0.1:{relay.server_address[1]}"
    try:
        agent_thread.start()
        assert hub.wait_for_agent(channel, timeout=3)
        with httpx.Client(timeout=10.0) as client:
            html = client.get(base + "/")
            assert html.status_code == 200
            assert "Spark" in html.text
            assert html.headers["content-type"].startswith("text/html")

            asset = client.get(base + "/assets/app.js")
            assert asset.status_code == 200
            assert "addEventListener" in asset.text

            sw = client.get(base + "/sw.js")
            assert sw.status_code == 200
            assert sw.headers["content-type"].startswith("application/javascript")

            status = client.get(base + "/api/status")
            assert status.status_code == 200
            assert json.loads(status.text)["token"] == "secret"

            started = time.monotonic()
            first_at = 0.0
            chunks: list[bytes] = []
            with client.stream("GET", base + "/api/stream") as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                for raw in response.iter_raw():
                    if raw:
                        if not chunks:
                            first_at = time.monotonic()
                        chunks.append(raw)
            assert b"".join(chunks) == b"data: 0\n\ndata: 1\n\ndata: 2\n\n"
            assert first_at > 0
            assert first_at - started < 0.5
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
