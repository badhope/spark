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


# --- W4-b: permission gate and subprocess hook execution -------------------


def test_build_hooks_requires_explicit_enable(tmp_path: Path) -> None:
    from spark.plugins import build_plugin_hooks

    source = _write_plugin(tmp_path, hooks=[{"event": "pre_tool", "command": "run.py"}])
    workdir = tmp_path / "work"
    install_local(source, workdir)
    bindings, issues = build_plugin_hooks([], workdir)
    assert bindings == []
    assert issues == []
    assert build_plugin_hooks(["missing"], workdir)[1]


def test_build_hooks_rejects_missing_hooks_permission(tmp_path: Path) -> None:
    from spark.plugins import build_plugin_hooks

    source = _write_plugin(
        tmp_path,
        permissions=["files:read"],
        hooks=[{"event": "pre_tool", "command": "run.py"}],
    )
    workdir = tmp_path / "work"
    install_local(source, workdir)
    bindings, issues = build_plugin_hooks(["demo"], workdir)
    assert bindings == []
    assert any("hooks 权限" in issue for issue in issues)


def test_build_hooks_rejects_escape_command(tmp_path: Path) -> None:
    from spark.plugins import build_plugin_hooks

    source = _write_plugin(
        tmp_path,
        permissions=["hooks"],
        hooks=[{"event": "pre_tool", "command": "../outside.sh"}],
    )
    workdir = tmp_path / "work"
    install_local(source, workdir)
    bindings, issues = build_plugin_hooks(["demo"], workdir)
    assert bindings == []
    assert any("逃出" in issue or "位于插件目录" in issue for issue in issues)


def test_plugin_hook_runs_with_sanitized_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from spark.hooks import HookRegistry
    from spark.plugins import build_plugin_hooks

    source = _write_plugin(
        tmp_path,
        permissions=["hooks"],
        hooks=[{"event": "pre_tool", "command": "hook.py"}],
    )
    (source / "hook.py").write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'TEST_SECRET', 'SPARK_HOME'):\n"
        "    if os.environ.get(key):\n"
        "        print('leaked: ' + key)\n"
        "        sys.exit(1)\n"
        "sys.exit(0)\n"
    )
    (source / "hook.py").chmod(0o755)
    workdir = tmp_path / "work"
    install_local(source, workdir)
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("TEST_SECRET", "secret")
    bindings, issues = build_plugin_hooks(["demo"], workdir)
    assert issues == []
    assert len(bindings) == 1
    registry = HookRegistry()
    registry.register(bindings[0].event, bindings[0].hook)
    decision = registry.notify("pre_tool", {"tool": "read_file"})
    assert decision.allow, decision.reason


def test_sanitized_env_excludes_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    from spark.plugins import sanitized_env

    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = sanitized_env()
    assert "OPENAI_API_KEY" not in env
    assert env["PATH"] == "/usr/bin"


def test_project_config_cannot_enable_plugins() -> None:
    from spark.config import _sanitize_project_config

    clean = _sanitize_project_config(
        {"plugins": {"enabled": ["evil"]}, "hooks": [{"event": "pre_tool"}]}
    )
    assert "plugins" not in clean
    assert "hooks" not in clean


def test_plugins_config_roundtrip(tmp_path: Path) -> None:
    from spark.config import SparkConfig, load_config, save_config

    cfg = SparkConfig()
    cfg.plugins.enabled = ["demo"]
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    text = path.read_text()
    assert "[plugins]" in text
    loaded = load_config(config_path=path, workdir=tmp_path)
    assert loaded.plugins.enabled == ["demo"]


def test_plugin_hook_crash_becomes_deny(tmp_path: Path) -> None:
    from spark.hooks import HookRegistry
    from spark.plugins import build_plugin_hooks

    source = _write_plugin(
        tmp_path,
        permissions=["hooks"],
        hooks=[{"event": "pre_tool", "command": "crash.sh"}],
    )
    (source / "crash.sh").write_text("#!/bin/sh\nexit 7\n")
    (source / "crash.sh").chmod(0o755)
    workdir = tmp_path / "work"
    install_local(source, workdir)
    bindings, issues = build_plugin_hooks(["demo"], workdir)
    assert issues == []
    registry = HookRegistry()
    registry.register(bindings[0].event, bindings[0].hook)
    decision = registry.notify("pre_tool", {"tool": "read_file"})
    assert not decision.allow
    assert "plugin:demo" in decision.reason


