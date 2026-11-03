from __future__ import annotations

import pytest

from spark.config import SparkConfig
from spark.tui.welcome import (
    error_hint,
    help_text,
    key_status,
    status_text,
    welcome_text,
)


def make_cfg(**kwargs) -> SparkConfig:
    cfg = SparkConfig()
    cfg.provider.name = "openai_compat"
    cfg.provider.model = "demo-model"
    cfg.provider.base_url = "https://example.invalid/v1"
    for key, value in kwargs.items():
        section, field = key.split("__")
        target = getattr(cfg, section)
        setattr(target, field, value)
    return cfg


def test_key_status_marks_configured_key() -> None:
    cfg = make_cfg(provider__api_key="sk-abcdefghijklmnop")
    marker, label = key_status(cfg)
    assert marker == "OK"
    assert "sk-a" in label
    assert "sk-abcdefghijklmnop" not in label


def test_key_status_flags_missing_key(monkeypatch) -> None:
    monkeypatch.delenv("SPARK_API_KEY", raising=False)
    monkeypatch.delenv("SPARK_KEY", raising=False)
    cfg = make_cfg(provider__api_key=None)
    marker, label = key_status(cfg)
    assert marker == "X"
    assert "spark" in label.lower() or "?" in label


def test_key_status_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("SPARK_API_KEY", "sk-from-env-1234")
    cfg = make_cfg(provider__api_key=None)
    marker, _ = key_status(cfg)
    assert marker == "OK"


def test_key_status_flags_mock_provider() -> None:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    marker, label = key_status(cfg)
    assert marker == "!"
    assert "mock" in label.lower()


def test_status_text_lists_core_facts() -> None:
    text = status_text(make_cfg(provider__api_key="sk-abcdefghijkl"), "sess-1234")
    assert "demo-model" in text
    assert "example.invalid" in text
    assert "sess-12" in text


def test_welcome_contains_examples_and_shortcut_hint() -> None:
    text = welcome_text(make_cfg(provider__api_key="sk-abcdefghijkl"))
    assert "可以这样说" in text
    assert "?" in text
    assert "demo-model" in text


def test_welcome_warns_when_key_missing(monkeypatch) -> None:
    monkeypatch.delenv("SPARK_API_KEY", raising=False)
    text = welcome_text(make_cfg(provider__api_key=None))
    assert "spark init" in text


def test_help_text_lists_commands_and_shortcuts() -> None:
    text = help_text()
    for expected in ("Ctrl+T", "spark web", "spark doctor", "spark init"):
        assert expected in text


def test_error_hint_covers_common_failures() -> None:
    assert "spark init" in error_hint("Missing User API Key")
    assert "base_url" in error_hint("connection timeout")
    assert "max_repeat_calls" in error_hint("Circuit breaker: tool x repeated")
    assert "max_turn_tokens" in error_hint("Token budget exceeded for this turn")
    assert "doctor" in error_hint("something odd happened")


@pytest.mark.asyncio
async def test_tui_mounts_and_opens_help_without_crashing(tmp_path) -> None:
    from spark.providers.mock import MockProvider
    from spark.sandbox import WorkdirSandbox
    from spark.store import SessionStore
    from spark.tools.registry import ToolContext, ToolRegistry
    from spark.tui.app import HelpScreen, SparkApp

    cfg = make_cfg(provider__api_key="sk-abcdefghijkl")
    store = SessionStore(tmp_path / "tui.db")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
    app = SparkApp(
        workdir=tmp_path,
        cfg=cfg,
        store=store,
        session_id="session-under-test",
        provider=MockProvider(),
        registry=ToolRegistry(ctx),
        bridge=None,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one("#chat")
        rendered = "\n".join(str(line) for line in chat.lines)
        assert rendered.strip(), "welcome panel should render"
        assert "spark init" in rendered or "可以这样说" in rendered
        assert any(binding.key == "question_mark" for binding in app.BINDINGS)
        app.action_toggle_help()
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)
    store.close()


@pytest.mark.asyncio
async def test_tui_narrow_terminal_fits_toolbar_and_status(tmp_path) -> None:
    from spark.providers.mock import MockProvider
    from spark.sandbox import WorkdirSandbox
    from spark.store import SessionStore
    from spark.tools.registry import ToolContext, ToolRegistry
    from spark.tui.app import SparkApp

    cfg = make_cfg(provider__api_key="sk-abcdefghijkl")
    store = SessionStore(tmp_path / "tui-narrow.db")
    app = SparkApp(
        workdir=tmp_path,
        cfg=cfg,
        store=store,
        session_id="session-under-test",
        provider=MockProvider(),
        registry=ToolRegistry(
            ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
        ),
        bridge=None,
    )
    async with app.run_test(size=(20, 10)) as pilot:
        await pilot.pause()
        assert all(
            widget.region.x >= 0 and widget.region.x + widget.region.width <= 20
            for widget in app.screen.walk_children()
        )
        status = app.query_one("#status")
        assert status.styles.overflow_x == "hidden"
        assert status.styles.height is not None
        assert app.query_one("#test-model").label.plain == "T"
        assert app.query_one("#help").label.plain == "?"
    store.close()


