from __future__ import annotations

from pathlib import Path

import httpx

from spark.config import SparkConfig
from spark.pr import GitHubClient, RepoRef, build_context, format_context, parse_remote
from spark.sandbox import WorkdirSandbox
from spark.tools.pr_tools import PrNumberArgs, pr_view
from spark.tools.registry import ToolContext, ToolRegistry


def test_parse_remote_variants() -> None:
    assert parse_remote("https://github.com/acme/spark.git") == RepoRef("acme", "spark")
    assert parse_remote("git@github.com:acme/spark.git") == RepoRef("acme", "spark")
    assert parse_remote("ssh://git@github.com/acme/spark") == RepoRef("acme", "spark")
    assert parse_remote("https://gitcode.com/acme/spark.git") is None


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
