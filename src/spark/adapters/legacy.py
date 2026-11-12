"""Legacy adapters for the ports in :mod:`spark.adapters`.

These wrap the current hand-written implementations so callers can already
depend on the stable protocol while the concrete library is swapped later
(ddgs/trafilatura, markitdown/docling, Casbin/OPA, Vault, ...).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from spark.adapters import (
    IngestedDocument,
    SearchResult,
)
from spark.knowledge import _read_doc, _split_chunks
from spark.models import ToolCall
from spark.policy import decide
from spark.tools.web import bing_search


class BingSearchProvider:
    """``SearchProvider`` over the existing Bing HTML scraper."""

    def search(self, query: str, max_results: int = 8) -> list[SearchResult]:
        rows = bing_search(query, max_results)
        return [
            SearchResult(
                title=str(row.get("title") or ""),
                url=str(row.get("url") or ""),
                snippet=str(row.get("snippet") or ""),
            )
            for row in rows
        ]


class TextDocumentIngestor:
    """``DocumentIngestor`` for the text formats the knowledge base supports.

    The private helpers are wrapped deliberately: when ``markitdown``/``docling``
    land they implement the same port and this adapter becomes the fallback.
    """

    def ingest(self, path: Path) -> IngestedDocument:
        text = _read_doc(path)
        if text is None:
            raise ValueError(f"unsupported or unreadable document: {path.name}")
        return IngestedDocument(
            name=path.name,
            text=text,
            chunks=_split_chunks(text),
        )


class LocalArtifactStore:
    """``ArtifactStore`` on the local filesystem with path-escape guards."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        raw = str(key).replace("\\", "/").strip()
        if not raw:
            raise ValueError("empty artifact key")
        candidate = Path(raw)
        if candidate.is_absolute():
            raise ValueError(f"absolute artifact key is not allowed: {key!r}")
        target = (self.root / raw).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"artifact key escapes root: {key!r}") from exc
        return target

    def put(self, name: str, data: bytes) -> str:
        target = self._resolve(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=".tmp-", suffix=".part"
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp_name, target)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return str(target.relative_to(self.root)).replace("\\", "/")

    def get(self, key: str) -> bytes:
        return self._resolve(key).read_bytes()

    def list(self, prefix: str = "") -> list[str]:
        out: list[str] = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            rel = str(path.relative_to(self.root)).replace("\\", "/")
            if rel.startswith(prefix):
                out.append(rel)
        return sorted(out)

    def delete(self, key: str) -> bool:
        target = self._resolve(key)
        if not target.is_file():
            return False
        target.unlink()
        return True


class LegacyPolicyEngine:
    """``PolicyEngine`` over ``spark.policy.decide``."""

    def decide(
        self,
        mode: str,
        tool: ToolCall,
        *,
        allow_always: set[str],
        readonly_mcp: set[str],
        rules: list | None = None,
        plan_approved: bool = False,
        scopes: dict | None = None,
    ) -> str:
        return decide(
            mode,  # type: ignore[arg-type]
            tool,
            allow_always=allow_always,
            readonly_mcp=readonly_mcp,
            rules=rules,
            plan_approved=plan_approved,
            scopes=scopes,
        )


class EnvSecretProvider:
    """``SecretProvider`` over environment variables (optional prefix)."""

    def __init__(self, prefix: str = "") -> None:
        self.prefix = prefix

    def get(self, name: str) -> str | None:
        value = os.environ.get(f"{self.prefix}{name}")
        return value or None


__all__ = [
    "BingSearchProvider",
    "EnvSecretProvider",
    "LegacyPolicyEngine",
    "LocalArtifactStore",
    "TextDocumentIngestor",
]
