"""治理面与双粒度回滚的回归测试。

覆盖三组此前只有设计稿、没有实现的能力：

1. 权限规则 / 审批档案的写接口（``docs/API.md`` 的「写接口设计」）；
2. 消息级 undo 连带文件还原（竞品矩阵 P0「按轮把文件+消息一并还原」）；
3. 声明式子代理的独立模型（``DeclaredAgent.model`` + ``task`` 透传）。

每条都断言**可证伪的行为**：写盘后的值、拒绝时的错误码、还原后的文件内容。
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

from spark.agents import get_declared_agent, inject_subagents, parse_agent_file
from spark.config import (
    PermissionRule,
    SparkConfig,
    load_config,
    save_config,
)
from spark.store import SessionStore
from spark.web.server import build_web_server


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(base: str, path: str, body: object | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(
        base + path, data=data, headers=headers, method="POST" if data is not None else "GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture()
def web(tmp_path: Path, monkeypatch):
    """真实 HTTP 服务 + 真实 save_config（写 tmp，绝不碰 ~/.spark）。"""
    import spark.web.server as server_module

    saved: list[Path] = []

    def spy(cfg: SparkConfig) -> Path:
        target = tmp_path / f"saved-{len(saved)}.toml"
        save_config(cfg, target)
        saved.append(target)
        return target

    monkeypatch.setattr(server_module, "save_config", spy)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock-model"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "governance.db")
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
        yield base, state, saved, tmp_path
    finally:
        server.shutdown()
        server.server_close()
        store.close()


# ---------------------------------------------------------------------------
# 1. 权限规则写接口
# ---------------------------------------------------------------------------


def test_permission_rules_round_trip_and_reload(web) -> None:
    base, state, saved, tmp_path = web
    status, body = _request(
        base,
        "/api/permissions/rules",
        {
            "scope": "user",
            "rules": [
                {"action": "deny", "resource": "**/.env", "tool": "read_file"},
                {"action": "allow", "resource": "src/**", "tool": ""},
            ],
        },
    )
    assert status == 200, body
    assert len(state.cfg.permission_rules) == 2
    assert state.cfg.permission_rules[0].action == "deny"

    loaded = load_config(config_path=saved[-1], workdir=tmp_path)
    assert [(r.action, r.resource, r.tool) for r in loaded.permission_rules] == [
        ("deny", "**/.env", "read_file"),
        ("allow", "src/**", ""),
    ]


def test_permission_rules_reject_non_user_scope(web) -> None:
    """project/local 层来自工作目录文件，API 不得替它们授权。"""
    base, state, saved, _ = web
    before = state.cfg.model_dump()
    status, body = _request(
        base, "/api/permissions/rules", {"scope": "project", "rules": []}
    )
    assert status == 400
    assert "not writable" in body["error"]
    assert state.cfg.model_dump() == before
    assert saved == []


def test_permission_rules_reject_malformed_entries(web) -> None:
    """非法规则必须报错，不能静默丢弃——用户会以为 deny 生效了。"""
    base, state, saved, _ = web
    for payload in (
        {"rules": [{"action": "block", "resource": "**"}]},
        {"rules": [{"action": "deny"}]},
        {"rules": "nope"},
        {"rules": ["nope"]},
    ):
        status, body = _request(base, "/api/permissions/rules", payload)
        assert status == 400, (payload, body)
    assert state.cfg.permission_rules == []
    assert saved == []


def test_permission_rule_delete_by_index(web) -> None:
    base, state, saved, tmp_path = web
    state.cfg.permission_rules = [
        PermissionRule(action="deny", resource="a/**"),
        PermissionRule(action="allow", resource="b/**"),
    ]
    status, body = _request(base, "/api/permissions/rules/delete", {"index": 0})
    assert status == 200, body
    assert [r.resource for r in state.cfg.permission_rules] == ["b/**"]
    loaded = load_config(config_path=saved[-1], workdir=tmp_path)
    assert [r.resource for r in loaded.permission_rules] == ["b/**"]

    status, _ = _request(base, "/api/permissions/rules/delete", {"index": 9})
    assert status == 404


def test_permission_rule_delete_rejects_negative_index(web) -> None:
    base, state, _saved, _tmp = web
    state.cfg.permission_rules = [PermissionRule(action="deny", resource="a/**")]
    status, _ = _request(base, "/api/permissions/rules/delete", {"index": -1})
    assert status == 400
    assert len(state.cfg.permission_rules) == 1


# ---------------------------------------------------------------------------
# 2. 审批档案写接口
# ---------------------------------------------------------------------------


def test_approval_profile_create_activate_delete(web) -> None:
    base, state, saved, tmp_path = web
    status, body = _request(
        base,
        "/api/approval_profiles/save",
        {
            "name": "Reviewer",
            "approval": "suggest",
            "tools": ["read_file", "grep"],
            "system_prompt": "You review code.",
        },
    )
    assert status == 200, body
    pid = body["id"]
    assert [p.id for p in state.cfg.approval_profiles] == [pid]

    status, body = _request(base, "/api/approval_profiles/activate", {"id": pid})
    assert status == 200, body
    assert state.cfg.active_approval_profile_id == pid

    loaded = load_config(config_path=saved[-1], workdir=tmp_path)
    assert loaded.active_approval_profile_id == pid
    assert loaded.approval_profiles[0].tools == ["read_file", "grep"]

    status, body = _request(base, "/api/approval_profiles/delete", {"id": pid})
    assert status == 200, body
    # 删活跃档案必须清指针，否则 _apply_approval_profile 会静默失效
    assert state.cfg.active_approval_profile_id == ""
    assert state.cfg.approval_profiles == []


def test_approval_profile_rejects_unknown_model_profile(web) -> None:
    base, state, saved, _ = web
    status, body = _request(
        base,
        "/api/approval_profiles/save",
        {"name": "X", "approval": "suggest", "model_profile_id": "does-not-exist"},
    )
    assert status == 404
    assert state.cfg.approval_profiles == []
    assert saved == []


def test_approval_profile_rejects_unknown_approval_mode(web) -> None:
    base, state, saved, _ = web
    status, _ = _request(
        base, "/api/approval_profiles/save", {"name": "X", "approval": "yolo"}
    )
    assert status == 400
    assert state.cfg.approval_profiles == []
    assert saved == []


def test_approval_profile_create_requires_approval(web) -> None:
    base, state, _saved, _tmp = web
    status, _ = _request(base, "/api/approval_profiles/save", {"name": "X"})
    assert status == 400
    assert state.cfg.approval_profiles == []


def test_approval_profile_activate_unknown_returns_404(web) -> None:
    base, state, _saved, _tmp = web
    status, _ = _request(base, "/api/approval_profiles/activate", {"id": "nope"})
    assert status == 404
    assert state.cfg.active_approval_profile_id == ""


def test_settings_still_rejects_governance_sections(web) -> None:
    """写接口是独立端点；/api/settings 仍应显式拒绝，避免两套语义。"""
    base, state, saved, _ = web
    status, body = _request(
        base, "/api/settings", {"permission_rules": [{"action": "allow", "resource": "**"}]}
    )
    assert status == 400
    assert "read-only" in body["error"]
    assert state.cfg.permission_rules == []
    assert saved == []


# ---------------------------------------------------------------------------
# 3. 消息级 undo 连带文件还原
# ---------------------------------------------------------------------------


def test_message_undo_without_snapshot_reports_files_untouched(web) -> None:
    base, state, _saved, _tmp = web
    msg_id = state.store.append_message(
        state.session_id,
        __import__("spark.models", fromlist=["ChatMessage"]).ChatMessage(
            role="user", content="hello"
        ),
    )
    status, body = _request(
        base, "/api/messages/truncate", {"message_id": msg_id, "restore_files": True}
    )
    assert status == 200, body
    assert body["files_restored"] is False
    assert "no checkpoint" in body["restore_reason"]


def test_message_undo_restores_files_from_prior_checkpoint(web) -> None:
    from spark.core.checkpoints import make_checkpoint_record
    from spark.models import ChatMessage

    base, state, _saved, _tmp = web
    work = state.workdir
    (work / "a.txt").write_text("before", encoding="utf-8")
    msg_id = state.store.append_message(
        state.session_id, ChatMessage(role="user", content="edit it")
    )
    snapshot_id, _hash = make_checkpoint_record(work)
    state.store.add_checkpoint(
        state.session_id, "cp", msg_id, json.dumps({"snapshot_id": snapshot_id})
    )
    # 模拟之后的轮次改坏了文件
    (work / "a.txt").write_text("after", encoding="utf-8")
    (work / "created-later.txt").write_text("temp", encoding="utf-8")

    status, body = _request(
        base, "/api/messages/truncate", {"message_id": msg_id, "restore_files": True}
    )
    assert status == 200, body
    assert body["files_restored"] is True
    assert (work / "a.txt").read_text(encoding="utf-8") == "before"
    assert not (work / "created-later.txt").exists()


def test_message_undo_default_keeps_files(web) -> None:
    """不传 restore_files 时行为不变：只回退对话，文件保持现状。"""
    from spark.core.checkpoints import make_checkpoint_record
    from spark.models import ChatMessage

    base, state, _saved, _tmp = web
    work = state.workdir
    (work / "a.txt").write_text("before", encoding="utf-8")
    msg_id = state.store.append_message(
        state.session_id, ChatMessage(role="user", content="x")
    )
    snapshot_id, _ = make_checkpoint_record(work)
    state.store.add_checkpoint(
        state.session_id, "cp", msg_id, json.dumps({"snapshot_id": snapshot_id})
    )
    (work / "a.txt").write_text("after", encoding="utf-8")

    status, body = _request(base, "/api/messages/truncate", {"message_id": msg_id})
    assert status == 200, body
    assert body["files_restored"] is False
    assert (work / "a.txt").read_text(encoding="utf-8") == "after"


def test_nearest_checkpoint_picks_the_prior_one(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "nearest.db")
    try:
        sid = store.create_session(tmp_path, "m", title="t")
        first = store.add_checkpoint(sid, "first", 10, "{}")
        second = store.add_checkpoint(sid, "second", 30, "{}")
        assert store.nearest_checkpoint_at_or_before(sid, 5) is None
        assert store.nearest_checkpoint_at_or_before(sid, 10)["id"] == first
        assert store.nearest_checkpoint_at_or_before(sid, 29)["id"] == first
        assert store.nearest_checkpoint_at_or_before(sid, 30)["id"] == second
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 4. 子代理独立模型
# ---------------------------------------------------------------------------


def _write_agent(wd: Path, name: str, frontmatter_extra: str = "") -> Path:
    d = wd / ".claude" / "agents"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.md"
    path.write_text(
        "---\n"
        f"name: {name}\n"
        "description: test agent\n"
        "tools: read_file\n"
        f"{frontmatter_extra}"
        "---\n"
        "You are a specialist.\n",
        encoding="utf-8",
    )
    return path


def test_declared_agent_parses_model_field(tmp_path: Path) -> None:
    path = _write_agent(tmp_path, "cheap", "model: tiny-model\n")
    agent = parse_agent_file(path)
    assert agent.model == "tiny-model"


def test_declared_agent_model_defaults_to_empty(tmp_path: Path) -> None:
    path = _write_agent(tmp_path, "plain")
    assert parse_agent_file(path).model == ""


def test_available_subagents_block_exposes_model(tmp_path: Path) -> None:
    path = _write_agent(tmp_path, "cheap", "model: tiny-model\n")
    block = inject_subagents("sys", [parse_agent_file(path)])
    assert 'model="tiny-model"' in block


def test_task_params_carry_the_agent_model(tmp_path: Path) -> None:
    """_single_task_params / _parallel_task_params 必须把 model 传下去。"""
    from spark.core.loop import AgentLoop

    _write_agent(tmp_path, "cheap", "model: tiny-model\n")
    agent = get_declared_agent(tmp_path, "cheap", global_dir=tmp_path / "nope")

    single = AgentLoop._single_task_params(("do it", agent))
    assert single[3] == "tiny-model"

    _p, _s, _a, models = AgentLoop._parallel_task_params([("do it", agent)])
    assert models == ["tiny-model"]

    plain_path = _write_agent(tmp_path, "plain")
    plain = parse_agent_file(plain_path)
    assert AgentLoop._single_task_params(("do it", plain))[3] == ""


def test_spawn_subtask_switches_provider_for_declared_model(tmp_path: Path, monkeypatch) -> None:
    """指定 model 时必须用新 provider；构造失败则回退父 provider 而不是炸掉整轮。"""
    import spark.core.loop as loop_module
    import spark.providers.factory as factory
    from spark.config import AgentConfig

    cfg = SparkConfig(
        provider={"name": "mock", "model": "main-model"},
        agent=AgentConfig(sandbox_mode="workspace", approval="full-auto", max_tool_rounds=3),
    )
    from spark.sandbox import WorkdirSandbox
    from spark.tools.registry import ToolContext, ToolRegistry

    store = SessionStore(":memory:")
    sid = store.create_session(tmp_path, "main-model", title="t")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg)
    parent_provider = object()

    loop = loop_module.AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=parent_provider,  # type: ignore[arg-type]
        registry=ToolRegistry(ctx),
        store=store,
        session_id=sid,
    )

    seen: list[str] = []

    class _Provider:
        async def stream(self, messages, tools, effort=None):  # noqa: ANN001
            from spark.models import ChatDelta

            yield ChatDelta(type="text", text="ok")
            yield ChatDelta(type="end")

    def fake_create(sub_cfg):
        seen.append(sub_cfg.provider.model)
        return _Provider()

    monkeypatch.setattr(factory, "create_provider", fake_create)

    async def run() -> None:
        async for _event in loop.spawn_subtask("hi", model="tiny-model"):
            pass

    import asyncio

    asyncio.new_event_loop().run_until_complete(run())
    store.close()
    assert seen == ["tiny-model"]
    # 父配置不被子代理污染
    assert loop.cfg.provider.model == "main-model"


def test_spawn_subtask_falls_back_when_model_creation_fails(tmp_path: Path, monkeypatch) -> None:
    import spark.core.loop as loop_module
    import spark.providers.factory as factory
    from spark.config import AgentConfig
    from spark.sandbox import WorkdirSandbox
    from spark.tools.registry import ToolContext, ToolRegistry

    cfg = SparkConfig(
        provider={"name": "mock", "model": "main-model"},
        agent=AgentConfig(sandbox_mode="workspace", approval="full-auto"),
    )
    store = SessionStore(":memory:")
    sid = store.create_session(tmp_path, "main-model", title="t")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg)

    class _Provider:
        async def stream(self, messages, tools, effort=None):  # noqa: ANN001
            from spark.models import ChatDelta

            yield ChatDelta(type="text", text="ok")
            yield ChatDelta(type="end")

    parent_provider = _Provider()
    loop = loop_module.AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=parent_provider,  # type: ignore[arg-type]
        registry=ToolRegistry(ctx),
        store=store,
        session_id=sid,
    )

    def boom(_cfg):
        raise RuntimeError("unknown model")

    monkeypatch.setattr(factory, "create_provider", boom)

    async def run() -> None:
        async for _event in loop.spawn_subtask("hi", model="does-not-exist"):
            pass

    import asyncio

    asyncio.new_event_loop().run_until_complete(run())
    store.close()
    assert loop.cfg.provider.model == "main-model"


def test_approval_profile_activation_applies_tool_allowlist(web) -> None:
    """激活后 loop 的工具白名单应随之收敛（不是只改配置文件）。"""
    base, state, _saved, _tmp = web
    status, body = _request(
        base,
        "/api/approval_profiles/save",
        {"name": "RO", "approval": "suggest", "tools": ["read_file"]},
    )
    assert status == 200, body
    pid = body["id"]
    status, body = _request(base, "/api/approval_profiles/activate", {"id": pid})
    assert status == 200, body
    names = {s["function"]["name"] for s in state.loop.registry.schemas()}
    assert names == {"read_file"}
    assert state.loop._approval_override == "suggest"


def test_status_reports_effective_approval_not_baseline(web) -> None:
    """审批档案覆盖审批模式时，/api/status 必须报生效值，否则 UI 会撒谎。"""
    base, state, _saved, _tmp = web
    assert state.status()["approval"] == "full-auto"

    status, body = _request(
        base,
        "/api/approval_profiles/save",
        {"name": "RO", "approval": "suggest", "tools": ["read_file"]},
    )
    assert status == 200, body
    status, body = _request(base, "/api/approval_profiles/activate", {"id": body["id"]})
    assert status == 200, body

    st = state.status()
    assert st["approval"] == "suggest", "status must report the override"
    assert st["approval_base"] == "full-auto"

    cfg = _request(base, "/api/config")[1]
    assert cfg["agent"]["approval"] == "full-auto", "the form edits the baseline"
    assert cfg["agent"]["effective_approval"] == "suggest"


def test_safety_switches_round_trip_through_settings(web) -> None:
    """三个安全开关必须能经 /api/settings 写入、落盘、回读。

    它们改变 agent 的实际行为（审查、断网、改文件），所以"保存了但没生效"
    比不支持更糟——这里断言的是完整往返。
    """
    base, state, saved, tmp_path = web
    status, body = _request(
        base,
        "/api/settings",
        {"agent": {"auto_review": True, "network_access": False, "format_on_write": True}},
    )
    assert status == 200, body
    assert state.cfg.agent.auto_review is True
    assert state.cfg.agent.network_access is False
    assert state.cfg.agent.format_on_write is True

    loaded = load_config(config_path=saved[-1], workdir=tmp_path)
    assert loaded.agent.auto_review is True
    assert loaded.agent.network_access is False
    assert loaded.agent.format_on_write is True

    # /api/config 必须回显，否则设置面板刷新后显示默认值
    cfg = _request(base, "/api/config")[1]
    assert cfg["agent"]["auto_review"] is True
    assert cfg["agent"]["network_access"] is False
    assert cfg["agent"]["format_on_write"] is True


def test_safety_switches_default_to_conservative_values(web) -> None:
    base, state, _saved, _tmp = web
    cfg = _request(base, "/api/config")[1]
    assert cfg["agent"]["auto_review"] is False, "review costs a model call: off by default"
    assert cfg["agent"]["network_access"] is True, "off would break pip/npm out of the box"
    assert cfg["agent"]["format_on_write"] is False, "silently rewriting files must be opt-in"
