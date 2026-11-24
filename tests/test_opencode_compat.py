from __future__ import annotations

import json
from pathlib import Path

from spark.agents import discover_declared_agents, get_declared_agent
from spark.opencode_compat import (
    _strip_jsonc,
    discover_agents,
    find_config,
    load_config,
    load_project,
)
from spark.slash import discover_commands, expand_command


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_jsonc_comments_and_trailing_commas(tmp_path: Path) -> None:
    path = tmp_path / "opencode.jsonc"
    _write(
        path,
        """{
  // a comment with https://example.com inside
  "model": "anthropic/claude-sonnet-5-5",
  "mcp": {"docs": {"url": "https://example.com/mcp"}},
  /* block comment */
  "default_agent": "build",
}
""",
    )
    data = load_config(path)
    assert data["model"] == "anthropic/claude-sonnet-5-5"
    assert data["mcp"]["docs"]["url"] == "https://example.com/mcp"
    assert data["default_agent"] == "build"
    assert "//" in json.dumps(data["mcp"])


def test_markdown_agent_permissions_become_tool_denylist(tmp_path: Path) -> None:
    _write(
        tmp_path / ".opencode" / "agents" / "reviewer.md",
        """---
description: Review only
mode: subagent
permission:
  edit: deny
  bash: deny
  webfetch: deny
---
Review the diff. Do not edit.
""",
    )
    agents = discover_agents(tmp_path)
    assert len(agents) == 1
    reviewer = agents[0]
    assert reviewer.name == "reviewer"
    assert reviewer.mode == "subagent"
    assert "write_file" in reviewer.denied_tools
    assert "run_shell" in reviewer.denied_tools
    assert "browser_*" in reviewer.denied_tools
    declared = reviewer.to_declared()
    assert declared.body.startswith("Review the diff")
    assert declared.denied_tools


def test_v2_permissions_and_tools_map(tmp_path: Path) -> None:
    _write(
        tmp_path / ".opencode" / "agents" / "team" / "reviewer.md",
        """---
description: V2 reviewer
mode: subagent
permissions:
  - action: edit
    resource: "*"
    effect: deny
tools:
  write: false
  bash: false
---
Review only.
""",
    )
    agent = discover_agents(tmp_path)[0]
    assert agent.name == "team/reviewer"
    assert "write_file" in agent.denied_tools
    assert "run_shell" in agent.denied_tools


def test_declared_agents_include_opencode_and_claude_wins(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / ".claude" / "agents" / "reviewer.md",
        """---
name: reviewer
description: Claude reviewer
---
Claude body.
""",
    )
    _write(
        tmp_path / ".opencode" / "agents" / "reviewer.md",
        """---
description: OpenCode reviewer
---
OpenCode body.
""",
    )
    _write(
        tmp_path / ".opencode" / "agents" / "planner.md",
        """---
description: Planner
---
Planner body.
""",
    )
    agents = discover_declared_agents(tmp_path)
    by_name = {agent.name: agent for agent in agents}
    assert by_name["reviewer"].description == "Claude reviewer"
    assert by_name["planner"].description == "Planner"
    assert get_declared_agent(tmp_path, "planner") is not None
    assert get_declared_agent(tmp_path, "missing") is None


def test_inline_and_file_commands_are_merged(tmp_path: Path) -> None:
    _write(
        tmp_path / "opencode.json",
        json.dumps(
            {
                "command": {
                    "review": {
                        "template": "Review $ARGUMENTS",
                        "description": "Review a path",
                    },
                    "ship": {
                        "template": "Ship it",
                        "description": "Ship",
                    },
                }
            }
        ),
    )
    _write(
        tmp_path / ".opencode" / "commands" / "review.md",
        """---
description: File review wins
---
Review the file: $ARGUMENTS
""",
    )
    commands = discover_commands(tmp_path)
    by_name = {command.name: command for command in commands}
    assert "review" in by_name
    assert "ship" in by_name
    assert "File review wins" in by_name["review"].description
    assert expand_command(tmp_path, "/ship now") == "Ship it"
    assert expand_command(tmp_path, "/review src") == "Review the file: src"


def test_inline_agent_prompt_file_is_resolved(tmp_path: Path) -> None:
    _write(
        tmp_path / "opencode.json",
        json.dumps(
            {
                "agent": {
                    "reviewer": {
                        "description": "Inline reviewer",
                        "mode": "subagent",
                        "prompt": "{file:./prompts/review.txt}",
                        "permission": {"edit": "deny", "bash": "deny"},
                    }
                }
            }
        ),
    )
    _write(tmp_path / "prompts" / "review.txt", "Be strict.\n")
    project = load_project(tmp_path)
    assert project.config_path is not None
    assert project.agents[0].body == "Be strict."
    assert "write_file" in project.agents[0].denied_tools


def test_project_status_reports_missing_config_without_throwing(tmp_path: Path) -> None:
    project = load_project(tmp_path)
    assert project.config_path is None
    assert project.agents == []
    assert project.commands == []
    assert project.errors == []


def test_jsonc_stripper_preserves_strings_with_commas_and_slashes() -> None:
    raw = '{"url":"https://x.test/a,b","note":"// not a comment"}'
    assert _strip_jsonc(raw) == raw
    assert find_config(Path("/tmp/does-not-exist")) is None
