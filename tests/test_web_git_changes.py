from __future__ import annotations

import subprocess
from pathlib import Path

from spark.web.git_api import git_changes


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_non_repo_is_a_normal_state(tmp_path: Path) -> None:
    report = git_changes(str(tmp_path))
    assert report["repo"] is False
    assert report["entries"] == []


def test_branch_status_and_diff(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-qm", "init")
    (tmp_path / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("new\n", encoding="utf-8")

    report = git_changes(str(tmp_path))
    assert report["repo"] is True
    assert {e["path"] for e in report["entries"]} == {"a.txt", "b.txt"}
    assert "two" in report["diff"]
    assert report["branch"]


def test_status_is_bounded(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    for i in range(450):
        (tmp_path / f"f{i}.txt").write_text("x\n", encoding="utf-8")
    report = git_changes(str(tmp_path))
    assert len(report["entries"]) <= 400
