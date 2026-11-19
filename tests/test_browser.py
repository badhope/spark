from __future__ import annotations

from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from spark.config import SparkConfig  # noqa: E402
from spark.sandbox import WorkdirSandbox  # noqa: E402
from spark.tools import browser  # noqa: E402
from spark.tools.registry import ToolContext, ToolRegistry  # noqa: E402


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(sandbox=WorkdirSandbox(tmp_path), config=SparkConfig())


def test_browser_local_page_click_type_screenshot(tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text(
        "<!doctype html><html><body>"
        "<button id='b' onclick=\"document.getElementById('out').textContent='clicked'\">Go</button>"
        "<input id='i'><div id='out'>start</div></body></html>"
    )
    ctx = _ctx(tmp_path)
    opened = browser.browser_open(
        ctx, browser.BrowserArgs(url="file://" + str(page))
    )
    assert opened.ok, opened.payload
    clicked = browser.browser_click(ctx, browser.BrowserClickArgs(selector="#b"))
    assert clicked.ok, clicked.payload
    assert "clicked" in str(clicked.payload["text"])
    typed = browser.browser_type(
        ctx, browser.BrowserTypeArgs(selector="#i", text="hello")
    )
    assert typed.ok, typed.payload
    shot = browser.browser_screenshot(ctx, browser.BrowserScreenshotArgs(name="test"))
    assert shot.ok, shot.payload
    assert (tmp_path / shot.payload["path"]).is_file()
    viewport = browser.browser_viewport(
        ctx, browser.BrowserViewportArgs(width=800, height=600)
    )
    assert viewport.ok, viewport.payload


def test_browser_rejects_file_outside_workdir(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.html"
    outside.write_text("<html><body>no</body></html>")
    ctx = _ctx(tmp_path)
    result = browser.browser_open(
        ctx, browser.BrowserArgs(url="file://" + str(outside))
    )
    assert not result.ok
    assert "工作目录" in str(result.payload.get("error", ""))


def test_browser_tools_registered(tmp_path: Path) -> None:
    names = {
        item["function"]["name"]
        for item in ToolRegistry(_ctx(tmp_path)).schemas()
    }
    assert {"browser_open", "browser_click", "browser_type", "browser_screenshot"} <= names
