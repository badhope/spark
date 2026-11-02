import type {
  ChatEvent,
  Status,
  ProbeResult,
  SessionRow,
  HistoryMessage,
  FullConfig,
  SettingsPayload,
  ModelProfile,
} from "./types"
import { useUiStore } from "./store/uiStore"

const DEFAULT_TIMEOUT_MS = 30000

export async function fetchWithTimeout(
  url: string,
  options?: RequestInit,
  timeout = DEFAULT_TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController()
  const timeoutId = setTimeout(
    () => controller.abort(new DOMException("Request timeout", "TimeoutError")),
    timeout,
  )
  const onCallerAbort = () => controller.abort()
  if (options?.signal) {
    options.signal.addEventListener("abort", onCallerAbort, { once: true })
  }
  try {
    return await fetch(url, { ...options, signal: controller.signal })
  } finally {
    clearTimeout(timeoutId)
    // 关键：请求结束后移除监听，避免长生命周期 signal 上堆积回调（泄漏）
    options?.signal?.removeEventListener("abort", onCallerAbort)
  }
}

async function readJson<T>(res: Response): Promise<T> {
  // Error bodies are not always JSON (a proxy 502, a 413 from the stdlib
  // server). Parsing unconditionally turned those into "Unexpected token" and
  // hid the real status.
  const text = await res.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = null
    }
  }
  if (!res.ok) {
    const fromBody = (data as { error?: string } | null)?.error
    throw new Error(fromBody || `HTTP ${res.status} ${text.slice(0, 120)}`.trim())
  }
  return data as T
}

export async function fetchStatus(): Promise<Status> {
  return readJson<Status>(await fetchWithTimeout("/api/status"))
}

export async function fetchConfig(): Promise<FullConfig> {
  return readJson<FullConfig>(await fetchWithTimeout("/api/config"))
}

export async function fetchAgentsMd(): Promise<{
  filename: string
  exists: boolean
  content: string
  chars: number
  max_fragment_chars: number
}> {
  return readJson(await fetchWithTimeout("/api/agents_md"))
}

export interface SlashCommandInfo {
  name: string
  description: string
}

/** 项目里定义的自定义斜杠命令（.spark/commands/*.md）。 */
export async function fetchCommands(): Promise<{ commands: SlashCommandInfo[] }> {
  return readJson(await fetchWithTimeout("/api/commands"))
}

/** 导出当前会话（opencode/codex 的 /export 等价物）。 */
export async function exportSession(
  format: "markdown" | "json" | "html" = "markdown",
): Promise<{ ok: boolean; format: string; content: string }> {
  return readJson(
    await fetchWithTimeout("/api/sessions/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format }),
    }),
  )
}

export async function saveAgentsMd(
  content: string,
): Promise<{ ok: boolean; chars: number; max_fragment_chars: number }> {
  return readJson(
    await fetchWithTimeout("/api/agents_md", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }),
  )
}

export interface MemoryItem {
  id: number
  type: string
  content: string
  importance: number
  status: string
  access_count: number
  created_at: number
  updated_at: number
  score?: number
}

export interface MemoryStats {
  active: number
  archived: number
  by_type: Record<string, number>
  embedding_available: boolean | null
  embedding_model: string
  capacity?: number
}

export async function fetchMemories(query: string): Promise<{
  items: MemoryItem[]
  search: MemoryItem[]
  stats: MemoryStats
}> {
  const url = query ? `/api/memory?q=${encodeURIComponent(query)}` : "/api/memory"
  return readJson(await fetchWithTimeout(url))
}

export async function memoryAdd(payload: {
  content: string
  type: string
  importance: number
}): Promise<{ ok: boolean; id: number; stats: MemoryStats }> {
  return readJson(
    await fetchWithTimeout("/api/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "add", ...payload }),
    }),
  )
}

export async function memoryDelete(id: number): Promise<{ ok: boolean; stats: MemoryStats }> {
  return readJson(
    await fetchWithTimeout("/api/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "delete", id }),
    }),
  )
}

