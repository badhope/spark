import { useCallback, useEffect, useMemo, useState } from "react"
import { fetchQueue, queueAction, type QueueState, type QueueTask } from "../api"
import { Badge, Button, IconButton, Spinner, cls } from "./ui"
import { MiniBars, RingGauge, StatCard } from "./Visuals"
import { IconClock, IconRefresh, IconSend, IconStop, IconTrash } from "./icons"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

function queueTime(ts: number | null | undefined, lang: "zh" | "en", now = Date.now()): string {
  if (!ts) return ""
  const date = new Date(ts * 1000)
  const current = new Date(now)
  const time = date.toLocaleTimeString(lang === "zh" ? "zh-CN" : "en-US", {
    hour: "2-digit",
    minute: "2-digit",
  })
  if (date.toDateString() === current.toDateString()) return time
  const day = `${String(date.getMonth() + 1).padStart(2, "0")}/${String(date.getDate()).padStart(2, "0")}`
  return date.getFullYear() === current.getFullYear() ? `${day} ${time}` : `${date.getFullYear()}/${day} ${time}`
}

function queueDuration(start: number | null | undefined, end: number | null | undefined): string {
  if (!start || !end) return ""
  const seconds = Math.max(0, end - start)
  return seconds < 60 ? `${seconds.toFixed(1)}s` : `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
}

function queueStatusLabel(task: QueueTask, lang: "zh" | "en"): string {
  const known = ["queued", "running", "ok", "failed", "cancelled", "blocked"]
  return known.includes(task.status)
    ? t(`queue.status.${task.status}` as "queue.status.queued", lang)
    : task.status
}

function statusTone(task: QueueTask): "neutral" | "ok" | "err" | "accent" | "warn" {
  if (task.status === "ok") return "ok"
  if (task.status === "failed" || task.status === "blocked") return "err"
  if (task.status === "running") return "accent"
  if (task.status === "queued") return "warn"
  return "neutral"
}

function statusDot(task: QueueTask): string {
  if (task.status === "running") return "status-dot running"
  if (task.status === "ok") return "status-dot ok"
  if (task.status === "failed" || task.status === "blocked") return "status-dot err"
  return "status-dot"
}

/** Settings → System → Queue: local idle-task queue. */
export function QueueSection() {
  const lang = useUiStore((s) => s.lang)
  const [state, setState] = useState<QueueState | null>(null)
  const [prompt, setPrompt] = useState("")
  const [title, setTitle] = useState("")
  const [kind, setKind] = useState<"agent" | "autopilot">("agent")
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")
  const [now, setNow] = useState(() => Date.now())

  const load = useCallback(async () => {
    try {
      setState(await fetchQueue())
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

  async function act(body: Record<string, unknown>) {
    setBusy(true)
    setErr("")
    try {
      await queueAction(body)
      await load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function enqueue() {
    if (!prompt.trim()) return
    await act({ action: "create", prompt: prompt.trim(), title: title.trim(), kind })
    setPrompt("")
    setTitle("")
  }

  const tasks = state?.tasks || []
  const running = tasks.some((task) => task.status === "running")
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])

  const counts = state?.counts || {}
  const queued = Number(counts.queued || 0)
  const active = Number(counts.running || 0)
  const done = Number(counts.ok || 0)
  const failed = Number(counts.failed || 0) + Number(counts.blocked || 0)
  const cancelled = Number(counts.cancelled || 0)
  const total = queued + active + done + failed + cancelled
  const terminal = done + failed + cancelled
  const successRate = terminal ? Math.round((done / terminal) * 100) : 0
  const backlogPct = total ? Math.round(((queued + active) / total) * 100) : 0
  const distribution = useMemo(() => [queued, active, done, failed], [queued, active, done, failed])

  return (
    <div className="flex flex-col gap-3">
      <section className="surface spotlight p-4">
        <div className="flex items-start gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[9px] bg-accent-soft text-accent">
            <IconClock size={15} />
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-semibold text-ink">{t("queue.title", lang)}</h3>
              <span className={cls("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px]", state?.paused ? "border-warn/35 bg-warn/10 text-warn" : "border-ok/35 bg-ok/10 text-ok")}>
                <span className={cls("status-dot", state?.paused ? "" : "ok")} />
                {state?.paused ? t("queue.pause", lang) : t("queue.status.running", lang)}
              </span>
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-ink3">{t("queue.desc", lang)}</p>
          </div>
          <div className="ml-auto flex shrink-0 items-center gap-1">
            <Button size="sm" variant="ghost" onClick={() => void act({ action: state?.paused ? "resume" : "pause" })} disabled={busy}>
              {state?.paused ? <IconSend size={12} /> : <IconStop size={12} />}
              {state?.paused ? t("queue.resume", lang) : t("queue.pause", lang)}
            </Button>
            <IconButton title={t("queue.refresh", lang)} onClick={() => void load()}>
              <IconRefresh size={13} />
            </IconButton>
          </div>
        </div>

        <div className="mt-3.5 grid grid-cols-2 gap-2 sm:grid-cols-3">
          <StatCard
            label={t("queue.metric.queued", lang)}
            value={queued}
            hint={`${t("queue.metric.total", lang)} ${total}`}
            tone="warn"
            progress={total ? (queued / total) * 100 : 0}
            icon={<IconClock size={13} />}
          />
          <StatCard
            label={t("queue.metric.running", lang)}
            value={active}
            hint={t("queue.status.running", lang)}
            tone="accent"
            progress={total ? (active / total) * 100 : 0}
            icon={<span className={cls("status-dot", active ? "running" : "")} />}
          />
          <div className="stat-card group col-span-2 flex items-center justify-between gap-3 sm:col-span-1">
            <div className="min-w-0">
              <div className="stat-label">{t("queue.metric.success", lang)}</div>
              <div className="mt-1 flex items-end gap-1.5">
                <span className="stat-value text-ok">{successRate}%</span>
                <span className="stat-hint pb-1">{done}/{terminal || 0}</span>
              </div>
              <div className="mt-2">
                <MiniBars values={distribution} height={18} tone="ok" />
              </div>
            </div>
            <RingGauge value={successRate} size={50} tone={successRate >= 80 ? "ok" : successRate >= 50 ? "warn" : "err"} />
          </div>
        </div>

        <div className="queue-flow mt-3">
          <div className="queue-flow-node">
            <span className="stat-label">{t("queue.metric.queued", lang)}</span>
            <span className="font-mono text-[16px] font-semibold text-warn">{queued}</span>
          </div>
          <span className="queue-flow-arrow" aria-hidden="true">→</span>
          <div className={cls("queue-flow-node", active > 0 && "active")}>
            <span className="stat-label">{t("queue.metric.running", lang)}</span>
            <span className="flex items-center gap-1.5 font-mono text-[16px] font-semibold text-accent">
              {active}
              {active > 0 && <span className="status-dot running" />}
            </span>
          </div>
          <span className="queue-flow-arrow" aria-hidden="true">→</span>
          <div className="queue-flow-node">
            <span className="stat-label">{t("queue.status.ok", lang)}</span>
            <span className="font-mono text-[16px] font-semibold text-ok">{done}</span>
          </div>
        </div>
        <div className="mt-2 flex items-center justify-between text-[10px] text-ink3">
          <span>{t("queue.metric.backlog", lang)} {backlogPct}%</span>
          <span>{failed > 0 ? `failed ${failed}` : ""}</span>
        </div>
        <div className="meter-track mt-1.5">
          <div className="meter-fill" style={{ width: `${backlogPct}%` }} />
        </div>

        <div className="mt-3.5 flex flex-col gap-2 border-t border-line pt-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] uppercase tracking-wide text-ink3">{t("queue.kind", lang)}</span>
            <select
              aria-label={t("queue.kind", lang)}
              value={kind}
              onChange={(e) => setKind(e.target.value === "autopilot" ? "autopilot" : "agent")}
              className="h-8 rounded-lg border border-line bg-panel2 px-2 text-[11px] text-ink outline-none focus:border-accent"
            >
              <option value="agent">{t("queue.kind.agent", lang)}</option>
              <option value="autopilot">{t("queue.kind.autopilot", lang)}</option>
            </select>
            {kind === "autopilot" && (
              <span className="text-[10px] leading-relaxed text-ink3">{t("queue.kindHint", lang)}</span>
            )}
          </div>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={t("queue.titlePh", lang)}
            className="h-8 rounded-lg border border-line bg-panel2 px-2.5 text-[12px] text-ink outline-none transition-[border-color,box-shadow] placeholder:text-ink3 focus:border-accent focus:shadow-[0_0_0_3px_var(--accent-soft)]"
          />
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={t("queue.promptPh", lang)}
            rows={2}
            className="resize-y rounded-lg border border-line bg-panel2 px-2.5 py-2 text-[12px] leading-relaxed text-ink outline-none transition-[border-color,box-shadow] placeholder:text-ink3 focus:border-accent focus:shadow-[0_0_0_3px_var(--accent-soft)]"
          />
          <div className="flex items-center gap-2">
            <Button size="sm" variant="primary" onClick={() => void enqueue()} disabled={busy || !prompt.trim()}>
              {busy ? <Spinner size={11} /> : t("queue.add", lang)}
            </Button>
            <span className="text-[10px] text-ink3">{t("queue.hint", lang)}</span>
          </div>
        </div>
        {err && <p className="mt-2 break-words text-[11px] text-err">{err}</p>}
      </section>

      <section className="surface p-4">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-ink">{t("queue.tasks", lang)}</h3>
          <span className="font-mono text-[10px] text-ink3">{tasks.length}</span>
        </div>
        {tasks.length === 0 ? (
          <p className="mt-2 rounded-lg border border-dashed border-line bg-panel2/60 px-3 py-6 text-center text-[11px] text-ink3">{t("queue.none", lang)}</p>
        ) : (
          <div className="mt-2 flex flex-col gap-1.5">
            {tasks.map((task: QueueTask) => {
              const tone = statusTone(task)
              const progress =
                task.status === "ok"
                  ? 100
                  : task.status === "failed" || task.status === "blocked" || task.status === "cancelled"
                    ? 100
                    : task.status === "running"
                      ? 100
                      : 0
              return (
                <div
                  key={task.id}
                  className={cls(
                    "group/task relative overflow-hidden rounded-xl border bg-panel2 px-3 py-2.5 transition-[border-color,transform,box-shadow] duration-200",
                    task.status === "running"
                      ? "spotlight border-accent/35"
                      : "border-line hover:-translate-y-px hover:border-line2 hover:shadow-[var(--shadow-sm)]",
                  )}
                >
                  <div className="flex items-start gap-2.5">
                    <span className={cls("mt-1.5", statusDot(task))} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink2">{task.title || task.prompt.slice(0, 60)}</span>
                        {task.kind === "autopilot" && <Badge tone="accent">Autopilot</Badge>}
                        <Badge tone={tone}>{queueStatusLabel(task, lang)}</Badge>
                        <span className="font-mono text-[9.5px] text-ink3">{t("queue.attempts", lang, { n: task.attempts })}</span>
                      </div>
                      <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-ink3">
                        <span>{t("queue.created", lang)} {queueTime(task.created_at, lang, now)}</span>
                        {task.started_at && <span>· {t("queue.started", lang)} {queueTime(task.started_at, lang, now)}</span>}
                        {task.finished_at && <span>· {t("queue.finished", lang)} {queueTime(task.finished_at, lang, now)}</span>}
                        {task.started_at && (task.finished_at || task.status === "running") && (
                          <span>· {t("queue.elapsed", lang)} {queueDuration(task.started_at, task.finished_at ?? now / 1000)}</span>
                        )}
                      </p>
                      {task.error && <p className="mt-1 line-clamp-2 text-[10px] text-err">{task.error}</p>}
                      {(task.status === "running" || task.status === "ok" || task.status === "failed" || task.status === "blocked") && (
                        <div className="meter-track mt-2">
                          <div
                            className={cls(
                              "meter-fill",
                              task.status === "running" && "meter-indeterminate",
                              task.status === "ok" && "ok",
                              (task.status === "failed" || task.status === "blocked") && "err",
                            )}
                            style={{ width: `${progress}%` }}
                          />
                        </div>
                      )}
                    </div>
                    {task.status === "queued" && (
                      <IconButton title={t("queue.cancel", lang)} onClick={() => void act({ action: "cancel", id: task.id })}>
                        <IconTrash size={13} />
                      </IconButton>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </section>
    </div>
  )
}
