from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlparse

from spark.hooks import HookRegistry
from spark.sandbox import safe_bare_filename

logger = logging.getLogger(__name__)

INDEX_HTML = Path(__file__).with_name("index.html")

HOOK_EVENTS = tuple(HookRegistry.EVENTS)

MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_AGENTS_MD_BYTES = 1 * 1024 * 1024
MCP_REDACTED_HEADER = "[REDACTED]"


def is_sensitive_mcp_header(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    return normalized in {
        "authorization",
        "proxyauthorization",
        "cookie",
        "setcookie",
        "apikey",
        "xapikey",
    } or normalized.endswith(("authorization", "apikey", "authtoken", "sessionid")) or any(
        token in normalized for token in ("token", "secret", "password", "credential", "session")
    )


def redact_mcp_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        name: MCP_REDACTED_HEADER if is_sensitive_mcp_header(name) else value
        for name, value in headers.items()
    }


_CTYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


def ui_dir_for() -> Path | None:
    for c in (
        Path(__file__).with_name("dist"),
        Path(__file__).resolve().parents[3] / "web" / "dist",
    ):
        if (c / "index.html").is_file():
            return c
    return None


def parse_mcp_servers(items: list) -> list:
    from spark.config import McpServerConfig, McpTransportConfig

    out: list[McpServerConfig] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        transport = cast(
            Literal["stdio", "sse", "streamable_http"],
            str(item.get("transport") or "stdio").strip() or "stdio",
        )
        if transport not in {"stdio", "sse", "streamable_http"}:
            transport = "stdio"
        command = str(item.get("command") or "").strip()
        http_raw = item.get("http") if isinstance(item.get("http"), dict) else None
        http: McpTransportConfig | None = None
        if transport == "stdio":
            if not command:
                raise ValueError("each MCP server needs name and command")
        else:
            url = str((http_raw or {}).get("url") or "").strip()
            if not url:
                raise ValueError(f"MCP server '{name}' needs http.url for {transport}")
            headers_raw = (http_raw or {}).get("headers")
            headers = (
                {str(k): str(v) for k, v in headers_raw.items()}
                if isinstance(headers_raw, dict)
                else {}
            )
            try:
                timeout_sec = int((http_raw or {}).get("timeout_sec") or 30)
            except (TypeError, ValueError):
                timeout_sec = 30
            http = McpTransportConfig(url=url, headers=headers, timeout_sec=timeout_sec)
        args_raw = item.get("args")
        ro_raw = item.get("readonly_tools")
        allowed_raw = item.get("allowed_tools")
        env_raw = item.get("env")
        out.append(
            McpServerConfig(
                name=name,
                command=command,
                args=[str(a) for a in args_raw] if isinstance(args_raw, list) else [],
                env={str(k): str(v) for k, v in env_raw.items()}
                if isinstance(env_raw, dict)
                else {},
                readonly_tools=[str(a) for a in ro_raw]
                if isinstance(ro_raw, list)
                else [],
                transport=transport,
                http=http,
                allowed_tools=[str(a) for a in allowed_raw]
                if isinstance(allowed_raw, list)
                else [],
            )
        )
    return out


def _cache_control(path: str) -> str | None:
    if path in {"/", "/index.html"}:
        return "no-cache"
    if path.startswith("/assets/"):
        return "public, max-age=31536000, immutable"
    return None


def _safe_agents_md_name(filename: str) -> str | None:
    """兼容入口：实现收敛到 :func:`spark.sandbox.safe_bare_filename`。"""
    return safe_bare_filename(filename)


def _probe_target_allowed(base_url: str, trusted_base_url: str) -> bool:
    """Only probe the same origin the user already configured; never let a request
    send the stored credential to an arbitrary host."""

    def host_of(value: str) -> str:
        try:
            return (urlparse(value).hostname or "").lower()
        except ValueError:
            return ""

    target = host_of(base_url)
    trusted = host_of(trusted_base_url)
    if not target or not trusted:
        return False
    return target == trusted


def _port_in_use(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def pick_free_port(host: str, preferred: int, attempts: int = 64) -> int:
    """从 *preferred* 起返回第一个未被监听的端口；范围内全被占用时抛 OSError。"""
    for port in range(preferred, min(preferred + attempts, 65536)):
        if not _port_in_use(host, port):
            return port
    raise OSError(
        f"no free port on {host} in range {preferred}-{preferred + attempts - 1}"
    )


def _event_payload(event) -> dict:
    payload: dict = {"type": event.type}
    if event.text:
        payload["text"] = event.text
    if event.tool_call:
        payload["tool"] = {
            "id": event.tool_call.id,
            "name": event.tool_call.name,
            "arguments": event.tool_call.arguments,
        }
    if event.result:
        payload["ok"] = event.result.ok
        payload["result"] = event.result.payload
    if event.approval:
        payload["approval"] = {
            "summary": event.approval.summary,
            "diff": event.approval.diff,
            # The full arguments travel with the request so the approval card can
            # show what will actually run. A truncated summary is not enough to
            # judge a shell command.
            "tool": event.approval.tool_call.name,
            "arguments": event.approval.tool_call.arguments,
        }
    if event.data:
        payload["data"] = event.data
    return payload


def probe_env() -> list[dict[str, str]]:
    """运行环境探测：常见工具链的路径与版本（只读，永不执行用户输入）。

    供「设置 → 权限 → 运行环境探测」展示；探测失败的工具标记 unavailable。
    """
    import shutil

    from spark.proc import run_bounded

    tools = [
        ("python", ["python", "--version"]),
        ("python3", ["python3", "--version"]),
        ("git", ["git", "--version"]),
        ("node", ["node", "--version"]),
        ("npm", ["npm", "--version"]),
        ("uv", ["uv", "--version"]),
    ]
    out: list[dict[str, str]] = []
    for name, argv in tools:
        exe = shutil.which(name)
        if not exe:
            out.append({"name": name, "path": "", "version": "unavailable"})
            continue
        version = ""
        try:
            proc = run_bounded(argv, timeout=5)
        except OSError:
            proc = None
        if proc is None:
            version = "unavailable"
        elif proc.timed_out:
            version = "timeout"
        else:
            version = (proc.stdout or proc.stderr).strip().splitlines()[0] if (
                proc.stdout or proc.stderr
            ) else ""
        out.append({"name": name, "path": exe, "version": version or "?"})
    return out
