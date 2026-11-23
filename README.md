> **English** | [中文](README_zh.md)

# Spark

**A browser-native, cross-platform, open-source AI coding and office workbench.**

Spark gives you one agent loop across the browser, terminal, desktop, IDE, mobile layout and API. Every write is gated by approval and a sandbox, every turn can be rolled back, and models, tools, Skills, MCP servers and UI are replaceable. Code and ledgers live in the workspace you choose by default.

```bash
# Linux / macOS — SHA256-verified installer
curl -fsSL https://raw.githubusercontent.com/badhope/spark/main/scripts/install.sh | sh

# Windows (PowerShell)
irm https://raw.githubusercontent.com/badhope/spark/main/scripts/install.ps1 | iex

spark init    # connect any OpenAI-compatible endpoint
spark web     # open the browser UI
```

No API key? Spark still starts, but reports the real configuration error and refuses to fake an answer. Settings has an explicit **Mock demo mode** you can switch in and out of with one click; live-mode errors (missing key, model not downloaded, unreachable endpoint) are shown as they are.

![Spark chat with reasoning, tool calls and a live context meter](assets/screenshots/11-chat.png)

## What it does

- **The browser is an execution surface.** The Web UI, desktop shell, CLI/TUI, IDE, mobile layout and API share one agent loop, so browser actions, approvals, rollback and ledgers do not split into separate stacks. Browser automation runs on Playwright with Chromium / Chrome / Edge / Firefox / WebKit engines, persistent profiles for logged-in sessions, and CDP attachment to an already-running browser. Its 24 `browser_*` tools cover snapshots, interaction, tabs, downloads, PDF, console, network, traces and Chromium metrics; per-domain `allow/read/prompt/deny` rules gate writes, with a second confirmation on sensitive sites. Reusing a signed-in tab goes through the official Playwright MCP `--extension` preset; see `docs/BROWSER_EXTENSION_BRIDGE.md`.
- **Every layer is replaceable.** MIT licensed; providers, tools, MCP servers, Skills, slash commands, plugins, themes and workflows can all be swapped, and the stack can be self-hosted.
- **Auditable execution.** Approval modes and sandbox levels are enforced before a write or command runs. Wildcard rules are scoped to user, project and local config; `deny` always wins. Protected credential paths and shell token checks stay on.
- **Recoverable by design.** Each turn snapshots the workspace and can restore files and conversation together, including outside git repositories.
- **Task-as-branch.** Start a task, commit each verified step, and finish on green tests; a failed run abandons its branch instead of leaking changes.
- **Built for real files.** The office workbench turns documents, meeting recordings and spreadsheets into cited minutes, action items, reports and exports. Local OCR and transcription keep the source on your machine.
- **Provider freedom.** Any OpenAI-compatible endpoint, fallback model chain and credential pool, plus a separate small model for auxiliary calls.

## Run it

```bash
spark                         # terminal TUI
spark web                     # browser UI on loopback
spark web --host 0.0.0.0      # LAN; Settings → Remote generates a QR link
spark web --public-url https://spark.example.com  # behind your HTTPS reverse proxy
spark desktop                 # native window, loopback only
spark exec "task" --json      # NDJSON events for scripts and CI
spark review                  # inspect the uncommitted diff
spark doctor                  # check config, credentials and runtime
spark browser-doctor          # launch the configured engine and render a test page
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

## Remote control

Open **Settings → System → Remote control** and generate a one-time link and QR code. The phone joins the same workspace; files, commands, model calls and approvals still run on the desktop. Same Wi-Fi works with `spark web --host 0.0.0.0`; public access uses `spark web --public-url https://your-domain` behind your own HTTPS reverse proxy.

The phone can chat, approve, cancel, switch sessions and read files/diffs. Settings, plugins and direct shell/PTY stay local. Pairing codes expire after 5 minutes; each device gets its own 7-day token that can be revoked independently. Paired pages show live presence and a local-only audit trail of remote writes; an HTTPS or localhost page can enable Web Push so a locked phone still receives approval alerts.

The phone gets its own home instead of a shrunken desktop: at 390×844 the first screen shows pending approvals, the current session and a new task, with a fixed four-item tab bar. Approvals take two taps — review on the home card, then allow or deny. It is installable as a PWA; the composer microphone uses browser `SpeechRecognition` and falls back to the keyboard when unsupported or denied.

![Spark remote control: QR code, one-time link and paired-device list](assets/screenshots/25-remote-control.png)

![Spark remote control: online device, last request and local audit trail](assets/screenshots/26-remote-audit.png)

![Spark mobile home: pending approval, current session and new task on one screen](assets/screenshots/27-mobile-home.png)

![Spark mobile approval: review from the home card, then deny or allow in two taps](assets/screenshots/28-mobile-approval.png)

## Safety model

Three layers narrow each other:

1. **Approval** — from plan-only to full-auto. The default asks before writes and commands.
2. **Sandbox** — workspace isolation by default; escapes and protected paths are refused.
3. **Rules** — tool and resource matching across user, project and local scopes. A cloned repository can tighten permissions, never grant itself more.

OS-level isolation is used where available: bwrap on Linux, Job Objects on Windows, `sandbox-exec` on macOS. When a platform cannot enforce a control, the tool result says so instead of pretending.

## Documentation

| Doc | 中文 |
|---|---|
| [Product positioning](docs/en/PRODUCT_POSITIONING.md) | [产品定位](docs/PRODUCT_POSITIONING.md) |
| [Core capability gaps](docs/CORE_CAPABILITY_GAPS.md) | [核心能力差距](docs/CORE_CAPABILITY_GAPS.md) |
| [Integration map](docs/INTEGRATION_MAP.md) | [外部集成地图](docs/INTEGRATION_MAP.md) |
| [Install & build](docs/en/INSTALL.md) | [安装与打包](docs/INSTALL.md) |
| [Configuration](docs/en/CONFIG.md) | [配置](docs/CONFIG.md) |
| [HTTP API](docs/en/API.md) | [API](docs/API.md) |
| [Architecture](docs/en/ARCHITECTURE.md) | [架构](docs/ARCHITECTURE.md) |
| [Features](docs/FEATURES.md) | [功能总览](docs/FEATURES.md) |
| [Remote control](docs/REMOTE.md) | [远程控制](docs/REMOTE.md) |
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

Python 3.11.5+ (or the bundled executable), Node.js 18+ only for frontend development, Windows / macOS / Linux.

---

**License:** MIT · **Author:** badhope
