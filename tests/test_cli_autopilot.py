from __future__ import annotations

from typer.testing import CliRunner

from spark.cli import app


def test_autopilot_help_is_registered() -> None:
    result = CliRunner().invoke(app, ["autopilot", "--help"], env={"COLUMNS": "120"})
    assert result.exit_code == 0
    assert "无人值守编程" in result.output
    assert "--max-turns" in result.output
    assert "--resume" in result.output
    assert "--heartbeat-inter" in result.output


def test_autopilot_rejects_interactive_approval_mode() -> None:
    result = CliRunner().invoke(
        app, ["autopilot", "fix bug", "--approval", "suggest"]
    )
    assert result.exit_code == 2
    assert "full-auto" in result.output


def test_autopilot_rejects_unrestricted_sandbox_by_default() -> None:
    result = CliRunner().invoke(
        app,
        ["autopilot", "fix bug", "--sandbox-mode", "unrestricted"],
    )
    assert result.exit_code == 2
    assert "allow-unrestricted" in result.output


def test_opencode_status_is_registered(tmp_path) -> None:
    result = CliRunner().invoke(
        app, ["opencode", "--workdir", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "config: none" in result.output
    assert "agents: 0" in result.output
