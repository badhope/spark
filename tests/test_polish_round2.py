"""Round-2 polish: real message ids, rename/fork, web-search gate, demo mode, self-heal.

Each test pins a user-visible behaviour that used to be missing or fake:

* history rows carry the database id so the UI can delete/edit a single message;
* sessions can be renamed and forked from the UI;
* turning the composer's web-search switch off removes the web tools for that
  turn (and only that turn);
* a provider that silently fell back to the scripted mock is reported as demo
  mode instead of looking like a real model;
* a corrupted global config is backed up and the app still starts;
* settings round-trip keeps repo-map fields;
* diagnostics/repair endpoints exist and repairs are real.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from spark.config import SparkConfig, load_config, save_config
from spark.providers.mock import MockProvider
from spark.store import SessionStore
from spark.web.server import serve_web


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _post(base: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


@pytest.fixture()
def live(tmp_path: Path, monkeypatch):
    import spark.web.server as server_module

    monkeypatch.setattr(server_module, "save_config", lambda cfg: None)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock-model"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "web.db")
    port = _free_port()
    thread = threading.Thread(
        target=serve_web,
        kwargs={
            "workdir": tmp_path,
            "cfg": cfg,
            "store": store,
            "host": "127.0.0.1",
            "port": port,
        },
        daemon=True,
    )
    thread.start()
    for _ in range(100):
        try:
            _get(f"http://127.0.0.1:{port}", "/api/status")
            break
        except (urllib.error.URLError, OSError):
            time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}", store
    finally:
        store.close()


# ---------------------------------------------------------------------------
# History ids + message level operations
# ---------------------------------------------------------------------------


def test_history_rows_carry_database_ids(live) -> None:
    base, store = live
    _post(base, "/api/chat", {"prompt": "hello"})
    body = _get(base, "/api/history")
    assert body["messages"], "history should not be empty"
    ids = [row.get("id") for row in body["messages"]]
    assert all(isinstance(i, int) and i > 0 for i in ids), ids
    assert body["messages"][0]["content"] == "hello"
    stored = store.load_message_ids(_get(base, "/api/status")["session_id"])
    assert ids == stored


def test_truncate_from_message_endpoint_uses_real_id(live) -> None:
    base, store = live
    sid = _get(base, "/api/status")["session_id"]
    from spark.models import ChatMessage

    first = store.append_message(sid, ChatMessage(role="user", content="one"))
    store.append_message(sid, ChatMessage(role="assistant", content="two"))
    body = _post(base, "/api/messages/truncate", {"message_id": first})
    assert body["ok"] is True
    assert body["messages"] == []


# ---------------------------------------------------------------------------
# Rename / fork
# ---------------------------------------------------------------------------


def test_session_rename_persists(live) -> None:
    base, store = live
    sid = _get(base, "/api/status")["session_id"]
    out = _post(base, "/api/sessions/rename", {"session_id": sid, "title": "重构支付模块"})
    assert out["ok"] is True
    assert store.get_session(sid)["title"] == "重构支付模块"
    listed = _get(base, "/api/sessions")["sessions"]
    assert any(row["id"] == sid and row["title"] == "重构支付模块" for row in listed)


def test_session_rename_rejects_blank_and_unknown(live) -> None:
    base, _ = live
    with pytest.raises(urllib.error.HTTPError) as blank:
        _post(base, "/api/sessions/rename", {"session_id": "nope", "title": "  "})
    assert blank.value.code == 400


def test_session_fork_copies_messages_and_switches(live) -> None:
    base, store = live
    sid = _get(base, "/api/status")["session_id"]
    from spark.models import ChatMessage

    store.append_message(sid, ChatMessage(role="user", content="fork me"))
    out = _post(base, "/api/sessions/fork", {"session_id": sid})
    new_sid = out["status"]["session_id"]
    assert new_sid != sid
    assert out["messages"][0]["content"] == "fork me"
    assert store.get_session(new_sid)["forked_from"] == sid
    assert len(store.list_sessions()) == 2


# ---------------------------------------------------------------------------
# Web-search gate
# ---------------------------------------------------------------------------


def test_registry_disabled_tools_are_hidden_and_refused(tmp_path: Path) -> None:
    from spark.tools.registry import ToolContext, ToolRegistry

    cfg = SparkConfig()
    reg = ToolRegistry(ToolContext(sandbox=__import__("spark.sandbox", fromlist=["WorkdirSandbox"]).WorkdirSandbox(tmp_path), config=cfg))
    names = {s["function"]["name"] for s in reg.schemas()}
    assert "web_search" in names
    reg.disabled_tools = {"web_search", "web_fetch"}
    names = {s["function"]["name"] for s in reg.schemas()}
    assert "web_search" not in names and "web_fetch" not in names
    result = __import__("asyncio").run(reg.execute(__import__("spark.models", fromlist=["ToolCall"]).ToolCall(id="c1", name="web_search", arguments={"query": "x"})))
    assert result.ok is False
    assert "disabled" in str(result.payload.get("error", "")).lower()


# ---------------------------------------------------------------------------
# Demo mode
# ---------------------------------------------------------------------------


def test_status_reports_demo_mode_when_key_missing(tmp_path: Path) -> None:
    from spark.web.state import SparkWebState

    cfg = SparkConfig()
    cfg.provider.name = "openai_compat"
    cfg.provider.api_key = None
    cfg.provider.api_key_env = "SPARK_MISSING_KEY_FOR_TEST"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "demo.db")
    state = SparkWebState(tmp_path, cfg, store)
    try:
        status = state.status()
        assert status["demo_mode"] is True
        assert "MockProvider" in status["demo_reason"] or "key" in status["demo_reason"].lower()
    finally:
        store.close()


def test_status_not_demo_for_real_provider(tmp_path: Path) -> None:
    from spark.web.state import SparkWebState

    cfg = SparkConfig()
    cfg.provider.name = "ollama"
    cfg.provider.base_url = "http://127.0.0.1:11434/v1"
    cfg.provider.model = "qwen2.5-coder"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "demo2.db")
    state = SparkWebState(tmp_path, cfg, store)
    try:
        status = state.status()
        assert status["demo_mode"] is False
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Config self-heal + settings round-trip
# ---------------------------------------------------------------------------


def test_broken_global_config_is_backed_up_and_recovered(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    broken = home / "config.toml"
    broken.write_text("[provider\nname = ", encoding="utf-8")
    monkeypatch.setenv("SPARK_HOME", str(home))
    cfg = load_config(workdir=tmp_path)
    assert cfg.provider.name == "openai_compat"
    assert cfg.recovery_notes, "a recovery note must explain what happened"
    backups = list(home.glob("config.toml.broken-*"))
    assert backups, "the broken file must be preserved"
    assert backups[0].read_text(encoding="utf-8").startswith("[provider")


def test_repo_map_fields_survive_save_and_load(tmp_path: Path) -> None:
    cfg = SparkConfig()
    cfg.context.repo_map_enabled = False
    cfg.context.repo_map_max_chars = 1234
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    text = path.read_text(encoding="utf-8")
    assert "repo_map_enabled" in text and "repo_map_max_chars" in text
    again = load_config(config_path=path, workdir=tmp_path)
    assert again.context.repo_map_enabled is False
    assert again.context.repo_map_max_chars == 1234


# ---------------------------------------------------------------------------
# Diagnostics / repair
# ---------------------------------------------------------------------------


def test_diagnostics_endpoint_lists_health(live) -> None:
    base, _ = live
    body = _get(base, "/api/diagnostics")
    assert body["ok"] is True
    assert body["rows"], "health rows should never be empty"
    assert {"level", "message", "fix"} <= set(body["rows"][0])


def test_repair_recreates_missing_workdir(tmp_path: Path, monkeypatch) -> None:
    from spark.selfheal import repair

    monkeypatch.setenv("SPARK_HOME", str(tmp_path / "home"))
    missing = tmp_path / "does" / "not" / "exist"
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    report = repair(cfg, workdir=missing, config_path=tmp_path / "home" / "config.toml")
    assert missing.is_dir()
    assert any(item["applied"] for item in report["actions"])
    assert (tmp_path / "home").is_dir()


def test_repair_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    from spark.selfheal import repair

    monkeypatch.setenv("SPARK_HOME", str(tmp_path / "home"))
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    first = repair(cfg, workdir=tmp_path, config_path=tmp_path / "home" / "config.toml")
    second = repair(cfg, workdir=tmp_path, config_path=tmp_path / "home" / "config.toml")
    assert first["ok"] and second["ok"]
    applied_second = [a for a in second["actions"] if a["applied"]]
    assert applied_second == [], "second repair must not redo work"


# ---------------------------------------------------------------------------
# Mock provider stays explicit
# ---------------------------------------------------------------------------


def test_factory_falls_back_to_mock_and_status_says_so(tmp_path: Path) -> None:
    """The fallback must be a real MockProvider *and* be reported as demo mode.

    Asserting the class name of a MockProvider we just constructed proved
    nothing; this drives the actual factory path that degrades.
    """
    from spark.providers.factory import create_provider

    cfg = SparkConfig()
    cfg.provider.name = "openai_compat"
    cfg.provider.base_url = "https://example.invalid/v1"
    cfg.provider.model = "m"
    cfg.provider.api_key = None
    cfg.provider.api_key_env = "SPARK_DEFINITELY_MISSING_KEY"
    provider = create_provider(cfg)
    assert type(provider).__name__ == "MockProvider"

    store = SessionStore(tmp_path / "demo.db")
    from spark.web.state import SparkWebState

    cfg.agent.approval = "full-auto"
    state = SparkWebState(tmp_path, cfg, store)
    try:
        status = state.status()
        assert status["demo_mode"] is True
        assert "API Key" in status["demo_reason"] or "Mock" in status["demo_reason"]
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Regressions found by the independent UI audit
# ---------------------------------------------------------------------------


def test_settings_switch_to_suggest_creates_approver(tmp_path: Path) -> None:
    """full-auto at startup → switch to suggest must install a real approver.

    Otherwise the loop keeps approver=None and every write/shell call is silently
    denied while the UI claims it will ask.
    """
    from spark.web.state import SparkWebState

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock-model"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "approver.db")
    state = SparkWebState(tmp_path, cfg, store)
    try:
        assert state.approver is None
        cfg.agent.approval = "suggest"
        state.rebuild_loop(session_id=state.session_id)
        approver = state.loop.approver
        assert approver is state.approver
        # A real approver must hold a turn until answered, then release it with
        # the decision the user sent — not merely be a non-None placeholder.
        import asyncio

        from spark.models import ApprovalRequest, ToolCall

        request = ApprovalRequest(
            tool_call=ToolCall(id="c1", name="write_file", arguments={"path": "a", "content": "b"}),
            summary="write a",
        )

        async def run() -> object:
            task = asyncio.ensure_future(approver(request))
            for _ in range(50):
                if approver.pending:
                    break
                await asyncio.sleep(0.02)
            assert approver.pending, "approver never became pending"
            approver.respond("allow")
            return await asyncio.wait_for(task, timeout=5)

        decision = asyncio.run(run())
        assert decision.action == "allow"
        assert decision.tool_call_id == "c1"
    finally:
        store.close()


def test_mcp_env_round_trips_through_config(tmp_path: Path) -> None:
    from spark.config import McpServerConfig, load_config

    cfg = SparkConfig()
    cfg.mcp_servers = [
        McpServerConfig(
            name="gh",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github"],
            env={"GITHUB_TOKEN": "t0ken"},
        )
    ]
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    again = load_config(config_path=path, workdir=tmp_path)
    assert again.mcp_servers[0].env == {"GITHUB_TOKEN": "t0ken"}


def test_test_endpoint_requires_key_for_new_host(live) -> None:
    base, _ = live
    with pytest.raises(urllib.error.HTTPError) as err:
        _post(base, "/api/test", {"base_url": "https://example.invalid/v1", "model": "m"})
    assert err.value.code == 400
    payload = json.loads(err.value.read().decode("utf-8"))
    assert payload.get("needs_api_key") is True


def test_sessions_list_cap_is_generous(live) -> None:
    base, store = live
    from spark.models import ChatMessage

    status = _get(base, "/api/status")
    sid = status["session_id"]
    workdir = Path(status["workdir"])
    for i in range(60):
        new = store.create_session(workdir, "mock-model", title=f"s{i}")
        store.append_message(new, ChatMessage(role="user", content="x"))
    rows = _get(base, "/api/sessions")["sessions"]
    assert len(rows) >= 60, "session list must not silently truncate at 50"
    assert any(r["id"] == sid for r in rows)


# ---------------------------------------------------------------------------
# Round 3: sub-agent graph events, session export
# ---------------------------------------------------------------------------


def test_subtask_events_are_emitted_for_parallel_fanout(tmp_path: Path) -> None:
    import asyncio

    from spark.core.loop import AgentLoop
    from spark.models import ChatDelta, ToolCall
    from spark.sandbox import WorkdirSandbox
    from spark.tools.registry import ToolContext, ToolRegistry

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "m"
    cfg.agent.approval = "full-auto"
    cfg.agent.max_tool_rounds = 3
    store = SessionStore(tmp_path / "sub.db")
    sid = store.create_session(tmp_path, "m")
    reg = ToolRegistry(ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg))
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="t1",
                        name="task",
                        arguments={"tasks": [{"prompt": "a"}, {"prompt": "b"}]},
                    ),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="done"), ChatDelta(type="end")],
        ]
    )
    loop = AgentLoop(
        workdir=tmp_path, cfg=cfg, provider=provider, registry=reg, store=store, session_id=sid
    )

    async def collect() -> list:
        return [e async for e in loop.iter_turn("go")]

    events = asyncio.run(collect())
    starts = [e for e in events if e.type == "subtask_start"]
    ends = [e for e in events if e.type == "subtask_end"]
    # The execution graph needs both edges of the life cycle, per child.
    assert len(starts) == 2 and len(ends) == 2
    assert {e.data["index"] for e in starts} == {0, 1}
    assert all(e.data["total"] == 2 for e in starts)
    store.close()


def test_session_export_markdown_has_metadata_and_tools(live) -> None:
    base, _ = live
    _post(base, "/api/chat", {"prompt": "list files"})
    body = _post(base, "/api/sessions/export", {"format": "markdown"})
    assert body["ok"] is True
    md = body["content"]
    assert md.startswith("# ")
    assert "工作目录" in md and "模型" in md
    assert "## 用户" in md and "list files" in md
    assert "list_dir" in md


def test_session_export_json_roundtrip(live) -> None:
    import json as _json

    base, _ = live
    _post(base, "/api/chat", {"prompt": "hello"})
    body = _post(base, "/api/sessions/export", {"format": "json"})
    payload = _json.loads(body["content"])
    assert payload["session_id"]
    assert any(m["role"] == "user" and m["content"] == "hello" for m in payload["messages"])


def test_session_export_rejects_unknown_format(live) -> None:
    base, _ = live
    with pytest.raises(urllib.error.HTTPError) as err:
        _post(base, "/api/sessions/export", {"format": "pdf"})
    assert err.value.code == 400


def test_subtask_start_carries_agent_name_and_prompt(tmp_path: Path) -> None:
    """子会话导航：UI 要能区分"并行跑的到底是哪几个专家"。

    subtask_start 事件必须带声明式子代理名（agent=...）和任务描述（prompt），
    否则三行轨迹都只写着 #1/#2/#3，无法对应。
    """
    import asyncio

    from spark.core.loop import AgentLoop
    from spark.models import ChatDelta, ToolCall
    from spark.sandbox import WorkdirSandbox
    from spark.tools.registry import ToolContext, ToolRegistry

    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "reader.md").write_text(
        "---\nname: reader\ndescription: reads code\nwhen_to_use: reading\ntools: read_file\n---\n"
        "You are a code reader.\n",
        encoding="utf-8",
    )
    (agents_dir / "tester.md").write_text(
        "---\nname: tester\ndescription: runs tests\nwhen_to_use: testing\ntools: run_shell\n---\n"
        "You are a test runner.\n",
        encoding="utf-8",
    )

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "m"
    cfg.agent.approval = "full-auto"
    cfg.agent.max_tool_rounds = 3
    store = SessionStore(tmp_path / "sub2.db")
    sid = store.create_session(tmp_path, "m")
    reg = ToolRegistry(ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg))
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="t1",
                        name="task",
                        arguments={
                            "tasks": [
                                {"prompt": "inspect module", "agent": "reader"},
                                {"prompt": "run the suite", "agent": "tester"},
                            ]
                        },
                    ),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="done"), ChatDelta(type="end")],
        ]
    )
    loop = AgentLoop(
        workdir=tmp_path, cfg=cfg, provider=provider, registry=reg, store=store, session_id=sid
    )

    async def collect() -> list:
        return [e async for e in loop.iter_turn("go")]

    events = asyncio.run(collect())
    starts = [e for e in events if e.type == "subtask_start"]
    assert len(starts) == 2
    by_index = {e.data["index"]: e.data for e in starts}
    assert by_index[0]["agent"] == "reader"
    assert by_index[1]["agent"] == "tester"
    assert by_index[0]["prompt"] == "inspect module"
    assert by_index[1]["prompt"] == "run the suite"
    # 无声明的普通并行 fanout 不携带 agent 字段（前端按缺省处理）。
    store.close()


def test_subtask_tool_events_are_tagged_with_their_child(tmp_path: Path) -> None:
    """A child's tools must carry its index, or the graph draws them as siblings.

    Runs the real collector against a real child AgentLoop whose provider emits a
    glob tool call — no fake queue writer, so deleting the tagging in
    ``spawn_subtasks_parallel`` makes this fail.
    """
    import asyncio

    from spark.core.loop import AgentLoop
    from spark.models import ChatDelta, ToolCall
    from spark.sandbox import WorkdirSandbox
    from spark.tools.registry import ToolContext, ToolRegistry

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "m"
    cfg.agent.approval = "full-auto"
    cfg.agent.max_tool_rounds = 2
    store = SessionStore(tmp_path / "tag.db")
    sid = store.create_session(tmp_path, "m")
    reg = ToolRegistry(ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg))
    scripted = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(id="x1", name="glob", arguments={"pattern": "*.py"}),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="child done"), ChatDelta(type="end")],
        ]
    )
    loop = AgentLoop(
        workdir=tmp_path, cfg=cfg, provider=scripted, registry=reg, store=store, session_id=sid
    )

    async def collect():
        return [
            e
            async for e in loop.spawn_subtasks_parallel(
                ["a", "b"], system_extras=["", ""], tools_allowlists=[None, None]
            )
        ]

    events = asyncio.run(collect())
    tool_events = [e for e in events if e.type in {"tool_start", "tool_end"}]
    # Both children share one scripted provider, so exactly one of them gets the
    # tool-call round; what matters is that every tool event it produces carries
    # an index (without the tagging these would be plain, `data=None`).
    assert tool_events, [e.type for e in events]
    assert all(e.data and "subtask_index" in e.data for e in tool_events)
    assert {e.data["subtask_index"] for e in tool_events} <= {0, 1}
    store.close()


def test_history_keeps_assistant_tool_calls_and_ids(live) -> None:
    """Reloading a session must rebuild tool cards with their arguments.

    Regression: `_history_messages` dropped assistant rows that only carried
    tool_calls (no text), so the UI rendered every tool as `{}`.
    """
    import json as _json

    base, store = live
    _post(base, "/api/chat", {"prompt": "list files"})
    body = _get(base, "/api/history")
    calls = [m for m in body["messages"] if m.get("tool_calls")]
    assert calls, "assistant tool-call rows must survive the history projection"
    call = calls[0]["tool_calls"][0]
    assert call["name"] and isinstance(call["arguments"], dict) and call["arguments"]
    # The tool result references the same call id so the UI can pair them.
    tool_rows = [m for m in body["messages"] if m.get("role") == "tool"]
    assert any(m.get("tool_call_id") for m in tool_rows)
    _json.dumps(body)  # must be JSON-serialisable


def test_security_headers_and_no_version_leak() -> None:
    """Audit F-004: CSP/Permissions-Policy present, Server header hides Python."""
    import threading
    import urllib.request

    from spark.web.server import build_web_server

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "m"
    cfg.agent.approval = "full-auto"
    store = SessionStore(_tmpdir := __import__("tempfile").mkdtemp() + "/h.db")
    server, _ = build_web_server(
        workdir=Path(_tmpdir), cfg=cfg, store=store, host="127.0.0.1", port=0
    )
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as r:
            headers = {k.lower(): v for k, v in r.headers.items()}
        assert "content-security-policy" in headers
        assert "default-src 'self'" in headers["content-security-policy"]
        assert "permissions-policy" in headers
        assert "python" not in headers.get("server", "").lower()
        assert "3.1" not in headers.get("server", "")
    finally:
        server.shutdown()
        server.server_close()
        store.close()


def test_ask_user_emits_question_and_returns_answer(tmp_path: Path) -> None:
    """Audit F-010: the agent can ask the user and receive the answer."""
    import asyncio

    from spark.core.loop import AgentLoop
    from spark.models import ChatDelta, ToolCall
    from spark.providers.mock import MockProvider
    from spark.sandbox import WorkdirSandbox
    from spark.tools.registry import ToolContext, ToolRegistry

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "m"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "ask.db")
    sid = store.create_session(tmp_path, "m")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg)
    seen: list[tuple[str, list[str]]] = []

    async def asker(question: str, options: list[str]) -> str:
        seen.append((question, options))
        return "pnpm"

    ctx.question_asker = asker
    reg = ToolRegistry(ctx)
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="q1",
                        name="ask_user",
                        arguments={"question": "用哪个包管理器？", "options": ["pnpm", "npm"]},
                    ),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="好的"), ChatDelta(type="end")],
        ]
    )
    loop = AgentLoop(
        workdir=tmp_path, cfg=cfg, provider=provider, registry=reg, store=store, session_id=sid
    )

    async def collect():
        return [e async for e in loop.iter_turn("go")]

    events = asyncio.run(collect())
    questions = [e for e in events if e.type == "question"]
    assert len(questions) == 1
    assert questions[0].data["question"] == "用哪个包管理器？"
    assert questions[0].data["options"] == ["pnpm", "npm"]
    assert seen == [("用哪个包管理器？", ["pnpm", "npm"])]
    ends = [e for e in events if e.type == "tool_end"]
    assert ends and ends[0].result.ok
    assert ends[0].result.payload["answer"] == "pnpm"
    store.close()


def test_ask_user_without_channel_fails_gracefully(tmp_path: Path) -> None:
    """CLI/ACP surfaces have no asker: the tool must explain, not hang."""
    import asyncio

    from spark.models import ToolCall
    from spark.sandbox import WorkdirSandbox
    from spark.tools.registry import ToolContext, ToolRegistry

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg)
    reg = ToolRegistry(ctx)
    result = asyncio.run(
        reg.execute(ToolCall(id="q", name="ask_user", arguments={"question": "hi"}))
    )
    assert result.ok is False
    assert "no interactive channel" in result.payload["error"]


# ---------------------------------------------------------------------------
# Structured audit trail (audit F-014)
# ---------------------------------------------------------------------------


def test_audit_is_off_by_default(monkeypatch) -> None:
    from spark import audit

    monkeypatch.delenv("SPARK_AUDIT_LOG", raising=False)
    assert audit.audit_path() is None
    audit.record("tool_denied", tool="run_shell")  # must not raise or write
    assert audit.read_recent() == []


def test_audit_writes_redacted_jsonl(tmp_path: Path, monkeypatch) -> None:
    import json as _json

    from spark import audit

    target = tmp_path / "audit.jsonl"
    monkeypatch.setenv("SPARK_AUDIT_LOG", str(target))
    audit.record(
        "tool_denied",
        tool="run_shell",
        reason="blocked",
        arguments={"command": "curl -H 'Authorization: Bearer sk-secret1234567890abcdef'"},
    )
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = _json.loads(lines[0])
    assert entry["kind"] == "tool_denied"
    assert entry["tool"] == "run_shell"
    assert "sk-secret1234567890abcdef" not in lines[0]
    assert "REDACTED" in lines[0]


def test_audit_unknown_kind_is_ignored(tmp_path: Path, monkeypatch) -> None:
    from spark import audit

    target = tmp_path / "a.jsonl"
    monkeypatch.setenv("SPARK_AUDIT_LOG", str(target))
    audit.record("not_a_real_kind", foo="bar")
    assert not target.exists()


def test_audit_read_recent_returns_newest(tmp_path: Path, monkeypatch) -> None:
    from spark import audit

    target = tmp_path / "b.jsonl"
    monkeypatch.setenv("SPARK_AUDIT_LOG", str(target))
    for i in range(5):
        audit.record("sandbox_blocked", path=f"/{i}", matched="x")
    recent = audit.read_recent(limit=2)
    assert len(recent) == 2
    assert recent[-1]["path"] == "/4"


def test_denied_tool_call_lands_in_audit(tmp_path: Path, monkeypatch) -> None:
    """End-to-end: a policy-denied tool must produce an audit entry."""
    import asyncio

    from spark.core.loop import AgentLoop
    from spark.models import ChatDelta, ToolCall
    from spark.providers.mock import MockProvider
    from spark.sandbox import WorkdirSandbox
    from spark.tools.registry import ToolContext, ToolRegistry

    target = tmp_path / "e2e-audit.jsonl"
    monkeypatch.setenv("SPARK_AUDIT_LOG", str(target))
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "m"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "aud.db")
    sid = store.create_session(tmp_path, "m")
    reg = ToolRegistry(ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg))
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="d1", name="write_file", arguments={"path": "/etc/passwd", "content": "x"}
                    ),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="ok"), ChatDelta(type="end")],
        ]
    )
    loop = AgentLoop(
        workdir=tmp_path, cfg=cfg, provider=provider, registry=reg, store=store, session_id=sid
    )

    async def run():
        return [e async for e in loop.iter_turn("go")]

    asyncio.run(run())
    store.close()
    text = target.read_text(encoding="utf-8")
    assert "tool_denied" in text or "sandbox_blocked" in text
    assert "write_file" in text


def test_tool_only_turns_have_a_timeline_marker(tmp_path: Path) -> None:
    """A turn that only calls tools must not render as a blank timeline row."""
    from spark.store import SessionStore

    store = SessionStore(tmp_path / "tl.db")
    try:
        sid = store.create_session(tmp_path, "m")
        from spark.models import ChatMessage, ToolCall

        store.append_message(
            sid,
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "a.py"})],
            ),
        )
        briefs = store.list_message_briefs(sid)
        assert briefs and briefs[0]["preview"].strip()
        assert "read_file" in briefs[0]["preview"]
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Round 7: share HTML export, session import, inline shell (!cmd)
# ---------------------------------------------------------------------------


def test_session_export_html_is_self_contained(live) -> None:
    base, _ = live
    _post(base, "/api/chat", {"prompt": "<script>alert(1)</script> share me"})
    body = _post(base, "/api/sessions/export", {"format": "html"})
    assert body["ok"] is True and body["format"] == "html"
    html = body["content"]
    assert html.startswith("<!doctype html>")
    assert "share me" in html
    # 消息内容必须被转义：注入的 script 不能原样出现在正文里
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_session_import_roundtrip(live) -> None:
    import json as _json

    base, _ = live
    _post(base, "/api/chat", {"prompt": "import me please"})
    export = _post(base, "/api/sessions/export", {"format": "json"})
    payload = _json.loads(export["content"])
    imported = _post(base, "/api/sessions/import", {"content": payload})
    assert imported["ok"] is True
    assert imported["imported"] >= 1
    assert imported["session_id"] != payload["session_id"]
    texts = [m.get("content") for m in imported["messages"] if m.get("role") == "user"]
    assert any("import me please" in t for t in texts)


def test_session_import_accepts_json_string(live) -> None:
    import json as _json

    base, _ = live
    payload = {"title": "字符串导入", "messages": [{"role": "user", "content": "hi"}]}
    imported = _post(base, "/api/sessions/import", {"content": _json.dumps(payload)})
    assert imported["ok"] is True
    assert imported["imported"] == 1


def test_session_import_rejects_bad_payload(live) -> None:
    base, _ = live
    for bad in (
        {"content": "not json at all"},
        {"content": {"messages": []}},
        {"content": {"messages": [{"role": "tool", "content": "x"}]}},
        {},
    ):
        with pytest.raises(urllib.error.HTTPError) as err:
            _post(base, "/api/sessions/import", bad)
        assert err.value.code == 400


def test_inline_shell_runs_in_workdir(live) -> None:
    base, _ = live
    body = _post(base, "/api/shell", {"command": "echo spark-inline-ok"})
    assert body["result"]["exit_code"] == 0
    assert "spark-inline-ok" in body["result"]["stdout"]


def test_inline_shell_rejects_empty_and_errors_are_structured(live) -> None:
    base, _ = live
    with pytest.raises(urllib.error.HTTPError) as err:
        _post(base, "/api/shell", {"command": ""})
    assert err.value.code == 400
    body = _post(base, "/api/shell", {"command": "exit 3"})
    assert body["result"]["exit_code"] == 3
    assert body["ok"] is False


def test_share_html_does_not_leak_local_paths(live) -> None:
    """分享文件面向外发：不得带本机绝对路径与会话 id（真机 review 指出）。"""
    base, _ = live
    _post(base, "/api/chat", {"prompt": "hello share"})
    html = _post(base, "/api/sessions/export", {"format": "html"})["content"]
    status = _get(base, "/api/status")
    assert str(status["workdir"]) not in html
    assert str(status["session_id"]) not in html
    assert "hello share" in html
