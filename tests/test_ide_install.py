from __future__ import annotations

import json
from pathlib import Path

import pytest

from spark.errors import ConfigError
from spark.ide import install_zed, zed_settings_path


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
