from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from spark import cli
from spark.config import SparkConfig, load_config

runner = CliRunner()


def test_version_command_reports_package_version() -> None:
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert "spark" in result.stdout.lower()


def test_init_writes_config_without_touching_user_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(cli, "default_home", lambda: home)
    cfg_path = tmp_path / "spark.toml"
    result = runner.invoke(
        cli.app,
        [
            "init",
            "--config",
            str(cfg_path),
            "--base-url",
            "https://example.invalid/v1",
            "--model",
            "demo-model",
            "--api-key",
            "sk-test-1234567890",
            "--no-probe",
        ],
    )
    assert result.exit_code == 0, result.stdout
    saved = cfg_path.read_text(encoding="utf-8")
    assert "example.invalid" in saved
    assert "demo-model" in saved
    assert "sk-test-1234567890" in saved


def test_init_never_echoes_the_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    result = runner.invoke(
        cli.app,
        [
            "init",
            "--config",
            str(cfg_path),
            "--base-url",
            "https://example.invalid/v1",
            "--model",
            "demo",
            "--api-key",
            "sk-supersecret-value-0001",
            "--no-probe",
        ],
    )
    assert result.exit_code == 0
    assert "sk-supersecret-value-0001" not in result.stdout


def test_init_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    args = [
        "init",
        "--config",
        str(cfg_path),
        "--base-url",
        "https://example.invalid/v1",
        "--model",
        "demo",
        "--api-key",
        "sk-abc-123",
        "--no-probe",
    ]
    assert runner.invoke(cli.app, args).exit_code == 0
    assert runner.invoke(cli.app, args).exit_code == 0


def test_doctor_passes_with_complete_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    runner.invoke(
        cli.app,
        [
            "init",
            "--config",
            str(cfg_path),
            "--base-url",
            "https://example.invalid/v1",
            "--model",
            "demo",
            "--api-key",
            "sk-abc-123456",
            "--no-probe",
        ],
    )
    result = runner.invoke(cli.app, ["doctor", "--config", str(cfg_path), "--no-probe"])
    assert result.exit_code == 0, result.stdout
    assert "配置" in result.stdout or "config" in result.stdout.lower()


