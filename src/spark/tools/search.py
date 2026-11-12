from __future__ import annotations

import fnmatch
import os
import re
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from spark.models import ToolResult
from spark.sandbox import WorkdirSandbox

_wc_glob: Any = None
try:  # optional: keep the in-tree matcher for minimal installs
    from wcmatch import glob as _wc_glob
except Exception:  # pragma: no cover - optional dependency
    pass
_WC_FLAGS: int | None = (
    _wc_glob.GLOBSTAR | _wc_glob.DOTMATCH if _wc_glob is not None else None
)


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    i = 0
    out: list[str] = ["^"]
    n = len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def _glob_matches(rel: str, pattern: str) -> bool:
    """Match a workdir-relative path; wcmatch when available, regex fallback."""
    if _wc_glob is not None and _WC_FLAGS is not None:
        try:
            return bool(_wc_glob.globmatch(rel, pattern, flags=_WC_FLAGS))
        except Exception:  # pragma: no cover - malformed pattern -> fallback
            pass
    return _glob_to_regex(pattern).match(rel) is not None


def _ignore_spec(root: Path):
    """Load .gitignore/.ignore when the optional pathspec package is present."""
    try:
        import pathspec
    except Exception:  # pragma: no cover - optional dependency
        return None
    lines: list[str] = []
    for name in (".gitignore", ".ignore"):
        path = root / name
        if path.is_file():
            try:
                lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                continue
    if not lines:
        return None
    try:
        return pathspec.PathSpec.from_lines("gitwildmatch", lines)
    except Exception:  # pragma: no cover - malformed ignore file
        return None


SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    "target",
    ".next",
    ".nuxt",
    ".cache",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "vendor",
}

DEFAULT_SKIP_PATTERN = ""

# Hard scan budgets. Without these a repository with no match (or a very large
# one) makes a single tool call walk the whole tree and can block for minutes.
MAX_GREP_FILES = 5000
MAX_GLOB_FILES = 20000
MAX_SEARCH_BYTES = 128 * 1024 * 1024
MAX_SEARCH_SECONDS = 30.0
MAX_FILE_BYTES = 1_500_000


class SearchBudget:
    """Tracks file/byte/time consumption so a search can stop predictably."""

    def __init__(self, max_files: int, deadline_sec: float | None = None) -> None:
        self.max_files = max(1, int(max_files))
        self.deadline_sec = (
            float(deadline_sec)
            if deadline_sec and deadline_sec > 0
            else MAX_SEARCH_SECONDS
        )
        self.deadline = time.monotonic() + self.deadline_sec
        self.files = 0
        self.bytes = 0
        self.exhausted = False
        self.reason = ""

    def should_stop(self) -> bool:
        if self.exhausted:
            return True
        if self.files >= self.max_files:
            self.exhausted = True
            self.reason = f"file budget reached ({self.max_files} files scanned)"
            return True
        if self.bytes >= MAX_SEARCH_BYTES:
            self.exhausted = True
            self.reason = "byte budget reached"
            return True
        if time.monotonic() > self.deadline:
            self.exhausted = True
            self.reason = "time budget reached"
            return True
        return False

    def take(self, size: int) -> None:
        self.files += 1
        self.bytes += max(0, int(size))


class GrepArgs(BaseModel):
    pattern: str
    path: str = "."
    glob: str = ""  # e.g. "*.py"
    max_results: int = 50
    max_files: int = 0  # 0 = use the global budget
    deadline_sec: float = 0.0  # 0 = use the global budget


class GlobArgs(BaseModel):
    pattern: str  # e.g. "src/**/*.ts"
    path: str = "."
    max_results: int = 200
    max_files: int = 0
    deadline_sec: float = 0.0


def _walk(root: Path):
    root = Path(root)
    spec = _ignore_spec(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            # Exact membership only: startswith(".git") also pruned .github,
            # so grep reported "no matches" for workflow files that do exist.
            d for d in dirnames if d not in SKIP_DIRS
        ]
        if spec is not None:
            try:
                rel_dir = Path(dirpath).relative_to(root).as_posix()
            except ValueError:
                rel_dir = "."
            prefix = "" if rel_dir == "." else rel_dir + "/"
            dirnames[:] = [
                d for d in dirnames if not spec.match_file(f"{prefix}{d}/")
            ]
            filenames = [f for f in filenames if not spec.match_file(f"{prefix}{f}")]
        yield Path(dirpath), filenames


