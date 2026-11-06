"""HTTP Handler class + route dispatch.  Kept thin: all business logic lives
in ``spark.web._handlers`` as standalone ``(handler, body) -> None`` functions.

The Handler class is responsible for:

* HTTP plumbing (reading request bodies, writing JSON responses)
* Security guards (loopback check, origin check, CSRF, auth token)
* Routing (GET → handler methods, POST → dispatch table)

This module is intentionally import-light so it can be used from test
suites without pulling in the full application stack.
"""

from __future__ import annotations

import json as _json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from spark.web._handlers import (  # noqa: F401  (re-export for backward-compat)
    _handle_agents_md,
    _handle_approval,
    _handle_automations,
    _handle_automations_cron_check,
    _handle_cancel,
    _handle_chat,
    _handle_chat_stream,
    _handle_checkpoints_rollback,
    _handle_diagnostics,
    handle_debug,
    handle_debug_client_log,
    _handle_knowledge,
    _handle_memory,
    _handle_messages_delete,
    _handle_messages_truncate,
    _handle_models_activate,
    _handle_models_delete,
    _handle_models_save,
    _handle_approval_profiles_activate,
    _handle_approval_profiles_delete,
    _handle_approval_profiles_save,
    _handle_permissions_rules,
    _handle_permissions_rules_delete,
    _handle_plugins,
    _handle_prompts,
    _handle_selfheal,
    _handle_skills,
    _handle_wiki,
    _handle_session_export,
    _handle_session_import,
    _handle_question_answer,
    _handle_sessions_delete,
    _handle_sessions_fork,
    _handle_sessions_new,
    _handle_sessions_rename,
    _handle_sessions_switch,
    _handle_settings,
    _handle_shell,
    _handle_test,
    _history_messages,
)
from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 只读工具 API（文件查看 / 网页阅读）
# - 文件接口被严格限制在 state.workdir 之内（resolve + 前缀校验）
# - browse 复用 agent 的 web_fetch 通道（含 SSRF 防护），不做客户端渲染
# ---------------------------------------------------------------------------

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".next", ".spark"}


def _resolve_in_workdir(workdir: str, rel: str) -> Path | None:
    """Resolve *rel* (relative or absolute) inside *workdir*; None when escaping."""
    try:
        base = Path(workdir).resolve()
        target = (base / rel).resolve() if rel else base
        target.relative_to(base)
        return target
    except (ValueError, OSError):
        return None


def _fs_tree(workdir: str, rel: str) -> dict[str, Any]:
    base = _resolve_in_workdir(workdir, rel)
    if base is None:
        return {"error": "path outside workdir"}
    if not base.exists():
        return {"error": "not found"}
    if not base.is_dir():
        return {"error": "not a directory"}
    entries: list[dict[str, Any]] = []
    try:
        children = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return {"error": "unreadable directory"}
    for p in children:
        if p.name in _SKIP_DIRS or p.name.startswith("."):
            continue
        if p.is_dir():
            entries.append({"name": p.name, "type": "dir"})
        else:
            try:
                size = p.stat().st_size
                mtime = int(p.stat().st_mtime)
            except OSError:
                size, mtime = 0, 0
            entries.append({"name": p.name, "type": "file", "size": size, "mtime": mtime})
        if len(entries) >= 500:
            break
    return {"path": str(base), "entries": entries}


_MAX_READ_CHARS = 100_000
_MAX_READ_BYTES = _MAX_READ_CHARS * 4


def _fs_read(workdir: str, rel: str, sandbox: Any = None) -> dict[str, Any]:
    target = _resolve_in_workdir(workdir, rel)
    if target is None:
        return {"error": "path outside workdir"}
    if sandbox is not None and not sandbox.is_path_allowed(target):
        return {"error": "path protected by sandbox policy"}
    if not target.exists() or not target.is_file():
        return {"error": "not found"}
    try:
        size = target.stat().st_size
        with target.open("rb") as handle:
            raw = handle.read(_MAX_READ_BYTES)
    except OSError:
        return {"error": "unreadable file"}
    if b"\x00" in raw[:8192]:
        return {"name": target.name, "binary": True, "size": size}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        if size <= _MAX_READ_BYTES:
            return {"name": target.name, "binary": True, "size": size}
        text = raw[:_MAX_READ_BYTES].decode("utf-8", errors="ignore")
    truncated = size > _MAX_READ_BYTES or len(text) > _MAX_READ_CHARS
    return {
        "name": target.name,
        "path": str(target),
        "content": text[:_MAX_READ_CHARS],
        "size": size,
        "truncated": truncated,
    }


