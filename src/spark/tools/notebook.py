from __future__ import annotations

import json
import uuid

import nbformat
from pydantic import BaseModel

from spark.models import ToolResult
from spark.sandbox import WorkdirSandbox


class ReadNotebookArgs(BaseModel):
    path: str
    max_cells: int = 40


class NotebookEditArgs(BaseModel):
    path: str
    cell_index: int
    new_source: str
    cell_type: str | None = None  # optional: change to "code" or "markdown"


def _load(path) -> nbformat.NotebookNode:
    """Read and normalise a notebook through nbformat.

    Older/hand-written notebooks in the wild often omit ``nbformat_minor``,
    cell ``id`` and ``execution_count``. Fill those defaults before
    ``from_dict`` so a partial-but-readable notebook is repaired rather than
    rejected; nbformat still owns the schema and the writer.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("cells"), list):
        raise ValueError("not a valid notebook (missing cells)")
    data.setdefault("nbformat", 4)
    data.setdefault("nbformat_minor", 5)
    data.setdefault("metadata", {})
    for cell in data["cells"]:
        if not isinstance(cell, dict):
            raise ValueError("not a valid notebook (cell is not an object)")
        cell.setdefault("metadata", {})
        cell.setdefault("id", uuid.uuid4().hex[:8])
        if cell.get("cell_type") == "code":
            cell.setdefault("outputs", [])
            cell.setdefault("execution_count", None)
    return nbformat.from_dict(data)


def _source_text(cell) -> str:
    src = cell.get("source", "")
    return src if isinstance(src, str) else "".join(src)


def _output_preview(cell) -> str:
    if cell.get("cell_type") != "code":
        return ""
    parts: list[str] = []
    for out in cell.get("outputs", [])[:3]:
        if out.get("output_type") == "stream":
            parts.append(str(out.get("text", ""))[:300])
        elif out.get("output_type") in {"execute_result", "display_data"}:
            data = out.get("data", {})
            if "text/plain" in data:
                parts.append(str(data["text/plain"])[:300])
            elif "image/png" in data:
                parts.append("[image output]")
        elif out.get("output_type") == "error":
            parts.append(f"[error] {out.get('ename', '')}: {out.get('evalue', '')}")
    return "\n".join(parts)[:600]


def read_notebook(sandbox: WorkdirSandbox, args: ReadNotebookArgs) -> ToolResult:
    try:
        path = sandbox.resolve(args.path)
        nb = _load(path)
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    cells = nb.cells
    limit = max(1, min(200, args.max_cells))
    out = []
    for i, cell in enumerate(cells[:limit]):
        out.append(
            {
                "index": i,
                "id": cell.get("id", ""),
                "type": cell.get("cell_type", ""),
                "source": _source_text(cell)[:4000],
                "output_preview": _output_preview(cell),
            }
        )
    payload: dict = {"path": args.path, "cells": out, "count": len(out)}
    if len(cells) > limit:
        payload["note"] = f"showing first {limit} of {len(cells)} cells"
    return ToolResult(ok=True, payload=payload)


def notebook_edit(sandbox: WorkdirSandbox, args: NotebookEditArgs) -> ToolResult:
    if not sandbox.write_allowed:
        return ToolResult(ok=False, payload={"error": "notebook_edit is disabled: sandbox-only access mode"})
    try:
        path = sandbox.resolve(args.path)
        nb = _load(path)
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    cells = nb.cells
    if args.cell_index < 0 or args.cell_index >= len(cells):
        return ToolResult(ok=False, payload={"error": f"cell_index out of range (0..{len(cells) - 1})"})
    cell = cells[args.cell_index]
    new_type = args.cell_type or cell.get("cell_type")
    if new_type not in {"code", "markdown", "raw"}:
        return ToolResult(ok=False, payload={"error": "cell_type must be code, markdown or raw"})
    old_source = _source_text(cell)
    cell["cell_type"] = new_type
    cell["source"] = args.new_source.splitlines(keepends=True)
    cell.setdefault("metadata", {})
    if new_type == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    else:
        cell.pop("execution_count", None)
        cell.pop("outputs", None)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            nbformat.write(nb, handle)
        tmp.replace(path)
    except Exception as exc:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        return ToolResult(ok=False, payload={"error": str(exc)})
    return ToolResult(
        ok=True,
        payload={
            "path": args.path,
            "cell_index": args.cell_index,
            "cell_type": new_type,
            "old_source": old_source[:1000],
            "new_source": args.new_source[:1000],
        },
    )
