import { create } from "zustand"
import type { ChatEvent, GoalState, PlanStep, ChatAttachment, ModelErrorInfo } from "../types"
import { streamChat, fetchHistory, fetchSessionPending, followSessionEvents, deleteMessage as deleteMessageApi, truncateFromMessage as truncateFromMessageApi, runInlineShell, fetchReplays, setMessageFeedback } from "../api"
import { useSessionStore } from "./sessionStore"
import { useSessionRuntimeStore } from "./sessionRuntime"
import { useUiStore } from "./uiStore"
import { t } from "../i18n"

export interface SubtaskTrace {
  index: number
  thinkingChars: number
  answerChars: number
  tools: number
  phase: "thinking" | "answering" | "done"
  /** 子代理收到的任务描述（subtask_start 自带）。UI 靠它区分"哪个子任务"。 */
  prompt?: string
  /** 声明的子代理名（若通过 task(agent=...) 派发）。 */
  agent?: string
  /** 子任务开始时间与耗时（subtask_trace 到达时冻结）。 */
  startedAt?: number
  durationMs?: number
}

export interface ToolCallState {
  id: string
  name: string
  arguments: unknown
  status: "running" | "ok" | "error"
  startedAt: number
  durationMs?: number
  result?: unknown
  /** Only on the `task` tool: per-child trace collected from subtask_* events. */
  subtasks?: SubtaskTrace[]
}

/** 助手消息内部的顺序片段，忠实还原"思考→计划→工具→文本"交错顺序 */
export type Segment =
  | { kind: "text"; text: string }
  | { kind: "thinking"; text: string; startedAt?: number; durationMs?: number }
  | { kind: "plan"; steps: PlanStep[] }
  | { kind: "tool"; tool: ToolCallState }
  | { kind: "side"; question: string; answer: string; model: string; ok: boolean }
  | { kind: "goal"; goal: GoalState; label?: string }

export interface ChatMsg {
  id: string
  role: "user" | "assistant" | "error" | "notice" | "summary" | "shell"
  /** 全文（复制用），与 segments 中的文本一致 */
  text: string
  images?: string[]
  /** 非图片附件只保留文件名用于展示；正文已进入本轮 prompt（后端注入）。 */
  attachments?: { name: string; kind?: string; size?: number }[]
  streaming?: boolean
  segments: Segment[]
  /** 本轮开始时间；用于生成中的实时计时与结束后的本轮耗时。 */
  startedAt?: number
  /** 本轮结束后的总耗时（毫秒）；历史消息没有该字段，不伪造。 */
  durationMs?: number
  /** 消息时间（由 replay recorded_at 或本地发送时间提供）；没有就不显示。 */
  createdAt?: number
  /** 本轮实际使用的模型（来自 replay 摘要；实时消息取当前会话模型）。 */
  model?: string
  /** 本轮 token 用量（来自 replay 摘要；实时消息在完成后回填）。 */
  usage?: { input_tokens?: number; output_tokens?: number; reasoning_tokens?: number; cache_read_tokens?: number; cache_write_tokens?: number; cost_usd?: number; source?: string }
  /** 后端消息行 id（编辑/删除/重试需要精确锚点）；流式新消息在刷新前没有 */
  dbId?: number
  /** 助手消息赞/踩；持久化在 message_feedback。 */
  feedback?: "up" | "down"
  /** 模型/配置失败的结构化诊断；旧事件只有 text 时为空。 */
  error?: ModelErrorInfo
  meta?: {
    compacted?: { before: number; after: number; summarized: number }
    /** `!cmd` 内联 shell 的执行结果（本地卡片，不进模型上下文） */
    shell?: {
      command: string
      exitCode: number
      stdout: string
      stderr: string
      truncated?: boolean
    }
  }
}

/** 一轮真实执行的节点/边，供执行图渲染；由 SSE 事件累积，不是样例数据。 */
export interface GraphNode {
  id: string
  label: string
  kind: "root" | "agent" | "tool" | "result"
  status: "running" | "done" | "error" | "pending"
}

export interface GraphEdge {
  from: string
  to: string
  label?: string
}

export interface TurnGraph {
  nodes: GraphNode[]
  edges: GraphEdge[]
  /** 这张图属于哪个会话；切换会话后不能把上一会话的实时图当成当前会话。 */
  sessionId?: string
}

const RESUMING = new Set<string>()

