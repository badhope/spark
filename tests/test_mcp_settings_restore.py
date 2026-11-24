from __future__ import annotations

from types import SimpleNamespace

import pytest

from spark.config import McpServerConfig, McpTransportConfig, SparkConfig
from spark.web._handlers_settings import _restore_redacted_mcp_headers
from spark.web.utils import MCP_REDACTED_HEADER


def _state(*servers: McpServerConfig) -> SimpleNamespace:
    cfg = SparkConfig()
    cfg.mcp_servers = list(servers)
    return SimpleNamespace(cfg=cfg)


def test_stdio_env_is_restored_only_for_same_command() -> None:
    saved = McpServerConfig(
        name="stdio",
        command="npx",
        args=["server"],
        env={"GITHUB_TOKEN": "ghp-secret"},
    )
    state = _state(saved)
    restored = _restore_redacted_mcp_headers(
        state,
        [{"name": "stdio", "command": "npx", "args": ["server"], "env": {"GITHUB_TOKEN": MCP_REDACTED_HEADER}}],
    )
    assert restored[0]["env"]["GITHUB_TOKEN"] == "ghp-secret"

    with pytest.raises(ValueError, match="explicit replacement"):
        _restore_redacted_mcp_headers(
            state,
            [{"name": "stdio", "command": "other", "args": ["server"], "env": {}}],
        )


def test_redacted_env_cannot_cross_to_changed_command_even_with_replace() -> None:
    saved = McpServerConfig(name="stdio", command="npx", env={"TOKEN": "secret"})
    state = _state(saved)
    with pytest.raises(ValueError, match="cannot be restored"):
        _restore_redacted_mcp_headers(
            state,
            [
                {
                    "name": "stdio",
                    "command": "other",
                    "env": {"TOKEN": MCP_REDACTED_HEADER},
                    "env_replace": True,
                }
            ],
        )


def test_http_headers_restore_only_when_url_is_unchanged() -> None:
    saved = McpServerConfig(
        name="remote",
        transport="sse",
        http=McpTransportConfig(
            url="https://mcp.example/sse",
            headers={"Authorization": "Bearer secret", "X-Trace": "public"},
        ),
    )
    state = _state(saved)
    same_url = _restore_redacted_mcp_headers(
        state,
        [
            {
                "name": "remote",
                "transport": "sse",
                "http": {
                    "url": "https://mcp.example/sse",
                    "headers": {"Authorization": MCP_REDACTED_HEADER, "X-Trace": "public"},
                },
            }
        ],
    )
    assert same_url[0]["http"]["headers"]["Authorization"] == "Bearer secret"
    assert same_url[0]["http"]["headers"]["X-Trace"] == "public"

    changed_url = _restore_redacted_mcp_headers(
        state,
        [
            {
                "name": "remote",
                "transport": "sse",
                "http": {
                    "url": "https://evil.example/sse",
                    "headers": {"Authorization": MCP_REDACTED_HEADER, "X-Trace": "public"},
                },
            }
        ],
    )
    assert "Authorization" not in changed_url[0]["http"]["headers"]
    assert changed_url[0]["http"]["headers"]["X-Trace"] == "public"


def test_non_dict_items_are_preserved() -> None:
    state = _state()
    items = _restore_redacted_mcp_headers(state, ["legacy", 3, None, {"name": "x"}])
    assert items[0] == "legacy"
    assert items[1] == 3
    assert items[2] is None
    assert items[3] == {"name": "x"}
