"""Declarative sub-agents ("file as subagent", Claude-Code convention).

Scans ``<worktree>/.claude/agents/*.md`` (project level) and
``~/.claude/agents/*.md`` (user level).  Each Markdown file is one sub-agent:

* frontmatter::

      ---
      name: reader            # required, the spawn handle
      description: ...        # one-line summary shown to the main agent
      when_to_use: ...        # optional hint for when to delegate
      tools: read_file, grep  # optional comma/space list; default = all tools
      ---

* the body (everything after the closing ``---``) is the sub-agent's own
  system prompt — its role, rules and output format.

Parsing rules mirror :mod:`spark.skills` (no YAML dependency: only simple
``key: value`` frontmatter lines are read).  Unlike skills, a declared agent
file must be semantically valid — missing frontmatter, missing ``name`` or an
empty body raise :class:`AgentFileError`; discovery is tolerant (one bad file
never kills the main loop) while spawning resolves strictly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

# Reuse the exact frontmatter precedent from skills.py: single source for the
# ``---`` delimiter grammar and key/value splitting.
from spark.skills import _parse_frontmatter

logger = logging.getLogger(__name__)

PROJECT_AGENTS_DIR = Path(".claude") / "agents"


class AgentFileError(ValueError):
    """A declared-agent file failed validation (frontmatter/name/body)."""


@dataclass
class DeclaredAgent:
    name: str
    description: str
    body: str
    path: Path
    when_to_use: str = ""
    tools: list[str] = field(default_factory=list)
    #: Optional model id for this specialist. Empty means "inherit the main
    #: model". A cheap reviewer or a strong architect can each declare their
    #: own, which is what the competitors' subagent-level model selection does.
    model: str = ""


def _parse_tools(raw: str) -> list[str]:
    return [t.strip() for t in raw.replace(",", " ").split() if t.strip()]


def parse_agent_file(path: Path) -> DeclaredAgent:
    """Strictly parse one ``*.md`` file into a :class:`DeclaredAgent`.

    Raises :class:`AgentFileError` when the file has no frontmatter block,
    no usable ``name``, or an empty body.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise AgentFileError(f"{path}: unreadable: {exc}") from exc
    parsed = _parse_frontmatter(text)
    if parsed is None:
        raise AgentFileError(f"{path}: missing YAML frontmatter (--- ... ---)")
    meta, body = parsed
    name = (meta.get("name") or "").strip() or path.stem.strip()
    if not name:
        raise AgentFileError(f"{path}: frontmatter is missing 'name'")
    stripped_body = body.strip()
    if not stripped_body:
        raise AgentFileError(f"{path}: empty body — a sub-agent needs instructions")
    return DeclaredAgent(
        name=name,
        description=(meta.get("description") or "").strip(),
        when_to_use=(meta.get("when_to_use") or "").strip(),
        tools=_parse_tools(meta.get("tools", "")),
        model=(meta.get("model") or "").strip(),
        body=stripped_body,
        path=path,
    )


def default_global_dir() -> Path:
    """User-level agents dir (~/.claude/agents), overridable in tests."""
    return Path.home() / ".claude" / "agents"


def discover_declared_agents(
    workdir: Path,
    *,
    global_dir: Path | None = None,
    strict: bool = False,
) -> list[DeclaredAgent]:
    """Discover declared sub-agents under ``.claude/agents/``.

    Project-level files win over user-level files on name collision.  By
    default invalid files are skipped with a warning (tolerant discovery so a
    broken demo file never breaks the main loop); pass ``strict=True`` to let
    :class:`AgentFileError` propagate.
    """
    global_dir = global_dir or default_global_dir()
    dirs: list[tuple[Path, bool]] = [
        (workdir / PROJECT_AGENTS_DIR, True),
        (global_dir, False),
    ]
    agents: list[DeclaredAgent] = []
    seen: set[str] = set()
    for d, inside in dirs:
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            if md.name.startswith("."):
                continue
            try:
                agent = parse_agent_file(md)
            except AgentFileError as exc:
                if strict:
                    raise
                logger.warning("skipping invalid agent file: %s", exc)
                continue
            if agent.name in seen:
                continue  # project level already won this name
            seen.add(agent.name)
            agents.append(agent)
    return agents


def get_declared_agent(
    workdir: Path,
    name: str,
    *,
    global_dir: Path | None = None,
) -> DeclaredAgent | None:
    """Resolve one declared agent by name; its file is parsed strictly.

    Looks up ``<name>.md`` directly (project level first, then user level) so
    a file that *exists* but fails validation raises :class:`AgentFileError` —
    the task tool surfaces it as "invalid agent file ..." instead of silently
    degrading to "unknown".  Returns ``None`` only when no such file exists.
    """
    name = name.strip()
    if name.endswith(".md"):
        name = name[:-3]
    name = name.strip()
    if not name:
        return None
    global_dir = global_dir or default_global_dir()
    for path in (workdir / PROJECT_AGENTS_DIR / f"{name}.md", global_dir / f"{name}.md"):
        if path.is_file():
            return parse_agent_file(path)
    return None


def inject_subagents(system_prompt: str, agents: list[DeclaredAgent]) -> str:
    """Append an ``<available_subagents>`` XML block (mirrors inject_skills)."""
    if not agents:
        return system_prompt
    newline = "" if system_prompt.endswith("\n") else "\n"
    block = [
        f"{newline}<available_subagents>",
        "  To delegate a self-contained task to a specialist, call the `task` "
        'tool with {"agent": "<name>", "prompt": "..."}. Use "tasks" with '
        'items {"agent": "<name>", "prompt": "..."} to run several in parallel.',
    ]
    for a in agents:
        attr = f' when_to_use="{a.when_to_use}"' if a.when_to_use else ""
        model_attr = f' model="{a.model}"' if a.model else ""
        block.append(
            f'  <subagent name="{a.name}" description="{a.description}"{attr}{model_attr}/>'
        )
    block.append("</available_subagents>\n")
    return system_prompt + "\n".join(block)


def subagent_system_prompt(agent: DeclaredAgent) -> str:
    """Wrap a declared agent's body so it becomes its own system-prompt slot."""
    return (
        f"<declared_subagent name=\"{agent.name}\" path=\"{agent.path}\">\n"
        f"{agent.body}\n"
        f"</declared_subagent>"
    )
