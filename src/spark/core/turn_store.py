"""Turn 级检查点存储（A10：in-flight turn 崩溃恢复的基础设施）。

一个 ``TurnCheckpointer`` 实例 = 一个 AsyncSqliteSaver + 一张 ``pending_turns``
登记表，同库共存（默认 ``$SPARK_HOME/checkpoints.db``）：

- **checkpoints/writes**：LangGraph 官方表（AsyncSqliteSaver 自管），保存
  挂起 turn 的完整轮次状态（含审批 interrupt 位点）。
- **pending_turns**：自建索引表——turn 开始登记、正常结束/错误清除、
  崩溃/审批挂起保留。恢复 API 以此为事实来源（避免依赖 LangGraph 内部表结构）。

线程/并发约束：AsyncSqliteSaver 内部有锁，AgentLoop 层面单会话 turn 串行
（session.busy 门），多会话各持各的库连接；本封装不做额外并发控制。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from spark.config import default_home

logger = logging.getLogger(__name__)

__all__ = [
    "TurnCheckpointer",
    "MemoryTurnCheckpointer",
    "persistent_checkpointer",
]

def _checkpoint_serde() -> JsonPlusSerializer:
    """checkpoint 序列化器：显式登记 spark 私有类型的 msgpack 白名单。

    turn 状态（AgentTurnState）里的 ``tool_calls`` 是 ``spark.models.ToolCall``
    实例。当前版本反序列化未登记类型只打 WARNING；上游已声明未来版本将默认
    阻断——而且阻断方式是**静默降级成普通 dict**（不抛异常），下游按属性访问
    ToolCall 会炸出难以定位的 AttributeError，且只在崩溃恢复/审批挂起续跑这条
    低频路径上显形。提前登记白名单，升级即安全，也消除日志噪音。
    """
    return JsonPlusSerializer(
        allowed_msgpack_modules=[("spark.models", "ToolCall")]
    )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_turns (
    thread_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    workdir TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


class TurnCheckpointer:
    """持久 turn 检查点：AsyncSqliteSaver + pending_turns 索引。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Any = None
        self._saver: AsyncSqliteSaver | None = None

    @property
    def saver(self) -> AsyncSqliteSaver:
        if self._saver is None:
            raise RuntimeError("TurnCheckpointer.start() 未调用")
        return self._saver

    async def start(self) -> None:
        """建立连接并初始化表（幂等；连接生命周期归本实例管理）。"""
        import aiosqlite

        if self._saver is not None:
            return
        # aiosqlite<0.22：langgraph-checkpoint-sqlite 依赖 Thread.is_alive()
        self._conn = await aiosqlite.connect(str(self.db_path))
        # Match the session store's durability profile (WAL + NORMAL): every turn
        # writes ~7 checkpoint rows, and the SQLite default (FULL) costs ~19 ms
        # per turn in fsyncs for zero recovery benefit on a local agent.
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        # Keep the WAL from pinning ~4 MB forever in a long-lived web process.
        await self._conn.execute("PRAGMA wal_autocheckpoint=64")
        self._saver = AsyncSqliteSaver(self._conn, serde=_checkpoint_serde())
        await self._saver.setup()
        async with self._conn.executescript(_SCHEMA):
            await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:  # pragma: no cover - best effort
                logger.debug("wal checkpoint failed", exc_info=True)
            try:
                await self._conn.close()
            except Exception:  # pragma: no cover
                logger.debug("checkpoint db close failed", exc_info=True)
            self._conn = None
            self._saver = None

    # -- pending turn 登记 ---------------------------------------------------

    async def register(self, thread_id: str, session_id: str, workdir: str, model: str) -> None:
        assert self._conn is not None, "start() 未调用"
        await self._conn.execute(
            "INSERT OR REPLACE INTO pending_turns"
            " (thread_id, session_id, workdir, model, created_at) VALUES (?,?,?,?,?)",
            (thread_id, session_id, workdir, model, time.time()),
        )
        await self._conn.commit()

    async def unregister(self, thread_id: str) -> None:
        """turn 终态（正常/错误/丢弃）后清除登记 + 删除 checkpoint。"""
        assert self._conn is not None, "start() 未调用"
        await self._conn.execute(
            "DELETE FROM pending_turns WHERE thread_id = ?", (thread_id,)
        )
        await self._conn.commit()
        if self._saver is not None:
            try:
                await self._saver.adelete_thread(thread_id)
            except Exception:  # pragma: no cover
                logger.debug("delete_thread failed: %s", thread_id, exc_info=True)

    async def list_pending(self) -> list[dict[str, Any]]:
        assert self._conn is not None, "start() 未调用"
        cursor = await self._conn.execute(
            "SELECT thread_id, session_id, workdir, model, created_at"
            " FROM pending_turns ORDER BY created_at"
        )
        rows = await cursor.fetchall()
        return [
            {
                "thread_id": r[0],
                "session_id": r[1],
                "workdir": r[2],
                "model": r[3],
                "created_at": r[4],
            }
            for r in rows
        ]