export async function memoryOptimize(): Promise<{
  ok: boolean
  report: { consolidated_groups: number; archived: number; errors: string[] }
}> {
  return readJson(
    await fetchWithTimeout("/api/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "optimize" }),
    }),
  )
}

export async function saveSettings(
  payload: SettingsPayload,
): Promise<{ ok: boolean; status: Status; mcp_errors: string[]; config: FullConfig }> {
  return readJson(
    await fetchWithTimeout("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  )
}

export async function listSessions(): Promise<{ sessions: SessionRow[]; current: string }> {
  return readJson(await fetchWithTimeout("/api/sessions"))
}

export interface SessionSearchHit {
  id: string
  workdir: string
  title: string
  updated_at: number
  match: { kind: "title" | "content"; snippet: string }
}

/** 会话全文检索：FTS5 trigram（中文子串友好），搜得到消息正文。 */
export async function searchSessions(q: string, limit = 20): Promise<{ hits: SessionSearchHit[] }> {
  const query = encodeURIComponent(q)
  return readJson(await fetchWithTimeout(`/api/sessions/search?q=${query}&limit=${limit}`))
}

export async function fetchHistory(): Promise<{ session_id: string; messages: HistoryMessage[] }> {
  return readJson(await fetchWithTimeout("/api/history"))
}

export async function newSession(): Promise<Status> {
  const data = await readJson<{ status: Status }>(
    await fetchWithTimeout("/api/sessions/new", { method: "POST" }),
  )
  return data.status
}

export async function switchSession(
  sessionId: string,
): Promise<{ status: Status; messages: HistoryMessage[] }> {
  return readJson(
    await fetchWithTimeout("/api/sessions/switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    }),
  )
}

export async function cancelTurn(): Promise<void> {
  await readJson(await fetchWithTimeout("/api/cancel", { method: "POST" }))
}

export async function deleteSession(sessionId: string): Promise<{ ok: boolean; status?: Status }> {
  return readJson(
    await fetchWithTimeout("/api/sessions/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    }),
  )
}

export async function renameSession(sessionId: string, title: string): Promise<{ ok: boolean; status: Status }> {
  return readJson(
    await fetchWithTimeout("/api/sessions/rename", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, title }),
    }),
  )
}

export async function forkSession(
  sessionId?: string,
): Promise<{ ok: boolean; status: Status; messages: HistoryMessage[] }> {
  return readJson(
    await fetchWithTimeout("/api/sessions/fork", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId ?? "" }),
    }),
  )
}

export async function deleteMessage(messageId: number): Promise<{
  ok: boolean
  status: Status
  messages: HistoryMessage[]
}> {
  return readJson(
    await fetchWithTimeout("/api/messages/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_id: messageId }),
    }),
  )
}

export interface HealthRow {
  level: "ok" | "warn" | "bad"
  kind: string
  message: string
  fix: string
  auto_fixable: boolean
}

export async function fetchDiagnostics(): Promise<{
  ok: boolean
  rows: HealthRow[]
  blocking: number
  warnings: number
}> {
  return readJson(await fetchWithTimeout("/api/diagnostics"))
}

export async function runSelfHeal(): Promise<{
  ok: boolean
  actions: Array<{ kind: string; message: string; applied: boolean; detail?: string; error?: string }>
  rows: HealthRow[]
  remaining: HealthRow[]
}> {
  return readJson(await fetchWithTimeout("/api/selfheal", { method: "POST" }))
}

export async function answerQuestion(answer: string): Promise<void> {
  await readJson(
    await fetchWithTimeout("/api/question/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answer }),
    }),
  )
}

export async function respondApproval(decision: "allow" | "allow_always" | "deny"): Promise<void> {
  await readJson(
    await fetchWithTimeout("/api/approval", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    }),
  )
}

export async function listModelProfiles(): Promise<{ profiles: ModelProfile[] }> {
  return readJson(await fetchWithTimeout("/api/models"))
}

