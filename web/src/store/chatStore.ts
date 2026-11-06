import { create } from "zustand"
import type { ChatEvent, GoalState, PlanStep } from "../types"
import { streamChat, fetchHistory, deleteMessage as deleteMessageApi, truncateFromMessage as truncateFromMessageApi, runInlineShell } from "../api"
import { useSessionStore } from "./sessionStore"
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
  | { kind: "thinking"; text: string }
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
  streaming?: boolean
  segments: Segment[]
  /** 后端消息行 id（编辑/删除/重试需要精确锚点）；流式新消息在刷新前没有 */
  dbId?: number
  /** 时间戳（真实事件流画执行图用） */
  startedAt?: number
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
}

export function uid(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return `m-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
  }
}

interface ChatState {
  items: ChatMsg[]
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
  setLoading: (v: boolean) => void
  setWebSearch: (v: boolean) => void
  addErrorItem: (text: string) => void
  send: (images?: string[]) => Promise<void>
  /** `!cmd` 内联 shell：本地执行并渲染结果卡片，不进入对话历史 */
  runShell: (command: string) => Promise<void>
  /** 删除单条消息（后端持久化 + 本地同步） */
  deleteMessage: (dbId: number) => Promise<void>
  /** 重新生成：截断到该助手消息之前，重放它对应的用户消息 */
  regenerate: (assistantDbId: number) => Promise<void>
  /** 编辑重发：截断到该用户消息之前，并把原文放回输入框 */
  startEditMessage: (userDbId: number) => Promise<void>
  /** 传输失败时记住原 prompt，供「重试」按钮一键重发 */
  lastFailedPrompt: { prompt: string; images: string[] } | null
  retryLastFailed: () => Promise<void>
  loadInitialHistory: () => Promise<void>
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
  setItems: (items) => set({ items }),
  setLoading: (v) => set({ loading: v }),
  setWebSearch: (v) => {
    try {
      localStorage.setItem(WEB_SEARCH_KEY, v ? "1" : "0")
    } catch {
      /* storage blocked: the toggle still works for this session */
    }
    set({ webSearch: v })
  },

  addErrorItem: (text) => set((s) => ({ items: [...s.items, { id: uid(), role: "error", text, segments: [] }] })),

  lastFailedPrompt: null,

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

  loadInitialHistory: async () => {
    set({ loading: true })
    try {
      const h = await fetchHistory()
      const items = historyToItems(h.messages)
      // Draft is keyed per session, and the session id is only known now, so
      // the initial read at module load always used an empty key.
      let draft = ""
      try {
        draft = localStorage.getItem(`spark.draft:${h.session_id}`) ?? ""
      } catch {
        draft = ""
      }
      set({ items, loading: false, input: draft })
      useSessionStore.getState().syncPlanFromItems(items)
    } catch {
      set({ loading: false })
      useUiStore.getState().pushToast("toast.historyLoadFailed", "error")
    }
  },

  send: async (images: string[] = []) => {
    const { input, streaming, abortController } = get()
    const prompt = input.trim()
    if ((!prompt && images.length === 0) || streaming) return

    if (abortController) abortController.abort()
    const ac = new AbortController()
    set({ input: "", streaming: true, abortController: ac })
    const assistantId = uid()
    set((s) => ({
      items: [
        ...s.items,
        { id: uid(), role: "user", text: prompt, images, segments: [] },
        { id: assistantId, role: "assistant", text: "", streaming: true, segments: [] },
      ],
    }))

    const buf = { text: "", think: "" }
    let rafPending = false
    // 真实执行轨迹：root → 每个工具调用 → 结果。事件流就是唯一事实来源，
    // 之前执行图渲染的是写死的样例数据（"静态样例（待接 loop 事件流）"）。
    const graphNodes: GraphNode[] = [{ id: "root", label: "Spark", kind: "root", status: "running" }]
    const graphEdges: GraphEdge[] = []
    // The `task` tool is the parent of its sub-agents; remembering it lets the
    // graph draw root -> task -> subtask -> each subtask's own tools.
    let taskToolId: string | null = null
    set({ turnGraph: { nodes: [...graphNodes], edges: [] } })
    const pushGraph = () => set({ turnGraph: { nodes: [...graphNodes], edges: [...graphEdges] } })

    /** 修改当前助手消息；无则 no-op */
    function patchLast(fn: (it: ChatMsg) => ChatMsg) {
      set((s) => {
        const idx = s.items.findIndex((m) => m.id === assistantId)
        if (idx < 0) return s
        const next = [...s.items]
        next[idx] = fn(next[idx])
        return { items: next }
      })
    }

    /** 追加文本片段：若末段已是 text 则合并，否则新建 */
    function appendText(it: ChatMsg, add: string): ChatMsg {
      const segs = [...it.segments]
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
      if (last && last.kind === "thinking") {
        segs[segs.length - 1] = { kind: "thinking", text: last.text + add }
      } else {
        segs.push({ kind: "thinking", text: add })
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
          useSessionStore.getState().setQuestion({
            question: ev.data?.question ?? "",
            options: ev.data?.options ?? [],
          })
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
              ...it.segments,
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
          useSessionStore.getState().setApproval({ info: ev.approval, tool: ev.tool })
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
        case "turn_end":
          flush()
          graphNodes[0].status = "done"
          graphNodes.push({ id: "result", label: "result", kind: "result", status: "done" })
          for (const n of graphNodes) {
            if (n.kind === "tool") graphEdges.push({ from: n.id, to: "result" })
          }
          graphEdges.push({ from: "root", to: "result" })
          pushGraph()
          patchLast((it) => ({
            ...it,
            streaming: false,
            text: ev.text && !it.text ? ev.text : it.text,
          }))
          break
        case "turn_error":
          flush()
          graphNodes[0].status = "error"
          pushGraph()
          set((s) => ({ items: [...s.items, { id: uid(), role: "error", text: ev.text || "turn error", segments: [] }] }))
          break
        case "done":
          flush()
          patchLast((it) => ({ ...it, streaming: false }))
          break
      }
    }

    try {
      await streamChat(prompt, onEvent, ac.signal, images, get().webSearch)
      flush()
      set({ lastFailedPrompt: null })
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      flush()
      const isAbort = e instanceof DOMException && e.name === "AbortError"
      if (!isAbort) {
        set({
          lastFailedPrompt: { prompt, images },
        })
        set((s) => ({
          items: s.items.filter((m) => !(m.id === assistantId && !m.text && !m.segments.length)),
        }))
        get().addErrorItem(msg)
        // api.ts already surfaced a toast for transport failures; only the
        // higher-level "send failed" toast is added here for other errors.
      }
    } finally {
      // Any termination path ends the stream: an aborted or failed turn used to
      // leave the bubble blinking "generating" forever because only turn_end /
      // done cleared the flag.
      patchLast((it) => (it.streaming ? { ...it, streaming: false } : it))
      // Guard against a superseded turn: if the user already started a new one,
      // this late-completing stream must not clear the new turn's controller or
      // streaming flag (that made Stop disappear and allowed a third concurrent
      // send).
      if (get().abortController === ac) {
        set({ streaming: false, abortController: null })
        useSessionStore.getState().setApproval(null)
      }
      await useSessionStore.getState().refreshSessions()
      // Attach database ids to the messages just produced. Without this the
      // delete / edit / regenerate actions stayed disabled on every message
      // sent in this page load, until a manual refresh. Only ids are copied:
      // replacing the list would throw away reasoning/tool segments, which the
      // server does not persist.
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

  deleteMessage: async (dbId: number) => {
    if (get().streaming) return
    try {
      const data = await deleteMessageApi(dbId)
      set({ items: historyToItems(data.messages) })
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
      set({ items: historyToItems(data.messages), input: msg.text })
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
      set({ items: historyToItems(data.messages) })
      useSessionStore.getState().setStatus(data.status)
      set({ input: user.text })
      await get().send(user.images ?? [])
    } catch {
      useUiStore.getState().pushToast("toast.regenerateFailed", "error")
    }
  },
}))

/** 后端 history（user/assistant/tool 平铺）→ 前端分组 + 顺序片段模型 */
export function historyToItems(
  msgs: Array<{
    role: string
    content: string
    name?: string
    images?: string[]
    id?: number
    tool_call_id?: string
    tool_calls?: Array<{ id: string; name: string; arguments: unknown }>
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
      out.push({ id: msgId, role: "assistant", text: m.content, segments: segs, dbId: m.id })
    } else if (m.role === "user") {
      out.push({ id: uid(), role: "user", text: m.content, images: m.images, segments: [], dbId: m.id })
    } else {
      out.push({
        id: uid(),
        role: "assistant",
        text: m.content,
        segments: [{ kind: "text", text: m.content }],
        dbId: m.id,
      })
    }
  }
  return out
}
