from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from spark.errors import PathEscapeError
from spark.models import ToolResult
from spark.sandbox import SandboxPolicyError, WorkdirSandbox


class ReadFileArgs(BaseModel):
    path: str
    offset: int | None = None
    limit: int | None = 200


class ListDirArgs(BaseModel):
    path: str = "."
    max_entries: int = 200


class WriteFileArgs(BaseModel):
    path: str
    content: str


class ApplyPatchArgs(BaseModel):
    path: str
    old_text: str
    new_text: str


MAX_READ_BYTES = 5 * 1024 * 1024
MAX_READ_LINES = 2000

_UTF8_BOM = "\ufeff"


def _decode_file(path: Path) -> tuple[str, str, str]:
    """Read a file as (text with ``\\n`` endings, original newline, BOM prefix).

    Matching is done on normalised text while the original line endings and BOM
    are remembered, so editing one line of a CRLF file does not rewrite every
    other line too.
    """
    raw = path.read_bytes().decode("utf-8", errors="replace")
    bom = ""
    if raw.startswith(_UTF8_BOM):
        bom = _UTF8_BOM
        raw = raw[len(_UTF8_BOM) :]
    newline = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), newline, bom


def _encode_file(text: str, newline: str, bom: str) -> str:
    if newline == "\r\n":
        text = text.replace("\n", "\r\n")
    return bom + text


def read_file(sandbox: WorkdirSandbox, args: ReadFileArgs) -> ToolResult:
    try:
        path = sandbox.resolve(args.path)
    except (PathEscapeError, SandboxPolicyError) as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    if not path.exists() or not path.is_file():
        return ToolResult(ok=False, payload={"error": f"File not found: {args.path}"})
    try:
        size = path.stat().st_size
    except OSError as exc:
        return ToolResult(ok=False, payload={"error": f"cannot stat file: {exc}"})
    if size > MAX_READ_BYTES:
        return ToolResult(
            ok=False,
            payload={
                "error": (
                    f"file is too large to read ({size} bytes, limit "
                    f"{MAX_READ_BYTES}); use grep or read a slice instead"
                ),
                "path": args.path,
                "size": size,
            },
        )
    try:
        text, _newline, _bom = _decode_file(path)
    except OSError as exc:
        return ToolResult(ok=False, payload={"error": f"cannot read file: {exc}"})
    rows = text.splitlines()
    total = len(rows)
    start = 1 if args.offset is None else max(int(args.offset), 1)
    limit = 200 if args.limit is None else max(1, min(int(args.limit), MAX_READ_LINES))
    if start > total:
        return ToolResult(
            ok=False,
            payload={
                "error": f"offset {start} is past the end of {args.path} ({total} lines)",
                "path": args.path,
                "total_lines": total,
            },
        )
    window = rows[start - 1 : start - 1 + limit]
    last = start - 1 + len(window)
    return ToolResult(
        ok=True,
        payload={
            "path": args.path,
            "content": "\n".join(window),
            "total_lines": total,
            "first_line": start,
            "last_line": last,
            # Without this flag the model cannot tell a short file from a file it
            # was only shown part of, and will conclude code is missing.
            "truncated": last < total,
        },
    )


def list_dir(sandbox: WorkdirSandbox, args: ListDirArgs) -> ToolResult:
    try:
        path = sandbox.resolve(args.path)
    except (PathEscapeError, SandboxPolicyError) as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    if not path.exists() or not path.is_dir():
        return ToolResult(
            ok=False, payload={"error": f"Directory not found: {args.path}"}
        )
    entries: list[dict[str, Any]] = []
    for child in sorted(path.iterdir(), key=lambda p: p.name)[: args.max_entries]:
        item: dict[str, Any] = {"name": child.name, "type": "dir" if child.is_dir() else "file"}
        if child.is_file():
            item["size"] = child.stat().st_size
        entries.append(item)
    return ToolResult(ok=True, payload={"path": args.path, "entries": entries})