def test_plugin_enable_preserves_other_config(tmp_path: Path) -> None:
    import tomlkit

    from spark.cli import _set_plugin_enabled

    path = tmp_path / "config.toml"
    path.write_text('[provider]\nname = "mock"\n\n[plugins]\nenabled = ["old"]\n')
    _set_plugin_enabled("demo", True, path)
    doc = tomlkit.parse(path.read_text())
    assert doc["provider"]["name"] == "mock"
    assert doc["plugins"]["enabled"] == ["old", "demo"]
    _set_plugin_enabled("old", False, path)
    assert doc["plugins"]["enabled"] == ["old", "demo"]  # this doc is a snapshot
    reloaded = tomlkit.parse(path.read_text())
    assert reloaded["plugins"]["enabled"] == ["demo"]


def test_agent_loop_registers_enabled_plugin_hook(tmp_path: Path) -> None:
    from spark.config import SparkConfig
    from spark.core.loop import AgentLoop
    from spark.sandbox import WorkdirSandbox
    from spark.store import SessionStore
    from spark.tools.registry import ToolContext, ToolRegistry

    source = _write_plugin(
        tmp_path,
        permissions=["hooks"],
        hooks=[{"event": "pre_tool", "command": "hook.sh"}],
    )
    (source / "hook.sh").write_text("#!/bin/sh\nexit 0\n")
    (source / "hook.sh").chmod(0o755)
    workdir = tmp_path / "work"
    install_local(source, workdir)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.plugins.enabled = ["demo"]
    ctx = ToolContext(sandbox=WorkdirSandbox(workdir), config=cfg)
    store = SessionStore(tmp_path / "w4b.db")
    sid = store.create_session(workdir, "mock")
    loop = AgentLoop(
        workdir=workdir,
        cfg=cfg,
        provider=None,
        registry=ToolRegistry(ctx),
        store=store,
        session_id=sid,
    )
    names = [hook.name for hook in loop.hooks._hooks["pre_tool"]]
    assert "plugin:demo:pre_tool" in names
    assert loop.plugin_hook_issues == []
    store.close()


# --- W4-c: plugin tools -----------------------------------------------------


def test_manifest_accepts_tool_specs(tmp_path: Path) -> None:
    source = _write_plugin(
        tmp_path,
        permissions=["tools"],
        tools=[
            "declared_only",
            {
                "name": "hello",
                "description": "say hello",
                "command": "tool.py",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
        ],
    )
    manifest = validate_plugin(source)
    assert manifest.tools[0] == "declared_only"
    spec = manifest.tools[1]
    assert not isinstance(spec, str)
    assert spec.name == "hello"
    assert spec.parameters["type"] == "object"


def test_build_tools_requires_permission(tmp_path: Path) -> None:
    from spark.plugins import build_plugin_tools

    source = _write_plugin(
        tmp_path,
        permissions=["files:read"],
        tools=[{"name": "hello", "command": "tool.py"}],
    )
    workdir = tmp_path / "work"
    install_local(source, workdir)
    schemas, handlers, issues = build_plugin_tools(["demo"], workdir)
    assert schemas == [] and handlers == {}
    assert any("tools 权限" in issue for issue in issues)


def test_plugin_tool_runs_with_json_protocol(tmp_path: Path) -> None:
    from spark.plugins import build_plugin_tools

    source = _write_plugin(
        tmp_path,
        permissions=["tools"],
        tools=[
            {
                "name": "hello",
                "description": "echo a name",
                "command": "tool.py",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            }
        ],
    )
    (source / "tool.py").write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "print(json.dumps({'ok': True, 'payload': {"
        "'tool': payload['tool'], "
        "'echo': payload['arguments'].get('name')}}))\n"
    )
    (source / "tool.py").chmod(0o755)
    workdir = tmp_path / "work"
    install_local(source, workdir)
    schemas, handlers, issues = build_plugin_tools(["demo"], workdir)
    assert issues == []
    alias = "plugin__demo__hello"
    assert [item["function"]["name"] for item in schemas] == [alias]
    assert schemas[0]["function"]["parameters"]["required"] == ["name"]
    result = handlers[alias]({"name": "world"})
    assert result.ok
    assert result.payload == {"tool": "hello", "echo": "world"}


