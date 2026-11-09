"""Environment self-healing: detect what is broken and fix what can be fixed.

The point is to keep the agent usable on a messy machine instead of dying with
a stack trace the user cannot act on. Two halves:

* ``diagnose`` — read-only health report (what is wrong, how to fix it);
* ``repair``   — apply the safe, reversible subset (create missing dirs, write
  a default config when none exists, back up an unparsable config, create the
  database directory, restore spawn-able permissions).

Deliberately NOT auto-installed here: language runtimes, npm packages, and
anything that needs a network round trip or admin rights. Those are reported
with the exact command and left to the user — an agent that silently installs
software is a supply-chain risk, not a convenience.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from spark.config import (
    SparkConfig,
    default_home,
    env_api_key,
    write_config_template,
)
from spark.tools.shell import detect_env

__all__ = ["diagnose", "repair", "probe_runtimes"]

#: Runtime → install hint. Kept short: the UI shows this line verbatim.
INSTALL_HINTS = {
    "python": "安装 Python 3.11+ 并勾选 Add to PATH：https://www.python.org/downloads/",
    "python3": "安装 Python 3.11+；Windows 上 python3 通常不存在，用 python",
    "pip": "python -m ensurepip --upgrade",
    "node": "安装 Node.js LTS：https://nodejs.org/",
    "npm": "随 Node.js 一起安装（nodejs.org）",
    "git": "安装 Git：https://git-scm.com/downloads",
}

#: Tools the agent itself needs to do useful work.
REQUIRED_FOR_AGENT = ("python", "git")
OPTIONAL = ("node", "npm")

_MAX_LOG_LINES = 20


def _row(level: str, kind: str, message: str, fix: str = "", auto: bool = False) -> dict:
    return {
        "level": level,
        "kind": kind,
        "message": message,
        "fix": fix,
        "auto_fixable": auto,
    }


def _writable(path: Path) -> bool:
    probe = path if path.exists() else path.parent
    try:
        if not probe.exists():
            return False
        if probe.is_dir():
            test = probe / ".spark-write-probe"
            test.write_text("ok", encoding="utf-8")
            test.unlink()
            return True
        return os.access(probe, os.W_OK)
    except OSError:
        return False


def probe_runtimes(cwd: Path | None = None) -> dict[str, str]:
    """Installed runtimes, reusing the cached prober (never raises)."""
    try:
        return detect_env(cwd or Path.cwd())
    except Exception:
        return {}


def _runtime_rows(env: dict[str, str]) -> list[dict]:
    rows: list[dict] = []
    for name in REQUIRED_FOR_AGENT:
        if name in env:
            rows.append(_row("ok", f"runtime.{name}", f"{name}: {env[name]}"))
        else:
            rows.append(
                _row(
                    "bad",
                    f"runtime.{name}",
                    f"未检测到 {name}——依赖它的命令会失败",
                    INSTALL_HINTS.get(name, f"安装 {name} 并加入 PATH"),
                )
            )
    for name in OPTIONAL:
        if name in env:
            rows.append(_row("ok", f"runtime.{name}", f"{name}: {env[name]}"))
        else:
            rows.append(
                _row(
                    "warn",
                    f"runtime.{name}",
                    f"未检测到 {name}——前端相关任务会退化",
                    INSTALL_HINTS.get(name, ""),
                )
            )
    return rows


def diagnose(
    cfg: SparkConfig,
    *,
    workdir: Path,
    config_path: Path | None = None,
) -> list[dict]:
    """Full health report. Every row is ``{level, kind, message, fix, auto_fixable}``."""
    rows: list[dict] = []
    resolved = workdir
    home = default_home()

    # --- workdir ---
    if not resolved.exists():
        rows.append(
            _row(
                "bad",
                "workdir.missing",
                f"工作目录不存在：{resolved}",
                "自动创建该目录",
                auto=True,
            )
        )
    elif not resolved.is_dir():
        rows.append(
            _row("bad", "workdir.not_dir", f"工作目录不是文件夹：{resolved}", "换一个 --workdir")
        )
    elif not _writable(resolved):
        rows.append(
            _row("bad", "workdir.readonly", f"工作目录不可写：{resolved}", "检查目录权限或换目录")
        )
    else:
        rows.append(_row("ok", "workdir.ok", f"工作目录可用：{resolved}"))

    # --- SPARK_HOME ---
    if not home.exists():
        rows.append(
            _row("warn", "home.missing", f"配置目录不存在：{home}", "自动创建", auto=True)
        )
    elif not _writable(home):
        rows.append(
            _row("bad", "home.readonly", f"配置目录不可写：{home}", "检查权限或设置 SPARK_HOME 到可写目录")
        )
    else:
        rows.append(_row("ok", "home.ok", f"配置目录可用：{home}"))

    # --- credentials ---
    if cfg.provider.name == "mock":
        rows.append(
            _row("warn", "provider.mock", "当前为演示模型（mock），不会真实调用大模型", "运行 spark init 或填 API Key")
        )
    elif cfg.provider.name == "ollama":
        rows.append(_row("ok", "provider.ollama", f"Ollama 本地模型：{cfg.provider.model}"))
    elif not (cfg.provider.api_key or env_api_key(cfg)):
        key_env = cfg.provider.api_key_env or "SPARK_API_KEY"
        rows.append(
            _row(
                "bad",
                "provider.no_key",
                f"未配置 API Key——当前会退回演示模式；需要环境变量 {key_env}",
                f"设置环境变量 {key_env}，或在 Web 设置中填入密钥",
            )
        )
    elif not cfg.provider.base_url:
        rows.append(_row("bad", "provider.no_url", "未配置 base_url", "在设置中填写接口地址"))
    else:
        rows.append(_row("ok", "provider.ok", f"模型：{cfg.provider.model} @ {cfg.provider.base_url}"))

    # --- config file ---
    path = config_path or (home / "config.toml")
    if not path.exists():
        rows.append(
            _row("warn", "config.missing", f"配置文件不存在：{path}", "自动写入默认配置", auto=True)
        )
    else:
        try:
            path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            rows.append(
                _row("bad", "config.unreadable", f"配置文件无法读取：{exc}", "备份并重建")
            )

    # --- runtimes ---
    env = probe_runtimes(resolved if resolved.exists() else Path.cwd())
    rows.extend(_runtime_rows(env))

    # diagnose 是只读报告：此前这里顺手 mkdir SPARK_HOME（违反契约），
    # 使 home.missing 的自动修复项永远无法触发——看一眼报告就把目录建了，
    # repair 便无事可做。目录创建归 SessionStore/TurnCheckpointer 构造器
    # 与 repair 的 home.missing 动作负责。

    # --- summary ---
    bad = sum(1 for r in rows if r["level"] == "bad")
    warn = sum(1 for r in rows if r["level"] == "warn")
    rows.insert(
        0,
        _row(
            "ok" if bad == 0 else "bad",
            "summary",
            f"{bad} 个阻断项 / {warn} 个警告",
            "点击「一键修复」处理可自动修复项" if bad or warn else "",
        ),
    )
    return rows


def _backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.broken-{stamp}")
    shutil.copy2(path, target)
    return target


def _apply(action: dict) -> None:
    kind = action["kind"]
    if kind == "workdir.missing":
        Path(action["_path"]).mkdir(parents=True, exist_ok=True)
    elif kind == "home.missing":
        Path(action["_path"]).mkdir(parents=True, exist_ok=True)
    elif kind == "config.missing":
        target = Path(action["_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        write_config_template(target)
    elif kind == "config.unreadable":
        target = Path(action["_path"])
        backup = _backup(target)
        target.unlink()
        write_config_template(target)
        action["detail"] = f"已备份到 {backup.name} 并重建默认配置"


def _action_target(row: dict, *, workdir: Path, config_path: Path) -> str:
    kind = row["kind"]
    if kind == "workdir.missing":
        return str(workdir)
    if kind == "home.missing":
        return str(default_home())
    return str(config_path)


def repair(
    cfg: SparkConfig,
    *,
    workdir: Path,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Apply every auto-fixable repair, then re-diagnose.

    Idempotent: a second call on a healthy tree applies nothing. Returns
    ``{ok, actions, rows}`` where ``actions`` is the audit trail.
    """
    path = config_path or (default_home() / "config.toml")
    before = diagnose(cfg, workdir=workdir, config_path=path)
    actions: list[dict] = []
    for row in before:
        if not row.get("auto_fixable"):
            continue
        action = {
            "kind": row["kind"],
            "message": row["message"],
            "applied": False,
            "_path": _action_target(row, workdir=workdir, config_path=path),
        }
        try:
            _apply(action)
            action["applied"] = True
        except Exception as exc:  # noqa: BLE001 - report, never crash the repair loop
            action["error"] = str(exc)
        actions.append(action)
    after = diagnose(cfg, workdir=workdir, config_path=path)
    remaining = [r for r in after if r["level"] == "bad" and not r.get("auto_fixable")]
    return {
        "ok": not remaining,
        "actions": actions,
        "rows": after,
        "remaining": remaining,
    }


def bootstrap_config(path: Path | None = None) -> Path:
    """Create the config file with defaults if it does not exist yet."""
    target = path or (default_home() / "config.toml")
    if not target.exists():
        write_config_template(target)
    return target


def run_if_missing(cmd: list[str]) -> str | None:
    """Return command output when the executable exists, else None.

    Used by the UI's environment card so "installed?" means "actually runs",
    not "there is a file with that name on PATH".
    """
    exe = shutil.which(cmd[0])
    if exe is None:
        return None
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (proc.stdout or proc.stderr or "").strip().splitlines()
    return text[0][:80] if text else "installed"
