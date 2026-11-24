from __future__ import annotations

import json
import logging
import queue
import select
import socket
import threading
import time

from spark.sanitize import sanitize, sanitize_sse_payload
from spark.web.utils import _event_payload

logger = logging.getLogger(__name__)

SSE_TEXT_FLUSH_INTERVAL = 0.04
SSE_TEXT_MAX_CHARS = 1024
SSE_HEARTBEAT_INTERVAL = 15.0
SSE_MAX_QUEUED_EVENTS = 256
SSE_WRITER_POLL_INTERVAL = 0.25
SSE_DISCONNECT_ERRORS = (
    ConnectionError,
    BrokenPipeError,
    ConnectionAbortedError,
    OSError,
)
_SSE_DONE = object()
_SSE_STOP = object()


class _SSESession:
    _pending_text: list[str]
    _pending_chars: int
    _pending_since: float | None
    _last_write: float

    def __init__(
        self,
        wfile,
        *,
        connection=None,
        on_disconnect=None,
        close_connection=None,
        flush_interval: float = SSE_TEXT_FLUSH_INTERVAL,
        heartbeat_interval: float = SSE_HEARTBEAT_INTERVAL,
        max_text_chars: int = SSE_TEXT_MAX_CHARS,
        max_queue: int = SSE_MAX_QUEUED_EVENTS,
        secrets: tuple[str, ...] = (),
        session_id: str = "",
        turn_id: str = "",
        start_seq: int = 0,
        on_event=None,
    ) -> None:
        self._wfile = wfile
        self._connection = connection
        self._on_disconnect = on_disconnect
        self._close_connection = close_connection
        self._on_event = on_event
        self._session_id = str(session_id or "")
        self._turn_id = str(turn_id or "")
        self._envelope_enabled = bool(self._session_id or self._turn_id)
        self._seq = max(0, int(start_seq))
        self._peer_monitor_disabled = False
        # Linux 上 select+MSG_PEEK 在「刚建连、请求体刚读完」的极短窗口里可能
        # 报告可读且 recv 返回空，被误判成断连 —— 整条 SSE 流还没写出就被 abort
        # （CI ubuntu 100% 复现，Windows 不复现）。只在建连后的 grace 窗口内
        # 抑制该判定，空闲连接的真实关闭仍能被发现（见
        # tests/test_web_sse.py::test_sse_writer_notices_idle_socket_close）。
        self._created_at = time.monotonic()
        self._peer_grace_sec = 0.25
        self._flush_interval = max(0.001, float(flush_interval))
        self._heartbeat_interval = max(0.001, float(heartbeat_interval))
        self._max_text_chars = max(1, int(max_text_chars))
        self._secrets = tuple(s for s in secrets if s)
        self._queue: queue.Queue[object] = queue.Queue(maxsize=max(1, int(max_queue)))
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._disconnected = threading.Event()
        self._disconnect_lock = threading.Lock()

    @property
    def disconnected(self) -> bool:
        return self._disconnected.is_set()

    def set_start_seq(self, seq: int) -> None:
        """Set the next envelope number before the first payload is written."""
        self._seq = max(0, int(seq))

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="spark-sse", daemon=True)
        self._thread.start()

    def submit(self, event) -> bool:
        return self._enqueue(event)

    def submit_payload(self, payload: dict) -> bool:
        # The envelope (v/session_id/turn_id/seq) is added by the single writer
        # thread at flush time. Keeping the payload structured here lets text
        # deltas be merged without losing or reordering seq numbers.
        return self._enqueue(dict(payload))

    def finish(self) -> bool:
        if self._stop.is_set() or self._disconnected.is_set():
            self._wait()
            return False
        try:
            self._queue.put(_SSE_DONE, timeout=0.5)
        except queue.Full:
            self._disconnect()
            self._wait()
            return False
        self._wait()
        return not self._disconnected.is_set()

    def abort(self) -> None:
        if not self._disconnected.is_set():
            self._stop.set()
            self._close_transport()
            try:
                self._queue.put_nowait(_SSE_STOP)
            except queue.Full:
                pass
        self._wait()

    def _enqueue(self, item: object) -> bool:
        if self._stop.is_set() or self._disconnected.is_set():
            return False
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            self._disconnect()
            return False
        return True

    def _next_timeout(self, pending_since: float | None, last_write: float) -> float:
        now = time.monotonic()
        deadlines = [
            now + SSE_WRITER_POLL_INTERVAL,
            last_write + self._heartbeat_interval,
        ]
        if pending_since is not None:
            deadlines.append(pending_since + self._flush_interval)
        return max(0.0, min(deadlines) - now)

    def _peer_disconnected(self) -> bool:
        if self._connection is None or self._peer_monitor_disabled:
            return False
        if time.monotonic() - self._created_at < self._peer_grace_sec:
            return False
        try:
            readable, _, _ = select.select([self._connection], [], [], 0)
            if not readable:
                return False
            data = self._connection.recv(1, getattr(socket, "MSG_PEEK", 0))
            if data:
                self._peer_monitor_disabled = True
                return False
            return True
        except (OSError, ValueError):
            return True

    def _flush_pending(self) -> None:
        if not self._pending_text:
            return
        text = sanitize("".join(self._pending_text), self._secrets)
        self._write_payload({"type": "text_delta", "text": text})
        self._pending_text.clear()
        self._pending_chars = 0
        self._pending_since = None
        self._last_write = time.monotonic()

    def _append_text(self, value: str) -> None:
        while value:
            if not self._pending_text:
                self._pending_since = time.monotonic()
            room = self._max_text_chars - self._pending_chars
            part = value[:room]
            self._pending_text.append(part)
            self._pending_chars += len(part)
            value = value[len(part):]
            if self._pending_chars >= self._max_text_chars:
                self._flush_pending()
            elif (
                self._pending_since is not None
                and time.monotonic() - self._pending_since >= self._flush_interval
            ):
                self._flush_pending()

    def _run(self) -> None:
        self._pending_text = []
        self._pending_chars = 0
        self._pending_since = None
        self._last_write = time.monotonic()
        try:
            while True:
                if self._stop.is_set():
                    return
                if self._peer_disconnected():
                    self._disconnect()
                    return
                try:
                    item = self._queue.get(
                        timeout=self._next_timeout(self._pending_since, self._last_write)
                    )
                except queue.Empty:
                    now = time.monotonic()
                    if (
                        self._pending_text
                        and self._pending_since is not None
                        and now - self._pending_since >= self._flush_interval
                    ):
                        self._flush_pending()
                    if time.monotonic() - self._last_write >= self._heartbeat_interval:
                        self._write(b": ping\n\n")
                        self._last_write = time.monotonic()
                    continue
                if item is _SSE_STOP:
                    return
                if item is _SSE_DONE:
                    self._flush_pending()
                    self._write_payload({"type": "done"})
                    return
                if isinstance(item, bytes):
                    self._flush_pending()
                    self._write(item)
                    self._last_write = time.monotonic()
                    continue
                if isinstance(item, dict):
                    self._flush_pending()
                    self._write_payload(item)
                    self._last_write = time.monotonic()
                    continue
                event_type = getattr(item, "type", "")
                text = getattr(item, "text", None)
                if event_type == "text_delta" and text:
                    self._append_text(str(text))
                    continue
                self._flush_pending()
                self._write_payload(_event_payload(item))
                self._last_write = time.monotonic()
        except SSE_DISCONNECT_ERRORS:
            self._disconnect()
        except (TypeError, ValueError, UnicodeError):
            self._disconnect()

    def _write_payload(self, payload: dict) -> None:
        """Write one JSON event, optionally wrapped in the v1 envelope.

        Envelope mode is opt-in: production chat streams pass session_id and
        turn_id, while older unit tests / embedders can keep the legacy bare
        ``data:`` frames.
        """
        if not isinstance(payload, dict):
            payload = {"type": "message", "data": payload}
        if self._envelope_enabled:
            self._seq += 1
            envelope = {
                "v": 1,
                "session_id": self._session_id,
                "turn_id": self._turn_id,
                "seq": self._seq,
            }
            envelope.update(payload)
            clean = sanitize_sse_payload(envelope, self._secrets)
            raw = json.dumps(clean, ensure_ascii=False)
            frame = f"id: {self._seq}\ndata: {raw}\n\n".encode("utf-8")
            if self._on_event is not None:
                try:
                    self._on_event(dict(clean))
                except Exception:  # pragma: no cover - replay buffer must not kill stream
                    logger.debug("SSE on_event callback failed", exc_info=True)
            self._write(frame)
            return
        clean = sanitize_sse_payload(payload, self._secrets)
        raw = json.dumps(clean, ensure_ascii=False)
        self._write(b"data: " + raw.encode("utf-8") + b"\n\n")

    def _write(self, data: bytes) -> None:
        self._wfile.write(data)
        flush = getattr(self._wfile, "flush", None)
        if flush is not None:
            flush()

    def _disconnect(self) -> None:
        with self._disconnect_lock:
            if self._disconnected.is_set():
                return
            self._disconnected.set()
            self._stop.set()
        try:
            if self._on_disconnect is not None:
                self._on_disconnect()
        finally:
            self._close_transport()

    def _close_transport(self) -> None:
        if self._close_connection is None:
            return
        try:
            self._close_connection()
        except SSE_DISCONNECT_ERRORS:
            pass

    def _wait(self) -> None:
        if self._thread is None or self._thread is threading.current_thread():
            return
        self._thread.join(timeout=2.0)
