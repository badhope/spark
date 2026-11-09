"""ACP server 端到端测试：in-process 双端连接（socketpair），mock provider 驱动。

覆盖：initialize 握手、new_session + prompt 流式、工具调用事件、审批桥
（allow/deny/allow_always）、取消、load_session 历史回放。
"""

from __future__ import annotations

import asyncio
import os
import socket
from pathlib import Path
from typing import Any

import pytest
from acp import AgentSideConnection, ClientSideConnection
from acp.schema import (
    AllowedOutcome,
    DeniedOutcome,
    Implementation,
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    ReadTextFileResponse,
    RequestPermissionResponse,
    SessionNotification,
    WriteTextFileResponse,
)

from spark.acp_server import SparkAcpAgent
from spark.config import SparkConfig
from spark.models import ChatDelta, ToolCall
from spark.providers.mock import MockProvider
from spark.store import SessionStore


class FakeClient:
    """ACP client 测试替身：收集 update，权限请求按脚本自动应答。"""

    def __init__(self, permission_choice: str = "allow_once") -> None:
        self.updates: list[SessionNotification] = []
        self.permission_choice = permission_choice
        self.permission_requests: list[Any] = []

    async def request_permission(
        self, session_id: str, tool_call: Any, options: list[Any], **_: Any
    ) -> RequestPermissionResponse:
        self.permission_requests.append((session_id, tool_call, options))
        if self.permission_choice == "cancel":
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id=self.permission_choice)
        )

    async def session_update(self, session_id: str, update: Any, **_: Any) -> None:
        self.updates.append(SessionNotification(session_id=session_id, update=update))

    async def write_text_file(self, session_id: str, path: str, content: str, **_: Any) -> WriteTextFileResponse:
        Path(path).write_text(content, encoding="utf-8")
        return WriteTextFileResponse()

    async def read_text_file(self, session_id: str, path: str, **_: Any) -> ReadTextFileResponse:
        return ReadTextFileResponse(content=Path(path).read_text(encoding="utf-8"))

    async def create_terminal(self, **_: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:  # pragma: no cover
        return None

    # -- 断言辅助 ---------------------------------------------------------

    def updates_of(self, kind: str) -> list[Any]:
        return [n.update for n in self.updates if n.update.session_update == kind]

    def message_text(self) -> str:
        return "".join(u.content.text for u in self.updates_of("agent_message_chunk"))

    def user_message_text(self) -> str:
        return "".join(u.content.text for u in self.updates_of("user_message_chunk"))


async def _connect(
    agent: SparkAcpAgent, client: FakeClient
) -> tuple[AgentSideConnection, ClientSideConnection]:
    # Connection 默认 listening=True；这里显式关闭后统一驱动主循环
    a_sock, c_sock = socket.socketpair()
    a_reader, a_writer = await asyncio.open_connection(sock=a_sock)
    c_reader, c_writer = await asyncio.open_connection(sock=c_sock)
    agent_conn = AgentSideConnection(agent, a_writer, a_reader, listening=False)
    client_conn = ClientSideConnection(lambda _conn: client, c_writer, c_reader, listening=False)
    agent.on_connect(agent_conn)
    asyncio.get_running_loop().create_task(agent_conn.listen())
    asyncio.get_running_loop().create_task(client_conn._conn.main_loop())
    await asyncio.sleep(0)
    return agent_conn, client_conn


def _agent(
    tmp_path: Path,
    provider: MockProvider | None = None,
    approval: str = "suggest",
) -> SparkAcpAgent:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = approval  # type: ignore[assignment]
    store = SessionStore(tmp_path / "sessions.db")
    factory = ((lambda _cfg: provider) if provider is not None else None)
    return SparkAcpAgent(workdir=tmp_path, cfg=cfg, store=store, provider_factory=factory)


async def test_initialize_handshake(tmp_path: Path) -> None:
    client = FakeClient()
    agent = _agent(tmp_path, MockProvider())
    agent_conn, client_conn = await _connect(agent, client)

    response = await client_conn.initialize(
        protocol_version=1,
        client_capabilities=None,
        client_info=Implementation(name="zed", version="0.1"),
    )
    assert isinstance(response, InitializeResponse)
    assert response.protocol_version == 1
    assert response.agent_info is not None and response.agent_info.name == "spark"
    assert response.agent_capabilities.load_session is True
    await agent_conn.close()
    await client_conn.close()


async def test_new_session_and_prompt_stream(tmp_path: Path) -> None:
    client = FakeClient()
    provider = MockProvider(rounds=[[ChatDelta(type="text", text="hello "), ChatDelta(type="text", text="acp"), ChatDelta(type="end")]])
    agent = _agent(tmp_path, provider, approval="full-auto")
    agent_conn, client_conn = await _connect(agent, client)

    await client_conn.initialize(protocol_version=1)
    session: NewSessionResponse = await client_conn.new_session(cwd=str(tmp_path))
    assert session.session_id
    assert session.modes is not None and session.modes.available_modes

    result: PromptResponse = await client_conn.prompt(
        session.session_id, [{"type": "text", "text": "hi"}]
    )
    assert result.stop_reason == "end_turn"
    assert client.message_text() == "hello acp"
    await agent_conn.close()
    await client_conn.close()


async def test_tool_call_events_and_approval_allow(tmp_path: Path) -> None:
    client = FakeClient(permission_choice="allow_once")
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(id="c1", name="run_shell", arguments={"command": "echo hi"}),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="done"), ChatDelta(type="end")],
        ]
    )
    agent = _agent(tmp_path, provider, approval="suggest")
    agent_conn, client_conn = await _connect(agent, client)

    await client_conn.initialize(protocol_version=1)
    session = await client_conn.new_session(cwd=str(tmp_path))
    result = await client_conn.prompt(session.session_id, [{"type": "text", "text": "run"}])

    assert result.stop_reason == "end_turn"
    starts = client.updates_of("tool_call")
    assert len(starts) == 1
    assert starts[0].title == "run_shell"
    assert starts[0].status == "pending"
    progresses = client.updates_of("tool_call_update")
    assert len(progresses) == 1
    assert progresses[0].status == "completed"
    # 审批桥收到请求，且 tool_call_id 与 start 通知一致
    assert len(client.permission_requests) == 1
    assert client.permission_requests[0][1].tool_call_id == starts[0].tool_call_id
    assert client.message_text() == "done"
    await agent_conn.close()
    await client_conn.close()


