"""Workspace plugin manifest and local install/uninstall.

This module deliberately does **not** execute plugin code. A plugin can declare
tools/hooks/commands/permissions, but execution remains in the existing hook
runtime (which has approval, timeout and audit). Keeping discovery/installation
separate avoids a second, unapproved code-execution path.

Layout::

    <workdir>/.spark/plugins/<plugin-name>/
      spark-plugin.json
      ... entry files ...

Legacy ``plugin.json`` manifests are read for compatibility, but new plugins
should use ``spark-plugin.json``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from spark.errors import PluginError
from spark.hooks import SHELL_METACHARACTERS, HookRegistry, ShellHook

MANIFEST_NAME = "spark-plugin.json"
LEGACY_MANIFEST_NAME = "plugin.json"
PLUGIN_ROOT = Path(".spark") / "plugins"
MAX_NAME = 64
NAME_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
TOOL_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
COMMAND_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

KNOWN_PERMISSIONS = frozenset(
    {
        "files:read",
        "files:write",
        "shell",
        "network",
        "hooks",
        "mcp",
    }
)

_IGNORE = shutil.ignore_patterns(
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "dist",
    "build",
)


class PluginHook(BaseModel):
    """A hook declaration; execution is wired through ``cfg.hooks`` later."""

    model_config = ConfigDict(extra="allow")

    event: str = "pre_tool"
    command: str
    args: list[str] = Field(default_factory=list)
    name: str = ""
    timeout_sec: int = Field(default=15, ge=1, le=300)
    on_deny: str = "block"

    @field_validator("event")
    @classmethod
    def _event(cls, value: str) -> str:
        if value not in HookRegistry.EVENTS:
            raise ValueError(f"unknown hook event: {value}")
        return value

    @field_validator("command")
    @classmethod
    def _command(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("hook command must not be empty")
        if any(ch in SHELL_METACHARACTERS for ch in value):
            raise ValueError("hook command must not contain shell metacharacters")
        return value

    @field_validator("args")
    @classmethod
    def _args(cls, value: list[str]) -> list[str]:
        for part in value:
            if any(ch in SHELL_METACHARACTERS for ch in part):
                raise ValueError("hook args must not contain shell metacharacters")
        return value

    @field_validator("on_deny")
    @classmethod
    def _on_deny(cls, value: str) -> str:
        if value not in {"warn", "block"}:
            raise ValueError("on_deny must be warn or block")
        return value


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    version: str = "0.0.0"
    description: str = ""
    entry: str = ""
    permissions: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    hooks: list[PluginHook] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not NAME_RE.fullmatch(value):
            raise ValueError(
                "name must match ^[a-z][a-z0-9._-]{0,63}$ "
                "(lowercase, no spaces/slashes)"
            )
        return value

    @field_validator("version")
    @classmethod
    def _version(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("version must not be empty")
        try:
            Version(value)
        except InvalidVersion as exc:
            raise ValueError(f"invalid version: {value}") from exc
        return value

    @field_validator("entry")
    @classmethod
    def _entry(cls, value: str) -> str:
        raw = value.strip().replace("\\", "/")
        if not raw:
            return ""
        if raw.startswith("/") or raw.startswith("../") or "/../" in raw:
            raise ValueError("entry must stay inside the plugin directory")
        if raw in {".", ".."}:
            raise ValueError("entry must be a file path, not a directory")
        return raw

    @field_validator("permissions")
    @classmethod
    def _permissions(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - KNOWN_PERMISSIONS)
        if unknown:
            raise ValueError(
                "unknown permissions: " + ", ".join(unknown)
                + " (known: " + ", ".join(sorted(KNOWN_PERMISSIONS)) + ")"
            )
        return list(dict.fromkeys(value))

    @field_validator("tools")
    @classmethod
    def _tools(cls, value: list[str]) -> list[str]:
        for item in value:
            if not TOOL_RE.fullmatch(item):
                raise ValueError(f"invalid tool name: {item}")
        return list(dict.fromkeys(value))

    @field_validator("commands")
    @classmethod
    def _commands(cls, value: list[str]) -> list[str]:
        for item in value:
            if not COMMAND_RE.fullmatch(item):
                raise ValueError(f"invalid command name: {item}")
        return list(dict.fromkeys(value))


#: A plugin subprocess gets a deliberately tiny environment. It must never see
#: provider API keys, SPARK_* overrides, cloud credentials or the user's shell
#: startup files. PATH is needed for shebangs (``/usr/bin/env python3``).
SAFE_ENV_KEYS = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "TMP",
    "TEMP",
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
)


@dataclass(frozen=True)
class PluginHookBinding:
    event: str
    plugin: str
    hook: ShellHook


@dataclass(frozen=True)
class PluginRecord:
    name: str
    path: Path
    manifest: PluginManifest | None = None
    error: str = ""
    legacy: bool = False


def record_to_dict(record: PluginRecord) -> dict[str, Any]:
    """Stable JSON shape for CLI/Web without leaking internal Path objects."""
    manifest = record.manifest
    return {
        "name": record.name,
        "version": manifest.version if manifest else "",
        "description": manifest.description if manifest else "",
        "entry": manifest.entry if manifest else "",
        "permissions": manifest.permissions if manifest else [],
        "tools": manifest.tools if manifest else [],
        "commands": manifest.commands if manifest else [],
        "hooks": [h.model_dump() for h in manifest.hooks] if manifest else [],
        "path": record.path.as_posix(),
        "legacy": record.legacy,
        "error": record.error,
    }


def sanitized_env() -> dict[str, str]:
    """Return only benign process variables for plugin subprocesses."""
    return {key: os.environ[key] for key in SAFE_ENV_KEYS if key in os.environ}


def _plugin_command(
    record: PluginRecord,
    hook: PluginHook,
    permissions: list[str],
    issues: list[str],
) -> str | None:
    if "shell" in permissions:
        return hook.command
    raw = hook.command.strip().replace("\\", "/")
    if raw.startswith("/") or raw.startswith("../") or "/../" in raw:
        issues.append(
            f"plugin {record.name}: hook 命令必须位于插件目录内（或声明 shell 权限）: {hook.command}"
        )
        return None
    target = (record.path / raw).resolve()
    try:
        target.relative_to(record.path.resolve())
    except ValueError:
        issues.append(f"plugin {record.name}: hook 命令逃出插件目录: {hook.command}")
        return None
    if not target.is_file():
        issues.append(f"plugin {record.name}: hook 命令不存在: {hook.command}")
        return None
    if os.name != "nt" and not os.access(target, os.X_OK):
        issues.append(
            f"plugin {record.name}: hook 命令不可执行（chmod +x）: {hook.command}"
        )
        return None
    return str(target)


def build_plugin_hooks(
    enabled: Sequence[str], workdir: Path
) -> tuple[list[PluginHookBinding], list[str]]:
    """Build executable hook bindings for explicitly enabled plugins.

    Deny-by-default: a plugin must be named in ``plugins.enabled`` *and* declare
    the ``hooks`` permission. Every hook then runs in its own subprocess with a
    timeout, no stdin inheritance and a sanitised environment. Issues are
    returned instead of raised so one bad plugin cannot stop the agent loop.
    """
    selected = {str(name).strip() for name in enabled if str(name).strip()}
    records = {record.name: record for record in scan_plugins(workdir)}
    bindings: list[PluginHookBinding] = []
    issues: list[str] = []
    for name in sorted(selected):
        record = records.get(name)
        if record is None:
            issues.append(f"plugin {name}: 未安装或清单不匹配")
            continue
        if record.manifest is None or record.error:
            issues.append(f"plugin {name}: {record.error or 'manifest 无效，已跳过'}")
            continue
        if record.legacy:
            issues.append(f"plugin {name}: 旧 plugin.json 已跳过，请迁移到 {MANIFEST_NAME}")
            continue
        permissions = record.manifest.permissions
        if "hooks" not in permissions:
            issues.append(f"plugin {name}: 未声明 hooks 权限，已拒绝加载 hook")
            continue
        for hook in record.manifest.hooks:
            command = _plugin_command(record, hook, permissions, issues)
            if command is None:
                continue
            bindings.append(
                PluginHookBinding(
                    event=hook.event,
                    plugin=name,
                    hook=ShellHook(
                        name=f"plugin:{name}:{hook.name or hook.event}",
                        command=[command, *hook.args],
                        cwd=record.path,
                        timeout=hook.timeout_sec,
                        on_deny=hook.on_deny,
                        env=sanitized_env(),
                    ),
                )
            )
    return bindings, issues


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def plugin_root(workdir: Path) -> Path:
    return Path(workdir).resolve() / PLUGIN_ROOT


def find_manifest(root: Path) -> Path | None:
    root = Path(root)
    for name in (MANIFEST_NAME, LEGACY_MANIFEST_NAME):
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginError(f"{path}: 不是合法 JSON（{exc}）") from exc
    if not isinstance(data, dict):
        raise PluginError(f"{path}: manifest 顶层必须是 JSON object")
    return data


def _validate_entry(root: Path, entry: str) -> None:
    if not entry:
        return
    target = (root / entry).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise PluginError(f"entry escapes plugin directory: {entry}") from exc
    if not target.is_file():
        raise PluginError(f"entry file does not exist: {entry}")


def load_manifest(
    path: Path,
    *,
    require_entry: bool = False,
    legacy: bool | None = None,
) -> PluginManifest:
    """Parse and validate a manifest file.

    ``legacy`` is inferred from the filename when omitted. Legacy
    ``plugin.json`` gets the same schema but defaults preserve old files.
    """
    path = Path(path)
    data = _load_json(path)
    if legacy is None:
        legacy = path.name == LEGACY_MANIFEST_NAME
    if legacy:
        data.setdefault("version", "0.0.0")
    try:
        manifest = PluginManifest.model_validate(data)
    except ValidationError as exc:
        messages = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )
        raise PluginError(f"{path}: manifest 无效：{messages}") from exc
    if require_entry:
        _validate_entry(path.parent, manifest.entry)
    return manifest


def validate_plugin(source: Path, *, require_entry: bool = True) -> PluginManifest:
    """Validate a plugin directory (or a manifest file)."""
    source = Path(source).expanduser().resolve()
    if source.is_file():
        manifest = load_manifest(source, require_entry=require_entry, legacy=None)
        _validate_entry(source.parent, manifest.entry)
        return manifest
    if not source.is_dir():
        raise PluginError(f"插件目录不存在: {source}")
    manifest_path = find_manifest(source)
    if manifest_path is None:
        raise PluginError(
            f"{source} 缺少 {MANIFEST_NAME}（旧格式可用 {LEGACY_MANIFEST_NAME}）"
        )
    return load_manifest(
        manifest_path,
        require_entry=require_entry,
        legacy=manifest_path.name == LEGACY_MANIFEST_NAME,
    )


def _reject_symlinks(source: Path) -> None:
    for item in source.rglob("*"):
        if item.is_symlink():
            raise PluginError(f"插件包含符号链接，已拒绝安装: {item}")


def scan_plugins(workdir: Path) -> list[PluginRecord]:
    root = plugin_root(workdir)
    if not root.is_dir():
        return []
    records: list[PluginRecord] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if entry.name.startswith(".") or entry.is_symlink():
            continue
        if not entry.is_dir():
            continue
        manifest_path = find_manifest(entry)
        if manifest_path is None:
            records.append(
                PluginRecord(
                    name=entry.name,
                    path=entry,
                    error=f"缺少 {MANIFEST_NAME}",
                )
            )
            continue
        legacy = manifest_path.name == LEGACY_MANIFEST_NAME
        try:
            manifest = load_manifest(
                manifest_path,
                require_entry=False,
                legacy=legacy,
            )
        except PluginError as exc:
            records.append(PluginRecord(name=entry.name, path=entry, error=str(exc), legacy=legacy))
            continue
        error = ""
        if manifest.entry:
            try:
                _validate_entry(entry, manifest.entry)
            except PluginError as exc:
                error = str(exc)
        if legacy and not error:
            error = f"旧格式 {LEGACY_MANIFEST_NAME}；建议迁移到 {MANIFEST_NAME}"
        records.append(
            PluginRecord(
                name=manifest.name,
                path=entry,
                manifest=manifest,
                error=error,
                legacy=legacy,
            )
        )
    return records


def install_local(source: Path, workdir: Path, *, force: bool = False) -> PluginRecord:
    """Copy a local plugin directory into ``.spark/plugins/<name>``.

    Existing installs are moved to ``.backups/`` rather than deleted. A staging
    directory is used so a failed copy never leaves a half-written plugin.
    """
    source = Path(source).expanduser().resolve()
    if source.is_symlink() or not source.is_dir():
        raise PluginError(f"插件源必须是本地目录: {source}")
    _reject_symlinks(source)
    manifest = validate_plugin(source, require_entry=True)
    root = plugin_root(workdir)
    destination = root / manifest.name
    if source == destination:
        raise PluginError("插件源就是目标目录，无需重复安装")
    if destination.exists() and not force:
        raise PluginError(f"插件已存在: {manifest.name}（重装请加 --force）")
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".staging-{manifest.name}-{uuid.uuid4().hex}"
    backup: Path | None = None
    try:
        shutil.copytree(source, staging, ignore=_IGNORE, symlinks=True)
        validate_plugin(staging, require_entry=True)
        if destination.exists():
            backup_dir = root / ".backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / f"{manifest.name}-{_utc_stamp()}"
            os.replace(destination, backup)
        os.replace(staging, destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if backup is not None and backup.exists():
            os.replace(backup, destination)
        raise
    return PluginRecord(
        name=manifest.name,
        path=destination,
        manifest=manifest,
        legacy=False,
    )


def remove_plugin(name: str, workdir: Path) -> Path:
    """Move a plugin to ``.trash/``; never delete user files in place."""
    name = name.strip()
    if not NAME_RE.fullmatch(name):
        raise PluginError(f"非法插件名: {name!r}")
    root = plugin_root(workdir)
    destination = root / name
    if not destination.is_dir():
        raise PluginError(f"插件不存在: {name}")
    trash = root / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    target = trash / f"{name}-{_utc_stamp()}"
    os.replace(destination, target)
    return target
