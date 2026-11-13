from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from spark.config import McpServerConfig, McpTransportConfig, SparkConfig, load_config
from spark.proc import BoundedResult
from spark.store import SessionStore
from spark.web.server import build_web_server


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def web(tmp_path: Path, monkeypatch):
    import spark.web.server as server_module

    monkeypatch.setattr(server_module, "save_config", lambda cfg: None)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock-model"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "web-security.db")
    port = _free_port()
    server, state = build_web_server(
        workdir=tmp_path, cfg=cfg, store=store, host="127.0.0.1", port=port
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            _request(base, "/api/status")
            break
        except (urllib.error.URLError, OSError):
            time.sleep(0.05)
    else:
        server.shutdown()
        server.server_close()
        store.close()
        pytest.fail("server did not start")
    try:
        yield base, state, tmp_path
    finally:
        server.shutdown()
        server.server_close()
        store.close()


_MISSING = object()


def _request(
    base: str,
    path: str,
    body: object = _MISSING,
    *,
    headers: dict[str, str] | None = None,
    method: str | None = None,
) -> tuple[int, dict]:
    data = (
        None
        if method == "GET" or body is _MISSING
        else json.dumps(body).encode("utf-8")
    )
    request_headers = dict(headers or {})
    if data is not None:
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(
        base + path,
        data=data,
        headers=request_headers,
        method=method or ("POST" if data is not None else "GET"),
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_loopback_browser_origin_and_host_are_bound_to_service_port(web) -> None:
    base, _, _ = web
    port = urlparse(base).port
    assert port is not None
    origin = f"http://127.0.0.1:{port}"

    status, body = _request(base, "/api/cancel", {}, headers={"Origin": origin})
    assert status == 200, body

    status, body = _request(
        base,
        "/api/cancel",
        {},
        headers={"Origin": f"http://127.0.0.1:{port + 1}"},
    )
    assert status == 403
    assert "origin" in body["error"]

    status, body = _request(
        base,
        "/api/cancel",
        {},
        headers={"Host": f"127.0.0.1:{port + 1}"},
    )
    assert status == 403
    assert "host" in body["error"]

    status, body = _request(base, "/api/cancel", {})
    assert status == 200, body

    status, body = _request(
        base,
        "/api/cancel",
        {},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert status == 403
    assert "origin" in body["error"]


def test_post_json_requires_top_level_object(web) -> None:
    base, _, _ = web
    for payload in (None, [], "text", 42, True):
        status, body = _request(base, "/api/cancel", payload)
        assert status == 400, (payload, body)
        assert "object" in body["error"]


def test_remote_token_api_remains_compatible(tmp_path: Path, monkeypatch) -> None:
    import spark.web.server as server_module

    monkeypatch.setattr(server_module, "save_config", lambda cfg: None)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "remote.db")
    port = _free_port()
    server, state = build_web_server(
        workdir=tmp_path, cfg=cfg, store=store, host="0.0.0.0", port=port
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        status, body = _request(
            base,
            "/api/status",
            headers={
                "Host": f"192.0.2.1:{port}",
                "X-Spark-Token": state.control_token,
            },
        )
        assert status == 200, body
    finally:
        server.shutdown()
        server.server_close()
        store.close()


def test_agents_md_read_is_bounded(web) -> None:
    from spark.web.utils import MAX_AGENTS_MD_BYTES

    base, _, workdir = web
    (workdir / "AGENTS.md").write_bytes(b"x" * (MAX_AGENTS_MD_BYTES + 1))
    status, body = _request(base, "/api/agents_md", method="GET")
    assert status == 413
    assert body["max_bytes"] == MAX_AGENTS_MD_BYTES
    assert "content" not in body


def test_config_redacts_mcp_http_secrets_and_preserves_them_on_save(web) -> None:
    base, state, _ = web
    secret = "Bearer mcp-secret-value"
    api_key = "mcp-api-key-value"
    state.cfg.mcp_servers = [
        McpServerConfig(
            name="remote",
            transport="streamable_http",
            http=McpTransportConfig(
                url="https://mcp.example.invalid/mcp",
                headers={
                    "Authorization": secret,
                    "X-API-Key": api_key,
                    "X-Trace": "trace-id",
                },
            ),
        )
    ]
    status, config = _request(base, "/api/config", method="GET")
    assert status == 200
    headers = config["mcp_servers"][0]["http"]["headers"]
    assert headers["Authorization"] != secret
    assert headers["X-API-Key"] != api_key
    assert headers["X-Trace"] == "trace-id"
    assert secret not in json.dumps(config)
    assert api_key not in json.dumps(config)

    state.prepare_mcp = lambda servers: None
    status, body = _request(base, "/api/settings", {"mcp_servers": config["mcp_servers"]})
    assert status == 200, body
    saved_headers = state.cfg.mcp_servers[0].http.headers
    assert saved_headers["Authorization"] == secret
    assert saved_headers["X-API-Key"] == api_key

    changed = json.loads(json.dumps(config["mcp_servers"]))
    changed[0]["http"]["url"] = "https://new-mcp.example.invalid/mcp"
    status, body = _request(base, "/api/settings", {"mcp_servers": changed})
    assert status == 200, body
    assert "Authorization" not in state.cfg.mcp_servers[0].http.headers


@pytest.mark.parametrize("include_mcp,save_failure", [(False, False), (True, False), (True, True)])
def test_unrelated_settings_reuse_existing_bridge_with_redacted_headers(web, monkeypatch, include_mcp, save_failure) -> None:
    base, state, _ = web
    state.cfg.mcp_servers = [McpServerConfig(
        name="remote", transport="streamable_http",
        http=McpTransportConfig(url="https://mcp.example.invalid/mcp", headers={"Authorization": "secret-value"}),
    )]
    class ExistingBridge:
        errors = ["existing warning"]
        startup_errors = []
        servers = {"remote": object()}
        closed = False
        def register_into(self, registry):
            self.registry = registry
        def set_sampling_handler(self, provider):
            self.provider = provider
        async def call(self, name, args):
            return "available" if not self.closed else "closed"
        async def close(self):
            self.closed = True
    bridge = ExistingBridge()
    state.mcp_bridge = bridge
    bridge.register_into(state.loop.registry)
    bridge.set_sampling_handler(state.loop.provider)
    state.loop.registry.ctx.mcp_bridge = bridge
    state.loop.registry.ctx.mcp_call = bridge.call
    old_loop = state.loop
    old_effort = state.cfg.agent.effort
    _, config = _request(base, "/api/config")
    body = {"agent": {"effort": "low"}}
    if include_mcp:
        body["mcp_servers"] = config["mcp_servers"]
    def fail_prepare(servers):
        raise RuntimeError("single-instance server is already in use")
    monkeypatch.setattr(state, "prepare_mcp", fail_prepare)
    if save_failure:
        import spark.web.server as server_module
        def fail_save(cfg):
            raise OSError("save failed")
        monkeypatch.setattr(server_module, "save_config", fail_save)
    status, result = _request(base, "/api/settings", body)
    assert status == (500 if save_failure else 200), result
    assert state.cfg.agent.effort == (old_effort if save_failure else "low")
    if save_failure:
        assert state.loop is old_loop
    assert state.cfg.mcp_servers[0].http.headers["Authorization"] == "secret-value"
    assert state.mcp_bridge is bridge
    assert state.loop.registry.ctx.mcp_bridge is bridge
    assert bridge.registry is state.loop.registry
    assert bridge.provider is state.loop.provider
    assert asyncio.run(bridge.call("x", {})) == "available"
    if not save_failure:
        assert result["mcp_errors"] == ["existing warning"]


def test_mcp_env_http_roundtrip_redacts_preserves_edits_and_deletes(web, monkeypatch) -> None:
    base, state, _ = web
    state.cfg.mcp_servers = [McpServerConfig(name="srv", command="fake", env={"TOKEN": "secret-value", "MODE": "private-mode"})]
    candidates = []
    class HealthyBridge:
        errors = []
        startup_errors = []
        servers = {"srv": object()}
        def register_into(self, registry):
            pass
        def set_sampling_handler(self, provider):
            pass
        async def call(self, name, args):
            return "available"
        async def close(self):
            pass
    state.mcp_bridge = HealthyBridge()
    monkeypatch.setattr(state, "prepare_mcp", lambda servers: candidates.append(servers) or None)
    status, config = _request(base, "/api/config")
    assert status == 200
    assert "secret-value" not in json.dumps(config)
    assert "private-mode" not in json.dumps(config)
    row = config["mcp_servers"][0]
    assert set(row["env"]) == {"TOKEN", "MODE"}
    assert all(value and value not in {"secret-value", "private-mode"} for value in row["env"].values())
    status, _ = _request(base, "/api/settings", {"mcp_servers": [row], "agent": {"effort": "low"}})
    assert status == 200
    assert state.cfg.mcp_servers[0].env == {"TOKEN": "secret-value", "MODE": "private-mode"}
    assert candidates == []
    changed_target = json.loads(json.dumps(row))
    changed_target["command"] = "other-executable"
    status, _ = _request(base, "/api/settings", {"mcp_servers": [changed_target]})
    assert status == 400
    assert candidates == []
    assert state.cfg.mcp_servers[0].env == {"TOKEN": "secret-value", "MODE": "private-mode"}
    row["env"]["TOKEN"] = "replacement"
    del row["env"]["MODE"]
    row["env_replace"] = True
    status, _ = _request(base, "/api/settings", {"mcp_servers": [row]})
    assert status == 200
    assert state.cfg.mcp_servers[0].env == {"TOKEN": "replacement"}
    assert len(candidates) == 1


@pytest.mark.parametrize("env", [None, {}])
def test_legacy_mcp_empty_env_preserves_existing_and_explicit_clear_works(web, monkeypatch, env) -> None:
    base, state, _ = web
    state.cfg.mcp_servers = [McpServerConfig(name="srv", command="fake", env={"TOKEN": "secret-value"})]
    monkeypatch.setattr(state, "prepare_mcp", lambda servers: None)
    row = {"name": "srv", "command": "fake"}
    if env is not None:
        row["env"] = env
    status, _ = _request(base, "/api/settings", {"mcp_servers": [row]})
    assert status == 200
    assert state.cfg.mcp_servers[0].env == {"TOKEN": "secret-value"}
    changed = dict(row, command="other-executable")
    status, _ = _request(base, "/api/settings", {"mcp_servers": [changed]})
    assert status == 400
    assert state.cfg.mcp_servers[0].env == {"TOKEN": "secret-value"}
    row["env"] = {}
    row["env_replace"] = True
    status, _ = _request(base, "/api/settings", {"mcp_servers": [row]})
    assert status == 200
    assert state.cfg.mcp_servers[0].env == {}


def test_unchanged_failed_mcp_can_retry_without_restarting_healthy_bridge(web, monkeypatch) -> None:
    base, state, _ = web
    state.cfg.mcp_servers = [McpServerConfig(name="srv", command="fake")]
    class Bridge:
        def __init__(self, failed):
            self.startup_errors = ["failed"] if failed else []
            self.errors = list(self.startup_errors)
            self.servers = {} if failed else {"srv": object()}
            self.closed = False
        def register_into(self, registry):
            self.registry = registry
        def set_sampling_handler(self, provider):
            self.provider = provider
        async def call(self, name, args):
            return "available"
        async def close(self):
            self.closed = True
    old = Bridge(True)
    new = Bridge(False)
    state.mcp_bridge = old
    prepared = []
    def fail_retry(servers):
        raise OSError("still unavailable")
    monkeypatch.setattr(state, "prepare_mcp", fail_retry)
    _, config = _request(base, "/api/config")
    status, _ = _request(base, "/api/settings", {"mcp_servers": config["mcp_servers"]})
    assert status == 500
    assert state.mcp_bridge is old and not old.closed
    monkeypatch.setattr(state, "prepare_mcp", lambda servers: prepared.append(servers) or new)
    status, result = _request(base, "/api/settings", {"mcp_servers": config["mcp_servers"]})
    assert status == 200, result
    assert state.mcp_bridge is new and old.closed and len(prepared) == 1
    status, result = _request(base, "/api/settings", {"mcp_servers": config["mcp_servers"]})
    assert status == 200, result
    assert state.mcp_bridge is new and not new.closed and len(prepared) == 1
    new.startup_errors = ["another server failed"]
    new.errors = list(new.startup_errors)
    status, result = _request(base, "/api/settings", {"mcp_servers": config["mcp_servers"]})
    assert status == 200, result
    assert state.mcp_bridge is new and not new.closed and len(prepared) == 1
    assert result["mcp_errors"] == ["another server failed"]


def test_settings_validation_does_not_apply_provider_before_invalid_hooks(web) -> None:
    base, state, _ = web
    before_model = state.cfg.provider.model
    before_hooks = list(state.cfg.hooks)
    status, body = _request(
        base,
        "/api/settings",
        {
            "provider": {"model": "must-not-apply"},
            "hooks": [{"event": "not-a-hook", "command": "echo x"}],
        },
    )
    assert status == 400
    assert "hook" in body["error"]
    assert state.cfg.provider.model == before_model
    assert state.cfg.hooks == before_hooks


def test_fs_read_reuses_configured_protected_paths(web) -> None:
    base, state, workdir = web
    target = workdir / "secret.txt"
    target.write_text("do not expose", encoding="utf-8")
    state.cfg.agent.protected_paths = ["secret.txt"]
    state.sandbox.protected_paths = ["secret.txt"]
    status, body = _request(base, "/api/fs/read?path=secret.txt", method="GET")
    assert status == 200
    assert body.get("error")
    assert "do not expose" not in json.dumps(body)


def test_test_endpoint_limits_concurrent_probes(web, monkeypatch) -> None:
    import spark.web._handlers_settings as settings_module
    from spark.providers.probe import ProbeResult

    base, _, _ = web
    active = 0
    peak = 0
    lock = threading.Lock()

    async def fake_probe(**kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.35)
        with lock:
            active -= 1
        return ProbeResult(
            ok=True,
            model=kwargs["model"],
            base_url=kwargs["base_url"],
            content="ok",
            stream_content="ok",
            rounds=1,
        )

    monkeypatch.setattr(settings_module, "probe_provider", fake_probe)

    def request_probe() -> int:
        status, _ = _request(
            base,
            "/api/test",
            {"base_url": "https://probe.example.invalid/v1", "model": "m", "api_key": "k"},
        )
        return status

    with ThreadPoolExecutor(max_workers=3) as pool:
        statuses = list(pool.map(lambda _: request_probe(), range(3)))
    assert statuses.count(429) == 1
    assert statuses.count(200) == 2
    assert peak <= 2


def test_settings_preserves_environment_key(web, monkeypatch) -> None:
    base, state, _ = web
    state.cfg.provider.name = "openai_compat"
    state.cfg.provider.api_key = None
    monkeypatch.setenv(state.cfg.provider.api_key_env, "environment-key")
    status, body = _request(base, "/api/settings", {"provider": {"model": "new-model"}})
    assert status == 200, body
    assert state.cfg.provider.api_key is None
    assert state.loop.provider is not None
    from spark.config import require_api_key
    assert require_api_key(state.cfg) == "environment-key"


def test_settings_rejects_cross_endpoint_implicit_credentials(web, monkeypatch) -> None:
    base, state, _ = web
    state.cfg.provider.name = "openai_compat"
    state.cfg.provider.base_url = "https://old.example.invalid/v1"
    state.cfg.provider.api_key = "old-key"
    state.cfg.provider.api_keys = ["old-pool-key"]
    monkeypatch.setenv(state.cfg.provider.api_key_env, "old-env-key")
    status, _ = _request(base, "/api/settings", {"provider": {"base_url": "https://new.example.invalid/v1"}})
    assert status == 400
    assert state.cfg.provider.base_url == "https://old.example.invalid/v1"


def test_settings_rebuild_failure_keeps_memory_persistence_and_mcp(web, monkeypatch) -> None:
    import spark.web.server as server_module
    from spark.errors import ConfigError

    base, state, _ = web
    saved = []
    monkeypatch.setattr(server_module, "save_config", lambda cfg: saved.append(cfg.model_dump()))
    before = state.cfg.model_dump()
    old_loop = state.loop
    old_sandbox = state.sandbox
    boot_calls = []
    monkeypatch.setattr(state, "boot_mcp", lambda: boot_calls.append(True) or [])
    def fail_rebuild(**kwargs):
        raise ConfigError("injected rebuild failure")
    monkeypatch.setattr(state, "rebuild_loop", fail_rebuild)
    status, _ = _request(base, "/api/settings", {"provider": {"model": "changed"}, "mcp_servers": []})
    assert status == 400
    assert state.cfg.model_dump() == before
    assert state.loop is old_loop
    assert state.sandbox is old_sandbox
    assert saved == []
    assert boot_calls == []


def test_settings_save_failure_rolls_back_runtime_and_does_not_restart_mcp(web, monkeypatch) -> None:
    import spark.web.server as server_module

    base, state, _ = web
    before = state.cfg.model_dump()
    old_loop, old_sandbox = state.loop, state.sandbox
    boot_calls = []
    monkeypatch.setattr(state, "boot_mcp", lambda: boot_calls.append(True) or [])
    def fail_save(cfg):
        raise OSError("injected save failure")
    monkeypatch.setattr(server_module, "save_config", fail_save)
    status, _ = _request(base, "/api/settings", {"provider": {"model": "changed"}, "mcp_servers": []})
    assert status == 500
    assert state.cfg.model_dump() == before
    assert state.loop is old_loop
    assert state.sandbox is old_sandbox
    assert boot_calls == []


@pytest.mark.parametrize("failure", ["start", "reported", "register", "save"])
def test_mcp_settings_failure_preserves_existing_bridge_and_config(web, monkeypatch, failure) -> None:
    import spark.web.server as server_module
    import spark.web.state as state_module
    from spark.config import save_config

    base, state, workdir = web
    path = workdir / "settings.toml"
    save_config(state.cfg, path)
    before_disk = path.read_bytes()
    before = state.cfg.model_dump()
    old_loop = state.loop

    class Bridge:
        def __init__(self):
            self.errors = []
            self.startup_errors = []
            self.closed = False
            self.registry = None
            self.handler = None
            instances.append(self)

        async def start(self, configs, registry):
            if failure == "start":
                raise RuntimeError("start failed")
            if failure == "reported":
                self.errors.append("unavailable")
                self.startup_errors.append("unavailable")

        async def close(self):
            self.closed = True

        async def call(self, name, arguments):
            return "available" if not self.closed else "closed"

        def register_into(self, registry):
            if self is not old_bridge and failure == "register":
                raise RuntimeError("registration failed")
            self.registry = registry

        def set_sampling_handler(self, handler):
            self.handler = handler

    instances = []
    old_bridge = Bridge()
    state.mcp_bridge = old_bridge
    old_bridge.register_into(old_loop.registry)
    old_bridge.set_sampling_handler(old_loop.provider)
    monkeypatch.setattr(state_module, "McpBridge", Bridge)
    def persist(cfg):
        if failure == "save":
            raise OSError("save failed")
        save_config(cfg, path)
    monkeypatch.setattr(server_module, "save_config", persist)
    status, _ = _request(base, "/api/settings", {
        "provider": {"model": "changed"},
        "mcp_servers": [{"name": "new", "command": "fake"}],
    })
    assert status >= 400
    assert path.read_bytes() == before_disk
    assert state.cfg.model_dump() == before
    assert state.loop is old_loop
    assert state.mcp_bridge is old_bridge
    assert old_bridge.registry is old_loop.registry
    assert old_bridge.handler is old_loop.provider
    assert asyncio.run(old_bridge.call("x", {})) == "available"
    assert len(instances) == 2 and instances[1].closed


def test_concurrent_settings_validate_against_latest_committed_values(web, monkeypatch) -> None:
    import spark.web.server as server_module

    base, state, _ = web
    entered = threading.Event()
    release = threading.Event()
    saved = []
    def persist(cfg):
        saved.append(cfg.model_dump())
        if len(saved) == 1:
            entered.set()
            assert release.wait(10)
            raise OSError("first save failed")
    monkeypatch.setattr(server_module, "save_config", persist)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_request, base, "/api/settings", {"provider": {"model": "first"}})
        assert entered.wait(10)
        second = pool.submit(_request, base, "/api/settings", {"agent": {"effort": "low"}})
        time.sleep(0.2)
        release.set()
        assert first.result()[0] == 500
        assert second.result()[0] == 200
    assert state.cfg.provider.model == "mock-model"
    assert state.cfg.agent.effort == "low"
    assert saved[-1]["provider"]["model"] == "mock-model"


@pytest.mark.parametrize("operation", ["save", "delete", "activate", "prompt", "knowledge", "default"])
def test_cross_endpoint_writes_wait_for_failed_settings(web, monkeypatch, operation) -> None:
    import spark.config as config_module
    import spark.web.server as server_module
    import spark.web.state as state_module
    from spark.config import ModelProfile

    base, state, workdir = web
    if operation != "default":
        state.cfg.model_profiles = [ModelProfile(id="profile", name="p", provider="mock", base_url="http://localhost", model="profile-model")]
    entered, release = threading.Event(), threading.Event()
    writes = []
    def persist(cfg):
        snapshot = cfg.model_dump()
        writes.append(snapshot)
        if len(writes) == 1:
            entered.set()
            assert release.wait(10)
            raise OSError("injected failure")
    monkeypatch.setattr(server_module, "save_config", persist)
    monkeypatch.setattr(state_module, "save_config", persist)
    monkeypatch.setattr(config_module, "ensure_home", lambda: workdir)
    requests = {
        "save": ("/api/models/save", {"provider": "mock", "base_url": "http://localhost", "model": "new"}),
        "delete": ("/api/models/delete", {"id": "profile"}),
        "activate": ("/api/models/activate", {"id": "profile"}),
        "prompt": ("/api/prompts", {"action": "save_system", "content": "new prompt"}),
        "knowledge": ("/api/knowledge", {"action": "settings", "top_k": 9}),
        "default": ("/api/config", _MISSING),
    }
    path, body = requests[operation]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_request, base, "/api/settings", {"provider": {"model": "uncommitted"}})
        assert entered.wait(10)
        second = pool.submit(_request, base, path, body)
        try:
            time.sleep(0.4)
            assert len(writes) == 1
        finally:
            release.set()
        assert first.result(timeout=10)[0] == 500
        assert second.result(timeout=10)[0] == 200
    assert len(writes) == 2
    assert writes[-1]["provider"]["model"] == ("profile-model" if operation == "activate" else "mock-model")
    assert state.cfg.provider.model == writes[-1]["provider"]["model"]
    if operation == "default":
        assert state.cfg.model_profiles[0].model == "mock-model"


def test_settings_waits_for_profile_activation_and_uses_updated_provider(web, monkeypatch) -> None:
    import spark.web.server as server_module
    from spark.config import ModelProfile

    base, state, _ = web
    state.cfg.model_profiles = [ModelProfile(id="profile", name="p", provider="mock", base_url="http://localhost", model="profile-model")]
    entered, release = threading.Event(), threading.Event()
    writes = []
    def persist(cfg):
        writes.append(cfg.model_dump())
        if len(writes) == 1:
            entered.set()
            assert release.wait(10)
    monkeypatch.setattr(server_module, "save_config", persist)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_request, base, "/api/models/activate", {"id": "profile"})
        assert entered.wait(10)
        second = pool.submit(_request, base, "/api/settings", {"agent": {"effort": "low"}})
        try:
            time.sleep(0.4)
            assert len(writes) == 1
        finally:
            release.set()
        assert first.result(timeout=10)[0] == 200
        assert second.result(timeout=10)[0] == 200
    assert writes[-1]["provider"]["model"] == "profile-model"
    assert state.cfg.provider.model == "profile-model"
    assert state.cfg.agent.effort == "low"


