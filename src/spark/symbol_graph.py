"""Cross-language syntax-level symbol graph and call-chain queries.

This is intentionally not an LSP replacement.  It uses tree-sitter grammars
already shipped with Spark to answer the questions an agent asks before editing:

* where is this symbol defined?
* which functions call it (callers / blast radius)?
* what does this function call (callees)?
* which symbols are reachable from a change site within N hops?

Resolution is name-based, so duplicate names are reported as ``ambiguous``
instead of being silently collapsed.  Languages without a grammar degrade to
an empty graph; callers must treat that as "unknown", not "no callers".
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_FILES = 400
MAX_FILE_BYTES = 512_000
MAX_DEPTH = 4
_SKIP_PARTS = {
    "node_modules", ".git", ".hg", ".svn", "dist", "build", ".venv", "venv",
    "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache", "target",
    "vendor", "coverage", ".next", ".nuxt", ".idea", ".vscode",
}

_EXT_LANG = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
}

_DEF_QUERIES: dict[str, list[str]] = {
    "python": [
        "(function_definition name: (identifier) @name)",
        "(class_definition name: (identifier) @name)",
    ],
    "typescript": [
        "(function_declaration name: (identifier) @name)",
        "(class_declaration name: (type_identifier) @name)",
        "(method_definition name: (property_identifier) @name)",
        "(interface_declaration name: (type_identifier) @name)",
        "(type_alias_declaration name: (type_identifier) @name)",
        "(variable_declarator name: (identifier) @name)",
    ],
    "tsx": [
        "(function_declaration name: (identifier) @name)",
        "(class_declaration name: (type_identifier) @name)",
        "(method_definition name: (property_identifier) @name)",
        "(interface_declaration name: (type_identifier) @name)",
        "(type_alias_declaration name: (type_identifier) @name)",
        "(variable_declarator name: (identifier) @name)",
    ],
    "javascript": [
        "(function_declaration name: (identifier) @name)",
        "(class_declaration name: (identifier) @name)",
        "(method_definition name: (property_identifier) @name)",
        "(variable_declarator name: (identifier) @name)",
    ],
    "go": [
        "(function_declaration name: (identifier) @name)",
        "(method_declaration name: (field_identifier) @name)",
        "(type_declaration (type_spec name: (type_identifier) @name))",
    ],
    "rust": [
        "(function_item name: (identifier) @name)",
        "(struct_item name: (type_identifier) @name)",
        "(enum_item name: (type_identifier) @name)",
        "(trait_item name: (type_identifier) @name)",
    ],
}

_CALL_QUERIES: dict[str, list[str]] = {
    "python": [
        "(call function: (identifier) @call)",
        "(call function: (attribute attribute: (identifier) @call))",
    ],
    "typescript": [
        "(call_expression function: (identifier) @call)",
        "(call_expression function: (member_expression property: (property_identifier) @call))",
    ],
    "tsx": [
        "(call_expression function: (identifier) @call)",
        "(call_expression function: (member_expression property: (property_identifier) @call))",
    ],
    "javascript": [
        "(call_expression function: (identifier) @call)",
        "(call_expression function: (member_expression property: (property_identifier) @call))",
    ],
    "go": [
        "(call_expression function: (identifier) @call)",
        "(call_expression function: (selector_expression field: (field_identifier) @call))",
    ],
    "rust": [
        "(call_expression function: (identifier) @call)",
        "(call_expression function: (field_expression field: (field_identifier) @call))",
        "(call_expression function: (scoped_identifier name: (identifier) @call))",
    ],
}

_FUNCTION_NODE_TYPES = {
    "python": ("function_definition",),
    "typescript": ("function_declaration", "method_definition"),
    "tsx": ("function_declaration", "method_definition"),
    "javascript": ("function_declaration", "method_definition"),
    "go": ("function_declaration", "method_declaration"),
    "rust": ("function_item",),
}

_CLASS_NODE_TYPES = {
    "python": ("class_definition",),
    "typescript": ("class_declaration",),
    "tsx": ("class_declaration",),
    "javascript": ("class_declaration",),
    "go": ("type_declaration",),
    "rust": ("impl_item", "struct_item", "trait_item"),
}


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    path: str
    line: int
    end_line: int = 0
    qualname: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CallEdge:
    caller_name: str
    caller_path: str
    caller_line: int
    callee_name: str
    call_line: int
    callee_path: str = ""
    callee_line: int = 0
    callee_qualname: str = ""
    confidence: str = "unresolved"  # same_file / unique / ambiguous / unresolved
    candidates: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["candidates"] = list(self.candidates)
        return payload


@dataclass
class SymbolGraph:
    root: Path
    symbols: list[Symbol] = field(default_factory=list)
    calls: list[CallEdge] = field(default_factory=list)
    scanned_files: int = 0
    skipped_files: int = 0

    def __post_init__(self) -> None:
        self._reindex()

    def _reindex(self) -> None:
        self._by_name = defaultdict(list)
        self._by_path = defaultdict(list)
        for symbol in self.symbols:
            self._by_name[symbol.name].append(symbol)
            self._by_path[symbol.path].append(symbol)

    def find(self, name: str) -> list[Symbol]:
        """Return every definition with this simple name or qualname."""
        name = (name or "").strip()
        if not name:
            return []
        exact = [s for s in self.symbols if s.qualname == name]
        if exact:
            return exact
        return list(self._by_name.get(name, []))

    def callers(self, name: str, *, depth: int = 1) -> list[dict[str, Any]]:
        """Reverse call chain: functions that call *name*, up to *depth* hops."""
        name = (name or "").strip()
        if not name or depth < 1:
            return []
        depth = min(int(depth), MAX_DEPTH)
        reverse: dict[str, list[CallEdge]] = defaultdict(list)
        for edge in self.calls:
            reverse[edge.callee_name].append(edge)
            if edge.callee_qualname:
                reverse[edge.callee_qualname].append(edge)

        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        queue: deque[tuple[str, int]] = deque([(name, 0)])
        while queue:
            current, level = queue.popleft()
            if level >= depth:
                continue
            for edge in reverse.get(current, []):
                key = f"{edge.caller_path}:{edge.caller_line}:{edge.caller_name}"
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "depth": level + 1,
                        "symbol": edge.caller_name,
                        "path": edge.caller_path,
                        "line": edge.caller_line,
                        "callee": edge.callee_name,
                        "callee_path": edge.callee_path,
                        "callee_line": edge.callee_line,
                        "confidence": edge.confidence,
                    }
                )
                queue.append((edge.caller_name, level + 1))
        return out

    def callees(self, name: str, *, depth: int = 1) -> list[dict[str, Any]]:
        """Forward call chain: functions called by *name*, up to *depth* hops."""
        name = (name or "").strip()
        if not name or depth < 1:
            return []
        depth = min(int(depth), MAX_DEPTH)
        forward: dict[str, list[CallEdge]] = defaultdict(list)
        for edge in self.calls:
            forward[edge.caller_name].append(edge)
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        queue: deque[tuple[str, int]] = deque([(name, 0)])
        while queue:
            current, level = queue.popleft()
            if level >= depth:
                continue
            for edge in forward.get(current, []):
                key = (
                    f"{edge.caller_path}:{edge.caller_line}:"
                    f"{edge.callee_name}:{edge.call_line}"
                )
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "depth": level + 1,
                        "symbol": edge.callee_name,
                        "path": edge.callee_path,
                        "line": edge.callee_line,
                        "caller": edge.caller_name,
                        "caller_path": edge.caller_path,
                        "caller_line": edge.caller_line,
                        "confidence": edge.confidence,
                    }
                )
                queue.append((edge.callee_name, level + 1))
        return out

    def impact(self, name: str, *, depth: int = 2) -> dict[str, Any]:
        """A readable blast-radius report for a symbol."""
        symbols = self.find(name)
        return {
            "symbol": name,
            "definitions": [s.to_dict() for s in symbols],
            "callers": self.callers(name, depth=depth),
            "callees": self.callees(name, depth=depth),
            "depth": min(int(depth), MAX_DEPTH),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "scanned_files": self.scanned_files,
            "skipped_files": self.skipped_files,
            "symbols": [s.to_dict() for s in self.symbols],
            "calls": [c.to_dict() for c in self.calls],
        }


def _language(name: str):
    try:
        if name == "python":
            import tree_sitter_python
            from tree_sitter import Language

            return Language(tree_sitter_python.language())
        if name in {"typescript", "tsx"}:
            import tree_sitter_typescript
            from tree_sitter import Language

            mod = tree_sitter_typescript
            return Language(
                mod.language_typescript()
                if name == "typescript"
                else mod.language_tsx()
            )
        if name == "javascript":
            import tree_sitter_javascript
            from tree_sitter import Language

            return Language(tree_sitter_javascript.language())
        if name == "go":
            import tree_sitter_go
            from tree_sitter import Language

            return Language(tree_sitter_go.language())
        if name == "rust":
            import tree_sitter_rust
            from tree_sitter import Language

            return Language(tree_sitter_rust.language())
    except Exception as exc:  # pragma: no cover - optional grammar
        logger.debug("tree-sitter %s unavailable: %s", name, exc)
    return None


@lru_cache(maxsize=None)
def _cached_language(name: str):
    return _language(name)


def _node_text(source: bytes, node) -> str:
    try:
        return source[node.start_byte : node.end_byte].decode(
            "utf-8", errors="replace"
        ).strip()
    except Exception:  # pragma: no cover
        return ""


_QUERY_CACHE: dict[tuple[str, str], Any] = {}


def _compiled_query(language_name: str, language, query_src: str):
    """Compile each tree-sitter query once per language.

    Query compilation is far more expensive than parsing a small source file;
    rebuilding it for every file made a 400-file scan take minutes.
    """
    from tree_sitter import Query

    key = (language_name, query_src)
    cached = _QUERY_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        query = Query(language, query_src)
    except Exception as exc:  # pragma: no cover - grammar version drift
        logger.debug("query failed: %s (%s)", query_src, exc)
        _QUERY_CACHE[key] = False
        return False
    _QUERY_CACHE[key] = query
    return query


_PARSER_CACHE: dict[str, Any] = {}


def _parser(language_name: str, language):
    from tree_sitter import Parser

    parser = _PARSER_CACHE.get(language_name)
    if parser is None:
        parser = Parser(language)
        _PARSER_CACHE[language_name] = parser
    return parser


def _captures(
    language_name: str, language, source: bytes, root, query_src: str, capture: str
):
    from tree_sitter import QueryCursor

    query = _compiled_query(language_name, language, query_src)
    if not query:
        return []
    try:
        groups = QueryCursor(query).captures(root)
    except Exception as exc:  # pragma: no cover
        logger.debug("capture failed: %s", exc)
        return []
    if isinstance(groups, dict):
        return list(groups.get(capture, []))
    # Very old tree-sitter API: iterable of (node, capture_name).
    return [item[0] for item in groups if item[1] == capture]


def _enclosing_function_name(source: bytes, node, language_name: str) -> tuple[str, int]:
    types = _FUNCTION_NODE_TYPES.get(language_name, ())
    current = node.parent
    while current is not None:
        if current.type in types:
            name_node = current.child_by_field_name("name")
            if name_node is not None:
                return _node_text(source, name_node), current.start_point[0] + 1
        current = current.parent
    return "", 0


def _enclosing_container(source: bytes, node, language_name: str) -> str:
    types = _CLASS_NODE_TYPES.get(language_name, ())
    current = node.parent
    while current is not None:
        if current.type in types:
            if language_name == "rust" and current.type == "impl_item":
                type_node = current.child_by_field_name("type")
                if type_node is not None:
                    return _node_text(source, type_node)
            name_node = current.child_by_field_name("name")
            if name_node is not None:
                return _node_text(source, name_node)
        current = current.parent
    return ""


def _iter_source_files(root: Path, *, max_files: int = MAX_FILES):
    import os

    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name for name in dirnames if name not in _SKIP_PARTS
        )
        for filename in sorted(filenames):
            if count >= max_files:
                return
            path = Path(dirpath) / filename
            if path.suffix.lower() not in _EXT_LANG:
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            count += 1
            yield path


def _parse_file(root: Path, path: Path) -> tuple[list[Symbol], list[dict[str, Any]]]:
    language_name = _EXT_LANG.get(path.suffix.lower())
    if not language_name:
        return [], []
    language = _cached_language(language_name)
    if language is None:
        return [], []
    try:
        source = path.read_bytes()
        if not source:
            return [], []
        tree = _parser(language_name, language).parse(source)
        root_node = tree.root_node
    except Exception as exc:  # pragma: no cover - parser boundary
        logger.debug("symbol parse failed for %s: %s", path, exc)
        return [], []

    relative = path.relative_to(root).as_posix()
    symbols: list[Symbol] = []
    for query_src in _DEF_QUERIES.get(language_name, []):
        for node in _captures(
            language_name, language, source, root_node, query_src, "name"
        ):
            name = _node_text(source, node)
            if not name:
                continue
            kind = node.parent.type if node.parent is not None else "symbol"
            container = _enclosing_container(source, node, language_name)
            qualname = f"{container}.{name}" if container else name
            symbols.append(
                Symbol(
                    name=name,
                    kind=kind,
                    path=relative,
                    line=node.start_point[0] + 1,
                    end_line=(
                        node.parent.end_point[0] + 1
                        if node.parent is not None
                        else node.start_point[0] + 1
                    ),
                    qualname=qualname,
                )
            )

    calls: list[dict[str, Any]] = []
    for query_src in _CALL_QUERIES.get(language_name, []):
        for node in _captures(
            language_name, language, source, root_node, query_src, "call"
        ):
            callee = _node_text(source, node)
            if not callee:
                continue
            caller, caller_line = _enclosing_function_name(
                source, node, language_name
            )
            if not caller:
                continue
            calls.append(
                {
                    "caller_name": caller,
                    "caller_path": relative,
                    "caller_line": caller_line,
                    "callee_name": callee,
                    "call_line": node.start_point[0] + 1,
                }
            )
    return symbols, calls


def build_symbol_graph(root: Path, *, max_files: int = MAX_FILES) -> SymbolGraph:
    """Build a bounded, syntax-level graph for Python/TS/JS/Go/Rust."""
    root = root.resolve()
    graph = SymbolGraph(root=root)
    if not root.exists():
        return graph
    raw_calls: list[dict[str, Any]] = []
    for path in _iter_source_files(root, max_files=max_files):
        graph.scanned_files += 1
        symbols, calls = _parse_file(root, path)
        graph.symbols.extend(symbols)
        raw_calls.extend(calls)

    graph._reindex()
    for raw in raw_calls:
        candidates = [
            symbol
            for symbol in graph._by_name.get(raw["callee_name"], [])
            if not (
                symbol.path == raw["caller_path"]
                and symbol.line == raw["caller_line"]
                and symbol.name == raw["caller_name"]
            )
        ]
        same_file = [s for s in candidates if s.path == raw["caller_path"]]
        if len(same_file) == 1:
            target, confidence = same_file[0], "same_file"
        elif len(candidates) == 1:
            target, confidence = candidates[0], "unique"
        elif candidates:
            target, confidence = None, "ambiguous"
        else:
            target, confidence = None, "unresolved"
        graph.calls.append(
            CallEdge(
                caller_name=raw["caller_name"],
                caller_path=raw["caller_path"],
                caller_line=raw["caller_line"],
                callee_name=raw["callee_name"],
                call_line=raw["call_line"],
                callee_path=target.path if target else "",
                callee_line=target.line if target else 0,
                callee_qualname=target.qualname if target else "",
                confidence=confidence,
                candidates=tuple(
                    f"{s.path}:{s.line}:{s.qualname}" for s in candidates
                ),
            )
        )
    return graph


def format_graph_report(graph: SymbolGraph, name: str, *, depth: int = 2) -> str:
    """Human-readable impact report used by the CLI."""
    payload = graph.impact(name, depth=depth)
    lines = [
        f"# Symbol: {name}",
        f"# scanned: {graph.scanned_files} files; symbols={len(graph.symbols)}; calls={len(graph.calls)}",
    ]
    defs = payload["definitions"]
    if not defs:
        lines.append("definitions: none (check spelling or grammar availability)")
    else:
        lines.append("definitions:")
        for item in defs:
            lines.append(f"  {item['path']}:{item['line']}  {item['kind']}  {item['qualname']}")
    callers = payload["callers"]
    lines.append(f"callers (depth {depth}): {len(callers)}")
    for item in callers:
        target = f"{item['callee_path']}:{item['callee_line']}" if item["callee_path"] else "unresolved"
        lines.append(
            f"  {item['path']}:{item['line']}  {item['symbol']} -> {target}  [{item['confidence']}]"
        )
    callees = payload["callees"]
    lines.append(f"callees (depth {depth}): {len(callees)}")
    for item in callees:
        target = f"{item['path']}:{item['line']}" if item["path"] else "unresolved"
        lines.append(
            f"  {target}  {item['symbol']}  [{item['confidence']}]"
        )
    if not defs or not callers:
        lines.append("note: syntax-level graph; unresolved names are not proof of absence")
    return "\n".join(lines)


__all__ = [
    "CallEdge",
    "Symbol",
    "SymbolGraph",
    "build_symbol_graph",
    "format_graph_report",
]
