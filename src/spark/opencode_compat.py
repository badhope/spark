"""OpenCode project compatibility.

Spark already speaks Claude Code's file conventions.  OpenCode uses the same
"Markdown + frontmatter" idea but a different directory layout and JSONC config.
This module reads those assets directly so a team can keep its OpenCode agents,
commands and MCP declarations without copying prompts into a second format.

The mapping is intentionally narrow and explicit:

* ``.opencode/agents/**/*.md`` -> Spark declared sub-agents (body = system prompt)
* ``.opencode/commands/*.md`` -> Spark custom slash commands (body = template)
* ``opencode.json(c)`` inline ``agent`` / ``command`` entries -> same objects
* OpenCode ``deny`` permissions -> Spark tool denylists (glob-aware)
* ``mcp`` entries -> exposed as a normalized server map for the caller

It does not pretend to implement OpenCode's plugin runtime or its provider
registry.  Unsupported fields stay visible in :class:`OpenCodeProject` instead
of being silently discarded.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from spark.agents import DeclaredAgent
from spark.skills import _parse_frontmatter

CONFIG_NAMES = ("opencode.json", "opencode.jsonc")
OPENCODE_DIR = Path(".opencode")
AGENTS_DIR = OPENCODE_DIR / "agents"
COMMANDS_DIR = OPENCODE_DIR / "commands"
_MAX_AGENT_FILES = 200
_MAX_COMMAND_FILES = 200

# OpenCode's permission vocabulary -> Spark tool names.  Globs are deliberate:
# ``browser_*`` covers every browser tool without enumerating them here.
_ACTION_DENY_TOOLS: dict[str, list[str]] = {
    "edit": ["write_file", "apply_patch", "apply_edit", "notebook_edit"],
    "bash": [
        "run_shell", "bg_start", "bg_kill", "bg_list", "bg_output",
        "pty_start", "pty_write", "pty_read", "pty_kill", "pty_list",
    ],
    "webfetch": ["web_fetch", "web_search", "browser_*"],
    "read": ["read_file", "read_notebook"],
    "*": ["*"],
}


@dataclass
class OpenCodeAgent:
    name: str
    description: str
    body: str
    path: Path
    mode: str = ""
    model: str = ""
    permissions: dict[str, str] = field(default_factory=dict)
    denied_tools: list[str] = field(default_factory=list)

    def to_declared(self) -> DeclaredAgent:
        return DeclaredAgent(
            name=self.name,
            description=self.description,
            body=self.body,
            path=self.path,
            model=self.model,
            denied_tools=self.denied_tools,
        )


@dataclass
class OpenCodeCommand:
    name: str
    description: str
    body: str
    path: Path
    agent: str = ""
    model: str = ""


@dataclass
class OpenCodeProject:
    root: Path
    config_path: Path | None = None
    model: str = ""
    small_model: str = ""
    default_agent: str = ""
    agents: list[OpenCodeAgent] = field(default_factory=list)
    commands: list[OpenCodeCommand] = field(default_factory=list)
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    instructions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def find_config(root: Path) -> Path | None:
    """Find the nearest project config; never walk above the workdir."""
    root = root.resolve()
    candidates = [root / name for name in CONFIG_NAMES]
    candidates.extend(root / OPENCODE_DIR / name for name in CONFIG_NAMES)
    for path in candidates:
        if path.is_file():
            return path
    return None


def _strip_comments(text: str) -> str:
    """Remove // and /* */ comments while preserving string literals."""
    out: list[str] = []
    i = 0
    in_string = False
    escaped = False
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                if text[i] == "\n":
                    out.append("\n")
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_trailing_commas(text: str) -> str:
    """Remove commas before } or ] without touching string contents."""
    out: list[str] = []
    i = 0
    in_string = False
    escaped = False
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_jsonc(text: str) -> str:
    """Remove comments and trailing commas without touching string contents."""
    return _strip_trailing_commas(_strip_comments(text))


