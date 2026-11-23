"""Optional Playwright browser tools.

Playwright is a *dev/browser extra*, not a core dependency. Each workdir gets
one background thread that owns a sync Playwright session; tool handlers stay
synchronous and never block the agent loop on browser startup. URLs are checked
before navigation: public http(s) only, or ``file://`` paths inside the workdir
for local test pages.

The same worker also serves the workbench sidebar.  The browser defaults to
Bing for the home page and for bare-text searches, so opening the built-in
browser and asking for a web search use the same engine.
"""

from __future__ import annotations

import atexit
import base64
import json
import os
import queue
import re
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import quote_plus, unquote, urljoin, urlparse

from pydantic import BaseModel

from spark.browser_policy import (
    DEFAULT_SENSITIVE_RULES,
    SiteRule,
    host_of_url,
    parse_site_rules,
)
from spark.browser_policy import (
    guard as site_guard,
)
from spark.models import ToolResult
from spark.tools.untrusted import wrap

BROWSER_DIR = Path(".spark") / "browser"
MAX_TEXT = 8000
MAX_INTERACTIVE = 60
DEFAULT_BROWSER_HOME = "https://www.bing.com/"
DEFAULT_BROWSER_SEARCH = "https://www.bing.com/search?q="
_SEARCH_SUFFIX = "&setlang=zh-Hans"


class BrowserArgs(BaseModel):
    #: Empty opens the built-in home page (Bing).
    url: str = ""


class BrowserSearchArgs(BaseModel):
    query: str
    max_results: int = 8


class BrowserSnapshotArgs(BaseModel):
    max_text: int = 12000
    max_interactive: int = 60
    #: -1 = main page; 0..N = entry in browser_snapshot frames
    frame_index: int = -1


class BrowserClickArgs(BaseModel):
    selector: str = ""
    #: Prefer the ``ref`` returned by browser_open / browser_snapshot.
    ref: str = ""
    #: Optional semantic fallback instead of CSS: role + accessible name.
    role: str = ""
    name: str = ""
    #: Optional visible-text fallback.  Tried after role/name.
    text: str = ""
    button: str = "left"
    click_count: int = 1
    force: bool = False
    timeout_ms: int = 10000
    wait_after_ms: int = 250
    #: -1 = main page; 0..N = entry in browser_snapshot frames
    frame_index: int = -1


class BrowserTypeArgs(BaseModel):
    selector: str = ""
    ref: str = ""
    text: str
    submit: bool = False
    clear: bool = True
    #: ``fill`` (default, fast) or ``type`` (key-by-key, better for editors).
    mode: Literal["fill", "type"] = "fill"
    timeout_ms: int = 10000
    wait_after_ms: int = 150
    #: -1 = main page; 0..N = entry in browser_snapshot frames
    frame_index: int = -1


class BrowserPressArgs(BaseModel):
    key: str
    selector: str = ""
    ref: str = ""
    repeat: int = 1
    timeout_ms: int = 10000
    #: -1 = main page; 0..N = entry in browser_snapshot frames
    frame_index: int = -1


class BrowserHoverArgs(BaseModel):
    selector: str = ""
    ref: str = ""
    timeout_ms: int = 10000
    #: -1 = main page; 0..N = entry in browser_snapshot frames
    frame_index: int = -1


class BrowserSelectArgs(BaseModel):
    selector: str = ""
    ref: str = ""
    value: str = ""
    label: str = ""
    index: int = -1
    timeout_ms: int = 10000
    #: -1 = main page; 0..N = entry in browser_snapshot frames
    frame_index: int = -1


class BrowserScrollArgs(BaseModel):
    delta_y: int = 600
    delta_x: int = 0
    #: Optional element to scroll into view instead of a pixel delta.
    selector: str = ""
    ref: str = ""
    #: ``top`` / ``bottom`` jump to an edge; empty uses the pixel delta.
    to: str = ""
    #: -1 = main page; 0..N = entry in browser_snapshot frames
    frame_index: int = -1


class BrowserHistoryArgs(BaseModel):
    action: Literal["back", "forward", "reload"] = "back"
    timeout_ms: int = 15000


class BrowserTabsArgs(BaseModel):
    action: Literal["list", "new", "select", "close"] = "list"
    index: int = -1
    url: str = ""


class BrowserWaitArgs(BaseModel):
    selector: str = ""
    text: str = ""
    url_contains: str = ""
    seconds: float = 1.0
    state: Literal["attached", "detached", "visible", "hidden"] = "visible"
    timeout_ms: int = 15000
    #: -1 = main page; 0..N = entry in browser_snapshot frames
    frame_index: int = -1


class BrowserScreenshotArgs(BaseModel):
    name: str = ""
    full_page: bool = False
    selector: str = ""
    ref: str = ""
    #: -1 = main page; 0..N = entry in browser_snapshot frames
    frame_index: int = -1


class BrowserViewportArgs(BaseModel):
    width: int = 1280
    height: int = 800


class BrowserEvalArgs(BaseModel):
    expression: str
    max_chars: int = 8000
    #: -1 = main page; 0..N = entry in browser_snapshot frames
    frame_index: int = -1


class BrowserTraceArgs(BaseModel):
    name: str = "trace"


class BrowserMetricsArgs(BaseModel):
    pass


class BrowserFileUploadArgs(BaseModel):
    selector: str = ""
    ref: str = ""
    path: str
    #: -1 = main page; 0..N = entry in browser_snapshot frames
    frame_index: int = -1


class BrowserDownloadArgs(BaseModel):
    url: str = ""
    selector: str = ""
    ref: str = ""
    name: str = ""
    timeout_ms: int = 30000


class BrowserPDFArgs(BaseModel):
    name: str = ""
    landscape: bool = False
    print_background: bool = True


class BrowserConsoleArgs(BaseModel):
    max_items: int = 50
    level: str = ""
    contains: str = ""
    clear: bool = False


class BrowserNetworkArgs(BaseModel):
    max_items: int = 50
    contains: str = ""
    failed_only: bool = False
    clear: bool = False


class BrowserDialogArgs(BaseModel):
    action: Literal["status", "accept", "dismiss"] = "status"
    prompt_text: str = ""


class BrowserError(RuntimeError):
    pass


BROWSER_ENGINES = ("chromium", "chrome", "msedge", "firefox", "webkit")
_CHROMIUM_ENGINES = frozenset({"chromium", "chrome", "msedge"})
_ENGINE_CHANNELS = {"chrome": "chrome", "msedge": "msedge"}
_PROFILE_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class BrowserOptions:
    """Per-workdir browser execution surface.

    The options are part of the worker identity: changing one rebuilds the
    worker on the next browser action instead of silently keeping the old
    engine/profile.
    """

    engine: str = "chromium"
    profile: str = ""
    cdp_endpoint: str = ""
    headless: bool = True
    site_rules: tuple[SiteRule, ...] = ()


def browser_options(cfg: Any | None) -> BrowserOptions:
    agent = getattr(cfg, "agent", None) if cfg is not None else None
    engine = str(getattr(agent, "browser_engine", "chromium") or "chromium")
    profile = str(getattr(agent, "browser_profile", "") or "").strip()
    endpoint = str(getattr(agent, "browser_cdp_endpoint", "") or "").strip()
    headless = bool(getattr(agent, "browser_headless", True))
    try:
        rules = tuple(parse_site_rules(list(getattr(agent, "browser_site_rules", []) or [])))
    except ValueError as exc:
        raise BrowserError(f"browser_site_rules 无效: {exc}") from exc
    return BrowserOptions(
        engine=engine,
        profile=profile,
        cdp_endpoint=endpoint,
        headless=headless,
        site_rules=rules,
    )


def _engine_family(engine: str) -> Literal["chromium", "firefox", "webkit"]:
    if engine in _CHROMIUM_ENGINES:
        return "chromium"
    if engine == "firefox":
        return "firefox"
    if engine == "webkit":
        return "webkit"
    raise BrowserError(
        f"不支持的浏览器引擎: {engine!r}（可选 {'/'.join(BROWSER_ENGINES)}）"
    )


def _engine_channel(engine: str) -> str | None:
    return _ENGINE_CHANNELS.get(engine)


def _profile_dir(workdir: Path, profile: str) -> Path | None:
    name = profile.strip()
    if not name:
        return None
    if not _PROFILE_RE.fullmatch(name) or name in {".", ".."}:
        raise BrowserError(
            "browser_profile 只能包含字母、数字、点、下划线与短横线（1-64 字符）"
        )
    return workdir.resolve() / BROWSER_DIR / "profiles" / name



