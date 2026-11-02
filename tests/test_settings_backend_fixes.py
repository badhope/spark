"""回归测试：后端设置系统的持久化、边界校验、脱敏与配置暴露。

覆盖五类真实缺陷：

1. ``save_config`` 漏写 provider 韧性、memory 嵌入/重排、context 知识库、
   model_profiles 档案级字段 → 设置保存后重启丢失；
2. ``/api/settings`` 只钳下限，越界值 200 落盘，下次 ``load_config`` 抛
   ConfigError 走自愈重置；
3. ``_parse_hooks`` 不解析 ``on_deny``，任何 Web 保存把 warn 重置为 block；
4. ``/api/config`` 不返回、``/api/settings`` 不接受多组高级设置字段；
5. ``spark config show`` 明文输出 embedding 密钥与 MCP env / http.headers。
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from spark.config import (
    HookConfig,
    McpServerConfig,
    McpTransportConfig,
    ModelProfile,
    PermissionRule,
    SparkConfig,
    load_config,
    save_config,
)
from spark.store import SessionStore
from spark.web.server import build_web_server


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(base: str, path: str, body: object | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(
        base + path,
        data=data,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture()
def web(tmp_path: Path, monkeypatch):
    """真实 HTTP 服务 + 真实 save_config（写入 tmp 文件，绝不碰 ~/.spark）。"""
    import spark.web.server as server_module

    saved: list[Path] = []

    def spy(cfg: SparkConfig) -> Path:
        target = tmp_path / f"saved-{len(saved)}.toml"
        save_config(cfg, target)
        saved.append(target)
        return target

    monkeypatch.setattr(server_module, "save_config", spy)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock-model"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "settings-fixes.db")
    port = _free_port()
    server, state = build_web_server(
        workdir=tmp_path, cfg=cfg, store=store, host="127.0.0.1", port=port
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            _request(base, "/api/status")
            break
        except (urllib.error.URLError, OSError):
            time.sleep(0.05)
    else:
        server.shutdown()
        server.server_close()
        store.close()
        pytest.fail("server did not start")
    try:
        yield base, state, saved, tmp_path
    finally:
        server.shutdown()
        server.server_close()
        store.close()


def _reload(saved: list[Path], tmp_path: Path) -> SparkConfig:
    assert saved, "expected the settings save to persist a file"
    return load_config(config_path=saved[-1], workdir=tmp_path)


# ---------------------------------------------------------------------------
# 1. save → reload 往返：此前这些字段从不写进 TOML，重启即丢失
# ---------------------------------------------------------------------------


def test_save_config_roundtrips_provider_resilience(tmp_path: Path) -> None:
    cfg = SparkConfig()
    cfg.provider.api_keys = ["sk-pool-1", "sk-pool-2"]
    cfg.provider.fallback_models = ["fallback-a", "fallback-b"]
    cfg.provider.small_model = "tiny-model"
    path = tmp_path / "config.toml"
    save_config(cfg, path)

    loaded = load_config(config_path=path, workdir=tmp_path)
    assert loaded.provider.api_keys == ["sk-pool-1", "sk-pool-2"]
    assert loaded.provider.fallback_models == ["fallback-a", "fallback-b"]
    assert loaded.provider.small_model == "tiny-model"


def test_save_config_roundtrips_memory_embedding_and_rerank(tmp_path: Path) -> None:
    cfg = SparkConfig()
    cfg.memory.embedding_enabled = True
    cfg.memory.embedding_base_url = "https://embedding.example/v1"
    cfg.memory.embedding_api_key = "sk-embedding-secret"
    cfg.memory.rerank_model = "BAAI/bge-reranker-v2-m3"
    path = tmp_path / "config.toml"
    save_config(cfg, path)

    loaded = load_config(config_path=path, workdir=tmp_path)
    assert loaded.memory.embedding_enabled is True
    assert loaded.memory.embedding_base_url == "https://embedding.example/v1"
    assert loaded.memory.embedding_api_key == "sk-embedding-secret"
    assert loaded.memory.rerank_model == "BAAI/bge-reranker-v2-m3"


def test_save_config_roundtrips_context_knowledge(tmp_path: Path) -> None:
    cfg = SparkConfig()
    cfg.context.knowledge_enabled = False
    cfg.context.knowledge_top_k = 9
    cfg.context.knowledge_max_chars = 4321
    path = tmp_path / "config.toml"
    save_config(cfg, path)

    loaded = load_config(config_path=path, workdir=tmp_path)
    assert loaded.context.knowledge_enabled is False
    assert loaded.context.knowledge_top_k == 9
    assert loaded.context.knowledge_max_chars == 4321


def test_save_config_roundtrips_model_profile_resilience(tmp_path: Path) -> None:
    cfg = SparkConfig()
    cfg.model_profiles = [
        ModelProfile(
            id="p1",
            name="Profile 1",
            provider="openai_compat",
            base_url="https://api.example/v1",
            model="m1",
            api_key="sk-profile",
            api_keys=["sk-profile-pool"],
            fallback_models=["m1-fallback"],
            small_model="m1-small",
        )
    ]
    cfg.active_profile_id = "p1"
    path = tmp_path / "config.toml"
    save_config(cfg, path)

    loaded = load_config(config_path=path, workdir=tmp_path)
    profile = loaded.model_profiles[0]
    assert profile.api_keys == ["sk-profile-pool"]
    assert profile.fallback_models == ["m1-fallback"]
    assert profile.small_model == "m1-small"


def test_settings_save_survives_reload(web) -> None:
    base, _state, saved, tmp_path = web
    status, body = _request(
        base,
        "/api/settings",
        {
            "provider": {
                "api_keys": ["sk-pool-1"],
                "fallback_models": ["fb-1"],
                "small_model": "tiny",
            },
            "context": {"knowledge_enabled": False, "knowledge_top_k": 11},
            "memory": {
                "embedding_enabled": True,
                "embedding_base_url": "https://embedding.example/v1",
                "rerank_model": "rerank-x",
            },
        },
    )
    assert status == 200, body

    loaded = _reload(saved, tmp_path)
    assert loaded.provider.api_keys == ["sk-pool-1"]
    assert loaded.provider.fallback_models == ["fb-1"]
    assert loaded.provider.small_model == "tiny"
    assert loaded.context.knowledge_enabled is False
    assert loaded.context.knowledge_top_k == 11
    assert loaded.memory.embedding_enabled is True
    assert loaded.memory.embedding_base_url == "https://embedding.example/v1"
    assert loaded.memory.rerank_model == "rerank-x"


# ---------------------------------------------------------------------------
# 2. 越界值必须 400 且不落盘（此前 200 落盘 → 下次 load_config 抛 ConfigError）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"agent": {"shell_timeout_sec": 0}},
        {"agent": {"shell_timeout_sec": 99999}},
        {"agent": {"max_tool_rounds": 0}},
        {"agent": {"max_tool_rounds": 5000}},
        {"agent": {"max_output_chars": 10}},
        {"agent": {"max_repeat_calls": 100_000}},
        {"context": {"compact_threshold": 5}},
        {"context": {"keep_recent_messages": 1}},
        {"context": {"repo_map_max_chars": 10}},
        {"context": {"knowledge_top_k": 999}},
        {"context": {"knowledge_max_chars": 10}},
        {"memory": {"top_k": 999}},
        {"memory": {"capacity": 0}},
    ],
)
def test_settings_reject_out_of_range_values_without_persisting(web, payload) -> None:
    base, state, saved, _tmp_path = web
    before = state.cfg.model_dump()
    status, body = _request(base, "/api/settings", payload)
    assert status == 400, (payload, body)
    assert "error" in body
    assert state.cfg.model_dump() == before, "a rejected save must change nothing"
    assert saved == [], "a rejected save must not persist a config file"


def test_rejected_out_of_range_value_keeps_config_loadable(web) -> None:
    """反证：越界值若能落盘，下一次 load_config 会因 ConfigError 自愈重置。"""
    base, state, saved, _tmp_path = web
    status, _ = _request(base, "/api/settings", {"agent": {"shell_timeout_sec": 99999}})
    assert status == 400
    assert saved == []
    # 当前内存配置仍能被 pydantic 完整校验，说明下次启动不会被自愈重置。
    valid = SparkConfig.model_validate(state.cfg.model_dump())
    assert valid.agent.shell_timeout_sec == state.cfg.agent.shell_timeout_sec


# ---------------------------------------------------------------------------
# 3. hooks[].on_deny 必须被解析并保留
# ---------------------------------------------------------------------------


def test_settings_preserves_hook_on_deny_when_omitted(web) -> None:
    base, state, _saved, _tmp_path = web
    state.cfg.hooks = [
        HookConfig(event="pre_tool", command="echo guard", name="guard", on_deny="warn")
    ]
    status, body = _request(
        base,
        "/api/settings",
        {
            "hooks": [
                {
                    "event": "pre_tool",
                    "command": "echo guard",
                    "args": [],
                    "name": "guard",
                    "timeout_sec": 15,
                }
            ]
        },
    )
    assert status == 200, body
    assert state.cfg.hooks[0].on_deny == "warn", "an omitted on_deny must not reset to block"


def test_settings_applies_hook_on_deny(web) -> None:
    base, state, saved, tmp_path = web
    status, body = _request(
        base,
        "/api/settings",
        {
            "hooks": [
                {
                    "event": "pre_tool",
                    "command": "echo guard",
                    "args": [],
                    "name": "guard",
                    "timeout_sec": 15,
                    "on_deny": "warn",
                }
            ]
        },
    )
    assert status == 200, body
    assert state.cfg.hooks[0].on_deny == "warn"
    assert _request(base, "/api/config")[1]["hooks"][0]["on_deny"] == "warn"
    assert _reload(saved, tmp_path).hooks[0].on_deny == "warn"


def test_settings_rejects_unknown_hook_on_deny(web) -> None:
    base, state, saved, _tmp_path = web
    status, body = _request(
        base,
        "/api/settings",
        {"hooks": [{"event": "pre_tool", "command": "echo", "on_deny": "explode"}]},
    )
    assert status == 400, body
    assert state.cfg.hooks == []
    assert saved == []


# ---------------------------------------------------------------------------
# 4. /api/config 暴露 / /api/settings 接受高级设置字段
# ---------------------------------------------------------------------------


def test_config_endpoint_exposes_advanced_settings(web) -> None:
    base, state, _saved, _tmp_path = web
    state.cfg.memory.embedding_api_key = "sk-embedding-secret"
    state.cfg.permission_rules = [PermissionRule(action="deny", resource="/etc/**")]
    state.cfg.mcp_servers = [
        McpServerConfig(
            name="remote",
            transport="sse",
            allowed_tools=["list_*"],
            http=McpTransportConfig(
                url="https://mcp.example/sse",
                headers={"Authorization": "Bearer top-secret"},
            ),
        )
    ]
    status, config = _request(base, "/api/config")
    assert status == 200

    context = config["context"]
    for key in (
        "agents_md",
        "repo_map_max_chars",
        "knowledge_enabled",
        "knowledge_top_k",
        "knowledge_max_chars",
    ):
        assert key in context, f"context.{key} missing from /api/config"

    memory = config["memory"]
    for key in (
        "embedding_enabled",
        "embedding_base_url",
        "embedding_api_key_masked",
        "has_embedding_api_key",
        "rerank_model",
    ):
        assert key in memory, f"memory.{key} missing from /api/config"

    assert "api_key_env" in config["provider"]
    assert config["mcp_servers"][0]["allowed_tools"] == ["list_*"]
    assert config["replay"]["enabled"] is True
    assert config["updater"]["mode"] == "notify"
    assert config["permission_rules"] == [
        {"action": "deny", "resource": "/etc/**", "tool": ""}
    ]
    assert config["approval_profiles"] == []
    assert config["active_approval_profile_id"] == ""

    dumped = json.dumps(config)
    assert "sk-embedding-secret" not in dumped
    assert "Bearer top-secret" not in dumped


def test_config_endpoint_exposes_hook_on_deny(web) -> None:
    base, state, _saved, _tmp_path = web
    state.cfg.hooks = [HookConfig(event="pre_tool", command="echo", on_deny="warn")]
    hooks = _request(base, "/api/config")[1]["hooks"]
    assert hooks[0]["on_deny"] == "warn"


def test_settings_accepts_advanced_fields(web) -> None:
    base, state, saved, tmp_path = web
    status, body = _request(
        base,
        "/api/settings",
        {
            "provider": {"api_key_env": "MY_SPARK_KEY"},
            "context": {
                "agents_md": "CLAUDE.md",
                "repo_map_max_chars": 9000,
                "knowledge_enabled": False,
                "knowledge_top_k": 4,
                "knowledge_max_chars": 3000,
            },
            "memory": {
                "embedding_enabled": True,
                "embedding_base_url": "https://embedding.example/v1",
                "embedding_api_key": "sk-embedding-secret",
                "rerank_model": "rerank-x",
            },
            "replay": {"enabled": False},
            "updater": {"mode": "off"},
        },
    )
    assert status == 200, body

    loaded = _reload(saved, tmp_path)
    assert loaded.provider.api_key_env == "MY_SPARK_KEY"
    assert loaded.context.agents_md == "CLAUDE.md"
    assert loaded.context.repo_map_max_chars == 9000
    assert loaded.context.knowledge_enabled is False
    assert loaded.context.knowledge_top_k == 4
    assert loaded.context.knowledge_max_chars == 3000
    assert loaded.memory.embedding_enabled is True
    assert loaded.memory.embedding_base_url == "https://embedding.example/v1"
    assert loaded.memory.embedding_api_key == "sk-embedding-secret"
    assert loaded.memory.rerank_model == "rerank-x"
    assert loaded.replay.enabled is False
    assert loaded.updater.mode == "off"
    assert state.cfg.updater.mode == "off"


def test_settings_keeps_stored_embedding_key_when_blank(web) -> None:
    base, state, saved, tmp_path = web
    state.cfg.memory.embedding_api_key = "sk-stored"
    status, body = _request(
        base, "/api/settings", {"memory": {"embedding_api_key": ""}}
    )
    assert status == 200, body
    assert state.cfg.memory.embedding_api_key == "sk-stored"
    assert _reload(saved, tmp_path).memory.embedding_api_key == "sk-stored"


@pytest.mark.parametrize("new_url,new_key,expected", [
    ("https://other.example/v1", "", ""),
    ("https://other.example/v1", "synthetic-new", "synthetic-new"),
    ("https://old.example/v1/", "", "synthetic-old"),
])
def test_embedding_url_change_does_not_reuse_old_key(web, new_url, new_key, expected) -> None:
    base, state, saved, tmp_path = web
    state.cfg.memory.embedding_base_url = "https://old.example/v1"
    state.cfg.memory.embedding_api_key = "synthetic-old"
    status, body = _request(base, "/api/settings", {"memory": {"embedding_base_url": new_url, "embedding_api_key": new_key}})
    assert status == 200, body
    assert state.cfg.memory.embedding_api_key == expected
    assert _reload(saved, tmp_path).memory.embedding_api_key == expected


def test_web_profile_switch_isolates_environment_after_reload(web, monkeypatch) -> None:
    from spark.config import require_api_key
    from spark.errors import ConfigError

    base, state, saved, tmp_path = web
    monkeypatch.setenv("SPARK_API_KEY", "synthetic-environment")
    monkeypatch.setattr(state, "rebuild_loop", lambda **kwargs: None)
    state.cfg.provider.name = "openai_compat"
    state.cfg.provider.base_url = "https://same.example/v1"
    state.cfg.provider.api_key = "synthetic-old"
    state.cfg.provider.api_keys = ["synthetic-old-pool"]
    state.cfg.active_profile_id = "first"
    state.cfg.model_profiles = [ModelProfile(id="second", base_url="https://same.example/v1", model="new")]
    status, body = _request(base, "/api/models/activate", {"id": "second"})
    assert status == 200, body
    loaded = _reload(saved, tmp_path)
    assert loaded.provider.api_key_env == ""
    assert loaded.provider.api_keys == []
    with pytest.raises(ConfigError):
        require_api_key(loaded)


def test_provider_resilience_explicit_clear_persists(web) -> None:
    base, state, saved, tmp_path = web
    state.cfg.provider.api_keys = ["synthetic-pool"]
    state.cfg.provider.fallback_models = ["fallback"]
    state.cfg.provider.small_model = "small"
    status, body = _request(base, "/api/settings", {"provider": {"api_keys": [], "fallback_models": [], "small_model": ""}})
    assert status == 200, body
    provider = _reload(saved, tmp_path).provider
    assert (provider.api_keys, provider.fallback_models, provider.small_model) == ([], [], "")


def test_settings_rejects_unsafe_agents_md(web) -> None:
    base, state, saved, _tmp_path = web
    status, body = _request(
        base, "/api/settings", {"context": {"agents_md": "../outside.md"}}
    )
    assert status == 400, body
    assert state.cfg.context.agents_md == "AGENTS.md"
    assert saved == []


def test_settings_rejects_invalid_updater_mode(web) -> None:
    base, state, saved, _tmp_path = web
    status, body = _request(base, "/api/settings", {"updater": {"mode": "always"}})
    assert status == 400, body
    assert state.cfg.updater.mode == "notify"
    assert saved == []


def test_web_create_profile_explicit_environment_binding_roundtrips(web, monkeypatch) -> None:
    from spark.config import require_api_key

    base, state, saved, tmp_path = web
    monkeypatch.setenv("PROFILE_KEY", "synthetic-profile")
    state.cfg.provider.name = "openai_compat"
    state.cfg.provider.api_key = None
    state.cfg.provider.api_key_env = "PROFILE_KEY"
    status, body = _request(base, "/api/models/save", {
        "name": "explicit", "base_url": "https://example.test/v1", "model": "model",
        "api_key_env": "PROFILE_KEY",
    })
    assert status == 200
    profile = next(p for p in _reload(saved, tmp_path).model_profiles if p.id == body["id"])
    assert profile.api_key_env == "PROFILE_KEY"
    status, _ = _request(base, "/api/models/activate", {"id": body["id"]})
    assert status == 200
    assert require_api_key(_reload(saved, tmp_path)) == "synthetic-profile"


def test_settings_allows_explicit_environment_unbinding(web, monkeypatch) -> None:
    from spark.config import env_api_key

    base, state, saved, tmp_path = web
    monkeypatch.setenv("SPARK_API_KEY", "synthetic-global")
    state.cfg.provider.api_key_env = "SPARK_API_KEY"
    status, _ = _request(base, "/api/settings", {"provider": {"api_key_env": ""}})
    assert status == 200
    assert state.cfg.provider.api_key_env == ""
    loaded = _reload(saved, tmp_path)
    assert loaded.provider.api_key_env == ""
    assert env_api_key(loaded) == ""


def test_settings_rejects_invalid_api_key_env_name(web) -> None:
    base, state, saved, _tmp_path = web
    status, body = _request(
        base, "/api/settings", {"provider": {"api_key_env": "not a name"}}
    )
    assert status == 400, body
    assert state.cfg.provider.api_key_env == "SPARK_API_KEY"
    assert saved == []


@pytest.mark.parametrize(
    "payload",
    [
        {"permission_rules": [{"action": "allow", "resource": "**"}]},
        {"approval_profiles": [{"id": "p", "name": "P"}]},
        {"active_approval_profile_id": "p"},
    ],
)
def test_settings_rejects_read_only_sections_explicitly(web, payload) -> None:
    """只读段必须显式 400，而不是静默忽略让调用方误以为写成功。"""
    base, state, saved, _tmp_path = web
    before = state.cfg.model_dump()
    status, body = _request(base, "/api/settings", payload)
    assert status == 400, body
    assert "read-only" in body["error"]
    assert state.cfg.model_dump() == before
    assert saved == []


# ---------------------------------------------------------------------------
# 5. spark config show 必须与 /api/config 使用同一套脱敏策略
# ---------------------------------------------------------------------------


def _secret_config() -> SparkConfig:
    cfg = SparkConfig()
    cfg.memory.embedding_api_key = "sk-embedding-secret"
    cfg.mcp_servers = [
        McpServerConfig(
            name="stdio",
            command="npx",
            env={"GITHUB_TOKEN": "ghp-super-secret"},
        ),
        McpServerConfig(
            name="remote",
            transport="sse",
            http=McpTransportConfig(
                url="https://mcp.example/sse",
                headers={"Authorization": "Bearer top-secret", "X-Trace": "public"},
            ),
        ),
    ]
    return cfg


def test_config_as_dict_never_leaks_secrets() -> None:
    from spark.onboard import config_as_dict

    dumped = json.dumps(config_as_dict(_secret_config()), ensure_ascii=False)
    for secret in (
        "sk-embedding-secret",
        "ghp-super-secret",
        "Bearer top-secret",
    ):
        assert secret not in dumped, f"{secret!r} leaked from config show"


def test_render_config_text_never_leaks_secrets() -> None:
    from spark.onboard import render_config_text

    text = render_config_text(_secret_config())
    for secret in ("sk-embedding-secret", "ghp-super-secret", "Bearer top-secret"):
        assert secret not in text, f"{secret!r} leaked from config show"


def test_cli_config_show_json_never_leaks_secrets(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from spark import cli

    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    save_config(_secret_config(), cfg_path)
    result = CliRunner().invoke(
        cli.app, ["config", "show", "--json", "--config", str(cfg_path), "--workdir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.stdout
    for secret in ("sk-embedding-secret", "ghp-super-secret", "Bearer top-secret"):
        assert secret not in result.stdout
