from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from spark.config import SparkConfig
from spark.pr import (
    GitHubClient,
    PrError,
    RepoRef,
    build_context,
    format_context,
    format_review,
    parse_remote,
    post_review,
    review_brief,
)
from spark.sandbox import WorkdirSandbox
from spark.tools.pr_tools import PrNumberArgs, pr_view
from spark.tools.registry import ToolContext, ToolRegistry


def test_parse_remote_variants() -> None:
    assert parse_remote("https://github.com/acme/spark.git") == RepoRef("acme", "spark")
    assert parse_remote("git@github.com:acme/spark.git") == RepoRef("acme", "spark")
    assert parse_remote("ssh://git@github.com/acme/spark") == RepoRef("acme", "spark")
    assert parse_remote("https://gitcode.com/acme/spark.git") == RepoRef("acme", "spark", "gitcode")
    assert parse_remote("git@gitee.com:acme/spark.git") == RepoRef("acme", "spark", "gitee")


def test_build_context_tolerates_missing_comment_endpoints() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/pulls/7"):
            return httpx.Response(
                200,
                json={
                    "title": "Optional comments",
                    "state": "open",
                    "base": {"ref": "main"},
                    "head": {"ref": "feature"},
                },
            )
        if path.endswith("/pulls/7/files"):
            return httpx.Response(200, json=[{"filename": "a.py", "status": "modified"}])
        if path.endswith("/pulls/7/diff"):
            return httpx.Response(200, text="diff --git a/a.py b/a.py\n+print(1)\n")
        if path.endswith("/issues/7/comments") or path.endswith("/pulls/7/comments"):
            return httpx.Response(404, json={"message": "not found"})
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = GitHubClient(
        RepoRef("acme", "spark", "gitee"),
        token="t",
        transport=httpx.MockTransport(handler),
    )
    try:
        context = build_context(client, 7)
    finally:
        client.close()
    assert context["title"] == "Optional comments"
    assert context["changed_files"] == 1
    assert "print(1)" in context["diff"]
    assert context["comments"] == []


def test_pr_repo_override_infers_platform() -> None:
    from spark.cli import _pr_repo

    assert _pr_repo("gitcode.com/acme/spark", Path(".")).forge == "gitcode"
    assert _pr_repo("gitee.com/acme/spark", Path(".")).forge == "gitee"
    assert _pr_repo("github.com/acme/spark", Path(".")).forge == "github"
    assert _pr_repo("acme/spark", Path(".")).forge == "github"