def _is_local_file(url: str, workdir: Path) -> tuple[bool, str]:
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return False, ""
    raw = unquote(parsed.path or "")
    if parsed.netloc and parsed.netloc not in {"", "localhost"}:
        raw = f"//{parsed.netloc}{raw}"
    # Windows file URIs are ``file:///C:/...``. Strip the URI's leading slash
    # before constructing Path; Path('/C:/...') on Windows normalizes to a
    # drive-relative string and escapes the workspace check.
    if raw.startswith("/") and len(raw) > 3 and raw[2] == ":":
        raw = raw[1:]
    path = Path(raw)
    try:
        resolved = path.resolve()
        resolved.relative_to(workdir.resolve())
    except ValueError:
        return True, "file:// 路径必须位于工作目录内"
    if not resolved.exists():
        return True, f"文件不存在: {resolved}"
    return True, ""


def validate_browser_url(url: str, workdir: Path) -> None:
    parsed = urlparse(url)
    if parsed.scheme == "file":
        inside, error = _is_local_file(url, workdir)
        if not inside:
            raise BrowserError("只允许打开工作目录内的 file:// 页面")
        if error:
            raise BrowserError(error)
        return
    if parsed.scheme not in {"http", "https"}:
        raise BrowserError("只允许 http/https 或工作目录内 file:// 页面")
    from spark.tools.web import _assert_fetchable

    try:
        _assert_fetchable(url)
    except ValueError as exc:
        raise BrowserError(str(exc)) from exc


def normalize_navigation_target(target: str) -> str:
    """Turn a sidebar omnibox entry into a URL.

    A bare phrase is searched on Bing.  A host-looking string gets HTTPS, so
    ``bing.com`` and ``github.com/badhope/spark`` both work without a scheme.
    Empty input opens the built-in home page.
    """
    target = (target or "").strip()
    if not target:
        return DEFAULT_BROWSER_HOME
    parsed = urlparse(target)
    if parsed.scheme in {"http", "https", "file"}:
        return target
    if target.startswith("//"):
        return "https:" + target
    host = target.split("/", 1)[0]
    if " " not in target and ("." in host or host.startswith("localhost")):
        return "https://" + target
    return DEFAULT_BROWSER_SEARCH + quote_plus(target) + _SEARCH_SUFFIX


def browser_home() -> str:
    return DEFAULT_BROWSER_HOME


def search_url(query: str) -> str:
    return DEFAULT_BROWSER_SEARCH + quote_plus(query.strip()) + _SEARCH_SUFFIX


def _log_url(url: str) -> str:
    """Keep console/network evidence useful without leaking query secrets."""
    try:
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"}:
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    except Exception:
        pass
    return url[:500]


