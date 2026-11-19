/**
 * Per-session frontend runtime.
 *
 * The backend already keeps each session's loop, approval bridge and event ring
 * isolated. This store mirrors that on the client: a stream writes only into the
 * runtime for its own `session_id`, so switching the visible conversation never
 * routes another session's deltas into the current chat.
 *
 * This first PR owns phase/pending/seq and abort handles. Chat item storage is
 * still in `chatStore`; the next PR moves `items` into each runtime.
 */

import { create } from "zustand"
import type { ChatEvent, PendingApproval, PendingQuestion, SessionPending } from "../types"

export type RuntimePhase =
  | "idle"
  | "streaming"
  | "waiting_approval"
  | "waiting_question"
  | "error"

export interface SessionRuntimeView {
  sessionId: string
  phase: RuntimePhase
  turnId: string
  lastSeq: number
  startedAt: number
  error?: string
  pendingApproval: PendingApproval | null
  pendingQuestion: PendingQuestion | null
  /** The SSE request for this session's active turn, if any. */
  abortController: AbortController | null
}

interface SessionRuntimeState {
  runtimes: Record<string, SessionRuntimeView>
  ensure: (sessionId: string) => SessionRuntimeView
  begin: (sessionId: string, abortController?: AbortController | null, turnId?: string) => void
  setAbortController: (sessionId: string, abortController: AbortController | null) => void
  applyEvent: (sessionId: string, event: ChatEvent) => void
  finish: (sessionId: string) => void
  fail: (sessionId: string, message: string) => void
  hydratePending: (sessionId: string, pending: SessionPending) => void
  setPendingApproval: (sessionId: string, approval: PendingApproval | null) => void
  setPendingQuestion: (sessionId: string, question: PendingQuestion | null) => void
  abort: (sessionId: string) => void
  remove: (sessionId: string) => void
}

const EMPTY_RUNTIME: Omit<SessionRuntimeView, "sessionId"> = {
  phase: "idle",
  turnId: "",
  lastSeq: 0,
  startedAt: 0,
  pendingApproval: null,
  pendingQuestion: null,
  abortController: null,
}

function runtimeOf(
  runtimes: Record<string, SessionRuntimeView>,
  sessionId: string,
): SessionRuntimeView {
  return runtimes[sessionId] ?? { sessionId, ...EMPTY_RUNTIME }
}

/** Abort controllers are kept per runtime so a background turn can be stopped. */

function patchRuntime(
  runtimes: Record<string, SessionRuntimeView>,
  sessionId: string,
  patch: Partial<SessionRuntimeView>,
): Record<string, SessionRuntimeView> {
  const current = runtimeOf(runtimes, sessionId)
  return { ...runtimes, [sessionId]: { ...current, ...patch } }
}