async def test_tool_call_denied(tmp_path: Path) -> None:
    client = FakeClient(permission_choice="reject_once")
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(id="c1", name="run_shell", arguments={"command": "echo hi"}),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="end")],
        ]
    )
    agent = _agent(tmp_path, provider, approval="suggest")
    agent_conn, client_conn = await _connect(agent, client)

    await client_conn.initialize(protocol_version=1)
    session = await client_conn.new_session(cwd=str(tmp_path))
    result = await client_conn.prompt(session.session_id, [{"type": "text", "text": "run"}])

    assert result.stop_reason == "end_turn"
    progresses = client.updates_of("tool_call_update")
    assert len(progresses) == 1
    assert progresses[0].status == "failed"
    await agent_conn.close()
    await client_conn.close()


async def test_prompt_cancelled(tmp_path: Path) -> None:
    client = FakeClient()

    class SlowProvider:
        """持续产 delta 的第一轮：让协作式 cancel 在 delta 间隙生效。"""

        def __init__(self) -> None:
            self.calls = 0

        async def stream(self, messages: Any, tools: Any, **_: Any):
            self.calls += 1
            for _ in range(200):
                await asyncio.sleep(0.05)
                yield ChatDelta(type="text", text=".")

    agent = _agent(tmp_path, MockProvider(), approval="full-auto")
    agent_conn, client_conn = await _connect(agent, client)
    await client_conn.initialize(protocol_version=1)
    session = await client_conn.new_session(cwd=str(tmp_path))
    spark_session = agent._sessions[session.session_id]
    spark_session.loop.provider = SlowProvider()

    prompt_task = asyncio.create_task(
        client_conn.prompt(session.session_id, [{"type": "text", "text": "long"}])
    )
    await asyncio.sleep(0.2)
    await client_conn.cancel(session.session_id)
    result = await asyncio.wait_for(prompt_task, timeout=10)

    assert result.stop_reason == "cancelled"
    await agent_conn.close()
    await client_conn.close()


