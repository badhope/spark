"""Optional Playwright browser tools.

Playwright is a *dev/browser extra*, not a core dependency. Each workdir gets
one background thread that owns a sync Playwright session; tool handlers stay
synchronous and never block the agent loop on browser startup. URLs are checked
before navigation: public http(s) only, or ``file://`` paths inside the workdir
for local test pages.
"""

from __future__ import annotations

import atexit
import base64
import os
import queue
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from pydantic import BaseModel

from spark.models import ToolResult
from spark.tools.untrusted import wrap

BROWSER_DIR = Path(".spark") / "browser"
MAX_TEXT = 8000


class BrowserArgs(BaseModel):
    url: str


class BrowserClickArgs(BaseModel):
    selector: str
    button: str = "left"
    click_count: int = 1


class BrowserTypeArgs(BaseModel):
    selector: str
    text: str
    submit: bool = False


class BrowserScrollArgs(BaseModel):
    delta_y: int = 600
    delta_x: int = 0


class BrowserScreenshotArgs(BaseModel):
    name: str = ""


class BrowserViewportArgs(BaseModel):
    width: int = 1280
    height: int = 800


class BrowserWaitArgs(BaseModel):
    selector: str = ""
    seconds: float = 1.0


class BrowserError(RuntimeError):
    pass


def _is_local_file(url: str, workdir: Path) -> tuple[bool, str]:
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return False, ""
    raw = unquote(parsed.path)
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