def test_build_context_with_mock_transport() -> None:
    pr = {
        "title": "Add browser tool",
        "state": "open",
        "draft": False,
        "user": {"login": "alice"},
        "html_url": "https://github.com/acme/spark/pull/7",
        "base": {"ref": "main"},
        "head": {"ref": "feature/browser", "sha": "abc123"},
        "body": "Description",
        "additions": 12,
        "deletions": 3,
        "changed_files": 2,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/pulls/7") and "diff" in request.headers.get("accept", ""):
            return httpx.Response(200, text="diff --git a/a.py b/a.py\n+print(1)\n")
        if path.endswith("/pulls/7"):
            return httpx.Response(200, json=pr)
        if path.endswith("/pulls/7/files"):
            return httpx.Response(
                200,
                json=[
                    {
                        "filename": "src/a.py",
                        "status": "modified",
                        "additions": 12,
                        "deletions": 3,
                    }
                ],
            )
        if path.endswith("/issues/7/comments"):
            return httpx.Response(
                200,
                json=[{"user": {"login": "bob"}, "body": "Please add a test", "created_at": "now"}],
            )
        if path.endswith("/pulls/7/comments"):
            return httpx.Response(
                200,
                json=[
                    {
                        "user": {"login": "carol"},
                        "body": "nit",
                        "path": "src/a.py",
                        "line": 4,
                    }
                ],
            )
        if path.endswith("/commits/abc123/status"):
            return httpx.Response(200, json={"statuses": [{"context": "ci", "state": "success"}]})
        if path.endswith("/commits/abc123/check-runs"):
            return httpx.Response(
                200,
                json={
                    "check_runs": [
                        {"name": "pytest", "status": "completed", "conclusion": "success"}
                    ]
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = GitHubClient(RepoRef("acme", "spark"), token="", transport=httpx.MockTransport(handler))
    try:
        context = build_context(client, 7)
    finally:
        client.close()
    assert context["title"] == "Add browser tool"
    assert context["files"][0]["filename"] == "src/a.py"
    assert "print(1)" in context["diff"]
    assert context["comments"][1]["path"] == "src/a.py"
    assert context["checks"]["check_runs"][0]["conclusion"] == "success"
    rendered = format_context(context)
    assert "Add browser tool" in rendered and "## Diff" in rendered
    brief = review_brief(context)
    assert brief["stats"]["files"] == 1
    assert any("测试" in row["title"] for row in brief["findings"])
    assert "Checklist" in format_review(context, brief)


def test_pr_view_respects_network_access(tmp_path: Path) -> None:
    config = SparkConfig()
    config.agent.network_access = False
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=config)
    result = pr_view(ctx, PrNumberArgs(number=1))
    assert not result.ok
    assert "网络访问已关闭" in str(result.payload.get("error", ""))


def test_pr_tools_are_registered(tmp_path: Path) -> None:
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=SparkConfig())
    names = {item["function"]["name"] for item in ToolRegistry(ctx).schemas()}
    assert {"pr_context", "pr_view", "pr_diff", "pr_checks"} <= names


def test_post_review_requires_body() -> None:
    client = GitHubClient(RepoRef("acme", "spark"), token="t")
    try:
        with pytest.raises(PrError, match="不能为空"):
            post_review(client, 7, "  ")
    finally:
        client.close()


def test_post_review_sends_comment() -> None:
    import json

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/pulls/7/reviews")
        seen.append(json.loads(request.content))
        return httpx.Response(201, json={"id": 1, "html_url": "https://github.com/acme/spark/pull/7#review"})

    client = GitHubClient(RepoRef("acme", "spark"), token="t", transport=httpx.MockTransport(handler))
    try:
        result = post_review(client, 7, "Looks good", event="comment")
    finally:
        client.close()
    assert result["id"] == 1
    assert seen[0]["event"] == "COMMENT"
    assert seen[0]["body"] == "Looks good"


def test_gitcode_client_uses_patches_and_token_query() -> None:
    calls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        assert request.url.host in {"api.gitcode.com"}
        assert request.url.params.get("access_token") == "t"
        path = request.url.path
        if path.endswith("/pulls/7"):
            return httpx.Response(200, json={
                "title": "GitCode PR", "state": "open", "draft": False,
                "user": {"login": "alice"}, "html_url": "https://gitcode.com/acme/spark/pulls/7",
                "base": {"ref": "main"}, "head": {"ref": "feature"},
                "body": "body", "additions": 1, "deletions": 0, "changed_files": 1,
            })
        if path.endswith("/pulls/7/diff"):
            return httpx.Response(404, json={"message": "not found"})
        if path.endswith("/pulls/7/files"):
            return httpx.Response(200, json=[{"filename": "a.py", "status": "modified", "additions": 1, "deletions": 0, "patch": "@@ -0,0 +1 @@\n+x = 1"}])
        if path.endswith("/issues/7/comments"):
            return httpx.Response(200, json=[])
        if path.endswith("/pulls/7/comments"):
            return httpx.Response(200, json=[])
        raise AssertionError(f"unexpected {request.method} {path}")

    client = GitHubClient(RepoRef("acme", "spark", "gitcode"), token="t", transport=httpx.MockTransport(handler))
    try:
        context = build_context(client, 7)
    finally:
        client.close()
    assert context["title"] == "GitCode PR"
    assert "x = 1" in context["diff"]
    assert context["checks"] == {"statuses": [], "check_runs": []}


def test_stored_pr_token_roundtrip_and_env_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stat

    from spark.pr import (
        _token,
        clear_stored_token,
        set_stored_token,
        stored_token,
        token_source,
    )

    monkeypatch.setenv("SPARK_HOME", str(tmp_path))
    for name in ("GITHUB_TOKEN", "GH_TOKEN", "GITCODE_TOKEN", "GITEE_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    path = set_stored_token("github", "ghp_file_token")
    assert path == tmp_path / "pr-tokens.json"
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stored_token("github") == "ghp_file_token"
    assert _token("github") == "ghp_file_token"
    assert token_source("github") == f"本地凭据 {path}"

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_env_token")
    assert _token("github") == "ghp_env_token"
    assert token_source("github") == "环境变量 GITHUB_TOKEN"

    client = GitHubClient(RepoRef("acme", "spark", "github"))
    try:
        assert client._client.headers["Authorization"] == "Bearer ghp_env_token"
    finally:
        client.close()

    assert clear_stored_token("github") is True
    assert stored_token("github") == ""
    assert clear_stored_token("github") is False


def test_pr_token_cli_saves_without_echo(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from spark.cli import app

    monkeypatch.setenv("SPARK_HOME", str(tmp_path))
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    result = CliRunner().invoke(app, ["pr", "token", "github"], input="ghp_cli_token\n")
    assert result.exit_code == 0, result.output
    assert "ghp_cli_token" not in result.output
    assert "权限 600" in result.output
    status = CliRunner().invoke(app, ["pr", "status", "--repo", "acme/spark", "--json"])
    assert status.exit_code == 0, status.output
    assert '"token_configured": true' in status.output
