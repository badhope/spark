"""Public positioning and built-in MCP coverage must not regress."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PUBLIC_SURFACES = [
    "README.md",
    "README_zh.md",
    "pyproject.toml",
    "mkdocs.yml",
    "docs/index.md",
    "docs/en/README.md",
    "docs/ARCHITECTURE.md",
    "docs/en/ARCHITECTURE.md",
    "docs/REMAINING.md",
    "src/spark/desktop.py",
    "src/spark/a2a_server.py",
    "src/spark/__init__.py",
    "web/src/i18n.ts",
    "scripts/build_promo.py",
    "bucket/spark.json",
    "bandit.yaml",
    "SECURITY.md",
    "CLAUDE.md",
]

OLD_POSITIVE_PHRASES = [
    "Local-first AI coding agent",
    "local-first AI coding agent",
    "本地优先的 AI 编程",
    "本地优先编码 Agent",
    "Local-first coding agent",
    "本地优先的 AI 编码助手",
]


def test_public_surfaces_do_not_use_retired_positioning() -> None:
    offenders = []
    for rel in PUBLIC_SURFACES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for phrase in OLD_POSITIVE_PHRASES:
            if phrase in text:
                offenders.append(f"{rel}: {phrase}")
    assert not offenders, offenders


def test_canonical_positioning_exists_and_names_the_three_pillars() -> None:
    text = (ROOT / "docs/PRODUCT_POSITIONING.md").read_text(encoding="utf-8")
    assert "浏览器原生、跨平台、开源可扩展" in text
    assert "不是“本地优先”产品" in text
    assert "全浏览器能力接入层" in text


def test_builtin_mcp_presets_cover_browser_code_and_docs() -> None:
    text = (ROOT / "web/src/components/SettingsModal.tsx").read_text(encoding="utf-8")
    for name in ("playwright", "chrome-devtools", "repomix", "markitdown"):
        assert f'name: "{name}"' in text
    assert text.count("name: \"") >= 7
