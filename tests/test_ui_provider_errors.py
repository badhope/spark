"""End-to-end provider failures through Spark's real HTTP + SSE path.

A fake OpenAI-compatible server returns 401/404/500 so the full stack is
exercised: LiteLLM raises, Spark classifies, SSE carries the structured error,
and the browser renders an actionable card. No mocking of Spark internals.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests._ui_browser import SKIP_REASON, browser_available, launch

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_api.sync_playwright

pytestmark = pytest.mark.skipif(not browser_available(), reason=SKIP_REASON)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _ProviderHandler(BaseHTTPRequestHandler):
    status = 404
    body: dict = {}
    requests: list[dict] = []

    def do_GET(self) -> None:  # noqa: N802 - stdlib protocol
        if self.path.endswith("/models"):
            self._reply(200, {"object": "list", "data": [{"id": "gone-model"}]})
        else:
            self._reply(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802 - stdlib protocol
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
        type(self).requests.append(payload)
        self._reply(type(self).status, type(self).body)

    def _reply(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args) -> None:
        return


@contextlib.contextmanager
def _fake_provider(status: int, message: str, error_type: str):
    handler = type(
        "_ScopedProviderHandler",
        (_ProviderHandler,),
        {
            "status": status,
            "requests": [],
            "body": {"error": {"message": message, "type": error_type, "code": error_type}},
        },
    )
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/v1", handler
    finally:
        server.shutdown()
        server.server_close()


@contextlib.contextmanager
def _spark_server(tmp_path: Path, base_url: str):
    (tmp_path / "README.md").write_text("# provider error fixture\n", encoding="utf-8")
    from spark.config import SparkConfig
    from spark.store import SessionStore
    from spark.web.server import build_web_server

    cfg = SparkConfig()
    cfg.provider.name = "openai_compat"
    cfg.provider.base_url = base_url
    cfg.provider.model = "gone-model"
    cfg.provider.api_key = "sk-e2e-test-key"
    cfg.provider.api_key_env = "SPARK_E2E_PROVIDER_KEY"
    cfg.agent.approval = "full-auto"
    cfg.agent.sandbox_mode = "workspace"
    store = SessionStore(tmp_path / "spark.db")
    port = _free_port()
    server, state = build_web_server(
        workdir=tmp_path, cfg=cfg, store=store, host="127.0.0.1", port=port
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    spark_base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        with contextlib.suppress(OSError):
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        time.sleep(0.1)
    try:
        yield spark_base, state
    finally:
        server.shutdown()
        server.server_close()
        state.close()
        store.close()


@pytest.mark.parametrize(
    ("status", "message", "expected_title", "expected_hint"),
    [
        (401, "Invalid API key provided", "模型鉴权失败", "API Key"),
        (404, "model 'gone-model' does not exist", "模型不存在或无权访问", "gone-model"),
        (500, "internal upstream failure", "模型服务内部错误", "稍后重试"),
    ],
)
def test_http_provider_failure_renders_actionable_card(
    tmp_path, status: int, message: str, expected_title: str, expected_hint: str
) -> None:
    with _fake_provider(status, message, f"e2e_{status}") as (base_url, fake), _spark_server(
        tmp_path, base_url
    ) as (spark, _state), sync_playwright() as playwright:
        browser = launch(playwright)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.set_default_timeout(6000)
        page.goto(f"{spark}/?onboarding=skip", wait_until="domcontentloaded")
        page.wait_for_timeout(500)

        page.get_by_placeholder(
            "给 Spark 发送任务，Enter 发送 / Shift+Enter 换行（/test 跑测试）"
        ).fill("hello")
        page.get_by_title("发送").click()

        error = page.get_by_test_id("model-error")
        error.wait_for(state="visible", timeout=20000)
        text = error.inner_text()
        assert expected_title in text, text
        assert expected_hint in text, text
        assert f"HTTP {status}" in text, text
        assert "打开模型设置" in text, text
        if status == 500:
            assert "重试这次请求" in text, text
        else:
            assert "重试这次请求" not in text, text
        # A real request reached the fake endpoint; Spark did not silently mock.
        assert fake.requests, "no completion request reached the provider"
        assert fake.requests[-1].get("model") == "gone-model"

        context.close()
        browser.close()


@contextlib.contextmanager
def _dead_endpoint():
    port = _free_port()  # reserved, then closed: connection refused
    yield f"http://127.0.0.1:{port}/v1"


def test_connection_refused_explains_local_service(tmp_path) -> None:
    with _dead_endpoint() as base_url, _spark_server(tmp_path, base_url) as (
        spark,
        _state,
    ), sync_playwright() as playwright:
        browser = launch(playwright)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.set_default_timeout(6000)
        page.goto(f"{spark}/?onboarding=skip", wait_until="domcontentloaded")
        page.wait_for_timeout(500)

        page.get_by_placeholder(
            "给 Spark 发送任务，Enter 发送 / Shift+Enter 换行（/test 跑测试）"
        ).fill("hello")
        page.get_by_title("发送").click()

        error = page.get_by_test_id("model-error")
        # Windows 的连接拒绝会走 LiteLLM 重试/退避，12s 不够；Linux/macOS 仍然秒回。
        error.wait_for(state="visible", timeout=45000)
        text = error.inner_text()
        assert "连接不上模型服务" in text, text
        assert base_url.split("://", 1)[1] in text, text
        assert "本地" in text or "Base URL" in text, text

        context.close()
        browser.close()
