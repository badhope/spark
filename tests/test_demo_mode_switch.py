from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
import urllib.request
from pathlib import Path

from spark.config import SparkConfig
from spark.store import SessionStore
from spark.web.server import build_web_server


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def _server(tmp_path: Path, cfg: SparkConfig):
    store = SessionStore(tmp_path / "demo.db")
    port = _free_port()
    server, state = build_web_server(
        workdir=tmp_path, cfg=cfg, store=store, host="127.0.0.1", port=port
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        with contextlib.suppress(OSError):
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        time.sleep(0.1)
    try:
        yield base, state
    finally:
        server.shutdown()
        server.server_close()
        state.close()
        store.close()


def _post(url: str, payload: dict) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def test_demo_toggle_preserves_and_restores_real_config(tmp_path: Path) -> None:
    cfg = SparkConfig()
    cfg.provider.name = "openai_compat"
    cfg.provider.base_url = "https://example.invalid/v1"
    cfg.provider.model = "real-model"
    cfg.provider.api_key = "sk-test-secret"
    cfg.provider.api_key_env = ""
    with _server(tmp_path, cfg) as (base, state):
        code, data = _post(f"{base}/api/demo", {"demo": True})
        assert code == 200 and data["demo_mode"] is True
        assert state.cfg.provider.name == "mock"
        status = state.status()
        assert status["demo_mode"] is True
        assert status["model_status"]["ready"] is True
        assert "脚本" in status["demo_reason"]

        code, data = _post(f"{base}/api/demo", {"demo": False})
        assert code == 200 and data["demo_mode"] is False
        assert state.cfg.provider.name == "openai_compat"
        assert state.cfg.provider.model == "real-model"
        assert state.cfg.provider.api_key == "sk-test-secret"
        assert state.status()["demo_mode"] is False


def test_exit_demo_without_saved_real_config_reports_error(tmp_path: Path) -> None:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock"
    with _server(tmp_path, cfg) as (base, _state):
        code, data = _post(f"{base}/api/demo", {"demo": False})
        assert code == 409
        assert "真实模型" in data["error"]


def test_demo_requires_explicit_boolean(tmp_path: Path) -> None:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    with _server(tmp_path, cfg) as (base, _state):
        code, data = _post(f"{base}/api/demo", {})
        assert code == 400
        assert "true or false" in data["error"]