def write_file(sandbox: WorkdirSandbox, args: WriteFileArgs) -> ToolResult:
    if not sandbox.write_allowed:
        return ToolResult(
            ok=False,
            payload={"error": "write_file is disabled: sandbox-only access mode"},
        )
    try:
        path = sandbox.resolve(args.path)
    except (PathEscapeError, SandboxPolicyError) as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    reason = sandbox.check_write_path(path)
    if reason:
        return ToolResult(ok=False, payload={"error": reason})
    path.parent.mkdir(parents=True, exist_ok=True)
    newline, bom = "\n", ""
    if path.is_file():
        _old, newline, bom = _decode_file(path)
    try:
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(_encode_file(args.content, newline, bom))
    except OSError as exc:
        return ToolResult(ok=False, payload={"error": f"cannot write file: {exc}"})
    payload: dict[str, Any] = {
        "path": args.path,
        "bytes": len(args.content.encode("utf-8")),
    }
    _maybe_format(sandbox, path, payload)
    return ToolResult(ok=True, payload=payload)


def _maybe_format(sandbox: WorkdirSandbox, path: Path, payload: dict[str, Any]) -> None:
    """写后格式化；结果如实写进 payload，失败不影响写入成功。"""
    cfg = getattr(sandbox, "cfg", None)
    enabled = bool(getattr(getattr(cfg, "agent", None), "format_on_write", False))
    if not enabled:
        return
    from spark.tools.formatting import format_file

    outcome = format_file(path, sandbox.root, enabled=True)
    if outcome.get("formatted"):
        payload["formatted"] = outcome.get("tool") or True
    elif outcome.get("error"):
        # 明确告知"写了但没格式化"，避免模型以为已经格式化过。
        payload["format_skipped"] = outcome["error"]


def apply_patch(sandbox: WorkdirSandbox, args: ApplyPatchArgs) -> ToolResult:
    if not sandbox.write_allowed:
        return ToolResult(
            ok=False,
            payload={"error": "apply_patch is disabled: sandbox-only access mode"},
        )
    try:
        path = sandbox.resolve(args.path)
    except (PathEscapeError, SandboxPolicyError) as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    reason = sandbox.check_write_path(path)
    if reason:
        return ToolResult(ok=False, payload={"error": reason})
    if not path.exists() or not path.is_file():
        return ToolResult(ok=False, payload={"error": f"File not found: {args.path}"})
    try:
        text, newline, bom = _decode_file(path)
    except OSError as exc:
        return ToolResult(ok=False, payload={"error": f"cannot read file: {exc}"})
    count = text.count(args.old_text)
    if count != 1:
        return ToolResult(
            ok=False,
            payload={
                "error": f"old_text matched {count} times; expected 1",
                "path": args.path,
            },
        )
    updated = text.replace(args.old_text, args.new_text, 1)
    try:
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(_encode_file(updated, newline, bom))
    except OSError as exc:
        return ToolResult(ok=False, payload={"error": f"cannot write file: {exc}"})
    payload: dict[str, Any] = {"path": args.path, "patched": True}
    _maybe_format(sandbox, path, payload)
    return ToolResult(ok=True, payload=payload)


def write_summary(path: str, content: str) -> str:
    preview = content if len(content) <= 4000 else content[:4000] + "\n...truncated..."
    return f"write_file {path}\n{preview}"


MAX_DIFF_CHARS = 6000


def _unified(path_str: str, before: str, after: str) -> str:
    lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"a/{path_str}",
            tofile=f"b/{path_str}",
            lineterm="",
            n=3,
        )
    )
    if not lines:
        return f"{path_str}: no change"
    text = "\n".join(lines)
    return text[:MAX_DIFF_CHARS] + "\n...diff truncated..." if len(text) > MAX_DIFF_CHARS else text


def diff_for_write(sandbox: WorkdirSandbox, path_str: str, content: str) -> str:
    """Real diff of ``write_file`` against what is on disk right now."""
    try:
        path = sandbox.resolve(path_str)
        before = _decode_file(path)[0] if path.is_file() else ""
    except (OSError, PathEscapeError, SandboxPolicyError) as exc:
        return f"write_file {path_str}: cannot read current contents ({exc})"
    return _unified(path_str, before, content)


def patch_diff(sandbox: WorkdirSandbox, path: str, old: str, new: str) -> str:
    """Real diff of ``apply_patch``: replace *old* once in the file, then diff."""
    try:
        resolved = sandbox.resolve(path)
        if not resolved.is_file():
            return f"apply_patch {path}: file does not exist yet"
        text = _decode_file(resolved)[0]
    except (OSError, PathEscapeError, SandboxPolicyError) as exc:
        return f"apply_patch {path}: cannot read current contents ({exc})"
    if text.count(old) != 1:
        return f"apply_patch {path}: old_text matched {text.count(old)} times, expected 1"
    return _unified(path, text, text.replace(old, new, 1))
