"""SQLite-backed session store with WAL mode and schema migrations."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from spark.models import ChatMessage, ToolCall, ToolResult


class SessionStore:
    def __init__(self, db_path: Path | str) -> None:
        # 修复：统一用 str() 比较，避免 Path(":memory:") != ":memory:" (str) 判空失效
        db_path = (
            Path(db_path)
            if isinstance(db_path, str) and db_path != ":memory:"
            else db_path
        )
        if isinstance(db_path, Path) and str(db_path) == ":memory:":
            # Path(":memory:") 不应触发父目录创建，转回字符串统一处理
            db_path = ":memory:"
        if isinstance(db_path, Path):
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    workdir TEXT NOT NULL,
                    model TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    title TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    created_at INTEGER NOT NULL,
                    payload_json TEXT,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );
                CREATE TABLE IF NOT EXISTS tool_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    message_id INTEGER,
                    name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    result_json TEXT,
                    approval TEXT,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bg_jobs (
                    id TEXT PRIMARY KEY,
                    command TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    exit_code INTEGER,
                    output TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS token_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    message_id INTEGER,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT 'estimated',
                    created_at INTEGER NOT NULL
                );
                """
            )
            # FTS5 trigram 全文索引（中文子串检索；短查询回退 LIKE）
            # 注意：FTS5 rowid 必须是整数，sessions.id 为 TEXT，故用 UNINDEXED 列承载。
            try:
                self._conn.executescript(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                        USING fts5(content, tokenize='trigram');
                    CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts
                        USING fts5(session_id UNINDEXED, title, tokenize='trigram');
                    """
                )
            except sqlite3.OperationalError:
                # SQLite < 3.34 无 trigram：降级，search() 自动回退 LIKE
                self._conn.executescript(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                        USING fts5(content);
                    CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts
                        USING fts5(session_id UNINDEXED, title);
                    """
                )
            # WAL + composite indexes: long sessions otherwise degrade to full
            # table scans on every load and pay two fsyncs per appended message.
            # 修复：用 str() 统一比较，防止 Path(":memory:") != ":memory:" 误判
            if str(self.db_path) != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_session_id
                    ON messages(session_id, id);
                CREATE INDEX IF NOT EXISTS idx_tool_events_session_message
                    ON tool_events(session_id, message_id);
                CREATE INDEX IF NOT EXISTS idx_checkpoints_session_id
                    ON checkpoints(session_id, id);
                CREATE INDEX IF NOT EXISTS idx_sessions_updated_at
                    ON sessions(updated_at DESC);
                """
            )
            # Schema version. The ad-hoc ALTERs below are idempotent, but without
            # a recorded version there is no way to tell an old database from a
            # half-migrated one — and no place to hang future migrations.
            self._migrate_schema()
            try:
                self._conn.execute(
                    "ALTER TABLE sessions ADD COLUMN compact_from INTEGER"
                )
            except sqlite3.OperationalError:
                pass
            try:
                self._conn.execute("ALTER TABLE sessions ADD COLUMN keywords TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                self._conn.execute(
                    "ALTER TABLE sessions ADD COLUMN forked_from TEXT"
                )
            except sqlite3.OperationalError:
                pass
            # 存量数据一次性回填 FTS 索引（幂等）
            self._conn.executescript(
                """
                INSERT INTO messages_fts (rowid, content)
                SELECT id, content FROM messages m
                WHERE content IS NOT NULL AND content != ''
                  AND NOT EXISTS (SELECT 1 FROM messages_fts f WHERE f.rowid = m.id);
                INSERT INTO sessions_fts (session_id, title)
                SELECT id, title FROM sessions s
                WHERE NOT EXISTS (SELECT 1 FROM sessions_fts f WHERE f.session_id = s.id);
                """
            )
            self._conn.commit()

    #: Bump when a step in :meth:`_migrate_schema` is added.
    SCHEMA_VERSION = 1

    def _migrate_schema(self) -> None:
        """Record/upgrade the schema version on *this* database.

        Steps must be idempotent: an older database reports ``user_version = 0``
        and re-runs everything, which the ALTER/CREATE statements tolerate.
        """
        current = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        if current >= self.SCHEMA_VERSION:
            return
        for version, step in ((1, self._migrate_v1),):
            if current < version:
                step()
                current = version
        self._conn.execute(f"PRAGMA user_version = {int(current)}")
        self._conn.commit()

    def _migrate_v1(self) -> None:
        """v1: the columns/indexes created by the legacy inline ALTERs.

        They are already applied above by :meth:`_init`; this hook exists so the
        version is recorded and future steps have a place to live.
        """

    def backup_to(self, target: Path | str) -> Path:
        """Write a consistent snapshot of this database to *target*.

        ``VACUUM INTO`` produces a single-file copy without the ``-wal`` sidecar,
        so it is safe to take while the app is running (unlike a plain file copy).
        """
        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists():
            target_path.unlink()
        with self._lock:
            self._conn.execute("VACUUM INTO ?", (str(target_path),))
        return target_path

    def schema_version(self) -> int:
        with self._lock:
            return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def _fts_sync_message(self, message_id: int, content: str | None) -> None:
        """Keep messages_fts aligned with the messages table (best-effort)."""
        try:
            self._conn.execute("DELETE FROM messages_fts WHERE rowid = ?", (message_id,))
            if content:
                self._conn.execute(
                    "INSERT INTO messages_fts (rowid, content) VALUES (?, ?)",
                    (message_id, content),
                )
        except sqlite3.OperationalError:
            pass

    def _fts_sync_title(self, session_id: str, title: str | None) -> None:
        """Keep sessions_fts aligned with the sessions table (best-effort)."""
        try:
            self._conn.execute(
                "DELETE FROM sessions_fts WHERE session_id = ?", (session_id,)
            )
            if title:
                self._conn.execute(
                    "INSERT INTO sessions_fts (session_id, title) VALUES (?, ?)",
                    (session_id, title),
                )
        except sqlite3.OperationalError:
            pass

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_session(self, workdir: Path, model: str, title: str = "untitled") -> str:
        """Create a new session row and return its id."""
        sid = uuid.uuid4().hex[:12]
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (id, workdir, model, created_at, updated_at, title) VALUES (?, ?, ?, ?, ?, ?)",
                (sid, str(workdir.resolve()), model, now, now, title),
            )
            # 新会话必须立刻进标题索引：此前只靠启动时的 backfill 兜底，
            # 本次进程内新建的会话标题在重启前一直搜不到。
            self._fts_sync_title(sid, title)
            self._conn.commit()
        return sid

    def list_sessions(self) -> list[dict]:
        """Return all sessions ordered by most-recently-updated first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, workdir, model, created_at, updated_at, title, keywords FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> dict | None:
        """Fetch a single session dict, or None if the id is unknown."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete_session(self, session_id: str) -> None:
        """Remove a session and its messages/tool-events permanently."""
        with self._lock:
            # FTS rows must be deleted *before* the messages they index: the
            # old order ran the subquery after the DELETE, so it always matched
            # nothing and left orphan rows behind.
            try:
                self._conn.execute(
                    "DELETE FROM messages_fts WHERE rowid IN "
                    "(SELECT id FROM messages WHERE session_id = ?)",
                    (session_id,),
                )
                self._conn.execute(
                    "DELETE FROM sessions_fts WHERE session_id = ?", (session_id,)
                )
            except sqlite3.OperationalError:
                pass
            self._conn.execute(
                "DELETE FROM tool_events WHERE session_id = ?", (session_id,)
            )
            self._conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            try:
                self._conn.execute(
                    "DELETE FROM token_usage WHERE session_id = ?", (session_id,)
                )
            except sqlite3.OperationalError:
                pass
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()

    def touch(self, session_id: str, title: str | None = None) -> None:
        """Update the session timestamp (and optionally its title)."""
        now = int(time.time())
        with self._lock:
            if title is None:
                # sessions.title is NOT NULL: a plain timestamp bump must not
                # write NULL into it.
                self._conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (now, session_id),
                )
            else:
                self._conn.execute(
                    "UPDATE sessions SET updated_at = ?, title = ? WHERE id = ?",
                    (now, title, session_id),
                )
                self._fts_sync_title(session_id, title)
            self._conn.commit()

    def set_title(self, session_id: str, title: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ?", (title, session_id)
            )
            self._fts_sync_title(session_id, title)
            self._conn.commit()

    def set_keywords(self, session_id: str, keywords: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET keywords = ? WHERE id = ?", (keywords, session_id)
            )
            self._conn.commit()

    def append_message(self, session_id: str, message: ChatMessage) -> int:
        """Persist a message and bump the session timestamp; return the new row id."""
        now = int(time.time())
        payload = {
            "tool_calls": [c.model_dump() for c in message.tool_calls]
            if message.tool_calls
            else None,
            "tool_call_id": message.tool_call_id,
            "name": message.name,
            "images": [i.model_dump() for i in message.images]
            if message.images
            else None,
        }
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at, payload_json) VALUES (?, ?, ?, ?, ?)",
                (session_id, message.role, message.content, now, json.dumps(payload)),
            )
            last_id = int(cur.lastrowid or 0)
            # 自动标题：首个 user 消息取前 24 字，替代 "untitled"（与会话搜索的标题命中配套）
            retitled = False
            if message.role == "user" and message.content:
                retitled = (
                    self._conn.execute(
                        "UPDATE sessions SET title = ? WHERE id = ? AND title = 'untitled'",
                        (message.content.strip()[:24], session_id),
                    ).rowcount
                    > 0
                )
            self._fts_sync_message(last_id, message.content)
            # 只有真的改过标题才同步 FTS。此前无条件写入消息前 24 字，把用户
            # 手改的标题在索引里覆盖掉——标题命中因此永远搜不到自定义标题。
            if retitled and message.content:
                self._fts_sync_title(session_id, message.content.strip()[:24])
            # The message and its session timestamp must land together, otherwise a
            # crash between the two commits leaves a message with stale ordering.
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
            )
            self._conn.commit()
        return last_id

    def append_tool_event(
        self,
        session_id: str,
        message_id: int | None,
        name: str,
        arguments: dict,
        result: ToolResult | None,
        approval: str | None,
    ) -> None:
        """Record one tool invocation (name, arguments, result, approval)."""
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                """INSERT INTO tool_events
                   (session_id, message_id, name, arguments_json, result_json, approval, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    message_id,
                    name,
                    json.dumps(arguments),
                    json.dumps(result.model_dump()) if result else None,
                    approval,
                    now,
                ),
            )
            self._conn.commit()

    def list_tool_events(self, session_id: str, limit: int = 500) -> list[dict]:
        """Tool invocations for a session, oldest first (export/audit use)."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, message_id, name, arguments_json, result_json, approval, created_at
                   FROM tool_events WHERE session_id = ? ORDER BY id LIMIT ?""",
                (session_id, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_compact_from(self, session_id: str, message_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET compact_from = ? WHERE id = ?",
                (message_id, session_id),
            )
            self._conn.commit()

    def get_compact_from(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT compact_from FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            return int(row["compact_from"]) if row and row["compact_from"] else 0

    def load_message_ids(self, session_id: str) -> list[int]:
        """Row ids of exactly the messages :meth:`load_messages` would return.

        Compaction needs this to store the cursor as *the id of the first
        retained message*; the in-memory history and the table must be index
        aligned for that to be safe, so the caller compares lengths.
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT id FROM messages
                   WHERE session_id = ?
                     AND id >= COALESCE((SELECT compact_from FROM sessions WHERE id = ?), 0)
                   ORDER BY id""",
                (session_id, session_id),
            ).fetchall()
        return [int(row["id"]) for row in rows]

    def load_messages(self, session_id: str) -> list[ChatMessage]:
        """Return messages for a session, honouring the compact-from cut-off."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, role, content, payload_json FROM messages
                   WHERE session_id = ?
                     AND id >= COALESCE((SELECT compact_from FROM sessions WHERE id = ?), 0)
                   ORDER BY id""",
                (session_id, session_id),
            ).fetchall()
        messages: list[ChatMessage] = []
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            # 修复：json.loads 可能解析出 list/str/number，需校验为 dict 才可 .get()
            if not isinstance(payload, dict):
                payload = {}
            tool_calls = None
            if payload.get("tool_calls"):
                tool_calls = [ToolCall.model_validate(c) for c in payload["tool_calls"]]
            images = None
            if payload.get("images"):
                from spark.models import ImageRef

                images = [ImageRef.model_validate(i) for i in payload["images"]]
            messages.append(
                ChatMessage(
                    role=row["role"],
                    content=row["content"],
                    tool_calls=tool_calls,
                    tool_call_id=payload.get("tool_call_id"),
                    name=payload.get("name"),
                    images=images,
                )
            )
        return messages

    # ---- checkpoints ----

    def add_checkpoint(
        self, session_id: str, label: str, message_id: int, snapshot_json: str
    ) -> int:
        now = int(time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO checkpoints (session_id, label, message_id, snapshot_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, label, message_id, snapshot_json, now),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def list_checkpoints(self, session_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, label, message_id, created_at FROM checkpoints WHERE session_id = ? ORDER BY id DESC LIMIT 50",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_checkpoint(self, session_id: str, checkpoint_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM checkpoints WHERE session_id = ? AND id = ?",
                (session_id, checkpoint_id),
            ).fetchone()
            return dict(row) if row else None

    def nearest_checkpoint_at_or_before(
        self, session_id: str, message_id: int
    ) -> dict | None:
        """Latest checkpoint whose message_id <= *message_id*.

        Message-level undo needs the file state as it was **before** the message
        being undone, so the checkpoint to restore is the one written at (or
        before) that message — not the newest one.
        """
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM checkpoints
                   WHERE session_id = ? AND message_id <= ?
                   ORDER BY message_id DESC, id DESC LIMIT 1""",
                (session_id, message_id),
            ).fetchone()
            return dict(row) if row else None

    def delete_messages_after(self, session_id: str, message_id: int) -> int:
        """Delete messages with id > message_id (and their tool events). Returns removed count."""
        with self._lock:
            # P0：FTS rowid 是全库自增的 messages.id，删除必须限定本会话的消息 id，
            # 否则单库多会话时会连带删掉其他会话消息的搜索索引。
            # 先删 FTS（子查询引用 messages），再删 messages 本体。
            try:
                self._conn.execute(
                    "DELETE FROM messages_fts WHERE rowid IN "
                    "(SELECT id FROM messages WHERE session_id = ? AND id > ?)",
                    (session_id, message_id),
                )
            except sqlite3.OperationalError:
                pass
            cur = self._conn.execute(
                "DELETE FROM messages WHERE session_id = ? AND id > ?",
                (session_id, message_id),
            )
            removed = cur.rowcount
            self._conn.execute(
                "DELETE FROM tool_events WHERE session_id = ? AND message_id > ?",
                (session_id, message_id),
            )
            self._conn.commit()
        return removed

    # ---- background jobs ----

    def upsert_bg_job(
        self, job_id: str, command: str, cwd: str, started_at: float
    ) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO bg_jobs (id, command, cwd, started_at, finished_at, exit_code, output)
                   VALUES (?, ?, ?, ?, NULL, NULL, '')
                   ON CONFLICT(id) DO NOTHING""",
                (job_id, command, cwd, started_at),
            )
            self._conn.commit()

    def update_bg_job(
        self,
        job_id: str,
        *,
        output: str,
        finished_at: float | None,
        exit_code: int | None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE bg_jobs SET output = ?, finished_at = ?, exit_code = ? WHERE id = ?",
                (output, finished_at, exit_code, job_id),
            )
            self._conn.commit()

    def list_bg_jobs(self, running_only: bool = False) -> list[dict]:
        # Include `output`: callers listing jobs otherwise could not show the
        # output of a job that only exists in the store (after a restart), which
        # made `bg_list` and restart recovery disagree.
        sql = (
            "SELECT id, command, cwd, started_at, finished_at, exit_code, output "
            "FROM bg_jobs"
        )
        if running_only:
            sql += " WHERE finished_at IS NULL"
        sql += " ORDER BY started_at DESC LIMIT 50"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
            return [dict(r) for r in rows]

    def get_bg_job(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM bg_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return dict(row) if row else None

    # ---- token usage audit ----

    def append_token_usage(
        self,
        session_id: str,
        message_id: int | None,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int = 0,
        source: str = "estimated",
    ) -> None:
        """Record one LLM call's token spend (estimated unless provider reports)."""
        now = int(time.time())
        total = int(input_tokens) + int(output_tokens) + int(reasoning_tokens)
        with self._lock:
            self._conn.execute(
                """INSERT INTO token_usage
                   (session_id, message_id, input_tokens, output_tokens, reasoning_tokens, total_tokens, source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, message_id, input_tokens, output_tokens, reasoning_tokens, total, source, now),
            )
            self._conn.commit()

    def session_token_usage(self, session_id: str, model: str | None = None) -> dict:
        """Aggregated token spend for a session, plus estimated cost when known.

        Cost is derived from the models.dev catalog; an unknown model (a local
        Ollama model, a private deployment) yields ``cost_usd=None`` rather than
        a misleading zero.
        """
        with self._lock:
            row = self._conn.execute(
                """SELECT COALESCE(SUM(input_tokens), 0) AS input_tokens,
                          COALESCE(SUM(output_tokens), 0) AS output_tokens,
                          COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
                          COALESCE(SUM(total_tokens), 0) AS total_tokens,
                          COUNT(*) AS calls
                   FROM token_usage WHERE session_id = ?""",
                (session_id,),
            ).fetchone()
            usage = dict(row) if row else {
                "input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
                "calls": 0,
            }
        usage["cost_usd"] = self._estimate_cost(model, usage) if model else None
        return usage

    @staticmethod
    def _estimate_cost(model: str | None, usage: dict) -> float | None:
        """Best-effort USD estimate; None when the catalog has no price."""
        if not model:
            return None
        try:
            from spark.config import default_home
            from spark.model_catalog import _FALLBACK_SNAPSHOT, _load_cache, estimate_cost, lookup

            # Never hit the network from a status read: prefer the on-disk cache,
            # and fall back to the bundled snapshot. A missing price simply
            # yields None; a status call must not add a 20 s HTTP timeout.
            catalog = _load_cache(default_home()) or _FALLBACK_SNAPSHOT
            provider = model.split("/", 1)[0] if "/" in model else ""
            entry = lookup(catalog, provider, model) if provider else None
            if entry is None:
                # Bare model names (deepseek-chat) are listed under their vendor
                # entry; search every provider for an exact model id.
                for vendor, data in (catalog or {}).items():
                    if model in (data.get("models") or {}):
                        provider = vendor
                        break
            return estimate_cost(
                catalog,
                provider,
                model,
                int(usage.get("input_tokens") or 0),
                int(usage.get("output_tokens") or 0),
            )
        except Exception:
            return None

    # ---- fork / message ops / search ----

    def fork(self, session_id: str) -> dict | None:
        """从指定会话分叉出新会话：复制全部消息与工作目录，标题标注"分叉"。

        返回新会话 meta；源会话不存在返回 None。
        """
        with self._lock:
            src = self.get_session(session_id)
            if src is None:
                return None
            new_sid = uuid.uuid4().hex[:12]
            now = int(time.time())
            self._conn.execute(
                "INSERT INTO sessions (id, workdir, model, created_at, updated_at, title, keywords, forked_from)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_sid,
                    src["workdir"],
                    src["model"],
                    now,
                    now,
                    f"{src['title']}（分叉）",
                    src.get("keywords"),
                    session_id,
                ),
            )
            self._conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at, payload_json)"
                " SELECT ?, role, content, created_at, payload_json FROM messages WHERE session_id = ?",
                (new_sid, session_id),
            )
            try:
                self._conn.execute(
                    "INSERT INTO messages_fts (rowid, content)"
                    " SELECT id, content FROM messages WHERE session_id = ? AND content != ''",
                    (new_sid,),
                )
                self._conn.execute(
                    "INSERT INTO sessions_fts (session_id, title) VALUES (?, ?)",
                    (new_sid, f"{src['title']}（分叉）"),
                )
            except sqlite3.OperationalError:
                pass
            self._conn.commit()
            return self.get_session(new_sid)

    def truncate_messages(self, session_id: str, message_id: int) -> list[ChatMessage] | None:
        """截断：删除指定消息及其后全部消息（编辑重发用）。返回剩余消息；消息不存在返回 None。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM messages WHERE session_id = ? AND id = ?",
                (session_id, message_id),
            ).fetchone()
            if row is None:
                return None
            # P0：同 delete_messages_after——FTS 删除限定本会话，防止跨会话误删
            try:
                self._conn.execute(
                    "DELETE FROM messages_fts WHERE rowid IN "
                    "(SELECT id FROM messages WHERE session_id = ? AND id >= ?)",
                    (session_id, message_id),
                )
            except sqlite3.OperationalError:
                pass
            self._conn.execute(
                "DELETE FROM messages WHERE session_id = ? AND id >= ?",
                (session_id, message_id),
            )
            self._conn.execute(
                "DELETE FROM tool_events WHERE session_id = ? AND message_id >= ?",
                (session_id, message_id),
            )
            self._conn.commit()
        return self.load_messages(session_id)

    def delete_message(self, session_id: str, message_id: int) -> list[ChatMessage] | None:
        """删除单条消息（持久生效）。返回剩余消息；消息不存在返回 None。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM messages WHERE session_id = ? AND id = ?",
                (session_id, message_id),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "DELETE FROM messages WHERE session_id = ? AND id = ?",
                (session_id, message_id),
            )
            self._conn.execute(
                "DELETE FROM tool_events WHERE session_id = ? AND message_id = ?",
                (session_id, message_id),
            )
            try:
                self._conn.execute("DELETE FROM messages_fts WHERE rowid = ?", (message_id,))
            except sqlite3.OperationalError:
                pass
            self._conn.commit()
        return self.load_messages(session_id)

    def list_message_briefs(self, session_id: str, limit: int = 200) -> list[dict]:
        """时间轴用：消息 id/角色/预览/时间（honouring compact-from 截断）。"""
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, role, content, payload_json, created_at FROM messages
                   WHERE session_id = ?
                     AND id >= COALESCE((SELECT compact_from FROM sessions WHERE id = ?), 0)
                   ORDER BY id DESC LIMIT ?""",
                (session_id, session_id, max(1, int(limit))),
            ).fetchall()
        briefs: list[dict] = []
        for row in reversed(rows):
            content = row["content"] or ""
            name = ""
            if row["role"] == "assistant" and not content.strip():
                # A turn that only requested tools has no text; without a marker
                # the timeline row renders blank, which reads as a broken entry.
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                    calls = (payload.get("tool_calls") or []) if isinstance(payload, dict) else []
                except (TypeError, ValueError):
                    calls = []
                if calls:
                    names = [str(c.get("name") or "tool") for c in calls if isinstance(c, dict)]
                    content = "调用工具：" + "、".join(names)
            if row["role"] == "tool":
                # Tool rows hold the raw JSON result; the timeline is for humans,
                # so show the tool that ran rather than a wall of escaped JSON.
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                    if isinstance(payload, dict):
                        name = str(payload.get("name") or "")
                except (TypeError, ValueError):
                    name = ""
            briefs.append(
                {
                    "id": int(row["id"]),
                    "role": row["role"],
                    "name": name,
                    "preview": content[:120].replace("\n", " "),
                    "created_at": int(row["created_at"]),
                }
            )
        return briefs

    def search(self, q: str, limit: int = 20) -> list[dict]:
        """会话搜索：FTS5（trigram，中文子串友好）优先，短查询回退 LIKE。

        FTS5 对 3+ 字符查询可命中任意子串并按 bm25 排序；trigram 查询
        长度小于 3 时无匹配，自动回退原 LIKE 实现。
        """
        needle = q.strip()
        if not needle:
            return []
        like = f"%{needle}%"
        rows: list[dict] = []
        fts_ok = len(needle) >= 3
        with self._lock:
            if fts_ok:
                try:
                    fts_rows = self._conn.execute(
                        """SELECT s.id, s.workdir, s.title, s.updated_at, m.content
                           FROM messages_fts mfts
                           JOIN messages m ON m.id = mfts.rowid
                           JOIN sessions s ON s.id = m.session_id
                           WHERE messages_fts MATCH ?
                           ORDER BY bm25(messages_fts) LIMIT ?""",
                        (self._fts_query(needle), limit),
                    ).fetchall()
                    title_rows = self._conn.execute(
                        """SELECT s.id, s.workdir, s.title, s.updated_at
                           FROM sessions_fts f JOIN sessions s ON s.id = f.session_id
                           WHERE sessions_fts MATCH ?
                           ORDER BY bm25(sessions_fts) LIMIT ?""",
                        (self._fts_query(needle), limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    fts_rows = []
                    title_rows = []
                    fts_ok = False
            else:
                fts_rows = []
                title_rows = []
            seen: set[str] = set()
            if fts_ok:
                for r in title_rows:
                    if r["id"] in seen:
                        continue
                    seen.add(r["id"])
                    rows.append(
                        {
                            "id": r["id"],
                            "workdir": r["workdir"],
                            "title": r["title"],
                            "updated_at": r["updated_at"],
                            "match": {"kind": "title", "snippet": r["title"]},
                        }
                    )
                for r in fts_rows:
                    if r["id"] in seen or len(rows) >= limit:
                        continue
                    seen.add(r["id"])
                    content = r["content"] or ""
                    pos = content.find(needle)
                    start = max(0, pos - 20)
                    snippet = (
                        ("…" if start > 0 else "")
                        + content[start : pos + len(needle) + 20]
                        + ("…" if start + len(needle) + 20 < len(content) else "")
                    )
                    rows.append(
                        {
                            "id": r["id"],
                            "workdir": r["workdir"],
                            "title": r["title"],
                            "updated_at": r["updated_at"],
                            "match": {"kind": "content", "snippet": snippet},
                        }
                    )
                return rows[:limit]
            # --- LIKE fallback（短查询 / FTS 不可用）---
            sessions = self._conn.execute(
                "SELECT id, workdir, title, updated_at FROM sessions"
                " WHERE title LIKE ? ORDER BY updated_at DESC LIMIT ?",
                (like, limit),
            ).fetchall()
            for r in sessions:
                rows.append(
                    {
                        "id": r["id"],
                        "workdir": r["workdir"],
                        "title": r["title"],
                        "updated_at": r["updated_at"],
                        "match": {"kind": "title", "snippet": r["title"]},
                    }
                )
            if len(rows) < limit:
                msg_rows = self._conn.execute(
                    "SELECT m.session_id, m.content, s.workdir, s.title, s.updated_at"
                    " FROM messages m JOIN sessions s ON s.id = m.session_id"
                    " WHERE m.content LIKE ? AND s.title NOT LIKE ?"
                    " ORDER BY s.updated_at DESC LIMIT ?",
                    (like, like, limit - len(rows)),
                ).fetchall()
                for r in msg_rows:
                    if r["session_id"] in seen:
                        continue
                    seen.add(r["session_id"])
                    content = r["content"] or ""
                    pos = content.find(needle)
                    start = max(0, pos - 20)
                    snippet = (
                        ("…" if start > 0 else "")
                        + content[start : pos + len(needle) + 20]
                        + ("…" if start + len(needle) + 20 < len(content) else "")
                    )
                    rows.append(
                        {
                            "id": r["session_id"],
                            "workdir": r["workdir"],
                            "title": r["title"],
                            "updated_at": r["updated_at"],
                            "match": {"kind": "content", "snippet": snippet},
                        }
                    )
        return rows[:limit]

    @staticmethod
    def _fts_query(q: str) -> str:
        """把用户输入转成安全的 FTS5 查询：整个短语作为一个词条匹配，
        转义 FTS 特殊字符（trigram 下空格分词，短语即子串命中）。"""
        escaped = q.replace('"', '""')
        return f'"{escaped}"'