class _Worker:
    """A dedicated thread owning the Playwright sync API + browser."""

    def __init__(self, workdir: Path, options: BrowserOptions | None = None) -> None:
        self.workdir = workdir
        self.options = options or BrowserOptions()
        self._startup_error: Exception | None = None
        self.allow_network = True
        self._owns_browser = True
        self._owns_context = True
        self._browser: Any = None
        self._context: Any = None
        self._queue: queue.Queue[Callable[[], Any] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="spark-browser", daemon=True)
        self._thread.start()
        self._closed = False
        self._pages: list[Any] = []
        self._page_guids: set[str] = set()
        self._active_index = 0
        self._console_log: list[dict[str, Any]] = []
        self._network_log: list[dict[str, Any]] = []
        self._dialog_log: list[dict[str, Any]] = []
        self._next_dialog_action = "dismiss"
        self._next_dialog_prompt = ""
        self._host_cache: dict[str, float] = {}

    def _current_url(self) -> str:
        try:
            return self.active_page().url
        except Exception:
            return ""

    def _site_rule_action(self, host: str) -> str | None:
        from spark.browser_policy import strongest_action

        return strongest_action(list(self.options.site_rules), host)

    def _guard_request(self, route: Any) -> None:
        """Re-check every request so a redirect/JS fetch cannot reach private hosts."""
        url = route.request.url
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"}:
            if not self.allow_network:
                route.abort()
                return
            host = (parsed.hostname or "").lower()
            if self._site_rule_action(host) == "deny":
                route.abort()
                return
            now = time.monotonic()
            if self._host_cache.get(host, 0) < now:
                try:
                    validate_browser_url(url, self.workdir)
                    self._host_cache[host] = now + 30
                except BrowserError:
                    route.abort()
                    return
        elif parsed.scheme == "file":
            inside, error = _is_local_file(url, self.workdir)
            if not inside or error:
                route.abort()
                return
        route.continue_()

    @staticmethod
    def _chromium_candidates() -> list[Path]:
        roots = [
            os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
            str(Path.home() / ".cache" / "ms-playwright"),
        ]
        candidates: list[Path] = []
        for root in roots:
            if not root:
                continue
            base = Path(root)
            if not base.is_dir():
                continue
            for item in sorted(base.glob("chromium-*"), reverse=True):
                exe = item / "chrome-linux" / "chrome"
                if exe.is_file():
                    candidates.append(exe)
        return candidates

    @classmethod
    def _launch(
        cls, launcher: Any, *, headless: bool = True, channel: str | None = None
    ) -> Any:
        kwargs: dict[str, Any] = {"headless": headless}
        if channel:
            kwargs["channel"] = channel
        try:
            return launcher.launch(**kwargs)
        except Exception as first:
            # Chrome/Edge channels point at a system browser; a failure there
            # must be reported as-is instead of silently falling back to a
            # different engine. Only the bundled Chromium gets the fallback.
            if channel:
                raise first
            # Playwright's bundled browser revision may not match the one
            # preinstalled in the container. Fall back to any Chromium binary
            # we can find.
            for exe in cls._chromium_candidates():
                try:
                    return launcher.launch(headless=headless, executable_path=str(exe))
                except Exception:
                    continue
            raise first

    def _context_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "viewport": {"width": 1280, "height": 800},
            "locale": "zh-CN",
            "color_scheme": "light",
            "timezone_id": "Asia/Shanghai",
            "accept_downloads": True,
        }
        if _engine_family(self.options.engine) == "chromium":
            # Firefox/WebKit reject a Chrome UA; let them report their real
            # identity instead of breaking sites that sniff the browser.
            kwargs["user_agent"] = _CHROME_UA
        return kwargs

    def _launch_persistent(
        self, launcher: Any, profile_dir: Path, launch_kwargs: dict[str, Any]
    ) -> Any:
        try:
            return launcher.launch_persistent_context(
                str(profile_dir), **launch_kwargs, **self._context_kwargs()
            )
        except Exception as first:
            if _engine_channel(self.options.engine):
                raise first
            for exe in self._chromium_candidates():
                try:
                    return launcher.launch_persistent_context(
                        str(profile_dir),
                        **launch_kwargs,
                        executable_path=str(exe),
                        **self._context_kwargs(),
                    )
                except Exception:
                    continue
            raise first

    def _open_browser(self) -> None:
        opts = self.options
        family = _engine_family(opts.engine)
        endpoint = opts.cdp_endpoint.strip()
        if endpoint:
            if family != "chromium":
                raise BrowserError("远程 CDP 只能连接 Chromium / Chrome / Edge")
            try:
                self._browser = self._playwright.chromium.connect_over_cdp(endpoint)
            except Exception as exc:  # noqa: BLE001 - report through call()
                raise BrowserError(f"连接 CDP 失败: {endpoint}: {exc}") from exc
            self._owns_browser = False
            contexts = list(self._browser.contexts)
            if contexts:
                # Respect the user's already-open window and tabs; creating a
                # new context would hide their logged-in session.
                self._context = contexts[0]
                self._owns_context = False
            else:
                self._context = self._browser.new_context(**self._context_kwargs())
                self._owns_context = True
            return

        launcher = getattr(self._playwright, family)
        launch_kwargs: dict[str, Any] = {"headless": bool(opts.headless)}
        channel = _engine_channel(opts.engine)
        if channel:
            launch_kwargs["channel"] = channel

        profile_dir = _profile_dir(self.workdir, opts.profile)
        if profile_dir is not None:
            profile_dir.mkdir(parents=True, exist_ok=True)
            self._context = self._launch_persistent(launcher, profile_dir, launch_kwargs)
            self._browser = getattr(self._context, "browser", None)
            self._owns_browser = True
            self._owns_context = True
            return

        self._browser = self._launch(launcher, **launch_kwargs)
        self._context = self._browser.new_context(**self._context_kwargs())
        self._owns_browser = True
        self._owns_context = True

    def _wire_page(self, page: Any) -> None:
        page.route("**/*", self._guard_request)
        page.on("console", self._on_console)
        page.on("pageerror", self._on_page_error)
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_request_failed)
        page.on("dialog", self._on_dialog)

    def _on_console(self, message: Any) -> None:
        try:
            location = message.location or {}
            self._console_log.append(
                {
                    "type": message.type,
                    "text": str(message.text)[:2000],
                    "url": _log_url(str(location.get("url") or "")),
                    "line": location.get("lineNumber"),
                    "page_url": str(getattr(getattr(message, "page", None), "url", "") or ""),
                }
            )
            del self._console_log[:-300]
        except Exception:
            pass

    def _on_page_error(self, error: Any) -> None:
        try:
            self._console_log.append(
                {
                    "type": "pageerror",
                    "text": str(error)[:2000],
                    "url": "",
                    "line": None,
                    "page_url": self._current_url(),
                }
            )
            del self._console_log[:-300]
        except Exception:
            pass

    def _on_response(self, response: Any) -> None:
        try:
            request = response.request
            self._network_log.append(
                {
                    "method": request.method,
                    "url": _log_url(request.url),
                    "status": int(response.status),
                    "ok": bool(response.ok),
                    "resource_type": request.resource_type,
                    "mime": (response.headers or {}).get("content-type", "").split(";", 1)[0],
                    "page_url": str(
                        getattr(getattr(getattr(request, "frame", None), "page", None), "url", "")
                    ),
                }
            )
            del self._network_log[:-300]
        except Exception:
            pass

    def _on_request_failed(self, request: Any) -> None:
        try:
            failure = request.failure or ""
            self._network_log.append(
                {
                    "method": request.method,
                    "url": _log_url(request.url),
                    "status": 0,
                    "ok": False,
                    "resource_type": request.resource_type,
                    "error": str(failure)[:300],
                    "page_url": str(
                        getattr(getattr(getattr(request, "frame", None), "page", None), "url", "")
                    ),
                }
            )
            del self._network_log[:-300]
        except Exception:
            pass

    def _on_dialog(self, dialog: Any) -> None:
        action = self._next_dialog_action or "dismiss"
        prompt = self._next_dialog_prompt
        info: dict[str, Any] = {
            "type": getattr(dialog, "type", ""),
            "message": str(getattr(dialog, "message", ""))[:2000],
            "default_value": str(getattr(dialog, "default_value", "") or ""),
        }
        try:
            if action == "accept":
                dialog.accept(prompt or None)
            else:
                dialog.dismiss()
            info["handled"] = action
        except Exception as exc:  # noqa: BLE001
            info["handled"] = "failed"
            info["error"] = str(exc)
        self._dialog_log.append(info)
        del self._dialog_log[:-50]
        self._next_dialog_action = "dismiss"
        self._next_dialog_prompt = ""


    def _ensure_page(self, page: Any) -> None:
        guid = _page_guid(page)
        if guid in self._page_guids:
            # Playwright can surface the same page via both context.new_page()
            # and the context "page" event; do not create duplicate tabs.
            return
        self._wire_page(page)
        self._page_guids.add(guid)
        self._pages.append(page)

    def _on_page(self, page: Any) -> None:
        """Track popups/tabs opened by a click so the agent can switch to them."""
        if self._closed:
            return
        self._ensure_page(page)

    def _sync_pages(self) -> None:
        """Pick up pages opened by popups without relying on event reentrancy."""
        try:
            opened = list(self._context.pages)
        except Exception:
            return
        for page in opened:
            if _page_guid(page) in self._page_guids:
                continue
            self._wire_page(page)
            self._page_guids.add(_page_guid(page))
            self._pages.append(page)
            self._active_index = len(self._pages) - 1

    def _run(self) -> None:
        from playwright.sync_api import sync_playwright

        try:
            self._playwright = sync_playwright().start()
        except Exception as exc:  # noqa: BLE001 - report through call()
            self._startup_error = exc
            return
        try:
            self._open_browser()
        except Exception as exc:  # noqa: BLE001 - report through call()
            self._startup_error = exc
            self._playwright.stop()
            return
        self._startup_error = None
        try:
            existing = list(self._context.pages)
        except Exception:
            existing = []
        if existing:
            # Persistent profiles and remote CDP sessions can come with tabs
            # already open; reuse them instead of replacing the user's window.
            for page in existing:
                self._wire_page(page)
                self._pages.append(page)
                self._page_guids.add(_page_guid(page))
            self._active_index = len(self._pages) - 1
        else:
            self._page = self._context.new_page()
            self._wire_page(self._page)
            self._pages = [self._page]
            self._page_guids = {_page_guid(self._page)}
            self._active_index = 0
        while True:
            item = self._queue.get()
            if item is None:
                break
            try:
                item()
            except Exception:
                # Future.set_exception() is called by the caller-side wrapper,
                # the worker only needs to stay alive.
                pass
        try:
            if self._owns_context and self._context is not None:
                try:
                    self._context.close()
                except Exception:
                    pass
            if self._owns_browser and self._browser is not None:
                try:
                    self._browser.close()
                except Exception:
                    pass
        finally:
            self._playwright.stop()

    def active_page(self) -> Any:
        """Return the current tab, recreating a blank tab after all were closed."""
        self._sync_pages()
        if not self._pages:
            page = self._context.new_page()
            self._ensure_page(page)
            self._active_index = len(self._pages) - 1
        idx = max(0, min(self._active_index, len(self._pages) - 1))
        page = self._pages[idx]
        if page.is_closed():
            self._page_guids.discard(_page_guid(page))
            self._pages.pop(idx)
            if not self._pages:
                page = self._context.new_page()
                self._ensure_page(page)
            else:
                page = self._pages[min(idx, len(self._pages) - 1)]
            self._active_index = min(idx, len(self._pages) - 1)
        return page

    def call(self, fn: Callable[[Any], Any]) -> Any:
        if self._startup_error is not None:
            raise BrowserError(f"浏览器启动失败: {self._startup_error}")
        future: Future[Any] = Future()

        def run() -> None:
            try:
                future.set_result(fn(self.active_page()))
            except Exception as exc:  # noqa: BLE001 - forwarded to the caller
                future.set_exception(exc)

        self._queue.put(run)
        return future.result(timeout=45)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)


_workers: dict[str, _Worker] = {}
_lock = threading.Lock()
_revision = 0


def _touch() -> int:
    """Mark the live viewport dirty after any page-changing action."""
    global _revision
    with _lock:
        _revision += 1
        return _revision


def live_revision() -> int:
    with _lock:
        return _revision


def has_worker(workdir: Path) -> bool:
    key = str(workdir.resolve())
    with _lock:
        return key in _workers


def tab_host(workdir: Path, index: int) -> str:
    """Return the host of a specific tab without starting an idle browser."""
    key = str(workdir.resolve())
    with _lock:
        worker = _workers.get(key)
    if worker is None or worker._startup_error is not None:
        return ""
    try:
        return str(
            worker.call(
                lambda _page: host_of_url(worker._pages[index].url)
                if 0 <= index < len(worker._pages)
                else ""
            )
        )
    except Exception:
        return ""


def current_host(workdir: Path) -> str:
    """Return the active page host without starting an idle browser."""
    key = str(workdir.resolve())
    with _lock:
        worker = _workers.get(key)
    if worker is None or worker._startup_error is not None:
        return ""
    try:
        return host_of_url(str(worker.call(lambda page: page.url)))
    except Exception:
        return ""


def evaluate_call_guard(
    cfg: Any,
    tool_name: str,
    args: dict[str, Any],
    workdir: Path,
    *,
    host: str | None = None,
) -> tuple[str, str]:
    """Compute the per-domain guard for one browser call.

    Called by the approval layer before execution. Navigation tools use their
    target URL; page actions use the current tab's host. When there is no host
    (file:// or no worker), rules do not apply.
    """
    options = browser_options(cfg)
    host = host or ""
    if tool_name == "browser_open":
        target = normalize_navigation_target(str(args.get("url") or ""))
        host = host_of_url(target)
    elif tool_name == "browser_search":
        host = host_of_url(DEFAULT_BROWSER_SEARCH)
    elif tool_name == "browser_tabs":
        action = str(args.get("action") or "list")
        if action == "select":
            raw_index = args.get("index", -1)
            index = int(raw_index) if raw_index is not None else -1
            host = tab_host(workdir, index)
        elif action == "new":
            target = normalize_navigation_target(str(args.get("url") or ""))
            host = host_of_url(target)
        elif not host:
            host = current_host(workdir)
    elif not host:
        host = current_host(workdir)
    return site_guard(
        list(options.site_rules),
        tool_name,
        host,
        args,
        fallback_rules=DEFAULT_SENSITIVE_RULES,
    )


