"""H4 审批档案 + H5 自定义命令目录。"""

from __future__ import annotations

from pathlib import Path

from spark.config import ApprovalProfile, SparkConfig
from spark.core.loop import AgentLoop
from spark.models import ToolCall
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.slash import discover_commands, expand_command, parse_slash
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry

# ---------------------------------------------------------------------------
# H4：审批档案
# ---------------------------------------------------------------------------


def _make_loop(tmp_path: Path, cfg: SparkConfig) -> tuple[AgentLoop, SessionStore]:
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session(tmp_path, "mock")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
    registry = ToolRegistry(ctx)
    loop = AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=MockProvider(),
        registry=registry,
        store=store,
        session_id=sid,
    )
    return loop, store


def test_approval_profile_overrides_mode_without_cfg_mutation(tmp_path: Path) -> None:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "suggest"
    cfg.approval_profiles = [
        ApprovalProfile(id="strict", name="Strict", approval="plan-only")
    ]
    cfg.active_approval_profile_id = "strict"
    loop, store = _make_loop(tmp_path, cfg)
    # 生效模式被档案覆盖；cfg 基线不被突变（重启/切档案语义一致）
    assert loop._approval_mode() == "plan-only"
    assert cfg.agent.approval == "suggest"
    store.close()


def test_approval_profile_tool_allowlist_globs(tmp_path: Path) -> None:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.approval_profiles = [
        ApprovalProfile(
            id="ro",
            name="Readonly",
            approval="full-auto",
            tools=["read_*", "list_dir", "grep"],
        )
    ]
    cfg.active_approval_profile_id = "ro"
    loop, store = _make_loop(tmp_path, cfg)
    allow = loop.registry.tool_allowlist
    assert allow is not None
    assert "read_file" in allow and "list_dir" in allow and "grep" in allow
    assert "write_file" not in allow and "run_shell" not in allow
    # 白名单同样收敛 schema 暴露面
    names = {s["function"]["name"] for s in loop.registry.schemas()}
    assert "write_file" not in names
    store.close()


def test_approval_profile_system_prompt_injected(tmp_path: Path) -> None:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.approval_profiles = [
        ApprovalProfile(
            id="pro",
            name="Pro",
            approval="suggest",
            system_prompt="Always answer in haiku.",
        )
    ]
    cfg.active_approval_profile_id = "pro"
    loop, store = _make_loop(tmp_path, cfg)
    assert "Always answer in haiku." in loop._extra_system_prompt
    store.close()


def test_no_active_profile_means_no_override(tmp_path: Path) -> None:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "suggest"
    cfg.approval_profiles = [
        ApprovalProfile(id="a", name="A", approval="full-auto")
    ]
    loop, store = _make_loop(tmp_path, cfg)
    assert loop._approval_mode() == "suggest"
    assert loop.registry.tool_allowlist is None
    store.close()


def test_scoped_tool_allowlist_filters_schemas(tmp_path: Path) -> None:
    """白名单下的 execute 调用仍被 registry 拦截（Unknown tool）。"""
    import asyncio

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    cfg.approval_profiles = [
        ApprovalProfile(id="ro", name="RO", approval="full-auto", tools=["read_file"])
    ]
    cfg.active_approval_profile_id = "ro"
    loop, store = _make_loop(tmp_path, cfg)
    result = asyncio.run(
        loop.registry.execute(ToolCall(id="x", name="write_file", arguments={}))
    )
    assert result.ok is False
    store.close()


# ---------------------------------------------------------------------------
# H5：自定义命令
# ---------------------------------------------------------------------------


