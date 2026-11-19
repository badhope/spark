from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
import urllib.error
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
def _remote_server(tmp_path: Path):
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock-model"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "remote-scope.db")
    port = _free_port()
    server, state = build_web_server(
        workdir=tmp_path, cfg=cfg, store=store, host="0.0.0.0", port=port
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            with urllib.request.urlopen(base + "/api/status", timeout=1):
                break
        except (urllib.error.URLError, OSError):
            time.sleep(0.05)
    try:
        yield base, state
    finally:
        server.shutdown()
        server.server_close()
        state.close()
        store.close()


def _request(base: str, path: str, *, token: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"X-Spark-Token": token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_device_token_is_read_and_run_only(tmp_path: Path) -> None:
    with _remote_server(tmp_path) as (base, state):
        code = state.pairing.new_code()["code"]
        device = state.pairing.claim(code, "phone")["token"]

        # Read paths used by the mobile UI remain available.
        assert _request(base, "/api/config", token=device)[0] == 200
        assert _request(base, "/api/sessions", token=device)[0] == 200
        assert _request(base, "/api/queue", token=device, body={"action": "list"})[0] == 200

        # Administrative and direct-execution writes are not.
        assert _request(base, "/api/settings", token=device, body={})[0] == 403
        assert _request(base, "/api/shell", token=device, body={})[0] == 403
        assert _request(base, "/api/plugins", token=device, body={})[0] == 403
        assert _request(base, "/api/debug", token=device)[0] == 403


def test_device_token_cannot_use_control_token_channels(tmp_path: Path) -> None:
    with _remote_server(tmp_path) as (base, state):
        code = state.pairing.new_code()["code"]
        device = state.pairing.claim(code, "phone")["token"]
        # Pairing management is local-only; a remote device must not mint codes.
        status, _ = _request(base, "/api/pair/code", token=device, body={})
        assert status in {403, 404}
        status, _ = _request(base, "/api/pair/devices", token=device)
        assert status in {403, 404}