def browser_install_hint(engine: str, error: str) -> str:
    """Turn a Playwright launch error into the shortest actionable fix."""
    family = _engine_family(engine)
    lowered = error.lower()
    if any(
        token in lowered
        for token in (
            "missing dependencies",
            "host system is missing",
            "libnss",
            "libgbm",
            "libwoff",
            "libharfbuzz",
            "libvpx",
            "error while loading shared libraries",
            "shared libraries",
        )
    ):
        return (
            'sudo -n env PATH="$PATH" "$(which python)" -m playwright install-deps '
            + family
        )
    if any(
        token in lowered
        for token in ("executable doesn't exist", "executable not found", "no such file")
    ):
        return f"python -m playwright install {family}"
    return "检查 PLAYWRIGHT_BROWSERS_PATH、浏览器引擎名称，或运行 spark doctor"


def diagnose_browser(cfg: Any, workdir: Path | None = None) -> dict[str, Any]:
    """Launch the configured engine and prove it can render a page.

    This is deliberately independent of the shared worker: doctor must not
    inherit a broken worker, and it must not leave a browser behind.
    """
    options = browser_options(cfg)
    family = _engine_family(options.engine)
    channel = _engine_channel(options.engine)
    result: dict[str, Any] = {
        "engine": options.engine,
        "family": family,
        "headless": True,
        "browsers_path": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
        "ok": False,
    }
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"Playwright 未安装: {exc}"
        result["hint"] = "pip install 'spark-agent[browser]'"
        return result
    try:
        with sync_playwright() as playwright:
            launcher = getattr(playwright, family)
            kwargs: dict[str, Any] = {"headless": True}
            if channel:
                kwargs["channel"] = channel
            try:
                browser = launcher.launch(**kwargs)
            except Exception as first:
                if channel or family != "chromium":
                    raise first
                last: Exception = first
                for exe in _Worker._chromium_candidates():
                    try:
                        browser = launcher.launch(
                            **kwargs, executable_path=str(exe)
                        )
                        break
                    except Exception as exc:  # noqa: BLE001
                        last = exc
                else:
                    raise last
            try:
                result["version"] = getattr(browser, "version", "")
                page = browser.new_page()
                page.set_content("<title>spark browser doctor</title><p>ok</p>")
                result["title"] = page.title()
                result["ok"] = result["title"] == "spark browser doctor"
                page.close()
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        result["hint"] = browser_install_hint(options.engine, str(exc))
    return result


def live_state(workdir: Path, cfg: Any | None = None) -> dict[str, Any]:
    """Read the shared Playwright page for the sidebar live view.

    Never starts a browser just because the panel asked for state: an idle
    browser would otherwise appear on every page load.  ``active`` is false
    until the agent (or the user's address bar) opens a page.
    """
    key = str(workdir.resolve())
    with _lock:
        worker = _workers.get(key)
        revision = _revision
    configured = browser_options(cfg)
    base = {
        "home": DEFAULT_BROWSER_HOME,
        "search_engine": "bing",
        "search_url": DEFAULT_BROWSER_SEARCH,
        "revision": revision,
        "configured_engine": configured.engine,
        "configured_profile": configured.profile,
        "configured_remote": bool(configured.cdp_endpoint),
        "headless": configured.headless,
    }
    if worker is None:
        return {"active": False, **base}
    if worker._startup_error is not None:
        return {
            "active": True,
            "error": f"浏览器启动失败: {worker._startup_error}",
            **base,
            "engine": worker.options.engine,
            "profile": worker.options.profile,
            "remote": bool(worker.options.cdp_endpoint),
            "restart_required": worker.options != configured,
        }
    try:
        info = worker.call(
            lambda page: {
                "url": "" if page.url == "about:blank" else page.url,
                "title": "" if page.url == "about:blank" else _page_title(page),
                "viewport": page.viewport_size or {},
                "tabs": len(worker._pages),
            }
        )
    except Exception as exc:  # noqa: BLE001 - surface through the panel
        return {
            "active": True,
            "error": f"读取浏览器状态失败: {exc}",
            **base,
            "engine": worker.options.engine,
            "profile": worker.options.profile,
            "remote": bool(worker.options.cdp_endpoint),
            "restart_required": worker.options != configured,
        }
    return {
        "active": True,
        **info,
        **base,
        "engine": worker.options.engine,
        "profile": worker.options.profile,
        "remote": bool(worker.options.cdp_endpoint),
        "restart_required": worker.options != configured,
    }


def live_screenshot(workdir: Path) -> bytes | None:
    key = str(workdir.resolve())
    with _lock:
        worker = _workers.get(key)
    if worker is None or worker._startup_error is not None:
        return None
    try:
        data = worker.call(lambda page: page.screenshot(type="png"))
    except Exception:
        return None
    if isinstance(data, bytes):
        return data
    return None


def live_action(
    ctx: Any,
    *,
    action: str,
    x: float = 0,
    y: float = 0,
    text: str = "",
    key: str = "",
) -> dict[str, Any]:
    """Actions initiated from the sidebar viewport (not the model tool path)."""
    worker = _get_worker(ctx.sandbox.root, ctx.config)
    worker.allow_network = bool(getattr(ctx.config.agent, "network_access", True))

    def run(page: Any) -> dict[str, Any]:
        if action == "back":
            page.go_back(wait_until="domcontentloaded", timeout=15_000)
        elif action == "forward":
            page.go_forward(wait_until="domcontentloaded", timeout=15_000)
        elif action == "reload":
            page.reload(wait_until="domcontentloaded", timeout=15_000)
        elif action == "click":
            size = page.viewport_size or {"width": 1280, "height": 800}
            px = max(0, min(float(x), 1000)) / 1000 * float(size["width"])
            py = max(0, min(float(y), 1000)) / 1000 * float(size["height"])
            page.mouse.click(px, py)
        elif action == "type":
            page.keyboard.type(text)
        elif action == "key":
            page.keyboard.press(key)
        else:
            raise BrowserError(f"未知浏览器动作: {action}")
        return _snapshot(worker.active_page())

    try:
        result = worker.call(run)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"浏览器操作失败: {exc}"}
    _touch()
    return {"ok": True, **result}


def _get_worker(workdir: Path, cfg: Any | None = None) -> _Worker:
    key = str(workdir.resolve())
    options = browser_options(cfg)
    with _lock:
        worker = _workers.get(key)
        if worker is not None and worker.options != options:
            # Config changed (engine/profile/CDP). Retire the old worker so the
            # next action cannot silently keep the previous browser surface.
            worker.close()
            worker = None
            _workers.pop(key, None)
        if worker is None:
            worker = _Worker(workdir, options)
            _workers[key] = worker
        return worker


def _close_all() -> None:
    with _lock:
        for worker in _workers.values():
            worker.close()
        _workers.clear()


atexit.register(_close_all)


def _page_title(page: Any) -> str:
    try:
        return page.title()
    except Exception:
        return ""


def _page_guid(page: Any) -> str:
    """Stable identity for a Playwright page across event/returned wrappers."""
    try:
        return str(page._impl_obj._guid)
    except Exception:
        return str(id(page))


def _settle(page: Any, timeout_ms: int = 1500) -> None:
    """Best-effort wait after navigation/actions without hanging on long-poll pages."""
    for state in ("load", "networkidle"):
        try:
            page.wait_for_load_state(state, timeout=timeout_ms)
        except Exception:
            pass
    try:
        page.wait_for_timeout(100)
    except Exception:
        pass


