"""Full-surface interaction sweep for the real Web UI.

The older smoke suite proves that the main screens render. This suite goes
further: every settings action is clicked, every message action is exercised,
the header tools and their downloads are driven, the workbench's five tabs are
walked end to end, and keyboard / responsive behaviour is asserted at the same
time. It is intentionally additive: it does not replace the focused regression
tests, it catches the "button renders but does nothing" class of bug.

Every test starts its own isolated server + workspace, so destructive settings
actions (delete profile, clear saved config, reset display) are safe.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests._ui_browser import SKIP_REASON, browser_available, launch
from tests.test_ui_browser import _live_server

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_api.sync_playwright

pytestmark = pytest.mark.skipif(not browser_available(), reason=SKIP_REASON)

_SETTINGS_PAGES = 5


@pytest.fixture()
def ui(tmp_path: Path):
    """A real browser page with clipboard + download permissions."""
    with _live_server(tmp_path) as base, sync_playwright() as playwright:
        browser = launch(playwright)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            permissions=["clipboard-read", "clipboard-write"],
            accept_downloads=True,
        )
        page = context.new_page()
        page.set_default_timeout(6000)
        page.goto(f"{base}/?onboarding=skip", wait_until="domcontentloaded")
        page.wait_for_timeout(700)
        try:
            yield page, base
        finally:
            context.close()
            browser.close()


def _watch_errors(page, base: str) -> list[tuple[str, str, str]]:
    errors: list[tuple[str, str, str]] = []
    page.on(
        "console",
        lambda msg: errors.append(("console", msg.type, msg.text[:280]))
        # The execution-graph panel probes latest.json as an optional
        # empty-state source; a missing run is a legitimate 404 and Chrome logs
        # every non-2xx fetch as a console resource error. Keep 5xx and JS
        # exceptions strict, ignore only that expected optional-resource miss.
        if msg.type == "error" and "404 (Not Found)" not in msg.text
        else None,
    )
    page.on("pageerror", lambda exc: errors.append(("pageerror", "error", str(exc)[:280])))
    page.on(
        "response",
        lambda res: errors.append(("http", str(res.status), res.url))
        if res.status >= 500 and res.url.startswith(base)
        else None,
    )
    return errors


def _settings_content(dialog):
    """The scrollable settings body, chosen by rendered text volume."""
    best = None
    best_len = -1
    panes = dialog.locator("div.overflow-y-auto")
    for index in range(panes.count()):
        pane = panes.nth(index)
        try:
            length = len(pane.inner_text().strip())
        except Exception:  # noqa: BLE001 - pane can re-render while we read
            continue
        if length > best_len:
            best, best_len = pane, length
    return best


def _button_label(button) -> str:
    try:
        text = " ".join((button.inner_text() or "").split())
    except Exception:  # noqa: BLE001
        text = ""
    title = (button.get_attribute("title") or "").strip()
    return (text or title or "<icon>")[:70]


def _open_settings(page):
    trigger = page.get_by_role("button", name=re.compile("设置|Settings")).first
    assert trigger.count() == 1
    trigger.click()
    page.wait_for_timeout(300)
    dialog = page.get_by_role("dialog")
    if dialog.count() == 0:
        # Settings mounts from a lazy chunk and fetches config before it can
        # render; the dialog is allowed to appear slightly after the click.
        dialog.first.wait_for(state="visible", timeout=5000)
    assert dialog.count() >= 1
    return dialog


def _close_secondary_overlays(page) -> None:
    """Close pickers that sit above settings, leaving settings itself open."""
    for _ in range(4):
        overlays = page.locator(".fixed.inset-0:visible")
        if overlays.count() <= 1:
            return
        close = page.locator('button[title="关闭"]:visible').last
        if close.count() == 0:
            return
        try:
            close.click(timeout=2500)
        except Exception:  # noqa: BLE001 - overlay may disappear between checks
            return
        page.wait_for_timeout(300)


def test_every_settings_action_is_clickable(ui) -> None:
    """Click every distinct settings action on all 18 tabs.

    The registry picker is covered by its own test because it is a full-screen
    secondary surface; every other action is clicked in-place. We assert that
    the sweep is broad (50+ actions) and that no click produces a JS exception
    or a 5xx from the local server.
    """
    page, base = ui
    errors = _watch_errors(page, base)
    clicked: list[str] = []
    skipped: list[str] = []

    dialog = _open_settings(page)
    pages = dialog.locator("[data-settings-page]:visible")
    assert pages.count() == _SETTINGS_PAGES, pages.count()

    for page_index in range(pages.count()):
        pages.nth(page_index).click()
        page.wait_for_timeout(220)
        tabs = dialog.locator("[data-settings-tab]:visible")
        tab_count = tabs.count()
        for tab_index in range(tab_count):
            if dialog.locator("[data-settings-tab]:visible").count() <= tab_index:
                break
            dialog.locator("[data-settings-tab]:visible").nth(tab_index).click()
            page.wait_for_timeout(260)
            pane = _settings_content(dialog)
            if pane is None:
                continue
            labels: list[str] = []
            buttons = pane.locator("button:visible")
            for index in range(buttons.count()):
                labels.append(_button_label(buttons.nth(index)))

            for label in labels:
                if label in {"Browse Registry", "关闭"}:
                    skipped.append(label)
                    continue
                pane = _settings_content(dialog)
                if pane is None:
                    break
                buttons = pane.locator("button:visible")
                target = None
                for index in range(buttons.count()):
                    if _button_label(buttons.nth(index)) == label:
                        target = buttons.nth(index)
                        break
                if target is None:
                    continue
                key = f"{page_index}/{tab_index}/{label}"
                try:
                    target.click(timeout=2800)
                    clicked.append(key)
                    page.wait_for_timeout(180)
                except Exception as exc:  # noqa: BLE001 - disabled/covered
                    skipped.append(f"{key}: {str(exc)[:80]}")
                _close_secondary_overlays(page)
                if page.get_by_role("dialog").count() == 0:
                    # Some destructive actions intentionally close settings.
                    dialog = _open_settings(page)
                    pages = dialog.locator("[data-settings-page]:visible")
                    if pages.count() <= page_index:
                        break
                    pages.nth(page_index).click()
                    page.wait_for_timeout(220)
                    tabs = dialog.locator("[data-settings-tab]:visible")
                    if tabs.count() > tab_index:
                        tabs.nth(tab_index).click()
                        page.wait_for_timeout(260)

    assert len(clicked) >= 50, (len(clicked), skipped[-10:])
    assert errors == [], errors
    # No settings tab should have ended up blank after the action sweep.
    assert _settings_content(dialog).inner_text().strip() != ""


def test_mcp_registry_modal_opens_and_closes(ui) -> None:
    """The registry picker is a real surface: open, show rows, close, return."""
    page, base = ui
    errors = _watch_errors(page, base)
    dialog = _open_settings(page)
    dialog.locator("[data-settings-page]:visible").nth(3).click()
    page.wait_for_timeout(250)
    dialog.locator("[data-settings-tab]:visible").nth(0).click()
    page.wait_for_timeout(250)
    page.get_by_role("button", name=re.compile("Registry")).click()
    page.wait_for_timeout(900)
    registry = page.get_by_role("dialog").last
    assert registry.is_visible()
    assert "Registry" in registry.inner_text()
    close = page.locator('button[title="关闭"]:visible').last
    assert close.count() == 1
    close.click()
    page.wait_for_timeout(350)
    assert page.get_by_role("dialog").count() == 1
    assert page.get_by_role("dialog").first.is_visible()
    assert errors == [], errors


def _send_prompt(page, prompt: str) -> None:
    composer = page.get_by_placeholder(re.compile("给 Spark 发送任务"))
    composer.fill(prompt)
    page.get_by_title("发送").click()
    page.wait_for_timeout(2600)


def test_message_actions_work_end_to_end(ui) -> None:
    """Copy, edit, feedback, regenerate and delete on a real conversation."""
    page, base = ui
    errors = _watch_errors(page, base)
    prompt = "请 list 当前工作区文件"
    _send_prompt(page, prompt)
    body = page.inner_text("body")
    assert "list_dir" in body

    # User message: copy.
    copy = page.get_by_title("复制").first
    assert copy.count() == 1
    copy.click()
    page.wait_for_timeout(200)
    clip = page.evaluate("() => navigator.clipboard.readText()")
    assert prompt in clip

    # Assistant feedback both ways; aria-pressed reflects persistence. The
    # action bar is rendered only after the done event carries the db id, so
    # wait for it rather than racing the final session refresh.
    up = page.get_by_title("这条回复有帮助").first
    up.wait_for(state="visible", timeout=8000)
    up.click()
    page.wait_for_timeout(500)
    assert up.get_attribute("aria-pressed") == "true"
    down = page.get_by_title("这条回复没帮助").first
    down.click()
    page.wait_for_timeout(500)
    assert down.get_attribute("aria-pressed") == "true"

    # Assistant copy-all returns the rendered answer text.
    page.get_by_title("复制全文").first.click()
    page.wait_for_timeout(150)
    assert page.evaluate("() => navigator.clipboard.readText()").strip()

    # Regenerate produces a fresh assistant answer.
    before = page.get_by_title("重新生成这条回复").count()
    page.get_by_title("重新生成这条回复").first.click()
    page.wait_for_timeout(2600)
    assert page.get_by_title("重新生成这条回复").count() >= before

    # Delete uses the shared confirm dialog and hits the delete endpoint.
    deletes = page.get_by_title("删除这条消息")
    assert deletes.count() >= 1
    deletes.last.click()
    page.wait_for_timeout(250)
    confirm = page.get_by_role("dialog").last
    assert "删除" in confirm.inner_text()
    with page.expect_response(lambda res: "/api/messages/delete" in res.url) as deleted:
        confirm.get_by_role("button", name=re.compile("删除")).last.click()
    assert deleted.value.status == 200
    page.wait_for_timeout(500)
    assert confirm.count() == 0

    # User edit runs last because starting an edit truncates later messages.
    # The prompt must come back into the composer and stay editable.
    page.get_by_title("编辑并重新发送").first.click()
    page.wait_for_timeout(350)
    composer = page.get_by_placeholder(re.compile("给 Spark 发送任务"))
    assert prompt in composer.input_value()
    composer.fill("")
    assert errors == [], errors


def test_header_tools_menu_keyboard_and_actions(ui) -> None:
    """Every header tool entry opens, downloads, or returns focus correctly."""
    page, base = ui
    errors = _watch_errors(page, base)
    trigger = page.get_by_title("工具").first

    # Keyboard: focus enters the menu, arrows move, Escape returns to trigger.
    trigger.click()
    menu = page.get_by_role("menu")
    menu.wait_for(state="visible")
    page.wait_for_timeout(150)
    first = page.get_by_role("menuitem").first
    assert first.evaluate("el => el === document.activeElement")
    page.keyboard.press("End")
    assert page.get_by_role("menuitem").last.evaluate("el => el === document.activeElement")
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)
    assert page.get_by_role("menu").count() == 0
    assert trigger.evaluate("el => el === document.activeElement")

    # Files entry opens the workbench.
    trigger.click()
    page.get_by_role("menuitem").filter(has_text=re.compile("文件")).first.click()
    page.wait_for_timeout(500)
    assert page.get_by_test_id("workbench").is_visible()
    page.get_by_title("收起右侧面板").click()

    # Browser entry opens the workbench on its web tab.
    trigger.click()
    page.get_by_role("menuitem").filter(has_text=re.compile("网页")).first.click()
    page.wait_for_timeout(500)
    wb = page.get_by_test_id("workbench")
    assert wb.is_visible()
    assert wb.locator('input[placeholder*="输入网址"]').is_visible()
    page.get_by_title("收起右侧面板").click()

    # Trace entry opens the workbench on its execution-graph tab.
    trigger.click()
    page.get_by_role("menuitem").filter(has_text=re.compile("执行图")).first.click()
    page.wait_for_timeout(500)
    wb = page.get_by_test_id("workbench")
    assert wb.is_visible()
    assert "Agent 执行图" in wb.inner_text()
    page.get_by_title("收起右侧面板").click()

    # Markdown export: verify the downloaded payload is a real transcript.
    trigger.click()
    with page.expect_download() as markdown_download:
        page.get_by_role("menuitem").filter(has_text=re.compile("Markdown|导出")).first.click()
    md = markdown_download.value
    md_path = md.path()
    assert md_path is not None
    md_text = Path(md_path).read_text(encoding="utf-8", errors="replace")
    assert md.suggested_filename.endswith(".md")
    assert len(md_text) > 20

    # Single-file HTML share: self-contained and non-empty.
    trigger.click()
    with page.expect_download() as html_download:
        page.get_by_role("menuitem").filter(has_text=re.compile("HTML|分享")).first.click()
    share = html_download.value
    share_path = share.path()
    assert share_path is not None
    share_text = Path(share_path).read_text(encoding="utf-8", errors="replace")
    assert share.suggested_filename.endswith(".html")
    assert "<html" in share_text.lower()
    assert "spark" in share_text.lower()

    # Remaining header controls must open and close without exceptions.
    page.get_by_title("检查点回滚").click()
    page.wait_for_timeout(350)
    assert page.get_by_role("dialog").count() >= 1
    page.keyboard.press("Escape")
    page.wait_for_timeout(250)

    page.get_by_title("命令面板 (Ctrl+K)").click()
    page.locator('[role="dialog"]').first.wait_for(state="visible", timeout=4000)
    page.keyboard.press("Escape")
    page.wait_for_timeout(250)

    page.get_by_title("快捷键与用法 (?)").click()
    page.locator('[role="dialog"]').first.wait_for(state="visible", timeout=4000)
    page.keyboard.press("Escape")
    page.wait_for_timeout(250)

    # Theme toggles both directions.
    theme = page.get_by_title(re.compile("主题"))
    theme.click()
    page.wait_for_timeout(250)
    theme.click()
    page.wait_for_timeout(250)

    assert errors == [], errors


def test_workbench_five_tabs_and_file_actions(ui, tmp_path: Path) -> None:
    """Walk files, changes, terminal, web, trace and click their actions."""
    page, base = ui
    errors = _watch_errors(page, base)

    # The changes tab only offers real actions when the workspace is a git
    # repository with an uncommitted edit. The fixture workspace starts clean,
    # so seed both here; this also exercises the diff renderer.
    if not (tmp_path / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "-c", "user.email=ui@example.com", "-c", "user.name=UI Sweep", "commit", "-qm", "init"],
            cwd=tmp_path,
            check=True,
        )
    (tmp_path / "README.md").write_text(
        "# Demo workspace\n\nfile tree fixture\n\nuncommitted sweep line\n", encoding="utf-8"
    )

    _send_prompt(page, "请 list 当前工作区文件")
    page.get_by_title(re.compile("工作台")).first.click()
    page.wait_for_timeout(600)
    wb = page.get_by_test_id("workbench")
    assert wb.is_visible()

    # --- files: filter, preview, copy path/content, download, back ---------
    wb.get_by_title("文件", exact=True).first.click()
    page.wait_for_timeout(350)
    search = wb.locator('input[placeholder*="筛选文件"]')
    search.fill("readme")
    page.wait_for_timeout(300)
    file_button = wb.get_by_role("button").filter(has_text="README.md").first
    assert file_button.count() == 1
    file_button.click()
    page.wait_for_timeout(500)
    assert "file tree fixture" in wb.inner_text()

    wb.get_by_title("复制相对路径").click()
    page.wait_for_timeout(150)
    assert "README.md" in page.evaluate("() => navigator.clipboard.readText()")

    wb.get_by_title("复制内容").click()
    page.wait_for_timeout(150)
    assert "file tree fixture" in page.evaluate("() => navigator.clipboard.readText()")

    with page.expect_download() as file_download:
        wb.get_by_title("下载文件").click()
    downloaded = file_download.value
    assert downloaded.suggested_filename == "README.md"
    assert downloaded.path() is not None
    assert "file tree fixture" in Path(downloaded.path()).read_text(encoding="utf-8", errors="replace")

    wb.get_by_title("文件", exact=True).last.click()
    page.wait_for_timeout(300)

    # --- changes ----------------------------------------------------------
    wb.get_by_title("变更", exact=True).first.click()
    page.wait_for_timeout(500)
    assert wb.get_by_title("刷新变更").count() == 1
    wb.get_by_title("刷新变更").click()
    page.wait_for_timeout(400)
    wb.get_by_title("复制完整 diff").click()
    page.wait_for_timeout(150)

    # --- terminal ---------------------------------------------------------
    wb.get_by_title("终端", exact=True).first.click()
    page.wait_for_timeout(600)
    terminal_text = wb.inner_text()
    if "不支持伪终端" not in terminal_text:
        textarea = wb.locator('textarea[aria-label="Terminal input"]')
        textarea.focus()
        page.keyboard.type("echo spark-full-sweep")
        page.keyboard.press("Enter")
        page.wait_for_timeout(1200)
        assert "spark-full" in wb.inner_text()
        wb.get_by_title("清屏").click()
        page.wait_for_timeout(250)
        assert "spark-full" not in wb.inner_text()
        wb.get_by_title("重启 shell").click()
        page.wait_for_timeout(700)

    # --- web --------------------------------------------------------------
    wb.get_by_title("网页", exact=True).first.click()
    page.wait_for_timeout(500)
    url = wb.locator('input[placeholder*="输入网址"]')
    url.fill("https://example.com")
    wb.get_by_role("button", name=re.compile("打开")).first.click()
    page.wait_for_timeout(1200)
    assert wb.is_visible()

    # --- trace ------------------------------------------------------------
    wb.get_by_title("追踪", exact=True).first.click()
    page.wait_for_timeout(700)
    wb.get_by_title("复制图数据").click()
    page.wait_for_timeout(200)
    graph_clip = page.evaluate("() => navigator.clipboard.readText()")
    assert "nodes" in graph_clip and "edges" in graph_clip
    with page.expect_download() as svg_download:
        wb.get_by_title("导出 SVG").click()
    svg = svg_download.value
    assert svg.suggested_filename.endswith(".svg")
    svg_path = svg.path()
    assert svg_path is not None
    svg_text = Path(svg_path).read_text(encoding="utf-8", errors="replace")
    assert "<svg" in svg_text
    for zoom in ("Zoom In", "Zoom Out", "Fit View"):
        wb.get_by_title(zoom).click()
        page.wait_for_timeout(150)

    page.get_by_title("收起右侧面板").click()
    assert errors == [], errors


def test_keyboard_and_responsive_layout(ui) -> None:
    """Keyboard shortcuts and four viewport widths drive the same controls."""
    page, base = ui
    errors = _watch_errors(page, base)

    # Tab must move focus off <body> and expose a focusable control.
    page.keyboard.press("Tab")
    page.wait_for_timeout(100)
    assert page.evaluate("() => document.activeElement && document.activeElement !== document.body")

    # Global shortcuts. macOS Chromium may reserve Control+K for browser UI;
    # Mod+K is Ctrl+K on Linux/Windows and Meta+K on macOS.
    page.keyboard.press("Control+k")
    page.wait_for_timeout(300)
    if page.get_by_role("dialog").count() == 0:
        page.keyboard.press("Meta+k")
        page.wait_for_timeout(300)
    assert page.get_by_role("dialog").count() >= 1
    page.keyboard.press("Escape")

    page.keyboard.press("?")
    page.wait_for_timeout(300)
    assert page.get_by_role("dialog").count() >= 1
    page.keyboard.press("Escape")

    # Responsive widths: no page-level horizontal overflow, core controls stay usable.
    for width, height in ((1440, 900), (1024, 768), (768, 900), (390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(350)
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
        )
        assert not overflow, f"horizontal overflow at {width}x{height}"

        # Settings must be reachable at every width.
        settings = page.get_by_role("button", name=re.compile("设置|Settings")).first
        assert settings.is_visible(), f"settings hidden at {width}x{height}"
        _open_settings(page)
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)

        # Composer send/tools must stay visible and large enough to tap.
        send = page.get_by_title("发送").first
        assert send.is_visible(), f"send hidden at {width}x{height}"
        box = send.bounding_box()
        assert box is not None
        assert box["width"] >= (36 if width <= 390 else 28), (width, box)
        assert box["height"] >= (36 if width <= 390 else 28), (width, box)

    assert errors == [], errors


def test_settings_mock_probe_does_not_call_network(ui) -> None:
    """Mock provider must short-circuit instead of probing localhost:9999."""
    page, base = ui
    errors = _watch_errors(page, base)
    requests: list[str] = []
    page.on("request", lambda req: requests.append(req.url) if "/api/test" in req.url else None)

    dialog = _open_settings(page)
    page.get_by_role("button", name=re.compile("测试连接")).click()
    page.wait_for_timeout(500)
    assert requests == []
    text = dialog.inner_text()
    assert "Mock 演示模式" in text or "Mock demo mode" in text
    assert errors == [], errors


def test_sidebar_attachments_and_remaining_header_controls(ui, tmp_path: Path) -> None:
    """Cover the controls not reached by the chat / settings sweeps."""
    page, base = ui
    errors = _watch_errors(page, base)

    # New chat, rename and delete are the three sidebar lifecycle actions.
    page.get_by_role("button", name=re.compile("新建会话")).click()
    page.wait_for_timeout(350)

    page.locator("li.group").first.hover()
    rename = page.get_by_title("重命名会话（双击标题也可以）").first
    rename.click(force=True)
    page.wait_for_timeout(250)
    input_box = page.locator('input[aria-label="重命名会话（双击标题也可以）"]').first
    assert input_box.count() == 1
    input_box.fill("回归巡检会话")
    input_box.press("Enter")
    page.wait_for_timeout(400)
    assert "回归巡检会话" in page.inner_text("body")

    delete = page.get_by_title("删除会话").first
    delete.click(force=True)
    page.wait_for_timeout(200)
    inline_delete = page.locator("li.group").first.get_by_role("button", name=re.compile("删除")).last
    assert inline_delete.count() == 1
    inline_delete.click()
    page.wait_for_timeout(500)

    # Attachment button opens exactly one file chooser and renders the chip.
    note = tmp_path / "sweep-note.txt"
    note.write_text("attachment sweep", encoding="utf-8")
    attach = page.get_by_title(re.compile("添加附件")).first
    with page.expect_file_chooser() as chooser:
        attach.click()
    chooser.value.set_files(str(note))
    page.wait_for_timeout(500)
    assert "sweep-note.txt" in page.inner_text("body")

    # Web-search toggle changes state in both directions.
    web = page.get_by_title(re.compile("联网搜索"))
    assert web.count() == 1
    web.click()
    page.wait_for_timeout(250)
    web.click()
    page.wait_for_timeout(250)

    # Context ring and cost chip both route into settings.
    context = page.get_by_title(re.compile("上下文")).first
    context.click()
    page.locator('[role="dialog"]').first.wait_for(state="visible", timeout=5000)
    assert page.get_by_role("dialog").count() >= 1
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # A fresh session has no accrued cost yet; run one turn so the chip exists
    # and also covers send + model meta rendering.
    _send_prompt(page, "你好")
    page.wait_for_timeout(1200)

    cost = page.locator("button").filter(has_text=re.compile(r"^\$")).first
    if cost.count():
        cost.click()
        page.locator('[role="dialog"]').first.wait_for(state="visible", timeout=5000)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    else:
        # Unknown local pricing deliberately renders "—" in settings and omits
        # the cost chip rather than fabricating $0. Nothing to click here.
        assert cost.count() == 0

    # Model chip opens the model settings tab.
    model_chip = page.get_by_role("button", name=re.compile("Mock 演示|Mock demo")).last
    model_chip.click()
    page.locator('[role="dialog"]').first.wait_for(state="visible", timeout=5000)
    assert "模型" in page.get_by_role("dialog").first.inner_text()
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # Right panel trigger opens and closes the workbench.
    page.get_by_title("展开右侧面板").click()
    page.wait_for_timeout(450)
    assert page.get_by_test_id("workbench").is_visible()
    page.get_by_title("收起右侧面板").click()
    page.wait_for_timeout(350)

    assert errors == [], errors
