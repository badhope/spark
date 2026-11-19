> **English** | [中文](README_zh.md)

# Spark

**Local-first AI coding agent and office workbench, with guardrails you can audit.**

Spark gives you one agent loop across the terminal, browser, desktop, IDE and API. Every write is gated by approval and a sandbox, every turn can be rolled back, and the model layer works with any OpenAI-compatible endpoint.

```bash
# Linux / macOS — SHA256-verified installer
curl -fsSL https://raw.githubusercontent.com/badhope/spark/main/scripts/install.sh | sh

# Windows (PowerShell)
irm https://raw.githubusercontent.com/badhope/spark/main/scripts/install.ps1 | iex

spark init    # connect any OpenAI-compatible endpoint
spark web     # open the browser UI
```

No API key? Spark starts in demo mode with a local mock model. Add a key in settings and the next turn uses the real model without a restart.

![Spark chat with reasoning, tool calls and a live context meter](assets/screenshots/11-chat.png)

## What it does

- **Auditable execution.** Approval modes and sandbox levels are enforced before a write or command runs. Wildcard rules are scoped to user, project and local config; `deny` always wins. Protected credential paths and shell token checks stay on.
- **Recoverable by design.** Each turn snapshots the workspace and can restore files and conversation together, including outside git repositories.
- **Task-as-branch.** Start a task, commit each verified step, and finish on green tests; a failed run abandons its branch instead of leaking changes.
- **Built for real files.** The office workbench turns documents, meeting recordings and spreadsheets into cited minutes, action items, reports and exports. Local OCR and transcription keep the source on your machine.
- **Provider freedom.** Any OpenAI-compatible endpoint, fallback model chain and credential pool, plus a separate small model for auxiliary calls.

## Run it

```bash
spark                         # terminal TUI
spark web                     # browser UI on loopback
spark desktop                 # native window, loopback only
spark exec "task" --json      # NDJSON events for scripts and CI
spark review                  # inspect the uncommitted diff
spark doctor                  # check config, credentials and runtime
```

The office workbench runs through the enterprise control plane:

```bash
spark enterprise --host 127.0.0.1 --port 18800
# http://127.0.0.1:18800/office
```

`spark exec` emits one JSON object per line and ends with `{"type":"final","text":"…"}`, so it pipes directly into `jq`.

## Interfaces

| Interface | Command | Use it for |
|---|---|---|
| Web UI | `spark web` | Chat, files, terminal, execution graph |
| Enterprise API | `spark enterprise` | `/api/v1` control plane and `/office` |
| Desktop | `spark desktop` | Native window, loopback only |
| TUI / CLI | `spark` · `spark exec --json` | Terminal work and automation |
| IDE / ACP | `spark acp` · `spark ide install` | Zed, JetBrains, Neovim |
| MCP / A2A | `spark mcp-serve` · `spark a2a-serve` | Tool and agent-to-agent integration |

## Safety model

Three layers narrow each other:

1. **Approval** — from plan-only to full-auto. The default asks before writes and commands.
2. **Sandbox** — workspace isolation by default; escapes and protected paths are refused.
3. **Rules** — tool and resource matching across user, project and local scopes. A cloned repository can tighten permissions, never grant itself more.

OS-level isolation is used where available: bwrap on Linux, Job Objects on Windows, `sandbox-exec` on macOS. When a platform cannot enforce a control, the tool result says so instead of pretending.

## Documentation

| Doc | 中文 |
|---|---|
| [Install & build](docs/en/INSTALL.md) | [安装与打包](docs/INSTALL.md) |
| [Configuration](docs/en/CONFIG.md) | [配置](docs/CONFIG.md) |
| [HTTP API](docs/en/API.md) | [API](docs/API.md) |
| [Architecture](docs/en/ARCHITECTURE.md) | [架构](docs/ARCHITECTURE.md) |
| [Features](docs/FEATURES.md) | [功能总览](docs/FEATURES.md) |
| [Enterprise control plane](docs/ENTERPRISE.md) | [企业控制面](docs/ENTERPRISE.md) |
| [A2A protocol](docs/A2A.md) | [A2A](docs/A2A.md) |
| [ACP / IDE setup](docs/ACP.md) | [IDE 接入](docs/ACP.md) |

More: [docs index](docs/README.md) · [English docs](docs/en/README.md) · [VS Code extension](vscode-spark/README.md) · [audit notes](docs/reviews/).

## Verify locally

```bash
pytest -q
ruff check src tests scripts
mypy src/spark --ignore-missing-imports
```

CI also runs a Windows checkout check, frontend build drift, packaging checks, a secret scan, an OpenAPI snapshot check and a mock-mode agent eval.

## Requirements

Python 3.11+ (or the bundled executable), Node.js 18+ only for frontend development, Windows / macOS / Linux.

---

**License:** MIT · **Author:** badhope
