import { create } from "zustand"
import type { ApprovalInfo, PlanStep, SessionRow, Status, ToolCall } from "../types"
import { listSessions, newSession, switchSession, deleteSession, cancelTurn, respondApproval, forkSession, answerQuestion, fetchSessionPending } from "../api"
import { useChatStore, historyToItems, attachReplayDurations, setDraftSession, type ChatMsg } from "./chatStore"
import { useSessionRuntimeStore } from "./sessionRuntime"
import { useUiStore } from "./uiStore"

export interface ApprovalState {
  info: ApprovalInfo
  tool: ToolCall
}

export interface QuestionState {
  question: string
  options: string[]
}

interface SessionState {
  status: Status | null
  sessions: SessionRow[]
  approval: ApprovalState | null
  approvalBusy: boolean
  question: QuestionState | null
  plan: PlanStep[]
  setStatus: (s: Status | null) => void
  patchStatus: (patch: Partial<Status>) => void
  setApproval: (a: ApprovalState | null) => void
  setQuestion: (q: QuestionState | null) => void
  handleAnswer: (answer: string) => Promise<void>
  setPlan: (p: PlanStep[]) => void
  syncPlanFromItems: (items: ChatMsg[]) => void
  refreshSessions: () => Promise<void>
  handleNewSession: () => Promise<void>
  handleSwitch: (id: string) => Promise<void>
  handleDelete: (id: string) => Promise<void>
  handleFork: (id?: string) => Promise<void>
  handleCancel: () => Promise<void>
  handleApproval: (decision: "allow" | "allow_always" | "deny") => Promise<void>
  /** Rebuild approval/question after a full page load, without re-opening a busy stream. */
  restorePending: () => Promise<void>
}

