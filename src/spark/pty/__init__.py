"""内置终端（PTY）管理器。

后端真实 fork 一个交互式 shell（默认 ``$SHELL`` / ``/bin/bash``），
按 ``(会话 id, tab id)`` 维度管理多个互不干扰的 shell 标签页：

* :class:`PtyManager.get_or_create` — 同一 (sid, tab) 复用已有 shell，
  不同 sid 即使 tab_id 相同也各自独立（切会话必须换 cwd，不能留在旧目录）；
* :class:`PtySession.start_reader` — 后台线程持续读取 PTY 输出，
  通过 ``loop.call_soon_threadsafe`` 投递到调用方提供的 ``asyncio.Queue``，
  shell 退出时向队列投递退出标记；
* :class:`PtyManager.write` — 对已关闭/不存在的 tab 是 no-op（返回 False），
  绝不向调用方抛错。

Windows 通过 pywinpty（ConPTY）提供真实 PTY；Linux/macOS 使用 ptyprocess。
整个模块在导入时仍可用，只有缺少对应后端时才在 ``PtySession`` 构造时给出
清晰的 ImportError。
"""

from __future__ import annotations

import codecs
import logging
import os
import queue
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: shell 退出时追加到输出流的标记，前端/测试据此判断标签页已结束。
EXIT_MARKER = "\x00__SPARK_PTY_EXIT__\x00"

_IS_WINDOWS = os.name == "nt"

if _IS_WINDOWS:  # pragma: no cover - Windows branch
    try:
        from winpty import PtyProcess as _PtyProcess
    except ImportError:
        _PtyProcess = None  # type: ignore[assignment]
else:  # pragma: no cover - POSIX branch
    try:
        from ptyprocess import PtyProcess as _PtyProcess
    except ImportError:
        _PtyProcess = None  # type: ignore[assignment]


def _default_shell() -> str:
    """Interactive shell: $SHELL when it exists, else the first known-good one."""
    candidates = [os.environ.get("SHELL", "")]
    candidates += ["/bin/bash", "/usr/bin/bash", "/bin/zsh", "/bin/sh"]
    for shell in candidates:
        if shell and os.path.exists(shell):
            return shell
    return "/bin/sh"