async def test_load_session_replay_history(tmp_path: Path) -> None:
    client = FakeClient()
    provider = MockProvider(rounds=[[ChatDelta(type="text", text="first answer"), ChatDelta(type="end")]])
    agent = _agent(tmp_path, provider, approval="full-auto")
    agent_conn, client_conn = await _connect(agent, client)

    await client_conn.initialize(protocol_version=1)
    session = await client_conn.new_session(cwd=str(tmp_path))
    await client_conn.prompt(session.session_id, [{"type": "text", "text": "first question"}])

    # 同一连接上重新 load_session → 回放历史（agent 会重建 loop 但复用 store）
    client.updates.clear()
    await client_conn.load_session(cwd=str(tmp_path), session_id=session.session_id)

    texts = client.message_text()
    assert "first question" in client.user_message_text()
    assert "first answer" in texts
    await agent_conn.close()
    await client_conn.close()


async def test_prompt_to_resource_blocks(tmp_path: Path) -> None:
    from acp.helpers import resource_link_block, text_block

    from spark.acp_server import prompt_to_input

    text, images = prompt_to_input(
        [
            text_block("look at"),
            resource_link_block("x.py", "file:///tmp/x.py"),
        ]
    )
    assert "look at" in text
    assert "file:///tmp/x.py" in text
    assert images == []


def test_tool_kind_mapping() -> None:
    from spark.acp_server import tool_kind

    assert tool_kind("read_file") == "read"
    assert tool_kind("run_shell") == "execute"
    assert tool_kind("apply_patch") == "edit"
    assert tool_kind("unknown_tool") == "execute"


def test_map_error_stop_reason() -> None:
    from spark.acp_server import map_error_stop_reason

    assert map_error_stop_reason("Turn cancelled") == "cancelled"
    assert map_error_stop_reason("Reached max_tool_rounds") == "max_turn_requests"
    assert map_error_stop_reason("Token budget exceeded for this turn") == "max_tokens"
    assert map_error_stop_reason("provider exploded") == "refusal"


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason=(
        "spawning `python -m spark acp` needs a full cold import (litellm + "
        "langgraph + textual) and exceeded 90s on GitHub's Windows runners. "
        "The in-process ACP tests above cover the same protocol paths; this one "
        "is a real-process smoke test and is run locally."
    ),
)
async def test_e2e_subprocess(tmp_path: Path) -> None:
    """真实子进程全链路：spawn ``python -m spark acp``，走 initialize/new/prompt。

    本地默认运行；CI 上跳过（冷启动开销过大且与 in-process 用例重复）。
    """
    import sys

    from acp import spawn_agent_process

    TIMEOUT = 90.0

    (tmp_path / "marker.txt").write_text("seed", encoding="utf-8")
    client = FakeClient()

    # spawn_stdio_transport 只透传白名单环境变量（PATH/HOME 等），SPARK_HOME
    # 会被过滤掉——子进程将回退到真实 ~/.spark：HOME 在网络盘上直接崩溃
    # （sqlite disk I/O error），即便本地能过也污染了用户的真实会话库。
    # 测试隔离必须显式把 SPARK_HOME 塞进 spawn env。
    async with spawn_agent_process(
        lambda _conn: client,
        sys.executable,
        "-m",
        "spark",
        "acp",
        "--workdir",
        str(tmp_path),
        "--provider",
        "mock",
        "--approval",
        "full-auto",
        env={"SPARK_HOME": str(tmp_path / ".spark-home")},
        cwd=str(tmp_path),
    ) as (conn, process):
        import asyncio as _aio

        init = await _aio.wait_for(conn.initialize(protocol_version=1), TIMEOUT)
        assert init.agent_info is not None and init.agent_info.name == "spark"
        session = await _aio.wait_for(conn.new_session(cwd=str(tmp_path)), TIMEOUT)
        result = await _aio.wait_for(
            conn.prompt(session.session_id, [{"type": "text", "text": "hello"}]), TIMEOUT
        )
        assert result.stop_reason == "end_turn"
        assert "mock provider" in client.message_text()
        process.terminate()
