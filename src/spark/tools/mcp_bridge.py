"""MCP bridge — thin wrapper over the official ``mcp`` Python SDK.

Replaces the hand-rolled JSON-RPC/SSE implementation with the official SDK.  The
SDK provides production-grade transports, protocol negotiation, session
management and reconnection — none of which we need to reinvent.

Supported transports (all official SDK):

* ``stdio`` → :func:`mcp.client.stdio.stdio_client`
* ``sse`` → :func:`mcp.client.sse.sse_client` (legacy SSE servers)
* ``streamable_http`` → :func:`mcp.client.streamable_http.streamable_http_client`
  (modern replacement for SSE; preferred when available)
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from typing import Any, Coroutine, cast

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl

from spark.config import McpServerConfig
from spark.models import ToolResult
from spark.policy import mcp_tool_allowed
from spark.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-server connection — one MCP server session
# ---------------------------------------------------------------------------


class _McpConnection:
    """Wraps a single MCP server connection using the official SDK."""

    def __init__(self, cfg: McpServerConfig) -> None:
        self.cfg = cfg
        self.tools: list[dict[str, Any]] = []
        self.resource_templates: list[dict[str, Any]] = []
        self._exit_stack = AsyncExitStack()
        self._session: ClientSession | None = None

    async def start(
        self,
        timeout_sec: float = 15.0,
        *,
        sampling_callback: Any = None,
        elicitation_callback: Any = None,
        message_handler: Any = None,
    ) -> None:
        """Connect, negotiate protocol version, and enumerate tools.

        ``timeout_sec`` 在**本协程内部**实施（``asyncio.timeout``），调用方不要再用
        ``asyncio.wait_for`` 包裹：``wait_for`` 超时会取消承载本协程的 task，而其
        ``AsyncExitStack`` 里的 anyio cancel scope 是在该 task 中进入的——之后在别的
        task 里 ``aclose()`` 会抛 "Attempted to exit cancel scope in a different task"
        （Python 3.11 + anyio 4.x 上必现）。

        P2 MCP 全能力回调：
        * ``sampling_callback`` — server 反向请求 LLM 补全（经 Spark Provider）；
        * ``elicitation_callback`` — server 请求用户输入（当前策略：一律 decline，
          hook 点已就位，UI 通道后接）；
        * ``message_handler`` — 接收 server 通知（tools/list_changed → 动态重注册）。
        """
        transport: AbstractAsyncContextManager[Any]
        if self.cfg.transport == "stdio":
            params = StdioServerParameters(
                command=self.cfg.command,
                args=self.cfg.args or [],
                # Merge over the inherited environment: the server needs PATH to
                # start at all, and per-server tokens live in cfg.env.
                env={**os.environ, **(self.cfg.env or {})} if self.cfg.env else None,
            )
            transport = stdio_client(params)
        elif self.cfg.http is not None:
            url = self.cfg.http.url
            headers = self.cfg.http.headers or {}
            timeout = self.cfg.http.timeout_sec or 30
            if self.cfg.transport == "sse":
                transport = sse_client(url=url, headers=headers, sse_read_timeout=timeout * 5)
            else:  # streamable_http
                http_client = httpx.AsyncClient(
                    base_url=url,
                    headers=headers,
                    timeout=timeout,
                )
                transport = streamable_http_client(url=url, http_client=http_client)
        else:
            raise RuntimeError(
                f"MCP server {self.cfg.name} requires http transport but none configured"
            )

        async with asyncio.timeout(timeout_sec):
            # stdio_client / sse_client yield (read, write); streamable_http_client
            # additionally yields a get_session_id callable. Unpacking it as a
            # pair raised "too many values to unpack" and every remote server
            # failed to start.
            entered = await self._exit_stack.enter_async_context(transport)
            read, write = entered[0], entered[1]
            session = await self._exit_stack.enter_async_context(
                ClientSession(
                    read,
                    write,
                    sampling_callback=sampling_callback,
                    elicitation_callback=elicitation_callback,
                    message_handler=message_handler,
                )
            )
            await session.initialize()
            self._session = session

            listed = await session.list_tools()
            self.tools = [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": t.inputSchema
                    if isinstance(t.inputSchema, dict)
                    else {"type": "object"},
                }
                for t in listed.tools
            ]
            # P2：resource templates（uriTemplate 形态的被动资源）
            try:
                listed_templates = await session.list_resource_templates()
                self.resource_templates = [
                    {
                        "uriTemplate": str(t.uriTemplate),
                        "name": getattr(t, "name", None),
                        "description": getattr(t, "description", None),
                        "mimeType": getattr(t, "mimeType", None),
                    }
                    for t in (getattr(listed_templates, "resourceTemplates", None) or [])
                ]
            except Exception as exc:
                logger.debug(
                    "MCP %s list_resource_templates unavailable: %s", self.cfg.name, exc
                )
                self.resource_templates = []
        logger.info("MCP server '%s' connected: %d tool(s)", self.cfg.name, len(self.tools))

    async def list_resource_templates(self) -> list[dict[str, Any]]:
        return list(self.resource_templates)

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if self._session is None:
            return ToolResult(ok=False, payload={"error": f"MCP {self.cfg.name} not connected"})
        try:
            result = await self._session.call_tool(name, arguments)
        except Exception as exc:
            logger.exception("MCP call %s.%s failed", self.cfg.name, name)
            # ExceptionGroup/CancelledError can stringify to nothing, which left
            # the model with an error it could not act on.
            detail = str(exc).strip() or exc.__class__.__name__
            return ToolResult(ok=False, payload={"error": detail})
        is_error = getattr(result, "isError", False)
        payload = _content_to_payload(result.content)
        return ToolResult(ok=not is_error, payload=payload)


    async def list_resources(self) -> dict[str, Any]:
        if self._session is None:
            return {"error": f"MCP {self.cfg.name} not connected"}
        try:
            res = await self._session.list_resources()
        except Exception as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            return {"error": detail}
        return {
            "resources": [
                {
                    "uri": str(r.uri),
                    "name": getattr(r, "name", None),
                    "description": getattr(r, "description", None),
                    "mimeType": getattr(r, "mimeType", None),
                }
                for r in (getattr(res, "resources", None) or [])
            ]
        }

    async def read_resource(self, uri: str) -> ToolResult:
        if self._session is None:
            return ToolResult(ok=False, payload={"error": f"MCP {self.cfg.name} not connected"})
        try:
            res = await self._session.read_resource(AnyUrl(uri))  # SDK 要求 AnyUrl 类型
        except Exception as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            return ToolResult(ok=False, payload={"error": detail})
        contents = [
            {
                "uri": str(getattr(c, "uri", "")),
                "mimeType": getattr(c, "mimeType", None),
                "text": getattr(c, "text", None),
            }
            for c in (getattr(res, "contents", None) or [])
        ]
        return ToolResult(ok=True, payload={"contents": contents})

    async def list_prompts(self) -> dict[str, Any]:
        if self._session is None:
            return {"error": f"MCP {self.cfg.name} not connected"}
        try:
            res = await self._session.list_prompts()
        except Exception as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            return {"error": detail}
        return {
            "prompts": [
                {
                    "name": p.name,
                    "description": getattr(p, "description", None),
                    "arguments": [
                        {"name": a.name, "description": getattr(a, "description", None), "required": getattr(a, "required", False)}
                        for a in (getattr(p, "arguments", None) or [])
                    ],
                }
                for p in (getattr(res, "prompts", None) or [])
            ]
        }

    async def get_prompt(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if self._session is None:
            return ToolResult(ok=False, payload={"error": f"MCP {self.cfg.name} not connected"})
        try:
            res = await self._session.get_prompt(name, arguments)
        except Exception as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            return ToolResult(ok=False, payload={"error": detail})
        messages = [
            {
                "role": getattr(m, "role", "user"),
                "text": getattr(getattr(m, "content", None), "text", None),
            }
            for m in (getattr(res, "messages", None) or [])
        ]
        return ToolResult(ok=True, payload={"messages": messages})

    async def close(self) -> None:
        """Close the session/transport.

        失败启动过的连接（``start`` 抛错）其 ``AsyncExitStack`` 可能已部分展开；
        ``aclose()`` 在极少数实现差异下会抛 RuntimeError，此时吞掉并继续——
        资源本已释放，调用方（``_start_on_owner``）不应因清理失败而中断整个启动流程。

        anyio 的 streamable_http 客户端会在 ``aclose()`` 时把子任务里的原始错误
        重新抛成 ``BaseExceptionGroup``（例如 401），同样不能让它冒出去覆盖
        真正的启动失败原因。
        """
        try:
            await self._exit_stack.aclose()
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:  # noqa: BLE001 — 清理失败不该中断宿主
            logger.debug("MCP %s close raised %r", self.cfg.name, exc)
        self._session = None
        self.tools = []


def _content_to_payload(content: Any) -> dict[str, Any]:
    """Convert MCP SDK content blocks to a plain dict."""
    if content is None:
        return {"result": None}
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for block in content:
            if hasattr(block, "text"):
                texts.append(block.text)
            elif hasattr(block, "data"):
                texts.append(str(block.data))
            else:
                texts.append(str(block))
        return {"content": texts, "text": "\n".join(texts) if texts else ""}
    return {"result": str(content)}


# ---------------------------------------------------------------------------
# Bridge — orchestrates one or more MCP servers
# ---------------------------------------------------------------------------

McpServer = _McpConnection


class McpBridge:
    """Manages multiple MCP server connections via the official SDK.

    Exposes the same public API as the original hand-rolled bridge so callers
    (cli.py, server.py, tui/app.py) do not need extensive changes.

    P2 全能力扩展：
    * :meth:`set_sampling_handler` — 把 Spark Provider 挂为 server 反向
      sampling 的大脑（server 请求补全 → provider.stream → 文本返回）；
    * elicitation — 当前策略一律 decline（hook 点已就位，UI 通道后接）；
    * tools/list_changed — 动态重注册该 server 的工具 schema（最小权限
      白名单在重注册时同样强制）。
    """

    def __init__(self) -> None:
        self.servers: dict[str, _McpConnection] = {}
        self.errors: list[str] = []
        self.startup_errors: list[str] = []
        self._registry: ToolRegistry | None = None
        self._sampling_handler: Any = None
        # The SDK binds stdio streams and session tasks to the loop that opened
        # them, so a bridge created inside a short-lived ``asyncio.run`` becomes
        # uncallable as soon as that loop closes — which is every CLI command and
        # every web turn. Owning a loop keeps sessions alive for the process.
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._run_loop, name="mcp-loop", daemon=True)
        self._loop_thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _on_owner(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """Await *coro* on the bridge's loop, no matter which loop calls us."""
        if asyncio.get_running_loop() is self._loop:
            return await coro
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self._loop))

    # -- P2：sampling（server 反向请求补全）---------------------------------

    def set_sampling_handler(self, handler: Any) -> None:
        """挂载 sampling 处理器（Provider-like，需有 ``stream``）；None 停用。"""
        self._sampling_handler = handler

    async def _provider_sampling(
        self, messages: list[dict[str, Any]], *, system: str | None, max_tokens: int
    ) -> str:
        """把 MCP SamplingMessage 列表交给 Spark Provider 补全，返回文本。"""
        provider = self._sampling_handler
        if provider is None or not hasattr(provider, "stream"):
            return ""
        from spark.models import ChatMessage, MessageRole

        chat_messages: list[ChatMessage] = []
        if system:
            chat_messages.append(ChatMessage(role="system", content=system))
        for item in messages:
            role = str(item.get("role") or "user")
            if role not in ("user", "assistant"):
                role = "user"
            chat_messages.append(
                ChatMessage(role=cast(MessageRole, role), content=str(item.get("content") or ""))
            )
        parts: list[str] = []
        async for delta in provider.stream(chat_messages, tools=[]):
            if delta.type == "text" and delta.text:
                parts.append(delta.text)
            elif delta.type == "end":
                break
        return "".join(parts)

    async def _sampling_callback(self, context: Any) -> Any:
        from mcp.types import CreateMessageResult, TextContent

        params = getattr(context, "params", None)
        raw_messages = getattr(params, "messages", None) or []
        messages: list[dict[str, Any]] = []
        for item in raw_messages:
            content = getattr(item, "content", None)
            text = getattr(content, "text", None)
            messages.append(
                {"role": str(getattr(item, "role", "user")), "content": str(text or "")}
            )
        system = getattr(params, "systemPrompt", None)
        max_tokens = int(getattr(params, "maxTokens", 0) or 0)
        try:
            text = await self._provider_sampling(
                messages, system=str(system) if system else None, max_tokens=max_tokens
            )
        except Exception as exc:
            logger.warning("MCP sampling failed: %s", exc)
            text = ""
        return CreateMessageResult(
            role="assistant",
            model="spark",
            content=TextContent(type="text", text=text),
            stopReason="endTurn" if text else "error",
        )

    async def _elicitation_callback(self, context: Any) -> Any:
        from mcp.types import ElicitResult

        logger.info(
            "MCP server elicitation request declined (no UI channel yet): %s",
            getattr(getattr(context, "params", None), "message", ""),
        )
        return ElicitResult(action="decline")

    # -- 注册 / 动态重注册 ---------------------------------------------------

    def _namespaced_schemas(self, name: str, server: _McpConnection) -> list[dict[str, Any]]:
        """server 工具 → 命名空间 schema（强制 allowed_tools 最小权限）。"""
        schemas: list[dict[str, Any]] = []
        for tool in server.tools:
            raw_name = tool.get("name") or "tool"
            if not mcp_tool_allowed(server.cfg.allowed_tools, raw_name):
                continue
            ns = f"mcp__{name}__{raw_name}"
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": ns,
                        "description": tool.get("description") or ns,
                        "parameters": tool.get("inputSchema")
                        or {"type": "object", "properties": {}},
                    },
                }
            )
        return schemas

    def register_into(self, registry: ToolRegistry) -> None:
        """Register all MCP tools into the registry (read-only annotations)."""
        self._registry = registry
        for name, server in self.servers.items():
            schemas = self._namespaced_schemas(name, server)
            if schemas:
                registry.register_scope(f"mcp__{name}", schemas)
            for tool in server.tools:
                raw_name = tool.get("name") or ""
                ns = f"mcp__{name}__{raw_name}"
                if raw_name in server.cfg.readonly_tools:
                    registry.readonly_mcp.add(ns)

    async def _refresh_server_tools(self, server_name: str) -> None:
        """tools/list_changed：重列工具 → unregister 旧 scope → 重注册。"""
        registry = self._registry
        server = self.servers.get(server_name)
        if registry is None or server is None or server._session is None:
            return
        try:
            listed = await server._session.list_tools()
        except Exception as exc:
            logger.warning("MCP %s re-list tools failed: %s", server_name, exc)
            return
        server.tools = [
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": t.inputSchema
                if isinstance(t.inputSchema, dict)
                else {"type": "object"},
            }
            for t in listed.tools
        ]
        registry.register_scope(f"mcp__{server_name}", self._namespaced_schemas(server_name, server))
        logger.info(
            "MCP %s tools changed: re-registered %d tool(s)", server_name, len(server.tools)
        )

    async def start(self, configs: list[McpServerConfig], registry: ToolRegistry) -> None:
        """Connect to all MCP servers in *configs* and register their tools.

        同时兜底把 ``bridge.call`` 绑定到 ``registry.ctx.mcp_call``：CLI/Web 由
        编排层显式绑定，但直接调用本 API（脚本/测试）也应 start 后即可执行，
        否则已注册的工具会以 "Unknown tool" 静默失败。
        """
        await self._on_owner(self._start_on_owner(configs, registry))
        if registry.ctx.mcp_call is None:
            registry.ctx.mcp_call = self.call

    async def _start_on_owner(self, configs: list[McpServerConfig], registry: ToolRegistry) -> None:
        for cfg in configs:
            conn = _McpConnection(cfg)
            try:
                # 超时由 conn.start 内部实施；不要用 asyncio.wait_for 包裹——
                # 其超时会取消本 task，导致 anyio cancel scope 跨 task 退出
                # （Python 3.11 上抛 RuntimeError）。
                await conn.start(
                    sampling_callback=self._sampling_callback,
                    elicitation_callback=self._elicitation_callback,
                    message_handler=self._make_message_handler(cfg.name),
                )
            except Exception as exc:
                logger.warning(
                    "MCP server '%s' failed to start: %s", cfg.name, exc, exc_info=True
                )
                message = f"MCP {cfg.name} failed: {exc}"
                self.errors.append(message)
                self.startup_errors.append(message)
                await conn.close()
                continue
            except BaseException as exc:
                # asyncio.timeout 在握手阶段超时会抛 CancelledError（BaseException），
                # 上面的 except Exception 拦不住，会一路冒到 spark web 的启动调用，
                # 让一个连不上的远程 server 拖垮整个进程。这里兜住并记为该 server
                # 启动失败，绝不因为单个 MCP server 影响宿主。
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                logger.warning(
                    "MCP server '%s' aborted during start: %r", cfg.name, exc
                )
                reason = (
                    "握手超时或被取消"
                    if isinstance(exc, asyncio.CancelledError)
                    else type(exc).__name__
                )
                message = f"MCP {cfg.name} failed: {reason}"
                self.errors.append(message)
                self.startup_errors.append(message)
                await conn.close()
                continue
            # P1-9: 最小权限白名单（加载路径强制）。
            # allowed_tools 非空 = 该 server 进入最小权限模式：公告的工具里
            # 未声明的一律不注册（默认拒绝），并记录被拦截的工具名，
            # 便于用户及时发现 server 新增了未授权能力。
            if cfg.allowed_tools:
                kept: list[dict[str, Any]] = []
                dropped: list[str] = []
                for tool in conn.tools:
                    tname = tool.get("name") or ""
                    if mcp_tool_allowed(cfg.allowed_tools, tname):
                        kept.append(tool)
                    else:
                        dropped.append(tname)
                if dropped:
                    logger.warning(
                        "MCP server '%s' blocked %d tool(s) outside whitelist %s: %s",
                        cfg.name,
                        len(dropped),
                        cfg.allowed_tools,
                        ", ".join(dropped),
                    )
                    self.errors.append(
                        f"MCP {cfg.name} blocked tools outside whitelist: {', '.join(dropped)}"
                    )
                conn.tools = kept
            self.servers[cfg.name] = conn
        self.register_into(registry)

    def _make_message_handler(self, server_name: str):
        """server 通知处理器：tools/list_changed → 动态重注册该 server。"""
        from mcp.types import ToolListChangedNotification

        async def on_message(message: Any) -> None:
            try:
                # RequestResponder 包裹真实 notification；两种形态都兼容
                payload = getattr(message, "message", message)
                if isinstance(payload, ToolListChangedNotification):
                    await self._refresh_server_tools(server_name)
            except Exception as exc:
                logger.warning(
                    "MCP %s notification handling failed: %s", server_name, exc
                )

        return on_message

    async def call(self, namespaced: str, arguments: dict[str, Any]) -> ToolResult:
        """Call a namespaced MCP tool: ``mcp__<server>__<tool>``."""
        return await self._on_owner(self._call_on_owner(namespaced, arguments))


    async def list_resources(self) -> list[dict[str, Any]]:
        """聚合所有 server 的 resources 列表（P2：MCP 全能力——resources）。"""
        return await self._on_owner(self._resources_on_owner())

    async def read_resource(self, server: str, uri: str) -> ToolResult:
        return await self._on_owner(self._read_resource_on_owner(server, uri))

    async def list_prompts(self) -> list[dict[str, Any]]:
        """聚合所有 server 的 prompts 列表（P2：MCP 全能力——prompts）。"""
        return await self._on_owner(self._prompts_on_owner())

    async def get_prompt(self, server: str, name: str, arguments: dict[str, Any]) -> ToolResult:
        return await self._on_owner(self._get_prompt_on_owner(server, name, arguments))

    async def _resources_on_owner(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name, conn in self.servers.items():
            entry = {"server": name, **await conn.list_resources()}
            # P2：templates 一并聚合（uriTemplate 形态资源）
            entry["templates"] = await conn.list_resource_templates()
            out.append(entry)
        return out

    async def _read_resource_on_owner(self, server: str, uri: str) -> ToolResult:
        conn = self.servers.get(server)
        if conn is None:
            return ToolResult(ok=False, payload={"error": f"MCP server not found: {server}"})
        return await conn.read_resource(uri)

    async def _prompts_on_owner(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name, conn in self.servers.items():
            out.append({"server": name, **await conn.list_prompts()})
        return out

    async def _get_prompt_on_owner(self, server: str, name: str, arguments: dict[str, Any]) -> ToolResult:
        conn = self.servers.get(server)
        if conn is None:
            return ToolResult(ok=False, payload={"error": f"MCP server not found: {server}"})
        return await conn.get_prompt(name, arguments)

    async def _call_on_owner(self, namespaced: str, arguments: dict[str, Any]) -> ToolResult:
        parts = namespaced.split("__", 2)
        if len(parts) != 3:
            return ToolResult(ok=False, payload={"error": f"Bad MCP tool name: {namespaced}"})
        _, server_name, tool_name = parts
        server = self.servers.get(server_name)
        if server is None:
            return ToolResult(
                ok=False,
                payload={"error": f"MCP server not available: {server_name}"},
            )
        # P1-9: 调用路径复核白名单（防御纵深）。注册时已按 allowed_tools
        # 过滤，此处再兜底一次：即使 registry 里出现了越权工具，也到不了
        # server。错误信息显式给出白名单内容，便于用户排障。
        if not mcp_tool_allowed(server.cfg.allowed_tools, tool_name):
            return ToolResult(
                ok=False,
                payload={
                    "error": (
                        f"MCP tool denied by whitelist: mcp__{server_name}__{tool_name}"
                        f" (allowed_tools={server.cfg.allowed_tools})"
                    )
                },
            )
        return await server.call(tool_name, arguments)

    async def close(self) -> None:
        """Shut down all MCP connections and the bridge's own loop."""
        await self._on_owner(self._close_on_owner())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=5)
        if not self._loop.is_closed():
            self._loop.close()

    async def _close_on_owner(self) -> None:
        for server in self.servers.values():
            try:
                await server.close()
            except Exception as exc:
                logger.warning("Error closing MCP server '%s': %s", server.cfg.name, exc)
        self.servers.clear()
