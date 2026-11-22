from __future__ import annotations

import asyncio
from pathlib import Path

from spark.config import SparkConfig
from spark.models import ToolCall
from spark.policy import READONLY_TOOLS
from spark.sandbox import WorkdirSandbox
from spark.tools.registry import ToolContext, ToolRegistry


def _registry(root: Path) -> ToolRegistry:
    sandbox = WorkdirSandbox(root, SparkConfig())
    return ToolRegistry(ToolContext(sandbox=sandbox, config=SparkConfig()))


def _call(registry: ToolRegistry, name: str, **arguments) -> dict:
    result = asyncio.run(
        registry.execute(ToolCall(id="test", name=name, arguments=arguments))
    )
    return result.model_dump()


def test_repo_context_summarizes_project(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\n[tool.ruff]\nline-length=100\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_main.py").write_text(
        "from src.main import *\n\ndef test_it():\n    assert True\n",
        encoding="utf-8",
    )
    (tmp_path / ".spark").mkdir()
    (tmp_path / ".spark" / "secret.md").write_text("hidden", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")

    result = _call(_registry(tmp_path), "repo_context")

    assert result["ok"] is True
    payload = result["payload"]
    assert payload["data"]["package_manager"] == "pip"
    assert any(item["name"] == "Python" for item in payload["data"]["languages"])
    assert "python -m pytest" in payload["data"]["commands"]["test"]
    assert "ruff check ." in payload["data"]["commands"]["lint"]
    assert "src/main.py" in payload["content"]
    assert "SECRET=1" not in payload["content"]
    assert ".spark" not in payload["data"]["top_level"]


def test_repo_context_counts_all_dirty_files_not_just_preview(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    for i in range(35):
        (tmp_path / f"dirty-{i:02d}.txt").write_text("x\n", encoding="utf-8")

    result = _call(_registry(tmp_path), "repo_context")

    assert result["payload"]["data"]["git"]["dirty_count"] == 35
    assert "未提交: 35 项" in result["payload"]["content"]
    assert len(result["payload"]["data"]["git"]["dirty_files"]) == 30


def test_repo_context_is_readonly() -> None:
    assert "repo_context" in READONLY_TOOLS
