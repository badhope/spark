"""Per-registry lazy LSP manager.

One manager is attached to each :class:`spark.tools.registry.ToolRegistry`.
Language servers are only started when an LSP tool is actually called, so a
normal chat turn never pays the process/startup cost.  Missing servers are
reported as :class:`LSPUnavailable` with an installation hint; the tool layer
falls back to tree-sitter where possible.
"""

from __future__ import annotations

import atexit
import logging
import threading
from pathlib import Path

from spark.lsp.client import LSPClient, LSPError
from spark.lsp.servers import (
    ServerSpec,
    detect_server,
    install_hint,
    language_for_path,
)

logger = logging.getLogger(__name__)


class LSPUnavailable(RuntimeError):
    """No trusted server is installed/available for a language."""

    def __init__(self, language: str, root: Path) -> None:
        self.language = language
        self.root = root
        super().__init__(
            f"no LSP server for {language or 'this file'} is available; "
            f"install one: {install_hint(language)}"
        )


class LSPManager:
    def __init__(
        self,
        root: Path,
        *,
        request_timeout: float = 12.0,
        startup_timeout: float = 20.0,
        overrides: dict[str, list[str] | tuple[str, ...]] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.request_timeout = request_timeout
        self.startup_timeout = startup_timeout
        self.overrides = dict(overrides or {})
        self._clients: dict[str, LSPClient] = {}
        self._lock = threading.RLock()
        self._closed = False
        atexit.register(self.close)

    def detect(self, path: str | Path) -> ServerSpec | None:
        language = language_for_path(path)
        if not language:
            return None
        return detect_server(language, self.root, overrides=self.overrides)

    def _client_key(self, spec: ServerSpec) -> str:
        return f"{spec.language}\0{' '.join(spec.command)}"

    def ensure_client(
        self, path: str | Path, *, start: bool = True
    ) -> LSPClient:
        """Return a live client for *path*, starting one if needed."""
        language = language_for_path(path)
        if not language:
            raise LSPUnavailable("", self.root)
        spec = detect_server(language, self.root, overrides=self.overrides)
        if spec is None:
            raise LSPUnavailable(language, self.root)
        key = self._client_key(spec)
        with self._lock:
            if self._closed:
                raise LSPError("LSP manager is closed")
            client = self._clients.get(key)
            if client is not None and client.alive:
                return client
            if client is not None:
                self._clients.pop(key, None)
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    logger.debug("lsp old client close failed", exc_info=True)
            if not start:
                raise LSPError(f"{spec.name} is not running")
            client = LSPClient(
                self.root,
                spec.command,
                spec.language,
                name=spec.name,
                request_timeout=self.request_timeout,
                startup_timeout=self.startup_timeout,
            )
            try:
                client.start()
            except Exception:
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass
                raise
            self._clients[key] = client
            logger.info(
                "lsp_started language=%s server=%s root=%s",
                spec.language,
                spec.name,
                self.root,
            )
            return client

    def running_client(self, path: str | Path) -> LSPClient | None:
        """Return an already-approved/live client for *path*, without starting one.

        Post-write diagnostics uses this so an ordinary edit never launches a
        language-server child process behind the approval gate.
        """
        language = language_for_path(path)
        if not language:
            return None
        spec = detect_server(language, self.root, overrides=self.overrides)
        if spec is None:
            return None
        key = self._client_key(spec)
        with self._lock:
            client = self._clients.get(key)
            return client if client is not None and client.alive else None

    def info(self, path: str | Path) -> dict[str, object]:
        """Return non-starting diagnostic info for the status/UI surfaces."""
        language = language_for_path(path)
        spec = self.detect(path)
        key = self._client_key(spec) if spec else ""
        client = self._clients.get(key) if key else None
        return {
            "language": language,
            "available": spec is not None,
            "server": spec.name if spec else "",
            "running": bool(client and client.alive),
            "install_hint": install_hint(language) if language and not spec else "",
        }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                logger.debug("lsp client close failed", exc_info=True)


__all__ = ["LSPManager", "LSPUnavailable"]
