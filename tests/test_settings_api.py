from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from spark.config import AgentConfig, HookConfig, SparkConfig, load_config, save_config
from spark.store import SessionStore
from spark.web.server import serve_web


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def get_json(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(base: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


@pytest.fixture()
def live(tmp_path: Path, monkeypatch):
    import spark.web.server as server_module

    # The spy exists so the suite can never touch the developer's real
    # ~/.spark/config.toml; it also re-serialises through the genuine
    # save_config into a temp file, so these tests still prove the payload is
    # writable/loadable instead of only proving "a function was called".
    written: list[SparkConfig] = []
    saved_paths: list[Path] = []

    def spy(cfg):
        written.append(cfg)
        target = tmp_path / f"saved-{len(saved_paths)}.toml"
        save_config(cfg, target)
        saved_paths.append(target)
        return target

    monkeypatch.setattr(server_module, "save_config", spy)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "web.db")
    port = free_port()
    thread = threading.Thread(
        target=serve_web,
        kwargs={
            "workdir": tmp_path,
            "cfg": cfg,
            "store": store,
            "host": "127.0.0.1",
            "port": port,
        },
        daemon=True,
    )
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            get_json(base, "/api/status")
            break
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    else:
        pytest.fail("server did not start")
    yield base, cfg, written, saved_paths
    store.close()


def test_settings_never_writes_real_user_config(live) -> None:
    base, cfg, written, saved_paths = live
    home_config = Path.home() / ".spark" / "config.toml"
    before = home_config.read_bytes() if home_config.exists() else None
    post_json(base, "/api/settings", {"agent": {"max_tool_rounds": 11}})
    after = home_config.read_bytes() if home_config.exists() else None
    assert before == after
    assert written, "expected save_config to be called"
    assert written[-1].agent.max_tool_rounds == 11
    # Round-trip through the real serialiser: the value must survive a reload,
    # not merely appear on the in-memory object handed to the spy.
    reloaded = load_config(config_path=saved_paths[-1], workdir=saved_paths[-1].parent)
    assert reloaded.agent.max_tool_rounds == 11


def test_config_endpoint_exposes_reliability_limits(live) -> None:
    base, _, _, _ = live
    agent = get_json(base, "/api/config")["agent"]
    assert agent["max_repeat_calls"] == 4
    assert agent["max_turn_tokens"] == 0


def test_settings_accepts_reliability_limits(live) -> None:
    base, cfg, _, _ = live
    post_json(
        base,
        "/api/settings",
        {"agent": {"max_repeat_calls": 7, "max_turn_tokens": 120000}},
    )
    agent = get_json(base, "/api/config")["agent"]
    assert agent["max_repeat_calls"] == 7
    assert agent["max_turn_tokens"] == 120000
    assert cfg.agent.max_repeat_calls == 7
    assert cfg.agent.max_turn_tokens == 120000


def test_settings_rejects_negative_repeat_limit(live) -> None:
    base, cfg, _, _ = live
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        post_json(base, "/api/settings", {"agent": {"max_repeat_calls": -3}})
    assert excinfo.value.code == 400
    assert cfg.agent.max_repeat_calls == 4


def test_settings_rejects_non_numeric_token_limit(live) -> None:
    base, _, _, _ = live
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        post_json(base, "/api/settings", {"agent": {"max_turn_tokens": "lots"}})
    assert excinfo.value.code == 400


def test_settings_accepts_zero_to_disable_token_budget(live) -> None:
    base, cfg, _, _ = live
    cfg.agent.max_turn_tokens = 5000
    post_json(base, "/api/settings", {"agent": {"max_turn_tokens": 0}})
    assert get_json(base, "/api/config")["agent"]["max_turn_tokens"] == 0


def test_settings_persists_hooks(live) -> None:
    base, cfg, _, _ = live
    post_json(
        base,
        "/api/settings",
        {
            "hooks": [
                {
                    "event": "pre_tool",
                    "command": "echo guard",
                    "args": [],
                    "name": "guard",
                    "timeout_sec": 5,
                }
            ]
        },
    )
    hooks = get_json(base, "/api/config")["hooks"]
    assert hooks[0]["event"] == "pre_tool"
    assert cfg.hooks[0].command == "echo guard"


def test_settings_rejects_unknown_hook_event(live) -> None:
    base, _, _, _ = live
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        post_json(
            base,
            "/api/settings",
            {"hooks": [{"event": "rm_rf", "command": "echo x"}]},
        )
    assert excinfo.value.code == 400


def test_config_roundtrips_hooks_through_toml(tmp_path: Path) -> None:
    cfg = SparkConfig()
    cfg.hooks = [
        HookConfig(event="pre_tool", command="lint.sh", name="linter", timeout_sec=9)
    ]
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    loaded = load_config(config_path=path, workdir=tmp_path)
    assert len(loaded.hooks) == 1
    assert loaded.hooks[0].command == "lint.sh"
    assert loaded.hooks[0].timeout_sec == 9


def test_agent_config_defaults_match_ui_contract() -> None:
    agent = AgentConfig()
    assert agent.max_repeat_calls == 4
    assert agent.max_turn_tokens == 0
    assert agent.browser_engine == "chromium"
    assert agent.browser_profile == ""
    assert agent.browser_cdp_endpoint == ""
    assert agent.browser_headless is True


def test_settings_accepts_browser_surface(live) -> None:
    base, cfg, _, saved_paths = live
    post_json(
        base,
        "/api/settings",
        {
            "agent": {
                "browser_engine": "msedge",
                "browser_profile": "work",
                "browser_cdp_endpoint": "http://127.0.0.1:9222",
                "browser_headless": False,
            }
        },
    )
    agent = get_json(base, "/api/config")["agent"]
    assert agent["browser_engine"] == "msedge"
    assert agent["browser_profile"] == "work"
    assert agent["browser_cdp_endpoint"] == "http://127.0.0.1:9222"
    assert agent["browser_headless"] is False
    reloaded = load_config(config_path=saved_paths[-1], workdir=saved_paths[-1].parent)
    assert reloaded.agent.browser_engine == "msedge"
    assert reloaded.agent.browser_profile == "work"
    assert reloaded.agent.browser_cdp_endpoint == "http://127.0.0.1:9222"
    assert reloaded.agent.browser_headless is False


def test_settings_rejects_bad_browser_profile(live) -> None:
    base, _, _, _ = live
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        post_json(
            base,
            "/api/settings",
            {"agent": {"browser_profile": "../escape"}},
        )
    assert excinfo.value.code == 400
