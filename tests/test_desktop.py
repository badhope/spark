"""桌面壳测试：build_web_server 构建、缺 pywebview 时的退出码。"""
from __future__ import annotations

import sys
from pathlib import Path

from spark.config import SparkConfig
from spark.desktop import run_desktop
from spark.store import SessionStore
from spark.web.server import build_web_server


def test_build_web_server_binds_ephemeral_port(tmp_path: Path) -> None:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    store = SessionStore(tmp_path / "desktop.db")
    try:
        server, state = build_web_server(
            workdir=tmp_path, cfg=cfg, store=store, host="127.0.0.1", port=0
        )
        port = server.server_address[1]
        assert isinstance(port, int) and port > 0
        assert state.session_id
    finally:
        server.server_close()
        store.close()


def test_run_desktop_without_pywebview_returns_2(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setitem(sys.modules, "webview", None)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    store = SessionStore(tmp_path / "desktop2.db")
    try:
        code = run_desktop(workdir=tmp_path, cfg=cfg, store=store, port=0)
    finally:
        store.close()
    assert code == 2



def test_run_desktop_without_gui_backend_returns_2(
    tmp_path: Path, monkeypatch
) -> None:
    """pywebview imports without Qt/GTK; start() must map to exit code 2."""
    import types

    fake = types.ModuleType("webview")
    fake.create_window = lambda *args, **kwargs: object()

    def _fail_start() -> None:
        raise RuntimeError(
            "You must have either QT or GTK with Python extensions installed "
            "in order to use pywebview."
        )

    fake.start = _fail_start
    monkeypatch.setitem(sys.modules, "webview", fake)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    store = SessionStore(tmp_path / "desktop3.db")
    try:
        code = run_desktop(workdir=tmp_path, cfg=cfg, store=store, port=0)
    finally:
        store.close()
    assert code == 2
