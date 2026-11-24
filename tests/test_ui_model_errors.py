"""UI coverage for model/configuration failures.

The important product rule: an unconfigured or unreachable model must explain
itself. A user should never see a blank turn, a scripted mock answer presented
as real, or an unlabelled stack trace.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
import urllib.request
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


@contextlib.contextmanager
def _missing_key_server(tmp_path: Path):
    (tmp_path / "README.md").write_text("# model error fixture\n", encoding="utf-8")
    from spark.config import SparkConfig
    from spark.store import SessionStore
    from spark.web.server import build_web_server

    cfg = SparkConfig()
    cfg.provider.name = "openai_compat"
    cfg.provider.base_url = "http://127.0.0.1:9/v1"  # deliberately unreachable
    cfg.provider.model = "unconfigured-model"
    cfg.provider.api_key = None
    cfg.provider.api_key_env = "SPARK_E2E_MISSING_KEY"
    cfg.agent.approval = "full-auto"
    cfg.agent.sandbox_mode = "workspace"
    store = SessionStore(tmp_path / "spark.db")
    port = _free_port()
    server, state = build_web_server(
        workdir=tmp_path, cfg=cfg, store=store, host="127.0.0.1", port=port
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        with contextlib.suppress(OSError):
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        time.sleep(0.1)
    try:
        yield base, state
    finally:
        server.shutdown()
        server.server_close()
        # ``daemon_threads`` lets a slow handler outlive server_close(); give
        # it a moment before closing the DB the handler is still using.
        time.sleep(0.3)
        state.close()
        store.close()


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def test_missing_key_shows_banner_error_card_and_survives_reload(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SPARK_E2E_MISSING_KEY", raising=False)
    with _missing_key_server(tmp_path) as (base, _state), sync_playwright() as playwright:
        status = _get_json(f"{base}/api/status")
        assert status["demo_mode"] is False, "missing key must be a real error, not demo mode"
        issue = status["model_status"]["issue"]
        assert status["model_status"]["ready"] is False
        assert issue["code"] == "missing_api_key"
        assert "API Key" in issue["title"]

        browser = launch(playwright)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.set_default_timeout(6000)
        page.goto(f"{base}/?onboarding=skip", wait_until="domcontentloaded")
        page.wait_for_timeout(600)

        banner = page.get_by_test_id("model-status-banner")
        banner.wait_for(state="visible")
        assert "API Key" in banner.inner_text()

        composer = page.get_by_placeholder("给 Spark 发送任务，Enter 发送 / Shift+Enter 换行（/test 跑测试）")
        composer.fill("hello")
        page.get_by_title("发送").click()

        error = page.get_by_test_id("model-error")
        error.wait_for(state="visible", timeout=8000)
        text = error.inner_text()
        assert "API Key" in text
        assert "未配置" in text
        assert "怎么处理" in text
        assert page.get_by_role("button", name="打开模型设置").count() >= 1
        # No scripted assistant answer should have been produced.
        assert page.locator(".md-body").count() == 0

        # The failure is persisted server-side, not only in the live store.
        status = _get_json(f"{base}/api/status")
        assert status["last_error"]["code"] == "missing_api_key"
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(700)
        page.get_by_test_id("model-error").wait_for(state="visible")
        assert "API Key" in page.get_by_test_id("model-error").inner_text()

        context.close()
        browser.close()
