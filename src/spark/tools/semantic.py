from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from spark.models import ToolResult


class SemanticSearchArgs(BaseModel):
    query: str = Field(description="natural-language or mixed code query")
    top_k: int = Field(default=8, ge=1, le=20)
    refresh: bool = Field(default=False, description="rebuild the index even if fresh")


def semantic_search(ctx: Any, args: SemanticSearchArgs) -> ToolResult:
    from spark.config import ensure_home
    from spark.semantic_index import make_code_index

    try:
        index = make_code_index(ctx.sandbox.root, ctx.config, ensure_home())
        if args.refresh or not index.exists():
            status = index.refresh(force=args.refresh)
        else:
            status = index.status()
        hits = index.search(args.query, top_k=args.top_k)
    except Exception as exc:  # noqa: BLE001 — retrieval must degrade, not crash the turn
        return ToolResult(ok=False, payload={"error": f"semantic index failed: {exc}"})
    return ToolResult(
        ok=True,
        payload={
            "query": args.query,
            "index": {
                "files": status.files,
                "chunks": status.chunks,
                "vectors": status.vectors,
                "path": str(index.index_path),
            },
            "hits": hits,
        },
    )