export async function saveModelProfile(payload: {
  id?: string
  name: string
  provider: string
  base_url: string
  model: string
  api_key?: string
  api_key_env?: string
}): Promise<{ ok: boolean; id: string }> {
  return readJson(
    await fetchWithTimeout("/api/models/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  )
}

export async function deleteModelProfile(id: string): Promise<void> {
  await readJson(
    await fetchWithTimeout("/api/models/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    }),
  )
}

export async function activateModelProfile(id: string): Promise<{ status: Status }> {
  return readJson(
    await fetchWithTimeout("/api/models/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    }),
  )
}

export async function testConnection(payload: {
  base_url: string
  model: string
  api_key: string
}): Promise<ProbeResult> {
  const res = await fetchWithTimeout(
    "/api/test",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
    // A probe performs a real streaming round-trip; the 30s default could cut a
    // slow endpoint off before it answers.
    120000,
  )
  if (!res.ok) {
    const text = await res.text()
    let detail = text
    try {
      detail = (JSON.parse(text) as { error?: string }).error || text
    } catch {
      /* non-JSON error body */
    }
    // Surface the failure as a ProbeResult so the UI shows it in place.
    return { ok: false, error: detail || `HTTP ${res.status}`, model: payload.model, base_url: payload.base_url, content: "", stream_content: "", rounds: 0, latency_ms: 0, details: [] }
  }
  return (await res.json()) as ProbeResult
}

/* ------------------------------------------------------------------ */
/* 内置终端（PTY）                                                     */
/* 架构说明：后端服务器基于标准库 http.server，不支持 WebSocket 升级，  */
/* 实时通道采用 SSE（GET 长连接逐帧推送），与 /api/chat/stream 同构。   */
/* ------------------------------------------------------------------ */

export interface PtyOpenInfo {
  tab_id: string
  cwd: string
  alive: boolean
  rows: number
  cols: number
}

export async function ptyOpen(tabId = "main", cwd?: string): Promise<PtyOpenInfo> {
  return readJson<PtyOpenInfo>(
    await fetchWithTimeout("/api/pty/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tab_id: tabId, cwd }),
    }),
  )
}

export async function ptyWrite(tabId: string, data: string): Promise<boolean> {
  const r = await readJson<{ ok: boolean }>(
    await fetchWithTimeout("/api/pty/write", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tab_id: tabId, data }),
    }),
  )
  return r.ok
}

export async function ptyResize(tabId: string, rows: number, cols: number): Promise<void> {
  await readJson(
    await fetchWithTimeout("/api/pty/resize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tab_id: tabId, rows, cols }),
    }),
  )
}

export async function ptyClose(tabId: string): Promise<void> {
  await readJson(
    await fetchWithTimeout("/api/pty/close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tab_id: tabId }),
    }),
  )
}

export type PtyEvent =
  | { type: "pty_open"; tab_id: string; cwd: string }
  | { type: "pty_data"; data: string }
  | { type: "pty_exit"; tab_id: string }

/** 订阅 PTY 输出 SSE 流（GET 长连接）。返回的 Promise 仅在流结束时 resolve。 */
export async function streamPty(
  tabId: string,
  onEvent: (ev: PtyEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetchWithTimeout(
    `/api/pty/stream?tab_id=${encodeURIComponent(tabId)}`,
    { method: "GET", signal },
    120000,
  )
  if (!res.ok || !res.body) {
    throw new Error(`pty stream HTTP ${res.status}`)
  }
  const reader = res.body.getReader()
  try {
    const decoder = new TextDecoder()
    let buffer = ""
    for (;;) {
      const { done, value } = await reader.read()
      if (done) return
      buffer += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)
        for (const line of frame.split("\n")) {
          if (!line.startsWith("data: ")) continue
          const payload = line.slice(6)
          if (!payload) continue
          try {
            onEvent(JSON.parse(payload) as PtyEvent)
          } catch {
            /* skip malformed frame */
          }
        }
      }
    }
  } finally {
    try { reader.cancel() } catch { /* ignore */ }
  }
}