class _Worker:
    """A dedicated thread owning the Playwright sync API + browser."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self._startup_error: Exception | None = None
        self._queue: queue.Queue[Callable[[], Any] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="spark-browser", daemon=True)
        self._thread.start()
        self._closed = False

    @staticmethod
    def _launch(chromium: Any) -> Any:
        try:
            return chromium.launch(headless=True)
        except Exception as first:
            # Playwright's bundled browser revision may not match the one preinstalled
            # in the container. Fall back to any Chromium binary we can find.
            roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""), str(Path.home() / ".cache" / "ms-playwright")]
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
            for exe in candidates:
                try:
                    return chromium.launch(headless=True, executable_path=str(exe))
                except Exception:
                    continue
            raise first

    def _run(self) -> None:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._launch(self._playwright.chromium)
        except Exception as exc:  # noqa: BLE001 - report through call()
            self._startup_error = exc
            self._playwright.stop()
            return
        self._startup_error = None
        self._context = self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            locale="zh-CN",
        )
        self._page = self._context.new_page()
        while True:
            item = self._queue.get()
            if item is None:
                break
            try:
                item()
            except Exception:
                # Future.set_exception() is called by the caller-side wrapper;
                # the worker only needs to stay alive.
                pass
        try:
            self._browser.close()
        finally:
            self._playwright.stop()

    def call(self, fn: Callable[[Any], Any]) -> Any:
        if self._startup_error is not None:
            raise BrowserError(f"浏览器启动失败: {self._startup_error}")
        future: Future[Any] = Future()

        def run() -> None:
            try:
                future.set_result(fn(self._page))
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


def _get_worker(workdir: Path) -> _Worker:
    key = str(workdir.resolve())
    with _lock:
        worker = _workers.get(key)
        if worker is None:
            worker = _Worker(workdir)
            _workers[key] = worker
        return worker


def _close_all() -> None:
    with _lock:
        for worker in _workers.values():
            worker.close()
        _workers.clear()


atexit.register(_close_all)


def _snapshot(page: Any) -> dict[str, Any]:
    try:
        text = page.locator("body").inner_text(timeout=5_000)
    except Exception:
        text = ""
    return {
        "url": page.url,
        "title": page.title(),
        "text": wrap(text[:MAX_TEXT]),
        "truncated": len(text) > MAX_TEXT,
    }


def browser_open(ctx: Any, args: BrowserArgs) -> ToolResult:
    if not getattr(ctx.config.agent, "network_access", True) and not args.url.startswith("file:"):
        return ToolResult(ok=False, payload={"error": "网络访问已关闭（agent.network_access=false）"})
    try:
        validate_browser_url(args.url, ctx.sandbox.root)
    except BrowserError as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    worker = _get_worker(ctx.sandbox.root)
    try:
        result = worker.call(lambda page: (page.goto(args.url, wait_until="domcontentloaded"), _snapshot(page))[1])
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"浏览器打开失败: {exc}"})
    return ToolResult(ok=True, payload=result)


def browser_click(ctx: Any, args: BrowserClickArgs) -> ToolResult:
    if not args.selector.strip():
        return ToolResult(ok=False, payload={"error": "selector 不能为空"})
    worker = _get_worker(ctx.sandbox.root)

    def action(page: Any) -> dict[str, Any]:
        page.locator(args.selector).first.click(
            button=args.button if args.button in {"left", "right", "middle"} else "left",
            click_count=max(1, min(args.click_count, 3)),
            timeout=10_000,
        )
        return _snapshot(page)

    try:
        return ToolResult(ok=True, payload=worker.call(action))
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"点击失败: {exc}"})


def browser_type(ctx: Any, args: BrowserTypeArgs) -> ToolResult:
    if not args.selector.strip():
        return ToolResult(ok=False, payload={"error": "selector 不能为空"})
    worker = _get_worker(ctx.sandbox.root)

    def action(page: Any) -> dict[str, Any]:
        field = page.locator(args.selector).first
        field.fill(args.text, timeout=10_000)
        if args.submit:
            field.press("Enter")
        return _snapshot(page)

    try:
        return ToolResult(ok=True, payload=worker.call(action))
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"输入失败: {exc}"})


def browser_scroll(ctx: Any, args: BrowserScrollArgs) -> ToolResult:
    worker = _get_worker(ctx.sandbox.root)

    def action(page: Any) -> dict[str, Any]:
        page.mouse.wheel(args.delta_x, args.delta_y)
        return _snapshot(page)

    try:
        return ToolResult(ok=True, payload=worker.call(action))
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"滚动失败: {exc}"})


def browser_wait(ctx: Any, args: BrowserWaitArgs) -> ToolResult:
    worker = _get_worker(ctx.sandbox.root)

    def action(page: Any) -> dict[str, Any]:
        if args.selector.strip():
            page.locator(args.selector).first.wait_for(timeout=15_000)
        else:
            page.wait_for_timeout(max(0, min(int(args.seconds * 1000), 15_000)))
        return _snapshot(page)

    try:
        return ToolResult(ok=True, payload=worker.call(action))
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"等待失败: {exc}"})


def browser_screenshot(ctx: Any, args: BrowserScreenshotArgs) -> ToolResult:
    worker = _get_worker(ctx.sandbox.root)
    out_dir = ctx.sandbox.root / BROWSER_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = "".join(ch for ch in args.name if ch.isalnum() or ch in "-_") or "shot"
    target = out_dir / f"{name}-{stamp}.png"
    try:
        data = worker.call(lambda page: page.screenshot(full_page=False))
        if isinstance(data, bytes):
            target.write_bytes(data)
        else:
            target.write_bytes(base64.b64decode(data))
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"截图失败: {exc}"})
    try:
        rel = target.relative_to(ctx.sandbox.root).as_posix()
    except ValueError:
        rel = str(target)
    return ToolResult(ok=True, payload={"path": rel, "bytes": target.stat().st_size})


def browser_viewport(ctx: Any, args: BrowserViewportArgs) -> ToolResult:
    worker = _get_worker(ctx.sandbox.root)
    width = max(320, min(args.width, 3840))
    height = max(240, min(args.height, 2160))

    def action(page: Any) -> dict[str, Any]:
        page.set_viewport_size({"width": width, "height": height})
        return _snapshot(page)

    try:
        return ToolResult(ok=True, payload=worker.call(action))
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, payload={"error": f"设置视口失败: {exc}"})
