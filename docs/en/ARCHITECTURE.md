# Spark Architecture

> 中文：[docs/ARCHITECTURE.md](../ARCHITECTURE.md)
> This document mirrors the **`src/spark`** tree — the single source of truth
> since the legacy root-level `spark/` package was retired (2026-10-09).

## Overview

Spark is a **browser-native, cross-platform, open-source AI coding
application**: one Codex-style ReAct loop serves the browser, terminal, desktop,
IDE, mobile layout and API. Models, tools, Skills, MCP servers, themes and
workflows are replaceable, while code and ledgers live in the workspace you
choose. A four-level sandbox, human approval gates, checkpoint/rollback and
long-term memory are part of the same runtime.

```
user input → AgentLoop (turn engine) → model call → tool execution → feedback → next round
                   ↑                                                  ↓
        context compaction ← memory retrieval ← AGENTS.md ←── SQLite persistence
                   ↓
        SSE streaming → Web UI (React) / TUI (Textual) / Desktop (pywebview)
```

## Core components

### 1. Agent loop (`src/spark/core/loop.py`)

`AgentLoop` drives one turn: build messages → stream the model → on tool calls
sandbox-check + approval-gate + execute → append results → repeat until the
model answers. Guards on the way:

| Guard | Where |
| --- | --- |
| Repeat-call circuit breaker (same tool+args fingerprint) | `_record_call` |
| Per-turn token budget | `_charge_tokens` / `_budget_exceeded` |
| Auto-compaction past the context threshold | `_maybe_compact` (turn-boundary-safe cut) |
| Incremental usage cache (prompt/history/tool schemas) | `_usage` |
| Hook events (`app_start`, `pre_tool`, …) | `_notify_safe` |

A LangGraph state-machine path (`graph.py`) formalises the same
call-model → execute-tools cycle with interrupt-based approval; `loop.py`
remains the outer orchestrator either way.

### 2. Context engine (`src/spark/core/context.py`)

Builds what the model actually sees: system prompt (`prompts/system.md`) +
workdir + detected environment block (real runtimes probed once per process) +
access-mode hint + `<available_skills>`; then AGENTS.md, the memory block, and
the token-budgeted recent history (images collapsed to placeholders except the
newest). `history_token_usage` shares the exact same prompt builder so the
usage meter can never drift from the real request.

### 3. Compaction (`src/spark/core/compact.py`)

When usage crosses `context.compact_threshold`, old turns are folded into a
structured 5-section summary (Objective / Important Details / Work State /
Next Move / Relevant Files). Prior summaries are merged by rules (new wins on
conflict, Active→Completed moves, file lists union). Falls back to
LlamaIndex's `ChatSummaryMemoryBuffer` when the direct LLM call fails.

### 4. Providers (`src/spark/providers/`)

| Module | Role |
| --- | --- |
| `base.py` | `Provider` protocol: `async stream(messages, tools)` |
| `factory.py` | `create_provider(cfg)` — `openai_compat` / `ollama` / `mock` |
| `openai_compat.py` | One LiteLLM-backed implementation serving both `OpenAICompatProvider` and `OllamaProvider`: streaming, retry with backoff, reasoning-content passthrough, incremental tool-call aggregation, `<think>` splitting |
| `mock.py` | Deterministic provider for tests |
| `probe.py` | Connectivity probe used by `spark test` / Settings → Test |

### 5. Tool registry (`src/spark/tools/registry.py`)

All tools are registered as JSON schemas and dispatched through a table, so
approval and sandbox checks are uniform:

```
ToolRegistry
├── fs:        read_file, write_file, apply_patch, list_dir
├── search:    grep, glob
├── shell:     run_shell (+ bg_start / bg_output / bg_kill, persisted across restarts)
├── git:       git_status / diff / log / branch / add / commit, run_tests
├── worktree:  worktree_list / create / remove
├── notebook:  read_notebook, notebook_edit
├── web:       web_search (Bing), web_fetch (SSRF-guarded)
├── planning:  update_plan, load_skill
├── agents:    task (sub-agent delegation, up to 4 parallel)
└── mcp__:     dynamic tools bridged by McpBridge (official MCP SDK)
```

Stale MCP tools (server restarted with fewer tools) are rejected by name via
the registry's schema revision.

### 6. Sandbox & policy (`src/spark/sandbox.py`, `src/spark/policy.py`)

Four access levels, progressively unlocked:

```
sandbox-only:   read/search/plan only
workspace:      + write/patch/shell inside the workdir
full-access:    + operate outside the workdir (protected paths still blocked)
unrestricted:   everything (Spark's own code protection included off)
```