def _interactive(page: Any, limit: int = MAX_INTERACTIVE) -> list[dict[str, Any]]:
    """Visible controls with stable refs and enough metadata for follow-up actions.

    Refs are assigned as ``data-spark-ref`` attributes.  Actions can use
    ``[data-spark-ref="e12"]`` instead of a brittle CSS path copied from a
    generated class name.  Existing refs are preserved across snapshots so a
    model can read a snapshot, then act without another lookup.
    """
    max_items = max(1, min(int(limit), 100))
    script = """
    (maxItems) => {
      const visible = (el) => {
        const style = getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden'
          && style.opacity !== '0' && el.getClientRects().length > 0
          && rect.width > 1 && rect.height > 1;
      };
      const labelOf = (el) => {
        const explicit = el.getAttribute('aria-label')
          || el.getAttribute('aria-labelledby')
          || el.getAttribute('placeholder')
          || el.getAttribute('title');
        if (explicit) return explicit.trim().slice(0, 80);
        if (el.labels && el.labels.length) return el.labels[0].innerText.trim().slice(0, 80);
        return '';
      };
      const nameOf = (el) => {
        const text = (el.innerText || el.value || '').trim();
        return (labelOf(el) || text || el.getAttribute('name') || '').slice(0, 80);
      };
      const roleOf = (el) => {
        const explicit = el.getAttribute('role');
        if (explicit) return explicit;
        const tag = el.tagName.toLowerCase();
        if (tag === 'a') return 'link';
        if (tag === 'button') return 'button';
        if (tag === 'textarea') return 'textbox';
        if (tag === 'select') return 'combobox';
        if (el.isContentEditable) return 'textbox';
        if (tag === 'input') {
          const type = (el.getAttribute('type') || 'text').toLowerCase();
          if (type === 'checkbox') return 'checkbox';
          if (type === 'radio') return 'radio';
          if (type === 'submit' || type === 'button' || type === 'image') return 'button';
          if (type === 'search') return 'searchbox';
          return 'textbox';
        }
        return tag;
      };
      // A document-scoped generation prefix prevents a ref captured before
      // navigation from resolving to a different control on the new page.
      // Same-document snapshots keep the prefix and continue the sequence.
      if (!window.__sparkRefGen) {
        window.__sparkRefGen = 'r' + Math.random().toString(36).slice(2, 7);
        window.__sparkRefSeq = 0;
      }
      const gen = window.__sparkRefGen;
      let seq = Number(window.__sparkRefSeq) || 0;
      for (const el of [...document.querySelectorAll('[data-spark-ref]')]) {
        const m = new RegExp('^' + gen + '-e(\\d+)$').exec(
          el.getAttribute('data-spark-ref') || ''
        );
        if (m) seq = Math.max(seq, parseInt(m[1], 10));
      }
      const refFor = (el) => {
        const current = el.getAttribute('data-spark-ref');
        if (current) return current;
        const ref = gen + '-e' + (++seq);
        el.setAttribute('data-spark-ref', ref);
        window.__sparkRefSeq = seq;
        return ref;
      };
      const nodes = [...document.querySelectorAll(
        'a[href],button,input,textarea,select,[role="button"],[role="link"],[role="textbox"],[role="combobox"],[role="checkbox"],[role="radio"],[contenteditable="true"]'
      )]
        .filter((el) => visible(el))
        .sort((a, b) => {
          const rank = (el) => {
            const tag = el.tagName.toLowerCase();
            if (tag === 'input' || tag === 'textarea' || tag === 'select') return 0;
            if (tag === 'button') return 1;
            if (tag === 'a') return 3;
            return 2;
          };
          return rank(a) - rank(b);
        })
        .slice(0, maxItems);
      return nodes.map((el, index) => {
        const ref = refFor(el);
        const rect = el.getBoundingClientRect();
        const tag = el.tagName.toLowerCase();
        const type = (el.getAttribute('type') || '').toLowerCase();
        const isPassword = type === 'password';
        return {
          index,
          ref,
          selector: '[data-spark-ref="' + ref + '"]',
          tag,
          role: roleOf(el),
          type,
          id: el.id || '',
          name: el.getAttribute('name') || '',
          label: labelOf(el),
          text: nameOf(el),
          value: isPassword ? '***' : String(el.value || '').slice(0, 120),
          placeholder: el.getAttribute('placeholder') || '',
          href: tag === 'a' ? String(el.href || '').slice(0, 240) : '',
          checked: typeof el.checked === 'boolean' ? el.checked : undefined,
          disabled: typeof el.disabled === 'boolean' ? el.disabled : undefined,
          bbox: {
            x: Math.round(rect.x), y: Math.round(rect.y),
            width: Math.round(rect.width), height: Math.round(rect.height),
          },
        };
      });
    }
    """
    try:
        rows = page.evaluate(script, max_items)
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    return rows


def _snapshot(
    page: Any,
    *,
    max_text: int = MAX_TEXT,
    max_interactive: int = MAX_INTERACTIVE,
    frame_index: int = -1,
) -> dict[str, Any]:
    max_chars = max(500, min(int(max_text), 50_000))
    target = _frame(page, frame_index)
    try:
        text = target.locator("body").inner_text(timeout=5_000)
    except Exception:
        text = ""
    try:
        scroll = target.evaluate(
            """() => ({
              x: window.scrollX, y: window.scrollY,
              width: Math.max(document.body ? document.body.scrollWidth : 0, document.documentElement.scrollWidth),
              height: Math.max(document.body ? document.body.scrollHeight : 0, document.documentElement.scrollHeight),
            })"""
        )
    except Exception:
        scroll = {}
    try:
        frames = [
            {"index": i, "url": frame.url, "name": frame.name}
            for i, frame in enumerate(page.frames[:10])
        ]
    except Exception:
        frames = []
    title = _page_title(page)
    return {
        "url": page.url,
        "title": title,
        "viewport": page.viewport_size or {},
        "scroll": scroll,
        "frames": frames,
        "frame_index": frame_index,
        "frame_url": getattr(target, "url", page.url),
        "text": wrap(text[:max_chars]),
        "truncated": len(text) > max_chars,
        "interactive": _interactive(target, max_interactive),
    }


def _ref(value: str) -> str:
    value = (value or "").strip()
    if value and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise BrowserError("ref 格式无效")
    return value


def _locator(
    page: Any,
    *,
    selector: str = "",
    ref: str = "",
    role: str = "",
    name: str = "",
    text: str = "",
) -> Any:
    ref = _ref(ref)
    if ref:
        return page.locator(f'[data-spark-ref="{ref}"]').first
    if selector.strip():
        return page.locator(selector.strip()).first
    if role.strip():
        kwargs: dict[str, Any] = {"exact": False}
        if name.strip():
            kwargs["name"] = name.strip()
        return page.get_by_role(role.strip(), **kwargs).first
    if text.strip():
        return page.get_by_text(text.strip(), exact=False).first
    raise BrowserError("需要一个定位条件：ref、selector、role/name 或 text")


def _frame(page: Any, index: int = -1) -> Any:
    """Resolve -1 to the main frame and 0..N to browser_snapshot frames."""
    if index < 0:
        return page
    frames = list(page.frames)
    if index >= len(frames):
        raise BrowserError(
            f"frame_index 无效: {index}; 当前页面有 {len(frames)} 个 frame (0..{len(frames)-1})"
        )
    return frames[index]


def _network_allowed(ctx: Any) -> bool:
    return bool(getattr(ctx.config.agent, "network_access", True))


def _action_failure(label: str, exc: Exception, *, ref: str = "") -> ToolResult:
    """Return a stable, model-readable error for browser actions.

    A ref from a previous document is the most common failure after a
    navigation.  Tell the model to re-snapshot instead of letting Playwright's
    timeout text stand alone.
    """
    payload: dict[str, Any] = {"error": f"{label}: {exc}"}
    if ref:
        payload["error_type"] = "stale_or_missing_ref"
        payload["ref"] = ref
        payload["hint"] = (
            "这个 ref 已失效或不存在。页面跳转、刷新或 DOM 大改后 ref 会重新生成；"
            "先调用 browser_snapshot 获取新的 ref，再重试；不要继续猜旧 ref。"
        )
    return ToolResult(ok=False, payload=payload)


def browser_open(ctx: Any, args: BrowserArgs) -> ToolResult:
    target = args.url.strip()
    if not target:
        target = DEFAULT_BROWSER_HOME
    if not _network_allowed(ctx) and not target.startswith("file:"):
        return ToolResult(ok=False, payload={"error": "网络访问已关闭（agent.network_access=false）"})
    try:
        validate_browser_url(target, ctx.sandbox.root)
    except BrowserError as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    worker = _get_worker(ctx.sandbox.root, ctx.config)
    worker.allow_network = _network_allowed(ctx)

    def action(page: Any) -> dict[str, Any]:
        response = page.goto(target, wait_until="domcontentloaded", timeout=30_000)
        _settle(page)
        result = _snapshot(page)
        if response is not None:
            result["status"] = response.status
        return result

    try:
        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"浏览器打开失败: {exc}"})
    _touch()
    return ToolResult(ok=True, payload=result)