@pytest.mark.asyncio
async def test_tui_narrow_approval_dialog_fits_buttons(tmp_path) -> None:
    from spark.models import ApprovalRequest, ToolCall
    from spark.providers.mock import MockProvider
    from spark.sandbox import WorkdirSandbox
    from spark.store import SessionStore
    from spark.tools.registry import ToolContext, ToolRegistry
    from spark.tui.app import ApprovalScreen, SparkApp

    cfg = make_cfg(provider__api_key="sk-abcdefghijkl")
    store = SessionStore(tmp_path / "tui-approval.db")
    app = SparkApp(
        workdir=tmp_path,
        cfg=cfg,
        store=store,
        session_id="session-under-test",
        provider=MockProvider(),
        registry=ToolRegistry(
            ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
        ),
        bridge=None,
    )
    request = ApprovalRequest(
        tool_call=ToolCall(id="call", name="run_shell", arguments={}),
        summary="run a command",
    )
    async with app.run_test(size=(20, 10)) as pilot:
        await pilot.pause()
        app.push_screen(ApprovalScreen(request))
        await pilot.pause()
        assert all(
            widget.region.x >= 0 and widget.region.x + widget.region.width <= 20
            for widget in app.screen.walk_children()
        )
        assert app.screen.query_one("#dialog").styles.max_width is not None
    store.close()


def test_key_status_matches_require_api_key_for_unbound_env(monkeypatch) -> None:
    """与 require_api_key 同口径：未绑定 api_key_env 时不得把 SPARK_API_KEY 当已配置。"""
    from spark.config import require_api_key
    from spark.errors import ConfigError

    monkeypatch.setenv("SPARK_API_KEY", "sk-global-but-unbound")
    cfg = make_cfg(provider__api_key=None)
    cfg.provider.api_key_env = ""
    marker, label = key_status(cfg)
    with pytest.raises(ConfigError):
        require_api_key(cfg)
    assert marker == "X", label


def test_key_status_accepts_bound_env(monkeypatch) -> None:
    monkeypatch.setenv("BOUND_KEY", "sk-bound")
    cfg = make_cfg(provider__api_key=None)
    cfg.provider.api_key_env = "BOUND_KEY"
    marker, label = key_status(cfg)
    assert marker == "OK"
    assert "BOUND_KEY" in label


def test_key_status_treats_ollama_as_usable_without_key() -> None:
    cfg = make_cfg(provider__api_key=None)
    cfg.provider.name = "ollama"
    cfg.provider.api_key_env = ""
    marker, _label = key_status(cfg)
    assert marker == "OK"


def test_status_text_default_order_unchanged() -> None:
    cfg = make_cfg(provider__api_key="sk-abcdefghijkl", agent__sandbox_mode="workspace")
    text = status_text(cfg, "sess-1234", workdir="/tmp/wd")
    assert text.startswith("模型 demo-model")
    assert text.endswith("会话 sess-123")
    # 无 workdir 时目录项自动省略，不出现空的"目录 "标签
    text_no_wd = status_text(cfg, "sess-1234")
    assert "目录" not in text_no_wd


def test_status_items_reorder_and_filter() -> None:
    cfg = make_cfg(provider__api_key="sk-abcdefghijkl")
    cfg.agent.status_items = ["session", "model"]
    text = status_text(cfg, "sess-1234", workdir="/tmp/wd")
    assert text == "会话 sess-123  |  模型 demo-model"


def test_status_items_unknown_entries_are_skipped() -> None:
    cfg = make_cfg(provider__api_key="sk-abcdefghijkl")
    cfg.agent.status_items = ["bogus_item", "model"]
    text = status_text(cfg, "sess-1234")
    assert "bogus_item" not in text
    assert "demo-model" in text


def test_status_items_empty_string_entries_ignored() -> None:
    cfg = make_cfg(provider__api_key="sk-abcdefghijkl")
    cfg.agent.status_items = ["", "  ", "session"]
    text = status_text(cfg, "sess-1234")
    assert text == "会话 sess-123"


def test_status_items_round_trip_via_save_and_load(tmp_path) -> None:
    from spark.config import load_config, save_config

    cfg = make_cfg()
    cfg.agent.status_items = ["session", "approval", "key"]
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    loaded = load_config(config_path=path, workdir=tmp_path)
    assert loaded.agent.status_items == ["session", "approval", "key"]
    # 默认值不落盘：未定制时不产生 status_items 行
    cfg2 = make_cfg()
    cfg2.agent.status_items = []
    p2 = tmp_path / "default.toml"
    save_config(cfg2, p2)
    assert "status_items" not in p2.read_text(encoding="utf-8")
