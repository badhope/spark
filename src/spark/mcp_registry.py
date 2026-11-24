"""MCP registry 聚合代理（P1-7）。

聚合官方 registry（``registry.modelcontextprotocol.io`` v0）与 Glama 评分，
并对"国内可用性"做启发式判定：需要境外 OAuth / 被墙域名的条目标记并默认过滤。

设计要点：

* 所有外部 HTTP 调用集中在 :func:`search_servers`，可注入 ``httpx.Client``
  （测试用 ``httpx.MockTransport`` 完全离线）。
* 网络不可达时**优雅降级**：返回内置离线数据集，``source == "offline"``，
  并把原始错误放进 ``error`` 字段，前端 UI 照常渲染。
* :func:`assess_china_availability` 是纯函数规则集，可扩展：新增规则只需往
  常量集合里加条目，单测随之覆盖。
* Glama 公开 API 需要 API key（401 即降级为 ``None``）；另提供
  :func:`parse_glama_search_html` 作为公开页面抓取的兜底解析器（当前页面无
  评分字段时返回 ``None``，结构与单测已就位）。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

REGISTRY_SEARCH_URL = "https://registry.modelcontextprotocol.io/v0/servers"
GLAMA_API_URL = "https://glama.ai/api/mcp/v1/servers"
GLAMA_SEARCH_URL = "https://glama.ai/mcp/servers"

# 实测该 search 接口在沙箱代理下冷启动可达 25s+，超时给 30s 余量。
DEFAULT_TIMEOUT = 30.0
DEFAULT_LIMIT = 20
MAX_LIMIT = 50

# ---------------------------------------------------------------------------
# 国内可用性规则（可扩展）
# ---------------------------------------------------------------------------

# 境外 OAuth 授权域名（host 后缀匹配）——这类 remote 在国内即使能连通，登录
# 链路也会被墙，直接判不可用。
OVERSEAS_OAUTH_HOSTS: frozenset[str] = frozenset(
    {
        "accounts.google.com",
        "oauth2.googleapis.com",
        "login.microsoftonline.com",
        "github.com",  # github.com/login/oauth/*
        "api.dropboxapi.com",
        "login.okta.com",
        "auth0.com",
        "keycloak",
        "auth.myunited.com",
    }
)

# 国内已知不可达的域名后缀。保守列表，只放"稳定被墙"的；误判宁可放过。
BLOCKED_HOST_SUFFIXES_CN: tuple[str, ...] = (
    "googleapis.com",
    "google.com",
    "google.dev",
    "gstatic.com",
    "googleusercontent.com",
    "githubusercontent.com",
)

# 需要境外凭证的环境变量前缀 —— 可运行但对国内用户有门槛，标 risk。
OVERSEAS_CRED_ENV_PREFIXES: tuple[str, ...] = (
    "GCS_",
    "GOOGLE_",
    "AWS_",
    "AZURE_",
    "GCP_",
)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def assess_china_availability(entry: dict[str, Any]) -> dict[str, Any]:
    """纯函数：根据解析后的 entry 判定国内可用性。

    返回 ``{"verdict": "available" | "risk" | "unreachable", "reasons": [str, ...]}``。
    """

    reasons: list[str] = []
    verdict = "available"

    # 1) remote URL：境外 OAuth / 被墙域名 → 不可用
    for remote in entry.get("remotes") or []:
        url = remote.get("url") or ""
        host = _host(url)
        if not host:
            continue
        if any(host == h or host.endswith("." + h) for h in OVERSEAS_OAUTH_HOSTS):
            reasons.append("requires_overseas_oauth")
            verdict = "unreachable"
            break
        if any(host.endswith(suf) for suf in BLOCKED_HOST_SUFFIXES_CN):
            reasons.append(f"blocked_domain:{host}")
            verdict = "unreachable"
            break

    # 2) 必填环境变量：境外云凭证 → risk
    for env in entry.get("required_env") or []:
        name = str(env.get("name") or "").upper()
        if name.startswith(OVERSEAS_CRED_ENV_PREFIXES):
            reasons.append(f"requires_overseas_cred:{env.get('name')}")
            if verdict != "unreachable":
                verdict = "risk"

    if not reasons:
        reasons.append("ok")
    return {"verdict": verdict, "reasons": reasons}


# ---------------------------------------------------------------------------
# Registry 响应解析（纯函数）
# ---------------------------------------------------------------------------


def _pick_latest_version(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """同一 server 会返回多个版本，优先取 isLatest。"""

    for e in entries:
        meta = (e.get("_meta") or {}).get("io.modelcontextprotocol.registry/official") or {}
        if meta.get("isLatest"):
            return e
    return entries[0]


def _suggested_command(pkg: dict[str, Any]) -> tuple[str, list[str]]:
    """从 package 描述构造 stdio 启动命令。

    registry 里 runtimeHint 常见值：``npx`` / ``uvx`` / ``docker``；
    部分条目省略 runtimeHint 但 transport=stdio + npm identifier，按约定补 npx。
    """

    runtime = (pkg.get("runtimeHint") or "").strip()
    transport = (pkg.get("transport") or {}).get("type") or ""
    args = [
        str(a.get("value")) for a in pkg.get("runtimeArguments") or [] if a.get("value") is not None
    ]
    identifier = (pkg.get("identifier") or "").strip()
    registry_type = (pkg.get("registryType") or "").strip().lower()
    if not runtime:
        if transport == "stdio" and registry_type == "npm" and identifier:
            runtime = "npx"
            if "-y" not in args:
                args = ["-y", *args]
        else:
            return "", []
    if runtime in {"npx", "uvx"} and identifier and identifier not in args:
        args.append(identifier)
    return runtime, args


def _registry_remotes(remotes: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"url": r.get("url") or "", "type": r.get("type") or ""} for r in remotes
    ]


def _registry_required_env(pkg: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": e.get("name") or "",
            "required": bool(e.get("isRequired")),
            "secret": bool(e.get("isSecret")),
        }
        for e in pkg.get("environmentVariables") or []
    ]


def _registry_entry(name: str, versions: list[dict[str, Any]]) -> dict[str, Any]:
    item = _pick_latest_version(versions)
    srv = item.get("server") or {}
    pkgs = srv.get("packages") or []
    pkg = next(
        (p for p in pkgs if (p.get("transport") or {}).get("type") == "stdio"),
        pkgs[0] if pkgs else {},
    )
    command, args = _suggested_command(pkg)
    remotes = srv.get("remotes") or []
    transport = "stdio" if pkg else ("remote" if remotes else "unknown")
    repo = srv.get("repository") or {}
    return {
        "name": name,
        "title": srv.get("title") or name,
        "description": srv.get("description") or "",
        "version": srv.get("version") or "",
        "repository_url": repo.get("url") or "",
        "transport": transport,
        "remotes": _registry_remotes(remotes),
        "remote_url": remotes[0].get("url") if remotes else None,
        "suggested_command": command or None,
        "suggested_args": args,
        "required_env": _registry_required_env(pkg),
    }

def parse_registry_servers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """把 registry v0 响应拍平成聚合代理内部的 entry 列表。"""

    by_name: dict[str, list[dict[str, Any]]] = {}
    for item in payload.get("servers") or []:
        srv = item.get("server") or {}
        name = srv.get("name") or ""
        if name:
            by_name.setdefault(name, []).append(item)
    return [_registry_entry(name, versions) for name, versions in by_name.items()]

# ---------------------------------------------------------------------------
# Glama 评分（best-effort）
# ---------------------------------------------------------------------------


def parse_glama_search_html(html: str) -> float | None:
    """从 Glama 公开搜索页 HTML 里 best-effort 抽评分。

    当前 Glama 页面 JSON-LD 不含 aggregateRating，解析器找不到就返回 None；
    结构留好，单测用 mock HTML 覆盖。
    """

    m = re.search(r'"ratingValue"\s*:\s*([0-9]+(?:\.[0-9]+)?)', html)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _glama_slug(registry_name: str) -> str:
    """registry 名 → Glama slug 的 best-effort 映射。"""

    last = registry_name.rsplit("/", 1)[-1]
    return re.sub(r"[^a-zA-Z0-9_-]", "-", last)


def fetch_glama_rating(registry_name: str, client: httpx.Client) -> dict[str, Any] | None:
    """best-effort 拉 Glama 评分；无 key / 401 / 网络错都返回 None，不抛。"""

    api_key = os.environ.get("GLAMA_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        resp = client.get(
            f"{GLAMA_API_URL}/{_glama_slug(registry_name)}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        rating = data.get("rating") or data.get("score")
        if rating is None:
            return None
        return {
            "score": float(rating),
            "count": int(data.get("ratingCount") or data.get("reviewCount") or 0),
            "source": "api",
        }
    except (httpx.HTTPError, ValueError) as exc:
        logger.debug("glama rating fetch failed for %s: %s", registry_name, exc)
        return None


# ---------------------------------------------------------------------------
# 离线数据集（降级 + 测试）
# ---------------------------------------------------------------------------


def _offline_dataset() -> list[dict[str, Any]]:
    """内置离线样本：覆盖 available / risk / unreachable 三种判定。"""

    return [
        {
            "name": "offline/filesystem",
            "title": "Filesystem (离线样本)",
            "description": "本地文件读写 MCP server（npm 包，国内可用 npmmirror）。",
            "version": "0.1.0",
            "repository_url": "https://github.com/modelcontextprotocol/servers",
            "transport": "stdio",
            "remotes": [],
            "remote_url": None,
            "suggested_command": "npx",
            "suggested_args": ["-y", "@modelcontextprotocol/server-filesystem"],
            "required_env": [],
        },
        {
            "name": "offline/gcs-bucket",
            "title": "GCS Bucket (离线样本)",
            "description": "Google Cloud Storage 桥接 —— 需要境外云凭证，标 risk。",
            "version": "0.1.5",
            "repository_url": "https://github.com/pulsemcp/mcp-servers",
            "transport": "stdio",
            "remotes": [],
            "remote_url": None,
            "suggested_command": "npx",
            "suggested_args": ["-y", "remote-filesystem-mcp-server"],
            "required_env": [
                {"name": "GCS_BUCKET", "required": True, "secret": False},
                {"name": "GCS_PRIVATE_KEY", "required": False, "secret": True},
            ],
        },
        {
            "name": "offline/remote-oauth",
            "title": "Remote OAuth (离线样本)",
            "description": "streamable-http remote，登录走 accounts.google.com —— 判不可用。",
            "version": "1.0.0",
            "repository_url": "",
            "transport": "remote",
            "remotes": [{"url": "https://accounts.google.com/mcp", "type": "streamable-http"}],
            "remote_url": "https://accounts.google.com/mcp",
            "suggested_command": None,
            "suggested_args": [],
            "required_env": [],
        },
        {
            "name": "offline/remote-blocked",
            "title": "Blocked Host (离线样本)",
            "description": "remote 域名落在被墙列表，判不可用。",
            "version": "1.0.0",
            "repository_url": "",
            "transport": "remote",
            "remotes": [{"url": "https://api.googleapis.com/mcp", "type": "streamable-http"}],
            "remote_url": "https://api.googleapis.com/mcp",
            "suggested_command": None,
            "suggested_args": [],
            "required_env": [],
        },
    ]


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def _build_client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True)


def _attach_china_verdict(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for it in items:
        it["china"] = assess_china_availability(it)
    return items


def search_servers(
    query: str,
    limit: int = DEFAULT_LIMIT,
    *,
    client: httpx.Client | None = None,
    include_unavailable: bool = False,
    offline_fallback: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """聚合搜索入口。

    返回::

        {
          "items": [...],
          "source": "live" | "offline",
          "error": str | None,
          "filtered_out": int,
        }
    """

    query = (query or "").strip()
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    owns_client = client is None
    client = client or _build_client(timeout)

    try:
        try:
            resp = client.get(
                REGISTRY_SEARCH_URL,
                params={"search": query, "version": "latest", "limit": limit},
            )
            resp.raise_for_status()
            payload = resp.json()
            items = _attach_china_verdict(parse_registry_servers(payload))
            source = "live"
            error = None
        except (httpx.HTTPError, ValueError) as exc:
            if not offline_fallback:
                return {"items": [], "source": "offline", "error": str(exc), "filtered_out": 0}
            logger.info("registry unreachable (%s), falling back to offline dataset", exc)
            items = _attach_china_verdict(_offline_dataset())
            if query:
                ql = query.lower()
                items = [
                    it
                    for it in items
                    if ql in it["name"].lower()
                    or ql in it["title"].lower()
                    or ql in it["description"].lower()
                ]
            source = "offline"
            error = f"registry 不可达，已切换到离线样本：{exc}"

        # Glama 评分 best-effort（仅 live 且配置了 GLAMA_API_KEY 时打 API）
        for it in items:
            if source == "live" and os.environ.get("GLAMA_API_KEY"):
                it["glama"] = fetch_glama_rating(it["name"], client)
            else:
                it.setdefault("glama", None)

        filtered_out = 0
        if not include_unavailable:
            kept = []
            for it in items:
                if it["china"]["verdict"] == "unreachable":
                    filtered_out += 1
                else:
                    kept.append(it)
            items = kept

        return {"items": items, "source": source, "error": error, "filtered_out": filtered_out}
    finally:
        if owns_client:
            client.close()
