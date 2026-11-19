from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import urllib.error
import urllib.request

from spark.config import SparkConfig
from spark.models import ApprovalRequest, ToolCall, TurnEvent
from spark.store import SessionStore
from spark.web.server import (
    WebApprover,
    _cache_control,
    _SSESession,
    build_web_server,
    serve_web,
    ui_dir_for,
)


class RecordingWriter:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self._lock = threading.Lock()

    def write(self, data: bytes) -> None:
        with self._lock:
            self.chunks.append(bytes(data))

    def flush(self) -> None:
        return None

    def data(self) -> list[dict]:
        chunks = list(self.chunks)
        payloads: list[dict] = []
        for chunk in chunks:
            for block in chunk.split(b"\n\n"):
                for line in block.splitlines():
                    if line.startswith(b"data: "):
                        payloads.append(json.loads(line[6:].decode("utf-8")))
        return payloads

    def frames(self) -> list[dict]:
        """Return parsed SSE frames including optional ``id:`` lines."""
        chunks = list(self.chunks)
        frames: list[dict] = []
        for chunk in chunks:
            for block in chunk.split(b"\n\n"):
                if not block.strip():
                    continue
                event_id = ""
                data = None
                for line in block.splitlines():
                    if line.startswith(b"id: "):
                        event_id = line[4:].decode("utf-8")
                    elif line.startswith(b"data: "):
                        data = json.loads(line[6:].decode("utf-8"))
                if data is not None:
                    frames.append({"id": event_id, "data": data})
        return frames


class FailingWriter:
    def __init__(self) -> None:
        self.closed = threading.Event()

    def write(self, data: bytes) -> None:
        raise ConnectionAbortedError("client went away")

    def flush(self) -> None:
        raise ConnectionAbortedError("client went away")


def test_sse_writer_notices_idle_socket_close() -> None:
    server_socket, client_socket = socket.socketpair()
    wfile = server_socket.makefile("wb")
    disconnected = threading.Event()
    session = _SSESession(
        wfile,
        connection=server_socket,
        on_disconnect=disconnected.set,
        close_connection=server_socket.close,
    )
    session.start()
    try:
        client_socket.shutdown(socket.SHUT_RDWR)
        client_socket.close()
        assert disconnected.wait(1.0)
    finally:
        session.abort()
        try:
            wfile.close()
        except OSError:
            pass
        server_socket.close()


def test_sse_writer_merges_text_and_preserves_event_types() -> None:
    writer = RecordingWriter()
    session = _SSESession(
        writer,
        flush_interval=0.02,
        heartbeat_interval=1.0,
        max_text_chars=1024,
    )
    session.start()
    assert session.submit(TurnEvent(type="text_delta", text="a"))
    assert session.submit(TurnEvent(type="text_delta", text="b"))
    assert session.submit(
        TurnEvent(type="tool_start", tool_call=ToolCall(id="t", name="x", arguments={}))
    )
    assert session.finish()

    payloads = writer.data()
    assert payloads[:3] == [
        {"type": "text_delta", "text": "ab"},
        {"type": "tool_start", "tool": {"id": "t", "name": "x", "arguments": {}}},
        {"type": "done"},
    ]


def test_sse_envelope_carries_session_turn_and_seq() -> None:
    writer = RecordingWriter()
    session = _SSESession(
        writer,
        flush_interval=0.02,
        heartbeat_interval=1.0,
        max_text_chars=1024,
        session_id="s-1",
        turn_id="t-1",
    )
    session.start()
    assert session.submit(TurnEvent(type="text_delta", text="a"))
    assert session.submit(TurnEvent(type="text_delta", text="b"))
    assert session.submit_payload({"type": "context", "data": {"used": 1}})
    assert session.finish()

    frames = writer.frames()
    assert [frame["data"]["type"] for frame in frames] == [
        "text_delta",
        "context",
        "done",
    ]
    assert [frame["data"]["seq"] for frame in frames] == [1, 2, 3]
    assert [frame["id"] for frame in frames] == ["1", "2", "3"]
    assert all(frame["data"]["v"] == 1 for frame in frames)
    assert all(frame["data"]["session_id"] == "s-1" for frame in frames)
    assert all(frame["data"]["turn_id"] == "t-1" for frame in frames)


def test_sse_legacy_mode_writes_bare_frames() -> None:
    writer = RecordingWriter()
    session = _SSESession(writer, flush_interval=0.02, heartbeat_interval=1.0)
    session.start()
    assert session.submit_payload({"type": "text_delta", "text": "legacy"})
    assert session.finish()

    frames = writer.frames()
    assert frames[0]["id"] == ""
    assert frames[0]["data"] == {"type": "text_delta", "text": "legacy"}
    assert "v" not in frames[0]["data"]


def test_sse_writer_emits_idle_heartbeat() -> None:
    writer = RecordingWriter()
    session = _SSESession(writer, flush_interval=0.02, heartbeat_interval=0.05)
    session.start()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and b": ping\n\n" not in writer.chunks:
        time.sleep(0.01)
    session.abort()

    assert b": ping\n\n" in writer.chunks


def test_sse_writer_disconnect_cancels_turn() -> None:
    disconnected = threading.Event()
    transport_closed = threading.Event()
    session = _SSESession(
        FailingWriter(),
        on_disconnect=disconnected.set,
        close_connection=transport_closed.set,
    )
    session.start()
    assert session.submit(TurnEvent(type="context", data={}))

    assert disconnected.wait(1.0)
    assert transport_closed.is_set()
    assert session.disconnected
    session.abort()


