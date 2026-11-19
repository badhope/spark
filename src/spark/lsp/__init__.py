"""Language Server Protocol support.

The package is deliberately split from ``spark.tools``: the client owns the
process/protocol lifecycle, while tool adapters only translate LSP results into
the agent's structured payloads.  This keeps a language server from becoming a
second, hidden execution channel inside a read-only tool.
"""

from spark.lsp.client import LSPClient, LSPError, LSPTimeout
from spark.lsp.manager import LSPManager, LSPUnavailable
from spark.lsp.servers import ServerSpec, detect_server, language_for_path

__all__ = [
    "LSPClient",
    "LSPError",
    "LSPManager",
    "LSPTimeout",
    "LSPUnavailable",
    "ServerSpec",
    "detect_server",
    "language_for_path",
]
