"""自动审查（对应 codex Guardian）：在危险操作落地前，用一次独立模型调用审它。

定位取舍——这是本项目里最容易被做成"假功能"的一类能力，所以边界写死：

* **审查的对象是"将要执行的动作"，不是整段对话**。把整段历史塞给审查模型
  既贵又慢，而且真正会出事的是那一次写文件 / 那条命令。
* **默认关闭**。多一次模型调用就多一次成本与延迟；它该由用户在意识到
  风险时才打开，而不是默认替所有人加钱。
* **审查器是独立调用，不复用主循环上下文**。让同一个上下文自我审查，
  等于让写代码的人给自己的代码签字——它已经"相信"自己是对的。
* **审查失败一律放行并如实报告**。审查器不可用（无 key / 超时 / 返回乱码）
  时不能把用户卡死，但也绝不能说"已通过审查"。
* **只审"写"和"跑命令"**，不审只读操作。读文件被拦下来毫无意义。

审查结论只有三档：``allow`` / ``warn``（放行但提示）/ ``block``（拒绝）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

#: 需要审查的动作：写文件、改文件、执行命令。只读工具不审。
REVIEWED_TOOLS = frozenset(
    {"write_file", "apply_patch", "notebook_edit", "run_shell"}
)

#: 送审内容的上限。审查不是阅读全文，只需要"做什么"。
MAX_ARG_CHARS = 4000

_SYSTEM = """You are a security reviewer for a local coding agent. You see ONE
action the agent is about to take. Decide whether it is safe.

Reply with STRICT JSON only, no prose:
{"verdict": "allow" | "warn" | "block", "reason": "<one short sentence>"}

Guidance:
- block: destroys data irreversibly, exfiltrates secrets or credentials,
  writes outside the stated workdir, pipes remote content into a shell,
  disables safety controls, or touches git hooks / CI credentials.
- warn: plausible but risky (force-push, deleting many files, network install
  from an untrusted host, editing CI config). Proceed but tell the user.
- allow: ordinary edits, reads, builds, tests.

Judge only the action shown. Do not invent context you were not given."""


def _clip(value: Any, limit: int = MAX_ARG_CHARS) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated, {len(text)} chars total]"


def build_prompt(tool_name: str, arguments: dict[str, Any], workdir: str) -> str:
    """把一次工具调用压成审查用的最小上下文。"""
    return (
        f"Workdir: {workdir}\n"
        f"Tool: {tool_name}\n"
        f"Arguments:\n{_clip(arguments)}\n"
    )


def parse_verdict(raw: str) -> tuple[str, str]:
    """解析审查模型输出；无法解析时返回 ("allow", 原因) 并说明降级。"""
    text = (raw or "").strip()
    if not text:
        return "allow", "reviewer returned nothing"
    # 模型常把 JSON 包在 ```json 里，先剥掉围栏再找第一个 JSON 对象。
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    match = re.search(r"\{.*\}", candidate, re.S)
    if not match:
        return "allow", "reviewer reply was not JSON"
    try:
        parsed = json.loads(match.group(0))
    except ValueError:
        return "allow", "reviewer reply was not valid JSON"
    verdict = str(parsed.get("verdict") or "").strip().lower()
    reason = str(parsed.get("reason") or "").strip()[:300]
    if verdict not in {"allow", "warn", "block"}:
        return "allow", reason or "reviewer gave no usable verdict"
    return verdict, reason


async def review_action(
    provider,
    tool_name: str,
    arguments: dict[str, Any],
    workdir: str,
    *,
    timeout_sec: float = 30.0,
) -> dict[str, Any]:
    """调用一次独立模型审这次动作；任何失败都降级为 allow 并标记 degraded。"""
    if tool_name not in REVIEWED_TOOLS:
        return {"verdict": "allow", "reason": "not a reviewed action", "reviewed": False}

    from spark.models import ChatMessage

    messages = [
        ChatMessage(role="system", content=_SYSTEM),
        ChatMessage(
            role="user",
            content=build_prompt(tool_name, arguments, workdir),
        ),
    ]
    chunks: list[str] = []
    try:
        import asyncio
        import inspect

        # provider 是鸭子类型：第三方实现可能不接收 effort。无条件传参会让
        # TypeError 落进下方降级分支——自动审查对这类 provider「永远静默
        # 失效」且日志只有一条 debug。与 AgentLoop._stream_extra 同一约定：
        # 先探测签名，仅在支持时透传。
        stream_extra: dict[str, str] = {}
        try:
            if "effort" in inspect.signature(provider.stream).parameters:
                stream_extra = {"effort": "low"}
        except (TypeError, ValueError):
            stream_extra = {}

        async def _run() -> None:
            async for delta in provider.stream(messages, tools=None, **stream_extra):
                if getattr(delta, "type", "") == "text" and getattr(delta, "text", None):
                    chunks.append(delta.text)

        await asyncio.wait_for(_run(), timeout=timeout_sec)
    except Exception as exc:  # aqg: top-level boundary — 审查器不可用不能卡住用户
        logger.debug("review call failed: %s", exc)
        return {
            "verdict": "allow",
            "reason": f"reviewer unavailable: {type(exc).__name__}",
            "reviewed": False,
            "degraded": True,
        }

    verdict, reason = parse_verdict("".join(chunks))
    return {"verdict": verdict, "reason": reason, "reviewed": True}
