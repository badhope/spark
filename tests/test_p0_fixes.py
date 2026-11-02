from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from spark.config import McpServerConfig, SparkConfig, load_config, save_config
from spark.models import ApprovalDecision, ApprovalRequest, ToolCall
from spark.sandbox import WorkdirSandbox
from spark.tools.mcp_bridge import McpBridge
from spark.tools.registry import ToolContext, ToolRegistry
from spark.web.server import WebApprover, parse_mcp_servers, ui_dir_for

FAKE_SERVER = r"""
import json, sys

def read():
    line = sys.stdin.readline()
    if not line:
        return None
    return json.loads(line)

def write(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

while True:
    msg = read()
    if msg is None:
        break
    method = msg.get("method")
    req_id = msg.get("id")
    if method == "initialize":
        write({"jsonrpc": "2.0", "id": req_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "0.0.0"}}})
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        write({"jsonrpc": "2.0", "id": req_id, "result": {"tools": [{"name": "echo", "description": "echo", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}]}})
    elif method == "tools/call":
        args = (msg.get("params") or {}).get("arguments") or {}
        write({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": args.get("text", "")}]}})
    else:
        if req_id is not None:
            write({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "unknown"}})
"""


def test_make_audit_invokes_pip_audit_as_python_module() -> None:
    make = shutil.which("make")
    if make is None:
        pytest.skip("make is unavailable")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [make, "-n", "audit"], cwd=root, capture_output=True, text=True, check=True
    )
    command = next(line for line in result.stdout.splitlines() if "-m pip_audit" in line)
    assert "-m pip -m pip_audit" not in command
    assert "-m pip_audit --progress-spinner off" in command


def test_web_approver_includes_tool_call_id() -> None:
    async def scenario() -> ApprovalDecision:
        approver = WebApprover()
        req = ApprovalRequest(
            tool_call=ToolCall(
                id="call_abc", name="run_shell", arguments={"command": "ls"}
            ),
            summary="run ls",
        )
        task = asyncio.ensure_future(approver(req))
        for _ in range(50):
            if approver.pending:
                break
            await asyncio.sleep(0.05)
        approver.respond("allow")
        return await asyncio.wait_for(task, timeout=2)

    decision = asyncio.run(scenario())
    assert decision.tool_call_id == "call_abc"
    assert decision.action == "allow"


def test_save_config_roundtrip_preserves_agent_context_memory(tmp_path: Path) -> None:
    cfg = SparkConfig()
    cfg.agent.sandbox_mode = "full-access"
    cfg.agent.approval = "auto-edit"
    cfg.agent.workdir_only = False
    cfg.agent.shell_timeout_sec = 42
    cfg.agent.max_tool_rounds = 17
    cfg.agent.max_output_chars = 1234
    cfg.agent.protected_paths = ["/etc", "secret"]
    cfg.agent.show_thinking = False
    cfg.agent.show_plan = False
    cfg.context.agents_md = "CUSTOM.md"
    cfg.context.max_context_tokens = 65536
    cfg.context.compact_threshold = 0.7
    cfg.context.keep_recent_messages = 5
    cfg.memory.enabled = False
    cfg.memory.top_k = 3
    cfg.memory.auto_extract = False
    path = save_config(cfg, tmp_path / "config.toml")
    loaded = load_config(config_path=path, workdir=tmp_path)
    assert loaded.agent.sandbox_mode == "full-access"
    assert loaded.agent.approval == "auto-edit"
    assert loaded.agent.workdir_only is False
    assert loaded.agent.shell_timeout_sec == 42
    assert loaded.agent.max_tool_rounds == 17
    assert loaded.agent.max_output_chars == 1234
    assert loaded.agent.protected_paths == ["/etc", "secret"]
    assert loaded.agent.show_thinking is False
    assert loaded.agent.show_plan is False
    assert loaded.context.agents_md == "CUSTOM.md"
    assert loaded.context.max_context_tokens == 65536
    assert loaded.context.compact_threshold == 0.7
    assert loaded.context.keep_recent_messages == 5
    assert loaded.memory.enabled is False
    assert loaded.memory.top_k == 3
    assert loaded.memory.auto_extract is False


def test_parse_mcp_servers_returns_config_models() -> None:
    parsed = parse_mcp_servers(
        [
            {
                "name": "fake",
                "command": "python",
                "args": ["-u"],
                "readonly_tools": ["a"],
            },
            {"name": "", "command": "x"},
        ]
    )
    assert len(parsed) == 1
    assert isinstance(parsed[0], McpServerConfig)
    assert parsed[0].name == "fake"
    with pytest.raises(ValueError):
        parse_mcp_servers([{"name": "x"}])


def test_parse_mcp_servers_accepts_remote_transports() -> None:
    """registry 首屏 20 条全是 remote：只认 stdio 会让「一键添加」永久禁用。"""

    parsed = parse_mcp_servers(
        [
            {
                "name": "remote-http",
                "transport": "streamable_http",
                "http": {"url": "https://example.com/mcp", "headers": {"X-A": "1"}},
            },
            {"name": "remote-sse", "transport": "sse", "http": {"url": "https://example.com/sse"}},
        ]
    )
    assert len(parsed) == 2
    assert parsed[0].transport == "streamable_http"
    assert parsed[0].http is not None and parsed[0].http.url == "https://example.com/mcp"
    assert parsed[0].http.headers == {"X-A": "1"}
    assert parsed[1].transport == "sse"

    with pytest.raises(ValueError):
        parse_mcp_servers([{"name": "remote-no-url", "transport": "streamable_http"}])


def test_mcp_call_survives_loop_restart(tmp_path: Path) -> None:
    script = tmp_path / "server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    server = McpServerConfig(
        name="fake", command=sys.executable, args=["-u", str(script)]
    )
    registry = ToolRegistry(ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg))
    bridge = McpBridge()
    asyncio.run(bridge.start([server], registry))
    registry.ctx.mcp_call = bridge.call
    result = asyncio.run(_call_mcp(bridge, "mcp__fake__echo", {"text": "cross-loop"}))
    assert result.ok
    assert "cross-loop" in str(result.payload)
    asyncio.run(bridge.close())


async def _call_mcp(bridge: McpBridge, name: str, args: dict):
    return await bridge.call(name, args)


def test_ui_dir_finds_react_dist() -> None:
    ui = ui_dir_for()
    assert ui is not None
    assert (ui / "index.html").is_file()


def test_makefile_uses_the_platform_venv_layout() -> None:
    makefile = Path(__file__).parents[1] / "Makefile"
    text = makefile.read_text(encoding="utf-8")
    expected = ".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python"
    assert expected in text

    make = shutil.which("make")
    if make is None:
        return
    expanded = subprocess.run(
        [make, "-n", "test"],
        cwd=makefile.parent,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert expected in expanded
    if sys.platform == "win32":
        assert ".venv/bin/python" not in expanded

    lint_expanded = subprocess.run(
        [make, "-n", "lint"],
        cwd=makefile.parent,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert f"{expected} -m ruff check src/spark tests" in lint_expanded
    assert "pip install" not in lint_expanded
