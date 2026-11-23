from __future__ import annotations

import json
import tempfile
from pathlib import Path

from spark.config import SparkConfig
from spark.sandbox import WorkdirSandbox
from spark.tools.registry import CODING_TOOL_ALLOWLIST, ToolContext, ToolRegistry


def _registry(cfg: SparkConfig) -> ToolRegistry:
    root = Path(tempfile.mkdtemp(prefix="spark-tool-preset-"))
    return ToolRegistry(ToolContext(sandbox=WorkdirSandbox(root, cfg), config=cfg))


def test_coding_preset_removes_large_tool_surfaces() -> None:
    cfg = SparkConfig()
    registry = _registry(cfg)
    full = registry.schemas()
    assert len(full) == 75
    registry.apply_tool_preset("coding")
    coding = registry.schemas()
    assert {s["function"]["name"] for s in coding} == set(CODING_TOOL_ALLOWLIST)
    names = {s["function"]["name"] for s in coding}
    assert "browser_click" not in names
    # A cheap model with web tools in a coding task will search syntax instead of
    # reading the repo; keep the coding surface offline-only.
    assert "web_search" not in names and "web_fetch" not in names
    # The win is real, not cosmetic: tool schema bytes drop by >60%.
    full_chars = len(json.dumps(full, ensure_ascii=False))
    coding_chars = len(json.dumps(coding, ensure_ascii=False))
    assert coding_chars < full_chars * 0.4

    # Switching back must restore the full surface, not leave a stale allowlist.
    registry.apply_tool_preset("full")
    assert len(registry.schemas()) == 75


def test_explicit_allowlist_is_never_overwritten() -> None:
    cfg = SparkConfig()
    registry = _registry(cfg)
    registry.tool_allowlist = {"read_file"}
    registry.apply_tool_preset("coding")
    assert registry.tool_allowlist == {"read_file"}
    assert [s["function"]["name"] for s in registry.schemas()] == ["read_file"]


def test_config_keeps_tool_preset() -> None:
    cfg = SparkConfig.model_validate({"agent": {"tool_preset": "coding"}})
    assert cfg.agent.tool_preset == "coding"
