"""LLM-backed fact extraction and conflict resolution for memory."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field

from spark.memory.llm import chat_complete, parse_json_object
from spark.memory.store import MemoryRow
from spark.sanitize import redact_secrets
from spark.structured import StructuredOutputError
from spark.structured import complete as structured_complete

logger = logging.getLogger(__name__)


EXTRACT_SYSTEM = """你是长期记忆抽取器。从对话记录中提取值得跨会话长期记住的信息。
只提取这些类别：
- preference: 用户偏好或明确指令（如"用 pnpm""回复用中文"）
- project: 项目事实与约定（如"构建命令是 npm run build""deploy 目录是 /srv/app"）
- lesson: 踩坑经验与排查方法（如"该端点流式返回必须带 Accept 头"）
规则：每条独立自包含、原子化，脱离上下文也能看懂；不超过 120 字；临时性、寒暄、一次性任务细节不要。
没有值得记的就返回空列表。
只输出 JSON：{"items":[{"content":"...","type":"preference|project|lesson","importance":1-10}]}"""

RESOLVE_SYSTEM = """你是记忆库管理员。将每条新记忆与现有相似记忆对比，逐条决定操作：
- NOOP: 某条现有记忆已表达同样的事实
- UPDATE: 新记忆与某条现有记忆冲突或是其更新，给出合并后的最新表述（target_id 为现有记忆 id）
- ADD: 全新信息
只输出 JSON：
{"ops":[{"action":"ADD","content":"...","type":"preference|project|lesson","importance":5},
        {"action":"UPDATE","target_id":123,"content":"...","importance":5},
        {"action":"NOOP"}]}
每个 candidate 恰好输出一个 op，顺序对应。"""

CONSOLIDATE_SYSTEM = """把多条相关的记忆合并成一条精炼记忆：保留全部关键信息，消除重复，不超过 150 字。
只输出 JSON：{"content":"...","importance":1-10}"""


class _ConsolidateResult(BaseModel):
    """P1-1: schema for :func:`consolidate_group` (instructor-validated)."""

    content: str = Field(description="合并后的精炼记忆文本，150 字以内")
    importance: float = Field(description="重要性，1-10")


@dataclass
class Candidate:
    content: str
    type: str
    importance: float


@dataclass
class Op:
    action: str
    content: str = ""
    type: str = "general"
    importance: float = 5.0
    target_id: int | None = None


def _valid_type(t: str) -> str:
    return t if t in {"preference", "project", "lesson"} else "general"


async def extract_facts(
    *,
    transcript: str,
    base_url: str,
    api_key: str,
    model: str,
    explicit: bool = False,
) -> list[Candidate]:
    user_text = transcript
    if explicit:
        user_text = "用户在对话中明确要求记住某些内容，请务必提取。\n\n" + user_text
    raw = await chat_complete(
        base_url=base_url,
        api_key=api_key,
        model=model,
        system=EXTRACT_SYSTEM,
        user=user_text,
    )
    data = parse_json_object(raw)
    if not data:
        return []
    out: list[Candidate] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        try:
            importance = float(item.get("importance", 5))
        except (TypeError, ValueError):
            importance = 5.0
        content = redact_secrets(content)
        if not content.strip():
            continue
        out.append(
            Candidate(
                content=content,
                type=_valid_type(str(item.get("type") or "")),
                importance=max(1.0, min(10.0, importance)),
            )
        )
    return out[:8]


def _resolve_prompt(
    candidates: list[Candidate],
    similar: dict[int, list[tuple[MemoryRow, float]]],
) -> str:
    lines = ["新记忆候选："]
    for i, cand in enumerate(candidates):
        lines.append(
            f"{i}. [{cand.type}] {cand.content} (importance={cand.importance})"
        )
    lines.append("")
    lines.append("现有相似记忆：")
    has_any = False
    for i in range(len(candidates)):
        for row, score in similar.get(i, [])[:3]:
            has_any = True
            lines.append(f"- id={row.id} 相关度={score} [{row.type}] {row.content}")
    if not has_any:
        lines.append("（无）")
    return "\n".join(lines)


def _resolve_op(item: dict | None, cand: Candidate) -> Op:
    item = item or {}
    action = str(item.get("action") or "ADD").upper()
    if action == "UPDATE":
        target = item.get("target_id")
        content = str(item.get("content") or "").strip()
        if target and content:
            try:
                importance = float(item.get("importance", cand.importance))
            except (TypeError, ValueError):
                importance = cand.importance
            return Op(
                action="UPDATE",
                content=content,
                type=cand.type,
                importance=importance,
                target_id=int(target),
            )
        action = "ADD"
    if action == "ADD":
        content = str(item.get("content") or cand.content).strip() or cand.content
        try:
            importance = float(item.get("importance", cand.importance))
        except (TypeError, ValueError):
            importance = cand.importance
        return Op(action="ADD", content=content, type=cand.type, importance=importance)
    return Op(action="NOOP")


async def resolve_operations(
    *,
    candidates: list[Candidate],
    similar: dict[int, list[tuple[MemoryRow, float]]],
    base_url: str,
    api_key: str,
    model: str,
) -> list[Op]:
    raw = await chat_complete(
        base_url=base_url,
        api_key=api_key,
        model=model,
        system=RESOLVE_SYSTEM,
        user=_resolve_prompt(candidates, similar),
    )
    data = parse_json_object(raw)
    if not data:
        return [
            Op(action="ADD", content=c.content, type=c.type, importance=c.importance)
            for c in candidates
        ]
    raw_ops = data.get("ops") or []
    ops: list[Op] = []
    for idx, cand in enumerate(candidates):
        item = raw_ops[idx] if idx < len(raw_ops) and isinstance(raw_ops[idx], dict) else None
        ops.append(_resolve_op(item, cand))
    return ops

async def consolidate_group(
    *,
    contents: list[str],
    base_url: str,
    api_key: str,
    model: str,
) -> tuple[str, float] | None:
    lines = [f"{i + 1}. {c}" for i, c in enumerate(contents)]
    user = "\n".join(lines)
    # P1-1: 优先走 instructor 结构化通道（schema 强校验 + 自动 repair），
    # 失败再回退到老的 chat_complete + parse_json_object 兜底，保证旧路径可用。
    try:
        result = await structured_complete(
            _ConsolidateResult,
            system=CONSOLIDATE_SYSTEM,
            user=user,
            model=model,
            base_url=base_url,
            api_key=api_key,
            max_retries=2,
        )
        content = (result.content or "").strip()
        if not content:
            return None
        importance = max(1.0, min(10.0, float(result.importance)))
        return content, importance
    except StructuredOutputError as exc:
        logger.info(
            "consolidate_structured_falling_back err=%s", str(exc)[:160]
        )
    raw = await chat_complete(
        base_url=base_url,
        api_key=api_key,
        model=model,
        system=CONSOLIDATE_SYSTEM,
        user=user,
    )
    data = parse_json_object(raw)
    if not data:
        return None
    content = str(data.get("content") or "").strip()
    if not content:
        return None
    try:
        importance = float(data.get("importance", 5))
    except (TypeError, ValueError):
        importance = 5.0
    return content, max(1.0, min(10.0, importance))
