"""P1-3 ``/test`` slash 命令：解析、处理器（含失败回灌 traceback）、入口接线。

验收要点：
- ``/test`` 解析只认 ``test``，未知 ``/xxx`` 与普通文本一律返回 None（透传给模型）；
- 处理器先失败后成功时，失败回灌消息必须携带 traceback；
- 回灌消息能真实注入 AgentLoop（最后一条 user 消息含 traceback）；
- TUI / CLI exec / web 三条入口都接到了同一套 slash 后端；
- ``/test`` 真实跑一遍 pytest（tmp 仓库），不只是 mock。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from spark.models import ChatDelta, ToolResult
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.slash import handle_test, parse_slash
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry


class _Sandbox:
    """鸭子沙箱：把任意路径 resolve 到 tmp_path 之下（与 gitops 测试同款）。"""

    def __init__(self, root: Path) -> None:
        self.root = root

    def resolve(self, path: str = ".") -> Path:
        return self.root / path


def _fail_result() -> ToolResult:
    return ToolResult(
        ok=False,
        payload={
            "command": "python -m pytest -q",
            "exit_code": 1,
            "output": (
                "============================= test session starts ==============================\n"
                "===== FAILURES =====\n"
                "_____________________________ test_add _____________________________\n"
                "def test_add():\n"
                ">   assert add(1, 2) == 4\n"
                "E   AssertionError: assert 3 == 4\n"
                "tests/test_math.py:5: AssertionError\n"
                "=========================== short test summary info ============================\n"
                "FAILED tests/test_math.py::test_add - AssertionError: assert 3 == 4\n"
                "1 failed, 1 passed in 0.12s\n"
            ),
        },
    )


def _pass_result() -> ToolResult:
    return ToolResult(
        ok=True,
        payload={
            "command": "python -m pytest -q",
            "exit_code": 0,
            "passed": True,
            "output": "2 passed in 0.05s",
        },
    )


# ---------------------------------------------------------------------------
# 1. slash 解析
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    ["", "hello", "  ", "/foo", "/bar tests", "/testx", "//test"],
)
def test_parse_slash_rejects_non_commands(text: str) -> None:
    assert parse_slash(text) is None


def test_parse_slash_recognizes_test() -> None:
    cmd = parse_slash("/test")
    assert cmd is not None
    assert cmd.name == "test"
    assert cmd.args == ""


def test_parse_slash_captures_args_and_lowercases() -> None:
    cmd = parse_slash("  /TEST tests/test_math.py  ")
    assert cmd is not None
    assert cmd.name == "test"
    assert cmd.args == "tests/test_math.py"


# ---------------------------------------------------------------------------
# 2. /test 处理器：先失败后成功，回灌消息含 traceback
# ---------------------------------------------------------------------------
class _FlipFlopRunner:
    """第一次跑失败（带 traceback），agent 修正后第二次跑全绿。"""

    def __init__(self) -> None:
        self.calls = 0
        self.paths: list[str] = []

    def __call__(self, sandbox, path: str) -> ToolResult:
        self.paths.append(path)
        self.calls += 1
        return _fail_result() if self.calls == 1 else _pass_result()


def test_handle_test_failure_feedback_carries_traceback() -> None:
    runner = _FlipFlopRunner()
    first = handle_test(_Sandbox(Path(".")), ".", runner=runner)
    assert first.ok is False
    assert first.feedback is not None
    # 回灌消息必须把失败现场交给 agent
    assert "AssertionError" in first.feedback
    assert "Traceback" not in first.feedback  # 示例输出里没有整段 traceback 头
    assert "tests/test_math.py:5" in first.feedback
    # 一行摘要要让用户一眼看到失败
    assert "失败" in first.summary


def test_handle_test_reports_path_and_then_green() -> None:
    runner = _FlipFlopRunner()
    handle_test(_Sandbox(Path(".")), "tests/unit", runner=runner)
    # 参数原样透传为运行目录，绝不拼成 shell 命令
    assert runner.paths == ["tests/unit"]
    second = handle_test(_Sandbox(Path(".")), ".", runner=runner)
    assert second.ok is True
    assert second.feedback is None
    assert "通过" in second.summary


def test_handle_test_error_with_no_runner_command() -> None:
    """跑不起来（无测试入口）也要把原因回灌给 agent。"""

    def _runner(sandbox, path: str) -> ToolResult:
        return ToolResult(
            ok=False, payload={"error": "no test command detected for this repository"}
        )

    result = handle_test(_Sandbox(Path(".")), ".", runner=_runner)
    assert result.ok is False
    assert result.feedback is not None
    assert "no test command" in result.feedback


# ---------------------------------------------------------------------------
# 3. 回灌消息真实注入 AgentLoop（端到端，用 MockProvider 见证）
# ---------------------------------------------------------------------------
def _loop(tmp_path: Path, provider: MockProvider) -> object:
    from spark.config import SparkConfig

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session(tmp_path, "mock")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
    registry = ToolRegistry(ctx)
    from spark.core.loop import AgentLoop

    return AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=provider,
        registry=registry,
        store=store,
        session_id=sid,
    )


@pytest.mark.asyncio
async def test_feedback_injected_into_agent_loop(tmp_path: Path) -> None:
    runner = _FlipFlopRunner()
    failed = handle_test(_Sandbox(tmp_path), ".", runner=runner)
    assert failed.feedback is not None

    provider = MockProvider(
        rounds=[[ChatDelta(type="text", text="已修复 add() 的返回值"), ChatDelta(type="end")]]
    )
    loop = _loop(tmp_path, provider)
    events = await loop.run(failed.feedback)

    assert events[-1].type == "turn_end"
    assert provider.calls == 1  # agent 确实读到了回灌消息并作答

    # 回灌消息作为 user 消息落进历史，且含失败现场
    user_msgs = [m for m in loop.history if m.role == "user" and m.content]
    assert user_msgs, "回灌消息必须作为 user 消息进入历史"
    joined = "\n".join(m.content or "" for m in user_msgs)
    assert "tests/test_math.py:5" in joined
    assert "AssertionError" in joined


# ---------------------------------------------------------------------------
# 4. /test 真实跑一遍 pytest（tmp 仓库，而非 mock）
# ---------------------------------------------------------------------------
def test_handle_test_runs_real_pytest(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_smoke.py").write_text(
        "def test_smoke():\n    assert 1 + 1 == 2\n", encoding="utf-8"
    )
    result = handle_test(_Sandbox(tmp_path), ".")  # 默认 runner = gitops.run_tests
    assert result.ok is True, result.summary + "\n" + result.output
    assert "pytest" in result.command


def test_handle_test_real_failure_feedback(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_bad.py").write_text(
        "def test_bad():\n    assert 1 == 2, \"boom\"\n", encoding="utf-8"
    )
    result = handle_test(_Sandbox(tmp_path), ".")
    assert result.ok is False
    assert result.feedback is not None
    # 真实 pytest 的失败输出（含 'boom' 与 FAILED）应进入回灌消息
    assert "boom" in result.feedback
    assert "FAILED" in result.feedback


# ---------------------------------------------------------------------------
# 5. 入口接线（源码级见证，沿用本仓库对 cli/web 的 inspect 测试惯例）
# ---------------------------------------------------------------------------
def test_tui_wires_test_slash() -> None:
    from spark.tui.app import SparkApp

    src = inspect.getsource(SparkApp)
    assert "parse_slash" in src
    assert "_run_test_slash" in src
    # 普通 prompt 仍走原有回合
    assert "_run_prompt" in src


def test_web_stream_wires_test_slash() -> None:
    # 第六轮把 /test 段落拆进 _run_test_slash/_dispatch_turn 后，接线见证
    # 从函数源码升级为模块源码（覆盖 helper 与调用点）。
    import spark.web._handlers_chat as chat_mod

    src = inspect.getsource(chat_mod)
    assert "parse_slash" in src
    assert "handle_test" in src
    assert "slash_result.feedback" in src


def test_cli_exec_wires_test_slash() -> None:
    # 复杂度拆分把 /test 分支移进 _run_test_mode 后，接线见证同样升级为
    # 模块源码：parse_slash 在 _exec_async，handle_test 在 helper。
    from spark import cli

    src = inspect.getsource(cli)
    assert "parse_slash" in src
    assert "handle_test" in src
    assert "_run_test_mode" in src