def test_discover_and_expand_commands(tmp_path: Path) -> None:
    cmd_dir = tmp_path / ".spark" / "commands"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "refactor.md").write_text(
        "---\ndescription: 重构指定文件\n---\n请重构 $ARGUMENTS，保持行为不变。",
        encoding="utf-8",
    )
    (cmd_dir / "plain.md").write_text("直接总结 $ARGUMENTS", encoding="utf-8")

    commands = discover_commands(tmp_path)
    names = {c.name for c in commands}
    assert {"refactor", "plain"} <= names

    expanded = expand_command(tmp_path, "/refactor src/app.py 保持接口")
    assert expanded == "请重构 src/app.py 保持接口，保持行为不变。"

    plain = expand_command(tmp_path, "/plain")
    assert plain == "直接总结"

    assert expand_command(tmp_path, "/no-such-cmd x") is None
    assert expand_command(tmp_path, "hello") is None


def test_builtin_test_not_shadowed(tmp_path: Path) -> None:
    cmd_dir = tmp_path / ".spark" / "commands"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "test.md").write_text("evil instructions", encoding="utf-8")
    commands = discover_commands(tmp_path)
    assert all(c.name != "test" for c in commands)
    # parse_slash 仍命中内建 /test
    assert parse_slash("/test tests/") is not None
    # expand_command 对内建名返回 None（回退内建路径）
    assert expand_command(tmp_path, "/test") is None


def test_custom_command_flows_through_loop_as_user_text(tmp_path: Path) -> None:
    """展开产物进入 agent loop 的 user 消息（不绕过审批/沙箱）。"""
    cmd_dir = tmp_path / ".spark" / "commands"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "hello.md").write_text("Say hi to $ARGUMENTS", encoding="utf-8")

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session(tmp_path, "mock")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
    loop = AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=MockProvider(),
        registry=ToolRegistry(ctx),
        store=store,
        session_id=sid,
    )
    expanded = expand_command(tmp_path, "/hello spark")
    assert expanded == "Say hi to spark"
    events = asyncio_run_collect(loop, expanded)
    assert events[-1].type == "turn_end"
    store.close()


def asyncio_run_collect(loop: AgentLoop, prompt: str):
    import asyncio

    async def collect():
        return [e async for e in loop.iter_turn(prompt)]

    return asyncio.run(collect())


# ---------------------------------------------------------------------------
# W4-c：插件 commands
# ---------------------------------------------------------------------------


def _install_command_plugin(workdir: Path, home: Path, monkeypatch) -> None:
    import json

    from spark.plugins import install_local

    source = workdir / "plugin-source"
    (source / "commands").mkdir(parents=True)
    (source / "spark-plugin.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "version": "0.1.0",
                "entry": "run.py",
                "permissions": ["files:read"],
                "commands": ["hello"],
            }
        )
    )
    (source / "run.py").write_text("print('demo')\n")
    (source / "commands" / "hello.md").write_text(
        "---\ndescription: plugin hello\n---\nPlugin says hi to $ARGUMENTS",
        encoding="utf-8",
    )
    install_local(source, workdir)
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text('[plugins]\nenabled = ["demo"]\n')
    monkeypatch.setenv("SPARK_HOME", str(home))


def test_plugin_command_discovery_and_expand(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "work"
    workdir.mkdir()
    _install_command_plugin(workdir, home, monkeypatch)
    commands = [c for c in discover_commands(workdir) if c.name == "hello"]
    assert len(commands) == 1
    assert commands[0].plugin == "demo"
    assert expand_command(workdir, "/hello world") == "Plugin says hi to world"


def test_plugin_command_disabled_and_local_shadow(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "work"
    workdir.mkdir()
    _install_command_plugin(workdir, home, monkeypatch)
    (home / "config.toml").write_text("[plugins]\nenabled = []\n")
    assert all(c.name != "hello" for c in discover_commands(workdir))

    (home / "config.toml").write_text('[plugins]\nenabled = ["demo"]\n')
    local = workdir / ".spark" / "commands"
    local.mkdir(parents=True)
    (local / "hello.md").write_text("LOCAL says $ARGUMENTS", encoding="utf-8")
    commands = [c for c in discover_commands(workdir) if c.name == "hello"]
    assert commands[0].plugin == ""
    assert expand_command(workdir, "/hello world") == "LOCAL says world"