export const useSessionRuntimeStore = create<SessionRuntimeState>((set, get) => ({
  runtimes: {},

  ensure: (sessionId) => {
    const current = get().runtimes[sessionId]
    if (current) return current
    const next = { sessionId, ...EMPTY_RUNTIME }
    set((s) => ({ runtimes: { ...s.runtimes, [sessionId]: next } }))
    return next
  },

  begin: (sessionId, abortController = null, turnId = "") => {
    set((s) => ({
      runtimes: patchRuntime(s.runtimes, sessionId, {
        phase: "streaming",
        turnId,
        lastSeq: 0,
        startedAt: Date.now(),
        error: undefined,
        pendingApproval: null,
        pendingQuestion: null,
        abortController,
      }),
    }))
  },

  setAbortController: (sessionId, abortController) => {
    set((s) => ({
      runtimes: patchRuntime(s.runtimes, sessionId, { abortController }),
    }))
  },

  applyEvent: (sessionId, event) => {
    if (!sessionId) return
    set((s) => {
      const current = runtimeOf(s.runtimes, sessionId)
      const seq = typeof event.seq === "number" ? event.seq : current.lastSeq
      // A replayed/duplicate frame must not move the cursor backwards.
      if (seq && seq < current.lastSeq) return s
      let phase: RuntimePhase = current.phase === "idle" ? "streaming" : current.phase
      if (event.type === "approval_needed") phase = "waiting_approval"
      else if (event.type === "question") phase = "waiting_question"
      else if (event.type === "turn_end" || event.type === "done") phase = "idle"
      else if (event.type === "turn_error") phase = "error"
      const patch: Partial<SessionRuntimeView> = {
        phase,
        lastSeq: seq,
        turnId: event.turn_id || current.turnId,
      }
      if (event.type === "approval_needed") {
        patch.pendingApproval = {
          summary: event.approval.summary,
          diff: event.approval.diff,
          tool: event.tool.name,
          arguments:
            (event.approval.arguments as Record<string, unknown> | undefined) ??
            (event.tool.arguments as Record<string, unknown> | undefined) ??
            {},
          tool_call_id: event.tool.id,
        }
        patch.pendingQuestion = null
      } else if (event.type === "question") {
        patch.pendingQuestion = {
          question: event.data.question,
          options: event.data.options,
        }
        patch.pendingApproval = null
      } else if (event.type === "turn_error") {
        patch.error = event.text || "turn error"
      }
      return { runtimes: patchRuntime(s.runtimes, sessionId, patch) }
    })
  },

  finish: (sessionId) => {
    set((s) => ({
      runtimes: patchRuntime(s.runtimes, sessionId, {
        phase: "idle",
        pendingApproval: null,
        pendingQuestion: null,
        abortController: null,
      }),
    }))
  },

  fail: (sessionId, message) => {
    set((s) => ({
      runtimes: patchRuntime(s.runtimes, sessionId, {
        phase: "error",
        error: message,
        pendingApproval: null,
        pendingQuestion: null,
        abortController: null,
      }),
    }))
  },

  hydratePending: (sessionId, pending) => {
    const approval = pending.approval
    const question = pending.question
    set((s) => {
      const current = runtimeOf(s.runtimes, sessionId)
      const busy = Boolean(pending.busy)
      const phase: RuntimePhase = approval
        ? "waiting_approval"
        : question
          ? "waiting_question"
          : busy
            ? "streaming"
            : current.phase === "streaming" ||
                current.phase === "waiting_approval" ||
                current.phase === "waiting_question"
              ? "idle"
              : current.phase
      return {
        runtimes: patchRuntime(s.runtimes, sessionId, {
          phase,
          lastSeq: Math.max(current.lastSeq, pending.last_seq ?? 0),
          pendingApproval: approval,
          pendingQuestion: question,
        }),
      }
    })
  },

  setPendingApproval: (sessionId, approval) => {
    set((s) => {
      const current = runtimeOf(s.runtimes, sessionId)
      return {
        runtimes: patchRuntime(s.runtimes, sessionId, {
          pendingApproval: approval,
          phase: approval ? "waiting_approval" : current.phase === "waiting_approval" ? "streaming" : current.phase,
        }),
      }
    })
  },

  setPendingQuestion: (sessionId, question) => {
    set((s) => {
      const current = runtimeOf(s.runtimes, sessionId)
      return {
        runtimes: patchRuntime(s.runtimes, sessionId, {
          pendingQuestion: question,
          phase: question ? "waiting_question" : current.phase === "waiting_question" ? "streaming" : current.phase,
        }),
      }
    })
  },

  abort: (sessionId) => {
    get().runtimes[sessionId]?.abortController?.abort()
    set((s) => ({
      runtimes: patchRuntime(s.runtimes, sessionId, {
        phase: "idle",
        pendingApproval: null,
        pendingQuestion: null,
        abortController: null,
      }),
    }))
  },

  remove: (sessionId) => {
    get().runtimes[sessionId]?.abortController?.abort()
    set((s) => {
      if (!s.runtimes[sessionId]) return s
      const next = { ...s.runtimes }
      delete next[sessionId]
      return { runtimes: next }
    })
  },
}))

export function runtimePhase(sessionId: string): RuntimePhase {
  return useSessionRuntimeStore.getState().runtimes[sessionId]?.phase ?? "idle"
}