def test_sse_writer_disconnects_when_queue_is_full() -> None:
    entered = threading.Event()
    released = threading.Event()
    disconnected = threading.Event()

    class BlockingWriter:
        def write(self, data: bytes) -> None:
            entered.set()
            released.wait(1.0)

        def flush(self) -> None:
            return None

    session = _SSESession(
        BlockingWriter(),
        on_disconnect=disconnected.set,
        close_connection=released.set,
        max_queue=2,
    )
    session.start()
    assert session.submit(TurnEvent(type="context", data={}))
    assert entered.wait(1.0)
    assert session.submit(TurnEvent(type="context", data={}))
    assert session.submit(TurnEvent(type="context", data={}))
    assert not session.submit(TurnEvent(type="context", data={}))

    assert disconnected.wait(1.0)
    session.abort()


def test_web_approver_cancel_interrupts_wait() -> None:
    async def scenario() -> None:
        approver = WebApprover()
        request = ApprovalRequest(
            tool_call=ToolCall(id="call", name="run_shell", arguments={}),
            summary="run",
        )
        task = asyncio.create_task(approver(request))
        for _ in range(20):
            if approver.pending:
                break
            await asyncio.sleep(0.01)
        approver.cancel()
        decision = await asyncio.wait_for(task, timeout=0.5)
        assert decision.action == "deny"

    asyncio.run(scenario())


def test_cache_policy_distinguishes_html_and_assets() -> None:
    assert _cache_control("/") == "no-cache"
    assert _cache_control("/index.html") == "no-cache"
    assert _cache_control("/assets/index-abc123.js") == (
        "public, max-age=31536000, immutable"
    )


def test_live_stream_keeps_sse_event_contract(tmp_path, monkeypatch) -> None:
    import spark.web.server as server_module

    monkeypatch.setattr(server_module, "default_home", lambda: tmp_path / "home")
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "web.db")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    thread = threading.Thread(
        target=serve_web,
        kwargs={
            "workdir": tmp_path,
            "cfg": cfg,
            "store": store,
            "host": "127.0.0.1",
            "port": port,
        },
        daemon=True,
    )
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/status", timeout=2):
                break
        except (urllib.error.URLError, OSError):
            time.sleep(0.05)
    else:
        store.close()
        raise AssertionError("server did not start")

    with urllib.request.urlopen(base + "/", timeout=5) as response:
        assert response.headers["Cache-Control"] == "no-cache"
    with urllib.request.urlopen(base + "/index.html", timeout=5) as response:
        assert response.headers["Cache-Control"] == "no-cache"
    ui = ui_dir_for()
    assert ui is not None
    asset = next((ui / "assets").iterdir())
    asset_path = "/" + asset.relative_to(ui).as_posix()
    with urllib.request.urlopen(base + asset_path, timeout=5) as response:
        assert response.headers["Cache-Control"] == (
            "public, max-age=31536000, immutable"
        )

    request = urllib.request.Request(
        base + "/api/chat/stream",
        data=json.dumps({"prompt": "hello"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
    finally:
        store.close()

    events = [
        json.loads(line[6:])
        for frame in raw.split("\n\n")
        for line in frame.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["type"] for event in events] == [
        "context",
        "text_delta",
        "context",
        "turn_end",
        "done",
    ]
    assert events[1]["text"]
    assert all(event["v"] == 1 for event in events)
    assert all(event["session_id"] for event in events)
    assert all(event["turn_id"] for event in events)
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))


def test_peer_disconnect_is_suppressed_during_connect_grace(monkeypatch) -> None:
    """Linux 回归：建连瞬间 select 可读 + recv 空会被误判断连，整条流 abort。

    Windows 上不复现（select 语义不同），所以这个不变量必须显式钉住：
    grace 窗口内根本不做 peer 检查，窗口过后才恢复（真实关闭的检测由
    test_sse_writer_notices_idle_socket_close 覆盖）。
    """
    from spark.web import sse as sse_mod

    class FakeConn:
        def __init__(self) -> None:
            self.recv_calls = 0

        def fileno(self) -> int:
            return -1

        def recv(self, n, flags=0):
            self.recv_calls += 1
            return b""

    conn = FakeConn()
    session = sse_mod._SSESession(object(), connection=conn)
    # select 永远报可读，模拟 Linux 建连瞬间的行为
    monkeypatch.setattr(sse_mod.select, "select", lambda *a, **k: ([conn], [], []))
    assert session._peer_disconnected() is False
    assert conn.recv_calls == 0, "grace 窗口内不应做 recv"
    session._created_at -= 1.0
    assert session._peer_disconnected() is True
    assert conn.recv_calls == 1


def test_legacy_sse_env_switch(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SPARK_WEB_LEGACY", "1")
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.model = "mock-model"
    store = SessionStore(tmp_path / "legacy.db")
    server, state = build_web_server(
        workdir=tmp_path,
        cfg=cfg,
        store=store,
        host="127.0.0.1",
        port=0,
    )
    try:
        assert state.legacy_sse is True
    finally:
        server.server_close()
        state.close()
        store.close()


def test_legacy_session_stream_writes_bare_data_frames() -> None:
    """The legacy switch also applies to /api/sessions/{id}/stream."""
    import json

    from spark.web.session_stream import _write_event

    class _WFile:
        def __init__(self) -> None:
            self.data = b""

        def write(self, data: bytes) -> None:
            self.data += data

        def flush(self) -> None:
            pass

    wfile = _WFile()
    _write_event(
        type("H", (), {"wfile": wfile})(),  # type: ignore[arg-type]
        3,
        {
            "v": 1,
            "session_id": "s",
            "turn_id": "t",
            "seq": 3,
            "type": "text_delta",
            "text": "hi",
        },
        legacy=True,
    )
    assert wfile.data.startswith(b"data: ")
    payload = json.loads(wfile.data.decode("utf-8").removeprefix("data: ").strip())
    assert payload == {"type": "text_delta", "text": "hi"}
