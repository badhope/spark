> 🌐 **English** | [中文](README_zh.md)

# Spark

**Local-first AI coding agent and office workbench — with guardrails you can audit.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-brightgreen.svg)](https://www.python.org/)
[![Node 18+](https://img.shields.io/badge/node-18%2B-339933.svg)](https://nodejs.org/)
[![CI](https://github.com/badhope/spark/actions/workflows/ci.yml/badge.svg)](https://github.com/badhope/spark/actions/workflows/ci.yml)
[![Security](https://github.com/badhope/spark/actions/workflows/security.yml/badge.svg)](https://github.com/badhope/spark/actions/workflows/security.yml)

One agent loop, eight surfaces, one permission model. Spark runs coding tasks,
office workflows and an enterprise control plane on the same auditable core:
approval before writes, sandboxed execution, checkpoint rollback, and a provider
layer that works with any OpenAI-compatible endpoint.

```bash
# Linux / macOS — SHA256-verified
curl -fsSL https://raw.githubusercontent.com/badhope/spark/main/scripts/install.sh | sh

# Windows (PowerShell)
irm https://raw.githubusercontent.com/badhope/spark/main/scripts/install.ps1 | iex

spark init    # point it at any OpenAI-compatible endpoint
spark web     # browser UI (auto-picks a free port)
```

No API key? Spark starts in **demo mode** with a scripted mock model. Add a key
in settings and the next turn uses the real model — no restart.

<p align="center">
  <img src="assets/screenshots/11-chat.png" alt="Spark chat with reasoning, tool calls and a live context meter" width="100%">
</p>

## Why Spark

- **Auditable execution.** Six approval modes × four sandbox levels. Wildcard
  rules across user/project/local scopes, where `deny` always wins. Protected
  credential paths and token-level shell checks.
- **Recoverable by design.** Every turn snapshots the workspace (tar.gz +
  manifest verification). Roll back files and conversation together — also in
  non-git directories.
- **Task-as-branch.** `task_begin` → `task_step_commit` → `task_finish`. Green
  tests gate each commit; a red run abandons the branch.
- **Enterprise control plane.** `/api/v1` with tenants, projects, tasks,
  artifacts and audit. SQLite for local development; PostgreSQL, Keycloak OIDC
  and Casbin for production.
- **Office workbench with real files.** `/office` turns meeting transcripts and
  documents into cited minutes, action items, reports and spreadsheets, then
  exports Markdown, Word, Excel, PowerPoint and PDF. Import docx/xlsx/pptx/pdf,
  email, images and scanned PDFs (local OCR); optional AI summaries when a
  model is configured.
- **Provider freedom.** Any OpenAI-compatible endpoint, fallback model chain,
  credential pool and a separate small model for auxiliary calls.

## Quick start

```bash
spark                         # terminal TUI
spark web                     # browser UI on 127.0.0.1
spark desktop                 # native window, loopback only
spark enterprise              # enterprise /api/v1 control plane
spark exec "task" --json      # NDJSON event stream for scripts and CI
spark review                  # audit the uncommitted diff
spark wiki --refresh          # regenerate the local code wiki
spark doctor                  # diagnose config, credentials and runtime
```

The office workbench is served by the enterprise control plane:

```bash
spark enterprise --host 127.0.0.1 --port 18800
# http://127.0.0.1:18800/office
```

`spark exec` streams one JSON object per line and ends with
`{"type":"final","text":"…"}`, so it pipes straight into `jq`.

## Eight surfaces

| Form | Command | Use case |
|---|---|---|
| Web UI | `spark web` | Chat, file viewer, terminal, execution graph |
| Enterprise API | `spark enterprise` | `/api/v1` control plane and `/office` |
| Desktop | `spark desktop` | Native pywebview window, loopback only |
| TUI | `spark` | Pure terminal / SSH |
| CLI | `spark exec "task" --json` | Automation, CI, piping |
| IDE (ACP) | `spark acp` | Zed and other ACP clients — [docs/ACP.md](docs/ACP.md) |
| MCP server | `spark mcp-serve` | Expose built-in tools to other MCP clients |
| A2A server | `spark a2a-serve` | Agent-to-agent delegation — [docs/A2A.md](docs/A2A.md) |

## Guardrails

Three layers, each narrowing the previous one:

1. **Approval** — from `plan-only` (plan, change nothing) to `full-auto`.
   The default `suggest` asks before any write or command.
2. **Sandbox** — `sandbox-only` / `workspace` (default) / `full-access` /
   `unrestricted`. Path escapes and protected paths are refused outright.
3. **Rules** — `[[permission_rules]]` match tool + resource across three
   scopes. A cloned repository can tighten permissions, never grant itself more.

OS-level isolation is layered on top: bwrap on Linux, a Job Object on Windows
(process-tree kill-on-close plus a memory ceiling), `sandbox-exec` on macOS.
Where a platform cannot enforce something, the tool result says so instead of
pretending.

## Verified locally

```bash
pytest -q                                  # 1493 passed, 3 skipped
ruff check src tests scripts
mypy src/spark --ignore-missing-imports
bandit -r src/spark -ll -ii -c bandit.yaml
lint-imports                               # architecture contracts
```

CI additionally runs secret scanning, an OpenAPI snapshot freshness check, a
coverage floor, a Windows-reserved-path check, packaging checks, and a
mock-mode agent eval suite on Linux and Windows.

## Documentation

| Document | Contents |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | Install, package, desktop build, auto-update, uninstall |
| [docs/CONFIG.md](docs/CONFIG.md) | Every config key and environment variable |
| [docs/API.md](docs/API.md) | HTTP API and SSE event protocol |
| [docs/ENTERPRISE.md](docs/ENTERPRISE.md) | `/api/v1`, `/office`, PostgreSQL, Keycloak, Casbin, Alembic |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layering and module boundaries |
| [docs/FEATURES.md](docs/FEATURES.md) | Feature-by-feature implementation map |
| [docs/A2A.md](docs/A2A.md) | A2A agent card, streaming, task lifecycle |
| [docs/competitive-comparison.md](docs/competitive-comparison.md) | How it compares to Qoder / ZCode / opencode / Codex CLI |
| [docs/reviews/](docs/reviews/) | Audit reports with findings, fixes and honest limits |
| [vscode-spark/](vscode-spark/) | VS Code extension |

## Platform requirements

- Python 3.11+ — or the bundled executable, no Python needed
- Node.js 18+ for frontend development only (release builds ship compiled assets)
- Windows / macOS / Linux

---

**License:** MIT · **Author:** badhope