def browser_search(ctx: Any, args: BrowserSearchArgs) -> ToolResult:
    query = args.query.strip()
    if not query:
        return ToolResult(ok=False, payload={"error": "query 不能为空"})
    if not _network_allowed(ctx):
        return ToolResult(ok=False, payload={"error": "网络访问已关闭（agent.network_access=false）"})
    target = search_url(query)
    try:
        validate_browser_url(target, ctx.sandbox.root)
    except BrowserError as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    worker = _get_worker(ctx.sandbox.root, ctx.config)
    worker.allow_network = _network_allowed(ctx)
    max_results = max(1, min(args.max_results, 10))
    script = """
    (max) => {
      const rows = [...document.querySelectorAll('#b_results > li.b_algo, #b_results li.b_algo')]
        .slice(0, max);
      return rows.map((li, index) => {
        const a = li.querySelector('h2 a') || li.querySelector('a');
        if (!a) return null;
        const title = (a.innerText || '').trim();
        const href = a.href || '';
        const snippetEl = li.querySelector('.b_caption p, .b_snippet, .b_caption');
        return {
          index, title, url: href,
          snippet: (snippetEl ? snippetEl.innerText : '').trim().slice(0, 300),
        };
      }).filter(Boolean);
    }
    """

    def action(page: Any) -> dict[str, Any]:
        page.goto(target, wait_until="domcontentloaded", timeout=30_000)
        _settle(page)
        page.wait_for_timeout(250)
        try:
            rows = page.evaluate(script, max_results)
        except Exception:
            rows = []
        return {
            "engine": "bing",
            "query": query,
            "results": wrap(json.dumps(rows, ensure_ascii=False)),
            "count": len(rows),
            **_snapshot(page),
        }

    try:
        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"Bing 搜索失败: {exc}"})
    _touch()
    return ToolResult(ok=True, payload=result)


def browser_snapshot(ctx: Any, args: BrowserSnapshotArgs) -> ToolResult:
    worker = _get_worker(ctx.sandbox.root, ctx.config)
    try:
        result = worker.call(
            lambda page: _snapshot(
                page,
                max_text=max(500, min(args.max_text, 50_000)),
                max_interactive=max(1, min(args.max_interactive, 100)),
                frame_index=args.frame_index,
            )
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"读取页面失败: {exc}"})
    return ToolResult(ok=True, payload=result)


def browser_click(ctx: Any, args: BrowserClickArgs) -> ToolResult:
    worker = _get_worker(ctx.sandbox.root, ctx.config)

    def action(page: Any) -> dict[str, Any]:
        target = _frame(page, args.frame_index)
        loc = _locator(
            target,
            selector=args.selector,
            ref=args.ref,
            role=args.role,
            name=args.name,
            text=args.text,
        )
        loc.click(
            button=args.button if args.button in {"left", "right", "middle"} else "left",
            click_count=max(1, min(args.click_count, 3)),
            timeout=max(1000, min(args.timeout_ms, 30_000)),
            force=args.force,
        )
        page.wait_for_timeout(max(0, min(args.wait_after_ms, 3_000)))
        return _snapshot(worker.active_page(), frame_index=args.frame_index)

    try:
        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return _action_failure("点击失败", exc, ref=args.ref)
    _touch()
    return ToolResult(ok=True, payload=result)


def browser_type(ctx: Any, args: BrowserTypeArgs) -> ToolResult:
    worker = _get_worker(ctx.sandbox.root, ctx.config)

    def action(page: Any) -> dict[str, Any]:
        target = _frame(page, args.frame_index)
        if args.ref or args.selector:
            field = _locator(target, selector=args.selector, ref=args.ref)
            if args.mode == "type":
                field.click(timeout=max(1000, min(args.timeout_ms, 30_000)))
                if args.clear:
                    field.press("Control+A")
                    field.press("Backspace")
                field.type(args.text, delay=50)
            else:
                field.fill(args.text, timeout=max(1000, min(args.timeout_ms, 30_000)))
            if args.submit:
                field.press("Enter")
        else:
            # Without a target, type into whatever is already focused.  This
            # mirrors a human click-then-type flow and is useful for custom
            # contenteditable editors that cannot be located reliably.
            page.keyboard.type(args.text)
            if args.submit:
                page.keyboard.press("Enter")
        page.wait_for_timeout(max(0, min(args.wait_after_ms, 3_000)))
        result = _snapshot(worker.active_page(), frame_index=args.frame_index)
        result["submitted"] = bool(args.submit)
        result["input_mode"] = args.mode
        return result

    try:
        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return _action_failure("输入失败", exc, ref=args.ref)
    _touch()
    return ToolResult(ok=True, payload=result)


def browser_press(ctx: Any, args: BrowserPressArgs) -> ToolResult:
    if not args.key.strip():
        return ToolResult(ok=False, payload={"error": "key 不能为空"})
    worker = _get_worker(ctx.sandbox.root, ctx.config)

    def action(page: Any) -> dict[str, Any]:
        frame = _frame(page, args.frame_index)
        if args.ref or args.selector:
            target = _locator(frame, selector=args.selector, ref=args.ref)
            target.focus(timeout=max(1000, min(args.timeout_ms, 30_000)))
            for _ in range(max(1, min(args.repeat, 20))):
                target.press(args.key)
        else:
            for _ in range(max(1, min(args.repeat, 20))):
                page.keyboard.press(args.key)
        page.wait_for_timeout(150)
        return _snapshot(worker.active_page(), frame_index=args.frame_index)

    try:
        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return _action_failure("按键失败", exc, ref=args.ref)
    _touch()
    return ToolResult(ok=True, payload=result)


def browser_hover(ctx: Any, args: BrowserHoverArgs) -> ToolResult:
    worker = _get_worker(ctx.sandbox.root, ctx.config)

    def action(page: Any) -> dict[str, Any]:
        frame = _frame(page, args.frame_index)
        target = _locator(frame, selector=args.selector, ref=args.ref)
        target.hover(timeout=max(1000, min(args.timeout_ms, 30_000)))
        page.wait_for_timeout(200)
        return _snapshot(worker.active_page(), frame_index=args.frame_index)

    try:
        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return _action_failure("悬停失败", exc, ref=args.ref)
    _touch()
    return ToolResult(ok=True, payload=result)


def browser_select(ctx: Any, args: BrowserSelectArgs) -> ToolResult:
    if not (args.value or args.label or args.index >= 0):
        return ToolResult(ok=False, payload={"error": "需要 value、label 或 index"})
    worker = _get_worker(ctx.sandbox.root, ctx.config)

    def action(page: Any) -> dict[str, Any]:
        frame = _frame(page, args.frame_index)
        target = _locator(frame, selector=args.selector, ref=args.ref)
        kwargs: dict[str, Any] = {}
        if args.value:
            kwargs["value"] = args.value
        elif args.label:
            kwargs["label"] = args.label
        else:
            kwargs["index"] = args.index
        target.select_option(**kwargs, timeout=max(1000, min(args.timeout_ms, 30_000)))
        page.wait_for_timeout(150)
        return _snapshot(worker.active_page(), frame_index=args.frame_index)

    try:
        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return _action_failure("选择失败", exc, ref=args.ref)
    _touch()
    return ToolResult(ok=True, payload=result)


def browser_scroll(ctx: Any, args: BrowserScrollArgs) -> ToolResult:
    worker = _get_worker(ctx.sandbox.root, ctx.config)

    def action(page: Any) -> dict[str, Any]:
        frame = _frame(page, args.frame_index)
        if args.ref or args.selector:
            target = _locator(frame, selector=args.selector, ref=args.ref)
            target.scroll_into_view_if_needed(timeout=10_000)
        elif args.to == "top":
            frame.evaluate("() => window.scrollTo(0, 0)")
        elif args.to == "bottom":
            frame.evaluate("() => window.scrollTo(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight))")
        else:
            frame.evaluate(
                "(delta) => window.scrollBy(delta.x, delta.y)",
                {"x": args.delta_x, "y": args.delta_y},
            )
        page.wait_for_timeout(150)
        return _snapshot(worker.active_page(), frame_index=args.frame_index)

    try:
        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return _action_failure("滚动失败", exc, ref=args.ref)
    _touch()
    return ToolResult(ok=True, payload=result)


def browser_history(ctx: Any, args: BrowserHistoryArgs) -> ToolResult:
    worker = _get_worker(ctx.sandbox.root, ctx.config)

    def action(page: Any) -> dict[str, Any]:
        timeout = max(1000, min(args.timeout_ms, 30_000))
        if args.action == "back":
            page.go_back(wait_until="domcontentloaded", timeout=timeout)
        elif args.action == "forward":
            page.go_forward(wait_until="domcontentloaded", timeout=timeout)
        else:
            page.reload(wait_until="domcontentloaded", timeout=timeout)
        _settle(page)
        return _snapshot(worker.active_page())

    try:
        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"历史操作失败: {exc}"})
    _touch()
    return ToolResult(ok=True, payload=result)


