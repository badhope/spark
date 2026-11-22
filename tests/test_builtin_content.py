from spark.builtin_content import ensure_workspace_defaults
from spark.core.context import load_system_prompt
from spark.plugins import build_plugin_tools, scan_plugins
from spark.skills import discover_skills
from spark.slash import discover_commands


def test_seed_builtin_skills_and_commands(tmp_path):
    result = ensure_workspace_defaults(tmp_path)

    assert result["skills"] >= 8
    assert result["commands"] >= 8
    assert result["plugins"] == 1
    assert len(discover_skills(tmp_path)) == result["skills"]
    assert len(discover_commands(tmp_path)) == result["commands"]
    assert (tmp_path / ".spark" / ".builtins-version").exists()


def test_seed_repo_health_plugin_is_discoverable_and_executable(tmp_path):
    ensure_workspace_defaults(tmp_path)
    plugin = tmp_path / ".spark" / "plugins" / "repo-health"
    assert (plugin / "spark-plugin.json").is_file()
    assert (plugin / "tool.py").stat().st_mode & 0o111
    assert (plugin / "commands" / "health.md").is_file()

    records = scan_plugins(tmp_path)
    assert [r.name for r in records] == ["repo-health"]
    assert records[0].error == ""
    assert len(records[0].manifest.tools) == 1

    schemas, handlers, issues = build_plugin_tools(["repo-health"], tmp_path)
    assert issues == []
    assert [s["function"]["name"] for s in schemas] == ["plugin__repo_health__scan"]


def test_repo_health_scan_reports_real_findings_without_secret_values(tmp_path):
    ensure_workspace_defaults(tmp_path)
    (tmp_path / "app.py").write_text(
        "import pdb\npdb.set_trace()\n# TODO fix this\n"
        'API_KEY = "sk-12345678901234567890"\n',
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    _, handlers, _ = build_plugin_tools(["repo-health"], tmp_path)
    result = handlers["plugin__repo_health__scan"]({"max_files": 100})

    assert result.ok is True
    assert result.payload["summary"]["possible_secrets"] >= 1
    assert result.payload["summary"]["debug_leftovers"] >= 1
    assert result.payload["summary"]["test_files"] == 1
    assert "sk-12345678901234567890" not in result.payload["content"]


def test_web_seed_enables_repo_health_once(tmp_path, monkeypatch):
    from spark import plugins as plugins_module

    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        plugins_module, "set_plugin_enabled", lambda name, enabled: calls.append((name, enabled))
    )

    ensure_workspace_defaults(tmp_path, enable_plugins=True)
    assert calls == [("repo-health", True)]
    assert (tmp_path / ".spark" / ".builtin-plugin-enabled").exists()

    ensure_workspace_defaults(tmp_path, enable_plugins=True)
    assert calls == [("repo-health", True)]


def test_seed_does_not_overwrite_user_edits(tmp_path):
    ensure_workspace_defaults(tmp_path)
    skill = tmp_path / ".spark" / "skills" / "tdd-loop.md"
    skill.write_text("user edited", encoding="utf-8")
    manifest = tmp_path / ".spark" / "plugins" / "repo-health" / "spark-plugin.json"
    manifest.write_text('{"name":"repo-health"}', encoding="utf-8")

    second = ensure_workspace_defaults(tmp_path)

    assert second["skills"] == 0
    assert second["commands"] == 0
    assert second["plugins"] == 0
    assert skill.read_text(encoding="utf-8") == "user edited"
    assert manifest.read_text(encoding="utf-8") == '{"name":"repo-health"}'


def test_default_prompt_is_coding_expert_and_mentions_repo_context():
    prompt = load_system_prompt()
    assert "资深编程专家" in prompt
    assert "repo_context" in prompt
    assert "最小 diff" in prompt