class PtySession:
    """单个 (sid, tab) 的 shell 会话。"""

    def __init__(
        self,
        sid: str,
        tab_id: str,
        cwd: str | Path,
        rows: int = 24,
        cols: int = 80,
    ) -> None:
        if _PtyProcess is None:
            raise ImportError(
                "内置终端需要 pywinpty（Windows）或 ptyprocess（Linux/macOS）："
                "pip install 'spark-agent[pty]'。"
            )
        self.sid = sid
        self.tab_id = tab_id
        self.cwd = Path(cwd).resolve()
        self._rows = int(rows)
        self._cols = int(cols)
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._reader_thread: threading.Thread | None = None
        self._loop: Any = None
        self._queue: Any = None
        self._sub_lock = threading.Lock()
        self._subscribers: list[queue.Queue[str]] = []

        if _IS_WINDOWS:
            argv = [os.environ.get("COMSPEC", "cmd.exe")]
        else:
            argv = [_default_shell(), "-i"]
        self._proc = _PtyProcess.spawn(
            argv,
            cwd=str(self.cwd),
            dimensions=(self._rows, self._cols),
            env=os.environ.copy(),
        )
        logger.info("pty spawned pid=%s tab=%r cwd=%s", self._proc.pid, tab_id, self.cwd)

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    @property
    def alive(self) -> bool:
        with self._lock:
            if self._stop.is_set():
                return False
            try:
                return bool(self._proc.isalive())
            except Exception:  # noqa: BLE001
                return False

    @property
    def size(self) -> tuple[int, int]:
        return (self._rows, self._cols)

    # ------------------------------------------------------------------
    # 读侧：后台线程 -> asyncio.Queue / 订阅者队列
    # ------------------------------------------------------------------
    def start_reader(self, loop: Any, q: Any) -> None:
        """启动后台读线程，把 PTY 输出文本投递到 ``q``（asyncio.Queue）。

        ``loop`` 可以尚未运行；线程内统一用 ``call_soon_threadsafe`` 投递。
        重复调用不会重复起线程（绑定第一个 loop/queue）。
        """
        with self._lock:
            self._loop = loop
            self._queue = q
            self._ensure_reader_locked()

    def attach(self) -> queue.Queue[str]:
        """HTTP/SSE 订阅：返回一个阻塞队列，读出后续增量文本。

        若测试侧尚未 ``start_reader``，此处自动启动纯广播读线程
        （web 模式没有 asyncio loop）。
        """
        with self._lock:
            self._ensure_reader_locked()
        q: queue.Queue[str] = queue.Queue(maxsize=1024)
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def _ensure_reader_locked(self) -> None:
        if self._reader_thread is not None:
            return
        self._reader_thread = threading.Thread(
            target=self._read_loop, name=f"spark-pty-{self.tab_id}", daemon=True
        )
        self._reader_thread.start()

    def detach(self, q: queue.Queue[str]) -> None:
        with self._sub_lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _broadcast(self, text: str) -> None:
        if not text:
            return
        if self._loop is not None and self._queue is not None:
            try:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, text)
            except Exception:  # noqa: BLE001 - loop 已关闭等
                pass
        with self._sub_lock:
            for sub in list(self._subscribers):
                try:
                    sub.put_nowait(text)
                except queue.Full:
                    # 消费方来不及读：丢弃最旧一条再塞，避免读线程阻塞
                    try:
                        sub.get_nowait()
                        sub.put_nowait(text)
                    except Exception:  # noqa: BLE001
                        pass

    def _read_loop(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while not self._stop.is_set():
                try:
                    data = self._proc.read(4096)
                except EOFError:
                    break
                except Exception:  # noqa: BLE001
                    break
                if not data:
                    if self._stop.is_set():
                        break
                    continue
                # pywinpty returns str; ptyprocess returns bytes. Decode only
                # the POSIX backend so incremental UTF-8 handling stays correct.
                text = decoder.decode(data) if isinstance(data, (bytes, bytearray)) else str(data)
                self._broadcast(text)
        finally:
            try:
                tail = decoder.decode(b"", True)
            except Exception:  # noqa: BLE001
                tail = ""
            self._broadcast(tail)
            # shell 退出：无论正常 exit 还是被杀，都补发退出标记
            self._broadcast(EXIT_MARKER)
            logger.info("pty reader ended tab=%r", self.tab_id)

    # ------------------------------------------------------------------
    # 写侧
    # ------------------------------------------------------------------
    def write(self, data: str | bytes) -> bool:
        if not self.alive:
            return False
        try:
            payload: str | bytes
            if _IS_WINDOWS:
                payload = data if isinstance(data, str) else bytes(data).decode("utf-8", "replace")
            else:
                payload = data.encode("utf-8", "replace") if isinstance(data, str) else bytes(data)
            self._proc.write(payload)
            return True
        except Exception:  # noqa: BLE001
            return False

    def resize(self, rows: int, cols: int) -> bool:
        if not self.alive:
            return False
        rows = max(2, min(int(rows), 512))
        cols = max(8, min(int(cols), 512))
        try:
            self._proc.setwinsize(rows, cols)
            self._rows, self._cols = rows, cols
            return True
        except Exception:  # noqa: BLE001
            return False

    def close(self, *, kill: bool = False) -> None:
        """停止读线程并结束子进程。幂等。"""
        self._stop.set()
        try:
            if kill or self.alive:
                try:
                    self._proc.close(force=True)
                except TypeError:
                    self._proc.close()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            if self._reader_thread is not None:
                self._reader_thread.join(timeout=1.0)


class PtyManager:
    """按 ``(sid, tab_id)`` 管理多个 :class:`PtySession`。"""

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], PtySession] = {}
        self._lock = threading.RLock()

    def get_or_create(
        self, sid: str, tab_id: str, cwd: str | Path, *, rows: int = 24, cols: int = 80
    ) -> PtySession:
        key = (sid, tab_id)
        with self._lock:
            sess = self._sessions.get(key)
            if sess is not None and sess.alive:
                return sess
            if sess is not None and not sess.alive:
                # 上一个 shell 已退出：清理后重建
                sess.close()
                self._sessions.pop(key, None)
            sess = PtySession(sid, tab_id, cwd, rows=rows, cols=cols)
            self._sessions[key] = sess
            return sess

    def get(self, sid: str, tab_id: str) -> PtySession | None:
        with self._lock:
            return self._sessions.get((sid, tab_id))

    def write(self, sid: str, tab_id: str, data: str | bytes) -> bool:
        """向 tab 写入；tab 不存在或已关闭时返回 False（no-op，不抛错）。"""
        with self._lock:
            sess = self._sessions.get((sid, tab_id))
        if sess is None:
            return False
        return sess.write(data)

    def resize(self, sid: str, tab_id: str, rows: int, cols: int) -> bool:
        with self._lock:
            sess = self._sessions.get((sid, tab_id))
        if sess is None:
            return False
        return sess.resize(rows, cols)

    def close_tab(self, sid: str, tab_id: str) -> bool:
        with self._lock:
            sess = self._sessions.pop((sid, tab_id), None)
        if sess is None:
            return False
        sess.close()
        return True

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for sess in sessions:
            sess.close()

    def list_tabs(self, sid: str) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"tab_id": s.tab_id, "cwd": str(s.cwd), "alive": s.alive, "size": s.size}
                for (sid_k, _), s in self._sessions.items()
                if sid_k == sid
            ]


# ----------------------------------------------------------------------
# 进程级单例（web 层复用）
# ----------------------------------------------------------------------
_global_manager: PtyManager | None = None
_global_lock = threading.Lock()


def get_manager() -> PtyManager:
    global _global_manager
    with _global_lock:
        if _global_manager is None:
            _global_manager = PtyManager()
        return _global_manager


__all__ = ["EXIT_MARKER", "PtyManager", "PtySession", "get_manager"]
