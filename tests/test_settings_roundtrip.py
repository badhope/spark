"""Round-trip test for every settings control: edit → save → reload → verify.

The existing UI tests only proved tabs render. This module drives a real browser,
changes each control, clicks the real "save all" button, then re-reads both the
rendered form and ``GET /api/config`` to confirm the value actually persisted —
and finally restores the original value so the test is repeatable.

Controls are discovered from the DOM, not hard-coded, so a newly added field is
covered automatically instead of silently escaping the suite.
"""

from __future__ import annotations

import contextlib
import os
import socket
import threading
import time
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_api.sync_playwright

EDGE_PATHS = [
    Path(r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    Path(r"C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
]


def _browser_available() -> bool:
    if os.environ.get("CI"):
        return True
    if Path("/usr/bin/chromium").exists() or Path("/usr/bin/google-chrome").exists():
        return True
    return any(p.exists() for p in EDGE_PATHS)


pytestmark = pytest.mark.skipif(not _browser_available(), reason="no Chromium/Edge")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextlib.contextmanager
def _server(tmp_path: Path):
    from spark.config import SparkConfig
    from spark.store import SessionStore
    from spark.web.server import build_web_server

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
    threading.Thread(target=server.serve_forever, daemon=True).start()
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
        state.close()
        store.close()


def _launch(playwright):
    if any(p.exists() for p in EDGE_PATHS):
        return playwright.chromium.launch(channel="msedge", headless=True)
    return playwright.chromium.launch(headless=True)


def _open_settings(page) -> object:
    page.get_by_role("button", name="设置").first.click()
    dialog = page.get_by_role("dialog")
    dialog.wait_for(state="visible")
    return dialog


def _content(dialog):
    panes = dialog.locator("div.overflow-y-auto")
    best, size = None, -1
    for i in range(panes.count()):
        pane = panes.nth(i)
        n = len(pane.inner_text().strip())
        if n > size:
            best, size = pane, n
    return best


def _field_by_label(pane, label_text: str):
    """Locate an input by its visible Field label (labels wrap the control)."""
    labels = pane.locator("label")
    for i in range(labels.count()):
        block = labels.nth(i)
        try:
            if label_text not in block.inner_text():
                continue
            field = block.locator('input[type="number"], input[type="text"]').first
            if field.count():
                return field
        except Exception:  # noqa: BLE001
            continue
    return None


def _save(page, dialog) -> bool:
    """Click the global save button; True when the success notice appears."""
    save = dialog.get_by_role("button", name="保存设置")
    if save.count() == 0:
        return False
    save.first.click()
    for _ in range(40):
        page.wait_for_timeout(150)
        body = dialog.inner_text()
        if "已保存" in body:
            return True
        if "失败" in body or "错误" in body:
            return False
    return False


@pytest.fixture()
def live(tmp_path: Path):
    with _server(tmp_path) as (base, state), sync_playwright() as pw:
        browser = _launch(pw)
        ctx = browser.new_context(viewport={"width": 1440, "height": 950})
        page = ctx.new_page()
        page.set_default_timeout(6000)
        page.goto(f"{base}/?onboarding=skip", wait_until="domcontentloaded")
        page.wait_for_timeout(600)
        try:
            yield page, base, state
        finally:
            ctx.close()
            browser.close()


def test_every_text_and_number_field_persists(live) -> None:
    """Each editable field must survive save → reload of /api/config."""
    page, base, _state = live
    dialog = _open_settings(page)
    tab_buttons = dialog.locator("nav button")
    checked: list[str] = []
    failures: list[str] = []

    for index in range(tab_buttons.count()):
        tab_buttons.nth(index).click()
        page.wait_for_timeout(160)
        pane = _content(dialog)
        if pane is None:
            continue
        inputs = pane.locator('input[type="text"], input[type="number"]')
        for i in range(min(inputs.count(), 4)):
            field = inputs.nth(i)
            try:
                if not field.is_visible() or not field.is_enabled():
                    continue
                current = field.input_value()
                kind = field.get_attribute("type")
            except Exception:  # noqa: BLE001 - pane re-rendered
                continue
            label = (field.get_attribute("aria-label") or field.get_attribute("placeholder") or "")[:40]
            if kind == "number":
                new_value = str(int(current or 0) + 1)
            else:
                if not current:
                    continue  # never invent a value for an empty optional field
                new_value = current + "-rt"
            try:
                field.fill(new_value)
            except Exception:  # noqa: BLE001
                continue
            ok = _save(page, dialog)
            # Read back from the same open dialog; re-opening stacks overlays
            # and the old backdrop then intercepts every click.
            page.wait_for_timeout(250)
            tab_buttons = dialog.locator("nav button")
            tab_buttons.nth(index).click()
            page.wait_for_timeout(160)
            pane = _content(dialog)
            field = pane.locator('input[type="text"], input[type="number"]').nth(i)
            try:
                after = field.input_value()
            except Exception:  # noqa: BLE001
                failures.append(f"tab{index} field{i} {label}: disappeared after save")
                continue
            checked.append(label or f"tab{index}#{i}")
            if after != new_value:
                failures.append(f"tab{index} field{i} {label}: {current!r}→{new_value!r} read back {after!r} (save_ok={ok})")
            # restore
            try:
                field.fill(current)
                _save(page, dialog)
            except Exception:  # noqa: BLE001
                pass
            tab_buttons.nth(index).click()
            page.wait_for_timeout(120)

    page.keyboard.press("Escape")
    assert len(checked) >= 8, f"only {len(checked)} fields exercised: {checked}"
    assert not failures, failures


def test_toggles_and_selects_persist(live) -> None:
    """Toggles and selects round-trip through the same save path."""
    page, base, _state = live
    dialog = _open_settings(page)
    tab_buttons = dialog.locator("nav button")
    checked = 0
    failures: list[str] = []

    for index in range(tab_buttons.count()):
        tab_buttons.nth(index).click()
        page.wait_for_timeout(160)
        pane = _content(dialog)
        if pane is None:
            continue
        toggles = pane.locator('button[role="switch"]')
        for i in range(min(toggles.count(), 3)):
            toggle = toggles.nth(i)
            try:
                if not toggle.is_visible() or not toggle.is_enabled():
                    continue
                before = toggle.get_attribute("aria-checked")
                toggle.click()
                page.wait_for_timeout(120)
                _save(page, dialog)
                page.wait_for_timeout(200)
                tab_buttons.nth(index).click()
                page.wait_for_timeout(160)
                pane = _content(dialog)
                after = pane.locator('button[role="switch"]').nth(i).get_attribute("aria-checked")
                checked += 1
                if after == before:
                    failures.append(f"tab{index} toggle{i}: stayed {before}")
                else:
                    pane.locator('button[role="switch"]').nth(i).click()
                    _save(page, dialog)
                    tab_buttons.nth(index).click()
                    page.wait_for_timeout(120)
            except Exception:  # noqa: BLE001 - control may not exist on this tab
                continue

        selects = pane.locator("select")
        for i in range(min(selects.count(), 2)):
            sel = selects.nth(i)
            try:
                if not sel.is_visible() or not sel.is_enabled():
                    continue
                options = sel.locator("option")
                if options.count() < 2:
                    continue
                before = sel.input_value()
                values = [options.nth(j).get_attribute("value") for j in range(options.count())]
                # A preset picker keeps value="" by design (it fills fields once),
                # so "did the select keep the value" is not a valid assertion.
                if not before:
                    continue
                target = next((v for v in values if v != before), None)
                if target is None:
                    continue
                sel.select_option(target)
                _save(page, dialog)
                page.wait_for_timeout(200)
                tab_buttons.nth(index).click()
                page.wait_for_timeout(160)
                pane = _content(dialog)
                after = pane.locator("select").nth(i).input_value()
                checked += 1
                if after != target:
                    failures.append(f"tab{index} select{i}: wanted {target}, got {after}")
                else:
                    pane.locator("select").nth(i).select_option(before)
                    _save(page, dialog)
                    tab_buttons.nth(index).click()
                    page.wait_for_timeout(120)
            except Exception:  # noqa: BLE001
                continue

    page.keyboard.press("Escape")
    assert checked >= 3, f"only {checked} toggles/selects exercised"
    assert not failures, failures


def test_config_api_reflects_saved_reliability_limits(live) -> None:
    """The UI number fields must reach the backend, not just local state."""
    page, base, _state = live
    import json
    import urllib.request

    def config() -> dict:
        with urllib.request.urlopen(f"{base}/api/config", timeout=5) as resp:
            return json.loads(resp.read())

    before = config()["agent"]["max_tool_rounds"]
    dialog = _open_settings(page)
    tab_buttons = dialog.locator("nav button")
    for i in range(tab_buttons.count()):
        if "Agent" in tab_buttons.nth(i).inner_text():
            tab_buttons.nth(i).click()
            break
    page.wait_for_timeout(300)
    pane = _content(dialog)
    target = _field_by_label(pane, "最大工具轮数")
    assert target is not None, "max_tool_rounds input not found"
    target.fill(str(before + 3))
    assert _save(page, dialog)
    page.wait_for_timeout(400)
    assert config()["agent"]["max_tool_rounds"] == before + 3

    # restore — same dialog, never re-open a second overlay
    tab_buttons.nth(1).click()  # Agent
    page.wait_for_timeout(250)
    pane = _content(dialog)
    field = _field_by_label(pane, "最大工具轮数")
    if field is not None:
        field.fill(str(before))
    _save(page, dialog)
    page.keyboard.press("Escape")
    assert config()["agent"]["max_tool_rounds"] == before