def test_registry_exposes_and_runs_plugin_tool(tmp_path: Path) -> None:
    import asyncio

    from spark.config import SparkConfig
    from spark.models import ToolCall
    from spark.plugins import build_plugin_tools
    from spark.sandbox import WorkdirSandbox
    from spark.tools.registry import ToolContext, ToolRegistry

    source = _write_plugin(
        tmp_path,
        permissions=["tools"],
        tools=[
            {
                "name": "hello",
                "description": "echo a name",
                "command": "tool.py",
                "parameters": {"type": "object", "properties": {"name": {"type": "string"}}},
            }
        ],
    )
    (source / "tool.py").write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "print(json.dumps({'ok': True, 'payload': {'echo': payload['arguments']['name']}}))\n"
    )
    (source / "tool.py").chmod(0o755)
    workdir = tmp_path / "work"
    install_local(source, workdir)
    schemas, handlers, issues = build_plugin_tools(["demo"], workdir)
    assert issues == []
    cfg = SparkConfig()
    registry = ToolRegistry(
        ToolContext(sandbox=WorkdirSandbox(workdir), config=cfg)
    )
    registry.register_plugin_tools(schemas, handlers)
    names = [s["function"]["name"] for s in registry.schemas()]
    assert "plugin__demo__hello" in names
    result = asyncio.run(
        registry.execute(
            ToolCall(id="x", name="plugin__demo__hello", arguments={"name": "world"})
        )
    )
    assert result.ok
    assert result.payload == {"echo": "world"}


def test_agent_loop_registers_enabled_plugin_tool(tmp_path: Path) -> None:
    from spark.config import SparkConfig
    from spark.core.loop import AgentLoop
    from spark.sandbox import WorkdirSandbox
    from spark.store import SessionStore
    from spark.tools.registry import ToolContext, ToolRegistry

    source = _write_plugin(
        tmp_path,
        permissions=["tools"],
        tools=[{"name": "hello", "command": "tool.py"}],
    )
    (source / "tool.py").write_text("#!/usr/bin/env python3\nprint('{}')\n")
    (source / "tool.py").chmod(0o755)
    workdir = tmp_path / "work"
    install_local(source, workdir)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.plugins.enabled = ["demo"]
    ctx = ToolContext(sandbox=WorkdirSandbox(workdir), config=cfg)
    store = SessionStore(tmp_path / "w4c.db")
    sid = store.create_session(workdir, "mock")
    loop = AgentLoop(
        workdir=workdir,
        cfg=cfg,
        provider=None,
        registry=ToolRegistry(ctx),
        store=store,
        session_id=sid,
    )
    names = [s["function"]["name"] for s in loop.registry.schemas()]
    assert "plugin__demo__hello" in names
    assert loop.plugin_hook_issues == []
    store.close()


def test_install_git_rejects_local_and_unsafe_refs(tmp_path: Path) -> None:
    from spark.plugins import install_git

    workdir = tmp_path / "work"
    with pytest.raises(PluginError, match="git 安装"):
        install_git(str(tmp_path), workdir)
    with pytest.raises(PluginError, match="无效的 ref"):
        install_git("https://example.com/demo.git", workdir, ref="-evil")
    with pytest.raises(PluginError, match="subdir"):
        install_git("https://example.com/demo.git", workdir, subdir="../escape")
    with pytest.raises(PluginError, match="git 安装"):
        install_git("file:///tmp/demo", workdir)


def test_registry_search_and_missing_install(tmp_path: Path, monkeypatch) -> None:
    from spark.plugins import (
        PluginError,
        install_from_registry,
        load_registry,
        search_registry,
    )

    index = tmp_path / "registry.json"
    index.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "demo",
                        "version": "1.2.0",
                        "description": "demo plugin",
                        "url": "https://example.com/demo.git",
                    }
                ]
            }
        )
    )
    monkeypatch.delenv("SPARK_PLUGIN_REGISTRY", raising=False)
    registry = load_registry(index)
    assert registry.plugins[0].name == "demo"
    assert [e.name for e in search_registry("DEM", index)] == ["demo"]
    assert search_registry("missing", index) == []
    with pytest.raises(PluginError, match="没有插件"):
        install_from_registry("missing", tmp_path / "work", source=index)


def test_registry_rejects_bad_entries(tmp_path: Path) -> None:
    from spark.plugins import PluginError, load_registry

    index = tmp_path / "registry.json"
    index.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "demo",
                        "version": "not-a-version",
                        "url": "https://example.com/demo.git",
                    }
                ]
            }
        )
    )
    with pytest.raises(PluginError, match="校验失败"):
        load_registry(index)


def test_registry_update_detection(tmp_path: Path) -> None:
    from spark.plugins import PluginError, check_updates, upgrade_from_registry

    workdir = tmp_path / "work"
    install_local(_write_plugin(tmp_path, version="0.1.0"), workdir)
    index = tmp_path / "registry.json"
    index.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "demo",
                        "version": "0.2.0",
                        "description": "demo",
                        "url": "https://example.com/demo.git",
                    }
                ]
            }
        )
    )
    updates = check_updates(workdir, index)
    assert len(updates) == 1
    assert updates[0].installed == "0.1.0"
    assert updates[0].available == "0.2.0"
    with pytest.raises(PluginError, match="未安装或"):
        upgrade_from_registry("missing", workdir, source=index)
