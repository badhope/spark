"""A small, synchronous LSP client over stdio JSON-RPC.

Why a hand-written client instead of a framework:
- the transport is a header-framed JSON-RPC stream, not a general RPC runtime;
- it must be lazy (starting a language server is a code-execution approval);
- server-initiated requests such as ``workspace/applyEdit`` must be denied so a
  server cannot bypass Spark's own approval/sandbox write path.

The client is intentionally single-request-at-a-time from a tool's point of
view.  A reader thread owns stdout, pending requests are keyed by id, and
diagnostics are cached from ``textDocument/publishDiagnostics``.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_MESSAGE_BYTES = 16 * 1024 * 1024
_MAX_DIAGNOSTICS_PER_URI = 500


class LSPError(RuntimeError):
    """Server returned an error or the protocol stream failed."""


class LSPTimeout(LSPError):
    """A request did not complete within its deadline."""


@dataclass
class _Pending:
    event: threading.Event
    result: Any = None
    error: dict[str, Any] | None = None


def _read_message(proc: subprocess.Popen[bytes]) -> dict[str, Any] | None:
    """Read one Content-Length framed message from *proc*'s stdout."""
    assert proc.stdout is not None
    headers: dict[str, str] = {}
    while True:
        line = proc.stdout.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        if b":" not in line:
            continue
        key, _, value = line.partition(b":")
        headers[key.strip().lower().decode("ascii", "ignore")] = value.strip().decode(
            "ascii", "ignore"
        )
    try:
        length = int(headers.get("content-length", "0"))
    except ValueError:
        return None
    if length <= 0 or length > _MAX_MESSAGE_BYTES:
        return None
    body = proc.stdout.read(length)
    if body is None or len(body) != length:
        return None
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        logger.debug("lsp: invalid json from server")
        return None
    return payload if isinstance(payload, dict) else None


def _drain_stderr(proc: subprocess.Popen[bytes], lines: list[str]) -> None:
    assert proc.stderr is not None
    try:
        for raw in proc.stderr:
            text = raw.decode("utf-8", "replace").strip()
            if text:
                lines.append(text)
                del lines[:-20]
    except (OSError, ValueError):
        pass


