"""Pull Request context for GitHub / GitCode / Gitee: remote parsing, REST client, compact context.

The client is deliberately read-only: it fetches PR metadata, files, diff,
comments and checks so the agent can review without a second push path. All
external text returned to the model is wrapped in the untrusted fence by the
tool layer, not here.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from spark.errors import SparkError
from spark.proc import run_bounded

GITHUB_API = "https://api.github.com"
FORGE_CONFIG = {
    "github": ("https://api.github.com", "github"),
    "gitcode": ("https://api.gitcode.com/api/v5", "gitcode"),
    "gitee": ("https://gitee.com/api/v5", "gitee"),
}
_REMOTE_RE = re.compile(
    r"(?P<host>github\.com|gitcode\.com|gitee\.com)[:/](?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)(?:\.git)?/?"
)


class PrError(SparkError):
    """A PR lookup/API failure with a user-readable message."""


@dataclass(frozen=True)
class RepoRef:
    owner: str
    repo: str
    forge: str = "github"

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"


def parse_remote(url: str) -> RepoRef | None:
    """Parse a GitHub/GitCode/Gitee remote URL into owner/repo/forge."""
    value = (url or "").strip()
    if not value:
        return None
    match = _REMOTE_RE.search(value)
    if match:
        host = match.group("host")
        forge = "gitcode" if host == "gitcode.com" else "gitee" if host == "gitee.com" else "github"
        repo = match.group("repo")
        if repo.endswith(".git"):
            repo = repo[:-4]
        return RepoRef(match.group("owner"), repo, forge)
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if host in {"github.com", "gitcode.com", "gitee.com"}:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2:
            forge = "gitcode" if host == "gitcode.com" else "gitee" if host == "gitee.com" else "github"
            repo = parts[1][:-4] if parts[1].endswith(".git") else parts[1]
            return RepoRef(parts[0], repo, forge)
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
        raise PrError(f"只支持 GitHub/GitCode/Gitee remote，当前 origin: {remote}")
    return repo


TOKEN_ENVS: dict[str, tuple[str, ...]] = {
    "github": ("GITHUB_TOKEN", "GH_TOKEN"),
    "gitcode": ("GITCODE_TOKEN", "GITCODE_ACCESS_TOKEN"),
    "gitee": ("GITEE_TOKEN", "GITEE_ACCESS_TOKEN"),
}


def token_env_names(forge: str = "github") -> tuple[str, ...]:
    return TOKEN_ENVS.get(forge, TOKEN_ENVS["github"])


def _token_file() -> Path:
    # Lazy import: tests change SPARK_HOME per test, and the CLI must honour it.
    from spark.config import default_home

    return default_home() / "pr-tokens.json"


def _read_token_file() -> dict[str, str]:
    path = _token_file()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if isinstance(v, str) and v}


def stored_token(forge: str = "github") -> str:
    return _read_token_file().get(forge, "")


def set_stored_token(forge: str, token: str) -> Path:
    if forge not in TOKEN_ENVS:
        raise PrError(f"不支持的代码托管平台: {forge}")
    token = token.strip()
    if not token:
        raise PrError("token 不能为空")
    path = _token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_token_file()
    data[forge] = token
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
    finally:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return path


def clear_stored_token(forge: str = "github") -> bool:
    path = _token_file()
    data = _read_token_file()
    if forge not in data:
        return False
    del data[forge]
    if data:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        finally:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return True


def token_source(forge: str = "github") -> str:
    for name in token_env_names(forge):
        if os.environ.get(name):
            return f"环境变量 {name}"
    if stored_token(forge):
        return f"本地凭据 {_token_file()}"
    return ""


def _token(forge: str = "github") -> str:
    for name in token_env_names(forge):
        value = os.environ.get(name) or ""
        if value:
            return value
    return stored_token(forge)


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
        base_url, _ = FORGE_CONFIG.get(repo.forge, ("", ""))
        if not base_url:
            raise PrError(f"不支持的代码托管平台: {repo.forge}")
        headers = {
            "Accept": "application/vnd.github+json" if repo.forge == "github" else "application/json",
            "User-Agent": "spark-pr-context",
        }
        if repo.forge == "github":
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        token = token if token is not None else _token(repo.forge)
        self._token = token
        if token and repo.forge == "github":
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=25.0,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, *, accept: str | None = None, **params: Any) -> Any:
        headers = {"Accept": accept} if accept else None
        if self._token and self.repo.forge != "github":
            params["access_token"] = self._token
        try:
            response = self._client.get(path, headers=headers, params=params or None)
        except httpx.HTTPError as exc:
            raise PrError(f"PR API 请求失败: {exc}") from exc
        if response.status_code == 404:
            raise PrError(f"PR 资源不存在: {path}")
        if response.status_code in (401, 403):
            raise PrError(
                "PR API 拒绝了请求（检查平台 token 权限或速率限制）"
            )
        if response.status_code >= 400:
            raise PrError(f"PR API {response.status_code}: {response.text[:300]}")
        if accept and ("diff" in accept or "text/" in accept):
            return response.text
        try:
            return response.json()
        except ValueError as exc:
            raise PrError("PR API 返回非 JSON 内容") from exc

    def pull(self, number: int) -> dict[str, Any]:
        return self._get(f"/repos/{self.repo.slug}/pulls/{number}")

    def diff(self, number: int) -> str:
        if self.repo.forge != "github":
            try:
                return self._get(f"/repos/{self.repo.slug}/pulls/{number}/diff", accept="text/plain")
            except PrError:
                return ""
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
        if self.repo.forge != "github":
            return {"statuses": [], "check_runs": []}
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


def _checked_body(body: str, limit: int = 60000) -> str:
    body = body.strip()
    if not body:
        raise PrError("review body 不能为空")
    if len(body) > limit:
        raise PrError(f"review body 超过 {limit} 字符上限")
    return body


def post_review(
    client: GitHubClient,
    number: int,
    body: str,
    *,
    event: str = "COMMENT",
) -> dict[str, Any]:
    """Publish a PR review. This is a write; callers must confirm explicitly."""
    event = event.upper()
    if event not in {"COMMENT", "REQUEST_CHANGES", "APPROVE"}:
        raise PrError("event 只支持 COMMENT / REQUEST_CHANGES / APPROVE")
    payload = {"body": _checked_body(body), "event": event}
    if client.repo.forge != "github":
        # GitCode/Gitee v5 expose PR comments, not GitHub's review-event endpoint.
        payload = {"body": payload["body"]}
        path = f"/repos/{client.repo.slug}/pulls/{number}/comments"
    else:
        path = f"/repos/{client.repo.slug}/pulls/{number}/reviews"
    params = {"access_token": client._token} if client._token and client.repo.forge != "github" else None
    try:
        response = client._client.post(path, json=payload, params=params)
    except httpx.HTTPError as exc:
        raise PrError(f"发布 PR review 失败: {exc}") from exc
    if response.status_code in (401, 403):
        raise PrError("发布 PR review 被拒绝（token 需要 pull_request:write 权限）")
    if response.status_code >= 400:
        raise PrError(f"发布 PR review 失败: {response.status_code} {response.text[:300]}")
    try:
        return response.json()
    except ValueError as exc:
        raise PrError("发布 PR review 返回非 JSON") from exc


def _optional_comments(
    fetch: Callable[[int], list[dict[str, Any]]],
    number: int,
) -> list[dict[str, Any]]:
    """Comment endpoints are optional on GitCode/Gitee and some GitHub repos."""
    try:
        return fetch(number)
    except PrError:
        return []


def _diff_from_files(files: list[dict[str, Any]]) -> str:
    """Reconstruct a unified diff when the forge does not expose one."""
    parts: list[str] = []
    for row in files:
        if not isinstance(row, dict):
            continue
        patch = str(row.get("patch") or "")
        if patch:
            parts.append(f"--- a/{row.get('filename', '')}\n+++ b/{row.get('filename', '')}\n{patch}")
    return "\n".join(parts)


def _compact_comments(
    issue_comments: list[dict[str, Any]],
    review_comments: list[dict[str, Any]],
    max_comments: int,
) -> list[dict[str, Any]]:
    """Flatten issue/review comments into the stable shape the model sees."""
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
    return comments[:max_comments]


def _pr_stats(
    pr: dict[str, Any], files: list[dict[str, Any]]
) -> tuple[int, int, int, list[dict[str, Any]]]:
    """Return changed_files/additions/deletions and the compact file list.

    Some forges omit aggregate counters from the PR payload; when they do and
    files are available, derive the totals from the per-file rows instead of
    reporting a misleading zero.
    """
    changed_files = int(pr.get("changed_files") or 0)
    additions = int(pr.get("additions") or 0)
    deletions = int(pr.get("deletions") or 0)
    if not changed_files and files:
        changed_files = len(files)
        additions = sum(int(row.get("additions") or 0) for row in files if isinstance(row, dict))
        deletions = sum(int(row.get("deletions") or 0) for row in files if isinstance(row, dict))
    compact_files = [
        {
            "filename": str(row.get("filename") or ""),
            "status": str(row.get("status") or ""),
            "additions": int(row.get("additions") or 0),
            "deletions": int(row.get("deletions") or 0),
        }
        for row in files
        if isinstance(row, dict)
    ]
    return changed_files, additions, deletions, compact_files


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
    if not diff:
        diff = _diff_from_files(files)
    issue_comments = _optional_comments(client.issue_comments, number)
    review_comments = _optional_comments(client.review_comments, number)
    checks = client.checks(number)
    comments = _compact_comments(issue_comments, review_comments, max_comments)
    changed_files, additions, deletions, compact_files = _pr_stats(pr, files)
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
        "additions": additions,
        "deletions": deletions,
        "changed_files": changed_files,
        "mergeable": pr.get("mergeable"),
        "mergeable_state": str(pr.get("mergeable_state") or ""),
        "files": compact_files,
        "diff": diff[:max_diff_chars],
        "diff_truncated": len(diff) > max_diff_chars,
        "comments": comments,
        "checks": checks,
    }


SECURITY_PATH_HINTS = (
    "auth",
    "crypto",
    "secret",
    "token",
    "password",
    "permission",
    "sandbox",
    "migration",
    "docker",
    ".github/",
    "pyproject.toml",
    "package.json",
    "lock",
)


def _review_scope(
    files: list[dict[str, Any]],
) -> tuple[list[str], bool, list[str]]:
    """Return changed source files, test presence and risky paths."""
    names = [str(row.get("filename") or "") for row in files]
    source_changed = [
        name
        for name in names
        if not name.startswith(("docs/", "tests/"))
        and not name.endswith((".md", ".rst", ".txt"))
    ]
    tests_changed = any(
        name.startswith("tests/") or "/test_" in name or name.endswith("_test.py")
        for name in names
    )
    risky = [name for name in names if any(hint in name.lower() for hint in SECURITY_PATH_HINTS)]
    return source_changed, tests_changed, risky


def _coverage_finding(source_changed: list[str], tests_changed: bool) -> dict[str, str] | None:
    if source_changed and not tests_changed:
        return {
            "severity": "P1",
            "title": "源码有改动但没有测试改动",
            "detail": "至少确认现有测试覆盖；否则要求作者补测试或给出人工验证步骤。",
        }
    return None


def _risky_path_finding(risky: list[str]) -> dict[str, str] | None:
    if risky:
        return {
            "severity": "P1",
            "title": "触及权限/密钥/构建类路径",
            "detail": ", ".join(risky[:8]),
        }
    return None


def _size_finding(context: dict[str, Any]) -> dict[str, str] | None:
    if int(context.get("changed_files") or 0) > 20 or (
        int(context.get("additions") or 0) + int(context.get("deletions") or 0) > 1000
    ):
        return {
            "severity": "P2",
            "title": "变更面较大",
            "detail": "建议拆分或按文件分层审查，避免漏掉边界条件。",
        }
    return None


def _ci_finding(checks: dict[str, Any]) -> dict[str, str] | None:
    failed = [
        row
        for row in (checks.get("check_runs") or [])
        if row.get("conclusion") in {"failure", "cancelled", "timed_out", "action_required"}
    ]
    failed_statuses = [
        row for row in (checks.get("statuses") or []) if row.get("state") in {"failure", "error"}
    ]
    if not failed and not failed_statuses:
        return None
    names_bad = [
        str(row.get("name") or row.get("context") or "check")
        for row in failed + failed_statuses
    ]
    return {"severity": "P1", "title": "CI 未通过", "detail": ", ".join(names_bad[:8])}


def _draft_finding(context: dict[str, Any]) -> dict[str, str] | None:
    if context.get("draft"):
        return {"severity": "P3", "title": "草稿 PR", "detail": "作者尚未标记 ready for review。"}
    return None


def _conflict_finding(context: dict[str, Any]) -> dict[str, str] | None:
    if context.get("mergeable") is False or context.get("mergeable_state") == "dirty":
        return {"severity": "P1", "title": "存在合并冲突", "detail": "先解决冲突再审查。"}
    return None


def review_brief(context: dict[str, Any]) -> dict[str, Any]:
    """Deterministic review checklist derived from PR metadata and checks.

    This is not an AI review: it surfaces the facts a reviewer must not miss
    (CI state, risky paths, test gaps, size) so the model/user can focus on
    reading the diff. The GitHub text itself stays untrusted.
    """
    files = context.get("files") or []
    source_changed, tests_changed, risky = _review_scope(files)
    findings = [
        finding
        for finding in (
            _coverage_finding(source_changed, tests_changed),
            _risky_path_finding(risky),
            _size_finding(context),
            _ci_finding(context.get("checks") or {}),
            _draft_finding(context),
            _conflict_finding(context),
        )
        if finding is not None
    ]
    checklist = [
        "读 diff，逐文件确认行为变化与边界条件",
        "确认新增/修改逻辑有测试覆盖，失败路径也有断言",
        "检查权限、密钥、网络、文件路径是否越界",
        "确认 CI 全部通过且没有跳过的必需检查",
        "检查 PR 描述是否说明动机、风险和回滚方式",
    ]
    return {
        "findings": findings,
        "checklist": checklist,
        "stats": {
            "files": len(files),
            "source_files": len(source_changed),
            "tests_changed": tests_changed,
            "comments": len(context.get("comments") or []),
            "diff_truncated": bool(context.get("diff_truncated")),
        },
    }


def format_review(context: dict[str, Any], brief: dict[str, Any]) -> str:
    lines = [
        f"# Review brief: #{context['number']} {context['title']}",
        f"{context['base']} <- {context['head']} by @{context['author']}",
    ]
    findings = brief.get("findings") or []
    lines.append("\n## Findings")
    if findings:
        for row in findings:
            lines.append(f"- [{row.get('severity')}] {row.get('title')}: {row.get('detail')}")
    else:
        lines.append("- 没有自动发现的结构性风险；仍需逐行读 diff。")
    lines.append("\n## Checklist")
    for item in brief.get("checklist") or []:
        lines.append(f"- [ ] {item}")
    stats = brief.get("stats") or {}
    lines.append(
        "\n## Stats\n"
        f"- files: {stats.get('files', 0)}\n"
        f"- source files: {stats.get('source_files', 0)}\n"
        f"- tests changed: {stats.get('tests_changed', False)}\n"
        f"- comments: {stats.get('comments', 0)}\n"
        f"- diff truncated: {stats.get('diff_truncated', False)}"
    )
    return "\n".join(lines)


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
