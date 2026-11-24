"""Reverse-reference + import graph impact analysis.

This is intentionally deterministic and language-agnostic where possible:
- Python imports are parsed with :mod:`ast`;
- JS/TS imports use a small regex resolver;
- symbol references use word-boundary scanning across source/test files.

The output is a candidate set (``affected_tests``), not a claim that every test
must run.  The agent can pass it to ``run_tests``/CI selection.
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_FILES = 2500
MAX_RESULTS = 120
MAX_FILE_BYTES = 1_000_000
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "dist",
    "build",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "target",
    "vendor",
    "coverage",
}
CODE_EXTENSIONS = {
    ".py",
    ".pyi",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rs",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".hpp",
    ".java",
    ".rb",
    ".lua",
}


def _iter_source(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if len(files) >= MAX_FILES:
            break
        if not path.is_file():
            continue
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            parts = path.parts
        if any(part in SKIP_DIRS for part in parts):
            continue
        if path.suffix.lower() not in CODE_EXTENSIONS:
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        files.append(path)
    return files


def _is_test(path: Path, root: Path) -> bool:
    try:
        parts = {part.lower() for part in path.relative_to(root).parts}
    except ValueError:
        parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    return (
        "tests" in parts
        or "test" in parts
        or "__tests__" in parts
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    )


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _python_module_name(root: Path, path: Path) -> str:
    rel = path.relative_to(root)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[0] in {"src", "lib"}:
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_python_module(root: Path, module: str, base: Path) -> Path | None:
    if not module:
        return None
    rel = Path(*module.split("."))
    candidates = [
        root / rel.with_suffix(".py"),
        root / rel / "__init__.py",
        root / "src" / rel.with_suffix(".py"),
        root / "src" / rel / "__init__.py",
        root / "lib" / rel.with_suffix(".py"),
        root / "lib" / rel / "__init__.py",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    # Relative import: caller passes the module relative to *base*.
    if base:
        candidates = [base / rel.with_suffix(".py"), base / rel / "__init__.py"]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
    return None


def _python_imports(path: Path, root: Path) -> set[Path]:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeError):
        return set()
    out: set[Path] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = _resolve_python_module(root, alias.name, path.parent)
                if target:
                    out.add(target)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            base = path.parent
            if node.level:
                for _ in range(max(0, node.level - 1)):
                    base = base.parent
                # ``from .foo import bar`` means base/foo.py, not a module
                # literally named ".foo"; the resolver receives the suffix.
                names = [module] if module else [alias.name for alias in node.names]
                candidates: list[Path] = []
                for name in names:
                    if not name:
                        continue
                    rel = Path(*name.split("."))
                    candidates.append(base / rel.with_suffix(".py"))
                    candidates.append(base / rel / "__init__.py")
                # ``from . import thing`` may re-export from the package itself.
                if not module:
                    candidates.append(base / "__init__.py")
                for candidate in candidates:
                    if candidate.is_file():
                        out.add(candidate)
            else:
                target = _resolve_python_module(root, module, path.parent)
                if target:
                    out.add(target)
    return out


_JS_IMPORT_RE = re.compile(
    r"""
    (?:
      from\s*["'](?P<from>[^"']+)["']
      |
      require\s*\(\s*["'](?P<require>[^"']+)["']\s*\)
      |
      import\s*\(\s*["'](?P<dynamic>[^"']+)["']\s*\)
    )
    """,
    re.VERBOSE,
)
_JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json")


def _js_imports(path: Path, root: Path) -> set[Path]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    out: set[Path] = set()
    for match in _JS_IMPORT_RE.finditer(text):
        spec = match.group("from") or match.group("require") or match.group("dynamic") or ""
        if not spec.startswith((".", "/")):
            continue
        base = path.parent / spec if spec.startswith(".") else root / spec.lstrip("/")
        candidates = [base, *[base.with_name(base.name + ext) for ext in _JS_EXTS]]
        candidates.extend(
            base / f"index{ext}" for ext in _JS_EXTS
        )
        for candidate in candidates:
            try:
                if candidate.is_file():
                    out.add(candidate.resolve())
                    break
            except OSError:
                continue
    return out


def _imports(path: Path, root: Path) -> set[Path]:
    suffix = path.suffix.lower()
    if suffix in {".py", ".pyi"}:
        return _python_imports(path, root)
    if suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
        return _js_imports(path, root)
    return set()


def _word_re(symbol: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w$]){re.escape(symbol)}(?![\w$])")


def _references(path: Path, symbol: str, root: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    pattern = _word_re(symbol)
    out: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not pattern.search(line):
            continue
        out.append(
            {
                "file": _relative(root, path),
                "line": line_no,
                "text": line.strip()[:180],
            }
        )
        if len(out) >= MAX_RESULTS:
            break
    return out


def _reverse_imports(files: list[Path], root: Path) -> dict[Path, set[Path]]:
    reverse: dict[Path, set[Path]] = {}
    for source in files:
        for target in _imports(source, root):
            reverse.setdefault(target, set()).add(source)
    return reverse


def _import_closure(reverse: dict[Path, set[Path]], changed: Path) -> set[Path]:
    # Direct importers, then one more hop so a test importing a facade still
    # shows up when the implementation changes.
    importers: set[Path] = set(reverse.get(changed, set()))
    frontier = set(importers)
    for _depth in range(2):
        next_frontier: set[Path] = set()
        for item in frontier:
            next_frontier |= reverse.get(item, set()) - importers
        importers |= next_frontier
        frontier = next_frontier
    return importers


def _affected_tests(
    files: list[Path],
    *,
    root: Path,
    changed: Path,
    importers: set[Path],
    reverse: dict[Path, set[Path]],
    symbol: str,
    symbol_refs: list[dict[str, Any]],
) -> set[Path]:
    affected: set[Path] = set()
    ref_files = {ref["file"] for ref in symbol_refs}
    for source in files:
        if not _is_test(source, root):
            continue
        resolved = source.resolve()
        if resolved == changed or resolved in importers:
            affected.add(source)
        elif resolved in reverse.get(changed, set()):
            affected.add(source)
        elif symbol and _relative(root, source) in ref_files:
            affected.add(source)
    return affected


def analyze_impact(
    root: Path,
    path: str | Path,
    *,
    symbol: str = "",
    max_results: int = MAX_RESULTS,
) -> dict[str, Any]:
    root = root.resolve()
    changed = Path(path)
    if not changed.is_absolute():
        changed = root / changed
    changed = changed.resolve()
    try:
        changed.relative_to(root)
    except ValueError:
        return {"error": f"path escapes the workspace: {path}"}
    if not changed.is_file():
        return {"error": f"file not found: {path}"}
    files = _iter_source(root)
    reverse = _reverse_imports(files, root)
    importers = _import_closure(reverse, changed)
    symbol_refs: list[dict[str, Any]] = []
    if symbol:
        for source in files:
            symbol_refs.extend(_references(source, symbol, root))
            if len(symbol_refs) >= max_results:
                break
    affected = _affected_tests(
        files,
        root=root,
        changed=changed,
        importers=importers,
        reverse=reverse,
        symbol=symbol,
        symbol_refs=symbol_refs,
    )
    tests = sorted(_relative(root, p) for p in affected)[:max_results]
    return {
        "changed": _relative(root, changed),
        "symbol": symbol,
        "importers": sorted(_relative(root, p) for p in importers)[:max_results],
        "references": symbol_refs[:max_results],
        "affected_tests": tests,
        "scanned_files": len(files),
        "truncated": len(tests) >= max_results or len(symbol_refs) >= max_results,
        "note": "candidate set from reverse imports + word-boundary references; not a semantic proof",
    }

__all__ = ["analyze_impact"]