@pytest.mark.parametrize("path,body", [
    ("/api/settings", {"agent": {"effort": "low"}}),
    ("/api/models/activate", {"id": "profile"}),
])
def test_config_writers_do_not_block_config_reads_while_turn_is_active(web, path, body) -> None:
    from spark.config import ModelProfile

    base, state, _ = web
    state.cfg.model_profiles = [ModelProfile(id="profile", name="p", provider="mock", base_url="http://localhost", model="profile-model")]
    state.turn_lock.acquire()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            update = pool.submit(_request, base, path, body)
            time.sleep(0.25)
            reader = pool.submit(_request, base, "/api/config")
            try:
                assert update.result(timeout=2)[0] == 409
                assert reader.result(timeout=2)[0] == 200
            finally:
                state.turn_lock.release()
    finally:
        if state.turn_lock.locked():
            state.turn_lock.release()


def test_mcp_allowed_tools_filter_is_nonfatal(web, monkeypatch) -> None:
    import spark.tools.mcp_bridge as bridge_module
    from spark.asyncutil import run_coroutine_sync

    base, state, _ = web
    class Connection:
        def __init__(self, cfg):
            self.cfg = cfg
            self.tools = [{"name": "allowed"}, {"name": "blocked"}]
        async def start(self, **kwargs):
            pass
        async def close(self):
            pass
    monkeypatch.setattr(bridge_module, "_McpConnection", Connection)
    try:
        status, body = _request(base, "/api/settings", {"mcp_servers": [{"name": "srv", "command": "fake", "allowed_tools": ["allowed"]}]})
        assert status == 200, body
        assert any("blocked" in error for error in body["mcp_errors"])
        assert [tool["name"] for tool in state.mcp_bridge.servers["srv"].tools] == ["allowed"]
    finally:
        if state.mcp_bridge is not None:
            run_coroutine_sync(state.mcp_bridge.close())
            state.mcp_bridge = None


