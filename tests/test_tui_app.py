"""TUI tests driven through Textual's own test harness.

The TUI was at 38% coverage because nothing exercised it; these tests run the
real app (real widgets, real bindings, real event loop) against a mock provider,
so they catch wiring regressions that a pure-function test cannot.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry

pytest.importorskip("textual")


def _build(tmp_path: Path):
    from spark.tui.app import SparkApp

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock-model"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "tui.db")
    sid = store.create_session(tmp_path, "mock-model")
    reg = ToolRegistry(ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg))
    app = SparkApp(
        workdir=tmp_path,
        cfg=cfg,
        store=store,
        session_id=sid,
        provider=MockProvider(),
        registry=reg,
        bridge=None,
    )
    return app, store


def test_tui_mounts_with_status_and_composer(tmp_path: Path) -> None:
    async def run() -> None:
        app, store = _build(tmp_path)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                from textual.widgets import Input

                composer = app.query_one("#composer", Input)
                assert composer is not None
                assert app.TITLE == "Spark"
                # Status line is populated from the real config, not a placeholder.
                status = app.query_one("#status")
                rendered = status.renderable if hasattr(status, "renderable") else str(status)
                assert "mock-model" in str(rendered) or "mock" in str(rendered).lower()
        finally:
            store.close()

    asyncio.run(run())


def test_tui_help_toggles(tmp_path: Path) -> None:
    async def run() -> None:
        app, store = _build(tmp_path)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                app.action_toggle_help()
                await pilot.pause()
                app.action_toggle_help()
                await pilot.pause()
                # Toggling twice returns to the normal screen without raising.
                assert app.screen is not None
        finally:
            store.close()

    asyncio.run(run())


def test_tui_clear_chat_is_safe_on_empty_log(tmp_path: Path) -> None:
    async def run() -> None:
        app, store = _build(tmp_path)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                app.action_clear_chat()
                await pilot.pause()
                app.action_clear_chat()
                await pilot.pause()
        finally:
            store.close()

    asyncio.run(run())


def test_tui_composer_accepts_typing(tmp_path: Path) -> None:
    async def run() -> None:
        from textual.widgets import Input

        app, store = _build(tmp_path)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.click("#composer")
                await pilot.press("h", "i")
                await pilot.pause()
                assert app.query_one("#composer", Input).value == "hi"
        finally:
            store.close()

    asyncio.run(run())


def test_tui_ctrl_d_quits_even_when_composer_focused(tmp_path: Path) -> None:
    """Input binds ctrl+d to delete_right, which used to swallow the quit key."""

    async def run() -> None:
        app, store = _build(tmp_path)
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.click("#composer")
                await pilot.press("ctrl+d")
                await pilot.pause()
                assert not app.is_running
        finally:
            store.close()

    asyncio.run(run())


def test_tui_quit_binding_is_registered(tmp_path: Path) -> None:
    """The bindings are the only discoverable interface: pin their keys."""

    async def run() -> None:
        app, store = _build(tmp_path)
        try:
            keys = {b.key: b.action for b in app.BINDINGS}
            assert keys.get("ctrl+d") == "quit"
            assert keys.get("ctrl+t") == "test_model"
            assert keys.get("question_mark") == "toggle_help"
            assert keys.get("ctrl+r") == "resume_session"
            assert keys.get("ctrl+l") == "clear_chat"
            assert keys.get("ctrl+c") == "cancel_turn"
        finally:
            store.close()

    asyncio.run(run())


def test_tui_welcome_helpers_cover_states(tmp_path: Path) -> None:
    from spark.tui.welcome import error_hint, help_text, status_text, welcome_text

    cfg = SparkConfig()
    cfg.provider.name = "openai_compat"
    cfg.provider.model = "demo"
    cfg.provider.base_url = "https://example.invalid/v1"
    cfg.provider.api_key = None
    cfg.provider.api_key_env = "SPARK_SURELY_MISSING"

    text = welcome_text(cfg, str(tmp_path))
    assert "demo" in text
    assert str(tmp_path) in text

    status = status_text(cfg, "abc123", str(tmp_path))
    assert "abc123" in status or "abc" in status

    help_body = help_text()
    assert "?" in help_body or "help" in help_body.lower()

    hint = error_hint("401 Unauthorized: invalid api key")
    assert hint  # a 401 must produce actionable guidance
    assert "key" in hint.lower() or "密钥" in hint
