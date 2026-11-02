"""Memory service: auto-extraction, retrieval, consolidation, and manual CRUD."""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

from spark.asyncutil import run_coroutine_sync
from spark.config import SparkConfig, env_api_key
from spark.memory.extractor import (
    consolidate_group,
    extract_facts,
    redact_secrets,
    resolve_operations,
)
from spark.memory.llm import embed_texts
from spark.memory.store import MemoryRow, MemoryStore

EXPLICIT_RE = re.compile(r"记住|请记|remember|memorize|keep in mind", re.IGNORECASE)

EMBED_RETRY_SECS = 600


def _kw_jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _build_transcript(user_text: str, assistant_text: str, tool_summary: str) -> str:
    pieces = [f"user: {user_text[:2000]}"]
    if assistant_text:
        pieces.append(f"assistant: {assistant_text[:2000]}")
    if tool_summary:
        pieces.append(f"tools: {tool_summary[:1500]}")
    return "\n".join(pieces)


class MemoryService:
    def __init__(self, store: MemoryStore, cfg: SparkConfig) -> None:
        self.store = store
        self.cfg = cfg
        self.embedding_available: bool | None = None
        self.embedding_checked_at = 0.0
        self._embedding_config: tuple[str, str, str, str] | None = None
        self._lock = threading.RLock()

    # ---------- embeddings ----------

    def _llm_ready(self) -> bool:
        p = self.cfg.provider
        return (
            p.name != "mock"
            and bool(p.base_url)
            and bool(p.api_key or p.name == "ollama")
        )

    def _embed(self, texts: list[str]) -> list[list[float]] | None:
        if not self.cfg.memory.enabled:
            self._embedding_config = None
            return None
        # P0 修复：向量检索显式 opt-in（memory.embedding_enabled，默认关）。
        # 默认配置曾把 /embeddings 请求打到 chat 端点（如 DeepSeek，无该路由），
        # 每轮失败降级关键词——宣传与实际不符。现在不开启就静默跳过。
        if not getattr(self.cfg.memory, "embedding_enabled", False):
            self._embedding_config = None
            return None
        p = self.cfg.provider
        base_url = self.cfg.memory.embedding_base_url or p.base_url
        same_target = base_url.rstrip("/") == p.base_url.rstrip("/")
        api_key = self.cfg.memory.embedding_api_key or ((p.api_key or env_api_key(self.cfg)) if same_target else "") or "ollama"
        embedding_config = (base_url, api_key, self.cfg.memory.embedding_model, p.name)
        now = time.time()
        if (
            self.embedding_available is False
            and self._embedding_config == embedding_config
            and now - self.embedding_checked_at < EMBED_RETRY_SECS
        ):
            return None
        self._embedding_config = embedding_config

        def _run_embed():
            return embed_texts(
                base_url=base_url,
                api_key=api_key,
                model=self.cfg.memory.embedding_model,
                texts=texts,
            )

        try:
            vecs = run_coroutine_sync(_run_embed(), timeout=60)
        except Exception:
            # 超时或异常时降级为不可用
            logger.warning("embedding 可用性检查失败", exc_info=True)
            vecs = None
        self.embedding_checked_at = now
        if vecs is None:
            self.embedding_available = False
            return None
        self.embedding_available = True
        return vecs

    # ---------- retrieval ----------

    def search(
        self, query: str, top_k: int | None = None
    ) -> list[tuple[MemoryRow, float]]:
        vecs = self._embed([query])
        q_emb = vecs[0] if vecs else None
        hits = self.store.search(query, top_k=top_k, query_embedding=q_emb)
        return self._maybe_rerank(query, hits, top_k)

    def _maybe_rerank(
        self,
        query: str,
        hits: list[tuple[MemoryRow, float]],
        top_k: int | None,
    ) -> list[tuple[MemoryRow, float]]:
        """可选重排：FlagEmbedding（bge-reranker-v2-m3）若已安装则对候选重排。

        未安装 / 加载失败时静默保持原序（RRF 已保证基础质量），不引入硬依赖。
        """
        if not self.cfg.memory.rerank_model:
            self._reranker_cache = (None, None)
            return hits
        if not hits or top_k == 0:
            return hits
        try:
            from FlagEmbedding import FlagReranker  # type: ignore[import-not-found]

            reranker = self._get_reranker(FlagReranker)
            if reranker is None:
                return hits
            top = min(len(hits), (top_k or self.cfg.memory.top_k) * 2)
            pairs = [[query, row.content] for row, _ in hits[:top]]
            scores = reranker.compute_score(pairs, normalize=True)
            if not isinstance(scores, (list, tuple)):
                scores = [scores]
            ranked = sorted(
                zip(hits[:top], scores), key=lambda x: x[1], reverse=True
            )
            return [(row, float(s)) for (row, _), s in ranked][
                : (top_k or self.cfg.memory.top_k)
            ]
        except ImportError:
            return hits
        except Exception:
            logger.debug("reranker 重排失败，保持原序", exc_info=True)
            return hits

    _reranker_cache: tuple = (None, None)

    def _get_reranker(self, cls) -> Any | None:
        """缓存已加载的 reranker（模型一次性加载，进程内复用）。"""
        model_name = self.cfg.memory.rerank_model
        if not model_name:
            self._reranker_cache = (None, None)
            return None
        cached, cached_name = self._reranker_cache
        if cached is not None and cached_name == model_name:
            return cached
        self._reranker_cache = (None, None)
        try:
            model = cls(model_name, device="cpu")
            self._reranker_cache = (model, model_name)
            return model
        except Exception:
            logger.warning("加载 reranker %s 失败，跳过重排", model_name)
            return None

    def retrieve_context(self, query: str, top_k: int | None = None) -> str | None:
        if not self.cfg.memory.enabled:
            return None
        with self._lock:
            hits = self.search(query, top_k=top_k)
            if not hits:
                return None
            lines = []
            for row, _score in hits:
                self.store.touch_access(row.id)
                lines.append(f"- [{row.type}] {row.content}")
        return "<long_term_memory>\n" + "\n".join(lines) + "\n</long_term_memory>"

    # ---------- recording ----------

    def record_turn(
        self,
        *,
        user_text: str,
        assistant_text: str,
        tool_summary: str,
        session_id: str,
    ) -> dict:
        if (
            not self.cfg.memory.enabled
            or not self.cfg.memory.auto_extract
            or not self._llm_ready()
        ):
            return {"extracted": 0, "ops": [], "conflicts": []}
        user_text = redact_secrets(user_text)
        assistant_text = redact_secrets(assistant_text)
        tool_summary = redact_secrets(tool_summary)
        transcript = _build_transcript(user_text, assistant_text, tool_summary)
        explicit = bool(EXPLICIT_RE.search(user_text))
        candidates, extract_error = self._extract_candidates(transcript, explicit)
        if extract_error:
            return {"extracted": 0, "ops": [], "conflicts": [], "error": extract_error}
        if not candidates:
            return {"extracted": 0, "ops": [], "conflicts": []}

        with self._lock:
            similar = self._similar_lookup(candidates)
            target_versions = self._target_versions(similar)
            ops, resolve_error = self._resolve_ops(candidates, similar)
            if resolve_error:
                return {
                    "extracted": len(candidates),
                    "ops": [],
                    "conflicts": [],
                    "error": resolve_error,
                }
            vec_map = self._embed_texts(ops)
            applied, conflicts, archived = self._apply_operations(
                ops, target_versions, vec_map, session_id
            )
        return {
            "extracted": len(candidates),
            "ops": applied,
            "archived": archived,
            "conflicts": conflicts,
        }

    def _extract_candidates(self, transcript: str, explicit: bool) -> tuple[list, str]:
        p = self.cfg.provider
        try:
            candidates = run_coroutine_sync(
                extract_facts(
                    transcript=transcript,
                    base_url=p.base_url,
                    api_key=p.api_key or "ollama",
                    model=p.model,
                    explicit=explicit,
                )
            )
        except Exception as exc:  # aqg: top-level boundary
            return [], str(exc)
        return list(candidates), ""

    def _similar_lookup(
        self, candidates: list
    ) -> dict[int, list[tuple[MemoryRow, float]]]:
        similar: dict[int, list[tuple[MemoryRow, float]]] = {}
        for i, cand in enumerate(candidates):
            similar[i] = self.search(cand.content, top_k=3)
        return similar

    @staticmethod
    def _target_versions(
        similar: dict[int, list[tuple[MemoryRow, float]]],
    ) -> dict[int, MemoryRow]:
        target_versions: dict[int, MemoryRow] = {}
        for hits in similar.values():
            for row, _score in hits:
                target_versions[row.id] = row
        return target_versions

    def _resolve_ops(self, candidates: list, similar: dict) -> tuple[list, str]:
        p = self.cfg.provider
        try:
            ops = run_coroutine_sync(
                resolve_operations(
                    candidates=candidates,
                    similar=similar,
                    base_url=p.base_url,
                    api_key=p.api_key or "ollama",
                    model=p.model,
                )
            )
        except Exception as exc:  # aqg: top-level boundary
            return [], str(exc)
        return list(ops), ""

    def _embed_texts(self, ops: list) -> dict[str, list[float]]:
        new_texts = [op.content for op in ops if op.action == "ADD"]
        update_texts = [
            op.content for op in ops if op.action == "UPDATE" and op.target_id
        ]
        all_texts = new_texts + update_texts
        vecs = self._embed(all_texts) if all_texts else None
        vec_map: dict[str, list[float]] = {}
        if vecs:
            for text, vec in zip(all_texts, vecs):
                vec_map[text] = vec
        return vec_map

    def _apply_operation(
        self,
        op,
        target_versions: dict[int, MemoryRow],
        vec_map: dict[str, list[float]],
        session_id: str,
    ) -> tuple[list[dict], dict | None]:
        """应用单条 ADD/UPDATE，返回 (applied 条目列表, 冲突记录或 None)。

        UPDATE 版本冲突时不失败：降级为 ADD，冲突记录带 fallback_id。
        """
        safe_content = redact_secrets(op.content)
        conflict: dict | None = None
        if op.action == "UPDATE" and op.target_id:
            target = target_versions.get(op.target_id)
            if target is not None and self.store.update_content_if_version(
                op.target_id,
                safe_content,
                target.version,
                importance=op.importance,
                embedding=vec_map.get(op.content),
                embedding_model=self.cfg.memory.embedding_model
                if op.content in vec_map
                else None,
            ):
                return (
                    [
                        {
                            "action": "UPDATE",
                            "id": op.target_id,
                            "content": safe_content,
                        }
                    ],
                    None,
                )
            conflict = {
                "action": "UPDATE",
                "id": op.target_id,
                "expected_version": target.version if target else None,
                "reason": "version_conflict",
            }
        memory_id = self.store.add(
            safe_content,
            type=op.type,
            importance=op.importance,
            embedding=vec_map.get(op.content) or vec_map.get(safe_content),
            embedding_model=self.cfg.memory.embedding_model
            if (op.content in vec_map or safe_content in vec_map)
            else None,
            source_session=session_id,
        )
        applied = [{"action": "ADD", "id": memory_id, "content": safe_content}]
        if conflict is not None:
            conflict["fallback_id"] = memory_id
        return applied, conflict

    def _apply_operations(
        self,
        ops: list,
        target_versions: dict[int, MemoryRow],
        vec_map: dict[str, list[float]],
        session_id: str,
    ) -> tuple[list[dict], list[dict], int]:
        applied: list[dict] = []
        conflicts: list[dict] = []
        with self.store.transaction():
            for op in ops:
                if op.action not in {"ADD", "UPDATE"}:
                    continue
                if op.action == "UPDATE" and not op.target_id:
                    continue
                entries, conflict = self._apply_operation(
                    op, target_versions, vec_map, session_id
                )
                applied.extend(entries)
                if conflict is not None:
                    conflicts.append(conflict)
            archived = self.store.enforce_capacity()
        return applied, conflicts, archived

    # ---------- optimization ----------

    def _similarity_clusters(self) -> list[list[MemoryRow]]:
        rows = self.store.all_active()
        token_sets = [set(r.keywords.split()) for r in rows]
        emb_map = {i: r.embedding for i, r in enumerate(rows) if r.embedding}
        parent = list(range(len(rows)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        # Compare only pairs that share at least one keyword. A full O(M^2) sweep
        # with 1536-dim embeddings took ~32s at 500 rows; the inverted index keeps
        # this proportional to the actual candidate pairs.
        from spark.memory.store import cosine

        inverted: dict[str, list[int]] = {}
        for i, tokens in enumerate(token_sets):
            for token in tokens:
                inverted.setdefault(token, []).append(i)

        candidates: set[tuple[int, int]] = set()
        for members in inverted.values():
            if len(members) < 2 or len(members) > 200:
                continue
            for a_pos in range(len(members)):
                for b_pos in range(a_pos + 1, len(members)):
                    a, b = members[a_pos], members[b_pos]
                    candidates.add((a, b) if a < b else (b, a))

        for i, j in candidates:
            if find(i) == find(j):
                continue
            kw = _kw_jaccard(token_sets[i], token_sets[j])
            close = kw > 0.5
            if not close and i in emb_map and j in emb_map and kw > 0.12:
                close = cosine(emb_map[i], emb_map[j]) > 0.88
            if close:
                union(i, j)
        groups: dict[int, list[int]] = {}
        for i in range(len(rows)):
            groups.setdefault(find(i), []).append(i)
        return [[rows[i] for i in idxs] for idxs in groups.values() if len(idxs) >= 2]

    def optimize(self) -> dict:
        report: dict = {"consolidated_groups": 0, "archived": 0, "errors": []}
        if not self.cfg.memory.enabled:
            report["error"] = "memory disabled"
            return report
        with self._lock:
            llm_ok = self._llm_ready()
            if llm_ok:
                clusters = self._similarity_clusters()[:25]
                p = self.cfg.provider
                for cluster in clusters:
                    contents = [r.content for r in cluster]
                    try:
                        merged = run_coroutine_sync(
                            consolidate_group(
                                contents=contents,
                                base_url=p.base_url,
                                api_key=p.api_key or "ollama",
                                model=p.model,
                            )
                        )
                    except Exception as exc:
                        report["errors"].append(str(exc))
                        continue
                    if not merged:
                        continue
                    content, importance = merged
                    vecs = self._embed([content])
                    self.store.add(
                        content,
                        type=cluster[0].type,
                        importance=max(importance, max(r.importance for r in cluster)),
                        embedding=vecs[0] if vecs else None,
                        embedding_model=self.cfg.memory.embedding_model
                        if vecs
                        else None,
                    )
                    for row in cluster:
                        self.store.archive(row.id)
                    report["consolidated_groups"] += 1
            report["archived"] = self.store.enforce_capacity()
            report["stats"] = self.store.stats()
        return report

    # ---------- management ----------

    def add_manual(
        self, content: str, type: str = "general", importance: float = 8.0
    ) -> int:
        content = redact_secrets(content)
        vecs = self._embed([content])
        with self._lock:
            return self.store.add(
                content,
                type=type,
                importance=importance,
                embedding=vecs[0] if vecs else None,
                embedding_model=self.cfg.memory.embedding_model if vecs else None,
                source_session=None,
            )

    def delete(self, memory_id: int) -> None:
        with self._lock:
            self.store.delete(memory_id)

    def list_memories(self, limit: int = 200) -> list[dict]:
        with self._lock:
            return [r.to_dict() for r in self.store.list_all(limit)]

    def stats(self) -> dict:
        with self._lock:
            stats = self.store.stats()
        stats["embedding_available"] = self.embedding_available
        stats["embedding_model"] = self.cfg.memory.embedding_model
        stats["capacity"] = self.cfg.memory.capacity
        return stats


__all__ = [
    "MemoryService",
    "MemoryStore",
]
