# Spark HTTP API Reference

> 中文：[docs/API.md](../API.md)
> Implementation source: `src/spark/web/routes.py` (GET routes + POST dispatch table) and `src/spark/web/_handlers.py` (POST handlers).

## Authentication

The server listens on `127.0.0.1` only by default (`spark web --host 0.0.0.0` opts into LAN exposure at your own risk).

- Auth lives in `routes.py` (`_guard` / `_authorized`); non-loopback callers must present the control token.
- The token is generated at startup (`SparkWebState.control_token`) and is only available on the console object; it is never logged.
- Loopback requests (127.0.0.1 / ::1 / localhost) need no token; everything else gets `401` unless the `X-Spark-Token` header matches.
- Extra guards: non-local Host header → `403`; a cross-origin Origin header → `403` (CSRF); POST bodies must be `application/json` or `415`.

## Static files

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | Serves the SPA shell (bundled `src/spark/web/dist/index.html` first, built-in fallback page otherwise) |
| `GET` | `/<static file>` | Serves assets from the UI directory (path-escape checked); `/assets/*` is cached immutable for one year |

## Read-only GET endpoints

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/status` | Session, sandbox mode, approval mode, context usage, plan, environment info |
| `GET` | `/api/config` | Settings view (some sensitive values masked; not all config): provider, agent, context (incl. `agents_md` / `repo_map_max_chars` / `knowledge_*`), memory (incl. `embedding_*` / `rerank_model`), mcp_servers, hooks (incl. `on_deny`), replay, updater, plus `permission_rules` / `approval_profiles` / `active_approval_profile_id` |
| `GET` | `/api/models` | Model profiles (api_key masked, `active` flag) |
| `GET` | `/api/agents_md` | Reads the AGENTS.md inside the workdir |
| `GET` | `/api/memory` | Long-term memory: `items`, `search?q=` hits with score, `stats` |
| `GET` | `/api/sessions` | Sessions for the current workdir (latest 50) + `current` id |
| `GET` | `/api/checkpoints` | Checkpoint list for the current session |
| `GET` | `/api/history` | Message history of the current session (images as base64 data URIs) |

## POST endpoints

| Method | Path | Body | Description |
| --- | --- | --- | --- |
| `POST` | `/api/chat/stream` | `{prompt, images?}` | Streaming chat over SSE. Events: `text_delta` / `tool` (call + result) / `approval` (with diff) / `turn_end` / `turn_error` / `done`; heartbeat `: ping` |
| `POST` | `/api/chat` | `{prompt}` | Non-streaming chat; returns the full event array (debugging) |
| `POST` | `/api/cancel` | — | Cancel the running turn and pending approval |
| `POST` | `/api/approval` | `{decision}` | `allow` / `allow_always` / `deny` |
| `POST` | `/api/models/save` | `{name, base_url, model, provider, api_key, id?}` | Create or update a model profile |
| `POST` | `/api/models/delete` | `{id}` | Delete a profile; clearing the active one resets `active_profile_id` |
| `POST` | `/api/models/activate` | `{id}` | Activate a profile and rebuild the AgentLoop; `409` while busy |
| `POST` | `/api/agents_md` | `{content}` | Write the AGENTS.md inside the workdir (filename validated) |
| `POST` | `/api/memory` | `{action}` | `add` (content/type/importance) / `delete` (id) / `optimize` |
| `POST` | `/api/sessions/new` | — | Start a new session |
| `POST` | `/api/sessions/delete` | `{session_id}` | Delete a session; auto-switches when deleting the current one |
| `POST` | `/api/sessions/switch` | `{session_id}` | Switch sessions and return the message history |
| `POST` | `/api/checkpoints/rollback` | `{checkpoint_id}` | Roll back to a checkpoint (restores workdir files + history) |
| `POST` | `/api/settings` | provider/agent/context/memory/hooks/mcp_servers/replay/updater sections | Save settings and rebuild the AgentLoop; `400` without persisting on validation failures, `409` while busy; omitted fields usually remain unchanged (except the pool when changing target) |
| `POST` | `/api/test` | `{base_url, model, api_key?}` | Probe the model endpoint (refuses to send the stored credential to a different host) |

Unknown paths return `404`; bodies above 32 MiB return `413`; malformed JSON returns `400`.

## `/api/settings` field contract

Omitted fields usually retain their values, but changing provider or endpoint clears the
old credential pool if `api_keys` is omitted. Some fields are coerced, sections of an
unexpected type may be ignored, and unknown fields are not guaranteed to cause an error.
Validation failures such as out-of-range values return `400`, for example
`{"error": "invalid settings: agent.shell_timeout_sec: Input should be less than or equal to 3600"}`;
failed candidate validation rolls back memory and does not save. Save or rebuild failures
may return `500`.

For `openai_compat`, switching to a different provider or endpoint requires an explicit
credential source: either send `api_key`, or send `api_key_env` naming an environment
variable that exists. Without either, the request returns `400` — the stored `api_key`
and the previous `api_key_env` both belong to the old endpoint, so reusing them silently
would send the credential to a new host. When the endpoint changes without a submitted
`api_key_env`, that binding is cleared; the environment value is only used to check
availability and is never written into the config file. `mock` and `ollama` need no
credentials and are exempt.

| Section | Writable fields |
| --- | --- |
| `provider` | `name`, `base_url`, `model`, `api_key` (blank = keep), `api_key_env` (valid env name), `api_keys`, `fallback_models`, `small_model` |
| `agent` | `approval`, `sandbox_mode`, `effort`, `workdir_only`, `protected_paths`, `system_prompt`, `show_*`, `shell_timeout_sec`, `max_tool_rounds`, `max_repeat_calls`, `max_turn_tokens`, `max_output_chars` |
| `context` | `agents_md` (bare filename only), `max_fragment_chars`, `history_budget_chars`, `max_context_tokens`, `compact_threshold`, `keep_recent_messages`, `repo_map_enabled`, `repo_map_max_chars`, `knowledge_enabled`, `knowledge_top_k`, `knowledge_max_chars` |
| `memory` | `enabled`, `top_k`, `capacity`, `embedding_model`, `auto_extract`, `embedding_enabled`, `embedding_base_url`, `embedding_api_key` (blank = keep), `rerank_model` |
| `replay` | `enabled` |
| `updater` | `mode` (`notify` / `off`) |
| `hooks` | `[{event, command, args, name, timeout_sec, on_deny}]`; a missing `on_deny` inherits the existing value for the same event+command |
| `mcp_servers` | Same shape as `/api/config`; sensitive env/header values sent back as `[REDACTED]` are restored |

Most numeric bounds are in the pydantic `ge`/`le` metadata in `src/spark/config.py`;
hook `timeout_sec` is separately converted and clamped to at least 1, with blank using 15.

Submitting `api_keys` replaces the entire pool: never submit the masks returned by GET as
keys; an empty array clears the pool. Long-key masks expose the first and last four
characters. MCP env values merge by default; `env_replace: true` replaces them. Old env
values are not automatically carried to a changed target. MCP header placeholders are
restored only for the same server name and URL, otherwise dropped; an env placeholder
that cannot be safely restored produces an error. Config files store secrets in plaintext;
restrict access.

### `permission_rules` / `approval_profiles` write API

`GET /api/config` returns `permission_rules`, `approval_profiles` and
`active_approval_profile_id`. The rules are the user-level rules, not the project/local
scopes; `permission_scopes` is not returned. `POST /api/settings` still returns an explicit
`400` (`... is read-only over /api/settings`) for any of these four keys — governance goes
through the dedicated endpoints below so the two semantics never blur.

Why they are separate from `/api/settings`: permission rules are a security boundary with
deny-across-scopes precedence (`spark.policy.decide` + `_load_scoped_permission_rules`), and
activating an approval profile rewrites the approval mode, tool allowlist and system prompt.
Their failure modes silently weaken or widen permissions, so they need their own response
and their own audit trail.

| Method | Path | Body | Semantics |
| --- | --- | --- | --- |
| `POST` | `/api/permissions/rules` | `{scope: "user", rules: [{action, resource, tool}]}` | Replaces the `user` scope wholesale; `project` / `local` come from workdir files and any other `scope` returns `400` |
| `POST` | `/api/permissions/rules/delete` | `{scope, index}` | Delete by index (glob strings are not stable keys); out of range `404`, negative `400` |
| `POST` | `/api/approval_profiles/save` | `{id?, name, approval, tools, system_prompt, model_profile_id}` | Mirrors `/api/models/save`: no `id` creates; `approval` is required on create; unknown `model_profile_id` returns `404` |
| `POST` | `/api/approval_profiles/delete` | `{id}` | Deleting the active profile clears `active_approval_profile_id` |
| `POST` | `/api/approval_profiles/activate` | `{id}` | Activate and rebuild the AgentLoop (`settings_lock` → non-blocking `turn_lock`); a profile with `model_profile_id` also switches that model profile |

Shared constraints: every write runs inside `state.settings_lock` as
"validate → apply → `save_config`"; rule writes additionally re-validate the whole config
against the pydantic models and roll back on failure. Every successful write appends an
audit event (`permission_rules_written` / `approval_profile_written` /
`approval_profile_deleted` / `approval_profile_activated`, see `spark.audit`). Malformed
rule entries are rejected rather than dropped — a `deny` the user believes exists but
doesn't is the worse outcome.

## SSE event format

`/api/chat/stream` responds with `text/event-stream`, one event per `data: {JSON}\n\n`:

```jsonc
{"type": "text_delta", "text": "…"}                       // incremental text (sanitized)
{"type": "tool", "tool": {…}, "ok": true, "result": {…}}  // tool call and result
{"type": "approval", "approval": {"summary": "…", "diff": "…"}}  // awaiting approval
{"type": "turn_end", "text": "…"}                         // turn finished
{"type": "turn_error", "text": "…"}                       // turn failed
{"type": "done"}                                          // stream closed
```

A dropped connection (write failure or peer close) cancels the running turn; a `: ping` heartbeat is sent every 15 seconds of silence.

> AI-generated
