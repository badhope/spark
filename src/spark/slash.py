"""Slash 命令 —— 用户在输入框以 ``/`` 开头触发的本地命令（不经过模型）。

P1-3 ``/test`` 是 Aider 验证过的「零成本 TDD 闭环」：

1. 解析 ``/test [目录]``；
2. 复用 :func:`spark.tools.gitops.run_tests` 在沙箱里跑仓库自己的测试入口
   （命令永远来自仓库信号，模型/用户都不能注入任意 shell）；
3. 测试通过 → 只给用户一行摘要，不打扰 agent；
4. 测试失败 → 把完整输出（含失败 traceback）包装成一条 user 消息，回灌进
   agent loop，让 agent 自己读 traceback 改代码，用户不必复制粘贴。

入口（TUI / web / CLI exec）只负责「识别 + 渲染摘要 + 失败时把 feedback 喂给
loop」，所有判定与文案都收敛在本模块，便于单测。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from spark.models import ToolResult
from spark.sandbox import WorkdirSandbox
from spark.tools.gitops import RunTestsArgs, run_tests

logger = logging.getLogger(__name__)

#: 已实现的 slash 命令名（小写）。未知 ``/xxx`` 一律按普通 prompt 透传给模型。
KNOWN_COMMANDS = {"test", "plan", "build", "side", "goal"}

#: 测试运行器可替换（单测用 mock runner 先失败后成功来验证回灌）。
TestRunner = Callable[[WorkdirSandbox, str], ToolResult]


@dataclass(frozen=True)
class SlashCommand:
    """一条解析后的 slash 命令。"""

    name: str
    args: str  # 命令名之后的原始参数尾巴（已 strip）


def parse_slash(text: str) -> SlashCommand | None:
    """把输入文本解析成 :class:`SlashCommand`。

    返回 ``None`` 的情况（调用方应回退到普通 agent 回合）：

    - 文本不以 ``/`` 开头（普通对话）；
    - 命令名不在 :data:`KNOWN_COMMANDS` 里（交给模型去理解，不臆造行为）。
    """
    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return None
    head = stripped[1:].split("\n", 1)[0]
    parts = head.split(maxsplit=1)
    name = parts[0].lower() if parts else ""
    if name not in KNOWN_COMMANDS:
        return None
    args = parts[1].strip() if len(parts) > 1 else ""
    return SlashCommand(name=name, args=args)


@dataclass(frozen=True)
class SlashTestResult:
    """/test 的执行结果：给用户的摘要 + 给 agent 的回灌消息。"""

    ok: bool  # 测试是否真的通过
    command: str  # 检测到的测试命令
    exit_code: int | None
    summary: str  # 一行用户可读摘要
    output: str = ""  # 完整捕获输出（已按 gitops 裁剪）
    #: 失败时注入 agent loop 的 user 消息；通过或无可修正时为 None。
    feedback: str | None = None


def _default_runner(sandbox: WorkdirSandbox, path: str) -> ToolResult:
    return run_tests(sandbox, RunTestsArgs(path=path))


def build_test_result(tool_result: ToolResult) -> SlashTestResult:
    """把 :class:`ToolResult` 翻译成面向用户/agent 的 :class:`SlashTestResult`。

    抽成纯函数，方便单测直接构造 ToolResult 验证文案与回灌逻辑。
    """
    payload: dict[str, Any] = tool_result.payload or {}
    command = str(payload.get("command") or "")
    exit_code = payload.get("exit_code")
    output = str(payload.get("output") or "")
    error = payload.get("error")

    if tool_result.ok:
        return SlashTestResult(
            ok=True,
            command=command,
            exit_code=exit_code,
            summary=f"✅ 测试通过（{command or 'pytest'}）",
            output=output,
            feedback=None,
        )

    if error:
        # 跑不起来（无测试入口 / 超时 / 沙箱拒绝）→ 把原因回灌给 agent 排查。
        summary = f"⚠️ 未能运行测试：{error}"
        feedback = (
            "运行仓库测试命令失败：\n"
            f"- 原因：{error}\n"
            f"- 检测到的命令：{command or '(无)'}\n"
            "请检查该仓库的测试配置（pyproject/pytest.ini/package.json），"
            "然后再次运行 /test。"
        )
        return SlashTestResult(
            ok=False,
            command=command,
            exit_code=exit_code,
            summary=summary,
            output=output,
            feedback=feedback,
        )

    # 真·测试失败：把含 traceback 的完整输出回灌给 agent 让它自行修正。
    summary = f"❌ 测试失败（exit={exit_code}）"
    feedback = (
        "仓库测试未通过。以下是完整输出（含失败 traceback）：\n\n"
        f"{output or '(无输出)'}\n\n"
        "请定位失败原因并修改源码使其通过（不要放宽测试本身的断言），"
        "改完后再次运行 /test 验证。"
    )
    return SlashTestResult(
        ok=False,
        command=command,
        exit_code=exit_code,
        summary=summary,
        output=output,
        feedback=feedback,
    )


def handle_test(
    sandbox: WorkdirSandbox,
    raw_args: str = "",
    *,
    runner: TestRunner | None = None,
) -> SlashTestResult:
    """执行 ``/test``：跑测试并产出摘要 + 失败回灌消息。

    ``raw_args`` 仅作为「在 workdir 下的哪个子目录跑测试」（透传为
    ``RunTestsArgs.path``），永远不拼接成 shell 命令。
    """
    runner = runner or _default_runner
    path = (raw_args or "").strip() or "."
    tool_result = runner(sandbox, path)
    return build_test_result(tool_result)


# ---------------------------------------------------------------------------
# H5：自定义命令目录（.spark/commands/*.md，文件即命令）
# ---------------------------------------------------------------------------

COMMANDS_DIR = Path(".spark") / "commands"


@dataclass(frozen=True)
class CommandInfo:
    """一条自定义命令：``<name>.md`` 的 frontmatter + 模板正文。"""

    name: str
    description: str
    body: str
    path: Path


def discover_commands(workdir: str | Path) -> list[CommandInfo]:
    """扫描 ``.spark/commands/*.md``；frontmatter 可选（description 提示用）。

    目录缺失/文件不可读 → 空列表（调用方可安全迭代）。内建命令
    （目前 ``/test``）优先——同名自定义命令永远不会遮蔽内建行为。
    """
    from spark.skills import _parse_frontmatter

    base = Path(workdir)
    commands_dir = base / COMMANDS_DIR
    if not commands_dir.is_dir():
        return []
    commands: list[CommandInfo] = []
    for md in sorted(commands_dir.glob("*.md")):
        if md.name.startswith("."):
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        parsed = _parse_frontmatter(text)
        if parsed is None:
            meta: dict[str, str] = {}
            body = text
        else:
            meta, body = parsed
        name = (meta.get("name") or md.stem).strip().lower()
        if not name:
            continue
        if name in KNOWN_COMMANDS:
            logger.debug("自定义命令 %s 与内建同名，已跳过（内建优先）", name)
            continue
        commands.append(
            CommandInfo(
                name=name,
                description=(meta.get("description") or "").strip(),
                body=body.strip(),
                path=md,
            )
        )
    return commands


def expand_command(workdir: str | Path, text: str) -> str | None:
    """把 ``/name args`` 展开为命令模板（``$ARGUMENTS`` 替换为参数尾巴）。

    返回 None 的情况（调用方回退原有行为——内建 slash 或普通 prompt）：
    非 ``/`` 开头、无匹配命令文件、模板为空。展开产物是一段**用户消息
    文本**（含模型指令），后续照常走 agent loop，不绕过任何审批/沙箱。
    """
    from spark.skills import _parse_frontmatter

    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return None
    after_slash = stripped[1:]
    token, _, rest = after_slash.partition(" ")
    name = token.strip().lower()
    if not name:
        return None
    if name in KNOWN_COMMANDS:
        # 内建命令优先：自定义文件永不遮蔽内建行为
        return None
    base = Path(workdir)
    candidate = base / COMMANDS_DIR / f"{name}.md"
    if not candidate.is_file():
        return None
    try:
        raw = candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    parsed = _parse_frontmatter(raw)
    body = (parsed[1] if parsed else raw).strip()
    if not body:
        return None
    return body.replace("$ARGUMENTS", rest.strip()).strip()


__all__ = [
    "COMMANDS_DIR",
    "KNOWN_COMMANDS",
    "CommandInfo",
    "SlashCommand",
    "SlashTestResult",
    "build_test_result",
    "discover_commands",
    "expand_command",
    "handle_test",
    "parse_slash",
]
