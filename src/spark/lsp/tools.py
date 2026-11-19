"""Tool adapters for the LSP client.

Every handler returns ``ToolResult`` and degrades visibly: an LSP tool that
cannot start a server either falls back to the syntax index (definition /
references / diagnostics / symbols) or returns a readable install hint.  It
never silently pretends to have semantic results.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from spark.lsp import impact
from spark.lsp.client import LSPClient, LSPError
from spark.lsp.manager import LSPManager, LSPUnavailable
from spark.lsp.workspace import (
    WorkspaceEditError,
    apply_plans,
    file_plan_payload,
    plan_workspace_edit,
    read_text,
    uri_to_path,
)
from spark.models import ToolResult
from spark.sandbox import SandboxPolicyError


class LSPPositionArgs(BaseModel):
    path: str
    line: int = Field(ge=1, description="1-based line")
    column: int = Field(ge=1, description="1-based column")


class LSPReferencesArgs(LSPPositionArgs):
    include_declaration: bool = True


class LSPSymbolsArgs(BaseModel):
    path: str
    query: str = ""


class LSPDiagnosticsArgs(BaseModel):
    path: str


class LSPRenameArgs(LSPPositionArgs):
    new_name: str
    dry_run: bool = True


class LSPCodeActionArgs(LSPPositionArgs):
    end_line: int | None = Field(default=None, ge=1)
    end_column: int | None = Field(default=None, ge=1)
    only: list[str] | None = None
    apply: bool = False


class AffectedTestsArgs(BaseModel):
    path: str
    symbol: str = ""


def _result(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(ok=True, payload=payload)


def _fail(message: str, **extra: Any) -> ToolResult:
    return ToolResult(ok=False, payload={"error": message, **extra})


def _resolve(ctx: Any, path: str) -> Path | ToolResult:
    try:
        resolved = ctx.sandbox.resolve(path)
    except (SandboxPolicyError, Exception) as exc:
        return _fail(str(exc))
    if not resolved.is_file():
        return _fail(f"file not found: {path}")
    return resolved


def _manager_info(manager: LSPManager | None) -> dict[str, Any]:
    if manager is None:
        return {"available": False, "server": "", "install_hint": ""}
    try:
        return manager.info(".")
    except Exception:
        return {"available": False, "server": "", "install_hint": ""}


def _client_for(manager: LSPManager | None, path: Path) -> LSPClient | ToolResult:
    if manager is None:
        return _fail("LSP manager is not attached to this registry")
    try:
        return manager.ensure_client(path)
    except LSPUnavailable as exc:
        return _fail(str(exc), language=exc.language, degraded=True)
    except LSPError as exc:
        return _fail(str(exc), degraded=True)


def _position(path: Path, line: int, column: int) -> dict[str, Any]:
    return {
        "textDocument": {"uri": path.as_uri()},
        "position": {"line": max(0, line - 1), "character": max(0, column - 1)},
    }


def _path_line(path: Path, line: int) -> str:
    try:
        text, _newline, _bom = read_text(path)
    except OSError:
        return ""
    rows = text.split("\n")
    if 1 <= line <= len(rows):
        return rows[line - 1]
    return ""


def _symbol_at(path: Path, line: int, column: int) -> str:
    text = _path_line(path, line)
    if not text:
        return ""
    index = max(0, min(len(text) - 1, column - 1))
    if index >= len(text) or not (text[index].isalnum() or text[index] in "_$"):
        # Allow the cursor immediately after a word.
        window = text[max(0, index - 1) : index + 2]
        match = re.search(r"[A-Za-z_$][A-Za-z0-9_$]*", window)
        return match.group(0) if match else ""
    start = index
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] in "_$"):
        start -= 1
    end = index + 1
    while end < len(text) and (text[end].isalnum() or text[end] in "_$"):
        end += 1
    return text[start:end]


def _location(root: Path, item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    uri = item.get("targetUri") or item.get("uri")
    range_obj = item.get("targetSelectionRange") or item.get("targetRange") or item.get("range")
    if not uri or not isinstance(range_obj, dict):
        return None
    try:
        target = uri_to_path(str(uri)).resolve()
        target.relative_to(root.resolve())
    except (WorkspaceEditError, ValueError, OSError):
        return None
    relative = str(target.relative_to(root.resolve())).replace("\\", "/")
    start = range_obj.get("start") or {}
    end = range_obj.get("end") or {}
    start_line = int(start.get("line", 0)) + 1
    start_col = int(start.get("character", 0)) + 1
    preview = _path_line(target, start_line).strip()[:180]
    return {
        "file": relative,
        "line": start_line,
        "col": start_col,
        "end_line": int(end.get("line", 0)) + 1,
        "end_col": int(end.get("character", 0)) + 1,
        "preview": preview,
    }


def _locations(root: Path, payload: Any) -> list[dict[str, Any]]:
    items: list[Any] = []
    if isinstance(payload, dict):
        items = [payload]
    elif isinstance(payload, list):
        items = payload
    out: list[dict[str, Any]] = []
    for item in items:
        loc = _location(root, item) if isinstance(item, dict) else None
        if loc:
            out.append(loc)
        if len(out) >= 100:
            break
    return out


def _hover_text(contents: Any) -> str:
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        return str(contents.get("value") or contents.get("language") or "")
    if isinstance(contents, list):
        return "\n\n".join(_hover_text(item) for item in contents if item)
    return ""


def _symbol_kind(kind: Any) -> str:
    names = {
        1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class",
        6: "method", 7: "property", 8: "field", 9: "constructor", 10: "enum",
        11: "interface", 12: "function", 13: "variable", 14: "constant",
        15: "string", 16: "number", 17: "boolean", 18: "array", 19: "object",
        20: "key", 21: "null", 22: "enum_member", 23: "struct", 24: "event",
        25: "operator", 26: "type_parameter",
    }
    try:
        return names.get(int(kind), str(kind))
    except (TypeError, ValueError):
        return str(kind)


def _document_symbols(root: Path, items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if "location" in item:
            loc = _location(root, item["location"])
            if loc:
                out.append({**loc, "name": str(item.get("name") or ""), "kind": _symbol_kind(item.get("kind"))})
            continue
        range_obj = item.get("selectionRange") or item.get("range") or {}
        start = range_obj.get("start") or {}
        out.append(
            {
                "name": str(item.get("name") or ""),
                "kind": _symbol_kind(item.get("kind")),
                "line": int(start.get("line", 0)) + 1,
                "col": int(start.get("character", 0)) + 1,
            }
        )
        children = item.get("children")
        if isinstance(children, list):
            out.extend(_document_symbols(root, children))
        if len(out) >= 300:
            break
    return out[:300]


_DIAG_SEVERITY = {1: "error", 2: "warning", 3: "information", 4: "hint"}


def _diagnostic_list(root: Path, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        range_obj = item.get("range") or {}
        start = range_obj.get("start") or {}
        end = range_obj.get("end") or {}
        raw_severity = item.get("severity")
        out.append(
            {
                "file": "",
                "line": int(start.get("line", 0)) + 1,
                "col": int(start.get("character", 0)) + 1,
                "end_line": int(end.get("line", 0)) + 1,
                "end_col": int(end.get("character", 0)) + 1,
                "severity": _DIAG_SEVERITY.get(raw_severity, "unknown")
                if isinstance(raw_severity, int)
                else "unknown",
                "code": str(item.get("code") or ""),
                "message": str(item.get("message") or "")[:240],
                "source": str(item.get("source") or ""),
            }
        )
        if len(out) >= 100:
            break
    return out


def _generic_definitions(root: Path, symbol: str) -> list[dict[str, Any]]:
    """Regex fallback for languages without an installed tree-sitter grammar."""
    patterns = [
        rf"(?:def|class|function|fn|func|type|interface|struct|enum|impl|const|let|var)\s+{re.escape(symbol)}\b",
        rf"\b{re.escape(symbol)}\s*(?:=|:=)\s*",
    ]
    out: list[dict[str, Any]] = []
    for path in _iter_text_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if any(re.search(pattern, line) for pattern in patterns):
                out.append(
                    {
                        "file": str(path.relative_to(root)).replace("\\", "/"),
                        "line": line_no,
                        "col": max(1, line.find(symbol) + 1),
                        "kind": "regex",
                    }
                )
                if len(out) >= 50:
                    return out
    return out


def _iter_text_files(root: Path):
    skip = {
        ".git", "node_modules", "dist", "build", ".venv", "venv", "__pycache__",
        ".mypy_cache", ".ruff_cache", ".pytest_cache", "target", "vendor",
    }
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            parts = path.parts
        if any(part in skip for part in parts):
            continue
        if path.suffix.lower() not in {
            ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs",
            ".c", ".h", ".cc", ".cpp", ".java", ".rb", ".lua",
        }:
            continue
        try:
            if path.stat().st_size > 512_000:
                continue
        except OSError:
            continue
        yield path


def _fallback_generic_references(root: Path, symbol: str) -> list[dict[str, Any]]:
    pattern = re.compile(rf"(?<![\w$]){re.escape(symbol)}(?![\w$])")
    out: list[dict[str, Any]] = []
    for path in _iter_text_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                out.append(
                    {
                        "file": str(path.relative_to(root)).replace("\\", "/"),
                        "line": line_no,
                        "text": line.strip()[:180],
                    }
                )
                if len(out) >= 80:
                    return out
    return out


def _fallback_definition(ctx: Any, path: Path, line: int, column: int) -> ToolResult:
    symbol = _symbol_at(path, line, column)
    if not symbol:
        return _fail("could not identify a symbol at that position")
    from spark.tools.symbols import find_symbol

    try:
        syntax = find_symbol(ctx.sandbox.root, symbol, mode="definition")
    except Exception as exc:  # pragma: no cover - defensive
        return _fail(f"symbol scan failed: {exc}")
    definitions = syntax.get("definitions") or []
    if not definitions:
        definitions = _generic_definitions(ctx.sandbox.root, symbol)
    return _result(
        {
            "definitions": definitions[:50],
            "symbol": symbol,
            "source": "tree-sitter" if definitions else "none",
            "degraded": True,
            "warning": "no LSP server available; returned syntax-level definitions",
        }
    )


def _fallback_references(ctx: Any, path: Path, line: int, column: int) -> ToolResult:
    symbol = _symbol_at(path, line, column)
    if not symbol:
        return _fail("could not identify a symbol at that position")
    refs = _fallback_generic_references(ctx.sandbox.root, symbol)
    return _result(
        {
            "references": refs,
            "symbol": symbol,
            "source": "text-scan",
            "degraded": True,
            "warning": "no LSP server available; returned text-level references",
        }
    )


def _fallback_symbols(ctx: Any, path: Path) -> ToolResult:
    symbols: list[dict[str, Any]] = []
    text, _nl, _bom = read_text(path)
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//", "/*", "*")):
            continue
        pattern = re.match(
            r"(?:async\s+)?(?:def|class|function|fn|func|type|interface|struct|enum|impl|const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)",
            stripped,
        )
        if pattern:
            symbols.append(
                {
                    "name": pattern.group(1),
                    "kind": "symbol",
                    "line": line_no,
                    "col": max(1, line.find(pattern.group(1)) + 1),
                }
            )
        if len(symbols) >= 200:
            break
    return _result(
        {
            "symbols": symbols,
            "source": "regex",
            "degraded": True,
            "warning": "no LSP server available; returned regex symbols",
        }
    )


def lsp_definition(
    ctx: Any, manager: LSPManager | None, args: LSPPositionArgs
) -> ToolResult:
    path = _resolve(ctx, args.path)
    if isinstance(path, ToolResult):
        return path
    client = _client_for(manager, path)
    if isinstance(client, ToolResult):
        return _fallback_definition(ctx, path, args.line, args.column)
    try:
        client.open_document(path)
        payload = client.request("textDocument/definition", _position(path, args.line, args.column))
    except (LSPError, OSError):
        return _fallback_definition(ctx, path, args.line, args.column)
    locations = _locations(ctx.sandbox.root, payload)
    return _result(
        {
            "definitions": locations,
            "source": "lsp",
            "server": client.name,
            "degraded": False,
        }
    )


def lsp_references(
    ctx: Any, manager: LSPManager | None, args: LSPReferencesArgs
) -> ToolResult:
    path = _resolve(ctx, args.path)
    if isinstance(path, ToolResult):
        return path
    client = _client_for(manager, path)
    if isinstance(client, ToolResult):
        return _fallback_references(ctx, path, args.line, args.column)
    try:
        client.open_document(path)
        params = _position(path, args.line, args.column)
        params["context"] = {"includeDeclaration": bool(args.include_declaration)}
        payload = client.request("textDocument/references", params)
    except (LSPError, OSError):
        return _fallback_references(ctx, path, args.line, args.column)
    return _result(
        {
            "references": _locations(ctx.sandbox.root, payload),
            "source": "lsp",
            "server": client.name,
            "degraded": False,
        }
    )


def lsp_hover(ctx: Any, manager: LSPManager | None, args: LSPPositionArgs) -> ToolResult:
    path = _resolve(ctx, args.path)
    if isinstance(path, ToolResult):
        return path
    client = _client_for(manager, path)
    if isinstance(client, ToolResult):
        return client
    try:
        client.open_document(path)
        payload = client.request("textDocument/hover", _position(path, args.line, args.column))
    except (LSPError, OSError) as exc:
        return _fail(str(exc), degraded=True)
    if not isinstance(payload, dict):
        return _result({"contents": "", "source": "lsp", "server": client.name})
    contents = _hover_text(payload.get("contents"))
    loc = _location(ctx.sandbox.root, {"uri": path.as_uri(), "range": payload.get("range")}) if payload.get("range") else None
    return _result(
        {
            "contents": contents,
            "range": loc,
            "source": "lsp",
            "server": client.name,
            "degraded": False,
        }
    )


def lsp_symbols(ctx: Any, manager: LSPManager | None, args: LSPSymbolsArgs) -> ToolResult:
    path = _resolve(ctx, args.path)
    if isinstance(path, ToolResult):
        return path
    client = _client_for(manager, path)
    if isinstance(client, ToolResult):
        return _fallback_symbols(ctx, path)
    try:
        client.open_document(path)
        if args.query:
            payload = client.request(
                "workspace/symbol", {"query": args.query, "limit": 100}
            )
            symbols: list[dict[str, Any]] = []
            if isinstance(payload, list):
                for item in payload:
                    if not isinstance(item, dict):
                        continue
                    loc = _location(ctx.sandbox.root, item.get("location") or {}) if item.get("location") else None
                    if loc:
                        symbols.append({**loc, "name": str(item.get("name") or ""), "kind": _symbol_kind(item.get("kind"))})
                    if len(symbols) >= 100:
                        break
        else:
            payload = client.request(
                "textDocument/documentSymbol", {"textDocument": {"uri": path.as_uri()}}
            )
            symbols = _document_symbols(ctx.sandbox.root, payload)
        return _result(
            {
                "symbols": symbols,
                "query": args.query,
                "source": "lsp",
                "server": client.name,
                "degraded": False,
            }
        )
    except (LSPError, OSError) as exc:
        return _fail(str(exc), degraded=True)


def _diagnostics_payload(root: Path, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean = _diagnostic_list(root, items)
    return clean


def lsp_diagnostics(
    ctx: Any, manager: LSPManager | None, args: LSPDiagnosticsArgs
) -> ToolResult:
    path = _resolve(ctx, args.path)
    if isinstance(path, ToolResult):
        return path
    client = _client_for(manager, path)
    if isinstance(client, ToolResult):
        from spark.tools.diagnostics import DiagnosticsArgs, diagnostics_tool

        fallback = diagnostics_tool(
            ctx.sandbox, DiagnosticsArgs(paths=[args.path], python=True, typescript=True)
        )
        fallback.payload["degraded"] = True
        fallback.payload["warning"] = "no LSP server available; used ruff/tsc fallback"
        fallback.payload["source"] = "ruff/tsc"
        return fallback
    try:
        uri = client.open_document(path)
        pulled = client.pull_diagnostics(uri)
        items = pulled if pulled is not None else client.diagnostics_for(uri, wait=1.5)
    except (LSPError, OSError) as exc:
        return _fail(str(exc), degraded=True)
    return _result(
        {
            "diagnostics": _diagnostics_payload(ctx.sandbox.root, items),
            "count": len(items),
            "source": "lsp",
            "server": client.name,
            "degraded": False,
        }
    )


def _rename_fallback(
    ctx: Any, path: Path, line: int, column: int, new_name: str, dry_run: bool
) -> ToolResult:
    symbol = _symbol_at(path, line, column)
    if not symbol:
        return _fail("could not identify a symbol at that position")
    if not dry_run:
        return _fail(
            "no LSP server available; syntax-level rename is dry-run only",
            symbol=symbol,
            hint="install a language server, then rerun with dry_run=false; otherwise use apply_edit after reviewing references",
        )
    refs = _fallback_generic_references(ctx.sandbox.root, symbol)
    return _result(
        {
            "symbol": symbol,
            "new_name": new_name,
            "dry_run": True,
            "applied": False,
            "can_apply": False,
            "files": [{"file": item["file"], "edits": 1} for item in refs],
            "references": refs,
            "source": "text-scan",
            "degraded": True,
            "warning": "no LSP server available; dry-run only, no files were written",
        }
    )


def _rename_result(
    ctx: Any, client: LSPClient, path: Path, args: LSPRenameArgs, edit: dict[str, Any]
) -> ToolResult:
    try:
        plans = plan_workspace_edit(ctx.sandbox.root, edit)
    except WorkspaceEditError as exc:
        return _fail(str(exc), degraded=False)
    if args.dry_run:
        return _result(
            {
                "symbol": _symbol_at(path, args.line, args.column),
                "new_name": args.new_name,
                "dry_run": True,
                "applied": False,
                "files": [file_plan_payload(plan) for plan in plans],
                "file_count": len(plans),
                "edit_count": sum(plan.edit_count for plan in plans),
                "source": "lsp",
                "server": client.name,
            }
        )
    try:
        outcome = apply_plans(ctx.sandbox, plans, dry_run=False)
    except WorkspaceEditError as exc:
        return _fail(str(exc), degraded=False)
    for plan in plans:
        try:
            client.refresh_document(plan.path)
        except Exception:  # noqa: BLE001 - refresh is best-effort
            pass
    return _result(
        {
            "symbol": _symbol_at(path, args.line, args.column),
            "new_name": args.new_name,
            "dry_run": False,
            "applied": True,
            "files": [file_plan_payload(plan) for plan in plans],
            "file_count": len(plans),
            "edit_count": sum(plan.edit_count for plan in plans),
            "written": outcome.get("written", []),
            "source": "lsp",
            "server": client.name,
        }
    )


def lsp_rename(ctx: Any, manager: LSPManager | None, args: LSPRenameArgs) -> ToolResult:
    path = _resolve(ctx, args.path)
    if isinstance(path, ToolResult):
        return path
    if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", args.new_name or ""):
        return _fail("new_name must be a single identifier")
    client = _client_for(manager, path)
    if isinstance(client, ToolResult):
        return _rename_fallback(ctx, path, args.line, args.column, args.new_name, args.dry_run)
    try:
        client.open_document(path)
        params = _position(path, args.line, args.column)
        params["newName"] = args.new_name
        # prepareRename is advisory; some servers implement rename without it.
        try:
            client.request("textDocument/prepareRename", _position(path, args.line, args.column))
        except LSPError:
            pass
        edit = client.request("textDocument/rename", params)
    except (LSPError, OSError) as exc:
        return _fail(f"rename failed: {exc}", degraded=True)
    if not isinstance(edit, dict):
        return _fail("LSP returned no WorkspaceEdit", degraded=False)
    return _rename_result(ctx, client, path, args, edit)


def lsp_code_action(
    ctx: Any, manager: LSPManager | None, args: LSPCodeActionArgs
) -> ToolResult:
    path = _resolve(ctx, args.path)
    if isinstance(path, ToolResult):
        return path
    client = _client_for(manager, path)
    if isinstance(client, ToolResult):
        return client
    if args.end_line is None or args.end_column is None:
        range_obj = {
            "start": {"line": args.line - 1, "character": args.column - 1},
            "end": {"line": args.line - 1, "character": args.column - 1},
        }
    else:
        range_obj = {
            "start": {"line": args.line - 1, "character": args.column - 1},
            "end": {"line": args.end_line - 1, "character": args.end_column - 1},
        }
    try:
        client.open_document(path)
        params: dict[str, Any] = {
            "textDocument": {"uri": path.as_uri()},
            "range": range_obj,
            "context": {"diagnostics": []},
        }
        if args.only:
            params["context"]["only"] = args.only
        actions = client.request("textDocument/codeAction", params)
    except (LSPError, OSError) as exc:
        return _fail(str(exc), degraded=True)
    if not isinstance(actions, list):
        return _fail("LSP returned no code actions", degraded=False)
    clean: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        item: dict[str, Any] = {
            "title": str(action.get("title") or ""),
            "kind": str(action.get("kind") or ""),
            "is_preferred": bool(action.get("isPreferred")),
            "has_edit": isinstance(action.get("edit"), dict),
            "has_command": bool(action.get("command")),
        }
        clean.append(item)
        if args.apply and isinstance(action.get("edit"), dict):
            try:
                plans = plan_workspace_edit(ctx.sandbox.root, action["edit"])
                outcome = apply_plans(ctx.sandbox, plans, dry_run=False)
                for plan in plans:
                    try:
                        client.refresh_document(plan.path)
                    except Exception:  # noqa: BLE001
                        pass
                applied.append(
                    {
                        "title": item["title"],
                        "files": [file_plan_payload(plan) for plan in plans],
                        "written": outcome.get("written", []),
                    }
                )
            except WorkspaceEditError as exc:
                applied.append({"title": item["title"], "error": str(exc)})
        if len(clean) >= 50:
            break
    return _result(
        {
            "actions": clean,
            "applied": applied,
            "source": "lsp",
            "server": client.name,
            "note": "server commands are never executed; only WorkspaceEdit actions can be applied",
        }
    )


def affected_tests(ctx: Any, args: AffectedTestsArgs) -> ToolResult:
    result = impact.analyze_impact(
        ctx.sandbox.root, args.path, symbol=args.symbol
    )
    if result.get("error"):
        return _fail(str(result["error"]))
    return _result(result)


__all__ = [
    "AffectedTestsArgs",
    "LSPCodeActionArgs",
    "LSPDiagnosticsArgs",
    "LSPPositionArgs",
    "LSPReferencesArgs",
    "LSPRenameArgs",
    "LSPSymbolsArgs",
    "affected_tests",
    "lsp_code_action",
    "lsp_definition",
    "lsp_diagnostics",
    "lsp_hover",
    "lsp_references",
    "lsp_rename",
    "lsp_symbols",
]
