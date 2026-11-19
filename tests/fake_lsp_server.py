#!/usr/bin/env python3
"""Minimal fake LSP server used by the W1 regression tests.

It speaks real Content-Length framed JSON-RPC over stdio and returns deterministic
locations/edits for two files: ``main.py`` and ``other.py``.  It is deliberately
not a language server implementation; it exists to verify Spark's client and
write path without requiring pyright/gopls/etc. on CI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse


def read_message() -> dict | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, _, value = line.partition(b":")
        headers[key.strip().lower().decode("ascii")] = value.strip().decode("ascii")
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode("utf-8"))


def send(payload: dict) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def file_path(uri: str) -> Path:
    parsed = urlparse(uri)
    return Path(unquote(parsed.path))


def range_at(path: Path, line: int = 0, start: int = 0, end: int = 3) -> dict:
    return {
        "start": {"line": line, "character": start},
        "end": {"line": line, "character": end},
    }


def main() -> None:
    root: Path | None = None
    while True:
        message = read_message()
        if message is None:
            return
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            root = file_path(str((message.get("params") or {}).get("rootUri") or ""))
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "capabilities": {
                            "textDocumentSync": 1,
                            "definitionProvider": True,
                            "referencesProvider": True,
                            "hoverProvider": True,
                            "documentSymbolProvider": True,
                            "renameProvider": {"prepareProvider": True},
                            "codeActionProvider": True,
                            "diagnosticProvider": {"interleave": False},
                        },
                        "serverInfo": {"name": "fake-lsp", "version": "1.0"},
                    },
                }
            )
        elif method == "initialized":
            continue
        elif method == "shutdown":
            send({"jsonrpc": "2.0", "id": request_id, "result": None})
        elif method == "exit":
            return
        elif method == "textDocument/didOpen":
            uri = (message.get("params") or {}).get("textDocument", {}).get("uri", "")
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "textDocument/publishDiagnostics",
                    "params": {
                        "uri": uri,
                        "diagnostics": [
                            {
                                "range": range_at(Path("main.py")),
                                "severity": 2,
                                "code": "FAKE001",
                                "source": "fake-lsp",
                                "message": "fake diagnostic",
                            }
                        ],
                    },
                }
            )
        elif method == "textDocument/definition":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "uri": (root / "main.py").as_uri() if root else "",
                        "range": range_at(root / "main.py" if root else Path("x")),
                    },
                }
            )
        elif method == "textDocument/references":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": [
                        {
                            "uri": (root / "main.py").as_uri() if root else "",
                            "range": range_at(root / "main.py" if root else Path("x"), 1, 0, 3),
                        },
                        {
                            "uri": (root / "other.py").as_uri() if root else "",
                            "range": range_at(root / "other.py" if root else Path("x"), 0, 0, 3),
                        },
                    ],
                }
            )
        elif method == "textDocument/hover":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "contents": {"kind": "markdown", "value": "```python\nfoo()\n```"},
                        "range": range_at(root / "main.py" if root else Path("x")),
                    },
                }
            )
        elif method == "textDocument/documentSymbol":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": [
                        {
                            "name": "foo",
                            "kind": 12,
                            "range": range_at(root / "main.py" if root else Path("x")),
                            "selectionRange": range_at(root / "main.py" if root else Path("x")),
                        }
                    ],
                }
            )
        elif method == "workspace/symbol":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": [
                        {
                            "name": "foo",
                            "kind": 12,
                            "location": {
                                "uri": (root / "other.py").as_uri() if root else "",
                                "range": range_at(root / "other.py" if root else Path("x")),
                            },
                        }
                    ],
                }
            )
        elif method == "textDocument/prepareRename":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"range": range_at(root / "main.py" if root else Path("x"))},
                }
            )
        elif method == "textDocument/rename":
            new_name = str((message.get("params") or {}).get("newName") or "renamed")
            edits = [
                {
                    "uri": (root / "main.py").as_uri() if root else "",
                    "range": range_at(root / "main.py" if root else Path("x")),
                },
                {
                    "uri": (root / "other.py").as_uri() if root else "",
                    "range": range_at(root / "other.py" if root else Path("x")),
                },
            ]
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "changes": {
                            item["uri"]: [
                                {
                                    "range": item["range"],
                                    "newText": new_name,
                                }
                            ]
                            for item in edits
                        }
                    },
                }
            )
        elif method == "textDocument/diagnostic":
            uri = (message.get("params") or {}).get("textDocument", {}).get("uri", "")
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "kind": "full",
                        "items": [
                            {
                                "range": range_at(root / "main.py" if root else Path("x")),
                                "severity": 1,
                                "code": "FAKE002",
                                "source": "fake-lsp",
                                "message": "fake error",
                            }
                        ],
                        "uri": uri,
                    },
                }
            )
        elif method == "textDocument/codeAction":
            edit_uri = (root / "main.py").as_uri() if root else ""
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": [
                        {
                            "title": "Replace foo",
                            "kind": "quickfix",
                            "isPreferred": True,
                            "edit": {
                                "changes": {
                                    edit_uri: [
                                        {
                                            "range": range_at(root / "main.py" if root else Path("x")),
                                            "newText": "fixed",
                                        }
                                    ]
                                }
                            },
                        },
                        {
                            "title": "Run server command",
                            "kind": "source",
                            "command": {"title": "run", "command": "fake.command"},
                        },
                    ],
                }
            )
        elif request_id is not None:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"unknown method: {method}"},
                }
            )


if __name__ == "__main__":
    main()
