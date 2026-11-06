"""把 spark 工具注册表反向暴露为 MCP server（stdio transport）。

spark 从「MCP client」升级为「双向节点」：任意 MCP 客户端
（Claude Code / Cursor / Cline / 其他 agent harness）都可以把
spark 当作工具服务器使用，直接调用 read_file / list_dir /
run_shell / git_* / web_search 等全部内建工具。

实现直接采用 mcp 官方 Python SDK 的 FastMCP（MIT），零新增依赖；
工具 schema 原样取自 ToolRegistry.schemas()，执行走
ToolRegistry.execute —— 沙箱、审批、路径防护全部复用现有链路。

用法::

    spark mcp-serve                      # 启动 stdio MCP server
    spark mcp-serve --port 8931          # streamable HTTP 模式
"""

from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.tools.base import ToolAnnotations

from spark import __version__
from spark.config import SparkConfig
from spark.models import ToolCall
from spark.policy import READONLY_TOOLS
from spark.sandbox import WorkdirSandbox
from spark.tools.registry import ToolContext, ToolRegistry

logger = logging.getLogger(__name__)

_INSTRUCTIONS = """Spark 内建工具服务器。

你正在通过 MCP 使用一个本地自托管的 AI 编程智能体（spark）的全部工具。
所有文件操作都限制在其工作目录内；危险操作受审批策略约束。
工具返回 JSON 文本：{ok, payload}，ok=false 时 payload.error 为错误原因。"""


def _make_handler(registry: ToolRegistry, name: str, properties: dict, required: list[str]):
    """为单个工具动态生成带显式参数的 async handler。

    FastMCP 的客户端参数校验（arg_model）来自函数签名，因此不能用
    ``**kwargs``；这里按工具的 JSON Schema properties 生成真实参数，
    ``None`` 即未传（required 由 openapi_schema 交给客户端约束）。
    """
    props = sorted(properties.keys()) if properties else []
    # 生成: async def _handler(path=None, offset=None, ...): return await _exec(...)
    params = ", ".join(f"{p}=None" for p in props)
    call_args = ", ".join(props)
    source = (
        "async def _handler(" + params + "):\n"
        f"    return await _exec({call_args})\n"
    )
    async def _exec(*args):
        kwargs = {p: v for p, v in zip(props, args) if v is not None}
        result = await registry.execute(
            ToolCall(id=f"mcp-{name}", name=name, arguments=kwargs)
        )
        return _format_result(result)

    namespace: dict = {"_exec": _exec}
    exec(compile(source, f"<mcp-tool-{name}>", "exec"), namespace)  # noqa: S102 — 静态 schema，非用户输入
    return namespace["_handler"]


def build_mcp_server(workdir: str, cfg: SparkConfig) -> FastMCP:
    """构建暴露 spark 全部内建工具的 FastMCP 实例。"""
    from pathlib import Path

    wd = Path(workdir).resolve()
    ctx = ToolContext(sandbox=WorkdirSandbox(wd), config=cfg)
    registry = ToolRegistry(ctx)

    mcp = FastMCP(name="spark", instructions=_INSTRUCTIONS)
    # FastMCP leaves the low-level Server's version unset, so `initialize`
    # answers with the MCP SDK's own package version (e.g. 1.30.0). Every other
    # surface reports spark.__version__; a client that logs the server version
    # would otherwise see a number that moves whenever the SDK is upgraded.
    mcp._mcp_server.version = __version__  # noqa: SLF001 — SDK exposes no setter

    for schema in registry.schemas():
        fn = schema["function"]
        name = fn["name"]
        if name == "task":
            # task 由 AgentLoop 特判执行；独立注册后在 MCP 侧恒失败，不暴露。
            continue
        desc = fn.get("description", "")
        params = fn.get("parameters", {})
        properties = params.get("properties", {})
        required = params.get("required", [])

        handler = _make_handler(registry, name, properties, required)
        annotations = ToolAnnotations(readOnlyHint=name in READONLY_TOOLS)
        mcp.add_tool(
            handler,
            name=name,
            description=desc,
            annotations=annotations,
        )
        logger.debug("exposed tool %s (props=%d)", name, len(properties))
    return mcp


def _format_result(result) -> str:
    """ToolResult → 结构化 JSON 文本（MCP 返回值必须是字符串）。"""
    payload = result.payload if result.payload is not None else {}
    return json.dumps(
        {"ok": bool(result.ok), "payload": payload},
        ensure_ascii=False,
        default=str,
    )
