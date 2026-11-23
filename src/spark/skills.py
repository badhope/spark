from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SkillInfo:
    name: str
    description: str
    content: str
    path: Path
    when_to_use: str = ""


_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    """Parse simple ``key: value`` YAML frontmatter, return (meta, body).

    No external dependencies; only ``key: value`` lines inside the two
    ``---`` delimiters are read.  Everything after the second ``---`` is
    the skill body.
    """
    match = _FM_RE.match(text)
    if not match:
        return None
    front, body = match.group(1), match.group(2)
    meta: dict[str, str] = {}
    for line in front.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            val = val.strip()
            # YAML frontmatter commonly quotes values. The rest of this module
            # treats them as plain strings and embeds them in XML attributes, so
            # strip a matching pair of quotes instead of leaking `"..."` into
            # the model prompt.
            if len(val) >= 2 and val[0] == val[-1] and val[0] in {"'", '"'}:
                val = val[1:-1]
            meta[key.strip()] = val
    return meta, body


SKILL_ROOTS = (
    Path(".spark") / "skills",
    Path(".claude") / "skills",
)


def discover_skills(workdir: Path) -> list[SkillInfo]:
    """Scan project skill directories, including Claude Code compatibility.

    Spark's native location wins on a name collision; ``.claude/skills`` and
    ``~/.claude/skills`` are read-only compatibility roots so existing
    Claude Code projects do not need to duplicate their skills.
    """
    roots = [workdir / rel for rel in SKILL_ROOTS]
    roots.append(Path.home() / ".claude" / "skills")

    skills: list[SkillInfo] = []
    seen: set[str] = set()
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(sorted(root.rglob("*.md")))
    for md in files:
        if md.name.startswith("."):
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        parsed = _parse_frontmatter(text)
        if parsed is None:
            continue
        meta, body = parsed
        name = meta.get("name") or md.stem
        if name in seen:
            continue
        seen.add(name)
        skills.append(
            SkillInfo(
                name=name,
                description=meta.get("description", ""),
                content=body.strip(),
                path=md,
                when_to_use=meta.get("when_to_use", ""),
            )
        )
    return skills


def inject_skills(system_prompt: str, skills: list[SkillInfo]) -> str:
    """Append an ``<available_skills>`` XML block to the current system prompt.

    Only the skill name and description are included; the LLM should call the
    ``load_skill`` tool to retrieve the full content of a skill it wants to use.
    A newline is prepended when the input does not already end with one.
    """
    if not skills:
        return system_prompt
    newline = "" if system_prompt.endswith("\n") else "\n"
    block = [
        f"{newline}<available_skills>",
        "  To use a skill, call the `load_skill` tool with the skill name.",
    ]
    for s in skills:
        attr = f" when_to_use=\"{s.when_to_use}\"" if s.when_to_use else ""
        block.append(
            f"  <skill name=\"{s.name}\" description=\"{s.description}\"{attr}/>"
        )
    block.append("</available_skills>\n")
    return system_prompt + "\n".join(block)


def format_skill_invocation(skill: SkillInfo) -> str:
    """Wrap a skill body so the LLM executes it as a self-contained instruction."""
    return (
        f"<agent_skill name=\"{skill.name}\" "
        f"description=\"{skill.description}\" "
        f"path=\"{skill.path}\">\n{skill.content}\n</agent_skill>"
    )
