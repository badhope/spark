"""End-to-end HTTP server tests — starts the real server on an ephemeral port
and exercises the full request → response chain over actual HTTP.

Covers:

* Server startup and status/config/models GET endpoints
* POST /api/settings validation (bad payloads)
* POST /api/chat (non-streaming) with mock provider
* POST /api/chat/stream — SSE response parsing
* Security: non-loopback token guard
* Session CRUD
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from spark.config import SparkConfig
from spark.store import SessionStore
from spark.web.server import build_web_server

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Get a free localhost port."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    """Start a real HTTP server on an ephemeral port, tear down after test."""
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.base_url = "http://localhost:9999/v1"
    cfg.provider.model = "mock-model"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "spark.db")
    port = _free_port()
    server, state = build_web_server(
        workdir=tmp_path, cfg=cfg, store=store, host="127.0.0.1", port=port
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="test-http")
    thread.start()
    time.sleep(0.3)
    try:
        yield server, state, port
    finally:
        server.shutdown()
        server.server_close()
        store.close()


@pytest.fixture
def server_no_model(tmp_path):
    """Server with no model configured — for validation testing."""
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.base_url = ""
    cfg.provider.model = ""
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "spark2.db")
    port = _free_port()
    server, state = build_web_server(
        workdir=tmp_path, cfg=cfg, store=store, host="127.0.0.1", port=port
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="test-http-nomodel")
    thread.start()
    time.sleep(0.3)
    try:
        yield server, state, port
    finally:
        server.shutdown()
        server.server_close()
        store.close()


def _get(port: int, path: str) -> tuple[int, dict]:
    """Perform a GET request and return (status_code, parsed_json)."""
    url = f"http://127.0.0.1:{port}{path}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


def _post(port: int, path: str, body: dict) -> tuple[int, dict]:
    """Perform a POST with JSON body and return (status_code, parsed_json)."""
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body_text)
        except json.JSONDecodeError:
            return exc.code, {"error": body_text}


def _post_sse(port: int, path: str, body: dict, timeout: float = 30.0) -> list[dict]:
    """Perform a POST to an SSE endpoint and parse all payloads."""
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=timeout)
    raw = resp.read().decode("utf-8", "replace")
    payloads = []
    for block in raw.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line[6:]))
    return payloads


# ---------------------------------------------------------------------------
# GET endpoints
# ---------------------------------------------------------------------------


class TestGetEndpoints:
    def test_status_returns_session_info(self, server):
        _, _, port = server
        status, body = _get(port, "/api/status")
        assert status == 200
        assert "session_id" in body
        assert "workdir" in body

    def test_config_roundtrip(self, server):
        _, _, port = server
        status, body = _get(port, "/api/config")
        assert status == 200
        assert "agent" in body
        assert "provider" in body

    def test_models_has_default_profile(self, server):
        _, state, port = server
        status, body = _get(port, "/api/models")
        assert status == 200
        # With a configured provider, ensure_default_profile creates a profile
        assert isinstance(body["profiles"], list)

    def test_agents_md_default(self, server):
        _, state, port = server
        status, body = _get(port, "/api/agents_md")
        assert status == 200
        assert body["filename"] == state.cfg.context.agents_md
        assert body["content"] == ""

    def test_history_empty_at_start(self, server):
        _, _, port = server
        status, body = _get(port, "/api/history")
        assert status == 200
        assert body["messages"] == []

    def test_sessions_list_current(self, server):
        _, state, port = server
        status, body = _get(port, "/api/sessions")
        assert status == 200
        assert body["current"] == state.session_id
        assert len(body["sessions"]) >= 1

    def test_static_serves_html(self, server):
        _, _, port = server
        url = f"http://127.0.0.1:{port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            html = resp.read().decode("utf-8")
            # The built-in INDEX_HTML or vite dist index.html
            assert "spark" in html.lower() or "<html" in html.lower()


# ---------------------------------------------------------------------------
# POST validation
# ---------------------------------------------------------------------------


class TestPostValidation:
    def test_settings_rejects_bad_provider(self, server):
        _, _, port = server
        status, body = _post(port, "/api/settings", {
            "provider": {"name": "invalid_provider", "base_url": "http://x/v1", "model": "m"}
        })
        assert status == 400

    def test_settings_rejects_missing_model(self, server_no_model):
        """With no model in state, empty model should fail validation."""
        _, _, port = server_no_model
        status, body = _post(port, "/api/settings", {
            "provider": {"name": "mock", "base_url": "http://x/v1", "model": ""}
        })
        assert status == 400

    def test_settings_accepts_valid_mock(self, server):
        _, _, port = server
        status, body = _post(port, "/api/settings", {
            "provider": {"name": "mock", "base_url": "http://localhost:9999/v1", "model": "mock-model"}
        })
        assert status == 200
        assert body["ok"] is True

    def test_unknown_endpoint_returns_404(self, server):
        _, _, port = server
        status, body = _post(port, "/api/nonexistent", {})
        assert status == 404

    def test_invalid_json_returns_400(self, server):
        _, _, port = server
        url = f"http://127.0.0.1:{port}/api/settings"
        req = urllib.request.Request(
            url,
            data=b"not json{{{",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        assert status == 400

    def test_chat_missing_prompt_returns_400(self, server):
        _, _, port = server
        status, body = _post(port, "/api/chat", {})
        assert status == 400


# ---------------------------------------------------------------------------
# Chat (non-streaming)
# ---------------------------------------------------------------------------


class TestChatEndpoint:
    def test_chat_returns_events(self, server):
        _, _, port = server
        status, body = _post(port, "/api/chat", {"prompt": "hello"})
        assert status == 200
        assert "events" in body
        assert len(body["events"]) > 0


# ---------------------------------------------------------------------------
# SSE streaming
# ---------------------------------------------------------------------------


class TestSSEStream:
    def test_stream_returns_text_delta(self, server):
        _, _, port = server
        payloads = _post_sse(port, "/api/chat/stream", {"prompt": "say hi"})
        assert len(payloads) > 0
        # Must have at least one text delta
        text_deltas = [p for p in payloads if p.get("type") == "text_delta"]
        assert len(text_deltas) > 0, f"No text_deltas in {payloads}"

    def test_stream_ends_with_done(self, server):
        _, _, port = server
        payloads = _post_sse(port, "/api/chat/stream", {"prompt": "hi"})
        types = [p.get("type") for p in payloads]
        # Last payload should be 'done' (MockProvider completes immediately)
        assert types[-1] == "done"

    def test_stream_empty_prompt_with_no_images_returns_400(self, server):
        _, _, port = server
        status, body = _post(port, "/api/chat/stream", {"prompt": ""})
        assert status == 400


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class TestSecurity:
    def test_loopback_access_allowed(self, server):
        _, _, port = server
        status, body = _get(port, "/api/status")
        # Loopback 127.0.0.1 should be allowed without token
        assert status == 200

    def test_x_content_type_options_header(self, server):
        _, _, port = server
        url = f"http://127.0.0.1:{port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_x_frame_options_header(self, server):
        _, _, port = server
        url = f"http://127.0.0.1:{port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            assert resp.headers.get("X-Frame-Options") == "DENY"


# ---------------------------------------------------------------------------
# Sessions CRUD
# ---------------------------------------------------------------------------


class TestSessionsCRUD:
    def test_create_new_session(self, server):
        _, _, port = server
        status, body = _post(port, "/api/sessions/new", {})
        assert status == 200
        assert body["ok"] is True

    def test_switch_to_current_session(self, server):
        _, state, port = server
        status, body = _post(port, "/api/sessions/switch", {"session_id": state.session_id})
        assert status == 200
        assert body["ok"] is True

    def test_switch_to_unknown_session_returns_404(self, server):
        _, _, port = server
        status, body = _post(port, "/api/sessions/switch", {"session_id": "invalid-id"})
        assert status == 404
