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
#: 上次生成参数（max_files 等），供 Web 面板回显。
META_NAME = ".meta.json"

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


def _select_paths(root: Path, cfg: WikiConfig) -> list[Path]:
    """按"代码优先"规则选出要索引的文件（不解析内容）。

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
    return selected


def _candidate_docs(root: Path, cfg: WikiConfig) -> list[ModuleDoc]:
    """只做文件枚举与 stat，不解析符号。

    符号解析是这条链路里最贵的一步，必须由调用方按内容哈希决定是否值得做；
    这里返回的 ``symbols`` 为空，命中缓存时会被缓存里的完整文档替换。
    """
    docs: list[ModuleDoc] = []
    for path in _select_paths(root, cfg):
        docs.append(
            ModuleDoc(
                path=path.relative_to(root).as_posix(),
                lines=_count_lines(path),
                bytes=_size(path),
            )
        )
    docs.sort(key=lambda m: m.path)
    return docs


def scan(root: Path, cfg: WikiConfig) -> list[ModuleDoc]:
    """遍历工作区并解析符号（全量；测试与一次性检查用）。"""
    modules = _candidate_docs(root, cfg)
    for doc in modules:
        path = root / doc.path
        if path.suffix in _EXT_LANG:
            doc.symbols = extract_symbols(path)
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


def _cached_doc(entry: dict[str, Any]) -> ModuleDoc | None:
    """从缓存条目还原一个完整的 ModuleDoc（含符号）。

    缓存必须存符号表而不只是摘要：否则"增量"只省了写盘，
    tree-sitter 每轮仍要重新解析全部文件——实测 240 文件全量 1.57s、
    "增量" 1.41s，几乎没有收益。
    """
    try:
        doc = ModuleDoc.from_dict(entry["doc"])
    except (KeyError, TypeError, ValueError):
        return None
    return doc if doc.path else None


def build_wiki(
    root: Path,
    cfg: WikiConfig | None = None,
    summarizer: Any = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """生成 wiki 到 ``<root>/<cfg.out_dir>``，返回统计摘要。

    ``summarizer(path, symbols) -> str`` 可选；只有它不为 None 且
    ``cfg.summarize`` 为真时才调用。异常一律吞掉并回退到无摘要，
    因为摘要只是锦上添花，不能阻断产物生成。

    ``refresh=True`` 忽略缓存全量重建。命中缓存的文件既不重新解析、
    也不重新写盘，只有 INDEX.md 与新增/变更的模块被写出。
    """
    cfg = cfg or WikiConfig()
    root = root.resolve()
    out = (root / cfg.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / MODULE_DIR).mkdir(parents=True, exist_ok=True)

    cache = {} if refresh else _read_cache(out)
    # 缓存键带 max_files：预算变化会改变入选文件集合，不能沿用旧结果。
    cache_key = f"budget:{cfg.max_files}"
    if cache.get(cache_key) != cfg.max_files:
        cache = {}
        cache[cache_key] = cfg.max_files

    modules: list[ModuleDoc] = []
    built = skipped = 0
    new_cache: dict[str, Any] = {cache_key: cfg.max_files}

    for doc in _candidate_docs(root, cfg):
        abs_path = root / doc.path
        digest = _hash_file(abs_path)
        entry = cache.get(doc.path)
        entry_dict = entry if isinstance(entry, dict) else {}
        reused = (
            _cached_doc(entry_dict) if entry_dict.get("hash") == digest else None
        )
        if reused is not None:
            reused.summary = str(entry_dict.get("summary") or "")
            modules.append(reused)
            new_cache[doc.path] = {"hash": digest, "summary": reused.summary, "doc": reused.to_dict()}
            skipped += 1
            continue

        # 缓存未命中才解析符号——这一步是整条链路里最贵的，
        # 放在哈希比较之后，命中缓存的运行就不再付出它。
        if not doc.symbols and abs_path.suffix in _EXT_LANG:
            doc.symbols = extract_symbols(abs_path)
        # 摘要在符号解析之后：summarizer 拿到的符号表是真实的，
        # 先算摘要会把空列表传进去，模型只能泛泛而谈。
        if cfg.summarize and summarizer is not None and doc.symbols:
            try:
                doc.summary = str(summarizer(doc.path, doc.symbols) or "").strip()
            except Exception:
                doc.summary = ""
        modules.append(doc)
        new_cache[doc.path] = {"hash": digest, "summary": doc.summary, "doc": doc.to_dict()}
        built += 1

    modules.sort(key=lambda m: m.path)

    # INDEX.md 每次都写（它含生成时间与统计，必须反映本次结果）；
    # 模块文档只在内容变化时写，避免无意义的文件 mtime 抖动。
    existing = {p.name for p in (out / MODULE_DIR).glob("*.md")}
    for doc in modules:
        entry = cache.get(doc.path) or {}
        if entry.get("hash") == new_cache[doc.path]["hash"] and _slug(doc.path) in existing:
            continue
        (out / MODULE_DIR / _slug(doc.path)).write_text(_render_module(doc), encoding="utf-8")

    index = _render_index(root, modules, cfg)
    (out / INDEX_NAME).write_text(index, encoding="utf-8")

    # 清理已删除文件的陈旧模块文档
    live = {_slug(m.path) for m in modules}
    for stale in (out / MODULE_DIR).glob("*.md"):
        if stale.name not in live:
            stale.unlink(missing_ok=True)

    _write_cache(out, new_cache)
    # 记录本次实际使用的参数，供 Web 面板回显。
    # 不能只在 HTTP handler 里写：CLI 生成的 wiki 也要能被面板正确显示
    # （否则面板会把 20 个文件的手册配上 240 的上限，自相矛盾）。
    _write_meta(out, cfg)
    return {
        "modules": len(modules),
        "symbols": sum(len(m.symbols) for m in modules),
        "built": built,
        "cached": skipped,
        "out_dir": str(out),
        "index": str(out / INDEX_NAME),
    }


def _write_meta(out: Path, cfg: WikiConfig) -> None:
    payload = {
        "max_files": cfg.max_files,
        "max_depth": cfg.max_depth,
        "summarize": cfg.summarize,
        "written_at": int(time.time()),
    }
    try:
        (out / META_NAME).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def read_meta(out: Path) -> dict[str, Any]:
    """读取上次生成参数；不存在或损坏时返回空 dict（调用方给默认值）。"""
    try:
        return json.loads((out / META_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


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
