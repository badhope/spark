"""GitHub Pull Request context: remote parsing, REST client, compact context.

The client is deliberately read-only: it fetches PR metadata, files, diff,
comments and checks so the agent can review without a second push path. All
external text returned to the model is wrapped in the untrusted fence by the
tool layer, not here.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from spark.errors import SparkError
from spark.proc import run_bounded

GITHUB_API = "https://api.github.com"
_REMOTE_RE = re.compile(
    r"(?:github\.com[:/])(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


class PrError(SparkError):
    """A PR lookup/API failure with a user-readable message."""


@dataclass(frozen=True)
class RepoRef:
    owner: str
    repo: str

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"


def parse_remote(url: str) -> RepoRef | None:
    """Parse a GitHub remote URL (https, ssh://, git@) into owner/repo."""
    value = (url or "").strip()
    if not value:
        return None
    match = _REMOTE_RE.search(value)
    if match:
        return RepoRef(match.group("owner"), match.group("repo"))
    parsed = urlparse(value)
    if parsed.hostname == "github.com":
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2:
            repo = parts[1][:-4] if parts[1].endswith(".git") else parts[1]
            return RepoRef(parts[0], repo)
    return None


def find_remote(workdir) -> str:
    result = run_bounded(
        ["git", "remote", "get-url", "origin"],
        cwd=workdir,
        timeout=10,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().splitlines()[0]
    raise PrError("当前目录没有 git origin；请在仓库内运行，或使用 --repo owner/name")


def find_repo(workdir) -> RepoRef:
    remote = find_remote(workdir)
    repo = parse_remote(remote)
    if repo is None:
        raise PrError(f"只支持 GitHub remote，当前 origin: {remote}")
    return repo


def _token() -> str:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


class GitHubClient:
    """Small read-only GitHub REST facade with injectable transport for tests."""

    def __init__(
        self,
        repo: RepoRef,
        token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.repo = repo
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "spark-pr-context",
        }
        token = token if token is not None else _token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=GITHUB_API,
            headers=headers,
            timeout=25.0,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, *, accept: str | None = None, **params: Any) -> Any:
        headers = {"Accept": accept} if accept else None
        try:
            response = self._client.get(path, headers=headers, params=params or None)
        except httpx.HTTPError as exc:
            raise PrError(f"GitHub API 请求失败: {exc}") from exc
        if response.status_code == 404:
            raise PrError(f"GitHub 资源不存在: {path}")
        if response.status_code in (401, 403):
            raise PrError(
                "GitHub API 拒绝了请求（检查 GITHUB_TOKEN 权限或速率限制）"
            )
        if response.status_code >= 400:
            raise PrError(f"GitHub API {response.status_code}: {response.text[:300]}")
        if accept and "diff" in accept:
            return response.text
        try:
            return response.json()
        except ValueError as exc:
            raise PrError("GitHub API 返回非 JSON 内容") from exc

    def pull(self, number: int) -> dict[str, Any]:
        return self._get(f"/repos/{self.repo.slug}/pulls/{number}")

    def diff(self, number: int) -> str:
        return self._get(
            f"/repos/{self.repo.slug}/pulls/{number}",
            accept="application/vnd.github.v3.diff",
        )

    def files(self, number: int) -> list[dict[str, Any]]:
        data = self._get(
            f"/repos/{self.repo.slug}/pulls/{number}/files",
            per_page=100,
        )
        return data if isinstance(data, list) else []

    def issue_comments(self, number: int) -> list[dict[str, Any]]:
        data = self._get(
            f"/repos/{self.repo.slug}/issues/{number}/comments", per_page=50
        )
        return data if isinstance(data, list) else []

    def review_comments(self, number: int) -> list[dict[str, Any]]:
        data = self._get(
            f"/repos/{self.repo.slug}/pulls/{number}/comments", per_page=50
        )
        return data if isinstance(data, list) else []

    def checks(self, number: int) -> dict[str, Any]:
        head = self._get(f"/repos/{self.repo.slug}/pulls/{number}")
        sha = str((head.get("head") or {}).get("sha") or "")
        if not sha:
            return {"statuses": [], "check_runs": []}
        statuses: list[dict[str, Any]] = []
        try:
            raw = self._get(f"/repos/{self.repo.slug}/commits/{sha}/status")
            statuses = raw.get("statuses") or [] if isinstance(raw, dict) else []
        except PrError:
            statuses = []
        check_runs: list[dict[str, Any]] = []
        try:
            raw = self._get(f"/repos/{self.repo.slug}/commits/{sha}/check-runs")
            runs = raw.get("check_runs") or [] if isinstance(raw, dict) else []
            check_runs = [
                {
                    "name": run.get("name", ""),
                    "status": run.get("status", ""),
                    "conclusion": run.get("conclusion", ""),
                    "url": (run.get("html_url") or ""),
                }
                for run in runs
                if isinstance(run, dict)
            ]
        except PrError:
            check_runs = []
        return {"statuses": statuses, "check_runs": check_runs}


def build_context(
    client: GitHubClient,
    number: int,
    *,
    max_diff_chars: int = 20000,
    max_comments: int = 40,
) -> dict[str, Any]:
    """Fetch a compact, model-friendly PR context."""
    pr = client.pull(number)
    diff = client.diff(number)
    files = client.files(number)
    issue_comments = client.issue_comments(number)
    review_comments = client.review_comments(number)
    checks = client.checks(number)
    comments: list[dict[str, Any]] = []
    for row in issue_comments + review_comments:
        if not isinstance(row, dict):
            continue
        user = row.get("user") or {}
        comments.append(
            {
                "author": user.get("login", ""),
                "body": str(row.get("body") or ""),
                "path": str(row.get("path") or ""),
                "line": row.get("line") or row.get("original_line"),
                "created_at": str(row.get("created_at") or ""),
            }
        )
    comments = comments[:max_comments]
    return {
        "number": number,
        "title": str(pr.get("title") or ""),
        "state": str(pr.get("state") or ""),
        "draft": bool(pr.get("draft")),
        "author": str((pr.get("user") or {}).get("login") or ""),
        "url": str(pr.get("html_url") or ""),
        "base": str((pr.get("base") or {}).get("ref") or ""),
        "head": str((pr.get("head") or {}).get("ref") or ""),
        "body": str(pr.get("body") or ""),
        "additions": int(pr.get("additions") or 0),
        "deletions": int(pr.get("deletions") or 0),
        "changed_files": int(pr.get("changed_files") or 0),
        "files": [
            {
                "filename": str(row.get("filename") or ""),
                "status": str(row.get("status") or ""),
                "additions": int(row.get("additions") or 0),
                "deletions": int(row.get("deletions") or 0),
            }
            for row in files
            if isinstance(row, dict)
        ],
        "diff": diff[:max_diff_chars],
        "diff_truncated": len(diff) > max_diff_chars,
        "comments": comments,
        "checks": checks,
    }


def format_context(context: dict[str, Any]) -> str:
    """Human-readable CLI rendering; keeps untrusted text visibly separated."""
    lines = [
        f"#{context['number']} {context['title']} [{context['state']}]",
        f"{context['base']} <- {context['head']}  by @{context['author']}",
        f"{context['changed_files']} files  +{context['additions']} -{context['deletions']}",
    ]
    if context.get("url"):
        lines.append(str(context["url"]))
    if context.get("body"):
        lines.extend(["", "## Description", str(context["body"])])
    files = context.get("files") or []
    if files:
        lines.extend(["", "## Files"])
        for row in files:
            lines.append(
                f"- {row.get('status', '')} {row.get('filename', '')} "
                f"(+{row.get('additions', 0)}/-{row.get('deletions', 0)})"
            )
    checks = context.get("checks") or {}
    if checks.get("check_runs") or checks.get("statuses"):
        lines.extend(["", "## Checks"])
        for row in checks.get("check_runs") or []:
            lines.append(f"- {row.get('name')}: {row.get('conclusion') or row.get('status')}")
        for row in checks.get("statuses") or []:
            lines.append(f"- {row.get('context')}: {row.get('state')}")
    comments = context.get("comments") or []
    if comments:
        lines.extend(["", "## Comments"])
        for row in comments:
            where = f" ({row['path']}:{row['line']})" if row.get("path") else ""
            lines.append(f"- @{row.get('author', '')}{where}: {row.get('body', '')}")
    if context.get("diff"):
        lines.extend(["", "## Diff", str(context["diff"])])
    return "\n".join(lines)
