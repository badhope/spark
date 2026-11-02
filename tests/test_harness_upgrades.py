"""多根目录、写后格式化、定时任务、符号定位的回归测试。

这四项都是从竞品矩阵里补齐的 harness 能力，共同点是「容易做成半成品」：
目录授权放宽了却没收住边界、格式化失败却吞掉错误、定时任务并发踩会话、
符号定位假装自己是 LSP。这里的断言全部针对**可证伪的边界行为**。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from spark.automations import (
    AutomationScheduler,
    CronError,
    looks_like_cron,
    new_automation,
    next_run_after,
    normalize_interval,
    parse_cron,
)
from spark.config import AgentConfig, SparkConfig
from spark.core.review import parse_verdict, review_action
from spark.errors import PathEscapeError
from spark.sandbox import SandboxPolicyError, WorkdirSandbox
from spark.store import SessionStore
from spark.tools import fs
from spark.tools.formatting import format_file
from spark.tools.symbols import find_symbol

# ---------------------------------------------------------------------------
# 1. 多根目录（--add-dir）
# ---------------------------------------------------------------------------


def test_extra_dirs_allow_reads_but_keep_the_boundary(tmp_path: Path) -> None:
    work = tmp_path / "work"
    extra = tmp_path / "extra"
    outside = tmp_path / "outside"
    for d in (work, extra, outside):
        d.mkdir()
    (extra / "a.txt").write_text("hello", encoding="utf-8")
    (outside / "b.txt").write_text("nope", encoding="utf-8")

    cfg = SparkConfig(agent=AgentConfig())
    cfg.agent.extra_dirs = [str(extra)]
    sandbox = WorkdirSandbox(work, cfg)

    assert sandbox.resolve(str(extra / "a.txt")).read_text(encoding="utf-8") == "hello"
    assert sandbox.resolve("a.txt").parent == work
    # 未授权的兄弟目录仍然拒绝——加目录不等于放开整个磁盘
    with pytest.raises(PathEscapeError):
        sandbox.resolve(str(outside / "b.txt"))


def test_extra_dirs_do_not_disable_protected_paths(tmp_path: Path) -> None:
    """额外目录里的 git 控制面（hooks 等）同样受保护。

    只相对主 root 计算 `.git` 归属时，`--add-dir` 目录里的 hooks 会完全
    逃过检查——那正是"克隆一个仓库就拿到执行权"的入口。
    """
    work = tmp_path / "work"
    extra = tmp_path / "extra"
    (work / ".git").mkdir(parents=True)
    (extra / ".git" / "hooks").mkdir(parents=True)
    (extra / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
    cfg = SparkConfig(agent=AgentConfig())
    cfg.agent.extra_dirs = [str(extra)]
    sandbox = WorkdirSandbox(work, cfg)

    with pytest.raises(SandboxPolicyError):
        sandbox.resolve(str(extra / ".git" / "hooks" / "pre-commit"))


def test_extra_dirs_deduplicate_and_skip_root(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    cfg = SparkConfig(agent=AgentConfig())
    cfg.agent.extra_dirs = [str(work), str(work)]
    sandbox = WorkdirSandbox(work, cfg)
    assert sandbox.extra_roots == []


def test_cli_add_dir_is_not_persisted(tmp_path: Path) -> None:
    """--add-dir 是本次进程的一次性授权，不能悄悄写进配置。"""
    from spark.cli import _common_cfg

    work = tmp_path / "work"
    work.mkdir()
    extra = tmp_path / "extra"
    extra.mkdir()
    cfg = _common_cfg(
        work, None, None, None, "mock", add_dirs=[extra]
    )
    assert str(extra.resolve()) in cfg.agent.extra_dirs


# ---------------------------------------------------------------------------
# 2. 写后格式化
# ---------------------------------------------------------------------------


def test_format_on_write_is_off_by_default(tmp_path: Path) -> None:
    cfg = SparkConfig(agent=AgentConfig())
    assert cfg.agent.format_on_write is False
    sandbox = WorkdirSandbox(tmp_path, cfg)
    result = fs.write_file(sandbox, fs.WriteFileArgs(path="a.py", content="x = 1\n"))
    assert result.ok
    assert "formatted" not in result.payload


def test_format_on_write_reports_the_tool(tmp_path: Path) -> None:
    cfg = SparkConfig(agent=AgentConfig(format_on_write=True))
    sandbox = WorkdirSandbox(tmp_path, cfg)
    result = fs.write_file(sandbox, fs.WriteFileArgs(path="a.py", content="x=1\n"))
    assert result.ok
    # ruff 是项目自身依赖，这里必然可用
    assert result.payload.get("formatted") == "ruff"


def test_format_skips_unknown_extensions(tmp_path: Path) -> None:
    target = tmp_path / "notes.xyz"
    target.write_text("x", encoding="utf-8")
    outcome = format_file(target, tmp_path, enabled=True)
    assert outcome["formatted"] is False
    assert outcome["error"] == ""


def test_format_never_raises_when_disabled(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    outcome = format_file(target, tmp_path, enabled=False)
    assert outcome == {"formatted": False, "tool": "", "error": ""}


def test_format_failure_keeps_the_write(tmp_path: Path, monkeypatch) -> None:
    """格式化失败不能让写文件失败——否则用户丢的是整个编辑。"""
    import spark.tools.formatting as formatting

    cfg = SparkConfig(agent=AgentConfig(format_on_write=True))
    sandbox = WorkdirSandbox(tmp_path, cfg)
    monkeypatch.setattr(formatting, "_resolve_tool", lambda name, root: None)
    result = fs.write_file(sandbox, fs.WriteFileArgs(path="b.py", content="y = 2\n"))
    assert result.ok
    assert (tmp_path / "b.py").read_text(encoding="utf-8") == "y = 2\n"
    assert "format_skipped" in result.payload


# ---------------------------------------------------------------------------
# 3. 定时任务
# ---------------------------------------------------------------------------


def test_cron_parsing_and_ranges() -> None:
    spec = parse_cron("*/15 9-11 * * 1-5")
    assert sorted(spec["minute"]) == [0, 15, 30, 45]
    assert spec["hour"] == {9, 10, 11}
    assert spec["weekday"] == {1, 2, 3, 4, 5}


@pytest.mark.parametrize(
    "expr",
    ["* * *", "60 * * * *", "*/0 * * * *", "* * * * 8", "a b c d e"],
)
def test_cron_rejects_invalid(expr: str) -> None:
    with pytest.raises(CronError):
        parse_cron(expr)


def test_next_run_lands_on_the_expected_minute() -> None:
    base = datetime(2026, 11, 3, 8, 7)  # 周二
    nxt = next_run_after("0 9 * * *", base)
    assert (nxt.hour, nxt.minute) == (9, 0)
    assert nxt.date() == base.date()


def test_next_run_respects_weekday_filter() -> None:
    friday = datetime(2026, 11, 6, 10, 0)  # 周五
    nxt = next_run_after("0 9 * * 1", friday)  # 只在周一
    assert nxt.weekday() == 0
    assert nxt > friday + timedelta(days=2)


def test_interval_helper() -> None:
    assert normalize_interval(20) == "*/20 * * * *"
    assert normalize_interval(60) == "0 * * * *"
    assert normalize_interval(120) == "0 */2 * * *"
    with pytest.raises(CronError):
        normalize_interval(90)


def test_looks_like_cron_quick_check() -> None:
    assert looks_like_cron("0 9 * * *") is True
    assert looks_like_cron("0 9 * *") is False
    assert looks_like_cron("99 9 * * *") is False


def test_new_automation_validates_input() -> None:
    with pytest.raises(CronError):
        new_automation(name="", prompt="x", cron="0 9 * * *")
    with pytest.raises(CronError):
        new_automation(name="a", prompt="", cron="0 9 * * *")
    with pytest.raises(CronError):
        new_automation(name="a", prompt="x", cron="bad")
    ok = new_automation(name="a", prompt="x", cron="0 9 * * *")
    assert ok["enabled"] is True
    assert ok["next_run_at"] > 0


def test_scheduler_marks_due_once(tmp_path: Path) -> None:
    """到点即写 last_run：同一任务不会被同一拍反复判为 due。"""
    store = SessionStore(tmp_path / "auto.db")
    try:
        rec = new_automation(name="t", prompt="p", cron="0 9 * * *")
        rec["next_run_at"] = 1  # 早已过期
        store.upsert_automation(rec)
        sched = AutomationScheduler(store, tick_sec=999)
        first = sched.run_due()
        assert [r["id"] for r in first] == [rec["id"]]
        second = sched.run_due()
        assert second == [], "the same task must not fire twice"
        row = store.get_automation(rec["id"])
        assert row["last_run"] > 1
        assert row["next_run_at"] > row["last_run"]
    finally:
        store.close()


def test_scheduler_skips_disabled(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "auto2.db")
    try:
        rec = new_automation(name="t", prompt="p", cron="0 9 * * *", enabled=False)
        rec["next_run_at"] = 1
        store.upsert_automation(rec)
        assert AutomationScheduler(store, tick_sec=999).run_due() == []
    finally:
        store.close()


def test_scheduler_initialises_next_run_without_firing(tmp_path: Path) -> None:
    """新建任务不应立刻执行一次——那是"刚建就炸"的最短路径。"""
    store = SessionStore(tmp_path / "auto3.db")
    try:
        rec = new_automation(name="t", prompt="p", cron="0 9 * * *")
        rec["next_run_at"] = 0
        store.upsert_automation(rec)
        assert AutomationScheduler(store, tick_sec=999).run_due() == []
        row = store.get_automation(rec["id"])
        assert row["next_run_at"] > 0
    finally:
        store.close()


def test_automation_store_crud(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "auto4.db")
    try:
        rec = new_automation(name="n", prompt="p", cron="0 9 * * *")
        store.upsert_automation(rec)
        assert [r["id"] for r in store.list_automations()] == [rec["id"]]
        store.mark_automation_result(rec["id"], status="skipped (agent busy)", skipped=True)
        row = store.get_automation(rec["id"])
        assert row["skipped_count"] == 1
        store.mark_automation_result(rec["id"], status="ok", session_id="s1")
        row = store.get_automation(rec["id"])
        assert (row["last_status"], row["last_session_id"]) == ("ok", "s1")
        assert store.delete_automation(rec["id"]) is True
        assert store.list_automations() == []
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 4. 符号定位
# ---------------------------------------------------------------------------


def test_find_symbol_locates_a_definition(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def target_fn():\n    return 1\n\n\nclass Thing:\n    pass\n",
        encoding="utf-8",
    )
    result = find_symbol(tmp_path, "target_fn", mode="definition")
    assert result["definitions"]
    assert result["definitions"][0]["line"] == 1
    assert "not a full LSP" in result["note"]


def test_find_symbol_reports_references(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "def helper():\n    return 2\n\n\ndef caller():\n    return helper()\n",
        encoding="utf-8",
    )
    result = find_symbol(tmp_path, "helper", mode="reference")
    lines = [r["line"] for r in result["references"]]
    assert 6 in lines, result


def test_find_symbol_handles_missing_symbol(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    result = find_symbol(tmp_path, "nope_not_here", mode="definition")
    assert result["definitions"] == []
    assert "hint" in result


def test_find_symbol_rejects_empty(tmp_path: Path) -> None:
    assert "error" in find_symbol(tmp_path, "   ")


def test_find_symbol_skips_vendor_directories(tmp_path: Path) -> None:
    vendor = tmp_path / "node_modules" / "pkg"
    vendor.mkdir(parents=True)
    (vendor / "x.py").write_text("def secret_fn():\n    pass\n", encoding="utf-8")
    result = find_symbol(tmp_path, "secret_fn", mode="definition")
    assert result["definitions"] == []


def test_find_symbol_is_registered_as_readonly() -> None:
    """只读分类是安全属性：改了就要显式知道。"""
    from spark.policy import READONLY_TOOLS

    assert "find_symbol" in READONLY_TOOLS


# ---------------------------------------------------------------------------
# 5. 网络限制开关
# ---------------------------------------------------------------------------


def test_network_access_defaults_on() -> None:
    assert SparkConfig(agent=AgentConfig()).agent.network_access is True


def test_bwrap_omits_share_net_when_network_denied(monkeypatch) -> None:
    """allow_network=False 必须不传 --share-net，否则等于没断网。"""
    import spark.tools.shell as shell

    monkeypatch.setattr(shell.os, "name", "posix")
    monkeypatch.setattr(shell, "_bwrap_path", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(shell.os.path, "isdir", lambda p: False)

    allowed = shell.bwrap_command("/w", "echo hi", "/w", allow_network=True)
    denied = shell.bwrap_command("/w", "echo hi", "/w", allow_network=False)
    assert allowed is not None and denied is not None
    assert "--share-net" in allowed
    assert "--share-net" not in denied
    # 两种情况都要保留 --unshare-all：断网靠的就是它建空网络命名空间
    assert "--unshare-all" in denied


def test_bwrap_unavailable_returns_none(monkeypatch) -> None:
    import spark.tools.shell as shell

    monkeypatch.setattr(shell, "_bwrap_path", lambda: None)
    assert shell.bwrap_command("/w", "echo hi", "/w", allow_network=False) is None


def test_network_denied_reports_when_it_cannot_be_enforced(tmp_path: Path, monkeypatch) -> None:
    """配置要断网但平台做不到时必须如实告警，不能假装已断网。"""
    import spark.tools.shell as shell

    monkeypatch.setattr(shell.os, "name", "nt")  # 模拟 Windows：bwrap 永远不可用
    cfg = SparkConfig(agent=AgentConfig(network_access=False, approval="full-auto"))
    sandbox = WorkdirSandbox(tmp_path, cfg)
    result = shell.run_shell(
        sandbox, shell.RunShellArgs(command="echo hi"), timeout_sec=10, max_output_chars=2000
    )
    assert result.ok
    assert "network_warning" in result.payload
    assert "network" not in result.payload or result.payload.get("network") != "blocked"


# ---------------------------------------------------------------------------
# 6. 自动审查（Guardian）
# ---------------------------------------------------------------------------


def test_auto_review_off_by_default() -> None:
    assert SparkConfig(agent=AgentConfig()).agent.auto_review is False


def test_review_only_covers_write_and_exec_tools() -> None:
    from spark.core.review import REVIEWED_TOOLS

    assert {"write_file", "apply_patch", "run_shell"} <= REVIEWED_TOOLS
    # 只读工具不该被审：拦读文件毫无意义，只是白花钱
    assert "read_file" not in REVIEWED_TOOLS
    assert "grep" not in REVIEWED_TOOLS


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"verdict": "block", "reason": "rm -rf"}', "block"),
        ('```json\n{"verdict":"warn","reason":"force push"}\n```', "warn"),
        ('{"verdict": "allow"}', "allow"),
        ("garbage", "allow"),
        ("", "allow"),
        ('{"verdict": "maybe"}', "allow"),
    ],
)
def test_parse_verdict(raw: str, expected: str) -> None:
    verdict, _reason = parse_verdict(raw)
    assert verdict == expected


def test_review_prompt_clips_huge_arguments() -> None:
    from spark.core.review import MAX_ARG_CHARS, build_prompt

    prompt = build_prompt("write_file", {"content": "x" * (MAX_ARG_CHARS * 2)}, "/w")
    assert len(prompt) < MAX_ARG_CHARS * 2
    assert "truncated" in prompt


def test_reviewer_failure_degrades_to_allow() -> None:
    """审查器挂了不能把用户卡死，但必须标记 degraded。"""
    import asyncio

    class BrokenProvider:
        async def stream(self, *a, **k):
            raise RuntimeError("no key")
            yield  # pragma: no cover

    result = asyncio.new_event_loop().run_until_complete(
        review_action(BrokenProvider(), "run_shell", {"command": "ls"}, "/w")
    )
    assert result["verdict"] == "allow"
    assert result["degraded"] is True
    assert result["reviewed"] is False


def test_reviewer_parses_block_from_provider() -> None:
    import asyncio

    from spark.models import ChatDelta

    class BlockingProvider:
        async def stream(self, *a, **k):
            yield ChatDelta(type="text", text='{"verdict": "block", "reason": "exfiltration"}')
            yield ChatDelta(type="end")

    result = asyncio.new_event_loop().run_until_complete(
        review_action(BlockingProvider(), "run_shell", {"command": "curl secret"}, "/w")
    )
    assert result["verdict"] == "block"
    assert result["reviewed"] is True


def test_unreviewed_tool_skips_the_call() -> None:
    """只读工具必须直接返回、不发起任何模型调用。"""
    import asyncio

    class ExplodingProvider:
        async def stream(self, *a, **k):
            raise AssertionError("must not be called for read-only tools")
            yield  # pragma: no cover

    result = asyncio.new_event_loop().run_until_complete(
        review_action(ExplodingProvider(), "read_file", {"path": "a"}, "/w")
    )
    assert result["reviewed"] is False
    assert result["verdict"] == "allow"
