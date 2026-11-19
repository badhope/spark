from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from spark.errors import ConfigError
from spark.ide import (
    install_jetbrains,
    install_neovim,
    install_zed,
    jetbrains_settings_path,
    neovim_config_path,
    zed_settings_path,
)


def test_install_zed_creates_settings_and_backs_up_existing(tmp_path: Path) -> None:
    settings = tmp_path / "zed" / "settings.json"
    result = install_zed(tmp_path, settings_path=settings)
    assert result.changed
    assert result.backup is None
    data = json.loads(settings.read_text())
    assert data["agent"]["custom_servers"]["spark"]["args"][0] == "acp"
    assert str(tmp_path.resolve()) in data["agent"]["custom_servers"]["spark"]["args"]


def test_install_zed_merges_without_dropping_other_keys(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "theme": "One Dark",
                "agent": {"custom_servers": {"other": {"command": "other"}}},
            }
        )
    )
    result = install_zed(tmp_path, settings_path=settings)
    data = json.loads(settings.read_text())
    assert result.backup is not None
    assert data["theme"] == "One Dark"
    assert data["agent"]["custom_servers"]["other"]["command"] == "other"
    assert data["agent"]["custom_servers"]["spark"]["command"] == "spark"


def test_install_zed_is_idempotent(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    first = install_zed(tmp_path, settings_path=settings)
    second = install_zed(tmp_path, settings_path=settings)
    assert first.changed
    assert not second.changed
    assert second.backup is None


def test_install_zed_refuses_invalid_json(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text("{ broken")
    with pytest.raises(ConfigError):
        install_zed(tmp_path, settings_path=settings)
    assert settings.read_text() == "{ broken"


def test_install_zed_force_recovers_invalid_json(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text("{ broken")
    result = install_zed(tmp_path, settings_path=settings, force=True)
    assert result.backup is not None
    assert result.backup.read_text() == "{ broken"
    assert json.loads(settings.read_text())["agent"]["custom_servers"]["spark"]["command"] == "spark"


def test_zed_settings_path_platform(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("spark.ide.platform.system", lambda: "Windows")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert zed_settings_path() == tmp_path / "Zed" / "settings.json"


# --- JetBrains ACP ---------------------------------------------------------


def test_install_jetbrains_creates_native_acp_config(tmp_path: Path) -> None:
    settings = tmp_path / "jetbrains" / "acp.json"
    result = install_jetbrains(tmp_path, settings_path=settings)
    assert result.target == "jetbrains"
    assert result.changed
    assert result.backup is None
    data = json.loads(settings.read_text())
    server = data["agent_servers"]["Spark"]
    assert server["command"] == "spark"
    assert server["args"][0] == "acp"
    assert str(tmp_path.resolve()) in server["args"]


def test_install_jetbrains_merges_without_dropping_other_agents(tmp_path: Path) -> None:
    settings = tmp_path / "acp.json"
    settings.write_text(
        json.dumps(
            {
                "default_mcp_settings": {"use_idea_mcp": True},
                "agent_servers": {"other": {"command": "other", "args": ["acp"]}},
            }
        )
    )
    result = install_jetbrains(tmp_path, settings_path=settings)
    data = json.loads(settings.read_text())
    assert result.backup is not None
    assert data["default_mcp_settings"]["use_idea_mcp"] is True
    assert data["agent_servers"]["other"]["command"] == "other"
    assert data["agent_servers"]["Spark"]["command"] == "spark"


def test_install_jetbrains_is_idempotent(tmp_path: Path) -> None:
    settings = tmp_path / "acp.json"
    first = install_jetbrains(tmp_path, settings_path=settings)
    second = install_jetbrains(tmp_path, settings_path=settings)
    assert first.changed
    assert not second.changed
    assert second.backup is None


def test_install_jetbrains_refuses_invalid_json(tmp_path: Path) -> None:
    settings = tmp_path / "acp.json"
    settings.write_text("{ broken")
    with pytest.raises(ConfigError):
        install_jetbrains(tmp_path, settings_path=settings)
    assert settings.read_text() == "{ broken"


def test_install_jetbrains_force_recovers_invalid_json(tmp_path: Path) -> None:
    settings = tmp_path / "acp.json"
    settings.write_text("{ broken")
    result = install_jetbrains(tmp_path, settings_path=settings, force=True)
    assert result.backup is not None
    assert result.backup.read_text() == "{ broken"
    assert json.loads(settings.read_text())["agent_servers"]["Spark"]["command"] == "spark"


def test_jetbrains_settings_path(tmp_path: Path) -> None:
    assert jetbrains_settings_path(tmp_path) == tmp_path / ".jetbrains" / "acp.json"


# --- Neovim / CodeCompanion ACP --------------------------------------------


def test_install_neovim_writes_compilable_lua(tmp_path: Path) -> None:
    settings = tmp_path / "nvim" / "spark_acp.lua"
    result = install_neovim(tmp_path, settings_path=settings)
    assert result.target == "neovim"
    assert result.changed
    assert result.snippet["usage"] == 'require("spark_acp").setup()'
    text = settings.read_text()
    assert "-- Generated by `spark ide install neovim`" in text
    assert '"spark", "acp", "--workdir"' in text
    if shutil.which("luac"):
        subprocess.run(["luac", "-p", str(settings)], check=True)


def test_install_neovim_refuses_foreign_module(tmp_path: Path) -> None:
    settings = tmp_path / "spark_acp.lua"
    settings.write_text("return { mine = true }\n")
    with pytest.raises(ConfigError):
        install_neovim(tmp_path, settings_path=settings)
    assert settings.read_text() == "return { mine = true }\n"


def test_install_neovim_force_backs_up_foreign_module(tmp_path: Path) -> None:
    settings = tmp_path / "spark_acp.lua"
    settings.write_text("return { mine = true }\n")
    result = install_neovim(tmp_path, settings_path=settings, force=True)
    assert result.backup is not None
    assert result.backup.read_text() == "return { mine = true }\n"
    assert "-- Generated by `spark ide install neovim`" in settings.read_text()


def test_neovim_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("spark.ide.platform.system", lambda: "Windows")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert neovim_config_path() == tmp_path / "nvim" / "lua" / "spark_acp.lua"
