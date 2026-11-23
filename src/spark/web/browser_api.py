"""Live Playwright viewport for the workbench ``web`` tab.

The agent tools and this endpoint deliberately share :mod:`spark.tools.browser`
workers: a model calling ``browser_open`` is the same page the sidebar renders,
and a click in the sidebar lands in that same page.  No second browser is
started for the UI.
"""

from __future__ import annotations

from typing import Any

from spark.tools import browser
from spark.tools.registry import ToolContext
from spark.web._routing import _route_state
from spark.web.lease import lease_reject


def _ctx(state: Any) -> ToolContext:
    return ToolContext(sandbox=state.sandbox, config=state.cfg)


def handle_browser_state(handler: Any, body: dict | None = None) -> None:
    state = _route_state(handler)
    handler._json(200, browser.live_state(state.sandbox.root, state.cfg))


def handle_browser_shot(handler: Any, body: dict | None = None) -> None:
    state = _route_state(handler)
    data = browser.live_screenshot(state.sandbox.root)
    if data is None:
        handler.send_response(204)
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        return
    handler.send_response(200)
    handler.send_header("Content-Type", "image/png")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def _device_write_allowed(handler: Any, state: Any) -> bool:
    denied = lease_reject(handler, state.current_runtime, acquire=True)
    if denied is not None:
        handler._json(denied["status"], denied["body"])
        return False
    return True


def handle_browser_navigate(handler: Any, body: dict) -> None:
    state = _route_state(handler)
    if not _device_write_allowed(handler, state):
        return
    raw = str(body.get("url") or "").strip()
    # The sidebar address bar doubles as an omnibox: an empty value opens the
    # Bing home page and a bare phrase is a Bing search, not an invalid URL.
    target = browser.normalize_navigation_target(raw)
    result = browser.browser_open(
        _ctx(state), browser.BrowserArgs(url=target)
    )
    if not result.ok:
        handler._json(200, {"ok": False, **result.payload})
        return
    live = browser.live_state(state.sandbox.root, state.cfg)
    handler._json(200, {"ok": True, **result.payload, "revision": live.get("revision", 0)})


def handle_browser_action(handler: Any, body: dict) -> None:
    state = _route_state(handler)
    if not _device_write_allowed(handler, state):
        return
    action = str(body.get("action") or "").strip()
    if action not in {"back", "forward", "reload", "click", "type", "key"}:
        handler._json(400, {"error": "unsupported browser action"})
        return
    result = browser.live_action(
        _ctx(state),
        action=action,
        x=float(body.get("x") or 0),
        y=float(body.get("y") or 0),
        text=str(body.get("text") or ""),
        key=str(body.get("key") or ""),
    )
    live = browser.live_state(state.sandbox.root, state.cfg)
    handler._json(200, {**result, "revision": live.get("revision", 0)})
