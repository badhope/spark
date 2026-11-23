"""桌面壳：用 pywebview 把本机 Web 界面包成原生应用窗口。

与 `spark web` 共享同一套 HTTP 服务、控制令牌与鉴权；桌面模式固定绑定
回环地址（127.0.0.1），不向局域网暴露端口。窗口关闭时优雅停机：
shutdown HTTP 服务并关闭会话存储。

依赖（可选安装）：
    pip install "spark[desktop]"

Windows 使用系统自带的 Edge WebView2 运行时，无额外下载。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 832
DEFAULT_TITLE = "Spark — Browser-native AI Coding & Office Workbench"


def _ensure_webview():
    try:
        import webview
    except ImportError:
        logger.error(
            '桌面模式缺少依赖：pip install "spark[desktop]"（或 pip install pywebview）'
        )
        return None
    # On Windows, verify that the WebView2 runtime is available.
    # Without it, webview.create_window silently fails or crashes.
    try:
        import webview.platforms.winforms as _winforms  # noqa: F401
        _ensure_webview2_runtime()
    except ImportError:
        pass  # non-Windows; skip runtime check
    return webview


def _ensure_webview2_runtime():
    """On Windows, verify Microsoft Edge WebView2 Runtime is installed.

    The runtime is pre-installed on Windows 11 and most Windows 10 systems,
    but may be missing on stripped-down or enterprise images.  Attempt a
    quick user-data-dir probe; log a friendly warning if creation fails so
    the user knows to install the runtime rather than getting a cryptic crash.
    """
    import tempfile
    from pathlib import Path

    probe_dir = Path(tempfile.mkdtemp(prefix="spark-wv2-probe-"))
    try:
        import webview

        probe = webview.create_window(
            "probe",
            html="<html><body>probe</body></html>",
            width=1,
            height=1,
            x=-32000,
            y=-32000,
            hidden=True,
        )
        # If we got here, the runtime loads.  Destroy the probe immediately.
        try:
            probe.destroy()
        except Exception:
            pass
    except Exception as exc:
        logger.warning(
            "Edge WebView2 Runtime 不可用，桌面模式可能无法启动。请下载安装："
            "https://developer.microsoft.com/en-us/microsoft-edge/webview2/ "
            "(%s: %s)",
            type(exc).__name__,
            exc,
        )
    finally:
        import shutil

        shutil.rmtree(probe_dir, ignore_errors=True)


def run_desktop(
    *,
    workdir: Path,
    cfg,
    store,
    port: int | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    title: str = DEFAULT_TITLE,
) -> int:
    """启动本机服务并打开原生窗口；窗口关闭后优雅停机。

    返回退出码：0 正常；2 缺少 pywebview；1 服务构建失败（端口占用等，
    由 build_web_server 抛 SystemExit 透传）。
    """
    webview = _ensure_webview()
    if webview is None:
        return 2

    from spark.web.server import build_web_server

    server, state = build_web_server(
        workdir=workdir, cfg=cfg, store=store, host="127.0.0.1", port=port
    )
    url = f"http://127.0.0.1:{server.server_address[1]}"
    logger.info("Spark desktop at %s session=%s", url, state.session_id)

    thread = threading.Thread(
        target=server.serve_forever, name="spark-web", daemon=True
    )
    thread.start()

    webview.create_window(title, url, width=width, height=height)
    try:
        webview.start()
    except Exception as exc:
        # pywebview imports fine without a GUI backend; the failure only
        # surfaces at start(). Give Linux users the real fix instead of a
        # raw "You must have either QT or GTK" stack trace.
        message = str(exc)
        if "QT or GTK" in message or "Main window failed to start" in message:
            logger.error(
                "桌面模式缺少 GUI 后端。Linux 请安装："
                'pip install "spark[desktop]"（会带 pywebview[qt]），'
                "以及系统库 libxcb-cursor0；无桌面环境请改用 spark web。"
            )
            return 2
        raise
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        store.close()
    return 0
