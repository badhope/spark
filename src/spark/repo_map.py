"""P0-10：repo-map 代码索引（Aider 算法精简版）。

遍历工作目录，用 tree-sitter 提取符号（函数/类/定义），按文件大小分桶
（Aider 的三桶策略：关注度随文件变大而衰减），浓缩成一份"代码地图"注入
上下文——模型无需先读整个仓库即可定位改动点。这是 aider/Claude Code
"repo map / context editing" 的成熟范式，直接复用 tree-sitter 官方绑定。

设计要点：
- 符号提取失败/无 grammar 的语言 → 降级为文件名地图（不阻塞）。
- 只对关注度高的桶输出符号，大文件只输出"存在 + 大小"。
- 输出上限 ~6k 字符，防止地图本身吃掉上下文预算。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from spark.proc import run_bounded

logger = logging.getLogger(__name__)

# 关注度桶（Aider 三桶）：行数越大，桶排名越低
BUCKETS: tuple[tuple[int, int], ...] = ((0, 50), (50, 500), (500, 10**6))
_BUCKET_RANK = {b: rank for rank, b in enumerate(BUCKETS)}
DEFAULT_EXCLUDES = {
    ".git", "node_modules", "dist", "build", "__pycache__", ".venv", "venv",
    ".idea", ".vscode", "*.pyc", "target", ".next", "vendor", "coverage", ".mypy_cache",
}

MAX_MAP_CHARS = 6000


@dataclass(frozen=True)
class RepoMapConfig:
    enabled: bool = True
    max_chars: int = MAX_MAP_CHARS
    max_files: int = 200
    max_depth: int = 5


def _try_language(name: str):
    """按名称构造 tree-sitter Language；失败返回 None（降级）。"""
    try:
        if name == "python":
            import tree_sitter_python
            from tree_sitter import Language

            return Language(tree_sitter_python.language())
        if name in {"typescript", "tsx"}:
            import tree_sitter_typescript
            from tree_sitter import Language

            lang = tree_sitter_typescript.language_typescript() if name == "typescript" else tree_sitter_typescript.language_tsx()
            return Language(lang)
        if name == "javascript":
            import tree_sitter_javascript
            from tree_sitter import Language

            return Language(tree_sitter_javascript.language())
    except Exception as exc:  # pragma: no cover - 环境差异
        logger.debug("tree-sitter %s unavailable: %s", name, exc)
    return None


def _symbol_query(name: str) -> list[str] | None:
    """每种语言的符号提取 query（tree-sitter query 语法）。"""
    queries = {
        "python": [
            "(function_definition name: (identifier) @name)",
            "(class_definition name: (identifier) @name)",
        ],
        "typescript": [
            "(function_declaration name: (identifier) @name)",
            "(class_declaration name: (type_identifier) @name)",
            "(method_signature name: (property_identifier) @name)",
            "(interface_declaration name: (type_identifier) @name)",
            "(type_alias_declaration name: (type_identifier) @name)",
        ],
        "tsx": [
            "(function_declaration name: (identifier) @name)",
            "(class_declaration name: (type_identifier) @name)",
            "(interface_declaration name: (type_identifier) @name)",
            "(type_alias_declaration name: (type_identifier) @name)",
        ],
        "javascript": [
            "(function_declaration name: (identifier) @name)",
            "(class_declaration name: (identifier) @name)",
        ],
    }
    return queries.get(name)


_EXT_LANG = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
}


def _extract_symbols(path: Path, lang_name: str) -> list[str]:
    """用 tree-sitter 提取文件内符号名；解析失败返回空列表。"""
    lang = _try_language(lang_name)
    if lang is None:
        return []
    queries = _symbol_query(lang_name)
    if not queries:
        return []
    try:
        from tree_sitter import Parser, Query, QueryCursor

        parser = Parser(lang)
        source = path.read_bytes()
        if not source:
            return []
        tree = parser.parse(source)
        symbols: list[str] = []
        for pattern in queries:
            try:
                query = Query(lang, pattern)
            except Exception:  # pragma: no cover - 语法版本差异
                continue
            try:
                captures = QueryCursor(query).captures(tree.root_node)
            except Exception:  # pragma: no cover - 0.25 及更早版本 API
                captures = None
            if captures is None:
                continue
            if isinstance(captures, dict):
                nodes: list = []
                for group in captures.values():
                    nodes.extend(group)
            else:  # 旧 API：可迭代 (node, tag)
                nodes = [item[0] for item in captures]
            for nd in nodes:
                name = _node_text(source, nd)
                if name and name not in symbols:
                    symbols.append(name)
        return symbols[:80]
    except Exception as exc:  # pragma: no cover
        logger.debug("symbol extraction failed for %s: %s", path, exc)
        return []


def _node_text(source: bytes, node) -> str:
    try:
        b = source[node.start_byte : node.end_byte]
        return b.decode("utf-8", "replace").strip()
    except Exception:  # pragma: no cover
        return ""



def _git_heat(root: Path, limit: int = 2000) -> dict[str, int]:
    """P1：git 热度（aider PageRank 思路的轻量近似）。

    统计最近 ``limit`` 次提交中各文件的被提及次数，作为桶内排序权重：
    改得越频繁的文件越先进入地图（替代纯目录遍历顺序的偏置）。
    非 git 仓库 / git 不可用 / 超时 → 返回空 dict，行为与旧版完全一致。
    """
    try:
        proc = run_bounded(
            ["git", "log", f"-{limit}", "--format=format:", "--name-only", "--"],
            cwd=root,
            timeout=10,
        )
    except OSError:
        return {}
    if proc.returncode != 0 or proc.timed_out:
        return {}
    heat: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        name = line.strip()
        if name:
            heat[name] = heat.get(name, 0) + 1
    return heat


def _bucket_rank(lines: int) -> int:
    for rank, (lo, hi) in enumerate(BUCKETS):
        if lo <= lines < hi:
            return rank
    return len(BUCKETS) - 1


def _iter_files(root: Path, cfg: RepoMapConfig) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in DEFAULT_EXCLUDES and not d.startswith(".")
        ]
        rel = Path(dirpath).relative_to(root)
        if len(rel.parts) > cfg.max_depth:
            dirnames[:] = []
            continue
        for fn in sorted(filenames):
            if any(fn.endswith(pat) for pat in ("~", ".pyc", ".class", ".o")):
                continue
            p = Path(dirpath) / fn
            if p.suffix in _EXT_LANG:
                files.append(p)
            elif not fn.startswith("."):
                # 非代码文件也进入地图（文件名 + 大小），帮助模型定位
                files.append(p)
        if len(files) >= cfg.max_files:
            break
    return files[: cfg.max_files]


def _file_stats(path: Path) -> tuple[int, int]:
    try:
        st = path.stat()
        return _count_lines(path), st.st_size
    except OSError:
        return 0, 0


def _count_lines(path: Path) -> int:
    try:
        with open(path, "rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def build_repo_map(root: Path, cfg: RepoMapConfig | None = None) -> str:
    """生成仓库代码地图（<= max_chars）。无代码时返回空字符串。"""
    cfg = cfg or RepoMapConfig()
    if not cfg.enabled or not root.exists():
        return ""
    files = _iter_files(root, cfg)
    if not files:
        return ""

    # P1：git 热度参与桶内排序（高频改动文件优先进入预算）
    heat = _git_heat(root)

    # 按桶排名分组：rank 0（<50 行）→ 输出符号；rank 1 → 符号 + 截断；rank 2 → 仅文件名
    per_bucket: dict[int, list[tuple[Path, int, list[str]]]] = {}
    for p in files:
        lines, _ = _file_stats(p)
        rank = _bucket_rank(lines)
        syms = _extract_symbols(p, _EXT_LANG.get(p.suffix, "")) if p.suffix in _EXT_LANG else []
        per_bucket.setdefault(rank, []).append((p, lines, syms))

    parts: list[str] = ["# Repo map (auto-generated by tree-sitter)"]
    budget = cfg.max_chars - len(parts[0]) - 64

    def emit(line: str) -> bool:
        nonlocal budget
        if budget <= 0:
            return False
        budget -= len(line) + 1
        parts.append(line)
        return True

    for rank in sorted(per_bucket):
        if budget <= 0:
            break
        entries = sorted(
            per_bucket[rank],
            key=lambda e: (
                -heat.get(str(e[0].relative_to(root)), 0),
                e[1],
                str(e[0]),
            ),
        )
        for p, lines, syms in entries:
            rel = p.relative_to(root)
            if rank == 0 and syms:
                line = f"{rel} [{lines}L] :: {', '.join(syms)}"
            elif rank == 1:
                prefix = ", ".join(syms[:12])
                line = f"{rel} [{lines}L] :: {prefix}{' …' if len(syms) > 12 else ''}"
            else:
                line = f"{rel} [{lines}L]"
            if not emit(line):
                break

    if len(parts) == 1:
        return ""
    return "\n".join(parts)
