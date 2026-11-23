import { memo, useMemo } from "react"
import type { ChatMsg } from "../store/chatStore"
import MessageItem, { type MessageActions } from "./MessageItem"
import { EmptyState } from "./EmptyState"
import { VirtualMessageList } from "./VirtualMessageList"
import { useChatStore } from "../store/chatStore"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

/* ------------------------------------------------------------------ */
/* 虚拟化高度估算（P1-5 起由 @tanstack/react-virtual 驱动）             */
/* ------------------------------------------------------------------ */

function estimateHeight(m: ChatMsg): number {
  if (m.role === "user") {
    return 60 + (m.images?.length ?? 0) * 100 + Math.min(m.text.length / 70, 10) * 22
  }
  if (m.role === "assistant") {
    let h = 44
    for (const s of m.segments) {
      if (s.kind === "text") h += 22 + Math.min((s.text.length / 52) * 22, 260)
      else if (s.kind === "tool") h += 46
      else if (s.kind === "thinking") h += 38
      else if (s.kind === "plan") h += 46 + s.steps.length * 30
    }
    return h + (m.streaming ? 20 : 0)
  }
  if (m.role === "error") return 80
  return 44
}

function SkeletonList() {
  return (
    <div className="mx-auto max-w-[780px] space-y-4 px-4 py-6">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="flex gap-3">
          <div className="skeleton h-7 w-7 shrink-0 rounded-full" />
          <div className="flex-1 space-y-2">
            <div className="skeleton h-4 w-24" />
            <div className="skeleton h-4 w-full" />
            <div className="skeleton h-4 w-3/4" />
          </div>
        </div>
      ))}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* ChatView：滚动容器已下沉到 VirtualMessageList（tanstack virtual）   */
/* ------------------------------------------------------------------ */

export const ChatView = memo(function ChatView() {
  const items = useChatStore((s) => s.items)
  const loading = useChatStore((s) => s.loading)
  const streaming = useChatStore((s) => s.streaming)
  const setInput = useChatStore((s) => s.setInput)
  const deleteMessage = useChatStore((s) => s.deleteMessage)
  const feedbackMessage = useChatStore((s) => s.feedbackMessage)
  const regenerate = useChatStore((s) => s.regenerate)
  const startEditMessage = useChatStore((s) => s.startEditMessage)
  const lang = useUiStore((s) => s.lang)
  const status = useSessionStore((s) => s.status)
  const activeGoal = useSessionStore((s) => s.activeGoal)
  const send = useChatStore((s) => s.send)

  const actions = useMemo<MessageActions>(
    () => ({
      onDelete: (m) => {
        if (!m.dbId) return
        const id = m.dbId
        // 原生 window.confirm 在移动端/WebView 里样式不可控，且会打断键盘
        // 焦点流；统一走 uiStore 的全站确认对话框。
        useUiStore.getState().requestConfirm({
          title: t("msg.deleteTitle", lang),
          message: t("msg.deleteConfirm", lang),
          danger: true,
          onConfirm: () => void deleteMessage(id),
        })
      },
      onEdit: (m) => {
        if (!m.dbId) return
        void startEditMessage(m.dbId)
      },
      onRegenerate: (m) => {
        if (!m.dbId) return
        void regenerate(m.dbId)
      },
      onFeedback: (m, rating) => {
        if (!m.dbId) return
        void feedbackMessage(m.dbId, rating)
      },
    }),
    [deleteMessage, regenerate, startEditMessage, feedbackMessage, lang],
  )

  const goalBanner = activeGoal ? (
    <div className="mx-auto mt-2 flex w-full max-w-[780px] items-center gap-2 rounded-xl border border-accent/25 bg-accent-soft px-3 py-2 text-[12px] text-ink2">
      <span className="shrink-0 text-accent">🎯</span>
      <span className="min-w-0 flex-1 truncate" title={activeGoal.objective}>
        {t("goal.resumeBanner", lang, { objective: activeGoal.objective })}
      </span>
      <button
        type="button"
        disabled={streaming}
        onClick={() => {
          setInput(t("goal.resumePrompt", lang, { objective: activeGoal.objective }))
          void send()
        }}
        className="shrink-0 rounded-md bg-accent px-2 py-1 text-[11px] font-medium text-on-accent disabled:opacity-45"
      >
        {t("goal.resumeAction", lang)}
      </button>
    </div>
  ) : null

  if (loading || !status) {
    return (
      <div className="flex-1 overflow-y-auto">
        {goalBanner}
        <SkeletonList />
      </div>
    )
  }

  if (items.length === 0) {
    return (
      <div className="flex-1 overflow-y-auto">
        {goalBanner}
        <EmptyState onPick={(p: string) => setInput(p)} />
      </div>
    )
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      {goalBanner}
      <VirtualMessageList
        items={items}
        resetKey={status.session_id}
        estimateHeight={estimateHeight}
        renderItem={(m) => (
          <div className="mx-auto max-w-[780px] px-3 sm:px-4" lang={lang}>
            <MessageItem key={m.id} msg={m} actions={actions} />
          </div>
        )}
        footer={
          streaming ? (
            <div className="pb-2 pt-1 text-center text-[11px] text-ink3">{t("chat.generating", lang)}</div>
          ) : undefined
        }
      />
    </div>
  )
})
