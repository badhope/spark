"""P0-4: spark 内建工具反向暴露为 MCP server 的单元验证。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from spark.config import SparkConfig
from spark.mcp_expose import _format_result, build_mcp_server
from spark.models import ToolResult


def _cfg(workdir: Path) -> SparkConfig:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    cfg.agent.workdir_only = True
    return cfg


def test_build_mcp_server_exposes_all_registry_tools(tmp_path: Path) -> None:
    mcp = build_mcp_server(str(tmp_path), _cfg(tmp_path))
    tools = {t.name for t in mcp._tool_manager.list_tools()}
    assert "read_file" in tools
    assert "list_dir" in tools
    assert "write_file" in tools
    assert "run_shell" in tools
    assert "update_plan" in tools
    assert len(tools) >= 22  # 内建工具全集


def test_format_result_roundtrip() -> None:
    text = _format_result(ToolResult(ok=True, payload={"content": "hi"}))
    assert '"ok": true' in text and "hi" in text
    err = _format_result(
        ToolResult(ok=False, payload={"error": "denied", "tool": "run_shell"})
    )
    assert '"ok": false' in err and "denied" in err


def test_server_reports_spark_version_not_the_sdk_version(tmp_path: Path) -> None:
    """`initialize` must answer with spark's version, not the MCP SDK's.

    FastMCP leaves the low-level Server version unset, so the SDK falls back to
    its own package version ("1.30.0"); a client logging serverInfo.version
    would then see a number that tracks the SDK, not the build.
    """
    from spark import __version__

    mcp = build_mcp_server(str(tmp_path), _cfg(tmp_path))
    assert mcp._mcp_server.version == __version__


def test_handler_executes_through_registry(tmp_path: Path) -> None:
    """动态生成的 handler 真实走 ToolRegistry.execute（沙箱生效）。"""
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    mcp = build_mcp_server(str(tmp_path), _cfg(tmp_path))
    tools = {t.name: t for t in mcp._tool_manager.list_tools()}
    read_file = tools["read_file"]
    result = asyncio.run(read_file.fn(path="a.txt"))
    assert '"ok": true' in result and "hello" in result

    # 越界路径被沙箱拦截（workdir_only）
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    blocked = asyncio.run(read_file.fn(path=str(outside)))
    assert '"ok": false' in blocked

