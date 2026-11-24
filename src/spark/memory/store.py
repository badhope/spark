from __future__ import annotations

import logging
import math
import sqlite3 as _sqlite3_module
import threading
import time
from array import array
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import chromadb

from spark.config import MemoryConfig

# 分词器移入 spark.textutil 以便知识库复用而不牵连 chromadb；此处保留原名导出，
# 既有 import 路径（tests / memory 内部）不变。
from spark.textutil import tokenize  # noqa: F401

logger = logging.getLogger(__name__)

_DEFAULT_DIM = 1536  # placeholder dim for memories without real embeddings


def pack_vector(vec: list[float]) -> bytes:
    return array("f", vec).tobytes()


def unpack_vector(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    arr = array("f")
    arr.frombytes(blob)
    return list(arr)


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


class MemoryRow:
    def __init__(self, row) -> None:
        self.id = int(row["id"])
        self.type = row["type"]
        self.content = row["content"]
        self.keywords = row["keywords"] or ""
        self.embedding = unpack_vector(row["embedding"])
        self.embedding_model = row["embedding_model"] or None
        self.importance = float(row["importance"])
        self.status = row["status"]
        self.source_session = row["source_session"] or None
        self.access_count = int(row["access_count"])
        self.version = int(row["version"])
        self.created_at = int(row["created_at"])
        self.updated_at = int(row["updated_at"])
        self.last_accessed = int(row["last_accessed"])

    def to_dict(self, include_status: bool = True) -> dict:
        out = {
            "id": self.id,
            "type": self.type,
            "content": self.content,
            "importance": self.importance,
            "access_count": self.access_count,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_accessed": self.last_accessed,
        }
        if include_status:
            out["status"] = self.status
        return out


class MemoryStore:
    def __init__(self, db_path: Path, cfg: MemoryConfig | None = None) -> None:
        db_path = Path(db_path) if not isinstance(db_path, Path) else db_path
        self.cfg = cfg or MemoryConfig()
        self.db_path = db_path
        self._transaction_depth = 0
        self._dim: int | None = None  # detected embedding dimension
        self._lock = threading.RLock()

        # ChromaDB is opened lazily: a web server that never touches memory
        # should not pay for (or leak) a native persistent client per instance.
        self._client: Any = None
        self._collection_obj: Any = None
        self._next_id: int | None = None
        self._closed = False
        # 时间戳 + 单调计数器；锁内递增保证多线程不产出重复 id。
        self._id_lock = threading.Lock()

        # Health tracking: timestamp of last exception (for degraded status)
        self._last_error_time: float | None = None

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _ensure_chroma(self) -> None:
        if self._collection_obj is not None:
            return
        if self._closed:
            raise RuntimeError("memory store is closed")
        with self._lock:
            if self._collection_obj is not None:
                return
            db_path = self.db_path
            if str(db_path) == ":memory:":
                self._client = chromadb.EphemeralClient()
            else:
                db_path.parent.mkdir(parents=True, exist_ok=True)
                chroma_dir = db_path.parent / f"{db_path.stem}_chroma"
                from chromadb.config import Settings as ChromaSettings

                self._client = chromadb.PersistentClient(
                    path=str(chroma_dir),
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
            self._collection_obj = self._client.get_or_create_collection(
                "memories",
                metadata={"hnsw:space": "cosine"},
            )
            if str(db_path) != ":memory:" and not self._collection_obj.get().get("ids"):
                self._maybe_migrate_sqlite(db_path)
            self._detect_dimension()

    @property
    def _collection(self):
        self._ensure_chroma()
        return self._collection_obj

    def _now(self) -> int:
        return int(time.time())

    def _compute_next_start_id(self) -> int:
        """扫描现有 id，取 (max, 当前秒时间戳*1000) 的较大值，作为单调计数起点。
        这样即使数据库已有数据、或被外部程序写入了更大的 id，我们也不会冲突。"""
        existing = self._collection.get()
        max_id = 0
        for id_str in existing.get("ids", []):
            try:
                max_id = max(max_id, int(id_str))
            except (ValueError, TypeError):
                pass
        time_based = int(time.time() * 1000)  # ms 级时间戳，与现有 id 空间明显拉开
        return max(max_id + 1, time_based)

    def _next_monotonic_id(self) -> int:
        """返回一个新的、严格递增的 id；线程安全（锁内操作）。"""
        with self._id_lock:
            if self._next_id is None:
                self._next_id = self._compute_next_start_id()
            self._next_id += 1
            return self._next_id

    def _detect_dimension(self) -> None:
        if self._dim is not None:
            return
        existing = self._collection.get(limit=1, include=["embeddings"])
        embs = existing.get("embeddings")
        if embs is not None and len(embs) > 0:
            first = embs[0]
            if first is not None and hasattr(first, "__len__"):
                self._dim = len(first)

    def _ensure_dim(self, embedding: list[float] | None) -> list[float]:
        if self._dim is None:
            if embedding:
                self._dim = len(embedding)
            else:
                self._dim = _DEFAULT_DIM
        if embedding:
            if len(embedding) != self._dim:
                if len(embedding) < self._dim:
                    return embedding + [0.0] * (self._dim - len(embedding))
                return embedding[: self._dim]
            return list(embedding)
        return [0.0] * self._dim

    def _numpy_to_list(self, emb) -> list[float] | None:
        if emb is None:
            return None
        if hasattr(emb, "tolist"):
            return emb.tolist()
        return list(emb)

    def _maybe_migrate_sqlite(self, db_path: Path) -> None:
        if not db_path.exists():
            return
        try:
            conn = _sqlite3_module.connect(db_path)
            conn.row_factory = _sqlite3_module.Row
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "memories" not in tables:
                conn.close()
                return
            columns = {
                str(r["name"])
                for r in conn.execute("PRAGMA table_info(memories)").fetchall()
            }
            rows = conn.execute("SELECT * FROM memories ORDER BY id").fetchall()
            conn.close()
            if not rows:
                return

            ids: list[str] = []
            documents: list[str] = []
            metadatas: list[dict] = []
            embeddings: list[list[float]] = []
            for row in rows:
                ids.append(str(row["id"]))
                documents.append(row["content"])
                vec = unpack_vector(row["embedding"])
                if vec and self._dim is None:
                    self._dim = len(vec)
                embeddings.append(self._ensure_dim(vec))
                meta = {
                    "type": row["type"],
                    "keywords": row["keywords"] or "",
                    "importance": float(row["importance"]),
                    "status": row["status"],
                    "source_session": row["source_session"] or "",
                    "access_count": int(row["access_count"]),
                    "version": int(row["version"]) if "version" in columns else 1,
                    "embedding_model": row["embedding_model"] or "",
                    "created_at": int(row["created_at"]),
                    "updated_at": int(row["updated_at"]),
                    "last_accessed": int(row["last_accessed"]),
                }
                metadatas.append(meta)
            if ids:
                self._collection.add(
                    ids=ids,
                    documents=documents,
                    metadatas=metadatas,
                    embeddings=embeddings,
                )
        except Exception:
            # 批量添加失败时记录，数据可能部分丢失
            logger.warning("memory 批量写入 ChromaDB 失败", exc_info=True)

    # ------------------------------------------------------------------
    # Transaction support (ChromaDB has no native transactions)
    # ------------------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._transaction_depth > 0:
            self._transaction_depth += 1
            try:
                yield
            finally:
                self._transaction_depth -= 1
            return

        with self._lock:
            self._transaction_depth = 1
            snapshot = self._snapshot_collection()
            try:
                yield
            except Exception:
                # 事务失败回滚快照后重新抛出
                logger.warning("memory 事务失败，已回滚快照", exc_info=True)
                self._restore_snapshot(snapshot)
                raise
            finally:
                self._transaction_depth = 0

    def _snapshot_collection(self) -> tuple[list[str], list[dict], list[list[float]], list[str]]:
        data = self._collection.get(include=["metadatas", "embeddings", "documents"])
        ids = list(data.get("ids", []))
        metas = [dict(m) for m in (data.get("metadatas") or [])]
        embs: list[list[float]] = []
        raw_embs = data.get("embeddings")
        if raw_embs is not None:
            if hasattr(raw_embs, "tolist") and hasattr(raw_embs, "shape"):
                for arr in raw_embs:
                    converted = self._numpy_to_list(arr)
                    embs.append(converted if converted is not None else [0.0] * (self._dim or _DEFAULT_DIM))
            else:
                for e in list(raw_embs):
                    if e is None:
                        embs.append([0.0] * (self._dim or _DEFAULT_DIM))
                    else:
                        embs.append(list(e) if not hasattr(e, "tolist") else e.tolist())
        docs = list(data.get("documents") or [])
        return ids, metas, embs, docs

    def _snapshot_map(
        self, snapshot: tuple[list[str], list[dict], list[list[float]], list[str]]
    ) -> dict[str, tuple[str, dict, list[float] | None]]:
        ids, metas, embs, docs = snapshot
        return {
            sid: (doc, meta, emb)
            for sid, doc, meta, emb in zip(ids, docs, metas, embs, strict=True)
        }

    def _current_map(self) -> tuple[list[str], dict[str, tuple[str, dict, list[float] | None]]]:
        cur = self._collection.get(include=["metadatas", "embeddings", "documents"])
        cur_ids = cur.get("ids", [])
        cur_docs = cur.get("documents") or []
        cur_metas = cur.get("metadatas") or []
        cur_embs_raw = cur.get("embeddings")
        cur_map: dict[str, tuple[str, dict, list[float] | None]] = {}
        for i, sid in enumerate(cur_ids):
            doc = cur_docs[i] if i < len(cur_docs) else ""
            meta = cur_metas[i] if i < len(cur_metas) else {}
            emb = None
            if cur_embs_raw is not None and i < len(cur_embs_raw) and cur_embs_raw[i] is not None:
                emb = self._numpy_to_list(cur_embs_raw[i])
            cur_map[sid] = (doc, meta, emb)
        return cur_ids, cur_map

    def _apply_restore(
        self,
        ids: list[str],
        snap_map: dict[str, tuple[str, dict, list[float] | None]],
        cur_ids: list[str],
        cur_map: dict[str, tuple[str, dict, list[float] | None]],
    ) -> None:
        snap_set = set(snap_map)
        cur_set = set(cur_map)
        # 1) ADD: 快照有、当前没有 → 先追加到原集合
        missing = [sid for sid in ids if sid not in cur_set]
        if missing:
            self._collection.add(
                ids=missing,
                documents=[snap_map[sid][0] for sid in missing],
                metadatas=[snap_map[sid][1] for sid in missing],
                embeddings=[snap_map[sid][2] for sid in missing],
            )
        # 2) UPDATE: 两边都有但内容有差异 → 用快照值覆盖
        common = [sid for sid in ids if sid in cur_set]
        changed = [sid for sid in common if cur_map[sid] != snap_map[sid]]
        if changed:
            self._collection.update(
                ids=changed,
                documents=[snap_map[sid][0] for sid in changed],
                metadatas=[snap_map[sid][1] for sid in changed],
                embeddings=[snap_map[sid][2] for sid in changed],
            )
        # 3) DELETE: 当前有、快照没有（事务中途新增的冗余数据）
        extra = [sid for sid in cur_ids if sid not in snap_set]
        if extra:
            self._collection.delete(ids=extra)

    def _restore_snapshot(
        self, snapshot: tuple[list[str], list[dict], list[list[float]], list[str]]
    ) -> None:
        """Copy-on-write 风格的回滚：先 diff 快照与当前集合，再按 添加 → 更新 → 删除
        三步退化改动，确保在快照中任意记录未恢复前不删除旧数据。
        """
        ids, _metas, _embs, _docs = snapshot
        snap_map = self._snapshot_map(snapshot)
        cur_ids, cur_map = self._current_map()
        self._apply_restore(ids, snap_map, cur_ids, cur_map)

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def _row_from_chroma(
        self, id_str: str, document: str, metadata: dict, embedding=None
    ) -> MemoryRow:
        return MemoryRow(
            {
                "id": int(id_str),
                "type": metadata.get("type", "general"),
                "content": document,
                "keywords": metadata.get("keywords", ""),
                "embedding": pack_vector(embedding) if embedding else None,
                "embedding_model": metadata.get("embedding_model") or None,
                "importance": float(metadata.get("importance", 5.0)),
                "status": metadata.get("status", "active"),
                "source_session": metadata.get("source_session") or None,
                "access_count": int(metadata.get("access_count", 0)),
                "version": int(metadata.get("version", 1)),
                "created_at": int(metadata.get("created_at", 0)),
                "updated_at": int(metadata.get("updated_at", 0)),
                "last_accessed": int(metadata.get("last_accessed", 0)),
            }
        )

    def get_all_with_where(self, where: dict | None = None) -> dict:
        kwargs: dict = {"include": ["metadatas", "embeddings", "documents"]}
        if where:
            kwargs["where"] = where
        return self._collection.get(**kwargs)

    def _fetch_all_chroma(self, where: dict | None = None) -> list[MemoryRow]:
        data = self.get_all_with_where(where)
        rows: list[MemoryRow] = []
        ids = data.get("ids", [])
        docs = data.get("documents", [])
        metas = data.get("metadatas", [])
        embs = data.get("embeddings")
        for i, id_str in enumerate(ids):
            doc = docs[i] if i < len(docs) else ""
            meta = metas[i] if i < len(metas) else {}
            emb = None
            if embs is not None and i < len(embs):
                emb = self._numpy_to_list(embs[i])
            rows.append(self._row_from_chroma(id_str, doc, meta, emb))
        return rows

    def _get_raw(self, memory_id: int) -> dict | None:
        data = self._collection.get(
            ids=[str(memory_id)], include=["metadatas", "embeddings", "documents"]
        )
        if not data.get("ids"):
            return None
        emb = None
        embs = data.get("embeddings")
        if embs is not None and len(embs) > 0 and embs[0] is not None:
            emb = self._numpy_to_list(embs[0])
        return {
            "id": data["ids"][0],
            "document": data["documents"][0] if data["documents"] else "",
            "metadata": data["metadatas"][0] if data["metadatas"] else {},
            "embedding": emb,
        }

    # ------------------------------------------------------------------
    # Public API — must stay test-compatible
    # ------------------------------------------------------------------

    def _record_error(self) -> None:
        """Record that an error occurred (for health check degradation)."""
        self._last_error_time = time.time()

    def health_check(self) -> dict:
        """Return health status of the memory store.

        Returns a dict with:
            - status: "ok" or "degraded" (degraded if an error occurred within 60s)
            - count: total number of memories in the store
            - dim: detected embedding dimension (or default)
        """
        try:
            data = self._collection.get(include=["metadatas"])
            count = len(data.get("ids", []))
            dim = self._dim or _DEFAULT_DIM
            status = "ok"
            if self._last_error_time is not None and (time.time() - self._last_error_time) < 60:
                status = "degraded"
            return {"status": status, "count": count, "dim": dim}
        except Exception:
            # status 查询失败返回 degraded，调用方可据此降级
            self._record_error()
            logger.warning("memory 状态查询失败，返回 degraded", exc_info=True)
            return {"status": "degraded", "count": -1, "dim": self._dim or _DEFAULT_DIM}

    def close(self) -> None:
        self._closed = True
        client = self._client
        self._collection_obj = None
        self._client = None
        if client is not None:
            try:
                client.reset()
            except Exception:
                # reset 失败不影响进程退出
                logger.debug("ChromaDB client reset 失败", exc_info=True)

    def _apply_metadata_update(
        self, memory_id: int, set_pairs: list[tuple[str, object]]
    ) -> int:
        """Apply a sequence of (column, value) assignments to the metadata of
        the given memory_id.  Returns 1 if the memory exists, 0 otherwise."""
        raw = self._get_raw(memory_id)
        if raw is None:
            return 0
        meta = raw["metadata"]
        for col, val in set_pairs:
            meta[col] = val
        emb = self._ensure_dim(raw["embedding"]) if raw.get("embedding") is not None else None
        self._collection.update(
            ids=[str(memory_id)],
            documents=[raw["document"]],
            metadatas=[meta],
            embeddings=[emb] if emb is not None else None,
        )
        return 1

    def add(
        self,
        content: str,
        *,
        type: str = "general",
        importance: float = 5.0,
        embedding: list[float] | None = None,
        embedding_model: str | None = None,
        source_session: str | None = None,
    ) -> int:
        """Insert a new memory, truncating content to 400 chars; returns the new id.

        Empty content is rejected: an empty memory is invisible in search yet
        still consumes a capacity slot and shows up in the management panel.
        """
        now = self._now()
        content = content.strip()[:400]
        if not content:
            raise ValueError("memory content must not be empty")
        importance = max(0.0, min(10.0, float(importance)))
        metadata = {
            "type": type,
            "keywords": " ".join(tokenize(content)[:40]),
            "importance": importance,
            "status": "active",
            "source_session": source_session or "",
            "access_count": 0,
            "version": 1,
            "embedding_model": embedding_model or "",
            "created_at": now,
            "updated_at": now,
            "last_accessed": now,
        }
        emb = self._ensure_dim(embedding if embedding else None)

        # 修复：不再用 "max+1"（并发不安全），改用单调计数器
        new_id = self._next_monotonic_id()

        self._collection.add(
            ids=[str(new_id)],
            documents=[content],
            metadatas=[metadata],
            embeddings=[emb],
        )
        return new_id

    def update_content_if_version(
        self,
        memory_id: int,
        content: str,
        expected_version: int,
        *,
        importance: float | None = None,
        embedding: list[float] | None = None,
        embedding_model: str | None = None,
    ) -> bool:
        """乐观锁更新：版本号匹配且状态 active 时才写入，返回是否成功。

        公开 API——供 MemoryService.record_turn 以捕获的版本做并发安全的
        UPDATE（冲突时调用方回退为 ADD 并记入 conflicts）。
        """
        raw = self._get_raw(memory_id)
        if raw is None:
            return False
        meta = raw["metadata"]
        if int(meta.get("version", 0)) != expected_version or meta.get("status") != "active":
            return False
        now = self._now()
        content = content.strip()[:400]
        meta["keywords"] = " ".join(tokenize(content)[:40])
        meta["updated_at"] = now
        if importance is not None:
            meta["importance"] = max(0.0, min(10.0, float(importance)))
        if embedding is not None:
            meta["embedding_model"] = embedding_model or meta.get("embedding_model")
        meta["version"] = int(meta.get("version", 1)) + 1

        # Build embedding: use provided one, else keep existing, else placeholder
        emb: list[float] | None = None
        if embedding is not None:
            emb = self._ensure_dim(embedding)
        elif raw.get("embedding") is not None:
            emb = self._ensure_dim(raw["embedding"])

        self._collection.update(
            ids=[str(memory_id)],
            documents=[content],
            metadatas=[meta],
            embeddings=[emb] if emb is not None else None,
        )
        return True

    def update_content(
        self,
        memory_id: int,
        content: str,
        *,
        importance: float | None = None,
        embedding: list[float] | None = None,
        embedding_model: str | None = None,
    ) -> None:
        row = self.get(memory_id)
        if row is None or row.status != "active":
            return
        self.update_content_if_version(
            memory_id,
            content,
            row.version,
            importance=importance,
            embedding=embedding,
            embedding_model=embedding_model,
        )

    def get(self, memory_id: int) -> MemoryRow | None:
        raw = self._get_raw(memory_id)
        if raw is None:
            return None
        return self._row_from_chroma(
            raw["id"], raw["document"], raw["metadata"], raw["embedding"]
        )

    def _archive_if_version(self, memory_id: int, expected_version: int) -> bool:
        raw = self._get_raw(memory_id)
        if raw is None:
            return False
        meta = raw["metadata"]
        if int(meta.get("version", 0)) != expected_version or meta.get("status") != "active":
            return False
        meta["status"] = "archived"
        meta["updated_at"] = self._now()
        meta["version"] = int(meta.get("version", 1)) + 1
        emb = None
        if raw.get("embedding") is not None:
            emb = self._ensure_dim(raw["embedding"])
        self._collection.update(
            ids=[str(memory_id)],
            documents=[raw["document"]],
            metadatas=[meta],
            embeddings=[emb] if emb is not None else None,
        )
        return True

    def archive(self, memory_id: int) -> bool:
        """Soft-delete a memory by marking it as 'archived'. Returns True on success."""
        row = self.get(memory_id)
        if row is None or row.status != "active":
            return False
        return self._archive_if_version(memory_id, row.version)

    def delete(self, memory_id: int) -> bool:
        """Hard-delete a memory. Returns True if it existed and was removed."""
        if self._get_raw(memory_id) is None:
            return False
        self._collection.delete(ids=[str(memory_id)])
        return True

    def touch_access(self, memory_id: int) -> None:
        raw = self._get_raw(memory_id)
        if raw is None:
            return
        meta = raw["metadata"]
        meta["access_count"] = int(meta.get("access_count", 0)) + 1
        meta["last_accessed"] = self._now()
        meta["version"] = int(meta.get("version", 1)) + 1
        emb = None
        if raw.get("embedding") is not None:
            emb = self._ensure_dim(raw["embedding"])
        self._collection.update(
            ids=[str(memory_id)],
            documents=[raw["document"]],
            metadatas=[meta],
            embeddings=[emb] if emb is not None else None,
        )

    def all_active(self) -> list[MemoryRow]:
        return self._fetch_all_chroma({"status": "active"})

    def list_all(self, limit: int = 200) -> list[MemoryRow]:
        rows = self._fetch_all_chroma()
        rows.sort(key=lambda r: (0 if r.status == "active" else 1, -r.updated_at))
        return rows[:limit]

    def stats(self) -> dict:
        data = self._collection.get(include=["metadatas"])
        by_status: dict[str, int] = {}
        by_type: dict[str, int] = {}
        metas = data.get("metadatas") or []
        for meta in metas:
            status = meta.get("status", "active")
            by_status[status] = by_status.get(status, 0) + 1
            if status == "active":
                t = meta.get("type", "general")
                by_type[t] = by_type.get(t, 0) + 1
        return {
            "active": by_status.get("active", 0),
            "archived": by_status.get("archived", 0),
            "by_type": by_type,
        }

    # ------------------------------------------------------------------
    # Scoring / search
    # ------------------------------------------------------------------

    def _recency(self, ts: int) -> float:
        days = max(0.0, (self._now() - ts) / 86400.0)
        return math.exp(-days / 30.0)

    def _importance_eff(self, row: MemoryRow) -> float:
        boost = min(2.0, math.log2(1 + row.access_count))
        return min(10.0, row.importance + boost)

    def score(
        self, row: MemoryRow, query_tokens: list[str], query_emb: list[float] | None
    ) -> float:
        cfg_w = (0.5, 0.25, 0.15, 0.10)
        w_emb, w_kw, w_rec, w_imp = cfg_w
        if row.embedding is None or query_emb is None:
            w_emb, w_kw = 0.0, 0.75
        emb_score = 0.0
        if row.embedding is not None and query_emb is not None:
            emb_score = cosine(row.embedding, query_emb)
        row_tokens = set(row.keywords.split())
        kw_score = 0.0
        if query_tokens:
            hit = sum(1 for t in query_tokens if t in row_tokens)
            kw_score = hit / len(query_tokens)
        score = (
            w_emb * emb_score
            + w_kw * kw_score
            + w_rec * self._recency(row.last_accessed)
            + w_imp * (self._importance_eff(row) / 10.0)
        )
        return round(score, 4)

    def _bm25(self, row: MemoryRow, q_tokens: list[str], idf: dict[str, float]) -> float:
        """BM25-style lexical score over the row's keyword index.

        Uses the same tokenizer as :func:`tokenize` (中文 bigram），词频 + IDF
        + 文档长度归一。与向量排序独立，供 RRF 融合。
        """
        row_tokens = (row.keywords or "").split()
        if not row_tokens:
            return 0.0
        doc_len = len(row_tokens)
        avg_len = max(1.0, self._avg_kw_len)
        k1, b = 1.2, 0.75
        score = 0.0
        counts: dict[str, int] = {}
        for t in row_tokens:
            counts[t] = counts.get(t, 0) + 1
        for t in q_tokens:
            tf = counts.get(t, 0)
            if tf == 0:
                continue
            idf_t = idf.get(t, 1.0)
            denom = tf + k1 * (1 - b + b * doc_len / avg_len)
            score += idf_t * (tf * (k1 + 1)) / denom
        return round(score, 4)

    def _rrf_merge(
        self,
        vec_rank: list[MemoryRow],
        kw_rank: list[MemoryRow],
        k: int = 60,
    ) -> dict[int, float]:
        """Reciprocal Rank Fusion：两个独立排序按 1/(k+rank) 融合。"""
        scores: dict[int, float] = {}
        for rank, row in enumerate(vec_rank):
            scores[row.id] = scores.get(row.id, 0.0) + 1.0 / (k + rank + 1)
        for rank, row in enumerate(kw_rank):
            scores[row.id] = scores.get(row.id, 0.0) + 1.0 / (k + rank + 1)
        return scores

    def _query_tokens(self, query: str) -> list[str]:
        q_tokens: list[str] = []
        seen: set[str] = set()
        for token in tokenize(query):
            if token not in seen:
                seen.add(token)
                q_tokens.append(token)
        return q_tokens

    def _search_rows(
        self, source_session: str | None, exclude_session: str | None
    ) -> list[MemoryRow]:
        rows: list[MemoryRow] = []
        for row in self.all_active():
            if source_session is not None and row.source_session not in (None, source_session):
                continue
            if exclude_session is not None and row.source_session == exclude_session:
                continue
            rows.append(row)
        return rows

    def _idf(self, rows: list[MemoryRow], q_tokens: list[str]) -> dict[str, float]:
        if not q_tokens:
            return {}
        doc_freq: dict[str, int] = {}
        for row in rows:
            rk = set((row.keywords or "").split())
            for token in q_tokens:
                if token in rk:
                    doc_freq[token] = doc_freq.get(token, 0) + 1
        n_docs = len(rows)
        return {
            token: math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
            for token, df in doc_freq.items()
        }

    def _rrf_search(
        self,
        rows: list[MemoryRow],
        q_tokens: list[str],
        idf: dict[str, float],
        query_embedding: list[float],
        top_k: int,
    ) -> list[tuple[MemoryRow, float]]:
        vec_rank = sorted(
            rows,
            key=lambda r: cosine(r.embedding or [0.0] * len(query_embedding), query_embedding)
            if r.embedding and len(r.embedding) == len(query_embedding)
            else 0.0,
            reverse=True,
        )
        kw_rank = sorted(rows, key=lambda r: self._bm25(r, q_tokens, idf), reverse=True)
        merged = self._rrf_merge(vec_rank, kw_rank)
        ranked = sorted(rows, key=lambda r: merged.get(r.id, 0.0), reverse=True)
        ranked = [r for r in ranked if merged.get(r.id, 0.0) > 0.0]
        return [(r, merged[r.id]) for r in ranked[:top_k]]

    def _linear_search(
        self,
        rows: list[MemoryRow],
        q_tokens: list[str],
        query_embedding: list[float] | None,
        min_score: float,
        top_k: int,
    ) -> list[tuple[MemoryRow, float]]:
        results: list[tuple[MemoryRow, float]] = []
        for row in rows:
            score = self.score(row, q_tokens, query_embedding)
            if score > min_score:
                results.append((row, score))
        results.sort(key=lambda pair: pair[1], reverse=True)
        return results[:top_k]

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        query_embedding: list[float] | None = None,
        min_score: float = 0.0,
        source_session: str | None = None,
        exclude_session: str | None = None,
        hybrid: bool = True,
    ) -> list[tuple[MemoryRow, float]]:
        """Hybrid keyword + vector search over active memories."""
        top_k = top_k or self.cfg.top_k
        q_tokens = self._query_tokens(query)
        rows = self._search_rows(source_session, exclude_session)
        if not rows:
            return []
        idf = self._idf(rows, q_tokens)
        self._avg_kw_len = sum(len((r.keywords or "").split()) for r in rows) / max(
            1, len(rows)
        )
        if hybrid and query_embedding is not None:
            return self._rrf_search(rows, q_tokens, idf, query_embedding, top_k)
        return self._linear_search(rows, q_tokens, query_embedding, min_score, top_k)
    def enforce_capacity(self, *, stale_days: float = 180.0) -> int:
        archived = 0
        with self.transaction():
            cutoff = self._now() - int(stale_days * 86400)
            stale_rows = self._fetch_all_chroma({"status": "active"})
            for row in stale_rows:
                if row.last_accessed < cutoff and row.importance < 7.0:
                    if self._archive_if_version(row.id, row.version):
                        archived += 1

            active_rows = self._fetch_all_chroma({"status": "active"})
            count = len(active_rows)
            overflow = count - self.cfg.capacity
            if overflow > 0:
                active_rows.sort(
                    key=lambda r: (
                        self._importance_eff(r) * 0.6
                        + self._recency(r.last_accessed) * 0.4
                    )
                )
                for row in active_rows[:overflow]:
                    if self._archive_if_version(row.id, row.version):
                        archived += 1
        return archived


__all__ = ["cosine", "pack_vector", "tokenize", "MemoryRow", "MemoryStore"]