def grep_tool(sandbox: WorkdirSandbox, args: GrepArgs) -> ToolResult:
    try:
        base = sandbox.resolve(args.path)
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    if not base.exists():
        return ToolResult(ok=False, payload={"error": f"path not found: {args.path}"})
    try:
        regex = re.compile(args.pattern)
    except re.error as exc:
        return ToolResult(ok=False, payload={"error": f"invalid regex: {exc}"})
    glob_filter = args.glob.strip()
    max_results = max(1, min(200, args.max_results))
    budget = SearchBudget(args.max_files or MAX_GREP_FILES, args.deadline_sec)
    matches: list[str] = []
    truncated = False
    root = sandbox.root.resolve()
    for dirpath, filenames in _walk(base):
        if budget.should_stop():
            break
        for name in filenames:
            if len(matches) >= max_results:
                truncated = True
                break
            if budget.should_stop():
                break
            if glob_filter and not fnmatch.fnmatch(name, glob_filter):
                continue
            file = dirpath / name
            try:
                resolved = file.resolve()
                resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            if not sandbox.is_path_allowed(resolved):
                # 命中保护路径清单（凭据/系统路径等）：跳过该文件
                continue
            try:
                size = file.stat().st_size
                if size > MAX_FILE_BYTES:
                    continue
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            budget.take(size)
            rel = file.relative_to(sandbox.root).as_posix()
            for lineno, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{rel}:{lineno}: {line.strip()[:240]}")
                    if len(matches) >= max_results:
                        truncated = True
                        break
    payload: dict = {
        "results": matches,
        "count": len(matches),
        "scanned_files": budget.files,
    }
    if not matches:
        payload["note"] = "no matches"
    if budget.exhausted:
        payload["truncated"] = True
        payload["note"] = f"{budget.reason}; narrow the path or pattern to search more"
    elif truncated:
        payload["note"] = (
            f"truncated at {max_results} matches; narrow the search if needed"
        )
    return ToolResult(ok=True, payload=payload)


def glob_tool(sandbox: WorkdirSandbox, args: GlobArgs) -> ToolResult:
    try:
        base = sandbox.resolve(args.path)
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    if not base.exists():
        return ToolResult(ok=False, payload={"error": f"path not found: {args.path}"})
    pattern = args.pattern.strip().lstrip("/")
    max_results = max(1, min(500, args.max_results))
    budget = SearchBudget(args.max_files or MAX_GLOB_FILES, args.deadline_sec)
    # wcmatch GLOBSTAR keeps '*' single-segment and '**' recursive; the regex
    # fallback preserves the same semantics. Match both the full relative path
    # and the bare filename so patterns like 'src/*.py' and '*.py' both work.
    out: list[str] = []
    truncated = False
    base_resolved = base.resolve()
    for dirpath, filenames in _walk(base):
        if budget.should_stop():
            break
        for name in filenames:
            if budget.should_stop():
                break
            if len(out) >= max_results:
                truncated = True
                break
            file = dirpath / name
            budget.take(0)
            if budget.exhausted:
                break
            try:
                resolved = file.resolve()
                resolved.relative_to(base_resolved)
            except (OSError, ValueError):
                continue
            rel = file.relative_to(base).as_posix()
            if _glob_matches(rel, pattern) or _glob_matches(name, pattern):
                out.append(file.relative_to(sandbox.root).as_posix())
                if len(out) >= max_results:
                    truncated = True
                    break
    payload: dict = {
        "files": out,
        "count": len(out),
        "scanned_files": budget.files,
    }
    if not out:
        payload["note"] = "no files matched"
    if budget.exhausted:
        payload["truncated"] = True
        payload["note"] = f"{budget.reason}; narrow the path or pattern to search more"
    elif truncated:
        payload["note"] = f"truncated at {max_results} files"
    return ToolResult(ok=True, payload=payload)
