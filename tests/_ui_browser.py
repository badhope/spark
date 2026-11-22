"""Shared real-browser helpers for the UI tests.

Three test modules drive the actual frontend (``test_ui_browser``,
``test_settings_roundtrip``, ``test_settings_shelf_actions``). Each used to carry
its own copy of the "is a browser installed?" check, and one of those copies
returned ``True`` whenever ``CI`` was set — which turned a missing browser into
a hard failure on every runner. Availability is now probed for real: the module
either finds a launchable browser or the tests skip with a reason.

Launch order, most to least faithful to what a user runs:

1. ``SPARK_UI_CHANNEL`` — explicit override, e.g. ``chrome`` or ``msedge``.
2. Installed Edge / Chrome, via Playwright's ``channel`` (uses the real app).
3. Playwright's bundled Chromium, when ``playwright install chromium`` has run.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

EDGE_PATHS = (
    Path(r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    Path(r"C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
)
CHROME_PATHS = (
    Path(r"C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path(r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/chromium"),
    Path("/usr/bin/chromium-browser"),
)


def _system_browser_exists() -> bool:
    if any(path.exists() for path in EDGE_PATHS + CHROME_PATHS):
        return True
    return any(
        shutil.which(name) for name in ("google-chrome", "chromium", "chromium-browser")
    ) or any(
        Path(path).exists()
        for path in ("/usr/local/bin/chromium", "/usr/local/bin/chromium-browser")
    )


def _bundled_chromium_works() -> bool:
    """True when Playwright's own Chromium is downloaded and launchable.

    A bare ``import playwright`` succeeds even with no browser binaries, so the
    only honest check is to try launching one.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=_LAUNCH_ARGS)
            browser.close()
        return True
    except Exception:
        return False


def browser_available() -> bool:
    override = os.environ.get("SPARK_UI_CHANNEL")
    if override:
        return True
    return _system_browser_exists() or _bundled_chromium_works()


def launch(playwright):
    """Launch the best available browser, preferring an installed real one."""
    override = os.environ.get("SPARK_UI_CHANNEL")
    if override:
        return playwright.chromium.launch(channel=override, headless=True, args=_LAUNCH_ARGS)
    if any(path.exists() for path in EDGE_PATHS):
        return playwright.chromium.launch(channel="msedge", headless=True, args=_LAUNCH_ARGS)
    # Prefer Playwright's pinned Chromium when it is installed. The distro
    # `/usr/local/bin/chromium` on this cloud host crashes its renderer as soon
    # as the full settings modal mounts, while the pinned build stays alive.
    try:
        return playwright.chromium.launch(headless=True, args=_LAUNCH_ARGS)
    except Exception:
        pass
    for candidate in (Path("/usr/local/bin/chromium"), Path("/usr/local/bin/chromium-browser")):
        if candidate.exists():
            return playwright.chromium.launch(executable_path=str(candidate), headless=True, args=_LAUNCH_ARGS)
    if any(path.exists() for path in CHROME_PATHS):
        return playwright.chromium.launch(channel="chrome", headless=True, args=_LAUNCH_ARGS)
    if shutil.which("google-chrome"):
        return playwright.chromium.launch(channel="chrome", headless=True, args=_LAUNCH_ARGS)
    return playwright.chromium.launch(headless=True, args=_LAUNCH_ARGS)


_LAUNCH_ARGS = ["--disable-dev-shm-usage", "--disable-gpu"]


SKIP_REASON = (
    "no launchable browser: install Edge/Chrome, or run "
    "`python -m playwright install chromium`, or set SPARK_UI_CHANNEL"
)
