"""W1 regression: real stdio LSP protocol through the tool registry."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.lsp.impact import analyze_impact
from spark.lsp.workspace import WorkspaceEditError, apply_plans, plan_workspace_edit
from spark.models import ToolCall
from spark.sandbox import WorkdirSandbox
from spark.tools.registry import ToolContext, ToolRegistry

FAKE_SERVER = Path(__file__).with_name("fake_lsp_server.py")


def _registry(root: Path, *, with_server: bool = True) -> ToolRegistry:
    sandbox = WorkdirSandbox(root, SparkConfig())
    registry = ToolRegistry(ToolContext(sandbox=sandbox, config=SparkConfig()))
    registry.lsp_manager.overrides = {
        "python": [sys.executable, str(FAKE_SERVER)] if with_server else []
    }
    return registry


def _call(registry: ToolRegistry, name: str, **arguments) -> dict:
    result = asyncio.run(
        registry.execute(ToolCall(id="test", name=name, arguments=arguments))
    )
    return result.model_dump()


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "main.py").write_text("foo()\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("foo()\n", encoding="utf-8")
    (tmp_path / "test_main.py").write_text(
        "import main\nfrom other import foo\n\ndef test_it():\n    foo()\n",
        encoding="utf-8",
    )
    return tmp_path


def test_lsp_navigation_and_diagnostics(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    registry = _registry(root)
    try:
        definition = _call(
            registry, "lsp_definition", path="main.py", line=1, column=1
        )
        assert definition["ok"] is True
        assert definition["payload"]["source"] == "lsp"
        assert definition["payload"]["definitions"][0]["file"] == "main.py"

        references = _call(
            registry, "lsp_references", path="main.py", line=1, column=1
        )
        assert references["ok"] is True
        assert {item["file"] for item in references["payload"]["references"]} == {
            "main.py",
            "other.py",
        }

        hover = _call(registry, "lsp_hover", path="main.py", line=1, column=1)
        assert hover["ok"] is True
        assert "foo()" in hover["payload"]["contents"]

        symbols = _call(registry, "lsp_symbols", path="main.py")
        assert symbols["ok"] is True
        assert symbols["payload"]["symbols"][0]["name"] == "foo"

        workspace_symbols = _call(
            registry, "lsp_symbols", path="main.py", query="foo"
        )
        assert workspace_symbols["ok"] is True
        assert workspace_symbols["payload"]["symbols"][0]["file"] == "other.py"

        diagnostics = _call(registry, "lsp_diagnostics", path="main.py")
        assert diagnostics["ok"] is True
        assert diagnostics["payload"]["source"] == "lsp"
        assert diagnostics["payload"]["diagnostics"][0]["code"] == "FAKE002"

        # A write in the same turn reuses the already-approved live client and
        # attaches diagnostics, so the agent gets a red/green signal without starting
        # a new language server behind the approval gate.
        written = _call(
            registry, "write_file", path="main.py", content="foo()\n"
        )
        assert written["ok"] is True
        assert written["payload"]["diagnostics_count"] >= 1

        actions = _call(registry, "lsp_code_action", path="main.py", line=1, column=1)
        assert actions["ok"] is True
        assert [item["title"] for item in actions["payload"]["actions"]] == [
            "Replace foo",
            "Run server command",
        ]
    finally:
        registry.close()


def test_rename_dry_run_is_identical_to_apply(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    registry = _registry(root)
    try:
        before_main = (root / "main.py").read_text(encoding="utf-8")
        before_other = (root / "other.py").read_text(encoding="utf-8")

        preview = _call(
            registry,
            "lsp_rename",
            path="main.py",
            line=1,
            column=1,
            new_name="bar",
            dry_run=True,
        )
        assert preview["ok"] is True
        assert preview["payload"]["applied"] is False
        assert preview["payload"]["file_count"] == 2
        preview_diffs = {
            item["file"]: item["diff"] for item in preview["payload"]["files"]
        }
        assert (root / "main.py").read_text(encoding="utf-8") == before_main

        applied = _call(
            registry,
            "rename_symbol",
            path="main.py",
            line=1,
            column=1,
            new_name="bar",
            dry_run=False,
        )
        assert applied["ok"] is True
        assert applied["payload"]["applied"] is True
        assert applied["payload"]["written"] == ["main.py", "other.py"]
        applied_diffs = {
            item["file"]: item["diff"] for item in applied["payload"]["files"]
        }
        assert applied_diffs == preview_diffs
        assert "bar" in (root / "main.py").read_text(encoding="utf-8")
        assert "bar" in (root / "other.py").read_text(encoding="utf-8")
        assert before_other == "foo()\n"
    finally:
        registry.close()


def test_affected_tests_uses_reverse_imports(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    registry = _registry(root)
    try:
        result = _call(
            registry, "affected_tests", path="main.py", symbol="foo"
        )
        assert result["ok"] is True
        assert "test_main.py" in result["payload"]["affected_tests"]
        assert "test_main.py" in result["payload"]["importers"]
    finally:
        registry.close()


def test_without_server_degrades_visibly(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / "main.py").write_text("def foo():\n    pass\n", encoding="utf-8")
    registry = _registry(root, with_server=False)
    try:
        definition = _call(
            registry, "lsp_definition", path="main.py", line=1, column=5
        )
        assert definition["ok"] is True
        assert definition["payload"]["degraded"] is True
        assert definition["payload"]["definitions"]

        hover = _call(registry, "lsp_hover", path="main.py", line=1, column=5)
        assert hover["ok"] is False
        assert "no LSP server" in hover["payload"]["error"]

        preview = _call(
            registry,
            "lsp_rename",
            path="main.py",
            line=1,
            column=5,
            new_name="bar",
            dry_run=True,
        )
        assert preview["ok"] is True
        assert preview["payload"]["can_apply"] is False
        assert preview["payload"]["applied"] is False
    finally:
        registry.close()


@pytest.mark.parametrize("relative", ["../escape.py", "/etc/passwd"])
def test_lsp_rejects_paths_outside_workspace(tmp_path: Path, relative: str) -> None:
    root = _workspace(tmp_path)
    registry = _registry(root)
    try:
        result = _call(
            registry, "lsp_definition", path=relative, line=1, column=1
        )
        assert result["ok"] is False
    finally:
        registry.close()


def test_impact_handles_relative_python_import(tmp_path: Path) -> None:
    package = tmp_path / "mypkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "foo.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package / "bar.py").write_text("from . import foo\n", encoding="utf-8")
    (package / "test_bar.py").write_text("from . import bar\n", encoding="utf-8")
    result = analyze_impact(tmp_path, "mypkg/foo.py")
    assert "mypkg/bar.py" in result["importers"]
    assert "mypkg/test_bar.py" in result["affected_tests"]


def test_impact_handles_relative_js_import(tmp_path: Path) -> None:
    source = tmp_path / "web"
    source.mkdir()
    (source / "b.ts").write_text("export const value = 1;\n", encoding="utf-8")
    (source / "a.ts").write_text('import { value } from "./b";\n', encoding="utf-8")
    (source / "b.test.ts").write_text('import { value } from "./b";\n', encoding="utf-8")
    result = analyze_impact(tmp_path, "web/b.ts")
    assert "web/a.ts" in result["importers"]
    assert result["affected_tests"] == ["web/b.test.ts"]


def test_code_action_apply_only_uses_workspace_edit(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    registry = _registry(root)
    try:
        result = _call(
            registry,
            "lsp_code_action",
            path="main.py",
            line=1,
            column=1,
            apply=True,
        )
        assert result["ok"] is True
        assert result["payload"]["applied"][0]["written"] == ["main.py"]
        assert (root / "main.py").read_text(encoding="utf-8") == "fixed()\n"
        # The second action is a server command; it must never be executed.
        assert len(result["payload"]["applied"]) == 1
    finally:
        registry.close()


def test_apply_plans_aborts_before_writing_on_stale_snapshot(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    edit = {
        "changes": {
            (root / "main.py").as_uri(): [
                {
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 3},
                    },
                    "newText": "bar",
                }
            ],
            (root / "other.py").as_uri(): [
                {
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 3},
                    },
                    "newText": "bar",
                }
            ],
        }
    }
    plans = plan_workspace_edit(root, edit)
    (root / "other.py").write_text("foo(1)\n", encoding="utf-8")
    with pytest.raises(WorkspaceEditError):
        apply_plans(WorkdirSandbox(root, SparkConfig()), plans, dry_run=False)
    assert (root / "main.py").read_text(encoding="utf-8") == "foo()\n"


def test_server_detection_covers_six_languages(tmp_path: Path, monkeypatch) -> None:
    from spark.lsp.servers import detect_server, language_for_path

    def fake_which(command: str, root: Path | None = None) -> str:
        return f"/usr/bin/{command}"

    monkeypatch.setattr("spark.lsp.servers._which", fake_which)
    expected = {
        ".py": "python",
        ".ts": "typescript",
        ".go": "go",
        ".rs": "rust",
        ".c": "c",
        ".java": "java",
    }
    for suffix, language in expected.items():
        assert language_for_path(f"file{suffix}") == language
        spec = detect_server(language, tmp_path)
        assert spec is not None
        assert spec.command


def test_rename_across_twenty_files(tmp_path: Path) -> None:
    for index in range(20):
        (tmp_path / f"f{index}.py").write_text("foo()\n", encoding="utf-8")
    edit = {
        "changes": {
            (tmp_path / f"f{index}.py").as_uri(): [
                {
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 3},
                    },
                    "newText": "bar",
                }
            ]
            for index in range(20)
        }
    }
    plans = plan_workspace_edit(tmp_path, edit)
    assert len(plans) == 20
    apply_plans(WorkdirSandbox(tmp_path, SparkConfig()), plans, dry_run=True)
    assert (tmp_path / "f0.py").read_text(encoding="utf-8") == "foo()\n"
    outcome = apply_plans(WorkdirSandbox(tmp_path, SparkConfig()), plans, dry_run=False)
    assert len(outcome["written"]) == 20
    assert all(
        (tmp_path / f"f{index}.py").read_text(encoding="utf-8") == "bar()\n"
        for index in range(20)
    )
