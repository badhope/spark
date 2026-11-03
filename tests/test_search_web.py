import socket
from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.sandbox import WorkdirSandbox
from spark.tools import search, web


@pytest.fixture()
def sandbox(tmp_path: Path) -> WorkdirSandbox:
    (tmp_path / "src").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "src" / "app.py").write_text("def main():\n    print('hello needle')\n")
    (tmp_path / "src" / "util.ts").write_text("export const needle = 1;\n")
    (tmp_path / "node_modules" / "lib.js").write_text("needle inside deps\n")
    (tmp_path / "README.md").write_text("# project\n")
    return WorkdirSandbox(tmp_path)


def test_grep_finds_matches_with_skip(sandbox: WorkdirSandbox) -> None:
    result = search.grep_tool(sandbox, search.GrepArgs(pattern="needle"))
    assert result.ok
    results = result.payload["results"]
    assert any("src/app.py" in r for r in results)
    assert any("src/util.ts" in r for r in results)
    assert not any("node_modules" in r for r in results)


def test_grep_glob_filter(sandbox: WorkdirSandbox) -> None:
    result = search.grep_tool(sandbox, search.GrepArgs(pattern="needle", glob="*.py"))
    results = result.payload["results"]
    assert all(r.startswith("src/app.py") for r in results)


def test_grep_invalid_regex(sandbox: WorkdirSandbox) -> None:
    result = search.grep_tool(sandbox, search.GrepArgs(pattern="([unclosed"))
    assert not result.ok
    assert "invalid regex" in result.payload["error"]


def test_glob_pattern(sandbox: WorkdirSandbox) -> None:
    result = search.glob_tool(sandbox, search.GlobArgs(pattern="src/**/*.ts"))
    assert result.ok
    assert result.payload["files"] == ["src/util.ts"]


def test_glob_no_match(sandbox: WorkdirSandbox) -> None:
    result = search.glob_tool(sandbox, search.GlobArgs(pattern="**/*.rs"))
    assert result.ok
    assert result.payload["count"] == 0


def test_grep_escape_outside_workdir(sandbox: WorkdirSandbox) -> None:
    result = search.grep_tool(sandbox, search.GrepArgs(pattern="x", path="../.."))
    assert not result.ok


def test_fetch_page_parses_html() -> None:
    html = "<html><head><title>T</title></head><body><script>bad()</script><p>hello world</p><p>second</p></body></html>"
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html=html, headers={"content-type": "text/html"})

    import httpx._transports.mock

    original = httpx.Client
    # DNS 也要密封：fake-IP 代理环境（198.18.0.0/15）下 getaddrinfo 会把
    # 任何域名解析到非公网地址，被 SSRF 防护拒绝——HTTP 已 mock，这里
    # 把解析结果也固定为公网地址，测试不再依赖真实 DNS。
    original_getaddrinfo = socket.getaddrinfo
    fake_getaddrinfo = lambda host, *a, **kw: [  # noqa: E731
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
    ]
    try:
        httpx.Client = lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)
        socket.getaddrinfo = fake_getaddrinfo
        page = web.fetch_page("https://example.com/x", 5000)
    finally:
        httpx.Client = original
        socket.getaddrinfo = original_getaddrinfo
    assert page["title"] == "T"
    assert "hello world" in page["content"]
    assert "bad()" not in page["content"]


def test_fetch_rejects_non_http() -> None:
    with pytest.raises(ValueError):
        web.fetch_page("file:///etc/passwd", 1000)


def test_registry_schemas_include_new_tools() -> None:
    from spark.tools.registry import ToolContext, ToolRegistry

    cfg = SparkConfig()
    registry = ToolRegistry(ToolContext(sandbox=WorkdirSandbox(Path(".")), config=cfg))
    names = {s["function"]["name"] for s in registry.schemas()}
    assert {"grep", "glob", "web_search", "web_fetch", "update_plan"} <= names
