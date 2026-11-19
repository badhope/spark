from __future__ import annotations

import json
from pathlib import Path

import pytest

from spark.errors import PluginError
from spark.plugins import (
    install_local,
    load_manifest,
    remove_plugin,
    scan_plugins,
    validate_plugin,
)


def _write_plugin(root: Path, *, name: str = "demo", **extra: object) -> Path:
    source = root / "source"
    source.mkdir(parents=True)
    (source / "spark-plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.1.0",
                "description": "demo plugin",
                "entry": "run.py",
                "permissions": ["files:read"],
                **extra,
            }
        ),
        encoding="utf-8",
    )
    (source / "run.py").write_text("print('demo')\n", encoding="utf-8")
    return source


def test_manifest_defaults_and_validation(tmp_path: Path) -> None:
    source = _write_plugin(tmp_path)
    manifest = validate_plugin(source)
    assert manifest.name == "demo"
    assert manifest.version == "0.1.0"
    assert manifest.permissions == ["files:read"]
    assert manifest.hooks == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "Bad Name"),
        ("version", "not a version"),
        ("entry", "../escape.py"),
        ("permissions", ["root"]),
        ("tools", ["Bad Tool"]),
        ("commands", ["/bad"]),
    ],
)
def test_manifest_rejects_bad_fields(tmp_path: Path, field: str, value: object) -> None:
    source = _write_plugin(tmp_path)
    manifest_path = source / "spark-plugin.json"
    data = json.loads(manifest_path.read_text())
    data[field] = value
    manifest_path.write_text(json.dumps(data))
    with pytest.raises(PluginError):
        validate_plugin(source)


def test_manifest_rejects_unknown_hook_event(tmp_path: Path) -> None:
    source = _write_plugin(tmp_path, hooks=[{"event": "unknown", "command": "echo"}])
    with pytest.raises(PluginError):
        validate_plugin(source)


def test_manifest_rejects_shell_metacharacters(tmp_path: Path) -> None:
    source = _write_plugin(tmp_path, hooks=[{"event": "pre_tool", "command": "echo | rm"}])
    with pytest.raises(PluginError):
        validate_plugin(source)


def test_install_local_copies_and_scans(tmp_path: Path) -> None:
    source = _write_plugin(tmp_path)
    workdir = tmp_path / "work"
    result = install_local(source, workdir)
    assert result.name == "demo"
    assert result.path == workdir / ".spark" / "plugins" / "demo"
    assert (result.path / "run.py").is_file()
    records = scan_plugins(workdir)
    assert len(records) == 1
    assert records[0].name == "demo"
    assert records[0].error == ""


def test_install_local_refuses_existing_without_force(tmp_path: Path) -> None:
    source = _write_plugin(tmp_path)
    workdir = tmp_path / "work"
    install_local(source, workdir)
    with pytest.raises(PluginError):
        install_local(source, workdir)


def test_install_local_force_moves_old_to_backup(tmp_path: Path) -> None:
    source = _write_plugin(tmp_path)
    workdir = tmp_path / "work"
    install_local(source, workdir)
    (source / "run.py").write_text("print('v2')\n")
    result = install_local(source, workdir, force=True)
    assert (result.path / "run.py").read_text() == "print('v2')\n"
    backups = list((workdir / ".spark" / "plugins" / ".backups").iterdir())
    assert len(backups) == 1
    assert backups[0].is_dir()


def test_install_local_refuses_symlink(tmp_path: Path) -> None:
    source = _write_plugin(tmp_path)
    (source / "link.py").symlink_to(source / "run.py")
    with pytest.raises(PluginError):
        install_local(source, tmp_path / "work")


def test_remove_plugin_moves_to_trash(tmp_path: Path) -> None:
    source = _write_plugin(tmp_path)
    workdir = tmp_path / "work"
    install_local(source, workdir)
    trash = remove_plugin("demo", workdir)
    assert not (workdir / ".spark" / "plugins" / "demo").exists()
    assert (trash / "run.py").is_file()


def test_scan_reports_missing_and_broken_manifest(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    bare = workdir / ".spark" / "plugins" / "bare"
    bare.mkdir(parents=True)
    broken = workdir / ".spark" / "plugins" / "broken"
    broken.mkdir()
    (broken / "spark-plugin.json").write_text("{oops")
    records = {r.name: r for r in scan_plugins(workdir)}
    assert "缺少" in records["bare"].error
    assert "JSON" in records["broken"].error


def test_legacy_plugin_json_is_read_with_warning(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    entry = workdir / ".spark" / "plugins" / "legacy"
    entry.mkdir(parents=True)
    (entry / "plugin.json").write_text(
        json.dumps({"name": "legacy", "description": "old", "entry": "run.py"})
    )
    (entry / "run.py").write_text("print(1)")
    records = scan_plugins(workdir)
    assert records[0].name == "legacy"
    assert records[0].legacy
    assert "旧格式" in records[0].error
    manifest = load_manifest(entry / "plugin.json", legacy=True)
    assert manifest.version == "0.0.0"


def test_cli_plugin_lifecycle(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from spark.cli import app

    source = _write_plugin(tmp_path)
    workdir = tmp_path / "work"
    runner = CliRunner()

    installed = runner.invoke(
        app, ["plugins", "install", str(source), "--workdir", str(workdir)]
    )
    assert installed.exit_code == 0, installed.output
    listed = runner.invoke(app, ["plugins", "list", "--workdir", str(workdir)])
    assert listed.exit_code == 0
    assert "demo" in listed.output
    removed = runner.invoke(app, ["plugins", "remove", "demo", "--workdir", str(workdir)])
    assert removed.exit_code == 0
    assert not (workdir / ".spark" / "plugins" / "demo").exists()
