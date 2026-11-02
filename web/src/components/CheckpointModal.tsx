import { memo, useCallback, useEffect, useState } from "react"
import { useUiStore } from "../store/uiStore"
import { useChatStore } from "../store/chatStore"
import { useSessionStore } from "../store/sessionStore"
import {
  fetchTimeline,
  rollbackCheckpoint,
  truncateFromMessage,
  type TimelineMessageBrief,
} from "../api"
import { Button, cls, IconButton, Spinner } from "./ui"
import { IconClock, IconHistory, IconX } from "./icons"
import { historyToItems } from "../store/chatStore"
import { t, type Lang } from "../i18n"

const _ROLE_LABEL: Record<string, string> = {
  user: "user",
  assistant: "spark",
  tool: "tool",
  summary: "summary",
}

/**
 * P2 双粒度时间机器：检查点（文件快照 + 对话回滚）与消息级 undo（仅回退对话）
 * 统一在一个时间轴面板中。任务级粒度（分支 + worktree）由 task_* 工具承载。
 */
export const CheckpointModal = memo(function CheckpointModal() {
  const open = useUiStore((s) => s.checkpointsOpen)
  const close = useUiStore((s) => s.closeCheckpoints)
  const lang = useUiStore((s) => s.lang)
  const busy = useUiStore((s) => s.cpsBusy)
  const setBusy = useUiStore((s) => s.setCpsBusy)
  const rows = useUiStore((s) => s.checkpoints)
  const setRows = useUiStore((s) => s.setCheckpoints)
  const pushToast = useUiStore((s) => s.pushToast)
  const [confirmId, setConfirmId] = useState<number | null>(null)
  const [rolling, setRolling] = useState<number | null>(null)
  const [messages, setMessages] = useState<TimelineMessageBrief[]>([])
  const [confirmMsgId, setConfirmMsgId] = useState<number | null>(null)
  const [undoing, setUndoing] = useState<number | null>(null)
  /** 消息级 undo 是否连带把工作区恢复到该消息之前的快照 */
  const [restoreFiles, setRestoreFiles] = useState(false)

  const load = useCallback(async () => {
    setBusy(true)
    try {
      const data = await fetchTimeline()
      setRows(data.checkpoints)
      setMessages(data.messages)
    } catch {
      pushToast("toast.checkpointsLoadFailed", "error")
    } finally {
      setBusy(false)
    }
  }, [setBusy, setRows, pushToast])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, close])

  function applyHistory(data: { status: unknown; messages: never[] }) {
    const items = historyToItems(data.messages)
    useChatStore.getState().setItems(items)
    useSessionStore.getState().setStatus(data.status as never)
    useSessionStore.getState().setPlan((data.status as { plan?: [] })?.plan || [])
    useSessionStore.getState().syncPlanFromItems(items)
  }

  async function doRollback(id: number) {
    setRolling(id)
    try {
      const data = await rollbackCheckpoint(id)
      applyHistory(data as never)
      pushToast("toast.rollbackDone", "success")
      await load()
    } catch {
      pushToast("toast.rollbackFailed", "error")
    } finally {
      setRolling(null)
      setConfirmId(null)
    }
  }

  async function doUndo(messageId: number) {
    setUndoing(messageId)
    try {
      const data = await truncateFromMessage(messageId, restoreFiles)
      applyHistory(data as never)
      // 勾了"连文件一起还原"但没有可用快照时必须如实说明，只回退了对话——
      // 不能让用户以为工作区也回去了。
      if (restoreFiles && !data.files_restored) {
        pushToast("toast.undoFilesSkipped", "info")
      } else {
        pushToast("toast.undoDone", "success")
      }
      await load()
    } catch {
      pushToast("toast.undoFailed", "error")
    } finally {
      setUndoing(null)
      setConfirmMsgId(null)
    }
  }

  function fmt(ts: number, lang: Lang): string {
    return new Date(ts * 1000).toLocaleString(lang === "zh" ? "zh-CN" : "en-US", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
  }

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-[2px]" onClick={close}>
      <aside
        className="anim-fade flex h-[min(84vh,640px)] w-[min(94vw,600px)] flex-col overflow-hidden rounded-2xl border border-line bg-canvas shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={t("sidebar.checkpoint", lang)}
      >
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <IconHistory size={16} className="text-ink2" />
          <span className="text-sm font-semibold text-ink">{t("sidebar.checkpoint", lang)}</span>
          <IconButton title={t("common.close", lang)} onClick={close} className="ml-auto">
            <IconX size={15} />
          </IconButton>
        </div>

        <div className="flex-1 overflow-y-auto p-3">
          {busy ? (
            <div className="flex justify-center py-10">
              <Spinner size={18} className="text-accent" />
            </div>
          ) : (
            <>
              {rows.length === 0 ? (
                <div className="flex flex-col items-center gap-2 py-8 text-center">
                  <IconClock size={22} className="text-ink3" />
                  <p className="px-6 text-xs leading-relaxed text-ink3">{t("checkpoint.empty", lang)}</p>
                </div>
              ) : (
                <ul className="flex flex-col gap-1.5">
                  {rows.map((r) => (
                    <li key={r.id} className={cls("rounded-xl border bg-panel p-3", confirmId === r.id ? "border-err/40" : "border-line")}>
                      <div className="flex items-center gap-2">
                        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-hover text-ink2">
                          <IconClock size={13} />
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-[13px] font-medium text-ink">{r.label || t("checkpoint.label", lang)}</p>
                          <p className="font-mono text-[10.5px] text-ink3">{fmt(r.created_at, lang)}</p>
                        </div>
                      </div>
                      {confirmId === r.id ? (
                        <div className="mt-2.5 flex gap-2">
                          <Button size="sm" variant="danger" full disabled={rolling !== null} onClick={() => doRollback(r.id)}>
                            {rolling === r.id ? <Spinner size={11} /> : t("checkpoint.confirmRollback", lang)}
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => setConfirmId(null)}>
                            {t("common.cancel", lang)}
                          </Button>
                        </div>
                      ) : (
                        <Button size="sm" variant="soft" full className="mt-2.5" onClick={() => setConfirmId(r.id)}>
                           {t("checkpoint.rollbackTo", lang)}
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
              )}

              <p className="mb-2 mt-4 px-1 text-[11px] font-semibold uppercase tracking-wide text-ink3">
                {t("timeline.messages", lang)}
              </p>
              <label className="mb-2 flex items-start gap-2 px-1">
                <input
                  type="checkbox"
                  className="mt-0.5 accent-[var(--accent)]"
                  checked={restoreFiles}
                  onChange={(e) => setRestoreFiles(e.target.checked)}
                />
                <span className="flex min-w-0 flex-col">
                  <span className="text-[11.5px] text-ink2">{t("timeline.restoreFiles", lang)}</span>
                  <span className="text-[10px] leading-snug text-ink3">{t("timeline.restoreFilesHint", lang)}</span>
                </span>
              </label>
              {messages.length === 0 ? (
                <p className="px-1 pb-4 text-xs text-ink3">{t("timeline.messagesEmpty", lang)}</p>
              ) : (
                <ul className="flex flex-col gap-1">
                  {messages.map((m) => (
                    <li key={m.id} className={cls("rounded-xl border bg-panel px-3 py-2", confirmMsgId === m.id ? "border-err/40" : "border-line")}>
                      <div className="flex items-center gap-2">
                        <span className="w-12 shrink-0 font-mono text-[10px] uppercase text-ink3">
                          {_ROLE_LABEL[m.role] || m.role}
                        </span>
                        <p className="min-w-0 flex-1 truncate text-[12px] text-ink2">
                          {m.role === "tool" && m.name ? <span className="font-mono text-accent">{m.name}</span> : null}
                          {m.role === "tool" && m.name ? " · " : ""}
                          {m.preview || (m.role === "tool" ? "" : "-")}
                        </p>
                        {confirmMsgId === m.id ? (
                          <div className="flex shrink-0 items-center gap-1.5">
                            <Button size="sm" variant="danger" disabled={undoing !== null} onClick={() => doUndo(m.id)}>
                              {undoing === m.id ? <Spinner size={10} /> : t("common.confirm", lang)}
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => setConfirmMsgId(null)}>
                              {t("common.cancel", lang)}
                            </Button>
                          </div>
                        ) : (
                          <Button size="sm" variant="ghost" className="shrink-0" title={t("timeline.confirmUndo", lang)} onClick={() => setConfirmMsgId(m.id)}>
                            {t("timeline.undoTo", lang)}
                          </Button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      </aside>
    </div>
  )
})
