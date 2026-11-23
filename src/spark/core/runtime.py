"""Process-wide agent runtime: one event loop, one owner thread.

Why this exists
---------------
Several long-lived resources are bound to the event loop that created them:

* ``aiosqlite`` connections (behind LangGraph's ``AsyncSqliteSaver``) create
  futures on their constructing loop;
* the official MCP SDK enters ``anyio`` cancel scopes that must be exited from
  the same task that entered them;
* LiteLLM's async client caches an ``httpx.AsyncClient`` per loop.

The web server used to call ``AgentLoop.iter_turn_sync``, which built a *new*
event loop for every turn.  That made a persistent SQLite turn checkpointer
structurally impossible (``Future attached to a different loop``), so crash
recovery silently ran on ``MemorySaver`` and died with the process.

``AgentRuntime`` owns a single daemon thread running one loop forever.  Every
surface that wants durable turn state drives its turns through this runtime:

* the HTTP server (thread per request) uses :meth:`iterate` (blocking iterator);
* the Textual TUI (its own asyncio loop) uses :meth:`iterate_async`;
* ``spark exec`` / ACP use :meth:`run` or :meth:`iterate_async`.

Turns stay serialized per session by the caller's own lock — this module only
provides the loop, not concurrency control.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import queue
import threading
import time
from collections.abc import AsyncIterator, Coroutine, Iterator
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_DEFAULT_TIMEOUT = 600.0
_QUEUE_MAX = 512


class AgentRuntimeClosed(RuntimeError):
    """The runtime was closed while a turn was still in flight."""


class AgentRuntime:
    """A background asyncio loop plus sync/async bridges onto it."""

    def __init__(self, name: str = "spark-runtime") -> None:
        self._name = name
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        """True only while the owner thread is actually alive.

        Checking just ``_loop is not None`` masked a dead thread: every later
        ``run()`` would block until its timeout instead of failing fast.
        """
        return (
            self._loop is not None
            and not self._closed
            and self._thread is not None
            and self._thread.is_alive()
        )

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        """The owned loop, or None before :meth:`start`."""
        return self._loop

    def start(self) -> asyncio.AbstractEventLoop:
        """Start (idempotently) and return the owned loop."""
        with self._lock:
            if self._closed:
                raise AgentRuntimeClosed("AgentRuntime is closed")
            if self._loop is not None:
                return self._loop
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def run() -> None:
                asyncio.set_event_loop(loop)
                loop.call_soon(ready.set)
                try:
                    loop.run_forever()
                finally:
                    try:
                        loop.run_until_complete(loop.shutdown_asyncgens())
                    except Exception:  # pragma: no cover - shutdown best effort
                        logger.debug("asyncgen shutdown failed", exc_info=True)

            thread = threading.Thread(target=run, name=self._name, daemon=True)
            thread.start()
            if not ready.wait(timeout=5):
                logger.warning("agent runtime thread did not signal readiness")
            self._loop = loop
            self._thread = thread
            return loop

    def close(self, timeout: float = 5.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loop, thread = self._loop, self._thread
        if loop is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=timeout)

    # -- submission --------------------------------------------------------

    def submit(self, coro: Coroutine[Any, Any, T]) -> concurrent.futures.Future[T]:
        """Run *coro* on the owned loop; returns a concurrent Future."""
        loop = self.start()  # raises AgentRuntimeClosed when this runtime is dead
        if loop.is_closed():  # pragma: no cover - defensive
            raise AgentRuntimeClosed("AgentRuntime loop is closed")
        return asyncio.run_coroutine_threadsafe(coro, loop)

    def run(self, coro: Coroutine[Any, Any, T], timeout: float = _DEFAULT_TIMEOUT) -> T:
        """Blocking run of *coro* on the owned loop.

        Fails fast — never waits the full timeout — when the runtime is closed or
        its owner thread has died. A dead runtime used to make every caller block
        for the whole timeout; called once per test teardown that turned the suite
        into a multi-hour hang.
        """
        if not self.running:
            coro.close()  # avoid "coroutine was never awaited"
            raise AgentRuntimeClosed("AgentRuntime is not running")
        future = self.submit(coro)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError(f"agent runtime timed out after {timeout}s") from None
        except concurrent.futures.CancelledError:
            raise AgentRuntimeClosed("AgentRuntime task was cancelled") from None

    # -- driving an async generator on the owned loop ----------------------

    @staticmethod
    async def _pump(agen: AsyncIterator[T], out: "asyncio.Queue[tuple[str, Any]]") -> None:
        """Drain *agen* into *out*, terminating with a sentinel either way.

        *out* is an ``asyncio.Queue`` driven with ``await put()``: back-pressure
        suspends the *producer coroutine* instead of blocking the loop thread.
        A synchronous ``queue.Queue`` with a blocking ``put`` could be stalled
        forever by one non-reading consumer (a stalled HTTP writer), freezing
        every session on the shared loop — reproduced before this change.
        """
        try:
            async for item in agen:
                await out.put(("item", item))
        except asyncio.CancelledError:
            try:
                out.put_nowait(("cancelled", None))
            except asyncio.QueueFull:
                pass
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised on the caller side
            try:
                out.put_nowait(("error", exc))
            except asyncio.QueueFull:
                pass
        else:
            try:
                out.put_nowait(("done", None))
            except asyncio.QueueFull:
                pass
        finally:
            aclose = getattr(agen, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:  # pragma: no cover - close best effort
                    logger.debug("agent generator close failed", exc_info=True)

    def iterate(
        self,
        agen: AsyncIterator[T],
        timeout: float = _DEFAULT_TIMEOUT,
        max_seconds: float | None = None,
    ) -> Iterator[T]:
        """Consume *agen* synchronously, from a non-async thread (HTTP handlers).

        The producer coroutine lives on the owned loop and awaits ``queue.put``,
        so a slow consumer applies back-pressure to the turn instead of blocking
        the shared loop. A single long-lived reader task on that loop feeds a
        thread-safe queue this thread blocks on.
        """
        loop = self.start()
        aqueue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        pump = self.submit(self._pump(agen, aqueue))
        out: queue.Queue = queue.Queue(maxsize=_QUEUE_MAX)
        stop = threading.Event()

        async def reader() -> None:
            while not stop.is_set():
                try:
                    item = await aqueue.get()
                except asyncio.CancelledError:
                    return
                if stop.is_set():
                    return
                while not stop.is_set():
                    try:
                        out.put(item, timeout=0.1)
                        break
                    except queue.Full:
                        continue
                if item[0] in ("done", "error", "cancelled"):
                    return

        loop.call_soon_threadsafe(lambda: loop.create_task(reader()))
        deadline = (
            time.monotonic() + float(max_seconds)
            if max_seconds is not None and float(max_seconds) > 0
            else None
        )
        try:
            while True:
                wait = timeout
                if deadline is not None:
                    wait = max(0.05, min(timeout, deadline - time.monotonic()))
                try:
                    kind, payload = out.get(timeout=wait)
                except queue.Empty:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"turn exceeded total runtime of {max_seconds}s"
                        ) from None
                    raise TimeoutError(f"turn produced no output for {timeout}s") from None
                if kind == "item":
                    yield payload
                elif kind == "error":
                    raise payload
                else:  # done / cancelled
                    return
        finally:
            stop.set()
            if not pump.done():
                pump.cancel()
            try:
                pump.result(timeout=5)
            except Exception:
                logger.debug("agent pump cleanup failed", exc_info=True)

    async def iterate_async(self, agen: AsyncIterator[T]) -> AsyncIterator[T]:
        """Consume *agen* from a caller-loop coroutine (Textual, ACP).

        One long-lived reader task on the runtime loop drains the queue in order
        and republishes each item onto the caller's loop with
        ``call_soon_threadsafe``. Creating a fresh ``Queue.get()`` per poll (the
        previous design) raced several pending gets for the same item and lost
        events.
        """
        loop = self.start()
        aqueue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        pump = self.submit(self._pump(agen, aqueue))
        caller = asyncio.get_running_loop()
        sink: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()

        async def reader() -> None:
            while not stop.is_set():
                try:
                    item = await aqueue.get()
                except asyncio.CancelledError:
                    return
                if caller.is_closed():
                    return
                try:
                    caller.call_soon_threadsafe(sink.put_nowait, item)
                except RuntimeError:
                    return
                if item[0] in ("done", "error", "cancelled"):
                    return

        def start_reader() -> None:
            loop.create_task(reader())

        loop.call_soon_threadsafe(start_reader)
        try:
            while True:
                kind, payload = await sink.get()
                if kind == "item":
                    yield payload
                elif kind == "error":
                    raise payload
                else:
                    return
        finally:
            stop.set()
            if not pump.done():
                pump.cancel()
            try:
                await asyncio.wrap_future(pump)
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("agent pump cleanup failed", exc_info=True)


_RUNTIME: AgentRuntime | None = None
_RUNTIME_LOCK = threading.Lock()


def get_runtime() -> AgentRuntime:
    """Process-wide runtime singleton (started on first use).

    Self-healing: if the previous runtime was closed (tests call
    ``reset_persistent_checkpointer`` between cases) or its thread died, a fresh
    one replaces it. Returning a dead instance made every caller block for its
    full timeout.
    """
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None or not _RUNTIME.running:
            _RUNTIME = AgentRuntime()
            _RUNTIME.start()
        return _RUNTIME


def shutdown_runtime() -> None:
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is not None:
            _RUNTIME.close()
            _RUNTIME = None


__all__ = ["AgentRuntime", "AgentRuntimeClosed", "get_runtime", "shutdown_runtime"]
