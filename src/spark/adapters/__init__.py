"""Stable ports for capabilities that should be swappable for mature libraries.

The protocols here are intentionally small and depend only on foundation models
(:mod:`spark.models`).  They are the seams referenced by the architecture
upgrade plan:

* ``SearchProvider``      — Bing scraping today, ddgs/Tavily/Exa later.
* ``DocumentIngestor``    — text-only knowledge base today, markitdown/docling later.
* ``ArtifactStore``       — local filesystem today, S3/ADLS/SharePoint later.
* ``PolicyEngine``        — hand-written rules today, Casbin/OPA later.
* ``SecretProvider``      — env/config today, keyring/Vault later.
* ``ModelGateway``        — LiteLLM provider today, LiteLLM Router/Proxy later.

Concrete adapters live in :mod:`spark.adapters.legacy` so core code can import
only the port, never the implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Protocol, runtime_checkable

from spark.models import ChatDelta, ChatMessage, ToolCall


@runtime_checkable
class ModelGateway(Protocol):
    """Streaming chat-completion gateway (structurally satisfied by ``Provider``)."""

    def stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict],
        effort: str | None = None,
    ) -> AsyncIterator[ChatDelta]: ...


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""


@runtime_checkable
class SearchProvider(Protocol):
    def search(self, query: str, max_results: int = 8) -> list[SearchResult]: ...


@dataclass(frozen=True)
class IngestedDocument:
    name: str
    text: str
    chunks: list[str]


@runtime_checkable
class DocumentIngestor(Protocol):
    def ingest(self, path: Path) -> IngestedDocument: ...


@runtime_checkable
class ArtifactStore(Protocol):
    def put(self, name: str, data: bytes) -> str: ...

    def get(self, key: str) -> bytes: ...

    def list(self, prefix: str = "") -> list[str]: ...

    def delete(self, key: str) -> bool: ...


@runtime_checkable
class PolicyEngine(Protocol):
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
    ) -> str: ...


@runtime_checkable
class SecretProvider(Protocol):
    def get(self, name: str) -> str | None: ...


__all__ = [
    "ArtifactStore",
    "DocumentIngestor",
    "IngestedDocument",
    "ModelGateway",
    "PolicyEngine",
    "SearchProvider",
    "SearchResult",
    "SecretProvider",
]
