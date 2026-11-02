from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ApprovalAction = Literal["allow", "deny", "allow_always"]
MessageRole = Literal["system", "user", "assistant", "tool", "summary"]
TurnEventType = Literal[
    "text_delta",
    "reasoning_delta",
    "tool_start",
    "tool_end",
    "approval_needed",
    "compaction",
    "context",
    "plan",
    # Sub-agent lifecycle. Without these the execution graph could only draw the
    # main agent and its own tools; a fan-out looked like a flat tool call.
    "subtask_start",
    "subtask_end",
    # Per-child progress/trace. The parent renders one bubble, so these carry
    # counters (chars, tool count) rather than the child's raw stream.
    "subtask_progress",
    "subtask_trace",
    # Interactive question: the agent asks the user and blocks until answered.
    "question",
    # Automatic review outcome (Guardian-style). Carries the verdict so the UI
    # can show "this action was reviewed / flagged" instead of it happening
    # invisibly — a silent reviewer is indistinguishable from no reviewer.
    "review",
    "turn_end",
    "turn_error",
]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ImageRef(BaseModel):
    media_type: str = "image/png"
    data: str  # base64


class ChatMessage(BaseModel):
    role: MessageRole
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    images: list[ImageRef] | None = None


class ChatDelta(BaseModel):
    type: Literal["text", "reasoning", "tool_call", "end"]
    text: str | None = None
    tool_call: ToolCall | None = None
    # P1：end 帧可携带 provider 上报的真实 usage（prompt/completion/reasoning tokens），
    # AgentLoop 回填 token_usage 表（source=reported），无上报时仍走估算。
    usage: dict[str, int] | None = None


class ToolResult(BaseModel):
    ok: bool
    payload: dict[str, Any]


class ApprovalDecision(BaseModel):
    tool_call_id: str
    action: ApprovalAction


class ApprovalRequest(BaseModel):
    tool_call: ToolCall
    summary: str
    diff: str | None = None


class TurnEvent(BaseModel):
    type: TurnEventType
    text: str | None = None
    tool_call: ToolCall | None = None
    result: ToolResult | None = None
    approval: ApprovalRequest | None = None
    data: dict[str, Any] | None = None
