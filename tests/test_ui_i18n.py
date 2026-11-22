"""English UI must not leak Chinese copy.

The build gate (web/scripts/check-i18n.mjs) catches static sources. This
browser test catches runtime data too: provider preset labels, local-model
descriptions, session titles and any component that forgot a t() branch.
"""

from __future__ import annotations

import re

import pytest

from tests._ui_browser import SKIP_REASON, browser_available, launch
from tests.test_ui_model_errors import _missing_key_server

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_api.sync_playwright

pytestmark = pytest.mark.skipif(not browser_available(), reason=SKIP_REASON)

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def _assert_no_cjk(text: str, where: str) -> None:
    found = sorted(set(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]+", text)))
    assert not found, f"{where} leaked Chinese UI: {found[:20]}"


def test_english_main_and_model_settings_have_no_chinese(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SPARK_MODEL_DIR", str(tmp_path / "models"))
    with _missing_key_server(tmp_path) as (base, _state):
        with sync_playwright() as playwright:
            browser = launch(playwright)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            page.set_default_timeout(8000)
            page.add_init_script("localStorage.setItem('spark-lang', 'en')")

            page.goto(f"{base}/?onboarding=skip", wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            _assert_no_cjk(page.locator("body").inner_text(), "English main shell")

            page.goto(
                f"{base}/?onboarding=skip&panel=settings&tab=model",
                wait_until="domcontentloaded",
            )
            page.wait_for_timeout(1200)
            _assert_no_cjk(page.locator("body").inner_text(), "English model settings")


            context.close()
            browser.close()
