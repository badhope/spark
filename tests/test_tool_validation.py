"""AQ-03: tool-call schema validation, one retry, and small-model tool selection."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.core.loop import AgentLoop
from spark.models import ChatDelta, ChatMessage, ToolCall, ToolResult
from spark.providers.llama_cpp import _filter_tools, _needs_tools, _trim_tools_for_task
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.tools.registry import ToolContext, ToolRegistry
from spark.tools.validation import argument_size_error


def _registry(tmp_path: Path) -> ToolRegistry:
    cfg = SparkConfig()
    cfg.agent.sandbox_mode = "workspace"
    return ToolRegistry(ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg))


def _loop(tmp_path: Path, provider: MockProvider | None = None) -> AgentLoop:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    store_path = tmp_path / "s.db"
    from spark.store import SessionStore

    store = SessionStore(store_path)
    sid = store.create_session(tmp_path, "mock")
    return AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=provider or MockProvider(),
        registry=_registry(tmp_path),
        store=store,
        session_id=sid,
    )


BAD_CASES = [
    ("read_file", {}, "invalid_tool_arguments"),
    ("read_file", {"path": 1}, "invalid_tool_arguments"),
    ("read_file", {"path": "a", "offset": "10"}, "invalid_tool_arguments"),
    ("list_dir", {"path": []}, "invalid_tool_arguments"),
    ("write_file", {"path": "a"}, "invalid_tool_arguments"),
    ("write_file", {"path": 1, "content": "x"}, "invalid_tool_arguments"),
    ("apply_patch", {"path": "a", "new_text": "x"}, "invalid_tool_arguments"),
    ("grep", {}, "invalid_tool_arguments"),
    ("grep", {"pattern": "x", "max_results": "many"}, "invalid_tool_arguments"),
    ("run_tests", {"command": "pwd"}, "invalid_tool_arguments"),
    ("run_tests", {"timeout_sec": "600"}, "invalid_tool_arguments"),
    ("run_shell", {"cwd": "."}, "invalid_tool_arguments"),
    ("web_search", {}, "invalid_tool_arguments"),
    ("web_search", {"query": 42}, "invalid_tool_arguments"),
    ("task", {"tasks": [{"agent": "x"}]}, "invalid_tool_arguments"),
    ("update_plan", {"steps": "not-a-list"}, "invalid_tool_arguments"),
    ("ask_user", {}, "invalid_tool_arguments"),
    ("no_such_tool", {}, "unknown_tool"),
]


@pytest.mark.parametrize(("name", "arguments", "code"), BAD_CASES)
def test_bad_tool_arguments_are_rejected(
    tmp_path: Path, name: str, arguments: dict, code: str
) -> None:
    error = _registry(tmp_path).validation_error(
        ToolCall(id="c", name=name, arguments=arguments)
    )
    assert error is not None
    assert error["code"] == code
    assert error["error"]


GOOD_CASES = [
    ("read_file", {"path": "README.md"}),
    ("read_file", {"path": "README.md", "offset": 0, "limit": 20}),
    ("list_dir", {"path": "."}),
    ("write_file", {"path": "a.txt", "content": "hello"}),
    ("apply_patch", {"path": "a.txt", "old_text": "a", "new_text": "b"}),
    ("grep", {"pattern": "TODO"}),
    ("run_tests", {"timeout_sec": 600}),
    ("web_search", {"query": "Spark"}),
    ("task", {"prompt": "inspect"}),
    ("task", {"tasks": [{"prompt": "a"}, {"prompt": "b"}]}),
    ("update_plan", {"steps": [{"title": "x", "status": "pending"}]}),
    ("ask_user", {"question": "continue?"}),
]


@pytest.mark.parametrize(("name", "arguments"), GOOD_CASES)
def test_valid_tool_arguments_pass(
    tmp_path: Path, name: str, arguments: dict
) -> None:
    assert _registry(tmp_path).validation_error(
        ToolCall(id="c", name=name, arguments=arguments)
    ) is None


def test_disabled_and_allowlisted_tools_are_not_executable(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.disabled_tools = {"read_file"}
    error = registry.validation_error(ToolCall(id="c", name="read_file", arguments={"path": "a"}))
    assert error is not None and error["code"] == "tool_disabled"

    registry.tool_allowlist = {"read_file"}
    error = registry.validation_error(ToolCall(id="c", name="write_file", arguments={"path": "a", "content": "b"}))
    assert error is not None and error["code"] == "tool_not_allowed"


def test_pathological_argument_payload_is_rejected(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    huge = "x" * (2 * 1024 * 1024 + 1)
    error = registry.validation_error(
        ToolCall(id="c", name="write_file", arguments={"path": "a", "content": huge})
    )
    assert error is not None and error["code"] == "tool_arguments_too_large"
    assert argument_size_error({"content": huge}) is not None


def test_validation_never_calls_handlers(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    called: list[dict] = []

    def _spy(arguments: dict) -> ToolResult:
        called.append(arguments)
        return ToolResult(ok=True, payload={})

    registry._dispatch_cache = {"read_file": _spy, "write_file": _spy}
    result = asyncio.run(
        registry.execute(ToolCall(id="c", name="read_file", arguments={}))
    )
    assert result.ok is False
    assert result.payload["code"] == "invalid_tool_arguments"
    assert called == []


def test_invalid_call_is_not_approved_and_gets_one_correction(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    bad = ToolCall(id="c", name="read_file", arguments={})
    verdicts = asyncio.run(loop._decide_calls([bad]))
    assert verdicts[0]["action"] == "invalid"
    assert verdicts[0]["validation"]["code"] == "invalid_tool_arguments"

    second = loop._validation_error(ToolCall(id="c2", name="read_file", arguments={}))
    assert second is not None
    assert second["retryable"] is False
    assert second["code"].endswith("_retry_exhausted")


def test_execute_round_skips_handler_for_invalid_call(tmp_path: Path, monkeypatch) -> None:
    loop = _loop(tmp_path)
    bad = ToolCall(id="c", name="read_file", arguments={})
    verdict = asyncio.run(loop._decide_calls([bad]))[0]
    called = False

    async def _boom(call: ToolCall) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(ok=True, payload={})

    monkeypatch.setattr(loop.registry, "execute", _boom)

    async def run():
        return [
            event
            async for event in loop._execute_round([bad], [verdict], "")
        ]

    events = asyncio.run(run())
    assert called is False
    tool_end = [e for e in events if e.type == "tool_end"]
    assert tool_end and tool_end[0].result is not None
    assert tool_end[0].result.ok is False
    assert tool_end[0].result.payload["code"] == "invalid_tool_arguments"


@pytest.mark.asyncio
async def test_invalid_call_returns_error_to_model_once(tmp_path: Path, monkeypatch) -> None:
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(id="bad", name="read_file", arguments={}),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="I will fix the arguments."), ChatDelta(type="end")],
        ]
    )
    loop = _loop(tmp_path, provider)
    called = False

    async def _boom(call: ToolCall) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(ok=True, payload={})

    monkeypatch.setattr(loop.registry, "execute", _boom)
    events = await loop.run("read a file")
    assert provider.calls == 2
    assert called is False
    ends = [event for event in events if event.type == "tool_end"]
    assert ends and ends[0].result is not None
    assert ends[0].result.payload["code"] == "invalid_tool_arguments"
    assert ends[0].result.payload["retryable"] is True


SELECTION_CASES = [
    ("读取 README.md", True),
    ("查看 src/main.py", True),
    ("列出目录", True),
    ("搜索 TODO", True),
    ("查找函数定义", True),
    ("运行测试", True),
    ("执行命令", True),
    ("构建项目", True),
    ("写入 config.py", True),
    ("修改 main.py", True),
    ("创建文件", True),
    ("删除旧文件", True),
    ("修复 bug", True),
    ("排查错误", True),
    ("检查代码", True),
    ("优化性能", True),
    ("部署服务", True),
    ("提交 commit", True),
    ("安装依赖", True),
    ("下载文件", True),
    ("计算 12345 * 6789", False),
    ("你好，介绍一下你自己", False),
    ("解释一下 Python GIL", False),
]


def _tool(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "description": name}}


@pytest.mark.parametrize(("prompt", "expected"), SELECTION_CASES)
def test_small_model_tool_selection(prompt: str, expected: bool) -> None:
    tools = [_tool("read_file"), _tool("run_tests"), _tool("web_search")]
    exposed = _needs_tools([ChatMessage(role="user", content=prompt)], tools)
    assert bool(exposed) is expected


def test_local_schema_filter_drops_non_core_tools() -> None:
    tools = [
        _tool("read_file"),
        _tool("run_tests"),
        _tool("web_search"),
        _tool("lsp_rename"),
        _tool("browser_open"),
        _tool("task"),
    ]
    kept = {item["function"]["name"] for item in _filter_tools(tools)}
    assert kept == {"read_file", "run_tests", "web_search"}
    assert _needs_tools(
        [ChatMessage(role="user", content="计算 12345 * 6789")],
        [_tool("run_tests")],
    ) == []


def test_schema_is_trimmed_to_task_verbs() -> None:
    tools = [
        _tool("read_file"),
        _tool("list_dir"),
        _tool("grep"),
        _tool("glob"),
        _tool("find_symbol"),
        _tool("write_file"),
        _tool("apply_patch"),
        _tool("apply_edit"),
        _tool("run_shell"),
        _tool("run_tests"),
        _tool("web_search"),
        _tool("web_fetch"),
        _tool("ask_user"),
    ]

    def names(prompt: str) -> set[str]:
        picked = _trim_tools_for_task([ChatMessage(role="user", content=prompt)], tools)
        return {item["function"]["name"] for item in picked}

    read_only = names("读取 README.md 并总结")
    assert {"read_file", "grep", "ask_user"} <= read_only
    assert not read_only & {"write_file", "apply_patch", "run_shell", "run_tests", "web_search"}

    edit_and_test = names("修复 bug 后运行测试")
    assert {"write_file", "apply_patch", "run_tests", "run_shell"} <= edit_and_test

    web = names("联网搜索最新的 Python 版本")
    assert {"web_search", "web_fetch"} <= web
    assert "run_tests" not in web

    assert _trim_tools_for_task(
        [ChatMessage(role="user", content="读取 README.md")], [_tool("run_tests")]
    ) == []

    followup = _trim_tools_for_task(
        [
            ChatMessage(role="user", content="读取 README.md"),
            ChatMessage(role="tool", content="done", tool_call_id="c"),
        ],
        tools,
    )
    assert len(followup) == len(tools)