class LSPClient:
    """One initialized language-server process."""

    def __init__(
        self,
        root: Path,
        command: tuple[str, ...] | list[str],
        language: str,
        *,
        name: str = "",
        request_timeout: float = 12.0,
        startup_timeout: float = 20.0,
    ) -> None:
        self.root = Path(root).resolve()
        self.command = tuple(command)
        self.language = language
        self.name = name or Path(self.command[0]).name
        self.request_timeout = max(1.0, float(request_timeout))
        self.startup_timeout = max(1.0, float(startup_timeout))
        self.capabilities: dict[str, Any] = {}
        self.server_info: dict[str, Any] = {}
        self.stderr: list[str] = []

        self._proc: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._pending: dict[int, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._diag_lock = threading.Lock()
        self._diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._diag_events: dict[str, threading.Event] = {}
        self._open_docs: dict[str, str] = {}
        self._doc_versions: dict[str, int] = {}
        self._next_id = 0
        self._alive = False

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._alive:
            return
        env = dict(os.environ)
        # Language servers should never inherit colour/REPL-only flags.
        env.setdefault("NO_COLOR", "1")
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - explicit trusted command
                self.command,
                cwd=str(self.root),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=False,
                bufsize=0,
            )
        except OSError as exc:
            raise LSPError(f"cannot start {self.name}: {exc}") from exc
        self._alive = True
        self._reader = threading.Thread(
            target=self._reader_loop, name="spark-lsp-reader", daemon=True
        )
        self._reader.start()
        self._stderr_reader = threading.Thread(
            target=_drain_stderr,
            args=(self._proc, self.stderr),
            name="spark-lsp-stderr",
            daemon=True,
        )
        self._stderr_reader.start()

        params = {
            "processId": os.getpid(),
            "clientInfo": {"name": "spark", "version": "0.15.2"},
            "rootUri": self.root.as_uri(),
            "workspaceFolders": [
                {"uri": self.root.as_uri(), "name": self.root.name or "workspace"}
            ],
            "capabilities": {
                "workspace": {
                    "workspaceFolders": True,
                    "configuration": True,
                    "applyEdit": False,
                },
                "textDocument": {
                    "definition": {"linkSupport": True},
                    "references": {},
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                    "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                    "rename": {"prepareSupport": True},
                    "publishDiagnostics": {"relatedInformation": True},
                    "codeAction": {
                        "codeActionLiteralSupport": {
                            "codeActionKind": {
                                "valueSet": [
                                    "",
                                    "quickfix",
                                    "refactor",
                                    "refactor.extract",
                                    "refactor.inline",
                                    "source",
                                    "source.organizeImports",
                                ]
                            }
                        }
                    },
                    "synchronization": {
                        "didSave": True,
                        "dynamicRegistration": False,
                    },
                },
                "window": {"workDoneProgress": True},
            },
        }
        result = self.request(
            "initialize", params, timeout=self.startup_timeout
        )
        if not isinstance(result, dict):
            raise LSPError(f"{self.name}: initialize returned no object")
        self.capabilities = result.get("capabilities") or {}
        server_info = result.get("serverInfo")
        self.server_info = server_info if isinstance(server_info, dict) else {}
        self.notify("initialized", {})

    def close(self) -> None:
        """Graceful shutdown, then bounded terminate/kill."""
        if not self._alive or self._proc is None:
            return
        proc = self._proc
        try:
            self.request("shutdown", None, timeout=2.0)
        except Exception:  # noqa: BLE001 - teardown must never raise
            pass
        try:
            self.notify("exit", {})
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
        self._alive = False
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        self._fail_pending(LSPError(f"{self.name}: client closed"))

    @property
    def alive(self) -> bool:
        return self._alive and self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------------
    # JSON-RPC
    # ------------------------------------------------------------------
    def _next_request_id(self) -> int:
        with self._pending_lock:
            self._next_id += 1
            return self._next_id

    def _send(self, message: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise LSPError(f"{self.name}: server is not running")
        body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        with self._write_lock:
            try:
                proc.stdin.write(header)
                proc.stdin.write(body)
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                self._alive = False
                raise LSPError(f"{self.name}: write failed: {exc}") from exc

    def request(
        self, method: str, params: Any = None, *, timeout: float | None = None
    ) -> Any:
        if not self.alive:
            raise LSPError(f"{self.name}: server is not running")
        rid = self._next_request_id()
        pending = _Pending(event=threading.Event())
        with self._pending_lock:
            self._pending[rid] = pending
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            message["params"] = params
        try:
            self._send(message)
            deadline = time.monotonic() + (
                self.request_timeout if timeout is None else max(0.1, timeout)
            )
            if not pending.event.wait(max(0.0, deadline - time.monotonic())):
                raise LSPTimeout(f"{self.name}: timeout waiting for {method}")
            if pending.error is not None:
                raise LSPError(
                    f"{self.name}: {pending.error.get('message') or pending.error}"
                )
            return pending.result
        finally:
            with self._pending_lock:
                self._pending.pop(rid, None)

    def notify(self, method: str, params: Any = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def _reader_loop(self) -> None:
        try:
            while self._alive and self._proc is not None:
                message = _read_message(self._proc)
                if message is None:
                    break
                self._dispatch(message)
        finally:
            self._alive = False
            self._fail_pending(LSPError(f"{self.name}: server exited"))

    def _fail_pending(self, error: Exception) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            item.error = {"message": str(error)}
            item.event.set()

    def _dispatch(self, message: dict[str, Any]) -> None:
        if "method" in message:
            if "id" in message:
                self._reply_server_request(message)
                return
            self._handle_notification(message)
            return
        rid = message.get("id")
        if rid is None:
            return
        with self._pending_lock:
            pending = self._pending.get(int(rid))
        if pending is None:
            return
        if "error" in message:
            pending.error = (
                message["error"] if isinstance(message["error"], dict) else {"message": str(message["error"])}
            )
        else:
            pending.result = message.get("result")
        pending.event.set()

    def _reply_server_request(self, message: dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        raw_params = message.get("params")
        params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
        if method == "workspace/configuration":
            items = params.get("items")
            result: Any = [{} for _ in items] if isinstance(items, list) else []
        elif method == "workspace/workspaceFolders":
            result = [
                {"uri": self.root.as_uri(), "name": self.root.name or "workspace"}
            ]
        elif method == "workspace/applyEdit":
            result = {
                "applied": False,
                "failureReason": "Spark applies edits through its own sandbox/approval path",
            }
        elif method in {
            "client/registerCapability",
            "client/unregisterCapability",
            "window/workDoneProgress/create",
            "window/showMessageRequest",
            "workspace/codeLens/refresh",
            "workspace/inlayHint/refresh",
            "workspace/semanticTokens/refresh",
        }:
            result = None
        else:
            logger.debug("lsp: unhandled server request %s", method)
            result = None
        try:
            self._send({"jsonrpc": "2.0", "id": message.get("id"), "result": result})
        except LSPError:
            pass

    def _handle_notification(self, message: dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        raw_params = message.get("params")
        params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
        if method != "textDocument/publishDiagnostics":
            return
        uri = str(params.get("uri") or "")
        diagnostics = params.get("diagnostics")
        if not uri or not isinstance(diagnostics, list):
            return
        with self._diag_lock:
            self._diagnostics[uri] = [
                item for item in diagnostics[:_MAX_DIAGNOSTICS_PER_URI] if isinstance(item, dict)
            ]
            self._diag_events.setdefault(uri, threading.Event()).set()

    # ------------------------------------------------------------------
    # documents and diagnostics
    # ------------------------------------------------------------------
    def open_document(self, path: Path) -> str:
        path = Path(path).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise LSPError(f"document is outside the workspace: {path}") from exc
        uri = path.as_uri()
        text = path.read_text(encoding="utf-8", errors="replace")
        previous = self._open_docs.get(uri)
        if previous is None:
            version = self._doc_versions.get(uri, 0) + 1
            self._doc_versions[uri] = version
            self.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": self.language,
                        "version": version,
                        "text": text,
                    }
                },
            )
        elif previous != text:
            # Full-document sync is safe across both full and incremental
            # servers when expressed as close+open; didChange's sync kind is
            # server-defined and may reject a full replacement.
            self.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
            version = self._doc_versions.get(uri, 0) + 1
            self._doc_versions[uri] = version
            self.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": self.language,
                        "version": version,
                        "text": text,
                    }
                },
            )
        self._open_docs[uri] = text
        return uri

    def diagnostics_for(self, uri: str, *, wait: float = 1.5) -> list[dict[str, Any]]:
        with self._diag_lock:
            if uri in self._diagnostics:
                return [dict(item) for item in self._diagnostics.get(uri, [])]
            event = self._diag_events.setdefault(uri, threading.Event())
        event.wait(max(0.0, wait))
        with self._diag_lock:
            return [dict(item) for item in self._diagnostics.get(uri, [])]

    def pull_diagnostics(self, uri: str) -> list[dict[str, Any]] | None:
        """Ask for diagnostics via LSP 3.17 pull diagnostics when supported.

        Returns ``None`` when the server did not advertise a diagnostic
        provider; callers then use the push cache.
        """
        provider = self.capabilities.get("diagnosticProvider")
        if not provider:
            return None
        result = self.request(
            "textDocument/diagnostic",
            {"textDocument": {"uri": uri}, "previousResultId": None},
        )
        if not isinstance(result, dict):
            return None
        kind = str(result.get("kind") or "")
        if kind == "unchanged":
            with self._diag_lock:
                return [dict(item) for item in self._diagnostics.get(uri, [])]
        items = result.get("items")
        if not isinstance(items, list):
            return []
        clean = [dict(item) for item in items[:_MAX_DIAGNOSTICS_PER_URI] if isinstance(item, dict)]
        with self._diag_lock:
            self._diagnostics[uri] = clean
            self._diag_events.setdefault(uri, threading.Event()).set()
        return clean

    def refresh_document(self, path: Path) -> None:
        """Re-open a document after Spark wrote it, so the next request sees it."""
        uri = path.resolve().as_uri()
        self._open_docs.pop(uri, None)
        self.open_document(path)


__all__ = ["LSPClient", "LSPError", "LSPTimeout"]