class MemoryTurnCheckpointer:
    """进程内 turn 检查点（子代理 / 短生命周期 CLI）：接口同 TurnCheckpointer。

    MemorySaver 无持久化——"崩溃恢复"退化为同进程内的审批挂起恢复，
    接口语义保持一致，调用方无需区分。
    """

    def __init__(self) -> None:
        from langgraph.checkpoint.memory import MemorySaver

        self._saver = MemorySaver()
        self._pending: dict[str, dict[str, Any]] = {}

    @property
    def saver(self) -> Any:
        return self._saver

    async def start(self) -> None:  # noqa: D102 — 接口同 TurnCheckpointer
        return None

    async def close(self) -> None:
        return None

    async def register(self, thread_id: str, session_id: str, workdir: str, model: str) -> None:
        self._pending[thread_id] = {
            "thread_id": thread_id,
            "session_id": session_id,
            "workdir": workdir,
            "model": model,
            "created_at": time.time(),
        }

    async def unregister(self, thread_id: str) -> None:
        self._pending.pop(thread_id, None)

    async def list_pending(self) -> list[dict[str, Any]]:
        return sorted(self._pending.values(), key=lambda r: r["created_at"])


_CHECKPOINTER: TurnCheckpointer | None = None
_CHECKPOINTER_PATH: Path | None = None


def persistent_checkpointer(db_path: Path | str | None = None) -> TurnCheckpointer | None:
    """Process-wide durable turn checkpointer, started on the agent runtime loop.

    Surfaces that drive turns through :mod:`spark.core.runtime` (web / TUI /
    ACP) share one connection: the saver's aiosqlite connection is bound to the
    runtime loop, so it must be created and started there.  Returns None when
    the durable store cannot be opened — callers then fall back to the
    in-memory saver and lose only crash recovery, not the approval flow.

    The target path is re-resolved on every call so a changed ``SPARK_HOME``
    (tests, or a user switching homes) gets its own database instead of
    silently reusing the previous one.
    """
    global _CHECKPOINTER, _CHECKPOINTER_PATH
    target = Path(db_path) if db_path is not None else default_home() / "checkpoints.db"
    if _CHECKPOINTER is not None and _CHECKPOINTER_PATH == target:
        return _CHECKPOINTER
    if _CHECKPOINTER is not None and _CHECKPOINTER_PATH != target:
        reset_persistent_checkpointer()
    try:
        from spark.core.runtime import get_runtime

        checkpointer = TurnCheckpointer(target)
        get_runtime().run(checkpointer.start(), timeout=15)
        _CHECKPOINTER = checkpointer
        _CHECKPOINTER_PATH = target
        return checkpointer
    except Exception:
        logger.warning("durable turn checkpointer unavailable", exc_info=True)
        return None


def reset_persistent_checkpointer() -> None:
    """Drop the cached checkpointer (used by tests and by home switching)."""
    global _CHECKPOINTER, _CHECKPOINTER_PATH
    checkpointer = _CHECKPOINTER
    _CHECKPOINTER = None
    _CHECKPOINTER_PATH = None
    if checkpointer is None:
        return
    try:
        from spark.core.runtime import get_runtime

        get_runtime().run(checkpointer.close(), timeout=10)
    except Exception:  # pragma: no cover - best effort
        logger.debug("checkpointer close failed", exc_info=True)
