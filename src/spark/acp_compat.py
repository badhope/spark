"""Small ACP schema builders kept locally after agent-client-protocol 1.0.

The upstream ``acp.helpers`` module was removed in 1.0; these helpers are
thin wrappers over the still-supported ``acp.schema`` models so Spark's ACP
surface keeps the same wire format without depending on a deleted import.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    ContentToolCallContent,
    PlanEntry,
    ResourceContentBlock,
    TextContentBlock,
    UserMessageChunk,
)


def text_block(text: str) -> TextContentBlock:
    return TextContentBlock(type="text", text=text)


def resource_link_block(
    name: str,
    uri: str,
    *,
    mime_type: str | None = None,
    size: int | None = None,
    description: str | None = None,
    title: str | None = None,
) -> ResourceContentBlock:
    return ResourceContentBlock(
        type="resource_link",
        name=name,
        uri=uri,
        mime_type=mime_type,
        size=size,
        description=description,
        title=title,
    )


def plan_entry(
    content: str,
    *,
    priority: str = "medium",
    status: str = "pending",
) -> PlanEntry:
    return PlanEntry(
        content=content,
        priority=priority,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
    )


def tool_content(block: Any) -> ContentToolCallContent:
    return ContentToolCallContent(type="content", content=block)


def update_user_message(content: Any) -> UserMessageChunk:
    return UserMessageChunk(session_update="user_message_chunk", content=content)


def update_agent_message(content: Any) -> AgentMessageChunk:
    return AgentMessageChunk(session_update="agent_message_chunk", content=content)


def update_agent_thought(content: Any) -> AgentThoughtChunk:
    return AgentThoughtChunk(session_update="agent_thought_chunk", content=content)


def update_plan(entries: Iterable[PlanEntry]) -> AgentPlanUpdate:
    return AgentPlanUpdate(session_update="plan", entries=list(entries))
