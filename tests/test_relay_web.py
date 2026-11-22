"""RC-02: a relay root serves the real Spark Web, its API and SSE."""
from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import httpx

from spark.config import SparkConfig
from spark.relay import RelayAgent, build_relay_server
from spark.store import SessionStore
from spark.web.server import build_web_server


def _wait_status(base: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(base + "/api/status", timeout=1.0)
        except httpx.HTTPError:
            time.sleep(0.05)
            continue
        if response.status_code == 200:
            return
        time.sleep(0.05)
    raise AssertionError("Spark Web did not start")


def test_relay_root_serves_real_spark_web(tmp_path: Path) -> None:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "relay-web.db")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        web_port = int(probe.getsockname()[1])
    web, state = build_web_server(
        workdir=tmp_path,
        cfg=cfg,
        store=store,
        host="127.0.0.1",
        port=web_port,
        public_url="http://relay.example.test",
    )
    web_thread = threading.Thread(target=web.serve_forever, daemon=True)
    web_thread.start()
    upstream = f"http://127.0.0.1:{web_port}"
    _wait_status(upstream)

    assert state.remote_access.ensure_base("public")["base_url"] == "http://relay.example.test"
    code = state.pairing.new_code()

    channel = "relay-web-channel"
    key = "r" * 24
    relay, hub = build_relay_server(
        "127.0.0.1",
        0,
        web_channel=channel,
        web_key=key,
    )
    relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
    relay_thread.start()
    agent = RelayAgent(
        f"http://127.0.0.1:{relay.server_address[1]}",
        channel,
        key,
        upstream,
        poll_wait=1,
    )
    stop = threading.Event()
    agent_thread = threading.Thread(target=agent.run, args=(stop,), daemon=True)
    agent_thread.start()
    base = f"http://127.0.0.1:{relay.server_address[1]}"
    try:
        assert hub.wait_for_agent(channel, timeout=3)
        with httpx.Client(timeout=20.0) as client:
            page = client.get(base + "/")
            assert page.status_code == 200
            assert "<html" in page.text.lower()

            claimed = client.post(
                base + "/api/pair/claim",
                json={"code": code["code"], "name": "phone"},
            )
            assert claimed.status_code == 200, claimed.text
            token = claimed.json()["token"]

            status = client.get(base + "/api/status", headers={"X-Spark-Token": token})
            assert status.status_code == 200, status.text
            assert "session_id" in json.loads(status.text)

            with client.stream(
                "POST",
                base + "/api/chat/stream",
                headers={
                    "X-Spark-Token": token,
                    "Content-Type": "application/json",
                },
                json={"prompt": "hello"},
            ) as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                first = next(response.iter_raw())
                assert first.startswith(b"id: 1") or first.startswith(b"data: ")
    finally:
        stop.set()
        agent.close()
        agent_thread.join(timeout=3)
        relay.shutdown()
        relay.server_close()
        web.shutdown()
        web.server_close()
        web_thread.join(timeout=3)
        state.close()
        store.close()
