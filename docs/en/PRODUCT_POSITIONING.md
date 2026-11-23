# Spark Product Positioning

> Revised 2026-11-23. This document supersedes every earlier "local-first" product description. Historical reviews keep their original wording but are marked as obsolete positioning.

## One line

**Spark is a browser-native, cross-platform, open-source AI coding and office workbench.**

One agent runtime runs across the browser, terminal, desktop shell, IDE and API. Models, tools, Skills, MCP servers, themes, workflows and deployment targets are replaceable and self-hostable.

## Three pillars

1. **Browser-native, not tied to one platform.** The Web UI is the primary surface and targets modern browsers; current UI regression is Chromium/Edge-first, while Firefox/Safari still need a cross-browser gate. The desktop shell, CLI/TUI, VS Code, JetBrains, Neovim, mobile layout and A2A API share the same backend. Current boundary: the Web UI is browser-native, and agent browser execution covers Chromium/Chrome/Edge/Firefox/WebKit, persistent profiles, CDP attachment, per-domain rules, sensitive-site confirmation and performance traces. The extension bridge is an official Playwright MCP `--extension` preset; users install the browser extension and enable it explicitly. We do not claim it is bundled native capability.
2. **A browser capability layer, not one `browser_*` tool.** Target: Chromium/Chrome/Edge/Firefox/WebKit engines, local and remote sessions, extension bridge, logged-in profiles, downloads/uploads, screenshots/PDF, console/network, performance traces and per-domain permissions. Current: 24 Playwright tools across five engines, persistent profiles, CDP attachment, per-domain rules, sensitive-site confirmation, traces, Chromium metrics and a web/reading panel in the workbench. The extension bridge is the official Playwright MCP `--extension` preset plus an acceptance guide.
3. **Open and deeply customizable.** MIT licensed; replaceable providers, tools, MCP servers, Skills, slash commands, plugins, themes and YAML workflows; MCP/ACP/A2A protocols; auditable approval, sandbox, checkpoints, cost ledger and OTel.

## What it is not

- Not a "local-first" product. Local storage is a deployment fact, not the identity.
- Not a single IDE plugin.
- Not a closed cloud platform that requires an account.
- Not "all browser capabilities are already done". The current boundary is 24 `browser_*` tools, five engines, persistent profiles, CDP attach, per-domain rules, sensitive-site confirmation, traces and Chromium metrics. The extension bridge ships as an official Playwright MCP `--extension` preset plus an acceptance guide, not as a bundled native extension.

Recommended wording: **A browser-native, cross-platform, open-source AI coding and office workbench.**
