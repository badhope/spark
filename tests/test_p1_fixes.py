from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from spark.config import AgentConfig, MemoryConfig, SparkConfig
from spark.core.checkpoints import (
    latest_snapshot_id,
    make_checkpoint_record,
    restore_workdir,
    snapshot_workdir,
)
from spark.memory.store import MemoryStore
from spark.models import ChatMessage, ToolCall, TurnEvent
from spark.sandbox import WorkdirSandbox
from spark.tools import bg, gitops, notebook, search


def _cfg(mode: str = "workspace", **agent_kw) -> SparkConfig:
    agent = AgentConfig(sandbox_mode=mode, **agent_kw)  # type: ignore[arg-type]
    return SparkConfig(agent=agent)


# ---------- P1-1 memory ----------


def test_memory_redacts_secrets_on_add(tmp_path: Path) -> None:
    from spark.memory.service import redact_secrets

    text = "api key is sk-abcdefghijklmnop and password=hunter2 token=ghp_abcdef123456"
    out = redact_secrets(text)
    assert "sk-abcdefghijklmnop" not in out
    assert "ghp_abcdef123456" not in out
    assert "hunter2" not in out


def test_enforce_capacity_archives_stale(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.db", MemoryConfig(capacity=100, enabled=True))
    mid = store.add("stale memory", importance=5)
    old = int(time.time()) - 400 * 86400
    store._apply_metadata_update(mid, [("last_accessed", old), ("created_at", old)])
    archived = store.enforce_capacity(stale_days=90)
    assert store.get(mid).status == "archived"
    assert archived >= 1


def test_embed_survives_running_loop(tmp_path: Path) -> None:
    from spark.memory.service import MemoryService

    cfg = SparkConfig()
    cfg.memory.enabled = (
        False  # embed returns None without network; still exercises loop guard
    )
    cfg.provider.name = "mock"
    svc = MemoryService(MemoryStore(tmp_path / "m.db", cfg.memory), cfg)

    async def outer() -> None:
        vecs = svc._embed(["hello"])  # must not raise RuntimeError from asyncio.run
        assert vecs is None

    asyncio.run(outer())


def test_update_content_accepts_embedding(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.db", MemoryConfig())
    mid = store.add("old", embedding=[1.0, 0.0])
    store.update_content(mid, "new text", importance=7, embedding=[0.0, 1.0])
    row = store.get(mid)
    assert row.content == "new text"
    assert row.embedding == [0.0, 1.0]


# ---------- P1-2 checkpoints ----------


def test_snapshot_dir_not_hardcoded_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    from spark.core import checkpoints

    resolved = checkpoints._snapshot_dir()
    assert "/root" not in str(resolved)
    assert str(home) in str(resolved) or resolved.is_absolute()


def test_latest_snapshot_id_strips_tar_gz(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from spark.core import checkpoints

    monkeypatch.setattr(checkpoints, "SNAPSHOT_DIR", tmp_path)
    sid = snapshot_workdir(tmp_path)
    assert (tmp_path / f"{sid}.tar.gz").exists()
    got = latest_snapshot_id()
    assert got == sid
    assert not str(got).endswith(".tar")


def test_make_checkpoint_record_and_restore_roundtrip(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "a.txt").write_text("hello", encoding="utf-8")
    sid, h = make_checkpoint_record(work)
    assert sid and h
    (work / "a.txt").write_text("mutated", encoding="utf-8")
    result = restore_workdir(work, sid)
    assert result["restored_files"] >= 1
    assert (work / "a.txt").read_text(encoding="utf-8") == "hello"


# ---------- P1-3 sandbox ----------


def test_sandbox_blocks_quoted_protected_path(tmp_path: Path) -> None:
    sb = WorkdirSandbox(tmp_path, _cfg("workspace"))
    assert sb.check_shell('cat "/etc/passwd"') is not None
    assert sb.check_shell("cat '/etc/shadow'") is not None


def test_sandbox_blocks_rm_rf_root(tmp_path: Path) -> None:
    sb = WorkdirSandbox(tmp_path, _cfg("workspace"))
    assert sb.check_shell("rm -rf /") is not None
    assert sb.check_shell("rm -rf ~") is not None


def test_unrestricted_still_checks_user_protected(tmp_path: Path) -> None:
    sb = WorkdirSandbox(
        tmp_path, _cfg("unrestricted", protected_paths=["/opt/custom-protected"])
    )
    reason = sb.check_shell("cat /opt/custom-protected/secret")
    assert reason is not None


# ---------- P1-4 bg ----------


def test_bg_start_disabled_in_sandbox_only(tmp_path: Path) -> None:
    sb = WorkdirSandbox(tmp_path, _cfg("sandbox-only"))
    result = bg.bg_start_tool(sb, {"command": "echo hi"})
    assert not result.ok
    assert "disabled" in str(result.payload.get("error", ""))


def test_bg_running_jobs_capped(tmp_path: Path) -> None:
    sb = WorkdirSandbox(tmp_path, _cfg("workspace"))
    bg.JOBS.clear()
    # 启动 MAX_JOBS+2 个：瞬时 running 达上限时新任务被拒（RuntimeError）；
    # echo 完成很快，名额可能已被释放（收割竞态）。规格核心：任意时刻 running <= MAX_JOBS。
    for i in range(bg.MAX_JOBS + 2):
        try:
            job = bg._start_background(sb, f"echo job{i}", ".")
            assert job["id"]
        except RuntimeError:
            pass
    running = [j for j in bg.JOBS.values() if j["finished_at"] is None]
    assert len(running) <= bg.MAX_JOBS


# ---------- P1-5 tools ----------


def test_notebook_edit_blocked_sandbox_only(tmp_path: Path) -> None:
    nb = {
        "cells": [
            {"cell_type": "code", "source": ["x=1"], "outputs": [], "metadata": {}}
        ],
        "metadata": {},
        "nbformat": 4,
    }
    path = tmp_path / "n.ipynb"
    path.write_text(json.dumps(nb), encoding="utf-8")
    sb = WorkdirSandbox(tmp_path, _cfg("sandbox-only"))
    r = notebook.notebook_edit(
        sb, notebook.NotebookEditArgs(path="n.ipynb", cell_index=0, new_source="y=2")
    )
    assert not r.ok
    assert "disabled" in str(r.payload.get("error", ""))


def test_notebook_markdown_cell_keeps_no_execution_count_contract(
    tmp_path: Path,
) -> None:
    nb = {
        "cells": [
            {"cell_type": "markdown", "source": "# t", "metadata": {}},
            {
                "cell_type": "code",
                "source": ["1"],
                "outputs": [{"output_type": "stream", "text": "old"}],
                "execution_count": 3,
                "metadata": {},
            },
        ],
        "metadata": {},
        "nbformat": 4,
    }
    path = tmp_path / "n.ipynb"
    path.write_text(json.dumps(nb), encoding="utf-8")
    sb = WorkdirSandbox(tmp_path, _cfg("workspace"))
    notebook.notebook_edit(
        sb, notebook.NotebookEditArgs(path="n.ipynb", cell_index=1, new_source="2")
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["cells"][1]["execution_count"] is None
    assert data["cells"][1]["outputs"] == []
    assert "execution_count" not in data["cells"][0] or data["cells"][0].get(
        "execution_count"
    ) in (None, 0)


def test_git_diff_rejects_option_like_ref(tmp_path: Path) -> None:
    sb = WorkdirSandbox(tmp_path, _cfg("workspace"))
    r = gitops.git_diff(sb, gitops.GitDiffArgs(path=".", ref="--output=/tmp/x"))
    assert not r.ok
    assert (
        "ref" in str(r.payload.get("error", "")).lower()
        or "invalid" in str(r.payload.get("error", "")).lower()
    )


def test_grep_skips_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("SECRET_TOKEN", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    link = work / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink not permitted")
    sb = WorkdirSandbox(work, _cfg("workspace"))
    r = search.grep_tool(sb, search.GrepArgs(pattern="SECRET_TOKEN", path="."))
    assert r.ok
    assert not any("SECRET_TOKEN" in line for line in r.payload.get("results", []))


def test_glob_double_star_matches_nested(tmp_path: Path) -> None:
    work = tmp_path / "work"
    (work / "src" / "deep").mkdir(parents=True)
    (work / "src" / "deep" / "a.ts").write_text("export {}", encoding="utf-8")
    (work / "src" / "b.ts").write_text("export {}", encoding="utf-8")
    sb = WorkdirSandbox(work, _cfg("workspace"))
    r = search.glob_tool(sb, search.GlobArgs(pattern="src/**/*.ts", path="."))
    assert r.ok
    files = r.payload["files"]
    assert any(f.endswith("b.ts") for f in files)
    assert any(f.endswith("a.ts") for f in files)


# ---------- P1-6 tui ----------


def test_tui_app_has_memory_and_running_guard() -> None:
    import inspect

    from spark.tui import app as tui_app

    src = inspect.getsource(tui_app.SparkApp)
    # The flag must not be called `_running`: Textual's App uses that name for
    # its own message loop and overwrote our value at startup (which disabled
    # the composer). Guard by behaviour-relevant name instead.
    assert "if self._turn_running" in src
    assert "self._running" not in src
    assert "cancel" in src  # on_unmount cancels


# ---------- P1-7 subagent ----------


@pytest.mark.asyncio
async def test_parent_task_forwards_approval_needed(tmp_path: Path) -> None:
    from spark.core.loop import AgentLoop
    from spark.providers.mock import MockProvider
    from spark.store import SessionStore
    from spark.tools.registry import ToolContext, ToolRegistry

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "suggest"
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

    async def fake_task(prompt: str):
        yield TurnEvent(type="approval_needed")
        yield TurnEvent(type="turn_end", text="done")

    loop.registry.ctx.task_runner = fake_task
    events = []
    async for ev in loop._run_tool(
        ToolCall(id="t1", name="task", arguments={"prompt": "x"}), 1
    ):
        events.append(ev)
    types = [e.type for e in events]
    assert "approval_needed" in types


def test_subagent_inherits_mcp_schemas(tmp_path: Path) -> None:
    from spark.core.loop import AgentLoop
    from spark.providers.mock import MockProvider
    from spark.store import SessionStore
    from spark.tools.registry import ToolContext, ToolRegistry

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session(tmp_path, "mock")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
    reg = ToolRegistry(ctx)
    reg.add_mcp_schema(
        {
            "type": "function",
            "function": {"name": "mcp__fake__echo", "parameters": {"type": "object"}},
        }
    )
    reg.readonly_mcp.add("mcp__fake__echo")
    loop = AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=MockProvider(),
        registry=reg,
        store=store,
        session_id=sid,
    )
    sub_ctx = loop._make_sub_context()
    sub_reg = ToolRegistry(sub_ctx)
    loop._inherit_registry(sub_reg)
    names = [
        s["function"]["name"] for s in sub_reg.schemas() if s.get("type") == "function"
    ]
    assert "mcp__fake__echo" in names
    assert "mcp__fake__echo" in sub_reg.readonly_mcp


# ---------- P2-9 loop / compact / cancel ----------


@pytest.mark.asyncio
async def test_cancel_before_iter_turn_yields_cancelled(tmp_path: Path) -> None:
    from spark.core.loop import AgentLoop
    from spark.store import SessionStore
    from spark.tools.registry import ToolContext, ToolRegistry

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session(tmp_path, "mock")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)

    class NoStream:
        def __init__(self):
            self.called = False

        async def stream(self, messages, schemas):
            self.called = True
            raise AssertionError("provider must not stream after cancel")

    provider = NoStream()
    loop = AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=provider,  # type: ignore[arg-type]
        registry=ToolRegistry(ctx),
        store=store,
        session_id=sid,
    )
    loop.cancelled = True
    events = []
    # cancel token: run() clears; direct iter_turn with cancelled=True should stop
    loop.cancelled = True
    async for ev in loop.iter_turn("hello"):
        events.append(ev)
        if ev.type == "turn_error":
            break
    assert any(e.type == "turn_error" for e in events)
    assert provider.called is False


@pytest.mark.asyncio
async def test_parallel_subtask_handles_runner_exception(tmp_path: Path) -> None:
    from spark.core.loop import AgentLoop
    from spark.providers.mock import MockProvider
    from spark.store import SessionStore
    from spark.tools.registry import ToolContext, ToolRegistry

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

    async def bad_runner(index: int, prompt: str, queue):
        raise RuntimeError("boom")

    loop._collect_subtask_events = bad_runner  # type: ignore[method-assign]
    ends = []
    async for ev in loop.spawn_subtasks_parallel(["a", "b"]):
        if ev.type == "turn_end":
            ends.append(ev)
    assert len(ends) == 2


@pytest.mark.asyncio
async def test_compact_falls_back_without_user_boundary(tmp_path: Path) -> None:
    from spark.core.loop import AgentLoop
    from spark.models import ChatDelta
    from spark.providers.mock import MockProvider
    from spark.store import SessionStore
    from spark.tools.registry import ToolContext, ToolRegistry

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.context.max_context_tokens = 200
    cfg.context.compact_threshold = 0.5
    cfg.context.keep_recent_messages = 2
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session(tmp_path, "mock")
    # only assistant/tool messages (no trailing user) — still over threshold
    for i in range(12):
        store.append_message(
            sid, ChatMessage(role="assistant", content=f"ans {i} " + "y" * 400)
        )
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
    provider = MockProvider(
        rounds=[[ChatDelta(type="text", text="ok"), ChatDelta(type="end")]]
    )
    loop = AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=provider,
        registry=ToolRegistry(ctx),
        store=store,
        session_id=sid,
    )
    loop.history = store.load_messages(sid)
    events = await loop.run("next")
    types = [e.type for e in events]
    # either compaction happens or turn completes without hang
    assert events[-1].type == "turn_end"
    assert "compaction" in types or loop.history[0].role != "system"


# ---------- P2-10 config ----------


def test_save_config_escapes_quotes_and_chmod(tmp_path: Path) -> None:
    from spark.config import load_config, save_config

    cfg = SparkConfig()
    cfg.provider.api_key = 'has"quote'
    cfg.provider.model = 'model"name'
    path = save_config(cfg, tmp_path / "c.toml")
    loaded = load_config(config_path=path, workdir=tmp_path)
    assert loaded.provider.api_key == 'has"quote'
    assert loaded.provider.model == 'model"name'
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600 or os.name == "nt"


def test_local_spark_toml_cannot_set_unrestricted(tmp_path: Path) -> None:
    from spark.config import load_config

    local = tmp_path / ".spark.toml"
    local.write_text(
        '[agent]\napproval = "full-auto"\nsandbox_mode = "unrestricted"\n',
        encoding="utf-8",
    )
    cfg = load_config(workdir=tmp_path)
    assert cfg.agent.sandbox_mode != "unrestricted"
    assert cfg.agent.approval != "full-auto"