- Command deny-list scanning + workdir path-escape checks (`sandbox.py`)
- Tool classification: READONLY / WRITE / SHELL / EXECUTE sets (`policy.py`);
  shell-class tools can never receive a persistent "always allow"
- Wildcard permission rules with per-(tool, args-signature) session grants

### 7. Web layer (`src/spark/web/`)

stdlib `http.server` — no web framework:

| Module | Role |
| --- | --- |
| `server.py` | Entry point; builds `SparkHTTPServer` + `SparkWebState` |
| `routes.py` | Handler class: security guards (loopback/host/origin/CSRF/token) + GET routes + POST dispatch table |
| `_handlers.py` | Standalone POST handlers (models/agents_md/memory/sessions/checkpoints/chat/settings/test) |
| `sse.py` | SSE session: text coalescing, heartbeat, backpressure queue, disconnect-cancels-turn |
| `state.py` | `SparkWebState`: config, loop lifecycle, approver bridge, MCP boot, memory |
| `utils.py` / `_httpbase.py` / `_routing.py` | Shared helpers |

Auth: loopback is token-free; remote callers need the `X-Spark-Token` header
(token generated at startup, never logged). Non-local Host header or a
cross-origin Origin header is rejected.

### 8. Persistence (`src/spark/store.py`)

SQLite (WAL) with combined indexes:

| Table | Content |
| --- | --- |
| `sessions` | Session meta (id, title, model, workdir, timestamps) |
| `messages` | Chat messages (role, content, tool payload) |
| `tool_events` | Audit trail per tool call (name, args, duration, ok) |
| `checkpoints` | Checkpoint records |
| `bg_jobs` | Background job state (survives restarts) |

### 9. Memory (`src/spark/memory/`)

`store.py` (ChromaDB vectors + SQLite rows, CAS version checks),
`extractor.py` (LLM fact extraction, secret redaction, consolidation),
`service.py` (add/search/optimize, degrade-to-keyword when embeddings fail).

### 10. Checkpoints (`src/spark/core/checkpoints.py`)

After every write/shell turn, an incremental `tar.gz` snapshot (baseline +
delta) lands under `~/.spark/checkpoints/`. Rollback restores the workdir
files (and deletes files created since) plus the conversation history.

## Frontend (`web/src/`)

React 19 + Zustand + Vite + Tailwind 4.

- `App.tsx` — layout, history rendering, checkpoint dialog
- `api.ts` — REST + SSE client (`fetchWithTimeout`, stream reader)
- `store/` — `chatStore` (stream state machine), `sessionStore`, `uiStore`
- `components/` — ChatMessage (Markdown + highlight.js), ApprovalCard,
  PlanCard, ContextMeter, ThinkingBlock, SessionSidebar, SettingsPanel…
- i18n (`i18n.ts`) — zh/en, persisted per client

Bundles are split (react / markdown / highlight / settings chunks) and built
into `src/spark/web/dist`, which ships inside the wheel so `pip install` works
without Node.

## CLI (`src/spark/cli.py`, Typer)

```
spark                # interactive TUI (textual)
spark exec "prompt"  # headless one-shot
spark web            # serve the web UI on 127.0.0.1
spark desktop        # pywebview shell around the same server (Windows WebView2)
spark test --model X # connectivity probe
spark sessions / resume <id>
spark init / doctor / version / config show|set|path
```

## Extension guide

### Add a tool

1. Implement it under `src/spark/tools/` (pydantic Args model → `ToolResult`)
2. Register the schema in `registry.py` (`schemas()` + `_build_dispatch_table()`)
3. Classify it in `policy.py` (READONLY / WRITE / SHELL / EXECUTE)
4. Add tests

### Add a provider

1. Implement the `Provider` protocol (`stream()` async generator) — or reuse
   the LiteLLM layer in `openai_compat.py`
2. Wire it in `providers/factory.py::create_provider`
3. Extend `ProviderName` in `config.py` and the Settings panel options

## Design principles

- **Browser-native, cross-platform**: one agent runtime serves the Web UI, CLI/TUI, desktop, IDE and API without binding to one OS or editor
- **Data sovereignty**: code, conversation, memory and checkpoints live in the workspace you choose; self-hosting is supported, not required as a product identity
- **Boring web stack**: stdlib HTTP server + SSE; no framework to pin
- **Layered safety**: sandbox levels → approval policy → protected paths →
  egress sanitization (`sanitize.py`)
- **Observable**: token accounting, context meter, tool audit events
- **Fail-soft**: memory degrades to keywords, probes degrade gracefully,
  compaction falls back across two paths
