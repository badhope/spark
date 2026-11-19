"""Workspace plugin manifest and local install/uninstall.

Installation alone never executes plugin code. Hook and tool execution is
opt-in (``plugins.enabled`` + the relevant permission), runs in a bounded
subprocess with a sanitised environment, and never bypasses the normal tool
approval path. Keeping discovery/installation separate avoids a second,
unapproved code-execution path.

Layout::

    <workdir>/.spark/plugins/<plugin-name>/
      spark-plugin.json
      ... entry files ...

Legacy ``plugin.json`` manifests are read for compatibility, but new plugins
should use ``spark-plugin.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from spark.errors import PluginError
from spark.hooks import SHELL_METACHARACTERS, HookRegistry, ShellHook
from spark.models import ToolResult
from spark.proc import run_bounded

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
        "tools",
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


class PluginToolSpec(BaseModel):
    """A plugin tool runner plus the JSON Schema exposed to the model."""

    model_config = ConfigDict(extra="allow")

    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    command: str
    args: list[str] = Field(default_factory=list)
    timeout_sec: int = Field(default=30, ge=1, le=300)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not TOOL_RE.fullmatch(value):
            raise ValueError(f"invalid tool name: {value}")
        return value

    @field_validator("description")
    @classmethod
    def _description(cls, value: str) -> str:
        return value.strip()[:4000]

    @field_validator("parameters")
    @classmethod
    def _parameters(cls, value: dict[str, Any]) -> dict[str, Any]:
        value = dict(value or {})
        kind = value.get("type")
        if kind not in (None, "object"):
            raise ValueError("tool parameters must be a JSON object schema")
        value.setdefault("type", "object")
        value.setdefault("properties", {})
        return value

    @field_validator("command")
    @classmethod
    def _command(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("tool command must not be empty")
        if any(ch in SHELL_METACHARACTERS for ch in value):
            raise ValueError("tool command must not contain shell metacharacters")
        return value

    @field_validator("args")
    @classmethod
    def _args(cls, value: list[str]) -> list[str]:
        for part in value:
            if any(ch in SHELL_METACHARACTERS for ch in part):
                raise ValueError("tool args must not contain shell metacharacters")
        return value


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    version: str = "0.0.0"
    description: str = ""
    entry: str = ""
    permissions: list[str] = Field(default_factory=list)
    tools: list[str | PluginToolSpec] = Field(default_factory=list)
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
    def _tools(cls, value: list[str | PluginToolSpec]) -> list[str | PluginToolSpec]:
        seen: set[str] = set()
        out: list[str | PluginToolSpec] = []
        for raw in value:
            item: str | PluginToolSpec
            if isinstance(raw, str):
                if not TOOL_RE.fullmatch(raw):
                    raise ValueError(f"invalid tool name: {raw}")
                item = raw
                name = raw
            elif isinstance(raw, dict):
                item = PluginToolSpec.model_validate(raw)
                name = item.name
            else:
                item = raw
                name = raw.name
            if name in seen:
                continue
            seen.add(name)
            out.append(item)
        return out

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


def enabled_plugin_names() -> set[str]:
    """Read ``[plugins].enabled`` without loading the full SparkConfig."""
    import tomlkit

    from spark.config import default_home

    path = default_home() / "config.toml"
    if not path.is_file():
        return set()
    try:
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a broken config must not hide plugins
        return set()
    table = doc.get("plugins")
    if table is None or not hasattr(table, "get"):
        return set()
    names = table.get("enabled")
    if not isinstance(names, list):
        return set()
    return {str(name) for name in names}


def set_plugin_enabled(
    name: str, enabled: bool, *, path: Path | None = None
) -> Path:
    """Add/remove a plugin from the global ``[plugins].enabled`` array.

    Edits only the ``plugins`` table so unrelated config (including secrets)
    stays byte-for-byte as the user wrote it.
    """
    import tomlkit

    from spark.config import ConfigError, default_home

    if not NAME_RE.fullmatch(name):
        raise ConfigError(f"非法插件名: {name}")
    target = path or (default_home() / "config.toml")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        try:
            doc = tomlkit.parse(target.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - keep the user's file intact
            raise ConfigError(f"配置文件无法解析：{target}") from exc
    else:
        doc = tomlkit.document()
    table = doc.get("plugins")
    if table is None:
        table = tomlkit.table()
        doc["plugins"] = table
    if not hasattr(table, "get"):
        raise ConfigError("[plugins] 必须是表")
    names = table.get("enabled")
    if names is None:
        names = tomlkit.array()
        table["enabled"] = names
    if not isinstance(names, list):
        raise ConfigError("[plugins] enabled 必须是数组")
    current = [str(item) for item in names]
    if enabled:
        if name not in current:
            current.append(name)
    else:
        current = [item for item in current if item != name]
    new_names = tomlkit.array()
    for item in current:
        new_names.append(item)
    table["enabled"] = new_names
    target.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return target


def sanitized_env() -> dict[str, str]:
    """Return only benign process variables for plugin subprocesses."""
    return {key: os.environ[key] for key in SAFE_ENV_KEYS if key in os.environ}


def _plugin_command(
    record: PluginRecord,
    hook: PluginHook | PluginToolSpec,
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
        if not record.manifest.hooks:
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


def plugin_tool_alias(plugin: str, tool: str) -> str:
    raw = f"plugin__{plugin}__{tool}".lower()
    safe = re.sub(r"[^a-z0-9_]", "_", raw)
    if len(safe) <= 64:
        return safe
    digest = hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()[:8]
    return f"{safe[:55]}_{digest}"


def _run_plugin_tool(
    record: PluginRecord,
    spec: PluginToolSpec,
    command: str,
    arguments: dict[str, Any],
    max_output_chars: int,
) -> ToolResult:
    payload = json.dumps({"tool": spec.name, "arguments": arguments}, ensure_ascii=False)
    try:
        result = run_bounded(
            [command, *spec.args],
            cwd=record.path,
            env=sanitized_env(),
            timeout=spec.timeout_sec,
            input_text=payload,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return ToolResult(ok=False, payload={"error": f"plugin tool failed to start: {exc}"})
    if result.timed_out:
        return ToolResult(
            ok=False,
            payload={"error": f"plugin tool timed out after {spec.timeout_sec}s"},
        )
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if result.returncode != 0:
        detail = (stderr or stdout)[-max_output_chars:]
        return ToolResult(
            ok=False,
            payload={
                "error": f"plugin tool exited with {result.returncode}",
                "detail": detail,
            },
        )
    if not stdout:
        return ToolResult(ok=True, payload={})
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return ToolResult(ok=True, payload={"output": stdout[:max_output_chars]})
    if isinstance(parsed, dict):
        if "ok" in parsed:
            body = parsed.get("payload")
            if not isinstance(body, dict):
                body = {"output": body}
            return ToolResult(ok=bool(parsed.get("ok")), payload=body)
        return ToolResult(ok=True, payload=parsed)
    return ToolResult(ok=True, payload={"output": parsed})


def build_plugin_tools(
    enabled: Sequence[str],
    workdir: Path,
    *,
    max_output_chars: int = 8000,
) -> tuple[
    list[dict[str, Any]],
    dict[str, Callable[[dict[str, Any]], ToolResult]],
    list[str],
]:
    """Build model-facing schemas and handlers for explicit plugin tools.

    A plugin must be enabled and declare the ``tools`` permission. Its tool
    names are namespaced (``plugin__<plugin>__<tool>``) so they cannot shadow
    built-ins; execution stays a bounded subprocess with sanitised env and the
    normal approval policy still applies.
    """
    selected = {str(name).strip() for name in enabled if str(name).strip()}
    records = {record.name: record for record in scan_plugins(workdir)}
    schemas: list[dict[str, Any]] = []
    handlers: dict[str, Callable[[dict[str, Any]], ToolResult]] = {}
    issues: list[str] = []
    aliases: dict[str, str] = {}
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
        if not record.manifest.tools:
            continue
        permissions = record.manifest.permissions
        if "tools" not in permissions:
            issues.append(f"plugin {name}: 未声明 tools 权限，已拒绝加载工具")
            continue
        for spec in record.manifest.tools:
            if isinstance(spec, str):
                issues.append(f"plugin {name}: 工具 {spec} 只有名称，缺少 runner，已跳过")
                continue
            command = _plugin_command(record, spec, permissions, issues)
            if command is None:
                continue
            alias = plugin_tool_alias(name, spec.name)
            if alias in aliases:
                issues.append(
                    f"plugin {name}: 工具别名冲突 {alias}（已有 {aliases[alias]}）"
                )
                continue
            aliases[alias] = f"{name}:{spec.name}"
            description = f"[plugin {name}] {spec.description}".strip()[:4000]
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": alias,
                        "description": description,
                        "parameters": spec.parameters,
                    },
                }
            )
            def _handler(
                arguments: dict[str, Any],
                *,
                _record: PluginRecord = record,
                _spec: PluginToolSpec = spec,
                _command: str = command,
                _max: int = max_output_chars,
            ) -> ToolResult:
                return _run_plugin_tool(
                    _record, _spec, _command, arguments, _max
                )

            handlers[alias] = _handler
    return schemas, handlers, issues


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


REGISTRY_ENV = "SPARK_PLUGIN_REGISTRY"
MAX_REGISTRY_BYTES = 2_000_000
_GIT_URL_PREFIXES = ("https://", "http://", "ssh://", "git@")


class PluginRegistryEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    version: str = "0.0.0"
    description: str = ""
    url: str
    ref: str = ""
    subdir: str = ""

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not NAME_RE.fullmatch(value):
            raise ValueError(f"invalid plugin name: {value}")
        return value

    @field_validator("version")
    @classmethod
    def _version(cls, value: str) -> str:
        try:
            Version(value)
        except InvalidVersion as exc:
            raise ValueError(f"invalid registry version: {value}") from exc
        return value

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(("https://", "ssh://", "git@")):
            raise ValueError("registry plugin url must use https/ssh")
        return value


class PluginBundle(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: str = ""
    plugins: list[str] = Field(default_factory=list)

    @field_validator("plugins")
    @classmethod
    def _plugins(cls, values: list[str]) -> list[str]:
        for value in values:
            if not NAME_RE.fullmatch(value):
                raise ValueError(f"invalid plugin name in bundle: {value}")
        return values


class PluginRegistry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    plugins: list[PluginRegistryEntry] = Field(default_factory=list)
    bundles: dict[str, PluginBundle] = Field(default_factory=dict)


def registry_source(source: str | Path | None = None) -> str:
    raw = str(source or os.environ.get(REGISTRY_ENV, "")).strip()
    if not raw:
        raise PluginError(
            f"未配置插件市场：传 --registry 或设置 {REGISTRY_ENV}"
        )
    return raw


def load_registry(source: str | Path | None = None) -> PluginRegistry:
    """Load a registry index from https or a local JSON file (bounded read)."""
    raw = registry_source(source)
    if raw.startswith(("https://", "http://")):
        if not raw.startswith("https://"):
            raise PluginError("远程插件市场必须使用 https")
        try:
            request = urllib.request.Request(
                raw, headers={"User-Agent": "spark-plugin-registry"}
            )
            with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
                payload = response.read(MAX_REGISTRY_BYTES + 1)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise PluginError(f"插件市场不可用: {exc}") from exc
        if len(payload) > MAX_REGISTRY_BYTES:
            raise PluginError("插件市场索引过大")
        text = payload.decode("utf-8", errors="replace")
    else:
        path = Path(raw).expanduser()
        if not path.is_file():
            raise PluginError(f"插件市场索引不存在: {path}")
        if path.stat().st_size > MAX_REGISTRY_BYTES:
            raise PluginError("插件市场索引过大")
        text = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PluginError(f"插件市场索引不是合法 JSON: {exc}") from exc
    if isinstance(data, list):
        data = {"plugins": data}
    if not isinstance(data, dict):
        raise PluginError("插件市场索引必须是对象或数组")
    try:
        return PluginRegistry.model_validate(data)
    except ValidationError as exc:
        raise PluginError(f"插件市场索引校验失败: {exc}") from exc


def search_registry(
    query: str = "", source: str | Path | None = None
) -> list[PluginRegistryEntry]:
    registry = load_registry(source)
    needle = query.strip().lower()
    if not needle:
        return list(registry.plugins)
    return [
        item
        for item in registry.plugins
        if needle in item.name.lower() or needle in item.description.lower()
    ]


@dataclass(frozen=True)
class PluginUpdate:
    name: str
    installed: str
    available: str
    url: str


def resolve_bundle(
    name: str, source: str | Path | None = None
) -> tuple[PluginBundle, list[PluginRegistryEntry]]:
    """Resolve a named bundle to its registry entries (no installation)."""
    registry = load_registry(source)
    bundle = registry.bundles.get(name)
    if bundle is None:
        available = ", ".join(sorted(registry.bundles)) or "（无）"
        raise PluginError(f"插件包不存在: {name}；可用: {available}")
    by_name = {entry.name: entry for entry in registry.plugins}
    missing = [plugin for plugin in bundle.plugins if plugin not in by_name]
    if missing:
        raise PluginError(f"插件包 {name} 引用了不存在的插件: {', '.join(missing)}")
    return bundle, [by_name[plugin] for plugin in bundle.plugins]


def install_bundle(
    name: str,
    workdir: Path,
    *,
    source: str | Path | None = None,
    force: bool = False,
) -> list[PluginRecord]:
    """Install every plugin in a bundle; each plugin stays independently removable.

    Installation is best-effort across members: a broken member is reported but
    does not prevent the others from installing. Each member goes through the
    same manifest validation/atomic replace path as a single install.
    """
    registry = load_registry(source)
    bundle = registry.bundles.get(name)
    if bundle is None:
        raise PluginError(f"插件包不存在: {name}")
    by_name = {entry.name: entry for entry in registry.plugins}
    installed: list[PluginRecord] = []
    failures: list[str] = []
    for plugin in bundle.plugins:
        entry = by_name.get(plugin)
        if entry is None:
            failures.append(f"{plugin}: 索引缺失")
            continue
        try:
            installed.append(
                install_git(
                    entry.url,
                    workdir,
                    ref=entry.ref,
                    subdir=entry.subdir,
                    force=force,
                )
            )
        except PluginError as exc:
            failures.append(f"{plugin}: {exc}")
    if failures:
        raise PluginError(
            "插件包部分安装失败: " + "; ".join(failures)
        )
    return installed


def check_updates(
    workdir: Path, source: str | Path | None = None
) -> list[PluginUpdate]:
    """Compare installed plugin versions with the registry index."""
    registry = load_registry(source)
    installed = {
        record.name: record.manifest.version
        for record in scan_plugins(workdir)
        if record.manifest is not None
    }
    out: list[PluginUpdate] = []
    for entry in registry.plugins:
        current = installed.get(entry.name)
        if current is None:
            continue
        try:
            newer = Version(entry.version) > Version(current)
        except InvalidVersion:
            continue
        if newer:
            out.append(
                PluginUpdate(
                    name=entry.name,
                    installed=current,
                    available=entry.version,
                    url=entry.url,
                )
            )
    return out


def upgrade_from_registry(
    name: str,
    workdir: Path,
    *,
    source: str | Path | None = None,
) -> PluginRecord:
    """Install a newer registry version; refuses when already current."""
    updates = [item for item in check_updates(workdir, source) if item.name == name]
    if not updates:
        current = next(
            (
                record.manifest.version
                for record in scan_plugins(workdir)
                if record.name == name and record.manifest is not None
            ),
            "",
        )
        if current:
            raise PluginError(f"插件 {name} 已是最新版本 {current}")
        raise PluginError(f"插件 {name} 未安装或有更新的索引不可用")
    return install_from_registry(name, workdir, source=source, force=True)


def install_from_registry(
    name: str,
    workdir: Path,
    *,
    source: str | Path | None = None,
    force: bool = False,
) -> PluginRecord:
    registry = load_registry(source)
    for entry in registry.plugins:
        if entry.name == name:
            return install_git(
                entry.url,
                workdir,
                ref=entry.ref,
                subdir=entry.subdir,
                force=force,
            )
    raise PluginError(f"插件市场没有插件: {name}")


_REF_RE = re.compile(r"^[A-Za-z0-9._/][A-Za-z0-9._/-]{0,127}$")


def _validate_git_source(url: str, ref: str, subdir: str) -> None:
    if not url.startswith(_GIT_URL_PREFIXES):
        raise PluginError(
            "git 安装只接受 https:// / http:// / ssh:// / git@ 地址；本地目录请用 install"
        )
    if any(ch in url for ch in ("\n", "\r", "\x00")):
        raise PluginError("git 地址包含非法字符")
    if ref:
        if not _REF_RE.fullmatch(ref) or ref.startswith("-"):
            raise PluginError(f"无效的 ref: {ref}")
    if subdir:
        raw = subdir.replace("\\", "/")
        if raw.startswith("/") or raw.startswith("../") or "/../" in raw:
            raise PluginError("subdir 必须位于仓库内")


def install_git(
    url: str,
    workdir: Path,
    *,
    ref: str = "",
    subdir: str = "",
    force: bool = False,
    timeout_sec: int = 300,
) -> PluginRecord:
    """Clone a public/authorised git repo, validate its manifest, then install.

    Only network git URLs are accepted. The clone runs with hooks disabled and
    in a private temp directory; the resulting plugin is installed through the
    same ``install_local`` validation/backup path as a local directory.
    """
    _validate_git_source(url, ref, subdir)
    dest = Path(tempfile.mkdtemp(prefix="spark-plugin-git-"))
    clone = dest / "repo"
    args = [
        "git",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "protocol.file.allow=never",
        "clone",
        "--depth",
        "1",
        "--single-branch",
    ]
    if ref:
        args.extend(["--branch", ref])
    args.extend([url, str(clone)])
    try:
        result = run_bounded(
            args,
            cwd=dest,
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace",
        )
        if result.timed_out:
            raise PluginError(f"git clone 超时（{timeout_sec}s）")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[-2000:]
            raise PluginError(f"git clone 失败: {detail or result.returncode}")
        source = clone / subdir if subdir else clone
        try:
            source.resolve().relative_to(clone.resolve())
        except ValueError as exc:
            raise PluginError("subdir 逃出仓库目录") from exc
        if not source.is_dir():
            raise PluginError(f"仓库内不存在插件目录: {subdir or '.'}")
        return install_local(source, workdir, force=force)
    finally:
        shutil.rmtree(dest, ignore_errors=True)


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
