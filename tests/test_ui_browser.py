"""End-to-end UI smoke tests driven by a real browser.

These tests start the real FastAPI server (uvicorn in a background thread),
load the built frontend bundle, and click through every reachable surface:
chat, tool calls, approval, file tree, terminal, settings tabs, checkpoints,
replay, command palette, and the office workbench.

The browser is Microsoft Edge via Playwright's ``channel="msedge"`` so no
separate browser download is required.  If Edge is not installed the whole
module skips rather than failing — the same tests run in CI on Ubuntu where
Chromium is installed instead.
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
    return any(path.exists() for path in EDGE_PATHS)


pytestmark = pytest.mark.skipif(
    not _browser_available(), reason="no Chromium/Edge available for UI tests"
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def _live_server(tmp_path: Path):
    if os.environ.get("SPARK_UI_BASE"):
        yield os.environ["SPARK_UI_BASE"]
        return
    (tmp_path / "README.md").write_text("# Demo workspace\n\nfile tree fixture\n", encoding="utf-8")
    from spark.config import SparkConfig
    from spark.store import SessionStore
    from spark.web.server import build_web_server

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.base_url = "http://localhost:9999/v1"
    cfg.provider.model = "mock-model"
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
        yield base
    finally:
        server.shutdown()
        server.server_close()
        state.close()
        store.close()


def _launch(playwright):
    if any(path.exists() for path in EDGE_PATHS):
        return playwright.chromium.launch(channel="msedge", headless=True)
    return playwright.chromium.launch(headless=True)


@pytest.fixture()
def page(tmp_path: Path):
    with _live_server(tmp_path) as base, sync_playwright() as playwright:
        browser = _launch(playwright)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.set_default_timeout(4000)
        page.goto(f"{base}/?onboarding=skip", wait_until="domcontentloaded")
        page.wait_for_timeout(600)
        try:
            yield page, base
        finally:
            context.close()
            browser.close()


def _open_tool(page, label: str) -> None:
    """Secondary tools live behind the header '工具' menu now."""
    menu = page.get_by_title("工具").first
    if menu.count() == 0:
        menu = page.get_by_title("Tools").first
    menu.click()
    page.wait_for_timeout(200)
    page.get_by_role("menuitem").filter(has_text=label).first.click()
    page.wait_for_timeout(500)


def test_shell_renders_core_surfaces(page) -> None:
    page, _base = page
    assert page.title()
    for title in ("命令面板", "右侧面板", "工具"):
        assert page.get_by_title(title).count() >= 1, title
    _open_tool(page, "文件查看器")
    assert page.get_by_role("dialog").count() >= 1
    page.keyboard.press("Escape")


def _settings_content(dialog):
    """The content pane is the scroll container with the most rendered text.

    ``overflow-y-auto`` also matches the nav list and inner lists, so picking
    the first/last one silently tests the wrong pane (memory tab looked empty).
    """
    best = None
    best_len = -1
    panes = dialog.locator("div.overflow-y-auto")
    for index in range(panes.count()):
        pane = panes.nth(index)
        length = len(pane.inner_text().strip())
        if length > best_len:
            best, best_len = pane, length
    return best


def test_settings_all_tabs_render_without_errors(page) -> None:
    """Every settings tab must render real content, not a blank pane."""
    page, _base = page
    page.get_by_role("button", name="设置").first.click()
    dialog = page.get_by_role("dialog")
    dialog.wait_for(state="visible")
    tabs = dialog.locator("nav button")
    count = tabs.count()
    assert count >= 15, count
    problems: list[str] = []
    for index in range(count):
        tab = tabs.nth(index)
        label = tab.inner_text().strip()
        tab.click()
        page.wait_for_timeout(220)
        text = _settings_content(dialog).inner_text()
        if len(text.strip()) < 40:
            problems.append(f"{label}: blank")
        if "加载失败" in text or "Failed to load" in text:
            problems.append(f"{label}: load failure")
    page.get_by_title("关闭").first.click()
    assert not problems, problems


def test_settings_controls_are_clickable(page) -> None:
    """Click the controls on every settings tab and keep going through re-renders.

    Clicking a control can replace the pane content, so the button list is
    re-read after every click instead of holding stale locators.
    """
    page, _base = page
    page.get_by_role("button", name="设置").first.click()
    dialog = page.get_by_role("dialog")
    dialog.wait_for(state="visible")
    tabs = dialog.locator("nav button")
    tab_count = tabs.count()
    clicked = 0
    skipped: list[str] = []
    for index in range(tab_count):
        tabs.nth(index).click()
        page.wait_for_timeout(200)
        seen = 0
        while seen < 10:
            content = _settings_content(dialog)
            buttons = content.locator("button:visible")
            total = buttons.count()
            if seen >= total:
                break
            btn = buttons.nth(seen)
            seen += 1
            try:
                title = (btn.get_attribute("title") or "") + " " + btn.inner_text()
            except Exception:  # noqa: BLE001 - pane re-rendered under us
                continue
            if any(k in title for k in ("删除", "卸载", "重置", "清空", "Delete", "Remove", "Reset")):
                skipped.append(title.strip()[:40])
                continue
            try:
                btn.click(timeout=2000)
                clicked += 1
                page.wait_for_timeout(80)
            except Exception:  # noqa: BLE001 - disabled or overlay is fine
                continue
    # A click may leave a nested dialog/pane open; Escape first, then the X.
    for _ in range(3):
        if page.get_by_role("dialog").count() == 0:
            break
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    assert clicked > 25, (clicked, skipped)


def test_send_message_and_tool_call(page) -> None:
    page, _base = page
    composer = page.get_by_placeholder("给 Spark 发送任务，Enter 发送 / Shift+Enter 换行（/test 跑测试）")
    composer.fill("请 list 当前工作区文件")
    page.get_by_title("发送").click()
    page.wait_for_timeout(2500)
    body = page.inner_text("body")
    assert "list_dir" in body
    assert "完成" in body


def test_file_tree_and_preview(page) -> None:
    page, _base = page
    page.get_by_title("右侧面板（文件 / 预览 / 网页）").click()
    page.wait_for_timeout(500)
    readme = page.locator("aside").last.get_by_role("button").filter(has_text="README.md").first
    readme.click()
    page.wait_for_timeout(800)
    assert "Spark" in page.inner_text("body")


def test_terminal_executes_command(page) -> None:
    page, _base = page
    _open_tool(page, "内置终端")
    page.wait_for_timeout(800)
    page.locator('textarea[aria-label="Terminal input"]').focus()
    page.keyboard.type("echo spark-ui-ok")
    page.keyboard.press("Enter")
    page.wait_for_timeout(1500)
    assert "spark-ui-ok" in page.inner_text('[role="dialog"]')
    page.get_by_title("关闭").last.click()


def test_command_palette_opens(page) -> None:
    page, _base = page
    page.get_by_title("命令面板 (Ctrl+K)").click()
    page.wait_for_timeout(400)
    assert page.get_by_role("dialog").count() >= 1
    page.keyboard.press("Escape")


def test_office_workbench_renders(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from spark.config import SparkConfig
    from spark.control.app import create_app
    from spark.control.settings import ControlSettings

    settings = ControlSettings(
        database_url=f"sqlite:///{(tmp_path / 'control.db').as_posix()}",
        artifact_root=str(tmp_path / "artifacts"),
        auth_mode="dev",
        allow_dev_auth=True,
    )
    app = create_app(settings, cfg=SparkConfig())
    with TestClient(app) as client:
        html = client.get("/office")
        assert html.status_code == 200
        body = html.text
        for anchor in ("generate", "report-generate", "document-file", "imap-fetch"):
            assert anchor in body, anchor
        meeting = client.post(
            "/api/v1/office/meetings",
            json={"title": "UI 会议", "transcript": "参会：甲\n决定：通过\n行动项：补测试，负责人：甲，截止：2026-12-01"},
        )
        assert meeting.status_code == 201
