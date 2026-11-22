"""UI coverage for the curated local-model download cards."""

from __future__ import annotations

import pytest

from tests._ui_browser import SKIP_REASON, browser_available, launch
from tests.test_ui_model_errors import _get_json, _missing_key_server

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_api.sync_playwright

pytestmark = pytest.mark.skipif(not browser_available(), reason=SKIP_REASON)


def test_local_model_catalog_and_settings_cards(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SPARK_MODEL_DIR", str(tmp_path / "models"))
    with _missing_key_server(tmp_path) as (base, _state):
        catalog = _get_json(f"{base}/api/local-models")
        assert [m["name"] for m in catalog["models"]][:2] == ["MiniCPM5-1B", "MiniCPM5-2B"]
        assert catalog["models"][0]["recommended"] is True
        assert catalog["models"][0]["context"] == 131_072

        with sync_playwright() as playwright:
            browser = launch(playwright)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page.set_default_timeout(8000)
            page.goto(f"{base}/?onboarding=skip&panel=settings&tab=model", wait_until="domcontentloaded")
            section = page.get_by_test_id("local-models")
            section.wait_for(state="visible")
            page.get_by_test_id("local-model-minicpm5-1b").wait_for(state="visible")
            text = section.inner_text()
            assert "MiniCPM5-1B" in text
            assert "MiniCPM5-2B" in text
            assert "Qwen3.5-2B" in text
            assert "Qwen3.5-4B" in text
            assert "BotPsy-0.8B" in text
            toggle = page.get_by_test_id("demo-mode-toggle")
            toggle.wait_for(state="visible")
            assert "进入 Mock 展示模式" in toggle.inner_text()
            assert "128K" in text
            assert "下载" in text
            context.close()
            browser.close()


def test_one_click_activation_switches_provider(tmp_path, monkeypatch) -> None:
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    target = model_dir / "MiniCPM5-1B-Q4_K_M.gguf"
    with target.open("wb") as fh:
        fh.write(b"GGUF")
        fh.truncate(688_065_920)
    monkeypatch.setenv("SPARK_MODEL_DIR", str(model_dir))

    with _missing_key_server(tmp_path) as (base, _state):
        with sync_playwright() as playwright:
            browser = launch(playwright)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            page.set_default_timeout(8000)
            page.goto(f"{base}/?onboarding=skip&panel=settings&tab=model", wait_until="domcontentloaded")
            page.get_by_test_id("local-model-minicpm5-1b").wait_for(state="visible")
            page.get_by_role("button", name="使用此模型").click()
            page.wait_for_timeout(1500)
            status = _get_json(f"{base}/api/status")
            assert status["provider"] == "llama_cpp"
            assert status["model"].endswith("MiniCPM5-1B-Q4_K_M.gguf")
            assert status["model_status"]["ready"] is True
            context.close()
            browser.close()


def test_demo_toggle_one_click_and_restores_real_error(tmp_path, monkeypatch) -> None:
    with _missing_key_server(tmp_path) as (base, _state):
        with sync_playwright() as playwright:
            browser = launch(playwright)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page.set_default_timeout(8000)
            page.goto(f"{base}/?onboarding=skip&panel=settings&tab=model", wait_until="domcontentloaded")
            toggle = page.get_by_test_id("demo-mode-toggle")
            toggle.wait_for(state="visible")
            assert "进入 Mock 展示模式" in toggle.inner_text()
            toggle.click()
            page.wait_for_timeout(1200)
            status = _get_json(f"{base}/api/status")
            assert status["provider"] == "mock"
            assert status["demo_mode"] is True
            assert "退出演示" in toggle.inner_text()

            toggle.click()
            page.wait_for_timeout(1200)
            status = _get_json(f"{base}/api/status")
            assert status["provider"] == "openai_compat"
            assert status["demo_mode"] is False
            assert status["model_status"]["ready"] is False
            assert status["model_status"]["issue"]["code"] == "missing_api_key"
            context.close()
            browser.close()