def load_config(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    data = json.loads(_strip_jsonc(text))
    if not isinstance(data, dict):
        raise ValueError("OpenCode config must be a JSON object")
    return data


def _read_frontmatter_lines(text: str) -> tuple[dict[str, str], str, list[str]]:
    parsed = _parse_frontmatter(text)
    if parsed is None:
        return {}, text, []
    meta, body = parsed
    match = re.match(r"^\ufeff?\s*---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", text, re.S)
    lines = match.group(1).splitlines() if match else []
    return meta, body, lines


def _parse_permission_map(lines: list[str]) -> dict[str, str]:
    """Read both OpenCode v1 ``permission:`` maps and v2 ``permissions:`` lists."""
    permissions: dict[str, str] = {}
    current_action = ""
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "action":
            current_action = value.strip("\"'")
        elif key == "effect" and current_action:
            permissions[current_action] = value.strip("\"'")
            current_action = ""
        elif key in {"edit", "bash", "webfetch", "read"} and value:
            permissions[key] = value.strip("\"'")
    return permissions


def _parse_tool_map(lines: list[str]) -> dict[str, bool]:
    tools: dict[str, bool] = {}
    in_tools = False
    for raw in lines:
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        if stripped.startswith("tools:"):
            in_tools = True
            continue
        if in_tools:
            if indent == 0 and not stripped.startswith("- "):
                in_tools = False
                continue
            key, _, value = stripped.partition(":")
            if key.strip() and value.strip().lower() in {"true", "false"}:
                tools[key.strip()] = value.strip().lower() == "true"
    return tools


def _denied_tools(permissions: dict[str, str], tools: dict[str, bool]) -> list[str]:
    denied: list[str] = []
    for action, effect in permissions.items():
        if effect == "deny":
            denied.extend(_ACTION_DENY_TOOLS.get(action, [action]))
    for tool, enabled in tools.items():
        if not enabled:
            denied.extend(_ACTION_DENY_TOOLS.get(tool, [tool]))
    # Stable de-duplication matters for deterministic prompt/tool ordering.
    return list(dict.fromkeys(denied))


def _agent_from_markdown(path: Path, agents_root: Path) -> OpenCodeAgent | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    meta, body, lines = _read_frontmatter_lines(text)
    body = body.strip()
    if not body:
        return None
    rel = path.relative_to(agents_root).with_suffix("").as_posix()
    name = (meta.get("name") or rel).strip()
    permissions = _parse_permission_map(lines)
    tools = _parse_tool_map(lines)
    return OpenCodeAgent(
        name=name,
        description=(meta.get("description") or "").strip(),
        body=body,
        path=path,
        mode=(meta.get("mode") or "").strip(),
        model=(meta.get("model") or "").strip(),
        permissions=permissions,
        denied_tools=_denied_tools(permissions, tools),
    )


def discover_agents(root: Path) -> list[OpenCodeAgent]:
    agents_root = root / AGENTS_DIR
    if not agents_root.is_dir():
        return []
    agents: list[OpenCodeAgent] = []
    for path in sorted(agents_root.rglob("*.md"))[:_MAX_AGENT_FILES]:
        if path.name.startswith("."):
            continue
        agent = _agent_from_markdown(path, agents_root)
        if agent is not None:
            agents.append(agent)
    return agents


def _resolve_file_ref(value: str, base: Path) -> str:
    match = re.fullmatch(r"\s*\{file:(.*)\}\s*", value)
    if not match:
        return value
    candidate = (base / match.group(1).strip()).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError:
        return value
    try:
        return candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return value


def _agent_from_config(
    name: str, item: dict[str, Any], config_path: Path
) -> OpenCodeAgent | None:
    body = str(item.get("system") or item.get("prompt") or "").strip()
    if not body:
        return None
    body = _resolve_file_ref(body, config_path.parent).strip()
    permissions: dict[str, str] = {}
    raw_permissions = item.get("permissions") or item.get("permission")
    if isinstance(raw_permissions, dict):
        permissions = {str(k): str(v) for k, v in raw_permissions.items()}
    elif isinstance(raw_permissions, list):
        for entry in raw_permissions:
            if not isinstance(entry, dict):
                continue
            action = str(entry.get("action") or "").strip()
            effect = str(entry.get("effect") or "").strip()
            if action and effect:
                permissions[action] = effect
    raw_tools = item.get("tools")
    tools: dict[str, Any] = raw_tools if isinstance(raw_tools, dict) else {}
    denied = _denied_tools(permissions, {str(k): bool(v) for k, v in tools.items()})
    return OpenCodeAgent(
        name=name,
        description=str(item.get("description") or "").strip(),
        body=body,
        path=config_path,
        mode=str(item.get("mode") or "").strip(),
        model=str(item.get("model") or "").strip(),
        permissions=permissions,
        denied_tools=denied,
    )


def _command_from_config(
    name: str, item: dict[str, Any], config_path: Path
) -> OpenCodeCommand | None:
    if not isinstance(item, dict):
        return None
    body = str(item.get("template") or "").strip()
    if not body:
        return None
    return OpenCodeCommand(
        name=name,
        description=str(item.get("description") or "").strip(),
        body=_resolve_file_ref(body, config_path.parent).strip(),
        path=config_path,
        agent=str(item.get("agent") or "").strip(),
        model=str(item.get("model") or "").strip(),
    )


def _command_from_markdown(path: Path) -> OpenCodeCommand | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    meta, body = _parse_frontmatter(text) or ({}, text)
    body = body.strip()
    if not body:
        return None
    return OpenCodeCommand(
        name=(meta.get("name") or path.stem).strip().lower(),
        description=(meta.get("description") or "").strip(),
        body=body,
        path=path,
        agent=(meta.get("agent") or "").strip(),
        model=(meta.get("model") or "").strip(),
    )


def discover_commands(root: Path) -> list[OpenCodeCommand]:
    commands_root = root / COMMANDS_DIR
    if not commands_root.is_dir():
        return []
    commands: list[OpenCodeCommand] = []
    for path in sorted(commands_root.glob("*.md"))[:_MAX_COMMAND_FILES]:
        if path.name.startswith("."):
            continue
        command = _command_from_markdown(path)
        if command is not None:
            commands.append(command)
    return commands


def _apply_project_config(
    project: OpenCodeProject, config: dict[str, Any], config_path: Path
) -> None:
    project.model = str(config.get("model") or "")
    project.small_model = str(config.get("small_model") or "")
    project.default_agent = str(config.get("default_agent") or "")
    raw_mcp = config.get("mcp")
    if isinstance(raw_mcp, dict):
        project.mcp_servers = raw_mcp
    raw_instructions = config.get("instructions")
    if isinstance(raw_instructions, list):
        project.instructions = [str(x) for x in raw_instructions if x]
    raw_agents = config.get("agent") or config.get("agents") or {}
    if isinstance(raw_agents, dict):
        for name, item in raw_agents.items():
            if isinstance(item, dict):
                agent = _agent_from_config(str(name), item, config_path)
                if agent is not None:
                    project.agents.append(agent)
    raw_commands = config.get("command") or config.get("commands") or {}
    if isinstance(raw_commands, dict):
        for name, item in raw_commands.items():
            command = _command_from_config(str(name), item, config_path)
            if command is not None:
                project.commands.append(command)


def _apply_file_assets(project: OpenCodeProject, root: Path) -> None:
    # File-based assets win on name collision over inline config entries.
    seen_agents = {agent.name for agent in project.agents}
    for agent in discover_agents(root):
        if agent.name not in seen_agents:
            project.agents.append(agent)
            seen_agents.add(agent.name)
    seen_commands = {command.name for command in project.commands}
    for command in discover_commands(root):
        if command.name not in seen_commands:
            project.commands.append(command)
            seen_commands.add(command.name)


def load_project(root: Path) -> OpenCodeProject:
    """Read OpenCode assets; invalid config is reported, not fatal."""
    root = root.resolve()
    project = OpenCodeProject(root=root)
    config_path = find_config(root)
    if config_path is not None:
        project.config_path = config_path
        try:
            config = load_config(config_path)
        except (OSError, ValueError) as exc:
            project.errors.append(f"{config_path}: {exc}")
            config = {}
        _apply_project_config(project, config, config_path)
    _apply_file_assets(project, root)
    return project

def to_declared_agents(project: OpenCodeProject) -> list[DeclaredAgent]:
    return [agent.to_declared() for agent in project.agents]


__all__ = [
    "AGENTS_DIR",
    "COMMANDS_DIR",
    "OpenCodeAgent",
    "OpenCodeCommand",
    "OpenCodeProject",
    "discover_agents",
    "discover_commands",
    "find_config",
    "load_config",
    "load_project",
    "to_declared_agents",
]
