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
        assert [m["name"] for m in catalog["models"]][:2] == ["MiniCPM5-1B", "BotPsy-0.8B"]
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
            assert "BotPsy-0.8B" in text
            assert "128K" in text
            assert "下载" in text
            context.close()
            browser.close()
