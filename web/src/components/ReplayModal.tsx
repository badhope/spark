import { memo, useCallback, useEffect, useState } from "react"
import { useUiStore } from "../store/uiStore"
import { useSessionStore } from "../store/sessionStore"
import { fetchReplays, fetchReplayTurn, type ReplayRecord, type ReplayTurnSummary } from "../api"
import { Button, cls, IconButton, Spinner } from "./ui"
import { IconCheck, IconClock, IconCopy, IconHistory, IconRefresh, IconX } from "./icons"
import { t } from "../i18n"

function statusColor(status?: string): string {
  if (status === "ok") return "text-ok"
  if (status === "cancelled") return "text-warn"
  return "text-err"
}

function fmtMs(ms?: number): string {
  if (!ms && ms !== 0) return "-"
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

/** P2 上下文合同：turn 级重放视图——列表 + 明细（文本 / 工具轨迹 / 审批 / 用量）。 */
export const ReplayModal = memo(function ReplayModal() {
  const open = useUiStore((s) => s.replayOpen)
  const close = useUiStore((s) => s.closeReplay)
  const lang = useUiStore((s) => s.lang)
  const pushToast = useUiStore((s) => s.pushToast)
  const sessionId = useSessionStore((s) => s.status?.session_id)
  const [turns, setTurns] = useState<ReplayTurnSummary[] | null>(null)
  const [detail, setDetail] = useState<ReplayRecord | null>(null)
  const [copied, setCopied] = useState("")

  const load = useCallback(async () => {
    setTurns(null)
    setDetail(null)
    try {
      const data = await fetchReplays()
      setTurns(data.turns)
    } catch {
      pushToast("toast.replayLoadFailed", "error")
      setTurns([])
    }
  }, [pushToast])

  useEffect(() => {
    if (open) load()
  }, [open, load, sessionId])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, close])

  async function copyText(text: string, key: string) {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(key)
      window.setTimeout(() => setCopied((v) => (v === key ? "" : v)), 1400)
    } catch {
      /* ignore clipboard denial */
    }
  }

  async function openDetail(index: number) {
    try {
      setDetail(await fetchReplayTurn(index))
    } catch {
      pushToast("toast.replayLoadFailed", "error")
    }
  }

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-[2px]" onClick={close}>
      <aside
        className="anim-fade flex h-[min(84vh,640px)] w-[min(96vw,720px)] flex-col overflow-hidden rounded-2xl border border-line bg-canvas shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={t("replay.title", lang)}
      >
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <IconHistory size={16} className="text-ink2" />
          <span className="text-sm font-semibold text-ink">{t("replay.title", lang)}</span>
          {detail && (
            <Button size="sm" variant="ghost" className="ml-2" onClick={() => setDetail(null)}>
              ←
            </Button>
          )}
          <IconButton title={t("common.refresh", lang)} onClick={() => void load()} className="ml-auto">
            <IconRefresh size={14} />
          </IconButton>
          <IconButton title={t("common.close", lang)} onClick={close}>
            <IconX size={15} />
          </IconButton>
        </div>

        {detail ? (
          <div className="flex-1 overflow-y-auto p-4">
            <div className="mb-3 flex flex-wrap items-center gap-3 text-xs text-ink2">
              <span className={cls("font-mono font-semibold", statusColor(detail.turn.status))}>
                {detail.turn.status}
              </span>
              <span>{fmtMs(detail.turn.duration_ms)}</span>
              <span className="font-mono">
                {t("replay.usage", lang)}: in {detail.turn.usage?.input_tokens ?? 0} / out {detail.turn.usage?.output_tokens ?? 0}
                {detail.turn.usage?.reasoning_tokens ? ` / think ${detail.turn.usage.reasoning_tokens}` : ""}
              </span>
              {detail.turn.resumed && <span className="rounded bg-hover px-1.5 py-0.5">resumed</span>}
            </div>
            <div className="mb-3 rounded-xl border border-line bg-panel p-3 text-[13px] text-ink">
              <div className="flex items-center gap-2">
                <span className="font-semibold">{t("chat.you", lang)}</span>
                <button
                  type="button"
                  title={copied === "prompt" ? t("common.copied", lang) : t("common.copy", lang)}
                  onClick={() => void copyText(detail.turn.prompt ?? "", "prompt")}
                  className="ml-auto flex h-6 w-6 items-center justify-center rounded-md text-ink3 hover:bg-hover hover:text-ink"
                >
                  {copied === "prompt" ? <IconCheck size={12} className="text-ok" /> : <IconCopy size={12} />}
                </button>
              </div>
              <p className="mt-1 whitespace-pre-wrap break-words">{detail.turn.prompt}</p>
            </div>
            {(detail.turn.reasoning || "").trim() && (
              <pre className="mb-3 max-h-40 overflow-y-auto whitespace-pre-wrap rounded-xl border border-line bg-panel p-3 font-mono text-[11px] text-ink2">
                {detail.turn.reasoning}
              </pre>
            )}
            {(detail.turn.tool_calls || []).length > 0 && (
              <div className="mb-3">
                <p className="mb-1.5 text-xs font-semibold text-ink2">
                  {t("replay.tools", lang)} ({detail.turn.tool_calls.length})
                </p>
                <ul className="flex flex-col gap-1.5">
                  {detail.turn.tool_calls.map((call) => (
                    <li key={call.id} className="rounded-xl border border-line bg-panel p-2.5 font-mono text-[11.5px]">
                      <span className={cls("font-semibold", call.ok ? "text-ok" : call.ok === false ? "text-err" : "text-ink2")}>
                        {call.ok ? "✓" : call.ok === false ? "✗" : "…"} {call.name}
                      </span>
                      <pre className="mt-1 max-h-32 overflow-y-auto whitespace-pre-wrap text-ink2">
                        {JSON.stringify(call.arguments, null, 1)}
                      </pre>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {(detail.turn.approvals || []).length > 0 && (
              <div className="mb-3">
                <p className="mb-1.5 text-xs font-semibold text-ink2">
                  {t("replay.approvals", lang)} ({detail.turn.approvals!.length})
                </p>
                <ul className="flex flex-col gap-1">
                  {detail.turn.approvals!.map((ap, i) => (
                    <li key={i} className="rounded-lg border border-line bg-panel px-2.5 py-1.5 text-[11.5px] text-ink2">
                      <span className="font-mono">{ap.tool || "?"}</span> → {ap.decision ?? "?"}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {detail.turn.text && (
              <div>
                <div className="mb-1.5 flex items-center gap-2">
                  <p className="text-xs font-semibold text-ink2">{t("chat.spark", lang)}</p>
                  <button
                    type="button"
                    title={copied === "text" ? t("common.copied", lang) : t("common.copy", lang)}
                    onClick={() => void copyText(detail.turn.text ?? "", "text")}
                    className="ml-auto flex h-6 w-6 items-center justify-center rounded-md text-ink3 hover:bg-hover hover:text-ink"
                  >
                    {copied === "text" ? <IconCheck size={12} className="text-ok" /> : <IconCopy size={12} />}
                  </button>
                </div>
                <pre className="whitespace-pre-wrap rounded-xl border border-line bg-panel p-3 font-mono text-[11.5px] text-ink">
                  {detail.turn.text}
                </pre>
              </div>
            )}
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto p-3">
            {turns === null ? (
              <div className="flex justify-center py-10">
                <Spinner size={18} className="text-accent" />
              </div>
            ) : turns.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-12 text-center">
                <IconClock size={22} className="text-ink3" />
                <p className="px-6 text-xs leading-relaxed text-ink3">{t("replay.empty", lang)}</p>
              </div>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {turns.map((turn) => (
                  <li key={turn.index}>
                    <button
                      type="button"
                      onClick={() => openDetail(turn.index)}
                      className="w-full rounded-xl border border-line bg-panel p-3 text-left transition-colors hover:border-accent/40"
                    >
                      <div className="flex items-center gap-2 text-[11px] text-ink3">
                        <span className="font-mono font-semibold text-ink2">#{turn.index}</span>
                        <span className={statusColor(turn.status)}>{turn.status}</span>
                        <span>{fmtMs(turn.duration_ms)}</span>
                        <span>
                          {t("replay.tools", lang)}: {turn.tools}
                        </span>
                        {turn.resumed && <span className="rounded bg-hover px-1.5">resumed</span>}
                      </div>
                      <p className="mt-1 truncate text-[13px] text-ink">{turn.prompt || "-"}</p>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </aside>
    </div>
  )
})