export const useSessionStore = create<SessionState>((set, get) => {
  let approvalBusyRef = false

  /** Rebuild pending approval/question UI from the backend after switch/refresh. */
  async function hydratePending(sessionId: string, resumeBusy = true) {
    try {
      const pending = await fetchSessionPending(sessionId)
      useSessionRuntimeStore.getState().hydratePending(sessionId, pending)
      if (get().status?.session_id !== sessionId) return
      if (pending.approval) {
        const p = pending.approval
        set({
          approval: {
            info: {
              summary: p.summary,
              diff: p.diff ?? null,
              id: p.tool_call_id,
              tool: p.tool,
              arguments: p.arguments,
            },
            tool: { id: p.tool_call_id, name: p.tool, arguments: p.arguments },
          },
          question: null,
        })
      } else {
        set({ approval: null })
      }
      set({ question: pending.question ? { question: pending.question.question, options: pending.question.options } : null })
      if (resumeBusy && pending.busy && !pending.approval && !pending.question) {
        void useChatStore.getState().resumeSession(sessionId, pending.last_seq ?? 0)
      }
    } catch {
      /* pending is best-effort; switching/history already succeeded */
    }
  }

  /**
   * The visible turn is the visible session's turn, not a global "streaming"
   * flag. Switching to a background session must not leave its Stop button
   * hidden, and switching to an idle session must not leave it disabled.
   */
  function syncVisibleStreaming(sessionId: string) {
    const rt = useSessionRuntimeStore.getState().runtimes[sessionId]
    const active =
      !!rt &&
      (rt.phase === "streaming" ||
        rt.phase === "waiting_approval" ||
        rt.phase === "waiting_question")
    useChatStore.setState({
      streaming: active,
      abortController: active ? rt.abortController : null,
    })
  }

  return {
    status: null,
    sessions: [],
    approval: null,
    approvalBusy: false,
    question: null,
    plan: [],

    setStatus: (s) => {
      setDraftSession(s?.session_id ?? "")
      if (s?.session_id) useSessionRuntimeStore.getState().ensure(s.session_id)
      set({ status: s })
    },
    patchStatus: (patch) => set((s) => ({ status: s.status ? { ...s.status, ...patch } : s.status })),
    setApproval: (a) => set({ approval: a }),
    setQuestion: (q) => set({ question: q }),
    handleAnswer: async (answer) => {
      const sid = get().status?.session_id
      try {
        await answerQuestion(answer, sid)
        if (sid) {
          useSessionRuntimeStore.getState().setPendingQuestion(sid, null)
          const rt = useSessionRuntimeStore.getState().runtimes[sid]
          if (!useChatStore.getState().streaming && rt) {
            void useChatStore.getState().resumeSession(sid, rt.lastSeq)
          }
        }
        set({ question: null })
      } catch {
        useUiStore.getState().pushToast("toast.answerFailed", "error")
      }
    },
    setPlan: (p) => set({ plan: p }),

    syncPlanFromItems: (items) => {
      for (let i = items.length - 1; i >= 0; i -= 1) {
        for (let j = items[i].segments.length - 1; j >= 0; j -= 1) {
          const seg = items[i].segments[j]
          if (seg.kind === "plan" && seg.steps.length) {
            set({ plan: seg.steps })
            return
          }
        }
      }
    },

    refreshSessions: async () => {
      try {
        const data = await listSessions()
        set({ sessions: data.sessions })
      } catch (e) {
        useUiStore.getState().pushToast("toast.sessionLoadFailed", "error")
        void e
      }
    },

    restorePending: async () => {
      const sessionId = get().status?.session_id
      if (!sessionId) return
      await hydratePending(sessionId, false)
    },

    handleNewSession: async () => {
      const previous = get().status?.session_id
      if (previous) useChatStore.getState().saveVisibleItems(previous)
      try {
        const st = await newSession()
        useSessionRuntimeStore.getState().ensure(st.session_id)
        set({ status: st, plan: st.plan || [], approval: null, question: null })
        syncVisibleStreaming(st.session_id)
        const chat = useChatStore.getState()
        chat.setItems([])
        chat.setInput(localStorage.getItem(`spark.draft:${st.session_id}`) ?? "")
        await get().refreshSessions()
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        useChatStore.getState().addErrorItem(msg)
        useUiStore.getState().pushToast("toast.newSessionFailed", "error")
      }
    },

    handleSwitch: async (id: string) => {
      const { status: currentStatus } = get()
      if (!currentStatus || id === currentStatus.session_id) return
      useChatStore.getState().saveVisibleItems(currentStatus.session_id)
      try {
        const data = await switchSession(id)
        set({ status: data.status, plan: data.status.plan || [] })
        syncVisibleStreaming(id)
        const chat = useChatStore.getState()
        // Prefer the cache only while this session still has a live turn;
        // once it is idle, server history is richer (tool blocks, db ids).
        const runtime = useSessionRuntimeStore.getState().runtimes[id]
        const live = runtime && (runtime.phase === "streaming" || runtime.phase === "waiting_approval" || runtime.phase === "waiting_question")
        const cached = chat.itemsBySession[id]
        const items =
          live && cached ? cached : await attachReplayDurations(historyToItems(data.messages), id)
        chat.setItems(items)
        // Pick up this session's own draft (empty for a session never typed in).
        chat.setInput(localStorage.getItem(`spark.draft:${id}`) ?? "")
        await hydratePending(id)
        await get().refreshSessions()
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        useChatStore.getState().addErrorItem(msg)
        useUiStore.getState().pushToast("toast.switchSessionFailed", "error")
      }
    },

    handleDelete: async (id: string) => {
      const { status: currentStatus } = get()
      if (!currentStatus) return
      try {
        const data = await deleteSession(id)
        useSessionRuntimeStore.getState().remove(id)
        if (data.status) set({ status: data.status })
        if (id === currentStatus.session_id) {
          const chat = useChatStore.getState()
          chat.setItems([])
          set({ plan: data.status?.plan || [] })
          if (data.status?.session_id) {
            useSessionRuntimeStore.getState().ensure(data.status.session_id)
            syncVisibleStreaming(data.status.session_id)
            await hydratePending(data.status.session_id)
          }
        }
        await get().refreshSessions()
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        void msg
        useUiStore.getState().pushToast("toast.deleteSessionFailed", "error")
      }
    },

    handleFork: async (id) => {
      try {
        const current = get().status?.session_id
        if (current) useChatStore.getState().saveVisibleItems(current)
        const data = await forkSession(id ?? current)
        useSessionRuntimeStore.getState().ensure(data.status.session_id)
        set({ status: data.status, plan: data.status.plan || [] })
        syncVisibleStreaming(data.status.session_id)
        const items = historyToItems(data.messages)
        useChatStore.getState().setItems(items)
        set({ approval: null, question: null })
        useUiStore.getState().pushToast("toast.forkDone", "success")
        await get().refreshSessions()
      } catch {
        useUiStore.getState().pushToast("toast.forkFailed", "error")
      }
    },

    handleCancel: async () => {
      // Abort the local SSE request first so the UI stops immediately, then ask
      // the server to stop the turn. The runtime is addressed by session id so
      // a background conversation can be stopped without switching to it first.
      const sid = get().status?.session_id
      if (sid) useSessionRuntimeStore.getState().abort(sid)
      useChatStore.getState().cancel()
      try {
        await cancelTurn(sid)
      } catch {
        useUiStore.getState().pushToast("toast.cancelFailed", "error")
      }
    },

    handleApproval: async (decision) => {
      if (approvalBusyRef) return
      approvalBusyRef = true
      set({ approvalBusy: true })
      const sid = get().status?.session_id
      try {
        await respondApproval(decision, sid)
        if (sid) {
          useSessionRuntimeStore.getState().setPendingApproval(sid, null)
          const rt = useSessionRuntimeStore.getState().runtimes[sid]
          if (!useChatStore.getState().streaming && rt) {
            void useChatStore.getState().resumeSession(sid, rt.lastSeq)
          }
        }
        set({ approval: null })
      } catch {
        useUiStore.getState().pushToast("toast.approvalFailed", "error")
      } finally {
        approvalBusyRef = false
        set({ approvalBusy: false })
      }
    },
  }
})
