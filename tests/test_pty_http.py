"""P1-5 PTY HTTP/SSE 端点 e2e：真实起 web server，走 open/write/stream/close 全链路。"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

if sys.platform == "win32":
    pytest.importorskip("winpty")
else:
    pytest.importorskip("ptyprocess")

from spark.config import SparkConfig  # noqa: E402
from spark.store import SessionStore  # noqa: E402
from spark.web.server import build_web_server  # noqa: E402


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.base_url = "http://localhost:9999/v1"
    cfg.provider.model = "mock-model"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "spark.db")
    port = _free_port()
    server, state = build_web_server(
        workdir=tmp_path, cfg=cfg, store=store, host="127.0.0.1", port=port
    )
    t = threading.Thread(target=server.serve_forever, daemon=True, name="test-pty-http")
    t.start()
    time.sleep(0.3)
    try:
        yield port, tmp_path
    finally:
        server.shutdown()
        server.server_close()
        store.close()


def _post(port: int, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_pty_http_open_write_stream_close(server):
    port, workdir = server
    info = _post(port, "/api/pty/open", {"tab_id": "t1"})
    assert info["alive"] is True
    assert info["tab_id"] == "t1"
    # 默认 cwd = workdir
    assert Path(info["cwd"]) == workdir.resolve()

    # SSE 读流：后台线程逐行读
    frames: queue.Queue[str] = queue.Queue()
    stream_done = threading.Event()

    def reader() -> None:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/pty/stream?tab_id=t1", timeout=10
            ) as resp:
                for line in resp:
                    if stream_done.is_set():
                        break
                    frames.put(line.decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            pass

    rt = threading.Thread(target=reader, daemon=True)
    rt.start()

    # 等流建立（pty_open 帧）
    deadline = time.monotonic() + 3.0
    saw_open = False
    while time.monotonic() < deadline:
        try:
            line = frames.get(timeout=0.3)
            if "pty_open" in line:
                saw_open = True
                break
        except queue.Empty:
            pass
    assert saw_open, "SSE 未收到 pty_open 帧"

    # 写命令，等回显出现在流里
    assert _post(port, "/api/pty/write", {"tab_id": "t1", "data": "echo HTTP_PTY_OK\n"})["ok"]
    got_text = ""
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            line = frames.get(timeout=0.3)
        except queue.Empty:
            continue
        if line.startswith("data: "):
            try:
                payload = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            if payload.get("type") == "pty_data":
                got_text += payload.get("data", "")
                if "HTTP_PTY_OK" in got_text:
                    break

    assert "HTTP_PTY_OK" in got_text, f"SSE 流未回显命令输出: {got_text!r}"

    # resize + close
    assert _post(port, "/api/pty/resize", {"tab_id": "t1", "rows": 40, "cols": 120})["ok"]
    assert _post(port, "/api/pty/close", {"tab_id": "t1"})["ok"] is True
    # 关闭后 write 是 no-op
    assert _post(port, "/api/pty/write", {"tab_id": "t1", "data": "ls\n"})["ok"] is False
    stream_done.set()
