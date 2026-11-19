import { useCallback, useEffect, useState } from "react"
import { fetchQueue, queueAction, type QueueState, type QueueTask } from "../api"
import { Button, IconButton, Spinner } from "./ui"
import { IconClock, IconRefresh, IconSend, IconStop, IconTrash } from "./icons"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

/** Settings → System → Queue: local idle-task queue. */
export function QueueSection() {
  const lang = useUiStore((s) => s.lang)
  const [state, setState] = useState<QueueState | null>(null)
  const [prompt, setPrompt] = useState("")
  const [title, setTitle] = useState("")
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")

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
    await act({ action: "create", prompt: prompt.trim(), title: title.trim() })
    setPrompt("")
    setTitle("")
  }

  const tasks = state?.tasks || []
  const queued = (state?.counts?.queued || 0)

  return (
    <div className="flex flex-col gap-3">
      <section className="rounded-xl border border-line bg-panel p-4">
        <div className="flex items-center gap-2">
          <IconClock size={14} className="text-accent" />
          <h3 className="text-sm font-semibold text-ink">{t("queue.title", lang)}</h3>
          <span className="rounded-full bg-hover px-2 py-0.5 text-[10px] text-ink3">{queued} queued</span>
          <div className="ml-auto flex items-center gap-1">
            <Button size="sm" variant="ghost" onClick={() => void act({ action: state?.paused ? "resume" : "pause" })} disabled={busy}>
              {state?.paused ? <IconSend size={12} /> : <IconStop size={12} />}
              {state?.paused ? t("queue.resume", lang) : t("queue.pause", lang)}
            </Button>
            <IconButton title={t("queue.refresh", lang)} onClick={() => void load()}>
              <IconRefresh size={13} />
            </IconButton>
          </div>
        </div>
        <p className="mt-1 text-[11px] leading-relaxed text-ink3">{t("queue.desc", lang)}</p>
        <div className="mt-3 flex flex-col gap-2">
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t("queue.titlePh", lang)} className="h-8 rounded-lg border border-line bg-panel2 px-2.5 text-[12px] text-ink outline-none placeholder:text-ink3 focus:border-accent" />
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder={t("queue.promptPh", lang)} rows={2} className="resize-y rounded-lg border border-line bg-panel2 px-2.5 py-2 text-[12px] leading-relaxed text-ink outline-none placeholder:text-ink3 focus:border-accent" />
          <div className="flex items-center gap-2">
            <Button size="sm" variant="primary" onClick={() => void enqueue()} disabled={busy || !prompt.trim()}>
              {busy ? <Spinner size={11} /> : t("queue.add", lang)}
            </Button>
            <span className="text-[10px] text-ink3">{t("queue.hint", lang)}</span>
          </div>
        </div>
        {err && <p className="mt-2 break-words text-[11px] text-err">{err}</p>}
      </section>

      <section className="rounded-xl border border-line bg-panel p-4">
        <h3 className="text-sm font-semibold text-ink">{t("queue.tasks", lang)}</h3>
        {tasks.length === 0 ? (
          <p className="mt-2 text-[11px] text-ink3">{t("queue.none", lang)}</p>
        ) : (
          <div className="mt-2 flex flex-col gap-1">
            {tasks.map((task: QueueTask) => (
              <div key={task.id} className="flex items-start gap-2 rounded-lg border border-line bg-panel2 px-3 py-2">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-[12px] font-medium text-ink2">{task.title || task.prompt.slice(0, 60)}</span>
                    <span className={task.status === "ok" ? "text-[10px] text-ok" : task.status === "failed" ? "text-[10px] text-err" : "text-[10px] text-ink3"}>{task.status}</span>
                    <span className="text-[10px] text-ink3">#{task.attempts}</span>
                  </div>
                  {task.error && <p className="mt-0.5 line-clamp-2 text-[10px] text-err">{task.error}</p>}
                </div>
                {task.status === "queued" && (
                  <IconButton title={t("queue.cancel", lang)} onClick={() => void act({ action: "cancel", id: task.id })}>
                    <IconTrash size={13} />
                  </IconButton>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