def _tabs(worker: _Worker) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, page in enumerate(worker._pages):
        try:
            if page.is_closed():
                continue
            rows.append(
                {
                    "index": index,
                    "url": "" if page.url == "about:blank" else page.url,
                    "title": "" if page.url == "about:blank" else _page_title(page),
                    "active": index == worker._active_index,
                }
            )
        except Exception:
            continue
    return rows


def browser_tabs(ctx: Any, args: BrowserTabsArgs) -> ToolResult:
    worker = _get_worker(ctx.sandbox.root, ctx.config)
    action = args.action
    index = args.index

    def run(_page: Any) -> dict[str, Any]:
        if action == "list":
            return {"tabs": _tabs(worker), **_snapshot(worker.active_page())}
        if action == "new":
            page = worker._context.new_page()
            worker._ensure_page(page)
            worker._active_index = len(worker._pages) - 1
            if args.url.strip():
                target = normalize_navigation_target(args.url)
                validate_browser_url(target, ctx.sandbox.root)
                page.goto(target, wait_until="domcontentloaded", timeout=30_000)
                _settle(page)
            elif _network_allowed(ctx):
                page.goto(DEFAULT_BROWSER_HOME, wait_until="domcontentloaded", timeout=30_000)
                _settle(page)
            return {"tabs": _tabs(worker), **_snapshot(worker.active_page())}
        if action == "select":
            if index < 0 or index >= len(worker._pages):
                raise BrowserError(f"标签页索引无效: {index}")
            worker._active_index = index
            return {"tabs": _tabs(worker), **_snapshot(worker.active_page())}
        if action == "close":
            if index < 0 or index >= len(worker._pages):
                raise BrowserError(f"标签页索引无效: {index}")
            old = worker._pages[index]
            worker._page_guids.discard(_page_guid(old))
            old.close()
            worker._pages.pop(index)
            if not worker._pages:
                page = worker._context.new_page()
                worker._ensure_page(page)
            worker._active_index = min(index, len(worker._pages) - 1)
            return {"tabs": _tabs(worker), **_snapshot(worker.active_page())}
        raise BrowserError(f"未知标签页动作: {action}")

    try:
        result = worker.call(run)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"标签页操作失败: {exc}"})
    _touch()
    return ToolResult(ok=True, payload=result)


def browser_wait(ctx: Any, args: BrowserWaitArgs) -> ToolResult:
    worker = _get_worker(ctx.sandbox.root, ctx.config)

    def action(page: Any) -> dict[str, Any]:
        timeout = max(1000, min(args.timeout_ms, 30_000))
        frame = _frame(page, args.frame_index)
        if args.selector.strip():
            frame.locator(args.selector.strip()).first.wait_for(state=args.state, timeout=timeout)
        elif args.text.strip():
            frame.get_by_text(args.text.strip(), exact=False).first.wait_for(
                state=args.state, timeout=timeout
            )
        elif args.url_contains.strip():
            page.wait_for_url(f"**{args.url_contains.strip()}**", timeout=timeout)
        else:
            page.wait_for_timeout(max(0, min(int(args.seconds * 1000), 30_000)))
        return _snapshot(worker.active_page(), frame_index=args.frame_index)

    try:
        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"等待失败: {exc}"})
    _touch()
    return ToolResult(ok=True, payload=result)


def browser_screenshot(ctx: Any, args: BrowserScreenshotArgs) -> ToolResult:
    worker = _get_worker(ctx.sandbox.root, ctx.config)
    out_dir = ctx.sandbox.root / BROWSER_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = "".join(ch for ch in args.name if ch.isalnum() or ch in "-_") or "shot"
    target_path = out_dir / f"{name}-{stamp}.png"
    try:
        def action(page: Any) -> bytes:
            if args.ref or args.selector:
                frame = _frame(page, args.frame_index)
                target = _locator(frame, selector=args.selector, ref=args.ref)
                return target.screenshot(timeout=10_000)
            return page.screenshot(full_page=args.full_page)

        data = worker.call(action)
        if isinstance(data, bytes):
            target_path.write_bytes(data)
        else:
            target_path.write_bytes(base64.b64decode(data))
    except Exception as exc:  # noqa: BLE001
        return _action_failure("截图失败", exc, ref=args.ref)
    try:
        rel = target_path.relative_to(ctx.sandbox.root).as_posix()
    except ValueError:
        rel = str(target_path)
    _touch()
    return ToolResult(
        ok=True,
        payload={
            "path": rel,
            "bytes": target_path.stat().st_size,
            "full_page": args.full_page and not (args.ref or args.selector),
            "element": bool(args.ref or args.selector),
        },
    )


def browser_viewport(ctx: Any, args: BrowserViewportArgs) -> ToolResult:
    worker = _get_worker(ctx.sandbox.root, ctx.config)
    width = max(320, min(args.width, 3840))
    height = max(240, min(args.height, 2160))

    def action(page: Any) -> dict[str, Any]:
        page.set_viewport_size({"width": width, "height": height})
        return _snapshot(page)

    try:
        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"设置视口失败: {exc}"})
    _touch()
    return ToolResult(ok=True, payload=result)


def browser_eval(ctx: Any, args: BrowserEvalArgs) -> ToolResult:
    expression = args.expression.strip()
    if not expression:
        return ToolResult(ok=False, payload={"error": "expression 不能为空"})
    worker = _get_worker(ctx.sandbox.root, ctx.config)
    max_chars = max(500, min(args.max_chars, 50_000))

    def action(page: Any) -> dict[str, Any]:
        value = _frame(page, args.frame_index).evaluate(expression)
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = str(value)
        return {
            "result": wrap(text[:max_chars]),
            "chars": len(text),
            "truncated": len(text) > max_chars,
        }

    try:
        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"执行 JS 失败: {exc}"})
    _touch()
    return ToolResult(ok=True, payload=result)


def browser_trace_start(ctx: Any, args: BrowserTraceArgs) -> ToolResult:
    """Start a Playwright trace (screenshots, DOM snapshots, sources)."""
    worker = _get_worker(ctx.sandbox.root, ctx.config)

    def action(page: Any) -> dict[str, Any]:
        page.context.tracing.start(
            screenshots=True, snapshots=True, sources=True
        )
        return {"started": True, "engine": worker.options.engine}

    try:
        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"启动 trace 失败: {exc}"})
    return ToolResult(ok=True, payload=result)


def browser_trace_stop(ctx: Any, args: BrowserTraceArgs) -> ToolResult:
    """Stop the active trace and save a zip under .spark/browser/traces."""
    worker = _get_worker(ctx.sandbox.root, ctx.config)
    name = _safe_filename(args.name, "trace")
    rel = BROWSER_DIR / "traces" / f"{name}-{int(time.time())}.zip"
    out = ctx.sandbox.root / rel
    out.parent.mkdir(parents=True, exist_ok=True)

    def action(page: Any) -> dict[str, Any]:
        page.context.tracing.stop(path=str(out))
        return {
            "path": str(rel).replace("\\", "/"),
            "bytes": out.stat().st_size if out.is_file() else 0,
            "engine": worker.options.engine,
        }

    try:
        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"停止 trace 失败: {exc}"})
    if not out.is_file() or out.stat().st_size == 0:
        return ToolResult(ok=False, payload={"error": "trace 文件为空或未生成"})
    return ToolResult(ok=True, payload=result)


def browser_metrics(ctx: Any, args: BrowserMetricsArgs | None = None) -> ToolResult:
    """Read Chromium DevTools performance metrics for the current page."""
    worker = _get_worker(ctx.sandbox.root, ctx.config)
    if _engine_family(worker.options.engine) != "chromium":
        return ToolResult(
            ok=False,
            payload={
                "error": "performance metrics 仅 Chromium / Chrome / Edge 支持",
                "engine": worker.options.engine,
            },
        )

    def action(page: Any) -> dict[str, Any]:
        session = page.context.new_cdp_session(page)
        try:
            session.send("Performance.enable")
            rows = session.send("Performance.getMetrics") or {}
        finally:
            try:
                session.detach()
            except Exception:
                pass
        metrics = {
            str(item.get("name")): item.get("value")
            for item in (rows.get("metrics") or [])
            if item.get("name")
        }
        return {"metrics": metrics, "url": page.url}

    try:
        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"读取性能指标失败: {exc}"})
    return ToolResult(ok=True, payload=result)