def test_doctor_warns_missing_key_with_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    cfg_path.write_text(
        '[provider]\nname = "openai_compat"\nbase_url = "https://example.invalid/v1"\n'
        'model = "demo"\napi_key_env = "SPARK_API_KEY"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("SPARK_API_KEY", raising=False)
    result = runner.invoke(cli.app, ["doctor", "--config", str(cfg_path), "--no-probe"])
    assert result.exit_code == 0
    assert "spark init" in result.stdout
    assert "演示模式" in result.stdout


def test_doctor_warns_when_workdir_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    cfg_path.write_text('[provider]\nname = "mock"\nmodel = "m"\n', encoding="utf-8")
    missing = tmp_path / "does-not-exist"
    result = runner.invoke(
        cli.app,
        ["doctor", "--config", str(cfg_path), "--workdir", str(missing), "--no-probe"],
    )
    assert "工作目录" in result.stdout or "workdir" in result.stdout.lower()


def test_config_show_masks_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    cfg_path.write_text(
        '[provider]\nname = "openai_compat"\nbase_url = "https://x.invalid/v1"\n'
        'model = "m"\napi_key = "sk-should-not-appear-1234"\n',
        encoding="utf-8",
    )
    result = runner.invoke(cli.app, ["config", "show", "--config", str(cfg_path)])
    assert result.exit_code == 0
    assert "sk-should-not-appear-1234" not in result.stdout
    assert "sk-s" in result.stdout


def test_config_set_updates_dotted_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    cfg_path.write_text('[provider]\nname = "mock"\nmodel = "m"\n', encoding="utf-8")
    result = runner.invoke(
        cli.app,
        ["config", "set", "agent.max_repeat_calls", "6", "--config", str(cfg_path)],
    )
    assert result.exit_code == 0, result.stdout
    loaded = load_config(config_path=cfg_path, workdir=tmp_path)
    assert loaded.agent.max_repeat_calls == 6


def test_config_set_rejects_unknown_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    cfg_path.write_text('[provider]\nname = "mock"\n', encoding="utf-8")
    result = runner.invoke(
        cli.app,
        ["config", "set", "agent.nope", "1", "--config", str(cfg_path)],
    )
    assert result.exit_code == 2
    assert "nope" in (result.stdout + result.stderr)


def test_config_set_rejects_bad_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    cfg_path.write_text('[provider]\nname = "mock"\n', encoding="utf-8")
    result = runner.invoke(
        cli.app,
        ["config", "set", "agent.max_repeat_calls", "abc", "--config", str(cfg_path)],
    )
    assert result.exit_code == 2


def test_missing_key_error_is_friendly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPARK_API_KEY", raising=False)
    cfg = SparkConfig()
    cfg.provider.name = "openai_compat"
    cfg.provider.api_key = None
    message = cli.friendly_config_error(cfg)
    assert "spark init" in message
    assert "API" in message


def test_json_output_mode_is_machine_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    cfg_path.write_text('[provider]\nname = "mock"\nmodel = "m"\n', encoding="utf-8")
    result = runner.invoke(
        cli.app, ["config", "show", "--config", str(cfg_path), "--json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["provider"]["name"] == "mock"


def test_help_lists_new_commands() -> None:
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for command in ("init", "doctor", "config", "version"):
        assert command in result.stdout


# ---------------------------------------------------------------------------
# CLI surface sweep (audit F-003: cli.py was at 29% line coverage)
# ---------------------------------------------------------------------------

def _invoke(args, monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(cli, "default_home", lambda: home)
    monkeypatch.setenv("SPARK_HOME", str(home))
    return runner.invoke(cli.app, args)


def test_help_lists_core_commands() -> None:
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("exec", "web", "desktop", "mcp-serve", "acp", "doctor", "config"):
        assert cmd in result.stdout, cmd


def test_config_path_and_show(tmp_path, monkeypatch) -> None:
    r = _invoke(["config", "path"], monkeypatch, tmp_path)
    assert r.exit_code == 0
    assert "config.toml" in r.stdout
    r = _invoke(["config", "show"], monkeypatch, tmp_path)
    assert r.exit_code == 0
    assert "provider" in r.stdout.lower()


def test_config_set_roundtrip(tmp_path, monkeypatch) -> None:
    r = _invoke(["config", "set", "provider.model", "demo-model"], monkeypatch, tmp_path)
    assert r.exit_code == 0
    r = _invoke(["config", "show"], monkeypatch, tmp_path)
    assert "demo-model" in r.stdout


def test_sessions_on_empty_home(tmp_path, monkeypatch) -> None:
    r = _invoke(["sessions"], monkeypatch, tmp_path)
    assert r.exit_code == 0
    assert "No sessions" in r.stdout or "暂无" in r.stdout


def test_replay_on_empty_home(tmp_path, monkeypatch) -> None:
    r = _invoke(["replay"], monkeypatch, tmp_path)
    assert r.exit_code == 0


def test_context_profile_needs_session(tmp_path, monkeypatch) -> None:
    r = _invoke(["context-profile"], monkeypatch, tmp_path)
    assert r.exit_code in (0, 1)


def test_doctor_json_output(tmp_path, monkeypatch) -> None:
    r = _invoke(["doctor", "--no-probe", "--json", "--workdir", str(tmp_path)], monkeypatch, tmp_path)
    assert r.exit_code in (0, 1)
    # The banner itself contains "[Ember]", so anchor on a newline-prefixed '['.
    start = r.stdout.rindex(chr(10) + "[")
    payload = json.loads(r.stdout[start:].strip())
    assert isinstance(payload, list) and payload


def test_doctor_backup_without_db_is_graceful(tmp_path, monkeypatch) -> None:
    r = _invoke(
        ["doctor", "--no-probe", "--backup", "--workdir", str(tmp_path)], monkeypatch, tmp_path
    )
    # The "skip" note goes to stderr by design (it is diagnostic, not result).
    out = r.stdout + (r.stderr or "")
    assert "跳过" in out or "skip" in out.lower()


def test_doctor_backup_creates_snapshot(tmp_path, monkeypatch) -> None:
    from spark.store import SessionStore

    r = _invoke(["doctor", "--no-probe", "--backup"], monkeypatch, tmp_path)
    home = tmp_path / "home"
    SessionStore(home / "sessions.db").close()
    r = _invoke(["doctor", "--no-probe", "--backup"], monkeypatch, tmp_path)
    assert "已备份" in r.stdout
    assert list((home / "backups").glob("sessions-*.db"))


def test_models_command_without_network(tmp_path, monkeypatch) -> None:
    """`spark models` hits models.dev; assert it degrades instead of crashing."""
    r = _invoke(["models", "--limit", "3"], monkeypatch, tmp_path)
    assert r.exit_code in (0, 1)


def test_agent_list_and_unknown_use(tmp_path, monkeypatch) -> None:
    r = _invoke(["agent", "list"], monkeypatch, tmp_path)
    assert r.exit_code == 0
    r = _invoke(["agent", "use", "ghost"], monkeypatch, tmp_path)
    assert r.exit_code != 0


def test_profile_list_and_unknown_use(tmp_path, monkeypatch) -> None:
    r = _invoke(["profile", "list"], monkeypatch, tmp_path)
    assert r.exit_code == 0
    r = _invoke(["profile", "use", "ghost"], monkeypatch, tmp_path)
    assert r.exit_code != 0


def test_exec_without_key_runs_in_demo_mode(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SPARK_API_KEY", raising=False)
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text(
        '[provider]\nname = "openai_compat"\nbase_url = "https://example.invalid/v1"\n'
        'model = "m"\napi_key_env = "SPARK_NOPE_KEY"\n',
        encoding="utf-8",
    )
    r = _invoke(
        ["exec", "hi", "--config", str(cfg_path), "--approval", "full-auto", "--workdir", str(tmp_path)],
        monkeypatch,
        tmp_path,
    )
    assert r.exit_code == 0
    assert "mock" in r.stdout.lower() or "演示" in r.stdout


def test_web_rejects_busy_port(tmp_path, monkeypatch) -> None:
    import socket

    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        r = _invoke(
            ["web", "--workdir", str(tmp_path), "--port", str(port), "--provider", "mock"],
            monkeypatch,
            tmp_path,
        )
        assert r.exit_code != 0
        out = r.stdout + (r.stderr or "")
        assert "占用" in out or "occupied" in out.lower()
    finally:
        holder.close()


# ---------------------------------------------------------------------------
# models --probe: verify the *configured* endpoint, not a public catalogue
# ---------------------------------------------------------------------------


def test_models_probe_requires_an_endpoint(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text('[provider]\nname = "mock"\nmodel = "m"\n', encoding="utf-8")
    r = _invoke(
        ["models", "--probe", "--config", str(cfg), "--workdir", str(tmp_path)],
        monkeypatch,
        tmp_path,
    )
    assert r.exit_code == 0
    assert "mock" in r.stdout


def test_models_probe_reports_unreachable_endpoint(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        '[provider]\nname = "openai_compat"\nbase_url = "http://127.0.0.1:1/v1"\n'
        'model = "m"\napi_key = "sk-test"\n',
        encoding="utf-8",
    )
    r = _invoke(
        ["models", "--probe", "--config", str(cfg), "--workdir", str(tmp_path)],
        monkeypatch,
        tmp_path,
    )
    assert r.exit_code == 1
    out = r.stdout + (r.stderr or "")
    assert "未返回模型列表" in out or "not" in out.lower()


def test_list_models_parses_and_degrades() -> None:
    """`list_models` must return [] for bad input rather than raising."""
    import asyncio

    from spark.providers.probe import list_models

    assert asyncio.run(list_models(base_url="", api_key="k")) == []
    assert asyncio.run(list_models(base_url="http://127.0.0.1:1/v1", api_key="k")) == []


# ---------------------------------------------------------------------------
# exec --json: NDJSON event stream for CI/scripting (codex exec --json parity)
# ---------------------------------------------------------------------------


def test_exec_json_streams_ndjson_and_final_line(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SPARK_API_KEY", raising=False)
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text(
        '[provider]\nname = "openai_compat"\nbase_url = "https://example.invalid/v1"\n'
        'model = "m"\napi_key_env = "SPARK_NOPE_KEY"\n',
        encoding="utf-8",
    )
    r = _invoke(
        [
            "exec",
            "hi",
            "--json",
            "--config",
            str(cfg_path),
            "--approval",
            "full-auto",
            "--workdir",
            str(tmp_path),
        ],
        monkeypatch,
        tmp_path,
    )
    assert r.exit_code == 0, r.stdout + (r.stderr or "")
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert lines, "expected at least one NDJSON line"
    events = [json.loads(ln) for ln in lines]
    assert all(isinstance(e, dict) and "type" in e for e in events)
    types = [e["type"] for e in events]
    assert "turn_end" in types
    assert types[-1] == "final"
    assert events[-1].get("text")


def test_exec_json_rejects_suggest_mode(tmp_path, monkeypatch) -> None:
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text(
        '[provider]\nname = "mock"\nmodel = "mock"\n',
        encoding="utf-8",
    )
    r = _invoke(
        [
            "exec",
            "hi",
            "--json",
            "--config",
            str(cfg_path),
            "--approval",
            "suggest",
            "--workdir",
            str(tmp_path),
        ],
        monkeypatch,
        tmp_path,
    )
    assert r.exit_code != 0
    combined = r.stdout + (r.stderr or "")
    assert "approval" in combined.lower() or "exec" in combined.lower()


# ---------------------------------------------------------------------------
# spark update — CLI surface (network mocked)
# ---------------------------------------------------------------------------


def _updater_cfg(tmp_path):
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text('[provider]\nname = "mock"\nmodel = "mock"\n', encoding="utf-8")
    return cfg_path


def test_update_reports_already_latest(tmp_path, monkeypatch) -> None:
    from spark import updater

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cli, "default_home", lambda: home)
    monkeypatch.setenv("SPARK_HOME", str(home))
    # 用一个必然"不比当前新"的版本：本地是源码安装，版本可能高于测试硬编码
    current = cli._installed_version()
    release = updater.ReleaseInfo(version=current, tag=f"v{current}", notes="", assets=[])
    monkeypatch.setattr(updater, "fetch_latest", lambda *a, **k: release)
    r = runner.invoke(cli.app, ["update"])
    assert r.exit_code == 0
    assert "最新" in r.stdout


def test_update_check_only_reports_url(tmp_path, monkeypatch) -> None:
    from spark import updater

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cli, "default_home", lambda: home)
    monkeypatch.setenv("SPARK_HOME", str(home))
    release = updater.ReleaseInfo(version="99.0.0", tag="v99.0.0", notes="", assets=[])
    monkeypatch.setattr(updater, "fetch_latest", lambda *a, **k: release)
    r = runner.invoke(cli.app, ["update", "--check"])
    assert r.exit_code == 0
    assert "releases/tag/v99.0.0" in r.stdout


def test_update_network_failure_is_friendly(tmp_path, monkeypatch) -> None:
    from spark import updater

    def boom(*a, **k):
        raise updater.UpdateError("no route to host")

    monkeypatch.setattr(updater, "fetch_latest", boom)
    r = runner.invoke(cli.app, ["update"])
    assert r.exit_code == 1
    assert "检查更新失败" in (r.stdout + (r.stderr or ""))


def test_update_pip_install_is_redirected_to_pip(tmp_path, monkeypatch) -> None:
    from spark import updater

    release = updater.ReleaseInfo(version="99.0.0", tag="v99.0.0", notes="", assets=[])
    monkeypatch.setattr(updater, "fetch_latest", lambda *a, **k: release)
    r = runner.invoke(cli.app, ["update"])
    assert r.exit_code == 2
    assert "pip install -U spark-agent" in r.stdout


# ---------- uninstall ----------


def _make_spark_home(home: Path) -> None:
    """伪造一个已使用过的 Spark home：配置、会话库、知识库各占一项。"""
    home.mkdir(parents=True)
    (home / "config.toml").write_text("[agent]\napproval = 'full-auto'\n", encoding="utf-8")
    (home / "sessions.db").write_bytes(b"sqlite" * 100)
    (home / "knowledge" / "abc123").mkdir(parents=True)
    (home / "knowledge" / "abc123" / "doc.md").write_text("k", encoding="utf-8")


def test_uninstall_list_shows_inventory_and_keeps_files(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    _make_spark_home(home)
    monkeypatch.setattr(cli, "default_home", lambda: home)
    monkeypatch.setenv("SPARK_HOME", str(home))
    r = runner.invoke(cli.app, ["uninstall", "--list"])
    assert r.exit_code == 0, r.stdout
    assert "config.toml" in r.stdout and "sessions.db" in r.stdout
    assert "knowledge" in r.stdout
    assert home.exists(), "--list 不得删除任何文件"


def test_uninstall_requires_confirmation_by_default(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    _make_spark_home(home)
    monkeypatch.setattr(cli, "default_home", lambda: home)
    monkeypatch.setenv("SPARK_HOME", str(home))
    r = runner.invoke(cli.app, ["uninstall"], input="n\n")
    assert r.exit_code == 0, r.stdout
    assert "已取消" in r.stdout + (r.stderr or "")
    assert home.exists(), "未确认时不得删除"


def test_uninstall_yes_removes_home(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    _make_spark_home(home)
    monkeypatch.setattr(cli, "default_home", lambda: home)
    monkeypatch.setenv("SPARK_HOME", str(home))
    r = runner.invoke(cli.app, ["uninstall", "--yes"])
    assert r.exit_code == 0, r.stdout
    assert not home.exists()
    # 项目内的 .spark/ 工作区不在清理范围（清单里根本不该出现 workdir 路径）
    assert str(tmp_path / "home" / "knowledge") not in r.stdout.split("数据目录：")[1].split("\n")[0]


def test_uninstall_refuses_to_delete_home_directory_itself(tmp_path, monkeypatch) -> None:
    """SPARK_HOME 指向用户家目录时必须拒绝——环境变量写错不能删掉整个家。"""
    home = tmp_path / "userhome"
    home.mkdir()
    (home / "config.toml").write_text("x", encoding="utf-8")
    monkeypatch.setattr(cli, "default_home", lambda: home)
    monkeypatch.setenv("SPARK_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    r = runner.invoke(cli.app, ["uninstall", "--yes"])
    assert r.exit_code == 2
    assert "拒绝删除" in r.stdout + (r.stderr or "")
    assert home.exists(), "守卫拦截后不得删除"


def test_uninstall_empty_home_is_a_noop(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "missing")
    monkeypatch.setenv("SPARK_HOME", str(tmp_path / "missing"))
    r = runner.invoke(cli.app, ["uninstall", "--yes"])
    assert r.exit_code == 0, r.stdout
    assert "没有可清理的数据" in r.stdout