function uid(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return `m-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
  }
}

/** 首个正文/工具事件到来时，把仍在流式的思考段定格；没有 startedAt 的旧段不补造。 */
function closeThinking(segments: Segment[], now = Date.now()): Segment[] {
  let changed = false
  const next = segments.map((seg) => {
    if (seg.kind !== "thinking" || seg.durationMs !== undefined || seg.startedAt === undefined) return seg
    changed = true
    return { ...seg, durationMs: Math.max(0, now - seg.startedAt) }
  })
  return changed ? next : segments
}

/** 统一收尾：关闭思考计时，并把本轮总耗时写进消息（已存在则不覆盖）。 */
function finishTurnMessage(it: ChatMsg, now = Date.now()): ChatMsg {
  const durationMs = it.durationMs ?? (it.startedAt ? Math.max(0, now - it.startedAt) : undefined)
  return { ...it, segments: closeThinking(it.segments, now), streaming: false, durationMs }
}

interface ChatState {
  items: ChatMsg[]
  /** Per-session item cache so a background turn survives a session switch. */
  itemsBySession: Record<string, ChatMsg[]>
  input: string
  streaming: boolean
  loading: boolean
  abortController: AbortController | null
  /** 输入框"联网搜索"开关：关闭时本轮不向模型提供 web_search/web_fetch */
  webSearch: boolean
  /** 当前会话真实执行轨迹（最近一轮）；空数组 = 还没有执行过 */
  turnGraph: TurnGraph
  setTurnGraph: (g: TurnGraph) => void
  setInput: (v: string) => void
  setStreaming: (v: boolean) => void
  setItems: (items: ChatMsg[]) => void
  saveVisibleItems: (sessionId: string) => void
  setLoading: (v: boolean) => void
  setWebSearch: (v: boolean) => void
  addErrorItem: (text: string, error?: ModelErrorInfo) => void
  send: (images?: string[], attachments?: ChatAttachment[]) => Promise<void>
  /** `!cmd` 内联 shell：本地执行并渲染结果卡片，不进入对话历史 */
  runShell: (command: string) => Promise<void>
  /** 删除单条消息（后端持久化 + 本地同步） */
  deleteMessage: (dbId: number) => Promise<void>
  feedbackMessage: (dbId: number, rating: "up" | "down") => Promise<void>
  /** 重新生成：截断到该助手消息之前，重放它对应的用户消息 */
  regenerate: (assistantDbId: number) => Promise<void>
  /** 编辑重发：截断到该用户消息之前，并把原文放回输入框 */
  startEditMessage: (userDbId: number) => Promise<void>
  /** 传输失败时记住原 prompt，供「重试」按钮一键重发 */
  lastFailedPrompt: { prompt: string; images: string[]; attachments?: ChatAttachment[] } | null
  /** POST 流断开后正在附着会话 SSE 续接本轮；UI 用它显示全局状态条。 */
  recovering: boolean
  retryLastFailed: () => Promise<void>
  /** ``autoResume=false`` is used by resumeSession itself to avoid a
   *  reconnect loop when a dropped SSE stream leaves the turn busy. */
  loadInitialHistory: (autoResume?: boolean) => Promise<void>
  /** Re-attach to a turn that was already running before a reload/switch. */
  resumeSession: (sessionId: string, afterSeq?: number) => Promise<void>
  cancel: () => void
}

const WEB_SEARCH_KEY = "spark.webSearch"
const DRAFT_KEY = "spark.draft"

let draftSession = ""

/** sessionStore calls this whenever the active session changes.

    Reading it lazily through getState() raced the initial status fetch: the
    first keystrokes were stored under an empty session id and silently lost.
*/
export function setDraftSession(sessionId: string) {
  draftSession = sessionId || ""
}

function draftKey(): string {
  return `${DRAFT_KEY}:${draftSession}`
}

/** 草稿：刷新/切会话不丢，按会话分开存，避免把 A 的草稿带进 B。 */
function readDraft(): string {
  try {
    return localStorage.getItem(draftKey()) ?? ""
  } catch {
    return ""
  }
}

function writeDraft(text: string) {
  try {
    if (text) localStorage.setItem(draftKey(), text)
    else localStorage.removeItem(draftKey())
  } catch {
    /* storage blocked: draft lives in memory only */
  }
}

function readWebSearchPref(): boolean {
  try {
    const raw = localStorage.getItem(WEB_SEARCH_KEY)
    return raw === null ? true : raw === "1"
  } catch {
    return true
  }
}

export const useChatStore = create<ChatState>((set, get) => ({
  items: [],
  itemsBySession: {},
  input: readDraft(),
  streaming: false,
  loading: false,
  abortController: null,
  webSearch: readWebSearchPref(),
  turnGraph: { nodes: [], edges: [] },

  setTurnGraph: (g) => set({ turnGraph: g }),

  setInput: (v) => {
    writeDraft(v)
    set({ input: v })
  },
  setStreaming: (v) => set({ streaming: v }),
  setItems: (items) => set((s) => {
    const sid = useSessionStore.getState().status?.session_id
    if (!sid) return { items }
    return { items, itemsBySession: { ...s.itemsBySession, [sid]: items } }
  }),
  saveVisibleItems: (sessionId) => {
    if (!sessionId) return
    const items = get().items
    set((s) => ({ itemsBySession: { ...s.itemsBySession, [sessionId]: items } }))
  },
  setLoading: (v) => set({ loading: v }),
  setWebSearch: (v) => {
    try {
      localStorage.setItem(WEB_SEARCH_KEY, v ? "1" : "0")
    } catch {
      /* storage blocked: the toggle still works for this session */
    }
    set({ webSearch: v })
  },

  addErrorItem: (text, error) => set((s) => ({ items: [...s.items, { id: uid(), role: "error", text, segments: [], error }] })),

  lastFailedPrompt: null,
  recovering: false,

  retryLastFailed: async () => {
    const failed = get().lastFailedPrompt
    if (!failed || get().streaming) return
    // Drop the failed attempt (error card + its user bubble) before resending:
    // keeping both made a recovered turn look like the same message was sent
    // twice, and the duplicate user row is persisted server-side too.
    set((s) => {
      const items = [...s.items]
      // Remove trailing error entries.
      while (items.length > 0 && items[items.length - 1].role === "error") items.pop()
      // Remove the user bubble that produced them (last remaining user msg with
      // the same text), so the retry re-adds exactly one.
      for (let i = items.length - 1; i >= 0; i -= 1) {
        if (items[i].role === "user" && items[i].text === failed.prompt) {
          items.splice(i, 1)
          break
        }
      }
      return { items }
    })
    set({ input: failed.prompt })
    await get().send(failed.images)
  },

  cancel: () => {
    const { abortController } = get()
    if (abortController) {
      abortController.abort()
      set({ abortController: null, streaming: false })
    }
  },

  loadInitialHistory: async (autoResume = true) => {
    set({ loading: true })
    try {
      const h = await fetchHistory()
      useSessionStore.getState().setActiveGoal(h.goal?.status === "active" ? h.goal : null)
      const items = await attachReplayDurations(historyToItems(h.messages), h.session_id)
      const persistedError = useSessionStore.getState().status?.last_error
      if (persistedError && !items.some((m) => m.role === "error")) {
        items.push({
          id: `persisted-error-${persistedError.code}`,
          role: "error",
          text: persistedError.message || persistedError.title,
          segments: [],
          error: persistedError,
        })
      }
      // Draft is keyed per session, and the session id is only known now, so
      // the initial read at module load always used an empty key.
      let draft = ""
      try {
        draft = localStorage.getItem(`spark.draft:${h.session_id}`) ?? ""
      } catch {
        draft = ""
      }
      set({ items, loading: false, input: draft })
      useSessionRuntimeStore.getState().ensure(h.session_id)
      try {
        const pending = await fetchSessionPending(h.session_id)
        useSessionRuntimeStore.getState().hydratePending(h.session_id, pending)
        if (autoResume && pending.busy && !pending.approval && !pending.question) {
          void get().resumeSession(h.session_id, pending.last_seq ?? 0)
        }
      } catch {
        /* pending is best-effort; history already rendered */
      }
      useSessionStore.getState().syncPlanFromItems(items)
    } catch {
      set({ loading: false })
      useUiStore.getState().pushToast("toast.historyLoadFailed", "error")
    }
  },

  resumeSession: async (sessionId, afterSeq = 0) => {
    if (!sessionId || RESUMING.has(sessionId)) return
    const runtime = useSessionRuntimeStore.getState().runtimes[sessionId]
    if (!runtime || runtime.phase === "idle" || runtime.phase === "error") return
    RESUMING.add(sessionId)
    const ac = new AbortController()
    useSessionRuntimeStore.getState().setAbortController(sessionId, ac)
    const visible = useSessionStore.getState().status?.session_id === sessionId
    if (visible) set({ streaming: true, abortController: ac })

    const assistantId = `resume-${sessionId}-${runtime.turnId || Date.now()}`
    const startedAt = runtime.startedAt || Date.now()
    let sawDone = false
    set((s) => {
      const currentVisible = useSessionStore.getState().status?.session_id === sessionId
      const cached = s.itemsBySession[sessionId] ?? (currentVisible ? s.items : [])
      if (cached.some((m) => m.id === assistantId)) return s
      const next: ChatMsg[] = [
        ...cached,
        { id: assistantId, role: "assistant", text: "", streaming: true, segments: [], startedAt },
      ]
      const itemsBySession = { ...s.itemsBySession, [sessionId]: next }
      return currentVisible ? { items: next, itemsBySession } : { itemsBySession }
    })

    const patchResume = (fn: (it: ChatMsg) => ChatMsg) => {
      set((s) => {
        const currentVisible = useSessionStore.getState().status?.session_id === sessionId
        const source = s.itemsBySession[sessionId] ?? (currentVisible ? s.items : [])
        const idx = source.findIndex((m) => m.id === assistantId)
        if (idx < 0) return s
        const next = [...source]
        next[idx] = fn(next[idx])
        const itemsBySession = { ...s.itemsBySession, [sessionId]: next }
        return currentVisible ? { items: next, itemsBySession } : { itemsBySession }
      })
    }

    const onEvent = (ev: ChatEvent) => {
      useSessionRuntimeStore.getState().applyEvent(sessionId, ev)
      const currentVisible = useSessionStore.getState().status?.session_id === sessionId
      if (ev.type === "text_delta") {
        patchResume((it) => {
          const segs = [...closeThinking(it.segments)]
          const last = segs[segs.length - 1]
          if (last && last.kind === "text") {
            segs[segs.length - 1] = { kind: "text", text: last.text + ev.text }
          } else {
            segs.push({ kind: "text", text: ev.text })
          }
          return { ...it, segments: segs, text: it.text + ev.text, streaming: true }
        })
      } else if (ev.type === "reasoning_delta") {
        patchResume((it) => {
          const segs = [...it.segments]
          const last = segs[segs.length - 1]
          if (last && last.kind === "thinking" && last.durationMs === undefined) {
            segs[segs.length - 1] = { kind: "thinking", text: last.text + ev.text, startedAt: last.startedAt ?? Date.now() }
          } else {
            segs.push({ kind: "thinking", text: ev.text, startedAt: Date.now() })
          }
          return { ...it, segments: segs }
        })
      } else if (ev.type === "question" && currentVisible) {
        useSessionStore.getState().setQuestion({
          question: ev.data?.question ?? "",
          options: ev.data?.options ?? [],
        })
      } else if (ev.type === "approval_needed" && currentVisible) {
        useSessionStore.getState().setApproval({ info: ev.approval, tool: ev.tool })
      } else if (ev.type === "turn_end") {
        sawDone = true
        patchResume((it) => finishTurnMessage({ ...it, text: ev.text && !it.text ? ev.text : it.text }))
      } else if (ev.type === "turn_error") {
        patchResume((it) => finishTurnMessage(it))
        const text = ev.text || ev.data?.error?.message || "turn error"
        const info = ev.data?.error
        set((s) => {
          const currentVisible = useSessionStore.getState().status?.session_id === sessionId
          const source = s.itemsBySession[sessionId] ?? (currentVisible ? s.items : [])
          const last = source[source.length - 1]
          if (last?.role === "error" && last.text === text) return s
          const next: ChatMsg[] = [
            ...source,
            { id: uid(), role: "error" as const, text, segments: [], error: info },
          ]
          const itemsBySession = { ...s.itemsBySession, [sessionId]: next }
          return currentVisible ? { items: next, itemsBySession } : { itemsBySession }
        })
      } else if (ev.type === "done") {
        sawDone = true
        patchResume((it) => finishTurnMessage(it))
      }
    }

    try {
      await followSessionEvents(sessionId, afterSeq, onEvent, ac.signal)
    } catch {
      /* the endpoint may have closed; history fallback below is authoritative */
    } finally {
      const phase = useSessionRuntimeStore.getState().runtimes[sessionId]?.phase
      const settled = sawDone || phase === "idle" || phase === "error"
      // A dropped stream is not a finished turn: keep the partial bubble alive
      // so a later switch/reload can reconnect instead of showing stale text.
      if (settled) {
        patchResume((it) => (it.streaming ? finishTurnMessage(it) : it))
      }
      useSessionRuntimeStore.getState().setAbortController(sessionId, null)
      if (sawDone || phase === "idle") {
        useSessionRuntimeStore.getState().finish(sessionId)
      }
      const currentVisible = useSessionStore.getState().status?.session_id === sessionId
      try {
        if (currentVisible) {
          if (get().abortController === ac) set({ streaming: false, abortController: null })
          // Only replace the live cache with server history once the turn is
          // actually settled. Otherwise a transient SSE drop would erase the
          // partial answer and leave the runtime claiming the turn is still busy.
          if (settled) await get().loadInitialHistory(false)
          // The done frame is authoritative even if /pending still reports
          // busy for a few milliseconds while the server unwinds the turn.
          if (sawDone) useSessionRuntimeStore.getState().finish(sessionId)
        }
      } finally {
        RESUMING.delete(sessionId)
      }
    }
  },

  send: async (images: string[] = [], attachments: ChatAttachment[] = []) => {
    const { input, streaming, abortController } = get()
    const prompt = input.trim()
    if ((!prompt && images.length === 0 && attachments.length === 0) || streaming) return
    const turnSessionId = useSessionStore.getState().status?.session_id ?? ""
    if (!turnSessionId) return

    if (abortController) abortController.abort()
    const ac = new AbortController()
    useSessionRuntimeStore.getState().begin(turnSessionId, ac)
    set({ input: "", streaming: true, abortController: ac })
    const assistantId = uid()
    set((s) => {
      const items: ChatMsg[] = [
        ...s.items,
        { id: uid(), role: "user", text: prompt, images, attachments: attachments.map((f) => ({ name: f.name, kind: f.kind, size: f.size })), segments: [], createdAt: Date.now() },
        { id: assistantId, role: "assistant", text: "", streaming: true, segments: [], startedAt: Date.now(), createdAt: Date.now(), model: useSessionStore.getState().status?.model },
      ]
      return {
        items,
        itemsBySession: { ...s.itemsBySession, [turnSessionId]: items },
      }
    })

    const buf = { text: "", think: "" }
    let rafPending = false
    // A structured provider error must survive the trailing `done` frame so the
    // card can still offer "retry this request".
    let sawTurnError = false
    // 真实执行轨迹：root → 每个工具调用 → 结果。事件流就是唯一事实来源，
    // 之前执行图渲染的是写死的样例数据（"静态样例（待接 loop 事件流）"）。
    const graphNodes: GraphNode[] = [{ id: "root", label: "Spark", kind: "root", status: "running" }]
    const graphEdges: GraphEdge[] = []
    // The `task` tool is the parent of its sub-agents; remembering it lets the
    // graph draw root -> task -> subtask -> each subtask's own tools.
    let taskToolId: string | null = null
    set({ turnGraph: { sessionId: turnSessionId, nodes: [...graphNodes], edges: [] } })
    const pushGraph = () => set({ turnGraph: { sessionId: turnSessionId, nodes: [...graphNodes], edges: [...graphEdges] } })

    /** 修改本轮助手消息：始终写会话缓存；仅当该会话可见时同步视图。 */
    function patchLast(fn: (it: ChatMsg) => ChatMsg) {
      set((s) => {
        const visible = useSessionStore.getState().status?.session_id === turnSessionId
        const source = s.itemsBySession[turnSessionId] ?? (visible ? s.items : [])
        const idx = source.findIndex((m) => m.id === assistantId)
        if (idx < 0) return s
        const next = [...source]
        next[idx] = fn(next[idx])
        const itemsBySession = { ...s.itemsBySession, [turnSessionId]: next }
        return visible ? { items: next, itemsBySession } : { itemsBySession }
      })
    }

    /** 追加文本片段：若末段已是 text 则合并，否则新建；正文开始即停止思考计时 */
    function appendText(it: ChatMsg, add: string): ChatMsg {
      const segs = [...closeThinking(it.segments)]
      const last = segs[segs.length - 1]
      if (last && last.kind === "text") {
        segs[segs.length - 1] = { kind: "text", text: last.text + add }
      } else {
        segs.push({ kind: "text", text: add })
      }
      return { ...it, segments: segs, text: it.text + add }
    }

    /** 追加思考片段 */
    function appendThinking(it: ChatMsg, add: string): ChatMsg {
      const segs = [...it.segments]
      const last = segs[segs.length - 1]
      if (last && last.kind === "thinking" && last.durationMs === undefined) {
        segs[segs.length - 1] = { kind: "thinking", text: last.text + add, startedAt: last.startedAt ?? Date.now() }
      } else {
        segs.push({ kind: "thinking", text: add, startedAt: Date.now() })
      }
      return { ...it, segments: segs }
    }

    function flush() {
      rafPending = false
      if (buf.text) {
        const add = buf.text
        buf.text = ""
        patchLast((it) => appendText(it, add))
      }
      if (buf.think) {
        const add = buf.think
        buf.think = ""
        patchLast((it) => appendThinking(it, add))
      }
    }

    function schedule() {
      if (!rafPending) {
        rafPending = true
        requestAnimationFrame(flush)
      }
    }

    const onEvent = (ev: ChatEvent) => {
      // Envelope routing: the runtime for the originating session consumes
      // phase/pending/seq even when another session is currently visible.
      useSessionRuntimeStore.getState().applyEvent(turnSessionId, ev)
      const isVisible = useSessionStore.getState().status?.session_id === turnSessionId
      // Background streams update their own item cache and runtime only; never
      // paint into the currently visible conversation or its status/plan.
      if (!isVisible) {
        if (ev.type === "text_delta") {
          buf.text += ev.text
          schedule()
        } else if (ev.type === "reasoning_delta") {
          buf.think += ev.text
          schedule()
        }
        return
      }
      switch (ev.type) {
        case "text_delta":
          buf.text += ev.text
          schedule()
          break
        case "reasoning_delta":
          buf.think += ev.text
          schedule()
          break
        case "question": {
          flush()
          if (isVisible) {
            useSessionStore.getState().setQuestion({
              question: ev.data?.question ?? "",
              options: ev.data?.options ?? [],
            })
          }
          break
        }
        case "subtask_progress": {
          const idx = ev.data?.index ?? 0
          patchLast((it) => ({
            ...it,
            segments: it.segments.map((seg) =>
              seg.kind === "tool" && seg.tool.name === "task"
                ? {
                    ...seg,
                    tool: {
                      ...seg.tool,
                      subtasks: (seg.tool.subtasks ?? []).map((s) =>
                        s.index === idx
                          ? {
                              ...s,
                              phase: ev.data.phase,
                              thinkingChars: ev.data.phase === "thinking" ? ev.data.chars : s.thinkingChars,
                              answerChars: ev.data.phase === "answering" ? ev.data.chars : s.answerChars,
                            }
                          : s,
                      ),
                    },
                  }
                : seg,
            ),
          }))
          break
        }
        case "subtask_trace": {
          const idx = ev.data?.index ?? 0
          patchLast((it) => ({
            ...it,
            segments: it.segments.map((seg) =>
              seg.kind === "tool" && seg.tool.name === "task"
                ? {
                    ...seg,
                    tool: {
                      ...seg.tool,
                      subtasks: (seg.tool.subtasks ?? []).map((s) =>
                        s.index === idx
                          ? {
                              ...s,
                              phase: "done" as const,
                              thinkingChars: ev.data.thinking_chars,
                              answerChars: ev.data.answer_chars,
                              tools: ev.data.tools,
                              durationMs: s.startedAt ? Math.max(0, Date.now() - s.startedAt) : undefined,
                            }
                          : s,
                      ),
                    },
                  }
                : seg,
            ),
          }))
          break
        }
        case "subtask_start": {
          flush()
          const idx = ev.data?.index ?? 0
          patchLast((it) => ({
            ...it,
            segments: it.segments.map((seg) =>
              seg.kind === "tool" && seg.tool.name === "task"
                ? {
                    ...seg,
                    tool: {
                      ...seg.tool,
                      subtasks: [
                        ...(seg.tool.subtasks ?? []).filter((s) => s.index !== idx),
                        {
                          index: idx,
                          thinkingChars: 0,
                          answerChars: 0,
                          tools: 0,
                          phase: "thinking" as const,
                          prompt: typeof ev.data?.prompt === "string" ? ev.data.prompt : "",
                          agent: typeof ev.data?.agent === "string" ? ev.data.agent : "",
                          startedAt: Date.now(),
                        },
                      ].sort((a, b) => a.index - b.index),
                    },
                  }
                : seg,
            ),
          }))
          graphNodes.push({
            id: `agent-${idx}`,
            label: `subtask ${idx + 1}`,
            kind: "agent",
            status: "running",
          })
          graphEdges.push({ from: taskToolId ?? "root", to: `agent-${idx}` })
          pushGraph()
          break
        }
        case "subtask_end": {
          flush()
          const idx = ev.data?.index ?? 0
          const node = graphNodes.find((n) => n.id === `agent-${idx}`)
          if (node) {
            node.status = "done"
            pushGraph()
          }
          // subtask_trace 通常先到；若没有 trace（失败/提前结束），这里兜底冻结耗时。
          patchLast((it) => ({
            ...it,
            segments: it.segments.map((seg) =>
              seg.kind === "tool" && seg.tool.name === "task"
                ? {
                    ...seg,
                    tool: {
                      ...seg.tool,
                      subtasks: (seg.tool.subtasks ?? []).map((s) =>
                        s.index === idx && s.durationMs === undefined
                          ? {
                              ...s,
                              phase: "done" as const,
                              durationMs: s.startedAt ? Math.max(0, Date.now() - s.startedAt) : undefined,
                            }
                          : s,
                      ),
                    },
                  }
                : seg,
            ),
          }))
          break
        }
        case "tool_start": {
          flush()
          const parent = typeof ev.data?.subtask_index === "number" ? `agent-${ev.data.subtask_index}` : "root"
          graphNodes.push({
            id: `tool-${ev.tool.id}`,
            label: ev.tool.name,
            kind: "tool",
            status: "running",
          })
          graphEdges.push({ from: parent, to: `tool-${ev.tool.id}` })
          if (ev.tool.name === "task") taskToolId = `tool-${ev.tool.id}`
          pushGraph()
          patchLast((it) => ({
            ...it,
            segments: [
              ...closeThinking(it.segments),
              {
                kind: "tool",
                tool: { id: ev.tool.id, name: ev.tool.name, arguments: ev.tool.arguments, status: "running", startedAt: Date.now() },
              },
            ],
          }))
          break
        }
        case "tool_end": {
          flush()
          const node = graphNodes.find((n) => n.id === `tool-${ev.tool.id}`)
          if (node) {
            node.status = ev.ok ? "done" : "error"
            pushGraph()
          }
          patchLast((it) => ({
            ...it,
            segments: it.segments.map((seg) =>
              seg.kind === "tool" && seg.tool.id === ev.tool.id
                ? {
                    ...seg,
                    tool: {
                      ...seg.tool,
                      status: ev.ok ? "ok" : "error",
                      result: ev.result,
                      durationMs: seg.tool.startedAt ? Date.now() - seg.tool.startedAt : undefined,
                    },
                  }
                : seg,
            ),
          }))
          break
        }
        case "approval_needed":
          if (isVisible) {
            useSessionStore.getState().setApproval({ info: ev.approval, tool: ev.tool })
          }
          break
        case "compaction": {
          if (ev.data?.usage) {
            useSessionStore.getState().patchStatus({ context: ev.data.usage })
          }
          if (ev.data?.before_tokens) {
            flush()
            const lang = useUiStore.getState().lang
            const text = t("context.compacted", lang, {
              before: ev.data.before_tokens ?? 0,
              after: ev.data.after_tokens ?? 0,
              summarized: ev.data.summarized_messages ?? 0,
            })
            set((s) => ({
              items: [
                ...s.items,
                {
                  id: uid(),
                  role: "notice",
                  text,
                  segments: [],
                  meta: {
                    compacted: {
                      before: ev.data.before_tokens ?? 0,
                      after: ev.data.after_tokens ?? 0,
                      summarized: ev.data.summarized_messages ?? 0,
                    },
                  },
                },
              ],
            }))
          }
          break
        }
        case "context": {
          if (ev.data?.usage) {
            useSessionStore.getState().patchStatus({ context: ev.data.usage })
          }
          break
        }
        case "plan": {
          if (ev.data?.steps) {
            const steps = ev.data.steps
            useSessionStore.getState().setPlan(steps)
            patchLast((it) => {
              const segs = [...it.segments]
              const last = segs[segs.length - 1]
              if (last && last.kind === "plan") {
                segs[segs.length - 1] = { kind: "plan", steps }
              } else {
                segs.push({ kind: "plan", steps })
              }
              return { ...it, segments: segs }
            })
          }
          break
        }
        case "approval_changed": {
          // /plan /build：切换只读计划档 / 回到构建档（后端改的是会话内 override）
          flush()
          const label = ev.data?.label || ""
          useSessionStore.getState().patchStatus?.({ approval: ev.data?.approval })
          if (label) {
            set((s) => ({
              items: [...s.items, { id: uid(), role: "notice" as const, text: label, segments: [] }],
            }))
          }
          break
        }
        case "side_answer": {
          // /side <问题>：旁支一次性回答，独立卡片，不污染主对话
          flush()
          const d = ev.data || {}
          set((s) => ({
            items: [
              ...s.items,
              {
                id: uid(),
                role: "assistant" as const,
                text: d.answer || "",
                segments: [
                  { kind: "side" as const, question: d.question || "", answer: d.answer || "", model: d.model || "", ok: !!d.ok },
                ],
              },
            ],
          }))
          break
        }
        case "goal_status": {
          // /goal：设立/查询/收口长程目标；goal 为 null = 无活跃目标
          flush()
          const goal = ev.data?.goal
          useSessionStore.getState().setActiveGoal(goal?.status === "active" ? goal : null)
          if (!goal) {
            set((s) => ({
              items: [...s.items, { id: uid(), role: "notice" as const, text: t("goal.none", useUiStore.getState().lang), segments: [] }],
            }))
            break
          }
          set((s) => ({
            items: [
              ...s.items,
              {
                id: uid(),
                role: "assistant" as const,
                text: `🎯 ${goal.objective}`,
                segments: [{ kind: "goal" as const, goal, label: ev.data?.label }],
              },
            ],
          }))
          break
        }
        case "goal_auto_continue": {
          // Goal 自动续跑：同一条 SSE 流里继续下一回合，给用户一个明确标记
          flush()
          const turn = ev.data?.turn ?? 1
          const max = ev.data?.max ?? 1
          set((s) => ({
            items: [
              ...s.items,
              {
                id: uid(),
                role: "notice" as const,
                text: t("goal.autoContinue", useUiStore.getState().lang, { turn, max }),
                segments: [],
              },
            ],
          }))
          break
        }
        case "review": {
          // 自动审查（Guardian）：只在拦截时打扰用户，降级时提示一次。
          flush()
          const verdict = ev.data?.verdict || ""
          const reason = ev.data?.reason || ""
          if (verdict === "block") {
            set((s) => ({
              items: [...s.items, { id: uid(), role: "notice" as const, text: t("review.blocked", useUiStore.getState().lang, { reason }), segments: [] }],
            }))
          } else if (ev.data?.degraded) {
            set((s) => ({
              items: [...s.items, { id: uid(), role: "notice" as const, text: t("review.degraded", useUiStore.getState().lang, { reason }), segments: [] }],
            }))
          }
          break
        }
        case "turn_end":
          flush()
          graphNodes[0].status = "done"
          graphNodes.push({ id: "result", label: "result", kind: "result", status: "done" })
          for (const n of graphNodes) {
            if (n.kind === "tool") graphEdges.push({ from: n.id, to: "result" })
          }
          graphEdges.push({ from: "root", to: "result" })
          pushGraph()
          patchLast((it) => finishTurnMessage({ ...it, text: ev.text && !it.text ? ev.text : it.text }))
          break
        case "turn_error":
          flush()
          sawTurnError = true
          graphNodes[0].status = "error"
          pushGraph()
          patchLast((it) => finishTurnMessage(it))
          set({ lastFailedPrompt: { prompt, images, attachments } })
          set((s) => {
            const items: ChatMsg[] = [
              ...s.items,
              {
                id: uid(),
                role: "error" as const,
                text: ev.text || ev.data?.error?.message || "turn error",
                segments: [],
                error: ev.data?.error,
              },
            ]
            // Keep the per-session cache aligned: a later `done` frame calls
            // patchLast(), which rebuilds `items` from itemsBySession and would
            // otherwise erase the error card that was just appended.
            return { items, itemsBySession: { ...s.itemsBySession, [turnSessionId]: items } }
          })
          break
        case "done":
          flush()
          patchLast((it) => finishTurnMessage(it))
          break
      }
    }

    /** After a dropped POST stream, attach to the session's replay/tail SSE. */
    async function recoverTurn(sessionId: string, afterSeq: number): Promise<boolean> {
      set({ recovering: true })
      try {
        await followSessionEvents(sessionId, afterSeq, onEvent, ac.signal)
        const phase = useSessionRuntimeStore.getState().runtimes[sessionId]?.phase
        return phase === "idle" || phase === "error"
      } catch {
        return false
      } finally {
        set({ recovering: false })
      }
    }

    try {
      await streamChat(prompt, onEvent, ac.signal, images, get().webSearch, attachments)
      flush()
      if (!sawTurnError) set({ lastFailedPrompt: null })
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      flush()
      const isAbort = e instanceof DOMException && e.name === "AbortError"
      if (!isAbort) {
        const runtime = useSessionRuntimeStore.getState().runtimes[turnSessionId]
        if (runtime && runtime.lastSeq > 0 && !ac.signal.aborted) {
          try {
            const recovered = await recoverTurn(turnSessionId, runtime.lastSeq)
            if (recovered && !ac.signal.aborted) {
              flush()
              set({ lastFailedPrompt: null })
              return
            }
          } catch {
            /* fall through to the normal failure path */
          }
        }
        useSessionRuntimeStore.getState().fail(turnSessionId, msg)
        const visible = useSessionStore.getState().status?.session_id === turnSessionId
        if (visible) {
          set({ lastFailedPrompt: { prompt, images, attachments } })
          set((s) => ({
            items: s.items.filter((m) => !(m.id === assistantId && !m.text && !m.segments.length)),
          }))
          get().addErrorItem(msg)
        } else {
          // Keep the background session's cache tidy without touching the
          // visible conversation; its runtime carries the error badge.
          set((s) => {
            const cached = s.itemsBySession[turnSessionId] ?? []
            const next = cached.filter((m) => m.id !== assistantId || m.text || m.segments.length)
            return { itemsBySession: { ...s.itemsBySession, [turnSessionId]: next } }
          })
        }
        // api.ts already surfaced a toast for transport failures; only the
        // higher-level "send failed" toast is added here for other errors.
      }
    } finally {
      // Any termination path ends the stream: an aborted or failed turn used to
      // leave the bubble blinking "generating" forever because only turn_end /
      // done cleared the flag.
      patchLast((it) => (it.streaming ? finishTurnMessage(it) : it))
      const runtime = useSessionRuntimeStore.getState().runtimes[turnSessionId]
      if (runtime?.phase !== "error") {
        useSessionRuntimeStore.getState().finish(turnSessionId)
      }
      // Guard against a superseded turn: if the user already started a new one,
      // this late-completing stream must not clear the new turn's controller or
      // streaming flag (that made Stop disappear and allowed a third concurrent
      // send).
      if (get().abortController === ac) {
        set({ streaming: false, abortController: null })
        if (useSessionStore.getState().status?.session_id === turnSessionId) {
          useSessionStore.getState().setApproval(null)
        }
      }
      await useSessionStore.getState().refreshSessions()
      // Attach database ids to the messages just produced. Without this the
      // delete / edit / regenerate actions stayed disabled on every message
      // sent in this page load, until a manual refresh. Only ids are copied:
      // replacing the list would throw away reasoning/tool segments, which the
      // server does not persist.
      if (useSessionStore.getState().status?.session_id === turnSessionId) {
        try {
          const h = await fetchHistory()
          if (!get().streaming) {
            const ids = h.messages
              .filter((m) => (m.role === "user" || m.role === "assistant") && m.id)
              .map((m) => ({ role: m.role, id: m.id as number }))
            set((s) => {
              let cursor = 0
              let changed = false
              const items = s.items.map((it) => {
                if (it.role !== "user" && it.role !== "assistant") return it
                while (cursor < ids.length && ids[cursor].role !== it.role) cursor += 1
                if (cursor >= ids.length) return it
                const id = ids[cursor].id
                cursor += 1
                if (it.dbId === id) return it
                changed = true
                return { ...it, dbId: id }
              })
              return changed ? { items } : s
            })
          }
        } catch {
          /* ids are a convenience; the next explicit action re-reads history */
        }
      }
    }
  },

  runShell: async (command: string) => {
    const { streaming } = get()
    if (streaming || !command.trim()) return
    const id = uid()
    set((s) => ({
      input: "",
      items: [...s.items, { id, role: "shell", text: `!${command}`, segments: [], meta: { shell: { command, exitCode: -1, stdout: "", stderr: "" } } }],
    }))
    try {
      const res = await runInlineShell(command)
      set((s) => ({
        items: s.items.map((it) =>
          it.id === id
            ? {
                ...it,
                meta: {
                  shell: {
                    command,
                    exitCode: res.result.exit_code ?? -1,
                    stdout: res.result.stdout ?? "",
                    stderr: res.result.stderr ?? "",
                    truncated: res.result.truncated,
                  },
                },
              }
            : it,
        ),
      }))
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      set((s) => ({
        items: s.items.map((it) =>
          it.id === id
            ? { ...it, meta: { shell: { command, exitCode: -1, stdout: "", stderr: msg } } }
            : it,
        ),
      }))
    }
  },

  feedbackMessage: async (dbId: number, rating: "up" | "down") => {
    const target = get().items.find((it) => it.dbId === dbId)
    if (!target) return
    const previous = target.feedback
    const next = previous === rating ? undefined : rating
    set((s) => ({ items: s.items.map((it) => (it.dbId === dbId ? { ...it, feedback: next } : it)) }))
    try {
      await setMessageFeedback(dbId, next ?? null)
    } catch (e) {
      set((s) => ({ items: s.items.map((it) => (it.dbId === dbId ? { ...it, feedback: previous } : it)) }))
      useUiStore.getState().pushToast("toast.feedbackFailed", "error")
      console.error(e)
    }
  },

  deleteMessage: async (dbId: number) => {
    if (get().streaming) return
    try {
      const data = await deleteMessageApi(dbId)
      set({ items: await attachReplayDurations(historyToItems(data.messages)) })
      useSessionStore.getState().setStatus(data.status)
      await useSessionStore.getState().refreshSessions()
      useUiStore.getState().pushToast("toast.messageDeleted", "success")
    } catch {
      useUiStore.getState().pushToast("toast.messageDeleteFailed", "error")
    }
  },

  startEditMessage: async (userDbId: number) => {
    if (get().streaming) return
    const msg = get().items.find((m) => m.dbId === userDbId)
    if (!msg) return
    try {
      // 真删而不是"只回填输入框"：否则用户改完发送后会变成同一句话的两条记录。
      const data = await truncateFromMessageApi(userDbId)
      set({ items: await attachReplayDurations(historyToItems(data.messages)), input: msg.text })
      useSessionStore.getState().setStatus(data.status)
      await useSessionStore.getState().refreshSessions()
    } catch {
      useUiStore.getState().pushToast("toast.editFailed", "error")
    }
  },

  regenerate: async (assistantDbId: number) => {
    if (get().streaming) return
    const items = get().items
    const index = items.findIndex((m) => m.dbId === assistantDbId)
    if (index < 0) return
    let userIndex = -1
    for (let i = index - 1; i >= 0; i -= 1) {
      if (items[i].role === "user") {
        userIndex = i
        break
      }
    }
    if (userIndex < 0) return
    const user = items[userIndex]
    if (!user.dbId) {
      useUiStore.getState().pushToast("toast.regenerateUnavailable", "error")
      return
    }
    try {
      // 删除该轮用户消息及其后全部内容，再用同一 prompt 重跑：后端每轮都会
      // 重新落一条 user 行，因此必须先截断而不是"只删助手回复"。
      const data = await truncateFromMessageApi(user.dbId)
      set({ items: await attachReplayDurations(historyToItems(data.messages)) })
      useSessionStore.getState().setStatus(data.status)
      set({ input: user.text })
      await get().send(user.images ?? [])
    } catch {
      useUiStore.getState().pushToast("toast.regenerateFailed", "error")
    }
  },
}))

/**
 * 历史消息本身不带耗时；从当前会话的 replay 摘要里按“用户 prompt + 助手
 * 文本前缀”回填本轮 duration_ms。刷新后仍能看到“耗时 3.1s”，但没匹配上
 * 的轮次宁可不显示，也不猜一个数字。
 */
export async function attachReplayDurations(items: ChatMsg[], sessionId?: string): Promise<ChatMsg[]> {
  if (!items.some((m) => m.role === "assistant" && m.text)) return items
  try {
    const data = await fetchReplays()
    if (sessionId && data.session_id && data.session_id !== sessionId) return items
    const turns = data.turns ?? []
    if (turns.length === 0) return items
    const used = new Set<number>()
    const pendingUserTimes = new Map<number, number>()
    let prompt = ""
    let lastUserIndex = -1
    const out = items.map((it, index) => {
      if (it.role === "user") {
        prompt = it.text.trim()
        lastUserIndex = index
        return it
      }
      if (it.role !== "assistant" || !it.text || !prompt) return it
      const candidates = turns.filter(
        (t) => !used.has(t.index) && t.duration_ms !== undefined && t.prompt.trim() === prompt,
      )
      const match =
        candidates.find((t) => {
          const preview = (t.text_preview ?? "").trim()
          return !preview || it.text.startsWith(preview)
        }) ?? candidates[0]
      if (!match || match.duration_ms === undefined) return it
      used.add(match.index)
      const createdAt = it.createdAt ?? (match.recorded_at ? match.recorded_at * 1000 : undefined)
      // 不能在这里读 out：map 还在构造 out，访问会触发 TDZ 并让整个回填被 catch。
      if (lastUserIndex >= 0 && createdAt && !items[lastUserIndex].createdAt) {
        pendingUserTimes.set(lastUserIndex, createdAt)
      }
      return {
        ...it,
        durationMs: match.duration_ms,
        model: match.model || it.model,
        usage: match.usage || it.usage,
        createdAt,
      }
    })
    for (const [index, createdAt] of pendingUserTimes) {
      out[index] = { ...out[index], createdAt }
    }
    return out
  } catch {
    // replay 是增强信息，读取失败不影响历史正文。
    return items
  }
}

/** 后端 history（user/assistant/tool 平铺）→ 前端分组 + 顺序片段模型 */
export function historyToItems(
  msgs: Array<{
    role: string
    content: string
    name?: string
    images?: string[]
    attachments?: string[]
    id?: number
    tool_call_id?: string
    tool_calls?: Array<{ id: string; name: string; arguments: unknown }>
    feedback?: "up" | "down"
  }>,
): ChatMsg[] {
  const out: ChatMsg[] = []
  // Tool results are matched to the pending call by id so the reloaded card
  // keeps the arguments it was created with (this used to render `{}`).
  const pending = new Map<string, ToolCallState>()
  for (const m of msgs) {
    if (m.role === "tool") {
      const matched = m.tool_call_id ? pending.get(m.tool_call_id) : undefined
      if (matched) {
        matched.status = "ok"
        matched.result = m.content
        pending.delete(m.tool_call_id as string)
        continue
      }
      const tool: ToolCallState = {
        id: `h-${out.length}-${Date.now()}`,
        name: m.name || "tool",
        arguments: {},
        status: "ok",
        startedAt: 0,
        result: m.content,
      }
      const last = out[out.length - 1]
      if (last && last.role === "assistant") {
        last.segments.push({ kind: "tool", tool })
      } else {
        out.push({ id: uid(), role: "assistant", text: "", segments: [{ kind: "tool", tool }] })
      }
    } else if (m.role === "assistant" && m.tool_calls && m.tool_calls.length > 0) {
      const msgId = uid()
      const segs: Segment[] = []
      if (m.content) segs.push({ kind: "text", text: m.content })
      for (const call of m.tool_calls) {
        const tool: ToolCallState = {
          id: call.id || `h-${msgId}-${segs.length}`,
          name: call.name,
          arguments: call.arguments,
          status: "running",
          startedAt: 0,
        }
        segs.push({ kind: "tool", tool })
        pending.set(tool.id, tool)
      }
      out.push({ id: msgId, role: "assistant", text: m.content, segments: segs, dbId: m.id, feedback: m.feedback })
    } else if (m.role === "user") {
      out.push({
        id: uid(),
        role: "user",
        text: m.content,
        images: m.images,
        attachments: (m.attachments ?? []).map((name) => ({ name })),
        segments: [],
        dbId: m.id,
      })
    } else {
      out.push({
        id: uid(),
        role: "assistant",
        text: m.content,
        segments: [{ kind: "text", text: m.content }],
        dbId: m.id,
        feedback: m.feedback,
      })
    }
  }
  return out
}