def test_mcp_connection_failure_with_filter_warning_rolls_back(web, monkeypatch) -> None:
    import spark.tools.mcp_bridge as bridge_module

    base, state, _ = web
    before, loop = state.cfg.model_dump(), state.loop
    closed = []
    class Connection:
        def __init__(self, cfg):
            self.cfg = cfg
            self.tools = [{"name": "blocked"}]
        async def start(self, **kwargs):
            if self.cfg.name == "bad":
                raise OSError("injected connection failure")
        async def close(self):
            closed.append(self.cfg.name)
    monkeypatch.setattr(bridge_module, "_McpConnection", Connection)
    status, _ = _request(base, "/api/settings", {"mcp_servers": [
        {"name": "ok", "command": "fake", "allowed_tools": ["allowed"]},
        {"name": "bad", "command": "fake"},
    ]})
    assert status == 500
    assert state.cfg.model_dump() == before
    assert state.loop is loop
    assert state.mcp_bridge is None
    assert sorted(closed) == ["bad", "ok"]


def test_mcp_settings_success_installs_candidate_before_closing_old(web, monkeypatch) -> None:
    import spark.web.server as server_module
    import spark.web.state as state_module
    from spark.config import load_config, save_config

    base, state, workdir = web
    path = workdir / "settings.toml"
    instances = []
    class Bridge:
        def __init__(self):
            self.errors = []
            self.startup_errors = []
            self.closed = False
            instances.append(self)
        async def start(self, configs, registry):
            pass
        async def close(self):
            if self is old_bridge:
                assert state.mcp_bridge is instances[1]
                assert state.loop.registry.ctx.mcp_bridge is instances[1]
            self.closed = True
        async def call(self, name, arguments):
            return "available" if not self.closed else "closed"
        def register_into(self, registry):
            self.registry = registry
        def set_sampling_handler(self, handler):
            self.handler = handler

    old_bridge = Bridge()
    state.mcp_bridge = old_bridge
    old_bridge.register_into(state.loop.registry)
    monkeypatch.setattr(state_module, "McpBridge", Bridge)
    monkeypatch.setattr(server_module, "save_config", lambda cfg: save_config(cfg, path))
    status, body = _request(base, "/api/settings", {
        "provider": {"model": "changed"},
        "mcp_servers": [{"name": "new", "command": "fake"}],
    })
    assert status == 200, body
    assert old_bridge.closed
    assert state.mcp_bridge is instances[1]
    assert not instances[1].closed
    assert state.loop.registry.ctx.mcp_bridge is instances[1]
    assert load_config(config_path=path, workdir=workdir).mcp_servers[0].name == "new"