/* ------------------------------------------------------------------ */
/* MCP registry 聚合搜索（P1-7）                                        */
/* ------------------------------------------------------------------ */

export interface McpRegistryItem {
  name: string
  title: string
  description: string
  version: string
  repository_url: string
  transport: "stdio" | "remote" | "unknown"
  remote_url: string | null
  suggested_command: string | null
  suggested_args: string[]
  required_env: { name: string; required: boolean; secret: boolean }[]
  glama: { score: number; count: number; source: string } | null
  china: { verdict: "available" | "risk" | "unreachable"; reasons: string[] }
}

export interface McpRegistryResult {
  items: McpRegistryItem[]
  source: "live" | "offline"
  error: string | null
  filtered_out: number
}

export async function searchMcpRegistry(
  query: string,
  includeUnavailable = false,
): Promise<McpRegistryResult> {
  const q = encodeURIComponent(query)
  const inc = includeUnavailable ? "1" : "0"
  return readJson<McpRegistryResult>(
    await fetchWithTimeout(`/api/mcp/registry/search?q=${q}&include_unavailable=${inc}`, undefined, 45000),
  )
}

export interface CheckpointRow {
  id: number
  label: string
  message_id: number
  created_at: number
}

export async function listCheckpoints(): Promise<{ checkpoints: CheckpointRow[] }> {
  return readJson(await fetchWithTimeout("/api/checkpoints"))
}

export async function rollbackCheckpoint(checkpointId: number): Promise<{
  ok: boolean
  report: { checkpoint_id: number; label: string; files: { restored_files: number; snapshot_id: string }; removed_messages: number }
  status: Status
  messages: HistoryMessage[]
}> {
  return readJson(
    await fetchWithTimeout("/api/checkpoints/rollback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ checkpoint_id: checkpointId }),
    }),
  )
}

/* ------------------------------------------------------------------ */
/* P2 上下文合同：turn 级重放记录 + 双粒度时间机器                       */
/* ------------------------------------------------------------------ */

export interface ReplayTurnSummary {
  index: number
  prompt: string
  status?: string
  duration_ms?: number
  tools: number
  resumed?: boolean
  text_preview?: string
  usage?: { input_tokens?: number; output_tokens?: number; reasoning_tokens?: number; source?: string }
  recorded_at?: number
}

export interface ReplayToolCall {
  id: string
  name: string
  arguments: unknown
  ok?: boolean | null
  result?: unknown
}

export interface ReplayRecord {
  schema: string
  session_id: string
  model: string
  recorded_at: number
  turn: {
    index: number
    prompt: string
    resumed?: boolean
    duration_ms: number
    status: string
    error?: string | null
    usage?: ReplayTurnSummary["usage"]
    text: string
    reasoning?: string
    tool_calls: ReplayToolCall[]
    approvals?: { tool?: string; summary: string; decision?: string | null }[]
    compaction?: { before_tokens?: number; after_tokens?: number } | null
  }
}

export async function fetchReplays(): Promise<{ session_id: string; turns: ReplayTurnSummary[] }> {
  return readJson(await fetchWithTimeout("/api/replays"))
}

export async function fetchReplayTurn(index: number): Promise<ReplayRecord> {
  return readJson(await fetchWithTimeout(`/api/replays/turn?index=${index}`))
}

export interface TimelineMessageBrief {
  id: number
  role: string
  name?: string
  preview: string
  created_at: number
}

export async function fetchTimeline(): Promise<{
  session_id: string
  checkpoints: CheckpointRow[]
  messages: TimelineMessageBrief[]
}> {
  return readJson(await fetchWithTimeout("/api/timeline"))
}

export async function truncateFromMessage(
  messageId: number,
  restoreFiles = false,
): Promise<{
  ok: boolean
  status: Status
  messages: HistoryMessage[]
  files_restored?: boolean
  restore_reason?: string
  restore_counts?: Record<string, unknown>
}> {
  return readJson(
    await fetchWithTimeout("/api/messages/truncate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_id: messageId, restore_files: restoreFiles }),
    }),
  )
}

