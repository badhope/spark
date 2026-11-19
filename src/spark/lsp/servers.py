"""Language-server discovery.

Security boundary: only executables already installed on ``PATH`` are eligible.
The repository under analysis is never allowed to supply a server binary (no
``node_modules/.bin`` and no ``npx``), mirroring the existing diagnostics tool's
rule.  LSP tools are classified as execute tools, so the user still sees and
approves the first server launch.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

#: Extension -> LSP language id.  ``tsx`` intentionally maps to
#: ``typescriptreact`` so the TypeScript server uses its JSX parser.
_EXT_LANG = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".java": "java",
    ".rb": "ruby",
    ".lua": "lua",
}

#: Candidate server commands, in preference order.  The first token is looked
#: up with :func:`shutil.which`; arguments are passed verbatim.
_SERVER_CANDIDATES: dict[str, tuple[tuple[str, ...], ...]] = {
    "python": (("pyright-langserver", "--stdio"), ("pylsp",)),
    "typescript": (("typescript-language-server", "--stdio"),),
    "typescriptreact": (("typescript-language-server", "--stdio"),),
    "javascript": (("typescript-language-server", "--stdio"),),
    "javascriptreact": (("typescript-language-server", "--stdio"),),
    "go": (("gopls", "serve"),),
    "rust": (("rust-analyzer",),),
    "c": (("clangd",),),
    "cpp": (("clangd",),),
    "java": (("jdtls",),),
    "ruby": (("solargraph", "stdio"),),
    "lua": (("lua-language-server",),),
}

_DISPLAY_NAMES = {
    "python": "Pyright / pylsp",
    "typescript": "typescript-language-server",
    "typescriptreact": "typescript-language-server",
    "javascript": "typescript-language-server",
    "javascriptreact": "typescript-language-server",
    "go": "gopls",
    "rust": "rust-analyzer",
    "c": "clangd",
    "cpp": "clangd",
    "java": "jdtls",
    "ruby": "solargraph",
    "lua": "lua-language-server",
}


@dataclass(frozen=True)
class ServerSpec:
    """A detected, trusted language-server command."""

    language: str
    command: tuple[str, ...]
    name: str

    def as_dict(self) -> dict[str, str]:
        return {
            "language": self.language,
            "server": self.name,
            "command": " ".join(self.command),
        }


def language_for_path(path: str | Path) -> str:
    """Return the LSP language id for *path*, or ``""`` when unsupported."""
    suffix = Path(path).suffix.lower()
    return _EXT_LANG.get(suffix, "")


def supported_languages() -> list[str]:
    return sorted(_SERVER_CANDIDATES)


def _which(command: str, root: Path | None = None) -> str | None:
    found = shutil.which(command)
    if not found:
        return None
    # ``shutil.which`` may return a relative hit if PATH contains ".".  Resolve
    # it, but never accept a hit under the analysed repository: a repo must not
    # be able to ship the binary that the coding agent then executes.
    resolved = Path(found).resolve()
    if root is not None:
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            pass
        else:
            logger.warning("refusing repository-local LSP executable: %s", resolved)
            return None
    return str(resolved)


def _override_command(
    command: Iterable[str], root: Path
) -> tuple[str, ...] | None:
    parts = tuple(str(part) for part in command if str(part).strip())
    if not parts:
        return None
    executable = parts[0]
    if Path(executable).is_absolute():
        try:
            resolved = Path(executable).resolve()
            resolved.relative_to(root.resolve())
        except ValueError:
            pass
        except OSError:
            return None
        else:
            # A test/override may intentionally use a workspace-local fake
            # server; it is never reached through automatic detection.
            pass
        return (str(resolved), *parts[1:])
    found = _which(executable, root)
    if not found:
        return None
    return (found, *parts[1:])


def detect_server(
    language: str,
    root: Path,
    *,
    overrides: dict[str, list[str] | tuple[str, ...]] | None = None,
) -> ServerSpec | None:
    """Detect a trusted server for *language*.

    ``overrides`` is an explicit opt-in used by tests and embedders.  It is not
    populated from the repository or from an untrusted tool argument.
    """
    lang = (language or "").strip()
    if not lang:
        return None
    if overrides and lang in overrides:
        command = _override_command(overrides[lang], root)
        if command:
            return ServerSpec(
                language=lang,
                command=command,
                name=f"override:{Path(command[0]).name}",
            )
        logger.warning("lsp override for %s is not executable", lang)
        return None

    for candidate in _SERVER_CANDIDATES.get(lang, ()):
        executable = _which(candidate[0], root)
        if executable:
            return ServerSpec(
                language=lang,
                command=(executable, *candidate[1:]),
                name=_DISPLAY_NAMES.get(lang, candidate[0]),
            )
    return None


def install_hint(language: str) -> str:
    names = {
        "python": "pyright (npm i -g pyright) or pip install python-lsp-server",
        "typescript": "npm i -g typescript-language-server typescript",
        "typescriptreact": "npm i -g typescript-language-server typescript",
        "javascript": "npm i -g typescript-language-server typescript",
        "javascriptreact": "npm i -g typescript-language-server typescript",
        "go": "go install golang.org/x/tools/gopls@latest",
        "rust": "rustup component add rust-analyzer",
        "c": "install clangd (LLVM)",
        "cpp": "install clangd (LLVM)",
        "java": "install Eclipse JDT Language Server",
        "ruby": "gem install solargraph",
        "lua": "install lua-language-server",
    }
    return names.get(language, f"install a language server for {language} on PATH")


__all__ = [
    "ServerSpec",
    "detect_server",
    "install_hint",
    "language_for_path",
    "supported_languages",
]
