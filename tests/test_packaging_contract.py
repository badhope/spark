"""打包契约：PyInstaller spec 必须显式声明函数内延迟导入的第三方包。

`repo_map.py` 与 `wiki.py` 在函数体内 `import tree_sitter_*`，
PyInstaller 的静态分析看不到这类导入。缺了它们，打包版不会报错——
`spark wiki` 只是安静地输出 0 个符号，文档变成空壳。这类"静默降级"
在单元测试里永远抓不到，只能对 spec 文件本身设门禁。

同理，源码新增 hidden import 时三个 spec 必须同步，否则
onefile / onedir / desktop 三种产物能力不一致。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPECS = ("spark-onefile.spec", "spark.spec", "spark-desktop.spec")

#: 由源码内函数级导入触达、必须显式声明的包。
MUST_DECLARE = (
    "tree_sitter",
    "tree_sitter_python",
    "tree_sitter_javascript",
    "tree_sitter_typescript",
)


def test_every_spec_declares_the_tree_sitter_grammars() -> None:
    for name in SPECS:
        text = (ROOT / name).read_text(encoding="utf-8")
        for package in MUST_DECLARE:
            assert f"'{package}'" in text, f"{name} is missing hiddenimport '{package}'"


def test_specs_do_not_exclude_a_required_package() -> None:
    for name in SPECS:
        text = (ROOT / name).read_text(encoding="utf-8")
        excludes = text[text.index("excludes=["):]
        for package in MUST_DECLARE:
            assert f"'{package}'" not in excludes, f"{name} excludes {package}"


def test_specs_ship_the_built_web_dist() -> None:
    """A wheel without dist serves a blank page; the spec must bundle it."""
    for name in SPECS:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "src/spark/web/dist" in text, f"{name} does not bundle the web UI"
