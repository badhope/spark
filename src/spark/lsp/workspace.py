"""Workspace-edit planning/applying shared by LSP rename and code actions.

The LSP server only returns edits; Spark writes them.  That is deliberate:
``workspace/applyEdit`` is denied in :mod:`spark.lsp.client`, so every byte
changed by an LSP operation still goes through the same sandbox check and
approval path as ``apply_edit``.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from spark.sandbox import WorkdirSandbox

_UTF8_BOM = "\ufeff"


class WorkspaceEditError(ValueError):
    """The server returned an edit Spark cannot apply safely."""


@dataclass
class FileEditPlan:
    path: Path
    relative_path: str
    old_text: str
    new_text: str
    diff: str
    edit_count: int


def read_text(path: Path) -> tuple[str, str, str]:
    """Return (normalized text, newline, BOM), preserving CRLF/BOM on write."""
    raw = path.read_bytes().decode("utf-8", errors="replace")
    bom = ""
    if raw.startswith(_UTF8_BOM):
        bom = _UTF8_BOM
        raw = raw[len(_UTF8_BOM) :]
    newline = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), newline, bom


def write_text(path: Path, text: str, newline: str, bom: str) -> None:
    rendered = text.replace("\n", newline)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(bom + rendered)


def uri_to_path(uri: str) -> Path:
    parsed = urlparse(str(uri or ""))
    if parsed.scheme and parsed.scheme != "file":
        raise WorkspaceEditError(f"unsupported URI scheme: {parsed.scheme}")
    raw = unquote(parsed.path or "")
    if parsed.netloc and parsed.netloc not in {"", "localhost"}:
        raw = f"//{parsed.netloc}{raw}"
    # Windows file URIs are ``file:///C:/...``. Strip the URI's leading slash
    # before constructing Path; Path('/C:/...') on Windows normalizes to a
    # drive-relative string and makes every edit look outside the workspace.
    if raw.startswith("/") and len(raw) > 3 and raw[2] == ":":
        raw = raw[1:]
    return Path(raw)


def position_to_offset(text: str, line: int, character: int) -> int:
    """Convert an LSP 0-based, UTF-16 position to a Python string offset."""
    if line < 0:
        raise WorkspaceEditError("negative line position")
    lines = text.split("\n")
    if line >= len(lines):
        raise WorkspaceEditError(
            f"line {line + 1} is past the end of the document ({len(lines)} lines)"
        )
    line_text = lines[line]
    start = sum(len(part) + 1 for part in lines[:line])
    used = 0
    index = 0
    while index < len(line_text) and used < character:
        used += 2 if ord(line_text[index]) > 0xFFFF else 1
        index += 1
    # Clamp rather than fail on a position that points past the line; servers
    # occasionally return end-of-line positions after a trim.
    return start + index


def span_for_range(text: str, range_obj: dict[str, Any]) -> tuple[int, int]:
    if not isinstance(range_obj, dict):
        raise WorkspaceEditError("edit has no range")
    start = range_obj.get("start") or {}
    end = range_obj.get("end") or {}
    try:
        return (
            position_to_offset(text, int(start.get("line", 0)), int(start.get("character", 0))),
            position_to_offset(text, int(end.get("line", 0)), int(end.get("character", 0))),
        )
    except (TypeError, ValueError) as exc:
        raise WorkspaceEditError("invalid edit range") from exc


def apply_text_edits(text: str, edits: list[dict[str, Any]]) -> str:
    """Apply LSP TextEdits, rejecting overlapping server edits."""
    spans: list[tuple[int, int, str]] = []
    for item in edits:
        if not isinstance(item, dict):
            continue
        start, end = span_for_range(text, item.get("range") or {})
        if start > end:
            raise WorkspaceEditError("edit range starts after it ends")
        spans.append((start, end, str(item.get("newText") or "")))
    if not spans:
        return text
    # Overlap is checked in source order, including insertions at the same
    # point (two inserts at one offset would make the intended order ambiguous).
    spans.sort(key=lambda x: (x[0], x[1]))
    previous_end = -1
    previous_start = -1
    for start, end, _ in spans:
        if start < previous_end or (start == previous_start and start == end):
            raise WorkspaceEditError("overlapping LSP edits are not supported")
        previous_start, previous_end = start, end
    updated = text
    for start, end, replacement in sorted(spans, key=lambda x: (x[0], x[1]), reverse=True):
        updated = updated[:start] + replacement + updated[end:]
    return updated


def _edit_changes(edit: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    changes = edit.get("changes")
    if isinstance(changes, dict):
        return {
            str(uri): [item for item in items if isinstance(item, dict)]
            for uri, items in changes.items()
            if isinstance(items, list)
        }
    document_changes = edit.get("documentChanges")
    if isinstance(document_changes, list):
        out: dict[str, list[dict[str, Any]]] = {}
        for item in document_changes:
            if not isinstance(item, dict):
                continue
            text_document = item.get("textDocument")
            if isinstance(text_document, dict) and text_document.get("uri"):
                edits = item.get("edits")
                if isinstance(edits, list):
                    out.setdefault(str(text_document["uri"]), []).extend(
                        e for e in edits if isinstance(e, dict)
                    )
                continue
            # RenameFile/CreateFile/DeleteFile are intentionally not applied by
            # this code path: they have different security and rollback rules.
            if item.get("kind") in {"create", "rename", "delete"}:
                raise WorkspaceEditError(
                    "LSP file operations (create/rename/delete) are not supported; use fs tools"
                )
        return out
    return {}


def plan_workspace_edit(root: Path, edit: dict[str, Any]) -> list[FileEditPlan]:
    """Convert a WorkspaceEdit into per-file plans without writing anything."""
    if not isinstance(edit, dict):
        raise WorkspaceEditError("LSP returned an invalid WorkspaceEdit")
    changes = _edit_changes(edit)
    if not changes:
        raise WorkspaceEditError("LSP returned an empty WorkspaceEdit")
    root = root.resolve()
    plans: list[FileEditPlan] = []
    for uri, edits in changes.items():
        path = uri_to_path(uri).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise WorkspaceEditError(f"edit escapes the workspace: {path}") from exc
        if not path.is_file():
            raise WorkspaceEditError(f"edit targets a missing file: {path}")
        old_text, _newline, _bom = read_text(path)
        try:
            new_text = apply_text_edits(old_text, edits)
        except WorkspaceEditError:
            raise
        relative = str(path.relative_to(root)).replace("\\", "/")
        diff = "".join(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
                n=3,
            )
        )
        plans.append(
            FileEditPlan(
                path=path,
                relative_path=relative,
                old_text=old_text,
                new_text=new_text,
                diff=diff,
                edit_count=len(edits),
            )
        )
    return sorted(plans, key=lambda p: p.relative_path)


def apply_plans(
    sandbox: WorkdirSandbox, plans: list[FileEditPlan], *, dry_run: bool
) -> dict[str, Any]:
    """Validate all plans, then write every file. No writes in dry-run."""
    if not plans:
        raise WorkspaceEditError("no edits to apply")
    if not sandbox.write_allowed:
        raise WorkspaceEditError("writes are disabled by the current sandbox mode")
    for plan in plans:
        reason = sandbox.check_write_path(plan.path)
        if reason:
            raise WorkspaceEditError(f"{plan.relative_path}: {reason}")
    if dry_run:
        return {"applied": False, "files": len(plans)}

    # Preflight every file before the first write: a server edit is planned
    # against a snapshot, and a concurrent user/tool edit must abort the whole
    # rename rather than leave a half-applied workspace.
    snapshots: list[tuple[Any, str, str, str]] = []
    for plan in plans:
        old_text, newline, bom = read_text(plan.path)
        if old_text != plan.old_text:
            raise WorkspaceEditError(
                f"{plan.relative_path}: file changed after the edit was planned; re-run rename"
            )
        snapshots.append((plan, old_text, newline, bom))

    written: list[str] = []
    try:
        for plan, _old_text, newline, bom in snapshots:
            # Track before opening: write_text truncates the target, so a failure
            # part-way through this file must include it in the rollback set.
            written.append(plan.relative_path)
            write_text(plan.path, plan.new_text, newline, bom)
    except OSError as exc:
        # Best-effort rollback: keep the failure explicit and restore files that
        # were already replaced. A rollback failure is reported alongside it.
        rollback_errors: list[str] = []
        for plan, old_text, newline, bom in reversed(snapshots[: len(written)]):
            try:
                write_text(plan.path, old_text, newline, bom)
            except OSError as rollback_exc:  # pragma: no cover - disk failure
                rollback_errors.append(f"{plan.relative_path}: {rollback_exc}")
        detail = f"; rollback errors: {'; '.join(rollback_errors)}" if rollback_errors else ""
        raise WorkspaceEditError(
            f"{plan.relative_path}: write failed after {len(written)} file(s) were written: {exc}{detail}"
        ) from exc
    return {"applied": True, "files": len(plans), "written": written}


def _safe_path_text(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def file_plan_payload(plan: FileEditPlan, *, include_diff: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "file": plan.relative_path,
        "edits": plan.edit_count,
    }
    if include_diff:
        payload["diff"] = plan.diff[:20_000]
    return payload


__all__ = [
    "FileEditPlan",
    "WorkspaceEditError",
    "apply_plans",
    "apply_text_edits",
    "file_plan_payload",
    "plan_workspace_edit",
    "position_to_offset",
    "read_text",
    "span_for_range",
    "uri_to_path",
    "write_text",
]
