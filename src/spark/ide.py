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
            f"{path} 不是合法 JSON，已拒绝覆盖。请手动在 agent.custom_servers 中加入 Spark，"
            f"或备份后重跑 --force（错误：{exc}）"
        ) from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} 顶层必须是 JSON object")
    return data


def _zed_server(workdir: Path, command: str) -> dict[str, Any]:
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
    snippet = {"agent": {"custom_servers": {"spark": _zed_server(workdir, command)}}}
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
    servers["spark"] = _zed_server(workdir, command)
    after = json.dumps(data, sort_keys=True)
    if before == after:
        return InstallResult("zed", path, None, False, snippet)
    backup = _backup(path)
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return InstallResult("zed", path, backup, True, snippet)


def install(target: str, workdir: Path, **kwargs: Any) -> InstallResult:
    normalized = target.strip().lower()
    if normalized == "zed":
        return install_zed(workdir, **kwargs)
    raise ConfigError(
        f"未知 IDE target: {target!r}。当前支持：zed（VS Code 用仓库自带 VSIX；"
        "Neovim 请用 `spark acp` 作为 ACP server 手动接入）"
    )
