from pathlib import Path

from spark.skills import SkillInfo, discover_skills, format_skill_invocation, inject_skills


def _write_skill(dir_path, name, content):
    skill_dir = dir_path / ".spark" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.md").write_text(content, encoding="utf-8")


def test_discover_no_skills_dir(tmp_path):
    skills = discover_skills(tmp_path)
    assert skills == []


def test_discover_single_skill_with_frontmatter(tmp_path):
    _write_skill(tmp_path, "refactor", """---
name: refactor-helper
description: Helps refactor code
---
You are a refactoring assistant.
""")
    skills = discover_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].name == "refactor-helper"
    assert skills[0].description == "Helps refactor code"
    assert "refactoring assistant" in skills[0].content.lower()


def test_discover_multiple_skills(tmp_path):
    _write_skill(tmp_path, "a", "---\nname: skill-a\n---\nContent A")
    _write_skill(tmp_path, "b", "---\nname: skill-b\n---\nContent B")
    skills = discover_skills(tmp_path)
    names = {s.name for s in skills}
    assert names == {"skill-a", "skill-b"}


def test_discover_nested_dir(tmp_path):
    """Skills in nested directories under .spark/skills/ are found."""
    nested = tmp_path / ".spark" / "skills" / "category" / "deep"
    nested.mkdir(parents=True)
    (nested / "util.md").write_text("---\nname: util\n---\nUtil skill")
    skills = discover_skills(tmp_path)
    assert any(s.name == "util" for s in skills)


def test_discover_skips_dotfiles(tmp_path):
    d = tmp_path / ".spark" / "skills" / "hidden"
    d.mkdir(parents=True)
    (d / ".secret.md").write_text("---\nname: secret\n---\nSecret")
    (d / "visible.md").write_text("---\nname: visible\n---\nVisible")
    skills = discover_skills(tmp_path)
    names = {s.name for s in skills}
    assert "secret" not in names
    assert "visible" in names


def test_discover_no_frontmatter_skipped(tmp_path):
    d = tmp_path / ".spark" / "skills" / "plain"
    d.mkdir(parents=True)
    (d / "plain.md").write_text("Just some content without frontmatter")
    skills = discover_skills(tmp_path)
    assert all(s.name != "plain" for s in skills)


def test_inject_skills_appends_xml():
    skills = [
        SkillInfo(name="test-skill", description="A test", content="Do X.", path=Path("/tmp/skill.md")),
    ]
    base = "You are a helpful assistant."
    result = inject_skills(base, skills)
    assert "<available_skills>" in result
    assert 'name="test-skill"' in result
    assert 'description="A test"' in result
    assert "load_skill" in result  # tells agent to use the tool
    assert base in result  # original preserved


def test_inject_empty_skills_returns_unchanged():
    base = "System prompt"
    assert inject_skills(base, []) == base


def test_format_skill_invocation():
    skill = SkillInfo(name="formatter", description="Format code", content="Format this", path=Path("/tmp/f.md"))
    result = format_skill_invocation(skill)
    assert "<agent_skill" in result
    assert 'name="formatter"' in result
    assert "Format this" in result


def test_discover_strips_quoted_frontmatter(tmp_path):
    _write_skill(tmp_path, "quoted", """---
name: "quoted-skill"
description: "Quoted description"
when_to_use: 'When testing quotes'
---
Body.
""")
    skills = discover_skills(tmp_path)
    assert skills[0].name == "quoted-skill"
    assert skills[0].description == "Quoted description"
    assert skills[0].when_to_use == "When testing quotes"
