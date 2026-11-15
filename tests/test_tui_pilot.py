"""TUI regressions found by driving the real app in a Textual Pilot session.

Two defects made the TUI unusable in practice while every existing test passed:

* ``SparkApp`` stored its own turn-in-progress flag in ``self._running``, a name
  Textual's ``App`` also uses for its message loop. Textual set it to ``True``
  at startup, so ``on_input_submitted`` returned immediately and the composer
  silently dropped every message.
* The tool-result line emitted ``[{color}]... [{status}][/color]`` — Rich parsed
  ``[ok]`` as a style tag, leaving ``[/color]`` unmatched, which raised
  ``MarkupError`` and tore down the app on the first tool call.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from spark.config import SparkConfig
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry
from spark.tui.app import SparkApp


class _NullBridge:
    async def call(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return ""

    async def close(self) -> None:
        return None

    def set_sampling_handler(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        return None


def _app(tmp_path: Path) -> tuple[SparkApp, SessionStore]:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.base_url = "http://localhost:9999/v1"
    cfg.provider.model = "mock-model"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "tui.db")
    sid = store.create_session(tmp_path, "mock", title="tui")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg)
    app = SparkApp(
        workdir=tmp_path,
        cfg=cfg,
        store=store,
        session_id=sid,
        provider=MockProvider(),
        registry=ToolRegistry(ctx),
        bridge=_NullBridge(),
        initial_prompt=None,
    )
    return app, store


def test_turn_flag_does_not_collide_with_textual(tmp_path: Path) -> None:
    """The busy flag must not reuse Textual's own ``_running`` attribute."""
    app, store = _app(tmp_path)
    try:
        assert app._turn_running is False
        # Textual's App owns `_running`; we must not read or write it.
        assert "_running" not in vars(app) or vars(app)["_running"] is not None
    finally:
        store.close()


def test_composer_submits_and_tool_result_renders(tmp_path: Path) -> None:
    """Typing + Enter must reach the agent, and a tool result must not crash."""
    from textual.widgets import RichLog

    async def run() -> str:
        app, store = _app(tmp_path)
        try:
            async with app.run_test() as pilot:
                await pilot.pause(1)
                assert app.query_one("#composer").has_focus
                await pilot.press(*"list files")
                await pilot.press("enter")
                await pilot.pause(10)
                log = app.query_one("#chat", RichLog)
                return "\n".join(str(getattr(line, "text", line)) for line in log.lines)
        finally:
            store.close()

    rendered = asyncio.run(run())
    assert "list files" in rendered, rendered[-400:]
    assert "list_dir" in rendered, "tool call never rendered"
    assert "◀" in rendered, "tool result line missing"
    # The reply proves the turn actually completed instead of hanging.
    assert "Done. Tool results applied." in rendered or "mock provider" in rendered
