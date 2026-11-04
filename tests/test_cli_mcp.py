"""spark mcp 子命令（插件市场 v1）：list / search / add / remove。"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from spark import cli
from spark.config import McpServerConfig, SparkConfig, load_config, save_config

runner = CliRunner()


@pytest.fixture()
def cfg_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cli, "default_home", lambda: home)
    monkeypatch.setenv("SPARK_HOME", str(home))
    return home


def _seed_cfg(home: Path, servers: list[McpServerConfig]) -> None:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.mcp_servers = servers
    save_config(cfg, home / "config.toml")


def _search_patch(monkeypatch: pytest.MonkeyPatch, items: list[dict], source: str = "live"):
    from spark import cli

    monkeypatch.setattr(
        "spark.mcp_registry.search_servers",
        lambda *a, **k: {"items": items, "source": source, "error": None, "filtered_out": 0},
    )
    return cli


_REG_ITEM = {
    "name": "io.example/notes",
    "title": "Notes",
    "description": "note taking server",
    "transport": "stdio",
    "suggested_command": "npx",
    "suggested_args": ["-y", "notes-mcp"],
    "required_env": [{"name": "NOTES_TOKEN", "required": True, "secret": True}],
    "remote_url": None,
    "china": {"available": True},
}


def test_mcp_search_prints_registry_results(cfg_home, monkeypatch) -> None:
    _search_patch(monkeypatch, [_REG_ITEM])
    r = runner.invoke(cli.app, ["mcp", "search", "notes"])
    assert r.exit_code == 0, r.stdout
    assert "io.example/notes" in r.stdout
    assert "npx" in r.stdout


def test_mcp_add_requires_missing_env(cfg_home, monkeypatch) -> None:
    _search_patch(monkeypatch, [_REG_ITEM])
    _seed_cfg(cfg_home, [])
    r = runner.invoke(cli.app, ["mcp", "add", "io.example/notes"])
    assert r.exit_code == 2
    assert "NOTES_TOKEN" in r.stdout + (r.stderr or "")


def test_mcp_add_and_remove_roundtrip(cfg_home, monkeypatch) -> None:
    _search_patch(monkeypatch, [_REG_ITEM])
    _seed_cfg(cfg_home, [])
    r = runner.invoke(
        cli.app,
        ["mcp", "add", "io.example/notes", "--env", "NOTES_TOKEN=t0k3n", "--readonly"],
    )
    assert r.exit_code == 0, r.stdout + (r.stderr or "")
    cfg = load_config(config_path=cfg_home / "config.toml", workdir=cfg_home)
    assert len(cfg.mcp_servers) == 1
    s = cfg.mcp_servers[0]
    assert s.name == "io.example/notes"
    assert s.command == "npx" and s.args == ["-y", "notes-mcp"]
    assert s.env["NOTES_TOKEN"] == "t0k3n"
    assert s.readonly_tools == ["*"]
    # list 能看到
    r = runner.invoke(cli.app, ["mcp", "list"])
    assert "io.example/notes" in r.stdout
    # 重复添加被拒
    r = runner.invoke(
        cli.app, ["mcp", "add", "io.example/notes", "--env", "NOTES_TOKEN=x"]
    )
    assert r.exit_code == 2
    # remove
    r = runner.invoke(cli.app, ["mcp", "remove", "io.example/notes"])
    assert r.exit_code == 0, r.stdout
    cfg = load_config(config_path=cfg_home / "config.toml", workdir=cfg_home)
    assert cfg.mcp_servers == []


def test_mcp_add_remote_server(cfg_home, monkeypatch) -> None:
    item = {
        **_REG_ITEM,
        "name": "io.example/remote",
        "transport": "remote",
        "suggested_command": None,
        "suggested_args": [],
        "remote_url": "https://mcp.example.com/mcp",
        "required_env": [],
    }
    _search_patch(monkeypatch, [item])
    _seed_cfg(cfg_home, [])
    r = runner.invoke(cli.app, ["mcp", "add", "io.example/remote"])
    assert r.exit_code == 0, r.stdout + (r.stderr or "")
    cfg = load_config(config_path=cfg_home / "config.toml", workdir=cfg_home)
    assert cfg.mcp_servers[0].transport == "streamable_http"
    assert cfg.mcp_servers[0].http is not None
    assert cfg.mcp_servers[0].http.url == "https://mcp.example.com/mcp"


def test_mcp_add_unknown_name_fails(cfg_home, monkeypatch) -> None:
    _search_patch(monkeypatch, [])
    _seed_cfg(cfg_home, [])
    r = runner.invoke(cli.app, ["mcp", "add", "does.not/exist"])
    assert r.exit_code == 2
