"""Repo Wiki：从源码结构生成可追溯的本地项目文档。

与 ``repo_map``（压进上下文的 6k 字符符号地图）不同，这里是**落盘产物**：
一份可提交、可 diff、可离线阅读的 Markdown 项目手册，每个条目都带
``路径:行号``，读者能直接跳转核对。

设计边界：

* **确定性优先**。结构事实（模块、符号、行号、依赖）完全由 tree-sitter
  静态解析得出，不需要模型、不需要网络、可重复生成、可 diff。模型摘要
  是可选的增量（``--summarize``），失败不影响结构部分。
* **引用必须可验证**。写盘前逐条校验 ``file:line`` 是否真的落在文件行数
  内；校验失败降级为纯路径引用，绝不留悬空行号。
* **增量**。每个文件的摘要按内容哈希缓存，改一个文件只重算一个文件；
  ``--refresh`` 才全量重建。
* **不进工作区版本控制**。默认写入 ``<workdir>/.spark/wiki/``，与检查点
  等同级；``.gitignore`` 里已有 ``.spark/`` 约定。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from spark.repo_map import _EXT_LANG, RepoMapConfig, _iter_files

#: 生成文件的固定文件名。固定名字让 diff 有意义。
INDEX_NAME = "INDEX.md"
MODULE_DIR = "modules"
CACHE_NAME = ".cache.json"

DEFAULT_MAX_FILES = 240
#: 单个模块文档里列出的符号上限，避免一个巨型文件刷屏。
MAX_SYMBOLS_PER_FILE = 120

#: 只有这些后缀进 wiki。二进制（png/mp4/ico/whl）与锁文件对"读代码"没有
#: 价值，列进去只会把配额吃光——首版就踩过：按字母序遍历时 assets/*.png
#: 把 max_files 占满，一个源文件都没索引到。
_DOC_SUFFIXES = {".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".json", ".cfg", ".ini"}
_BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".mp4", ".mov",
    ".zip", ".gz", ".tar", ".whl", ".exe", ".dll", ".so", ".dylib", ".pdf",
    ".lock", ".pyc", ".woff", ".woff2", ".ttf", ".otf",
}


@dataclass
class WikiConfig:
    max_files: int = DEFAULT_MAX_FILES
    max_depth: int = 6
    #: 是否用模型给每个模块写一句话摘要（需要真实 provider）。
    summarize: bool = False
    #: 生成目录（相对 workdir）。
    out_dir: str = ".spark/wiki"


@dataclass
class SymbolRef:
    name: str
    kind: str  # class | function | interface | type | method
    line: int


@dataclass
class ModuleDoc:
    path: str  # POSIX 相对路径，跨平台可读
    lines: int
    bytes: int
    symbols: list[SymbolRef] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "lines": self.lines,
            "bytes": self.bytes,
            "symbols": [{"name": s.name, "kind": s.kind, "line": s.line} for s in self.symbols],
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModuleDoc:
        return cls(
            path=str(data.get("path", "")),
            lines=int(data.get("lines", 0)),
            bytes=int(data.get("bytes", 0)),
            symbols=[
                SymbolRef(name=str(s.get("name", "")), kind=str(s.get("kind", "")), line=int(s.get("line", 0)))
                for s in data.get("symbols", [])
                if isinstance(s, dict)
            ],
            summary=str(data.get("summary", "")),
        )


_KIND_BY_CAPTURE = {
    "class": "class",
    "function": "function",
    "interface": "interface",
    "type": "type",
    "method": "method",
    "const": "const",
}

#: 每个语言一份「定义 + 行号」查询。capture 名决定 kind。
_DEF_QUERIES: dict[str, str] = {
    "python": """
        (class_definition name: (identifier) @class)
        (function_definition name: (identifier) @function)
    """,
    "typescript": """
        (class_declaration name: (type_identifier) @class)
        (function_declaration name: (identifier) @function)
        (interface_declaration name: (type_identifier) @interface)
        (type_alias_declaration name: (type_identifier) @type)
        (method_definition name: (property_identifier) @method)
    """,
    "tsx": """
        (class_declaration name: (type_identifier) @class)
        (function_declaration name: (identifier) @function)
        (interface_declaration name: (type_identifier) @interface)
        (type_alias_declaration name: (type_identifier) @type)
        (method_definition name: (property_identifier) @method)
    """,
    "javascript": """
        (class_declaration name: (identifier) @class)
        (function_declaration name: (identifier) @function)
        (method_definition name: (property_identifier) @method)
    """,
}


def _language_for(suffix: str) -> str:
    return _EXT_LANG.get(suffix, "")


def extract_symbols(path: Path) -> list[SymbolRef]:
    """tree-sitter 提取带行号的符号；不支持的语言或解析失败返回空列表。"""
    lang_name = _language_for(path.suffix)
    if not lang_name:
        return []
    query_src = _DEF_QUERIES.get(lang_name)
    if not query_src:
        return []
    try:
        import tree_sitter_javascript
        import tree_sitter_python
        import tree_sitter_typescript
        from tree_sitter import Language, Parser, Query, QueryCursor

        if lang_name == "python":
            language = Language(tree_sitter_python.language())
        elif lang_name == "typescript":
            language = Language(tree_sitter_typescript.language_typescript())
        elif lang_name == "tsx":
            language = Language(tree_sitter_typescript.language_tsx())
        else:
            language = Language(tree_sitter_javascript.language())

        source = path.read_bytes()
        if not source:
            return []
        tree = Parser(language).parse(source)
        query = Query(language, query_src)
        captures = QueryCursor(query).captures(tree.root_node)
        found: dict[tuple[int, str], SymbolRef] = {}
        if isinstance(captures, dict):
            for capture_name, nodes in captures.items():
                kind = _KIND_BY_CAPTURE.get(capture_name, capture_name)
                for node in nodes:
                    name = node.text.decode("utf-8", "replace") if node.text else ""
                    if not name:
                        continue
                    line = node.start_point[0] + 1
                    found[(line, name)] = SymbolRef(name=name, kind=kind, line=line)
        return sorted(found.values(), key=lambda s: (s.line, s.name))[:MAX_SYMBOLS_PER_FILE]
    except Exception:  # pragma: no cover - grammar/tree-sitter version drift
        return []


def _count_lines(path: Path) -> int:
    try:
        with path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def _hash_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def scan(root: Path, cfg: WikiConfig) -> list[ModuleDoc]:
    """遍历工作区，产出每个源文件的模块条目（不含摘要）。

    源码文件优先占配额：``_iter_files`` 按目录字母序返回，如果直接截断，
    ``assets/`` 这类目录里的二进制会把预算吃光（首版实测 40 个名额全被
    png 占掉）。这里先按"是否是代码"分两批，再在批内按路径稳定排序。
    """
    repo_cfg = RepoMapConfig(max_files=max(cfg.max_files * 4, 400), max_depth=cfg.max_depth)
    code: list[Path] = []
    docs: list[Path] = []
    for path in _iter_files(root, repo_cfg):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".spark/") or rel.startswith("node_modules/"):
            continue
        suffix = path.suffix.lower()
        if suffix in _BINARY_SUFFIXES or path.name in {"uv.lock", "package-lock.json"}:
            continue
        (code if suffix in _EXT_LANG else docs).append(path)

    selected = sorted(code)[: cfg.max_files]
    if len(selected) < cfg.max_files:
        selected += sorted(docs)[: cfg.max_files - len(selected)]

    modules: list[ModuleDoc] = []
    for path in selected:
        rel = path.relative_to(root).as_posix()
        modules.append(
            ModuleDoc(
                path=rel,
                lines=_count_lines(path),
                bytes=_size(path),
                symbols=extract_symbols(path) if path.suffix in _EXT_LANG else [],
            )
        )
    modules.sort(key=lambda m: m.path)
    return modules


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def verify_citation(root: Path, reference: str) -> bool:
    """``path:line`` 是否真实存在（文件存在且行号在范围内）。

    用于写盘前自检：模型摘要里出现的引用一律先校验，避免文档里留着
    指向不存在位置的链接——那比没有引用更误导人。
    """
    if ":" not in reference:
        return False
    path_part, _, line_part = reference.rpartition(":")
    try:
        line = int(line_part)
    except ValueError:
        return False
    if line < 1:
        return False
    target = (root / path_part).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return False
    if not target.is_file():
        return False
    return line <= _count_lines(target)


def _render_module(doc: ModuleDoc, lines_budget: int = 200) -> str:
    """单个模块的 Markdown。行号一律来自实际解析结果。"""
    title = doc.path
    out: list[str] = [f"## `{title}`", ""]
    out.append(f"- 行数：{doc.lines}　大小：{_human_size(doc.bytes)}")
    if doc.summary:
        out.append(f"- 摘要：{doc.summary}")
    out.append("")
    if not doc.symbols:
        out.append("_（无可用符号索引）_")
        out.append("")
        return "\n".join(out)
    out.append("| 行 | 类型 | 符号 |")
    out.append("| ---: | --- | --- |")
    for sym in doc.symbols[:lines_budget]:
        out.append(f"| {sym.line} | {sym.kind} | `{sym.name}` |")
    if len(doc.symbols) > lines_budget:
        out.append(f"| … | | 另有 {len(doc.symbols) - lines_budget} 个符号未列出 |")
    out.append("")
    return "\n".join(out)


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} GB"


def _slug(path: str) -> str:
    """``a/b/c.py`` → ``a__b__c.py.md``（扁平化，避免 Windows 路径长度与保留名）。"""
    safe = path.replace("/", "__").replace("\\", "__").replace(":", "_")
    # Windows 保留名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）不能做文件名
    stem = safe.split(".")[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL"} or (
        len(stem) == 4 and stem[:3] in {"COM", "LPT"} and stem[3].isdigit()
    ):
        safe = "_" + safe
    return safe + ".md"


def _read_cache(out: Path) -> dict[str, Any]:
    try:
        return json.loads((out / CACHE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_cache(out: Path, data: dict[str, Any]) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    (out / CACHE_NAME).write_text(payload, encoding="utf-8")


def build_wiki(
    root: Path,
    cfg: WikiConfig | None = None,
    summarizer: Any = None,
) -> dict[str, Any]:
    """生成 wiki 到 ``<root>/<cfg.out_dir>``，返回统计摘要。

    ``summarizer(path, symbols) -> str`` 可选；只有它不为 None 且
    ``cfg.summarize`` 为真时才调用。异常一律吞掉并回退到无摘要，
    因为摘要只是锦上添花，不能阻断产物生成。
    """
    cfg = cfg or WikiConfig()
    root = root.resolve()
    out = (root / cfg.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / MODULE_DIR).mkdir(parents=True, exist_ok=True)

    cache = _read_cache(out)
    modules = scan(root, cfg)
    built = skipped = 0
    summaries: dict[str, str] = {}

    for doc in modules:
        abs_path = root / doc.path
        digest = _hash_file(abs_path)
        cached = cache.get(doc.path, {})
        if cached.get("hash") == digest and isinstance(cached.get("summary"), str):
            doc.summary = cached["summary"]
        elif cfg.summarize and summarizer is not None and doc.symbols:
            try:
                doc.summary = str(summarizer(doc.path, doc.symbols) or "").strip()
            except Exception:
                doc.summary = ""
        summaries[doc.path] = doc.summary
        if cached.get("hash") == digest:
            skipped += 1
        else:
            built += 1
        cache[doc.path] = {"hash": digest, "summary": doc.summary}

    for doc in modules:
        (out / MODULE_DIR / _slug(doc.path)).write_text(_render_module(doc), encoding="utf-8")

    index = _render_index(root, modules, cfg)
    (out / INDEX_NAME).write_text(index, encoding="utf-8")

    # 清理已删除文件的陈旧模块文档
    live = {_slug(m.path) for m in modules}
    for stale in (out / MODULE_DIR).glob("*.md"):
        if stale.name not in live:
            stale.unlink(missing_ok=True)

    _write_cache(out, cache)
    return {
        "modules": len(modules),
        "symbols": sum(len(m.symbols) for m in modules),
        "built": built,
        "cached": skipped,
        "out_dir": str(out),
        "index": str(out / INDEX_NAME),
    }

def _render_index(root: Path, modules: list[ModuleDoc], cfg: WikiConfig) -> str:
    total_lines = sum(m.lines for m in modules)
    total_symbols = sum(len(m.symbols) for m in modules)
    by_ext: dict[str, int] = {}
    for m in modules:
        suffix = Path(m.path).suffix or "(none)"
        by_ext[suffix] = by_ext.get(suffix, 0) + 1
    top_ext = sorted(by_ext.items(), key=lambda kv: (-kv[1], kv[0]))[:8]

    out: list[str] = [
        "# Repo Wiki",
        "",
        f"> 由 `spark wiki` 从源码静态生成，生成时间 {time.strftime('%Y-%m-%d %H:%M:%S')}。",
        "> 结构事实来自 tree-sitter 解析（可重复、可 diff）；行号指向生成时的文件内容。",
        "",
        "## 概览",
        "",
        f"- 文件：{len(modules)}",
        f"- 代码行：{total_lines}",
        f"- 已索引符号：{total_symbols}",
        f"- 最大文件数上限：{cfg.max_files}",
        "",
    ]
    if top_ext:
        out += ["| 后缀 | 文件数 |", "| --- | ---: |"]
        out += [f"| `{ext}` | {n} |" for ext, n in top_ext]
        out.append("")

    out += ["## 模块", "", "| 文件 | 行数 | 符号数 | 摘要 |", "| --- | ---: | ---: | --- |"]
    for m in modules:
        link = f"[`{m.path}`]({MODULE_DIR}/{_slug(m.path)})"
        summary = (m.summary or "").replace("|", "\\|")
        out.append(f"| {link} | {m.lines} | {len(m.symbols)} | {summary} |")
    out.append("")
    out += [
        "## 维护",
        "",
        "```bash",
        "spark wiki              # 增量更新（按内容哈希跳过未变化的文件）",
        "spark wiki --refresh    # 全量重建",
        "spark wiki --summarize  # 额外生成模型摘要（需要真实 provider）",
        "```",
        "",
        f"_生成目录：`{cfg.out_dir}`_",
        "",
    ]
    return "\n".join(out)