def test_stream_queued_before_settings_change_does_not_run_stale_loop() -> None:
    from spark.web._handlers_chat import _dispatch_turn

    events = []
    stream = SimpleNamespace(disconnected=False, submit_payload=events.append)
    state = SimpleNamespace(turn_lock=threading.Lock(), loop=object(), busy=False)
    old_loop = SimpleNamespace(registry=SimpleNamespace(disabled_tools=set()))
    _dispatch_turn(state, stream, old_loop, "prompt", None, None, lambda event: None, True)
    assert events == [{"type": "turn_error", "text": "settings changed; retry the request"}]
    assert not state.busy


def test_real_vite_proxy_enforces_browser_origin(web) -> None:
    base, _, _ = web
    root = Path(__file__).resolve().parents[1]
    vite = root / "web" / "node_modules" / "vite" / "bin" / "vite.js"
    node = shutil.which("node")
    if not node or not vite.is_file():
        pytest.skip("Node and installed Vite are required")
    port = _free_port()
    proxy = f"http://127.0.0.1:{port}"
    env = dict(os.environ, SPARK_DEV_BACKEND=base)
    process = subprocess.Popen(
        [node, str(vite), "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
        cwd=root / "web", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            if process.poll() is not None:
                pytest.fail("Vite exited before becoming ready")
            try:
                status, _ = _request(proxy, "/api/status")
                break
            except (urllib.error.URLError, OSError):
                if time.monotonic() >= deadline:
                    pytest.fail("Vite did not become ready")
                time.sleep(0.1)
        assert status == 200
        status, _ = _request(proxy, "/api/cancel", {}, headers={"Origin": proxy})
        assert status == 200
        for headers in ({"Origin": "http://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            request = urllib.request.Request(proxy + "/api/cancel", data=b"{}", headers=headers)
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=20)
            assert error.value.code == 403
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def test_wildcard_loopback_forged_origin_requires_token(tmp_path: Path, monkeypatch) -> None:
    import spark.web.server as server_module

    monkeypatch.setattr(server_module, "save_config", lambda cfg: None)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "wildcard.db")
    port = _free_port()
    server, state = build_web_server(workdir=tmp_path, cfg=cfg, store=store, host="0.0.0.0", port=port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        status, _ = _request(base, "/api/status", headers={"Origin": base})
        assert status == 401
        status, _ = _request(base, "/api/status", headers={"Origin": base, "X-Spark-Token": state.control_token})
        assert status == 200
    finally:
        server.shutdown()
        server.server_close()
        store.close()


def test_harden_permissions_falls_back_when_icacls_fails(tmp_path: Path, monkeypatch) -> None:
    import spark.config as config_module

    target = tmp_path / "config.toml"
    target.write_text("x", encoding="utf-8")
    chmod_calls: list[tuple[Path, int]] = []
    monkeypatch.setattr(config_module.sys, "platform", "win32")
    monkeypatch.setattr(config_module, "_win_user_sid", lambda: "S-1-5-21-test")

    def fake_run(*args, **kwargs):
        return BoundedResult(args=[], returncode=1, stdout="", stderr="icacls failed")

    monkeypatch.setattr(config_module, "run_bounded", fake_run)
    monkeypatch.setattr(
        config_module.os,
        "chmod",
        lambda path, mode: chmod_calls.append((path, mode)),
    )
    config_module._harden_permissions(target)
    assert chmod_calls == [(target, 0o600)]


def test_win_acl_restricted_rejects_nonzero_icacls(tmp_path: Path, monkeypatch) -> None:
    import spark.config as config_module

    monkeypatch.setattr(config_module.sys, "platform", "win32")
    monkeypatch.setattr(
        config_module,
        "run_bounded",
        lambda *args, **kwargs: BoundedResult(
            args=[],
            returncode=1,
            stdout="config.toml BUILTIN\\Users:(F)",
            stderr="icacls failed",
        ),
    )
    assert config_module.win_acl_restricted(tmp_path / "config.toml") is False


# ---------------------------------------------------------------------------
# Provider credential resolution: env binding must not be a trap door
# ---------------------------------------------------------------------------


def test_switching_to_ollama_is_allowed_while_an_env_key_exists(web, monkeypatch) -> None:
    """ollama/mock 不需要凭据，环境里恰好有 key 不能变成 400 的借口。"""
    base, state, _ = web
    state.cfg.provider.name = "openai_compat"
    state.cfg.provider.base_url = "https://old.example.invalid/v1"
    state.cfg.provider.api_key = "old-key"
    monkeypatch.setenv(state.cfg.provider.api_key_env, "environment-key")
    status, body = _request(
        base,
        "/api/settings",
        {"provider": {"name": "ollama", "base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5-coder"}},
    )
    assert status == 200, body
    assert state.cfg.provider.name == "ollama"
    assert state.cfg.provider.api_key is None


def test_cross_endpoint_switch_accepts_explicit_environment_binding(web, monkeypatch) -> None:
    """显式绑定 api_key_env 是合法的凭据来源，且只绑定变量名、不落盘明文。"""
    import spark.web.server as server_module
    from spark.config import save_config

    base, state, tmp_path = web
    path = tmp_path / "env-binding.toml"
    monkeypatch.setattr(server_module, "save_config", lambda cfg: save_config(cfg, path))
    state.cfg.provider.name = "openai_compat"
    state.cfg.provider.base_url = "https://old.example.invalid/v1"
    state.cfg.provider.api_key = "old-key"
    monkeypatch.setenv("NEW_ENDPOINT_KEY", "new-endpoint-secret")
    status, body = _request(
        base,
        "/api/settings",
        {
            "provider": {
                "base_url": "https://new.example.invalid/v1",
                "api_key_env": "NEW_ENDPOINT_KEY",
            }
        },
    )
    assert status == 200, body
    assert state.cfg.provider.api_key_env == "NEW_ENDPOINT_KEY"
    assert state.cfg.provider.api_key is None, "environment value must never be persisted"
    loaded = load_config(config_path=path, workdir=tmp_path)
    assert loaded.provider.api_key_env == "NEW_ENDPOINT_KEY"
    assert loaded.provider.api_key in (None, "")


def test_cross_endpoint_switch_clears_stale_environment_binding(web, monkeypatch) -> None:
    """换端点不带凭据时，旧端点的变量绑定必须清掉，否则会把旧凭据发给新主机。"""
    import spark.web.server as server_module
    from spark.config import save_config

    base, state, tmp_path = web
    path = tmp_path / "stale-binding.toml"
    monkeypatch.setattr(server_module, "save_config", lambda cfg: save_config(cfg, path))
    state.cfg.provider.name = "openai_compat"
    state.cfg.provider.base_url = "https://old.example.invalid/v1"
    state.cfg.provider.api_key_env = "OLD_ENDPOINT_KEY"
    state.cfg.provider.api_key = None
    monkeypatch.setenv("OLD_ENDPOINT_KEY", "old-endpoint-secret")
    status, body = _request(
        base, "/api/settings", {"provider": {"base_url": "https://new.example.invalid/v1", "api_key": "fresh-key"}}
    )
    assert status == 200, body
    assert state.cfg.provider.api_key_env == ""
    loaded = load_config(config_path=path, workdir=tmp_path)
    assert loaded.provider.api_key_env == ""
    assert loaded.provider.api_key == "fresh-key"
