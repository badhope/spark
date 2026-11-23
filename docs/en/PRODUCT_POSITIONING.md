# Spark Product Positioning

> Revised 2026-11-23. This document supersedes every earlier "local-first" product description. Historical reviews keep their original wording but are marked as obsolete positioning.

## One line

**Spark is a browser-native, cross-platform, open-source AI coding and office workbench.**

One agent runtime runs across the browser, terminal, desktop shell, IDE and API. Models, tools, Skills, MCP servers, themes, workflows and deployment targets are replaceable and self-hostable.

## Three pillars

1. **Browser-native, not tied to one platform.** The Web UI is the primary surface and targets modern browsers; current UI regression is Chromium/Edge-first, while Firefox/Safari still need a cross-browser gate. The desktop shell, CLI/TUI, VS Code, JetBrains, Neovim, mobile layout and A2A API share the same backend. Current boundary: the Web UI is browser-native, while agent browser automation is still Playwright Chromium; a browser capability abstraction is the next core milestone, not a finished claim.
2. **A browser capability layer, not one `browser_*` tool.** Target: Chromium/Chrome/Edge/Firefox/WebKit engines, local and remote sessions, extension bridge, logged-in profiles, downloads/uploads, screenshots/PDF, console/network, performance traces and per-domain permissions. Current: 21 Playwright Chromium tools plus a web/reading panel in the workbench.
3. **Open and deeply customizable.** MIT licensed; replaceable providers, tools, MCP servers, Skills, slash commands, plugins, themes and YAML workflows; MCP/ACP/A2A protocols; auditable approval, sandbox, checkpoints, cost ledger and OTel.

## What it is not

- Not a "local-first" product. Local storage is a deployment fact, not the identity.
- Not a single IDE plugin.
- Not a closed cloud platform that requires an account.
- Not "all browser capabilities are already done".

Recommended wording: **A browser-native, cross-platform, open-source AI coding and office workbench.**
