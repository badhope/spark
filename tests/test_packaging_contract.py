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


def test_specs_ship_the_casbin_policies() -> None:
    """Without them the control plane raises at startup (authz.py loads the model).

    The one-file and desktop specs were missing this while spark.spec had it,
    so those two builds could not run `spark enterprise` at all.
    """
    for name in SPECS:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "src/spark/control/policies" in text, (
            f"{name} does not bundle the Casbin policies"
        )


def test_specs_ship_the_prompt_templates() -> None:
    for name in SPECS:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "src/spark/prompts" in text, f"{name} does not bundle the prompts"


#: Third-party packages that read data files at runtime via importlib.resources.
#: PyInstaller cannot see those reads, so the data has to be listed explicitly.
#: Both entries here shipped broken builds before being added: rapidocr's ONNX
#: models (image/PDF import crashed) and faster-whisper's Silero VAD weights
#: (transcription died with an ONNX NoSuchFile *after* the model had loaded).
RUNTIME_DATA = {
    "rapidocr_onnxruntime": ("*.onnx", "*.yaml"),
    "faster_whisper": ("*.onnx",),
}


def test_specs_collect_runtime_data_files() -> None:
    for name in SPECS:
        text = (ROOT / name).read_text(encoding="utf-8")
        for package, patterns in RUNTIME_DATA.items():
            assert f'"{package}"' in text, f"{name} never collects data for {package}"
            for pattern in patterns:
                assert f'"{pattern}"' in text, (
                    f"{name} does not collect {pattern} for {package}; "
                    "the frozen build fails at runtime, not at build time"
                )


def test_specs_glob_their_data_patterns() -> None:
    """Patterns must stay globs: upstream renames data files between releases.

    faster-whisper already renamed its VAD asset once
    (``silero_vad.onnx`` → ``silero_vad_v6.onnx``), so a hard-coded filename
    would silently stop matching instead of failing loudly. Comments are ignored
    here — they legitimately mention the file name to explain why it is globbed.
    """
    import re

    for name in SPECS:
        text = (ROOT / name).read_text(encoding="utf-8")
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        for match in re.finditer(r"silero[^\"']*", code):
            raise AssertionError(
                f"{name} hard-codes the VAD asset name {match.group(0)!r}; glob instead"
            )
