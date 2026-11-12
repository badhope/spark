from __future__ import annotations

import html as html_mod
import json
import logging
import re
import socket
from html.parser import HTMLParser
from urllib.parse import quote_plus, urlparse

import httpx
from pydantic import BaseModel

from spark.models import ToolResult
from spark.tools.untrusted import wrap

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


class WebSearchArgs(BaseModel):
    query: str
    max_results: int = 8


class WebFetchArgs(BaseModel):
    url: str
    max_chars: int = 12000


def _strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", "", fragment)
    return html_mod.unescape(text).strip()


def bing_search(query: str, max_results: int) -> list[dict[str, str]]:
    url = "https://www.bing.com/search?q=" + quote_plus(query)
    with httpx.Client(
        timeout=20.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        resp = client.get(url)
        resp.raise_for_status()
    page = resp.text
    blocks = re.findall(r'<li class="b_algo".*?</li>', page, re.DOTALL)
    results: list[dict[str, str]] = []
    for block in blocks[: max_results * 2]:
        m = re.search(r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not m:
            continue
        link = m.group(1)
        title = _strip_tags(m.group(2))
        snip = re.search(r"<p[^>]*>(.*?)</p>", block, re.DOTALL)
        snippet = _strip_tags(snip.group(1)) if snip else ""
        if link.startswith("http"):
            results.append({"title": title, "url": link, "snippet": snippet[:300]})
        if len(results) >= max_results:
            break
    return results


def ddgs_search(query: str, max_results: int) -> list[dict[str, str]] | None:
    """Search through ddgs when installed; None means "fall back to Bing".

    ddgs is the maintained DuckDuckGo client (the older ``duckduckgo_search``
    package was renamed). It is an optional dependency: minimal installs keep
    the existing Bing scraper.
    """
    try:
        from ddgs import DDGS
    except Exception:  # pragma: no cover - optional dependency
        return None
    try:
        with DDGS() as client:
            rows = client.text(query, max_results=max_results)
    except Exception as exc:  # noqa: BLE001 - search backends fail in many ways
        logger.debug("ddgs search failed, falling back to Bing: %s", exc, exc_info=True)
        return None
    results: list[dict[str, str]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        link = str(row.get("href") or row.get("url") or "")
        if not link.startswith("http"):
            continue
        results.append(
            {
                "title": str(row.get("title") or ""),
                "url": link,
                "snippet": str(row.get("body") or row.get("snippet") or "")[:300],
            }
        )
        if len(results) >= max_results:
            break
    return results or None


def search_web(query: str, max_results: int) -> list[dict[str, str]]:
    """Prefer ddgs; fall back to the legacy Bing scraper."""
    results = ddgs_search(query, max_results)
    if results:
        return results
    return bing_search(query, max_results)


class _TextExtractor(HTMLParser):
    SKIP = {
        "script",
        "style",
        "noscript",
        "svg",
        "head",
        "iframe",
        "nav",
        "footer",
        "form",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in {
            "p",
            "br",
            "li",
            "tr",
            "div",
            "h1",
            "h2",
            "h3",
            "h4",
            "pre",
            "section",
            "article",
        }:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0 and data.strip():
            self.parts.append(data.strip() + " ")

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [line.strip() for line in raw.splitlines()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(line for line in lines if line))


BLOCKED_NET_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata",
}
MAX_FETCH_BYTES = 8 * 1024 * 1024


#: Cloud metadata endpoints that are *not* covered by the private/reserved
#: ranges below. Aliyun/Tencent put theirs on 100.100.100.200 (CGNAT space),
#: which ipaddress reports as neither private nor reserved — only non-global.
BLOCKED_METADATA_IPS = {
    "100.100.100.200",  # Aliyun / Tencent Cloud
    "169.254.169.254",  # AWS / GCP / Azure (already link-local, defence in depth)
    "169.254.170.2",    # AWS ECS task metadata
    "fd00:ec2::254",    # AWS IMDSv6
}


def _is_public_host(host: str) -> bool:
    """Reject anything that is not a globally routable unicast address.

    Fail-closed by construction: instead of enumerating bad ranges (which kept
    missing CGNAT metadata endpoints, IPv6 ULA and decimal-encoded IPv4), we
    require ``is_global`` — the one property a legitimate public target has.
    """
    import ipaddress

    name = (host or "").strip().lower().strip("[]")
    if not name or name in BLOCKED_NET_HOSTS or name in BLOCKED_METADATA_IPS:
        return False
    # Decimal/octal/hex IPv4 (e.g. http://2130706433/ == 127.0.0.1) is not a
    # valid hostname and must never reach the resolver.
    try:
        if name.isdigit():
            return False
        addr = ipaddress.ip_address(name)
    except ValueError:
        # Not an IP literal: a DNS name. Resolution is re-checked in
        # _assert_fetchable() against the same predicate.
        return True
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return bool(
        addr.is_global
        and not addr.is_private
        and not addr.is_loopback
        and not addr.is_link_local
        and not addr.is_reserved
        and not addr.is_multicast
        and not addr.is_unspecified
    )


def _assert_fetchable(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(
            f"unsupported scheme: {parsed.scheme or 'none'} (use http/https)"
        )
    host = parsed.hostname or ""
    if not _is_public_host(host):
        raise ValueError(f"refusing to fetch a non-public address: {host}")
    try:

        for info in socket.getaddrinfo(host, None):
            address = info[4][0]
            if not _is_public_host(str(address)):
                raise ValueError(f"host resolves to a non-public address: {address}")
    except socket.gaierror:
        return


_MAX_REDIRECTS = 5


def extract_readable_text(raw: str) -> str | None:
    """Extract main article text with trafilatura when installed."""
    try:
        import trafilatura
    except Exception:  # pragma: no cover - optional dependency
        return None
    try:
        text = trafilatura.extract(
            raw,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )
    except Exception as exc:  # noqa: BLE001 - extraction must never break fetch
        logger.debug("trafilatura extraction failed: %s", exc, exc_info=True)
        return None
    return text.strip() if text else None


def fetch_page(url: str, max_chars: int) -> dict[str, str]:
    """Fetch a page with SSRF checks applied to *every* hop.

    ``follow_redirects`` stays off on purpose: httpx would chase the chain
    before we ever see it, so the redirect target would never be validated.
    Each hop is fetched, checked and re-validated here instead.
    """
    current = url
    with httpx.Client(
        timeout=25.0, follow_redirects=False, headers={"User-Agent": USER_AGENT}
    ) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            _assert_fetchable(current)
            resp = client.get(current)
            if not resp.is_redirect:
                break
            location = resp.headers.get("location", "")
            if not location:
                break
            current = str(resp.url.join(location))
        else:
            raise ValueError("too many redirects")
    resp.raise_for_status()
    ctype = resp.headers.get("content-type", "")
    raw = resp.text[:MAX_FETCH_BYTES]
    if "html" in ctype or "xml" in ctype or not ctype:
        title_m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.DOTALL | re.IGNORECASE)
        title = _strip_tags(title_m.group(1)) if title_m else ""
        text = extract_readable_text(raw)
        if not text:
            # trafilatura is optional and can decline short/odd pages; keep the
            # dependency-free parser as the fallback.
            parser = _TextExtractor()
            parser.feed(raw)
            text = parser.text()
    else:
        title = ""
        text = raw
    return {"title": title, "url": str(resp.url), "content": text[:max_chars]}


def web_search_tool(args: WebSearchArgs) -> ToolResult:
    query = args.query.strip()
    if not query:
        return ToolResult(ok=False, payload={"error": "query required"})
    max_results = max(1, min(10, args.max_results))
    try:
        results = search_web(query, max_results)
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": f"web search failed: {exc}"})
    if not results:
        return ToolResult(
            ok=False,
            payload={
                "error": "no results (search engine may be rate-limiting; try again later)"
            },
        )
    return ToolResult(
        ok=True,
        payload={
            "query": query,
            # 搜索结果来自外部：整体不可信，防止注入指令
            "results": wrap(json.dumps(results, ensure_ascii=False)),
            "count": len(results),
        },
    )


def web_fetch_tool(args: WebFetchArgs) -> ToolResult:
    url = args.url.strip()
    max_chars = max(500, min(50000, args.max_chars))
    try:
        page = fetch_page(url, max_chars)
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": f"fetch failed: {exc}"})
    content = page["content"]
    truncated = len(page["content"]) >= max_chars
    payload: dict = {
        "url": page["url"],
        "title": page["title"],
        # 网页正文来自外部：整体不可信，防止 prompt injection
        "content": wrap(content),
        "chars": len(content),
    }
    if truncated:
        payload["note"] = (
            f"content truncated at {max_chars} chars; fetch with larger max_chars or a more specific page if needed"
        )
    return ToolResult(ok=True, payload=payload)
