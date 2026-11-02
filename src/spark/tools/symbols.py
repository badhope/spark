"""符号定位（definition / references）——LSP 能力的零执行替代实现。

为什么不是完整 LSP 客户端：

* 竞品（opencode）内建 40+ 语言 LSP，靠的是**拉起语言服务器子进程**。而本仓库
  已经有一条明确的安全边界：只读工具不得执行仓库自带的二进制
  （见 ``tools/diagnostics.py`` 对 ``npx``/``node_modules/.bin/tsc`` 的拒绝）。
  为一个只读工具开一条"启动任意语言服务器"的通道，等于把这条边界打穿。
* 本项目已依赖 tree-sitter，能直接给出「符号在哪定义、在哪些文件被引用」，
  这正是 agent 改代码时最需要的那部分信息；类型推断、重命名这类需要完整
  语义分析的能力本来也不该由只读工具隐式提供。

因此这里实现的是**基于语法的符号索引**：准确、快、零执行风险，边界诚实
写在工具描述里，不假装自己是 LSP。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_FILES_SCANNED = 400
MAX_RESULTS = 50
MAX_FILE_BYTES = 512_000

_EXT_LANG = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
}

#: 每种语言的「定义」查询：capture 名统一为 @def，取节点文本即符号名。
_DEF_QUERIES: dict[str, list[str]] = {
    "python": [
        "(function_definition name: (identifier) @def)",
        "(class_definition name: (identifier) @def)",
        "(assignment left: (identifier) @def)",
    ],
    "typescript": [
        "(function_declaration name: (identifier) @def)",
        "(class_declaration name: (type_identifier) @def)",
        "(interface_declaration name: (type_identifier) @def)",
        "(type_alias_declaration name: (type_identifier) @def)",
        "(method_definition name: (property_identifier) @def)",
        "(variable_declarator name: (identifier) @def)",
    ],
    "javascript": [
        "(function_declaration name: (identifier) @def)",
        "(class_declaration name: (identifier) @def)",
        "(method_definition name: (property_identifier) @def)",
        "(variable_declarator name: (identifier) @def)",
    ],
}
_DEF_QUERIES["tsx"] = _DEF_QUERIES["typescript"]


def _load_language(name: str):
    try:
        if name == "python":
            import tree_sitter_python
            from tree_sitter import Language

            return Language(tree_sitter_python.language())
        if name in {"typescript", "tsx"}:
            import tree_sitter_typescript
            from tree_sitter import Language

            lang = (
                tree_sitter_typescript.language_typescript()
                if name == "typescript"
                else tree_sitter_typescript.language_tsx()
            )
            return Language(lang)
        if name == "javascript":
            import tree_sitter_javascript
            from tree_sitter import Language

            return Language(tree_sitter_javascript.language())
    except Exception as exc:  # pragma: no cover - 环境差异
        logger.debug("tree-sitter %s unavailable: %s", name, exc)
    return None


def _iter_source_files(root: Path, limit: int = MAX_FILES_SCANNED):
    """遍历源码文件；跳过依赖目录与点目录，防止扫进 node_modules。"""
    skip = {
        "node_modules", ".git", "dist", "build", ".venv", "venv",
        "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    }
    count = 0
    for path in root.rglob("*"):
        if count >= limit:
            return
        if not path.is_file():
            continue
        if any(part in skip for part in path.parts):
            continue
        if path.suffix.lower() not in _EXT_LANG:
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        count += 1
        yield path


def _definitions_in(path: Path, symbol: str) -> list[dict[str, Any]]:
    """在单个文件里找 *symbol* 的定义位置。"""
    lang_name = _EXT_LANG.get(path.suffix.lower())
    if not lang_name:
        return []
    language = _load_language(lang_name)
    if language is None:
        return []
    try:
        from tree_sitter import Parser, Query, QueryCursor

        source = path.read_bytes()
        parser = Parser(language)
        tree = parser.parse(source)
        found: list[dict[str, Any]] = []
        for query_src in _DEF_QUERIES.get(lang_name, []):
            try:
                query = Query(language, query_src)
            except Exception:  # pragma: no cover - 语法版本差异
                continue
            cursor = QueryCursor(query)
            for _pattern, caps in cursor.matches(tree.root_node):
                for node in caps.get("def", []):
                    text = source[node.start_byte : node.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                    if text != symbol:
                        continue
                    found.append(
                        {
                            "file": str(path),
                            "line": node.start_point[0] + 1,
                            "col": node.start_point[1] + 1,
                            "kind": node.parent.type if node.parent else "symbol",
                        }
                    )
        return found
    except Exception as exc:  # pragma: no cover - 解析失败不应让工具崩
        logger.debug("definition scan failed for %s: %s", path, exc)
        return []


def _references_in(path: Path, symbol: str) -> list[dict[str, Any]]:
    """文本级引用定位。

    刻意用文本匹配而不是 AST：引用既包括调用、类型注解，也包括字符串与
    注释里的提及，而 agent 排查影响面时这些都算线索。宁可多报（由模型判断）
    也不要漏报——漏掉一处调用点会导致改坏别人的代码。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    if symbol not in text:
        return out
    for lineno, line in enumerate(text.splitlines(), start=1):
        if symbol not in line:
            continue
        stripped = line.strip()
        # 定义行不算"引用"，避免把定义自己报成使用点。
        if stripped.startswith(("def ", "class ", "function ", "const ", "let ", "var ")):
            if f"{symbol}(" in stripped or f"{symbol} =" in stripped or f"{symbol}:" in stripped:
                continue
        out.append(
            {
                "file": str(path),
                "line": lineno,
                "text": stripped[:160],
            }
        )
        if len(out) >= MAX_RESULTS:
            return out
    return out


def find_symbol(
    root: Path,
    symbol: str,
    *,
    mode: str = "definition",
) -> dict[str, Any]:
    """在 *root* 下定位 *symbol*；mode 为 ``definition`` 或 ``reference``。"""
    symbol = (symbol or "").strip()
    if not symbol:
        return {"error": "symbol is required"}
    if len(symbol) > 200:
        return {"error": "symbol is too long"}

    definitions: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    scanned = 0
    for path in _iter_source_files(root):
        scanned += 1
        if mode in {"definition", "both"}:
            definitions.extend(_definitions_in(path, symbol))
        if mode in {"reference", "both"}:
            references.extend(_references_in(path, symbol))
        if len(definitions) >= MAX_RESULTS and len(references) >= MAX_RESULTS:
            break

    result: dict[str, Any] = {
        "symbol": symbol,
        "scanned_files": scanned,
        # 明确说明能力边界，避免模型把它当成完整 LSP 做重命名等操作。
        "note": "syntax-level index (tree-sitter + text scan); not a full LSP",
    }
    if mode in {"definition", "both"}:
        result["definitions"] = definitions[:MAX_RESULTS]
    if mode in {"reference", "both"}:
        result["references"] = references[:MAX_RESULTS]
    if mode == "definition" and not definitions:
        result["hint"] = "no definition found; try mode=reference or check the spelling"
    return result
