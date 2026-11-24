"""P2 上下文合同：turn 级重放记录（replay.jsonl，schema ``spark.replay/1``）。

每个 turn 归一化为一行 JSON，追加写 ``$SPARK_HOME/replays/<session_id>.jsonl``。
与 OTel/Langfuse 观测双轨：OTel 面向指标与分布式追踪，replay 面向
"这个 turn 到底发生了什么"的可回放合同——重放视图、回归对比、事故复盘
都以它为单一事实来源。

schema（v1，逐字段向后兼容追加，解析端必须容忍未知键）::

    {
      "schema": "spark.replay/1",
      "recorded_at": 1760000000,          # 行写入时间（epoch 秒）
      "session_id": "abc123",
      "workdir": "/abs/path",
      "model": "deepseek-chat",
      "turn": {
        "index": 3,                       # 会话内第几个记录的 turn（0 起）
        "prompt": "用户输入",
        "images": 0,                      # 附件数（不存内容，控体积/隐私）
        "resumed": false,                 # 崩溃恢复续跑的 turn
        "started_at": 1760000000.0,
        "duration_ms": 1234,
        "status": "ok | error | cancelled",
        "error": null,
        "compaction": {"before_tokens": .., "after_tokens": ..} | null,
        "usage": {"input_tokens": .., "output_tokens": ..,
                  "reasoning_tokens": .., "source": "reported|estimated"},
        "context_after": {"used": .., "limit": ..},
        "messages_after": 12,             # turn 结束后的历史长度
        "checkpoint_id": 7 | null,        # 自动检查点（write/shell 类回合）
        "text": "助手最终文本（delta 拼接）",
        "reasoning": "思考流（delta 拼接）",
        "tool_calls": [                   # 按 start/end 配对的完整工具轨迹
          {"id": .., "name": .., "arguments": {..},
           "ok": true, "result": {..},
           "approval": {"summary": .., "decision": "allow"} | null}
        ],
        "plan": [..] | null,
        "approvals": [ {"tool": .., "summary": .., "decision": "allow"} ]
      }
    }

设计约束：记录器**永不阻塞主循环**——任何异常降级为 log warning；
``spark replay`` CLI 与 Web 回放视图是本文件的唯一两个消费方。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from spark.config import default_home
from spark.models import ApprovalDecision, ApprovalRequest, TurnEvent

logger = logging.getLogger(__name__)

SCHEMA_ID = "spark.replay/1"
MAX_FIELD_CHARS = 200_000  # 单字段上限：防异常超大读结果撑爆单行


def replay_dir(home: Path | None = None) -> Path:
    return (home or default_home()) / "replays"


def replay_path(session_id: str, home: Path | None = None) -> Path:
    return replay_dir(home) / f"{session_id}.jsonl"


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + "\n...truncated..."
    return value


class TurnReplayRecorder:
    """观察单次 turn 的 TurnEvent 流，归一化为一行 JSON 记录。

    生命周期：turn 开始时创建 → 逐事件 :meth:`observe` → 审批决策经
    :meth:`record_approval`（AgentLoop 包裹 approver 注入）→ turn 终态
    :meth:`flush` 落盘。任何一步失败都只记日志。
    """

    def __init__(
        self,
        *,
        session_id: str,
        workdir: str,
        model: str,
        prompt: str,
        image_count: int = 0,
        resumed: bool = False,
        home: Path | None = None,
    ) -> None:
        self._session_id = session_id
        self._workdir = workdir
        self._model = model
        self._prompt = prompt
        self._image_count = image_count
        self._resumed = resumed
        self._home = home
        self._started = time.time()
        self._text_parts: list[str] = []
        self._reasoning_parts: list[str] = []
        self._tools: list[dict[str, Any]] = []
        self._tool_index: dict[str, int] = {}
        self._plan: list[dict[str, Any]] | None = None
        self._approvals: list[dict[str, Any]] = []
        self._compaction: dict[str, Any] | None = None
        self._usage: dict[str, Any] | None = None
        self._context_after: dict[str, Any] | None = None
        self._status = "ok"
        self._error: str | None = None

    # -- 观测 ----------------------------------------------------------------

    def _append_tool(self, event: TurnEvent) -> int:
        call = event.tool_call
        if call is None:
            return -1
        idx = len(self._tools)
        self._tool_index[call.id] = idx
        self._tools.append(
            {
                "id": call.id,
                "name": call.name,
                "arguments": call.arguments,
                "ok": None,
                "result": None,
                "approval": None,
            }
        )
        return idx

    def _record_tool_start(self, event: TurnEvent) -> None:
        self._append_tool(event)

    def _record_tool_end(self, event: TurnEvent) -> None:
        call = event.tool_call
        if call is None:
            return
        idx = self._tool_index.get(call.id)
        if idx is None or idx < 0:
            # resume 重放或旧记录：追加为独立条目，保证不丢
            idx = self._append_tool(event)
        self._tools[idx]["ok"] = bool(event.result.ok) if event.result else None
        self._tools[idx]["result"] = event.result.payload if event.result else None

    def _record_approval_event(self, event: TurnEvent) -> None:
        if event.approval is None:
            return
        self._approvals.append(
            {
                "tool": event.tool_call.name if event.tool_call else None,
                "summary": event.approval.summary,
                "decision": None,  # 决策经 record_approval 回填
            }
        )

    def _record_metric_event(self, event: TurnEvent) -> None:
        data = event.data
        if data is None:
            return
        if event.type == "compaction":
            self._compaction = {
                "before_tokens": data.get("before_tokens"),
                "after_tokens": data.get("after_tokens"),
                "summarized_messages": data.get("summarized_messages"),
            }
            return
        usage = data.get("usage")
        if isinstance(usage, dict):
            self._context_after = {"used": usage.get("used"), "limit": usage.get("limit")}

    def observe(self, event: TurnEvent) -> None:
        try:
            etype = event.type
            if etype == "text_delta" and event.text:
                self._text_parts.append(event.text)
            elif etype == "reasoning_delta" and event.text:
                self._reasoning_parts.append(event.text)
            elif etype == "tool_start" and event.tool_call is not None:
                self._record_tool_start(event)
            elif etype == "tool_end" and event.tool_call is not None:
                self._record_tool_end(event)
            elif etype == "plan" and isinstance(event.data, dict):
                self._plan = event.data.get("steps") or []
            elif etype == "approval_needed":
                self._record_approval_event(event)
            elif etype in {"compaction", "context"} and isinstance(event.data, dict):
                self._record_metric_event(event)
            elif etype == "turn_error":
                self._status = (
                    "cancelled" if "cancel" in (event.text or "").lower() else "error"
                )
                self._error = event.text
        except Exception:  # pragma: no cover — 观测永不阻塞主循环
            logger.debug("replay observe failed", exc_info=True)
    def record_approval(
        self, request: ApprovalRequest, decision: ApprovalDecision
    ) -> None:
        """回填最近的同工具待审批条目（无则新增一条，保事件完整性）。"""
        try:
            target = None
            for item in reversed(self._approvals):
                if item.get("decision") is None and item.get("tool") in (
                    None,
                    request.tool_call.name,
                ):
                    target = item
                    break
            if target is None:
                target = {"tool": request.tool_call.name, "summary": request.summary}
                self._approvals.append(target)
            target["decision"] = decision.action
        except Exception:  # pragma: no cover
            logger.debug("replay record_approval failed", exc_info=True)

    # -- 落盘 ----------------------------------------------------------------

    def flush(
        self,
        *,
        usage: dict[str, Any] | None = None,
        context_after: dict[str, Any] | None = None,
        messages_after: int = 0,
        checkpoint_id: int | None = None,
    ) -> dict[str, Any] | None:
        """归一化并追加写入一行；返回写入的记录（失败返回 None）。"""
        if usage is None:
            usage = self._usage
        if context_after is None:
            context_after = self._context_after
        record: dict[str, Any] = {
            "schema": SCHEMA_ID,
            "recorded_at": int(time.time()),
            "session_id": self._session_id,
            "workdir": self._workdir,
            "model": self._model,
            "turn": {
                "index": self._next_index(),
                "prompt": _clip(self._prompt),
                "images": self._image_count,
                "resumed": self._resumed,
                "started_at": self._started,
                "duration_ms": int((time.time() - self._started) * 1000),
                "status": self._status,
                "error": _clip(self._error),
                "compaction": self._compaction,
                "usage": usage,
                "context_after": context_after,
                "messages_after": messages_after,
                "checkpoint_id": checkpoint_id,
                "text": _clip("".join(self._text_parts)),
                "reasoning": _clip("".join(self._reasoning_parts)),
                "tool_calls": self._tools,
                "plan": self._plan,
                "approvals": self._approvals,
            },
        }
        try:
            path = replay_path(self._session_id, self._home)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            return record
        except OSError:
            logger.warning("replay 写入失败 session=%s", self._session_id, exc_info=True)
            return None

    def _next_index(self) -> int:
        try:
            path = replay_path(self._session_id, self._home)
            if not path.is_file():
                return 0
            with path.open("rb") as fh:
                return sum(1 for _ in fh)
        except OSError:
            return 0


def load_replays(
    session_id: str, *, home: Path | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    """读取一个会话的全部（或最近 *limit* 条）重放记录；坏行跳过。"""
    path = replay_path(session_id, home)
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("replay 坏行跳过 session=%s", session_id)
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except OSError:
        logger.warning("replay 读取失败 session=%s", session_id, exc_info=True)
    return records[-limit:]


def list_replay_sessions(home: Path | None = None) -> list[dict[str, Any]]:
    """列出有重放记录的会话（按最近写入排序）。"""
    directory = replay_dir(home)
    if not directory.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in directory.glob("*.jsonl"):
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append(
            {
                "session_id": path.stem,
                "updated_at": int(stat.st_mtime),
                "bytes": stat.st_size,
            }
        )
    out.sort(key=lambda item: -item["updated_at"])
    return out


__all__ = [
    "SCHEMA_ID",
    "TurnReplayRecorder",
    "list_replay_sessions",
    "load_replays",
    "replay_dir",
    "replay_path",
]