def _browse(url: str) -> dict[str, Any]:
    from spark.tools.web import fetch_page

    try:
        return fetch_page(url, max_chars=12000)
    except Exception as exc:  # noqa: BLE001  (surface a friendly message)
        return {"url": url, "error": str(exc)}


def build_handler_class(html: str):
    """Build a BaseHTTPRequestHandler subclass bound to *html* content."""

    class Handler(HandlerBase):
        def version_string(self) -> str:
            # Never advertise the interpreter version: it is free reconnaissance
            # for anyone who can reach a non-loopback bind.
            return "spark"

        def end_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            # The UI is a local SPA: everything it needs is same-origin. Inline
            # styles are required by the diff/markdown renderers, so style-src
            # allows 'unsafe-inline' while script-src does not.
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            )
            super().end_headers()

        def log_message(self, fmt: str, *args: object) -> None:
            import sys

            sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

        # ------------------------------------------------------------------
        # Security guards
        # ------------------------------------------------------------------
        def _is_loopback(self) -> bool:
            host = self.client_address[0] if self.client_address else ""
            return host in {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}

        def _server_is_loopback(self) -> bool:
            host = getattr(self.server, "server_address", ("",))[0]
            return host in {"127.0.0.1", "::1", "localhost"}

        def _service_port(self) -> int | None:
            port = getattr(self.server, "server_port", None)
            return port if isinstance(port, int) else None

        @staticmethod
        def _local_host(hostname: str) -> bool:
            return hostname.strip("[]").lower() in {"127.0.0.1", "::1", "localhost"}

        def _host_is_local(self) -> bool:
            host = (self.headers.get("Host") or "").strip()
            if not host:
                return True
            try:
                parsed = urlparse(f"http://{host}")
                hostname = parsed.hostname or ""
                port = parsed.port
            except ValueError:
                return False
            if parsed.username or parsed.password or parsed.path or parsed.params or parsed.query or parsed.fragment:
                return False
            service_port = self._service_port()
            if service_port is None:
                return False
            if self._server_is_loopback() and not self._local_host(hostname):
                return False
            if not self._server_is_loopback() and not self._local_host(hostname):
                if not self.headers.get("X-Spark-Token"):
                    return False
            return port == service_port or (port is None and service_port in {80, 443})

        def _origin_allowed(self) -> bool:
            fetch_site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
            if fetch_site == "cross-site":
                return False
            origin = (self.headers.get("Origin") or "").strip()
            if not origin:
                return True
            try:
                parsed = urlparse(origin)
                host = parsed.hostname or ""
                port = parsed.port
            except ValueError:
                return False
            service_port = self._service_port()
            if (
                service_port is None
                or parsed.scheme != "http"
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.params
                or parsed.query
                or parsed.fragment
            ):
                return False
            if self._is_loopback():
                return self._local_host(host) and (
                    port == service_port or (port is None and service_port in {80, 443})
                )
            try:
                host_parsed = urlparse(f"http://{self.headers.get('Host', '')}")
                request_host = (host_parsed.hostname or "").lower()
                request_port = host_parsed.port or service_port
            except ValueError:
                return False
            return host.lower() == request_host and port == request_port

        def _authorized(self) -> bool:
            import secrets as _secrets

            if self._server_is_loopback():
                return True
            supplied = (self.headers.get("X-Spark-Token") or "").encode("utf-8", "replace")
            state = _route_state(self)
            return bool(state.control_token) and _secrets.compare_digest(
                supplied, state.control_token.encode("utf-8")
            )

        def _guard(self, path: str) -> bool:
            if not self._host_is_local():
                self._json(403, {"error": "invalid host header"})
                return True
            if not self._origin_allowed():
                self._json(403, {"error": "cross-origin request rejected"})
                return True
            if self._server_is_loopback() or self._authorized():
                return False
            self._json(
                401,
                {
                    "error": "unauthorized",
                    "hint": "remote access requires the X-Spark-Token header",
                },
            )
            return True

        def _guard_mutation(self, path: str) -> bool:
            if self._guard(path):
                return True
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype and ctype != "application/json":
                self._json(
                    415,
                    {
                        "error": "unsupported content type",
                        "hint": "use application/json or the X-Spark-Token header",
                    },
                )
                return True
            return False

        # ------------------------------------------------------------------
        # Response helpers
        # ------------------------------------------------------------------
        def _json(self, code: int, body: dict | list) -> None:
            data = _json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _html(self, request_path: str = "/") -> None:
            from spark.web.utils import _cache_control

            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            cc = _cache_control(request_path)
            if cc:
                self.send_header("Cache-Control", cc)
            self.end_headers()
            self.wfile.write(data)

        def _read_json(self) -> dict:
            from spark.web.utils import MAX_REQUEST_BYTES

            raw_length = (self.headers.get("Content-Length") or "0").strip()
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length < 0:
                raise ValueError("invalid Content-Length")
            if length > MAX_REQUEST_BYTES:
                raise ValueError("request body too large")
            raw = self.rfile.read(length) if length else b"{}"
            payload = _json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                raise TypeError("request JSON must be an object")
            return payload

        # ------------------------------------------------------------------
        # Route dispatch — GET
        # ------------------------------------------------------------------
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path.startswith("/api/") and self._guard(path):
                return
            self._handle_get(path)

        def _handle_get(self, path: str) -> None:
            from spark.web.utils import _CTYPES, _cache_control

            state = _route_state(self)
            ui_dir = getattr(self.server, "ui_dir", None)
            if path in {"/", "/index.html"}:
                self._html(path)
                return
            if ui_dir is not None and not path.startswith("/api/"):
                rel = path.lstrip("/")
                if rel:
                    target = (ui_dir / rel).resolve()
                    try:
                        target.relative_to(ui_dir.resolve())
                    except ValueError:
                        self._json(404, {"error": "not found"})
                        return
                    if target.is_file():
                        data = target.read_bytes()
                        ctype = _CTYPES.get(target.suffix.lower(), "application/octet-stream")
                        self.send_response(200)
                        self.send_header("Content-Type", ctype)
                        self.send_header("Content-Length", str(len(data)))
                        cc = _cache_control(path)
                        if cc:
                            self.send_header("Cache-Control", cc)
                        self.end_headers()
                        self.wfile.write(data)
                        return
                self._html(path)
                return
            if path == "/api/status":
                self._json(200, state.status())
                return
            if path == "/api/diagnostics":
                _handle_diagnostics(self, {})
                return
            if path == "/api/debug":
                handle_debug(self, {})
                return
            if path == "/api/openapi.json":
                # P1：OpenAPI 合同层（前端 codegen 单一事实来源；见 web/api_spec.py）
                from spark.web.api_spec import OpenApiUnavailable, openapi_schema

                try:
                    schema = openapi_schema()
                except OpenApiUnavailable as exc:
                    self._json(503, {"error": str(exc), "hint": "pip install 'spark-agent[openapi]'"})
                    return
                self._json(200, schema)
                return
            if path == "/api/config":
                state.ensure_default_profile()
                self._json(200, state.full_config())
                return
            if path == "/api/models":
                from spark.config import mask_secret

                state.ensure_default_profile()
                profiles = [
                    {
                        "id": p.id,
                        "name": p.name,
                        "provider": p.provider,
                        "base_url": p.base_url,
                        "model": p.model,
                        "api_key_masked": mask_secret(p.api_key) if p.api_key else "",
                        "has_api_key": bool(p.api_key),
                        "active": p.id == state.cfg.active_profile_id,
                    }
                    for p in state.cfg.model_profiles
                ]
                self._json(200, {"profiles": profiles})
                return
            if path == "/api/agents_md":
                from spark.web.utils import MAX_AGENTS_MD_BYTES, _safe_agents_md_name

                safe_name = _safe_agents_md_name(state.cfg.context.agents_md)
                if safe_name is None:
                    self._json(400, {"error": "invalid agents_md filename"})
                    return
                path_md = _resolve_in_workdir(str(state.workdir), safe_name)
                if path_md is None:
                    self._json(400, {"error": "agents_md path escapes workdir"})
                    return
                content = ""
                exists = path_md.exists()
                if exists:
                    try:
                        with path_md.open("rb") as handle:
                            raw = handle.read(MAX_AGENTS_MD_BYTES + 1)
                    except OSError:
                        self._json(500, {"error": "unreadable agents_md file"})
                        return
                    if len(raw) > MAX_AGENTS_MD_BYTES:
                        self._json(
                            413,
                            {
                                "error": "agents_md file too large",
                                "max_bytes": MAX_AGENTS_MD_BYTES,
                            },
                        )
                        return
                    try:
                        content = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        self._json(400, {"error": "agents_md file must be UTF-8"})
                        return
                self._json(
                    200,
                    {
                        "filename": state.cfg.context.agents_md,
                        "exists": exists,
                        "content": content,
                        "chars": len(content),
                        "max_fragment_chars": state.cfg.context.max_fragment_chars,
                    },
                )
                return
            if path == "/api/memory":
                query = parse_qs(urlparse(self.path).query)
                q = (query.get("q", [""])[0] or "").strip()
                items = state.memory.list_memories()
                search_hits: list[dict] = []
                if q:
                    try:
                        for row, score in state.memory.search(q, top_k=8):
                            entry = row.to_dict()
                            entry["score"] = score
                            search_hits.append(entry)
                    except Exception as exc:
                        search_hits = [{"error": str(exc)}]
                self._json(
                    200,
                    {
                        "items": items,
                        "search": search_hits,
                        "stats": state.memory.stats(),
                    },
                )
                return
            if path == "/api/sessions":
                rows = state.store.list_sessions()
                sessions = [
                    {
                        k: row.get(k)
                        for k in (
                            "id",
                            "title",
                            "keywords",
                            "workdir",
                            "model",
                            "created_at",
                            "updated_at",
                        )
                    }
                    for row in rows
                    if row["workdir"] == str(state.workdir)
                ][:200]
                self._json(200, {"sessions": sessions, "current": state.session_id})
                return
            if path == "/api/sessions/search":
                # 全文检索（FTS5 trigram，中文子串友好）。侧栏此前只在已加载的
                # 会话标题/keywords/model 里过滤，搜不到消息正文；store 里早就有
                # 这套检索能力，只是没接线。
                qs = parse_qs(urlparse(self.path).query)
                q = (qs.get("q", [""])[0] or "").strip()
                try:
                    limit = int((qs.get("limit", ["20"])[0] or "20").strip())
                except ValueError:
                    limit = 20
                limit = max(1, min(limit, 100))
                if not q:
                    self._json(200, {"hits": []})
                    return
                try:
                    hits = state.store.search(q, limit=limit)
                except Exception as exc:  # noqa: BLE001 — 检索失败给空结果而非 500
                    logger.debug("session search failed: %s", exc)
                    hits = []
                self._json(
                    200,
                    {"hits": [h for h in hits if h.get("workdir") == str(state.workdir)]},
                )
                return
            if path == "/api/checkpoints":
                cps = state.store.list_checkpoints(state.session_id)
                self._json(200, {"checkpoints": cps})
                return
            # P2 上下文合同：turn 级重放记录（当前会话）
            if path == "/api/replays":
                from spark.core.replay import load_replays

                records = load_replays(state.session_id)
                turns = []
                for i, record in enumerate(records):
                    t = record.get("turn") or {}
                    turns.append(
                        {
                            "index": i,
                            "prompt": (t.get("prompt") or "")[:120],
                            "status": t.get("status"),
                            "duration_ms": t.get("duration_ms"),
                            "tools": len(t.get("tool_calls") or []),
                            "resumed": t.get("resumed", False),
                            "text_preview": (t.get("text") or "")[:160],
                            "usage": t.get("usage"),
                            "recorded_at": record.get("recorded_at"),
                        }
                    )
                self._json(200, {"session_id": state.session_id, "turns": turns})
                return
            if path == "/api/replays/turn":
                from spark.core.replay import load_replays

                query = parse_qs(urlparse(self.path).query)
                try:
                    index = int((query.get("index", ["0"])[0] or "0").strip())
                except ValueError:
                    index = -1
                records = load_replays(state.session_id)
                if index < 0 or index >= len(records):
                    self._json(404, {"error": f"turn index out of range (0..{len(records) - 1})"})
                    return
                self._json(200, records[index])
                return
            # P2 双粒度时间机器：检查点 + 消息级 undo 统一时间轴
            if path == "/api/timeline":
                checkpoints = state.store.list_checkpoints(state.session_id)
                messages = state.store.list_message_briefs(state.session_id, limit=200)
                self._json(
                    200,
                    {
                        "session_id": state.session_id,
                        "checkpoints": checkpoints,
                        "messages": messages,
                    },
                )
                return
            # H5：自定义命令目录（供命令面板补全）
            if path == "/api/commands":
                from spark.slash import discover_commands

                items = [
                    {"name": c.name, "description": c.description}
                    for c in discover_commands(state.workdir)
                ]
                self._json(200, {"commands": items})
                return
            if path == "/api/history":
                self._json(
                    200,
                    {
                        "session_id": state.session_id,
                        "messages": _history_messages(state),
                    },
                )
                return
            if path == "/api/fs/tree":
                query = parse_qs(urlparse(self.path).query)
                rel = (query.get("path", [""])[0] or "").strip()
                self._json(200, _fs_tree(str(state.workdir), rel))
                return
            if path == "/api/fs/read":
                query = parse_qs(urlparse(self.path).query)
                rel = (query.get("path", [""])[0] or "").strip()
                self._json(200, _fs_read(str(state.workdir), rel, state.sandbox))
                return
            if path == "/api/browse":
                query = parse_qs(urlparse(self.path).query)
                url = (query.get("url", [""])[0] or "").strip()
                if not url:
                    self._json(400, {"error": "url required"})
                    return
                self._json(200, _browse(url))
                return
            # P1-5：内置终端（PTY）——SSE 读流（写/开/关走 POST）
            if path == "/api/pty/stream":
                from spark.web.pty_api import handle_pty_stream

                handle_pty_stream(self)
                return
            # P1-7：MCP registry 聚合代理——搜索 + 国内可用性过滤
            if path == "/api/mcp/registry/search":
                query = parse_qs(urlparse(self.path).query)
                q = (query.get("q", [""])[0] or "").strip()
                limit_s = (query.get("limit", ["20"])[0] or "20").strip()
                include_unavailable = (
                    query.get("include_unavailable", ["0"])[0] or "0"
                ).strip() == "1"
                from spark.mcp_registry import search_servers

                try:
                    limit = int(limit_s)
                except ValueError:
                    limit = 20
                try:
                    result = search_servers(q, limit=limit, include_unavailable=include_unavailable)
                except Exception as exc:  # noqa: BLE001 — 代理层兜底，UI 给错误态
                    self._json(
                        200,
                        {"items": [], "source": "offline", "error": str(exc), "filtered_out": 0},
                    )
                    return
                self._json(200, result)
                return
            self._json(404, {"error": "not found"})

        # ------------------------------------------------------------------
        # Route dispatch — POST
        # ------------------------------------------------------------------
        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if self._guard_mutation(path):
                return
            try:
                body = self._read_json()
            except (_json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
                self._json(400, {"error": str(exc) or "invalid json"})
                return
            except ValueError as exc:
                self._json(413, {"error": str(exc)})
                return

            handler_fn = _POST_HANDLERS.get(path)
            if handler_fn is not None:
                handler_fn(self, body)
                return
            self._json(404, {"error": "not found"})

    return Handler


# ======================================================================
# POST dispatch table — wired from spark.web._handlers
# ======================================================================
_POST_HANDLERS: dict[str, Any] = {
    "/api/models/save": _handle_models_save,
    "/api/models/delete": _handle_models_delete,
    "/api/models/activate": _handle_models_activate,
    "/api/debug/client_log": handle_debug_client_log,
    "/api/automations": _handle_automations,
    "/api/automations/cron_check": _handle_automations_cron_check,
    "/api/permissions/rules": _handle_permissions_rules,
    "/api/permissions/rules/delete": _handle_permissions_rules_delete,
    "/api/approval_profiles/save": _handle_approval_profiles_save,
    "/api/approval_profiles/delete": _handle_approval_profiles_delete,
    "/api/approval_profiles/activate": _handle_approval_profiles_activate,
    "/api/agents_md": _handle_agents_md,
    "/api/memory": _handle_memory,
    "/api/cancel": _handle_cancel,
    "/api/checkpoints/rollback": _handle_checkpoints_rollback,
    "/api/messages/truncate": _handle_messages_truncate,
    "/api/messages/delete": _handle_messages_delete,
    "/api/approval": _handle_approval,
    "/api/question/answer": _handle_question_answer,
    "/api/sessions/new": _handle_sessions_new,
    "/api/sessions/delete": _handle_sessions_delete,
    "/api/sessions/switch": _handle_sessions_switch,
    "/api/sessions/rename": _handle_sessions_rename,
    "/api/sessions/fork": _handle_sessions_fork,
    "/api/selfheal": _handle_selfheal,
    "/api/sessions/export": _handle_session_export,
    "/api/sessions/import": _handle_session_import,
    "/api/chat/stream": _handle_chat_stream,
    "/api/chat": _handle_chat,
    "/api/skills": _handle_skills,
    "/api/prompts": _handle_prompts,
    "/api/plugins": _handle_plugins,
    "/api/knowledge": _handle_knowledge,
    "/api/wiki": _handle_wiki,
    "/api/settings": _handle_settings,
    "/api/test": _handle_test,
    "/api/shell": _handle_shell,
}

# P1-5：内置终端（PTY）写路径（读流是 GET /api/pty/stream，见 _handle_get）
from spark.web.pty_api import (
    handle_pty_close as _pty_close,
    handle_pty_open as _pty_open,
    handle_pty_resize as _pty_resize,
    handle_pty_write as _pty_write,
)

_POST_HANDLERS.update(
    {
        "/api/pty/open": _pty_open,
        "/api/pty/write": _pty_write,
        "/api/pty/resize": _pty_resize,
        "/api/pty/close": _pty_close,
    }
)
