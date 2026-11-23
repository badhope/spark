import { useCallback, useEffect, useState } from "react"
import {
  applyInlineEdit,
  buildCodeIndex,
  createDelivery,
  exportDelivery,
  fetchCodeIndex,
  generateInlineEdit,
  fetchWorkbench,
  queueAction,
  rollbackCheckpoint,
  updateDelivery,
  type CodeIndexStatus,
  type DeliveryRecord,
  type WorkbenchSnapshot,
} from "../api"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { Button, cls, IconButton, Spinner } from "./ui"
import { IconCheck, IconClock, IconDownload, IconFileDiff, IconFolder, IconRefresh, IconSend, IconSpark, IconX } from "./icons"
import { t } from "../i18n"

function ms(value: unknown): string {
  const n = typeof value === "number" ? value : Number(value)
  if (!Number.isFinite(n) || n <= 0) return "—"
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}s` : `${Math.round(n)}ms`
}

function when(ts?: number): string {
  if (!ts) return ""
  return new Date(ts * 1000).toLocaleString(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
}

function statusTone(status: string): string {
  if (status === "accepted" || status === "ok") return "text-ok"
  if (status === "rejected" || status === "failed" || status === "blocked") return "text-err"
  if (status === "review" || status === "queued") return "text-warn"
  return "text-ink3"
}

export function WorkbenchPanel() {
  const lang = useUiStore((s) => s.lang)
  const pushToast = useUiStore((s) => s.pushToast)
  const [snap, setSnap] = useState<WorkbenchSnapshot | null>(null)
  const [prompt, setPrompt] = useState("")
  const [title, setTitle] = useState("")
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")
  const [expanded, setExpanded] = useState<string | null>(null)
  const [privacyOpen, setPrivacyOpen] = useState(false)
  const switchSession = useSessionStore((s) => s.handleSwitch)
  const [paused, setPaused] = useState(false)
  const [index, setIndex] = useState<CodeIndexStatus | null>(null)
  const [inlinePath, setInlinePath] = useState("")
  const [inlineSelected, setInlineSelected] = useState("")
  const [inlineInstruction, setInlineInstruction] = useState("")
  const [inlineResult, setInlineResult] = useState<string | null>(null)
  const [inlineBaseHash, setInlineBaseHash] = useState<string | null>(null)
  const [inlineBusy, setInlineBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const [data, idx] = await Promise.all([fetchWorkbench(), fetchCodeIndex()])
      setSnap(data)
      setPaused(Boolean(data.queue?.paused))
      setIndex(idx)
      setErr("")
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 5000)
    return () => window.clearInterval(timer)
  }, [load])

  async function addTask() {
    if (!prompt.trim()) return
    setBusy(true)
    setErr("")
    try {
      await queueAction({ action: "create", prompt: prompt.trim(), title: title.trim() })
      setPrompt("")
      setTitle("")
      await load()
      pushToast("workbench2.queued", "success")
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function rebuildIndex() {
    setBusy(true)
    try {
      setIndex(await buildCodeIndex())
      pushToast("workbench2.indexBuilt", "success")
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function makeInlinePreview() {
    if (!inlinePath.trim() || !inlineSelected.trim()) return
    setInlineBusy(true)
    setErr("")
    try {
      const data = await generateInlineEdit({
        path: inlinePath.trim(),
        selected: inlineSelected,
        instruction: inlineInstruction.trim(),
        mode: "edit",
      })
      if (!data.ok) throw new Error(data.error || "inline edit returned no text")
      setInlineResult(data.text ?? "")
      setInlineBaseHash(data.base_sha256 ?? null)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setInlineBusy(false)
    }
  }

  async function applyInline() {
    if (inlineResult == null || !inlinePath.trim() || !inlineSelected.trim()) return
    if (!window.confirm(t("workbench2.inlineConfirmed", lang))) return
    setInlineBusy(true)
    setErr("")
    try {
      const data = await applyInlineEdit({
        path: inlinePath.trim(),
        selected: inlineSelected,
        replacement: inlineResult,
        base_sha256: inlineBaseHash,
      })
      if (!data.ok) throw new Error(data.error || "apply failed")
      pushToast("workbench2.inlineApplied", "success")
      setInlineSelected(inlineResult)
      setInlineResult(null)
      setInlineBaseHash(null)
      await load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setInlineBusy(false)
    }
  }

  async function queueControl(action: string, id?: string) {
    setBusy(true)
    try {
      await queueAction(id ? { action, id } : { action })
      await load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function makeLedger() {
    setBusy(true)
    setErr("")
    try {
      await createDelivery({})
      await load()
      pushToast("workbench2.ledgerCreated", "success")
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function setStatus(row: DeliveryRecord, status: DeliveryRecord["status"]) {
    setBusy(true)
    try {
      await updateDelivery(row.id, status)
      await load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function rollback(row: DeliveryRecord) {
    if (!row.checkpoint_id) {
      pushToast("workbench2.noCheckpoint", "info")
      return
    }
    setBusy(true)
    try {
      await rollbackCheckpoint(row.checkpoint_id)
      await updateDelivery(row.id, "rolled_back")
      await load()
      pushToast("workbench2.rolledBack", "success")
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function download(row: DeliveryRecord) {
    try {
      const data = await exportDelivery(row.id)
      const url = URL.createObjectURL(new Blob([data.markdown], { type: "text/markdown;charset=utf-8" }))
      const a = document.createElement("a")
      a.href = url
      a.download = `${(row.title || "delivery").replace(/[^\w\u4e00-\u9fa5.-]+/g, "-")}.md`
      a.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  const queueTasks = snap?.tasks || []
  const review = snap?.review || []
  const deliveries = snap?.deliveries || []
  const counts = snap?.queue?.counts || {}
  const privacy = (snap?.privacy || {}) as {
    mode?: string
    provider?: string
    model?: string
    endpoint?: string
    tokens_sent?: number
    tokens_received?: number
    files_read?: string[]
    network_tools?: string[]
  }
  const local = privacy.mode === "local"
  // /api/workbench returns metrics newest-first; [length-1] showed the oldest turn.
  const latest = (snap?.metrics || [])[0]
  const metric = (latest?.metrics || {}) as Record<string, unknown>

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="workbench-panel">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2">
        <IconSpark size={13} className="text-accent" />
        <span className="text-[12px] font-semibold text-ink">{t("workbench2.title", lang)}</span>
        <span className="rounded-full bg-hover px-1.5 py-0.5 text-[10px] text-ink3">{String(counts.running ?? 0)}/{queueTasks.length}</span>
        {review.length > 0 && <span className="rounded-full bg-warn/15 px-1.5 py-0.5 text-[10px] text-warn">{review.length} review</span>}
        <span className={cls("ml-auto rounded-full px-1.5 py-0.5 text-[10px]", local ? "bg-ok/15 text-ok" : "bg-warn/15 text-warn")}>
          {local ? t("workbench2.egressLocal", lang) : t("workbench2.egressRemote", lang)}
        </span>
        <IconButton
          title={paused ? t("workbench2.resume", lang) : t("workbench2.pause", lang)}
          onClick={() => void queueControl(paused ? "resume" : "pause")}
        >
          {paused ? <IconCheck size={13} /> : <IconClock size={13} />}
        </IconButton>
        <IconButton title={t("common.refresh", lang)} onClick={() => void load()}>
          <IconRefresh size={13} />
        </IconButton>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        <section className="rounded-xl border border-line bg-panel2 p-3">
          <div className="flex items-center gap-1.5 text-[11px] font-medium text-ink2">
            <IconSend size={12} /> {t("workbench2.newTask", lang)}
          </div>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={t("workbench2.titlePh", lang)}
            className="mt-2 h-7 w-full rounded-md border border-line bg-panel px-2 text-[11px] text-ink outline-none placeholder:text-ink3 focus:border-accent"
          />
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={t("workbench2.promptPh", lang)}
            rows={2}
            className="mt-1.5 w-full resize-y rounded-md border border-line bg-panel px-2 py-1.5 text-[11px] leading-relaxed text-ink outline-none placeholder:text-ink3 focus:border-accent"
          />
          <div className="mt-1.5 flex items-center gap-2">
            <Button size="sm" variant="primary" onClick={() => void addTask()} disabled={busy || !prompt.trim()}>
              {busy ? <Spinner size={10} /> : t("workbench2.add", lang)}
            </Button>
            <span className="text-[10px] text-ink3">{t("workbench2.localHint", lang)}</span>
          </div>
        </section>

        {err && <p className="mt-2 break-words text-[11px] text-err">{err}</p>}
        {paused && <p className="mt-2 rounded-md bg-warn/10 px-2 py-1 text-[10px] text-warn">{t("workbench2.queuePaused", lang)}</p>}

        <section className="mt-3 rounded-xl border border-line bg-panel2 p-3">
          <h4 className="flex items-center gap-1.5 text-[11px] font-semibold text-ink2">
            <IconSpark size={12} /> {t("workbench2.inlineTitle", lang)}
          </h4>
          <p className="mt-0.5 text-[10px] leading-relaxed text-ink3">{t("workbench2.inlineDesc", lang)}</p>
          <input
            value={inlinePath}
            onChange={(e) => setInlinePath(e.target.value)}
            placeholder={t("workbench2.inlinePath", lang)}
            className="mt-2 h-7 w-full rounded-md border border-line bg-panel px-2 font-mono text-[11px] text-ink outline-none placeholder:text-ink3 focus:border-accent"
          />
          <textarea
            value={inlineSelected}
            onChange={(e) => { setInlineSelected(e.target.value); setInlineResult(null); setInlineBaseHash(null) }}
            placeholder={t("workbench2.inlineSelection", lang)}
            rows={3}
            className="mt-1.5 w-full resize-y rounded-md border border-line bg-panel px-2 py-1.5 font-mono text-[10.5px] leading-relaxed text-ink outline-none placeholder:text-ink3 focus:border-accent"
          />
          <input
            value={inlineInstruction}
            onChange={(e) => setInlineInstruction(e.target.value)}
            placeholder={t("workbench2.inlineInstruction", lang)}
            className="mt-1.5 h-7 w-full rounded-md border border-line bg-panel px-2 text-[11px] text-ink outline-none placeholder:text-ink3 focus:border-accent"
          />
          <div className="mt-1.5 flex items-center gap-1.5">
            <Button size="sm" variant="primary" onClick={() => void makeInlinePreview()} disabled={inlineBusy || !inlinePath.trim() || !inlineSelected.trim()}>
              {inlineBusy ? <Spinner size={10} /> : null}
              {t("workbench2.inlineGenerate", lang)}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void applyInline()} disabled={inlineBusy || inlineResult == null}>
              {t("workbench2.inlineApply", lang)}
            </Button>
          </div>
          {inlineResult == null ? (
            <p className="mt-1 text-[10px] text-ink3">{t("workbench2.inlineNoResult", lang)}</p>
          ) : (
            <textarea
              value={inlineResult}
              onChange={(e) => setInlineResult(e.target.value)}
              rows={4}
              className="mt-1.5 w-full resize-y rounded-md border border-accent/40 bg-panel px-2 py-1.5 font-mono text-[10.5px] leading-relaxed text-ink outline-none focus:border-accent"
            />
          )}
        </section>

        <section className="mt-3">
          <h4 className="flex items-center gap-1.5 text-[11px] font-semibold text-ink2">
            <IconClock size={12} /> {t("workbench2.tasks", lang)}
          </h4>
          {queueTasks.length === 0 ? (
            <p className="mt-1.5 text-[11px] text-ink3">{t("workbench2.noTasks", lang)}</p>
          ) : (
            <div className="mt-1.5 flex flex-col gap-1">
              {queueTasks.slice(0, 8).map((task) => (
                <div key={task.id} className="rounded-lg border border-line bg-panel px-2.5 py-2">
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => void switchSession(task.session_id)}
                      className="min-w-0 flex-1 truncate text-left text-[11px] font-medium text-ink2 hover:text-accent"
                      title={t("workbench2.open", lang)}
                    >
                      {task.title || task.prompt.slice(0, 50)}
                    </button>
                    <span className={cls("text-[10px]", statusTone(task.status))}>{task.status}</span>
                  </div>
                  <div className="mt-0.5 flex items-center gap-2 text-[10px] text-ink3">
                    <span>#{task.attempts}</span>
                    {task.created_at && <span>{when(task.created_at)}</span>}
                    {task.delivery_id && <span className="text-accent">ledger</span>}
                    {(task.status === "queued" || task.status === "running") && (
                      <button
                        type="button"
                        onClick={() => void queueControl("cancel", task.id)}
                        className="ml-auto text-[10px] text-ink3 hover:text-err"
                      >
                        {t("workbench2.cancelTask", lang)}
                      </button>
                    )}
                  </div>
                  {task.error && <p className="mt-0.5 break-words text-[10px] text-err">{task.error}</p>}
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="mt-3">
          <h4 className="flex items-center gap-1.5 text-[11px] font-semibold text-ink2">
            <IconFileDiff size={12} /> {t("workbench2.review", lang)}
          </h4>
          {review.length === 0 ? (
            <p className="mt-1.5 text-[11px] text-ink3">{t("workbench2.noReview", lang)}</p>
          ) : (
            <div className="mt-1.5 flex flex-col gap-1.5">
              {review.slice(0, 4).map((row) => (
                <div key={row.id} className="rounded-lg border border-warn/40 bg-warn/5 px-2.5 py-2">
                  <div className="flex items-center gap-1.5">
                    <span className="min-w-0 flex-1 truncate text-[11px] font-medium text-ink2">{row.title}</span>
                    <span className="text-[10px] text-warn">review</span>
                  </div>
                  <div className="mt-0.5 text-[10px] text-ink3">
                    {row.files.length} files · {row.tests.length} checks · {when(row.created_at)}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    <Button size="sm" variant="primary" onClick={() => void setStatus(row, "accepted")} disabled={busy}>
                      <IconCheck size={11} /> {t("workbench2.accept", lang)}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => void setStatus(row, "rejected")} disabled={busy}>
                      <IconX size={11} /> {t("workbench2.reject", lang)}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => void rollback(row)} disabled={busy}>
                      {t("workbench2.rollback", lang)}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="mt-3">
          <div className="flex items-center gap-1.5">
            <h4 className="flex items-center gap-1.5 text-[11px] font-semibold text-ink2">
              <IconFolder size={12} /> {t("workbench2.ledger", lang)}
            </h4>
            <button type="button" onClick={() => void makeLedger()} disabled={busy} className="ml-auto text-[10px] text-accent hover:underline">
              {t("workbench2.createLedger", lang)}
            </button>
          </div>
          {deliveries.length === 0 ? (
            <p className="mt-1.5 text-[11px] text-ink3">{t("workbench2.noLedger", lang)}</p>
          ) : (
            <div className="mt-1.5 flex flex-col gap-1">
              {deliveries.slice(0, 6).map((row) => (
                <div key={row.id} className="rounded-lg border border-line bg-panel px-2.5 py-2">
                  <button
                    type="button"
                    onClick={() => setExpanded(expanded === row.id ? null : row.id)}
                    className="flex w-full items-center gap-1.5 text-left"
                  >
                    <span className="min-w-0 flex-1 truncate text-[11px] font-medium text-ink2">{row.title}</span>
                    <span className={cls("text-[10px]", statusTone(row.status))}>{row.status}</span>
                  </button>
                  <div className="mt-0.5 text-[10px] text-ink3">
                    {row.files.length} files · {row.tests.length} checks · {when(row.created_at)}
                  </div>
                  {expanded === row.id && (
                    <div className="mt-1.5 border-t border-line pt-1.5 text-[10px] text-ink3">
                      {row.summary && <p className="mb-1 line-clamp-3 whitespace-pre-wrap text-ink2">{row.summary}</p>}
                      {row.tests.slice(-3).map((test, i) => (
                        <p key={i} className="truncate">
                          <span className={test.ok ? "text-ok" : "text-err"}>{test.ok ? "PASS" : "FAIL"}</span> {test.command || test.name}
                        </p>
                      ))}
                      <div className="mt-1.5 flex gap-1">
                        <Button size="sm" variant="ghost" onClick={() => void download(row)}>
                          <IconDownload size={11} /> {t("workbench2.export", lang)}
                        </Button>
                        {row.status === "accepted" && (
                          <Button size="sm" variant="ghost" onClick={() => void rollback(row)}>
                            {t("workbench2.rollback", lang)}
                          </Button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="mt-3 rounded-xl border border-line bg-panel2 p-3">
          <button type="button" onClick={() => setPrivacyOpen(!privacyOpen)} className="flex w-full items-center gap-1.5 text-left">
            <span className={cls("inline-block h-1.5 w-1.5 rounded-full", local ? "bg-ok" : "bg-warn")} />
            <span className="text-[11px] font-semibold text-ink2">{t("workbench2.privacy", lang)}</span>
            <span className="ml-auto text-[10px] text-ink3">{local ? t("workbench2.egressLocal", lang) : t("workbench2.egressRemote", lang)}</span>
          </button>
          <div className="mt-1 text-[10px] text-ink3">
            {privacy.provider || "—"}{privacy.model ? ` · ${privacy.model}` : ""} · {t("workbench2.tokens", lang)} {privacy.tokens_sent ?? 0}/{privacy.tokens_received ?? 0}
          </div>
          {privacyOpen && (
            <div className="mt-1.5 space-y-1 border-t border-line pt-1.5 text-[10px] text-ink3">
              <p className="break-all">{privacy.endpoint || (local ? "local runtime" : "—")}</p>
              <p>{t("workbench2.filesRead", lang)}: {(privacy.files_read || []).length}</p>
              {(privacy.files_read || []).slice(0, 8).map((f) => <p key={f} className="truncate font-mono">{f}</p>)}
              <p>{t("workbench2.networkTools", lang)}: {(privacy.network_tools || []).join(", ") || "—"}</p>
              <p>{t("workbench2.telemetry", lang)}: local-only</p>
            </div>
          )}
        </section>

        <section className="mt-3 rounded-xl border border-line bg-panel2 p-3">
          <h4 className="text-[11px] font-semibold text-ink2">{t("workbench2.metrics", lang)}</h4>
          {latest ? (
            <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1 text-[10px] text-ink3">
              <span>TTFT</span><span className="text-right font-mono text-ink2">{ms(metric.ttft_ms)}</span>
              <span>{t("workbench2.turn", lang)}</span><span className="text-right font-mono text-ink2">{ms(metric.total_ms)}</span>
              <span>LLM</span><span className="text-right font-mono text-ink2">{ms(metric.llm_ms)}</span>
              <span>tools</span><span className="text-right font-mono text-ink2">{String(metric.tool_calls ?? 0)}</span>
            </div>
          ) : (
            <p className="mt-1 text-[10px] text-ink3">{t("workbench2.noMetrics", lang)}</p>
          )}
          {(snap?.checkpoints || []).length > 0 && (
            <p className="mt-1.5 text-[10px] text-ink3">
              {t("workbench2.checkpoints", lang)}: {(snap?.checkpoints || []).length} · {t("workbench2.latest", lang)} {(snap?.checkpoints || []).slice(-1)[0]?.label}
            </p>
          )}
          <div className="mt-1.5 flex items-center gap-2 text-[10px] text-ink3">
            <span>
              {t("workbench2.index", lang)}: {index?.built ? `${index.files}f / ${index.chunks}c` : t("workbench2.indexMissing", lang)}
            </span>
            <button type="button" onClick={() => void rebuildIndex()} disabled={busy} className="ml-auto text-accent hover:underline">
              {index?.built ? t("workbench2.indexRebuild", lang) : t("workbench2.indexBuild", lang)}
            </button>
          </div>
          <p className="mt-1 text-[10px] text-ink3">
            {t("workbench2.changed", lang)}: {snap?.changes?.status_count ?? 0} · {t("workbench2.cost", lang)} {snap?.cost?.cost_usd == null ? "—" : `$${Number(snap.cost.cost_usd).toFixed(4)}`}
          </p>
        </section>
      </div>
    </div>
  )
}