/* ------------------------------------------------------------------ */
/* 治理：权限规则 / 审批档案写接口（user 层；project/local 只读）        */
/* ------------------------------------------------------------------ */

export interface PermissionRulePayload {
  action: "allow" | "prompt" | "deny"
  resource: string
  tool?: string
}

export async function savePermissionRules(
  rules: PermissionRulePayload[],
): Promise<{ ok: boolean; permission_rules: PermissionRulePayload[] }> {
  return readJson(
    await fetchWithTimeout("/api/permissions/rules", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope: "user", rules }),
    }),
  )
}

export async function deletePermissionRule(index: number): Promise<{ ok: boolean; remaining: number }> {
  return readJson(
    await fetchWithTimeout("/api/permissions/rules/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope: "user", index }),
    }),
  )
}

export async function saveApprovalProfile(payload: {
  id?: string
  name: string
  approval: string
  tools?: string[]
  system_prompt?: string
  model_profile_id?: string
}): Promise<{ ok: boolean; id: string }> {
  return readJson(
    await fetchWithTimeout("/api/approval_profiles/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  )
}

export async function deleteApprovalProfile(id: string): Promise<{ ok: boolean }> {
  return readJson(
    await fetchWithTimeout("/api/approval_profiles/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    }),
  )
}

export async function activateApprovalProfile(id: string): Promise<{ ok: boolean; status: Status }> {
  return readJson(
    await fetchWithTimeout("/api/approval_profiles/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    }),
  )
}

/* ------------------------------------------------------------------ */
/* 只读工具：文件查看 / 网页阅读（与 agent 工具共用同一套安全边界）      */
/* ------------------------------------------------------------------ */

export interface FsEntry {
  name: string
  type: "dir" | "file"
  size?: number
  mtime?: number
}

export async function fetchFsTree(path: string): Promise<{ path?: string; entries?: FsEntry[]; error?: string }> {
  return readJson(await fetchWithTimeout(`/api/fs/tree?path=${encodeURIComponent(path)}`))
}

export async function fetchFsRead(
  path: string,
): Promise<{ name?: string; path?: string; content?: string; size?: number; truncated?: boolean; binary?: boolean; error?: string }> {
  return readJson(await fetchWithTimeout(`/api/fs/read?path=${encodeURIComponent(path)}`))
}

export async function browseUrl(
  url: string,
): Promise<{ title?: string; url?: string; content?: string; error?: string }> {
  return readJson(await fetchWithTimeout(`/api/browse?url=${encodeURIComponent(url)}`))
}

export async function streamChat(
  prompt: string,
  onEvent: (ev: ChatEvent) => void,
  signal?: AbortSignal,
  images: string[] = [],
  webSearch = true,
): Promise<void> {
  if (signal?.aborted) {
    throw new DOMException("Aborted", "AbortError")
  }

  // 不自动重发：POST /api/chat/stream 一到达服务器就开跑一回合，
  // 断线重发会重复执行同一 prompt（重复工具调用与计费）；失败由用户重发。
  let lastEventId: string | undefined

  {
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" }
      if (lastEventId) headers["Last-Event-ID"] = lastEventId
      const res = await fetchWithTimeout(
        "/api/chat/stream",
        {
          method: "POST",
          headers,
          body: JSON.stringify({ prompt, images, web_search: webSearch }),
          signal,
        },
        60000,
      )
      if (!res.ok || !res.body) {
        let msg = `HTTP ${res.status}`
        try {
          const data = await res.json()
          if (data.error) msg = data.error
        } catch {
          /* ignore */
        }
        throw new Error(msg)
      }
      const reader = res.body.getReader()
      // fetchWithTimeout() detaches the caller's abort listener once the
      // response headers arrive, so from here on nothing observes `signal`.
      // Without this hook, Stop only flipped a local flag while the server kept
      // running the turn (and its tools) until it finished on its own.
      const onAbort = () => {
        try {
          void reader.cancel()
        } catch {
          /* stream already closed */
        }
      }
      signal?.addEventListener("abort", onAbort, { once: true })
      try {
        const decoder = new TextDecoder()
        let buffer = ""
        for (;;) {
          const { done, value } = await reader.read()
          if (done) return
          buffer += decoder.decode(value, { stream: true })
          let idx: number
          while ((idx = buffer.indexOf("\n\n")) >= 0) {
            const frame = buffer.slice(0, idx)
            buffer = buffer.slice(idx + 2)
            for (const line of frame.split("\n")) {
              if (line.startsWith("id: ")) { lastEventId = line.slice(4).trim(); continue }
              if (!line.startsWith("data: ")) continue
              const payload = line.slice(6)
              if (!payload || payload === "[DONE]") continue
              try {
                onEvent(JSON.parse(payload) as ChatEvent)
              } catch {
                /* skip malformed frame */
              }
            }
          }
        }
      } finally {
        signal?.removeEventListener("abort", onAbort)
        try { reader.cancel() } catch { /* ignore */ }
      }
    } catch (e) {
      if (signal?.aborted || (e instanceof DOMException && e.name === "AbortError")) {
        throw e
      }
      useUiStore.getState().pushToast("toast.sendFailed", "error")
      throw e
    }
  }
}

/* ------------------------------------------------------------------ */
/* 技能 / 提示词 / 插件 / 知识库                                        */
/* ------------------------------------------------------------------ */

export interface SkillItem {
  name: string
  description: string
  when_to_use: string
  path: string
  chars: number
}

export interface CommandItem {
  name: string
  description: string
  path: string
}

export async function skillsAction(body: Record<string, unknown>): Promise<Record<string, unknown>> {
  return readJson(
    await fetchWithTimeout("/api/skills", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  )
}

export async function promptsAction(body: Record<string, unknown>): Promise<{
  system_prompt?: string
  default_prompt?: string
  commands?: CommandItem[]
  ok?: boolean
  error?: string
}> {
  return readJson(
    await fetchWithTimeout("/api/prompts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  )
}

export interface PluginItem {
  name: string
  description: string
  path: string
  has_manifest: boolean
  entry: string
}

export async function fetchPlugins(): Promise<{
  items: PluginItem[]
  hooks: Array<Record<string, unknown>>
  plugins_dir: string
}> {
  return readJson(
    await fetchWithTimeout("/api/plugins", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "list" }),
    }),
  )
}

export interface KnowledgeDoc {
  id: string
  name: string
  path: string
  size: number
  chunks: number
  added_at: number
}

export interface KnowledgeHit {
  doc_name: string
  index: number
  text: string
  score: number
}

export async function knowledgeAction(body: Record<string, unknown>): Promise<{
  items?: KnowledgeDoc[]
  added?: KnowledgeDoc[]
  errors?: string[]
  hits?: KnowledgeHit[]
  enabled?: boolean
  top_k?: number
  ok?: boolean
  error?: string
}> {
  return readJson(
    await fetchWithTimeout("/api/knowledge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  )
}

/** `!cmd` 内联 shell：直接在工作目录执行一条命令（不进对话历史）。 */
export async function runInlineShell(command: string): Promise<{
  ok: boolean
  result: {
    command: string
    exit_code: number
    stdout: string
    stderr: string
    truncated?: boolean
  }
}> {
  return readJson(
    await fetchWithTimeout("/api/shell", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command }),
    }),
  )
}

/** 导入会话 JSON（导出文件原样回灌）。 */
export async function importSession(content: unknown): Promise<{
  ok: boolean
  session_id: string
  imported: number
  skipped: number
}> {
  return readJson(
    await fetchWithTimeout("/api/sessions/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }),
  )
}

/** 生成单文件 HTML 分享（本地下载，无服务端上传）。 */
export async function exportSessionHtml(): Promise<{ ok: boolean; content: string }> {
  return exportSession("html")
}