def browser_file_upload(ctx: Any, args: BrowserFileUploadArgs) -> ToolResult:
    if not (args.ref or args.selector):
        return ToolResult(ok=False, payload={"error": "需要 ref 或 selector 定位文件输入框"})
    try:
        path = ctx.sandbox.resolve(args.path)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"文件路径不可用: {exc}"})
    if not path.is_file():
        return ToolResult(ok=False, payload={"error": f"文件不存在: {path}"})
    worker = _get_worker(ctx.sandbox.root, ctx.config)

    def action(page: Any) -> dict[str, Any]:
        frame = _frame(page, args.frame_index)
        target = _locator(frame, selector=args.selector, ref=args.ref)
        target.set_input_files(str(path), timeout=15_000)
        page.wait_for_timeout(200)
        return _snapshot(worker.active_page(), frame_index=args.frame_index)

    try:
        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return _action_failure("上传文件失败", exc, ref=args.ref)
    _touch()
    return ToolResult(ok=True, payload=result)

def _safe_filename(name: str, fallback: str = "download") -> str:
    base = Path((name or "").strip()).name
    cleaned = "".join(ch for ch in base if ch.isalnum() or ch in "-_.() ")
    cleaned = cleaned.strip(" .")
    return cleaned[:120] or fallback


def browser_download(ctx: Any, args: BrowserDownloadArgs) -> ToolResult:
    """Save a browser download or a cookie-authenticated direct response."""
    worker = _get_worker(ctx.sandbox.root, ctx.config)
    out_dir = ctx.sandbox.root / BROWSER_DIR / "downloads"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    requested = _safe_filename(args.name) if args.name else ""

    try:
        def action(page: Any) -> dict[str, Any]:
            suggested = ""
            if args.url.strip():
                target = args.url.strip()
                timeout = max(1000, min(args.timeout_ms, 60_000))
                response = None
                current = target
                for _ in range(5):
                    validate_browser_url(current, ctx.sandbox.root)
                    response = page.request.get(current, timeout=timeout, max_redirects=0)
                    if 300 <= response.status < 400:
                        location = (response.headers or {}).get("location", "")
                        if not location:
                            raise BrowserError(f"下载重定向缺少 Location: HTTP {response.status}")
                        current = urljoin(current, location)
                        continue
                    break
                if response is None:
                    raise BrowserError("下载请求没有响应")
                if response.status >= 400:
                    raise BrowserError(f"下载请求返回 HTTP {response.status}")
                body = response.body()
                if len(body) > 100 * 1024 * 1024:
                    raise BrowserError("下载内容超过 100 MB，拒绝写入")
                disposition = (response.headers or {}).get("content-disposition", "")
                match = re.search(r'filename\*?=(?:UTF-8\'\')?"([^";]+)"', disposition, re.I)
                if match:
                    suggested = unquote(match.group(1))
                if not suggested:
                    suggested = unquote(urlparse(current).path.rsplit("/", 1)[-1]) or "download.bin"
                filename = requested or _safe_filename(suggested, "download.bin")
                path = out_dir / f"{stamp}-{filename}"
                path.write_bytes(body)
                return {
                    "path": path.relative_to(ctx.sandbox.root).as_posix(),
                    "bytes": len(body),
                    "filename": filename,
                    "suggested": suggested,
                    "source": "url",
                }

            if not (args.ref or args.selector):
                raise BrowserError("需要 url，或 ref/selector 指向可下载的链接/按钮")
            loc = _locator(page, selector=args.selector, ref=args.ref)
            with page.expect_download(timeout=max(1000, min(args.timeout_ms, 120_000))) as info:
                loc.click(timeout=max(1000, min(args.timeout_ms, 60_000)))
            download = info.value
            suggested = download.suggested_filename or "download.bin"
            filename = requested or _safe_filename(suggested, "download.bin")
            path = out_dir / f"{stamp}-{filename}"
            download.save_as(str(path))
            return {
                "path": path.relative_to(ctx.sandbox.root).as_posix(),
                "bytes": path.stat().st_size,
                "filename": filename,
                "suggested": suggested,
                "source": "click",
            }

        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return _action_failure("下载失败", exc, ref=args.ref)
    _touch()
    return ToolResult(ok=True, payload=result)


def browser_pdf(ctx: Any, args: BrowserPDFArgs) -> ToolResult:
    worker = _get_worker(ctx.sandbox.root, ctx.config)
    out_dir = ctx.sandbox.root / BROWSER_DIR / "pdf"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = _safe_filename(args.name, "page")
    target_path = out_dir / f"{name}-{stamp}.pdf"

    def action(page: Any) -> bytes:
        return page.pdf(
            landscape=bool(args.landscape),
            print_background=bool(args.print_background),
            format="A4",
        )

    try:
        data = worker.call(action)
        if not isinstance(data, bytes):
            raise BrowserError("PDF 生成未返回字节流")
        target_path.write_bytes(data)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"PDF 生成失败: {exc}"})
    _touch()
    return ToolResult(
        ok=True,
        payload={
            "path": target_path.relative_to(ctx.sandbox.root).as_posix(),
            "bytes": target_path.stat().st_size,
            "landscape": bool(args.landscape),
            "print_background": bool(args.print_background),
        },
    )


def browser_console(ctx: Any, args: BrowserConsoleArgs) -> ToolResult:
    worker = _get_worker(ctx.sandbox.root, ctx.config)
    limit = max(1, min(args.max_items, 200))
    level = args.level.strip().lower()
    contains = args.contains.strip().lower()

    def action(_page: Any) -> dict[str, Any]:
        active_url = worker._current_url()
        rows = [row for row in worker._console_log if row.get("page_url") == active_url]
        other_count = len(worker._console_log) - len(rows)
        if level:
            rows = [row for row in rows if str(row.get("type", "")).lower() == level]
        if contains:
            rows = [row for row in rows if contains in str(row.get("text", "")).lower()]
        selected = rows[-limit:]
        for row in selected:
            row["text"] = wrap(str(row.get("text", "")))
        if args.clear:
            worker._console_log.clear()
        return {
            "messages": selected,
            "count": len(selected),
            "page_url": active_url,
            "other_page_messages": other_count,
            "total_before_clear": len(rows),
        }

    try:
        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"读取控制台失败: {exc}"})
    return ToolResult(ok=True, payload=result)


def browser_network(ctx: Any, args: BrowserNetworkArgs) -> ToolResult:
    worker = _get_worker(ctx.sandbox.root, ctx.config)
    limit = max(1, min(args.max_items, 200))
    contains = args.contains.strip().lower()

    def action(_page: Any) -> dict[str, Any]:
        active_url = worker._current_url()
        rows = [row for row in worker._network_log if row.get("page_url") == active_url]
        other_count = len(worker._network_log) - len(rows)
        if args.failed_only:
            rows = [row for row in rows if not row.get("ok")]
        if contains:
            rows = [row for row in rows if contains in str(row.get("url", "")).lower()]
        selected = rows[-limit:]
        if args.clear:
            worker._network_log.clear()
        return {
            "requests": selected,
            "count": len(selected),
            "page_url": active_url,
            "other_page_requests": other_count,
            "total_before_clear": len(rows),
            "note": "URL 已去掉 query string，避免泄露令牌；默认只返回当前页面",
        }

    try:
        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"读取网络日志失败: {exc}"})
    return ToolResult(ok=True, payload=result)


def browser_dialog(ctx: Any, args: BrowserDialogArgs) -> ToolResult:
    """Inspect recent dialogs, or arm accept/dismiss for the next dialog."""
    worker = _get_worker(ctx.sandbox.root, ctx.config)

    def action(_page: Any) -> dict[str, Any]:
        if args.action == "status":
            return {
                "dialogs": list(worker._dialog_log)[-20:],
                "count": len(worker._dialog_log),
                "next_action": worker._next_dialog_action,
                "note": "默认 auto-dismiss；要接受 confirm/prompt，先 action=accept 再触发。",
            }
        worker._next_dialog_action = args.action
        worker._next_dialog_prompt = args.prompt_text
        return {
            "armed": args.action,
            "prompt_text": args.prompt_text if args.action == "accept" else "",
            "note": "已武装下一次 dialog；现在触发点击/回车，再调用 action=status 查看结果。",
        }

    try:
        result = worker.call(action)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"对话框操作失败: {exc}"})
    _touch()
    return ToolResult(ok=True, payload=result)
