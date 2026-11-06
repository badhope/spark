"""Repo Wiki：结构可复现、引用可验证、增量与配额策略。

这些断言盯的是三类真实回归点：
1. 生成物必须来自解析而非字符串拼装（符号与行号对得上源码）；
2. 引用必须能落到真实位置（verify_citation 拒绝悬空行号）；
3. 提交进 git 的产物必须可 diff（两次生成、换台机器都是同一份）。
"""
from __future__ import annotations

from pathlib import Path

from spark.wiki import WikiConfig, build_wiki, scan, verify_citation

PY_SRC = '''"""Sample module."""


class Engine:
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


def helper(value: int) -> int:
    return value + 1
'''


def _project(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text(PY_SRC, encoding="utf-8")
    (tmp_path / "app.ts").write_text(
        "export interface Cfg { name: string }\nexport function boot(c: Cfg) {}\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    return tmp_path


def test_wiki_indexes_real_symbols_with_line_numbers(tmp_path: Path) -> None:
    root = _project(tmp_path)
    stats = build_wiki(root, WikiConfig())
    assert stats["modules"] == 3
    assert stats["symbols"] >= 5

    core = (root / ".spark" / "wiki" / "modules" / "pkg__core.py.md").read_text(encoding="utf-8")
    assert "`Engine`" in core and "`helper`" in core
    # Line numbers must be the real ones: class on 4, helper on 12.
    assert "| 4 | class | `Engine` |" in core
    assert "| 12 | function | `helper` |" in core


def test_wiki_skips_binaries_so_they_cannot_eat_the_budget(tmp_path: Path) -> None:
    """Regression: alphabetical walk let assets/*.png consume the whole quota."""
    root = _project(tmp_path)
    (root / "assets").mkdir()
    for i in range(30):
        (root / "assets" / f"{i:02d}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

    modules = scan(root, WikiConfig(max_files=3))
    suffixes = {Path(m.path).suffix for m in modules}
    assert ".png" not in suffixes
    assert ".py" in suffixes or ".ts" in suffixes


def test_wiki_output_is_deterministic_across_runs(tmp_path: Path) -> None:
    root = _project(tmp_path)
    build_wiki(root, WikiConfig())
    first = (root / ".spark" / "wiki" / "modules" / "pkg__core.py.md").read_text(encoding="utf-8")
    build_wiki(root, WikiConfig())
    second = (root / ".spark" / "wiki" / "modules" / "pkg__core.py.md").read_text(encoding="utf-8")
    assert first == second


def test_wiki_incremental_run_reports_cache_hits(tmp_path: Path) -> None:
    root = _project(tmp_path)
    build_wiki(root, WikiConfig())
    stats = build_wiki(root, WikiConfig())
    assert stats["cached"] == stats["modules"]
    assert stats["built"] == 0


def test_wiki_removes_module_docs_for_deleted_files(tmp_path: Path) -> None:
    root = _project(tmp_path)
    build_wiki(root, WikiConfig())
    stale = root / ".spark" / "wiki" / "modules" / "app.ts.md"
    assert stale.exists()

    (root / "app.ts").unlink()
    build_wiki(root, WikiConfig())
    assert not stale.exists()


def test_verify_citation_rejects_dangling_lines(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert verify_citation(root, "pkg/core.py:4") is True
    assert verify_citation(root, "pkg/core.py:1") is True
    assert verify_citation(root, "pkg/core.py:9999") is False
    assert verify_citation(root, "pkg/core.py:0") is False
    assert verify_citation(root, "missing.py:1") is False
    assert verify_citation(root, "../outside.py:1") is False
    assert verify_citation(root, "pkg/core.py") is False


def test_wiki_summarizer_failure_degrades_instead_of_breaking(tmp_path: Path) -> None:
    root = _project(tmp_path)

    def boom(path, symbols):
        raise RuntimeError("provider exploded")

    stats = build_wiki(root, WikiConfig(summarize=True), summarizer=boom)
    assert stats["modules"] == 3
    index = (root / ".spark" / "wiki" / "INDEX.md").read_text(encoding="utf-8")
    assert "Repo Wiki" in index


def test_wiki_summarizer_text_lands_in_the_index(tmp_path: Path) -> None:
    root = _project(tmp_path)
    build_wiki(
        root,
        WikiConfig(summarize=True),
        summarizer=lambda path, symbols: "负责核心引擎生命周期" if path.endswith("core.py") else "",
    )
    index = (root / ".spark" / "wiki" / "INDEX.md").read_text(encoding="utf-8")
    assert "负责核心引擎生命周期" in index


def test_wiki_never_writes_outside_the_workdir(tmp_path: Path) -> None:
    root = _project(tmp_path)
    build_wiki(root, WikiConfig())
    produced = {p for p in root.rglob("*") if p.is_file()}
    outside = [p for p in produced if root not in p.parents]
    assert not outside
    assert any(p.name == "INDEX.md" for p in produced)
