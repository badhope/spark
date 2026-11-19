"""One-command IDE wiring for Spark.

Only files we fully understand are written. Existing editor settings are
backed up before modification; malformed settings are refused instead of
overwritten (a broken editor config is worse than a manual install step).
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spark.errors import ConfigError


@dataclass(frozen=True)
class InstallResult:
    target: str
    path: Path
    backup: Path | None
    changed: bool
    snippet: dict[str, Any]


def zed_settings_path(home: Path | None = None) -> Path:
    home = home or Path.home()
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / "Zed" / "settings.json"
    return home / ".config" / "zed" / "settings.json"


def jetbrains_settings_path(home: Path | None = None) -> Path:
    """JetBrains 25.3+ ACP config lives in ``~/.jetbrains/acp.json``."""
    return (home or Path.home()) / ".jetbrains" / "acp.json"


def neovim_config_path(home: Path | None = None) -> Path:
    home = home or Path.home()
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        return base / "nvim" / "lua" / "spark_acp.lua"
    return home / ".config" / "nvim" / "lua" / "spark_acp.lua"


def _backup(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.spark-backup-{stamp}")
    shutil.copy2(path, backup)
    return backup


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".spark-settings-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(
            f"{path} 不是合法 JSON，已拒绝覆盖。请手动加入 Spark 配置，"
            f"或备份后重跑 --force（错误：{exc}）"
        ) from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} 顶层必须是 JSON object")
    return data


def _acp_server(workdir: Path, command: str) -> dict[str, Any]:
    return {
        "command": command,
        "args": ["acp", "--workdir", str(workdir.resolve())],
        "env": {},
    }


def install_zed(
    workdir: Path,
    *,
    settings_path: Path | None = None,
    command: str = "spark",
    force: bool = False,
) -> InstallResult:
    path = settings_path or zed_settings_path()
    snippet = {"agent": {"custom_servers": {"spark": _acp_server(workdir, command)}}}
    if not path.exists():
        _atomic_write(path, json.dumps(snippet, ensure_ascii=False, indent=2) + "\n")
        return InstallResult("zed", path, None, True, snippet)

    if not force:
        data = _load_json_object(path)
    else:
        try:
            data = _load_json_object(path)
        except ConfigError:
            data = {}
    before = json.dumps(data, sort_keys=True)
    agent = data.setdefault("agent", {})
    if not isinstance(agent, dict):
        raise ConfigError(f"{path}: agent 必须是 object")
    servers = agent.setdefault("custom_servers", {})
    if not isinstance(servers, dict):
        raise ConfigError(f"{path}: agent.custom_servers 必须是 object")
    servers["spark"] = _acp_server(workdir, command)
    after = json.dumps(data, sort_keys=True)
    if before == after:
        return InstallResult("zed", path, None, False, snippet)
    backup = _backup(path)
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return InstallResult("zed", path, backup, True, snippet)


def install_jetbrains(
    workdir: Path,
    *,
    settings_path: Path | None = None,
    command: str = "spark",
    force: bool = False,
) -> InstallResult:
    """Merge a Spark entry into JetBrains' native ACP config.

    JetBrains 25.3+ speaks ACP directly, so a custom JetBrains plugin would
    duplicate the editor's own chat UI and approval/diff plumbing.
    """
    path = settings_path or jetbrains_settings_path()
    server_id = "Spark"
    server = _acp_server(workdir, command)
    snippet = {"agent_servers": {server_id: server}}
    if not path.exists():
        _atomic_write(path, json.dumps(snippet, ensure_ascii=False, indent=2) + "\n")
        return InstallResult("jetbrains", path, None, True, snippet)

    if not force:
        data = _load_json_object(path)
    else:
        try:
            data = _load_json_object(path)
        except ConfigError:
            data = {}
    before = json.dumps(data, sort_keys=True)
    servers = data.setdefault("agent_servers", {})
    if not isinstance(servers, dict):
        raise ConfigError(f"{path}: agent_servers 必须是 object")
    servers[server_id] = server
    after = json.dumps(data, sort_keys=True)
    if before == after:
        return InstallResult("jetbrains", path, None, False, snippet)
    backup = _backup(path)
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return InstallResult("jetbrains", path, backup, True, snippet)


def _lua_string(value: str) -> str:
    return (
        '"'
        + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        + '"'
    )


_NEOVIM_TEMPLATE = '''-- Generated by `spark ide install neovim`. Safe to delete.
-- Requires codecompanion.nvim and Neovim >= 0.10.
local M = {}

function M.adapter()
  local ok, adapters = pcall(require, "codecompanion.adapters")
  if not ok then
    error("Spark: codecompanion.nvim is not installed")
  end
  return adapters.extend("opencode", {
    name = "spark",
    formatted_name = "Spark",
    commands = {
      default = { __ARGS__ },
    },
  })
end

function M.setup(opts)
  local ok, cc = pcall(require, "codecompanion")
  if not ok then
    error("Spark: codecompanion.nvim is not installed")
  end
  opts = opts or {}
  cc.setup(vim.tbl_deep_extend("force", {
    adapters = { acp = { spark = M.adapter } },
    interactions = { chat = { adapter = "spark" } },
  }, opts))
  return true
end

return M
'''


def _neovim_lua(workdir: Path, command: str) -> str:
    command_args = [command, "acp", "--workdir", workdir.resolve().as_posix()]
    return _NEOVIM_TEMPLATE.replace(
        "__ARGS__", ", ".join(_lua_string(arg) for arg in command_args)
    )


def install_neovim(
    workdir: Path,
    *,
    settings_path: Path | None = None,
    command: str = "spark",
    force: bool = False,
) -> InstallResult:
    """Write a self-contained Lua module; never edit the user's init.lua."""
    path = settings_path or neovim_config_path()
    lua = _neovim_lua(workdir, command)
    snippet = {
        "adapter": "codecompanion",
        "usage": 'require("spark_acp").setup()',
    }
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if "-- Generated by `spark ide install neovim`" not in text and not force:
            raise ConfigError(
                f"{path} 不是 Spark 生成的文件，已拒绝覆盖。备份后重跑 --force，"
                '或手动把 `require("spark_acp").setup()` 接入你的配置。'
            )
        if text == lua:
            return InstallResult("neovim", path, None, False, snippet)
        backup = _backup(path)
    else:
        backup = None
    _atomic_write(path, lua)
    return InstallResult("neovim", path, backup, True, snippet)


def install(target: str, workdir: Path, **kwargs: Any) -> InstallResult:
    normalized = target.strip().lower()
    if normalized == "zed":
        return install_zed(workdir, **kwargs)
    if normalized in {"jetbrains", "jb", "intellij"}:
        return install_jetbrains(workdir, **kwargs)
    if normalized in {"neovim", "nvim"}:
        return install_neovim(workdir, **kwargs)
    raise ConfigError(
        f"未知 IDE target: {target!r}。当前支持：zed、jetbrains（25.3+ 原生 ACP）、"
        "neovim（CodeCompanion ACP 片段）。VS Code 用仓库自带 VSIX。"
    )
