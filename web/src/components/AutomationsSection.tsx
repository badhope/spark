import { useCallback, useEffect, useState } from "react"
import { automationsAction, cronCheck, type AutomationRow } from "../api"
import { useUiStore } from "../store/uiStore"
import { Button, Field, Spinner, TextArea, TextInput, cls } from "./ui"
import { IconClock, IconPlus, IconRefresh, IconTrash } from "./icons"
import { t, type Lang } from "../i18n"

/** 常用频率预设：本地 agent 的真实用途几乎都在这个集合里。 */
const PRESETS: Array<{ key: string; cron: string }> = [
  { key: "auto.preset.hourly", cron: "0 * * * *" },
  { key: "auto.preset.daily9", cron: "0 9 * * *" },
  { key: "auto.preset.weekdays9", cron: "0 9 * * 1-5" },
  { key: "auto.preset.every30m", cron: "*/30 * * * *" },
  { key: "auto.preset.monday9", cron: "0 9 * * 1" },
]

function fmtTime(ts: number, lang: Lang): string {
  if (!ts) return "—"
  return new Date(ts * 1000).toLocaleString(lang === "zh" ? "zh-CN" : "en-US", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

/**
 * 定时任务面板。
 *
 * 交互取舍：
 * - 建任务必须有「下一次什么时候跑」的即时反馈，否则 cron 写错要等一天才发现；
 *   所以输入框每次变化都打一次 `/api/automations/cron_check`（防抖）。
 * - 运行结果用状态徽标直出（ok / failed / skipped），而不是只留一行日志。
 * - 删任务要二次确认：它可能正在跑。
 */
export default function AutomationsSection() {
  const lang = useUiStore((s) => s.lang)
  const pushToast = useUiStore((s) => s.pushToast)
  const [rows, setRows] = useState<AutomationRow[]>([])
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState({ name: "", prompt: "", cron: "0 9 * * *" })
  const [cronHint, setCronHint] = useState<{ valid: boolean; text: string }>({ valid: true, text: "" })
  const [confirmId, setConfirmId] = useState("")

  const load = useCallback(async () => {
    try {
      const data = await automationsAction({ action: "list" })
      setRows(data.automations ?? [])
    } catch (e) {
      pushToast("toast.error", "error", e instanceof Error ? e.message : String(e))
    }
  }, [pushToast])

  useEffect(() => {
    void load()
  }, [load])

  // cron 即时校验（防抖 350ms），让用户当场看到"下一次运行时间"。
  useEffect(() => {
    const expr = draft.cron.trim()
    if (!expr) {
      setCronHint({ valid: true, text: "" })
      return
    }
    const timer = setTimeout(() => {
      cronCheck(expr)
        .then((r) => {
          if (r.valid && r.next_run_at) {
            setCronHint({ valid: true, text: `${t("auto.nextRun", lang)}: ${fmtTime(r.next_run_at, lang)}` })
          } else {
            setCronHint({ valid: false, text: r.error ?? t("auto.invalidCron", lang) })
          }
        })
        .catch(() => setCronHint({ valid: false, text: t("auto.invalidCron", lang) }))
    }, 350)
    return () => clearTimeout(timer)
  }, [draft.cron, lang])

  async function create() {
    if (!draft.name.trim() || !draft.prompt.trim()) return
    setBusy(true)
    try {
      await automationsAction({
        action: "create",
        name: draft.name.trim(),
        prompt: draft.prompt.trim(),
        cron: draft.cron.trim(),
      })
      setDraft({ name: "", prompt: "", cron: "0 9 * * *" })
      await load()
      pushToast("auto.created", "success")
    } catch (e) {
      pushToast("toast.error", "error", e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function mutate(action: string, id: string) {
    setBusy(true)
    try {
      await automationsAction({ action, id })
      await load()
    } catch (e) {
      pushToast("toast.error", "error", e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <section className="surface flex flex-col gap-3 p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="text-[13px] font-semibold text-ink">{t("auto.title", lang)}</h3>
            <p className="mt-1 text-[11.5px] leading-relaxed text-ink3">{t("auto.desc", lang)}</p>
          </div>
          <Button size="sm" variant="ghost" onClick={() => void load()} disabled={busy}>
            <IconRefresh size={12} />
          </Button>
        </div>

        <div className="flex flex-col gap-2.5 rounded-[10px] border border-line bg-panel2 p-3">
          <div className="grid grid-cols-2 gap-2">
            <Field label={t("auto.name", lang)}>
              <TextInput
                value={draft.name}
                onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
                placeholder={t("auto.namePh", lang)}
              />
            </Field>
            <Field label={t("auto.cron", lang)} hint={
              <span className={cls("text-[10px]", cronHint.valid ? "text-ink3" : "text-err")}>
                {cronHint.text}
              </span>
            }>
              <TextInput
                value={draft.cron}
                onChange={(e) => setDraft((d) => ({ ...d, cron: e.target.value }))}
                placeholder="0 9 * * *"
                className="font-mono"
              />
            </Field>
          </div>

          <div className="flex flex-wrap gap-1">
            {PRESETS.map((p) => (
              <button
                key={p.key}
                type="button"
                onClick={() => setDraft((d) => ({ ...d, cron: p.cron }))}
                className="rounded-full border border-line bg-panel px-2 py-0.5 text-[10.5px] text-ink3 transition-colors hover:border-line2 hover:text-ink"
              >
                {t(p.key as never, lang)}
              </button>
            ))}
          </div>

          <Field label={t("auto.prompt", lang)} hint={<span className="text-[10px] text-ink3">{t("auto.promptHint", lang)}</span>}>
            <TextArea
              rows={3}
              value={draft.prompt}
              onChange={(e) => setDraft((d) => ({ ...d, prompt: e.target.value }))}
              placeholder={t("auto.promptPh", lang)}
            />
          </Field>

          <Button
            variant="soft"
            size="sm"
            disabled={busy || !draft.name.trim() || !draft.prompt.trim() || !cronHint.valid}
            onClick={() => void create()}
          >
            {busy ? <Spinner size={11} /> : <IconPlus size={12} />}
            {t("auto.create", lang)}
          </Button>
        </div>
      </section>

      <section className="surface flex flex-col gap-2 p-4">
        <h3 className="text-[13px] font-semibold text-ink">{t("auto.list", lang)}</h3>
        {rows.length === 0 ? (
          <div className="flex flex-col items-center gap-1.5 py-8 text-center">
            <IconClock size={20} className="text-ink3 opacity-60" />
            <p className="text-[11.5px] text-ink3">{t("auto.empty", lang)}</p>
          </div>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {rows.map((r) => {
              const status = r.last_status || ""
              const tone = status.startsWith("ok")
                ? "text-ok bg-ok/12 ring-ok/20"
                : status.startsWith("failed")
                  ? "text-err bg-err/12 ring-err/20"
                  : status.startsWith("skipped")
                    ? "text-warn bg-warn/12 ring-warn/20"
                    : "text-ink3 bg-hover ring-line"
              return (
                <li key={r.id} className="rounded-[10px] border border-line bg-panel2 px-3 py-2">
                  <div className="flex items-center gap-2">
                    <span className={cls("h-1.5 w-1.5 shrink-0 rounded-full", r.enabled ? "bg-ok" : "bg-ink3")} />
                    <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-ink">{r.name}</span>
                    {status && (
                      <span className={cls("shrink-0 rounded-[5px] px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset", tone)}>
                        {status}
                      </span>
                    )}
                  </div>
                  <p className="mt-1 truncate font-mono text-[10.5px] text-ink3">{r.cron}</p>
                  <p className="mt-0.5 truncate text-[10.5px] text-ink3">
                    {t("auto.nextRun", lang)}: {fmtTime(r.next_run_at, lang)}
                    {" · "}
                    {t("auto.lastRun", lang)}: {fmtTime(r.last_run, lang)}
                  </p>
                  <div className="mt-1.5 flex gap-1.5">
                    <Button size="sm" variant="ghost" disabled={busy} onClick={() => void mutate("toggle", r.id)}>
                      {r.enabled ? t("auto.disable", lang) : t("auto.enable", lang)}
                    </Button>
                    <Button size="sm" variant="ghost" disabled={busy} onClick={() => void mutate("run_now", r.id)}>
                      {t("auto.runNow", lang)}
                    </Button>
                    {confirmId === r.id ? (
                      <>
                        <Button size="sm" variant="danger" disabled={busy} onClick={() => { void mutate("delete", r.id); setConfirmId("") }}>
                          {t("common.confirm", lang)}
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setConfirmId("")}>
                          {t("common.cancel", lang)}
                        </Button>
                      </>
                    ) : (
                      <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirmId(r.id)}>
                        <IconTrash size={12} />
                      </Button>
                    )}
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </div>
  )
}
