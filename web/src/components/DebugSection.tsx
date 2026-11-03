import { useCallback, useEffect, useState } from "react"
import { fetchDebug, type DebugSnapshot } from "../api"
import { useUiStore } from "../store/uiStore"
import { Badge, Button, Spinner, cls } from "./ui"
import { IconBug, IconRefresh } from "./icons"
import { t } from "../i18n"

function Row({ label, value, tone }: { label: string; value: React.ReactNode; tone?: "ok" | "err" | "warn" }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-line/60 py-1 last:border-0">
      <span className="shrink-0 text-[11px] text-ink3">{label}</span>
      <span
        className={cls(
          "min-w-0 truncate text-right font-mono text-[11px]",
          tone === "err" ? "text-err" : tone === "warn" ? "text-warn" : tone === "ok" ? "text-ok" : "text-ink2",
        )}
        title={typeof value === "string" ? value : undefined}
      >
        {value}
      </span>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="surface flex flex-col gap-1 p-3">
      <h4 className="mb-1 text-[11px] font-semibold tracking-wide text-ink3 uppercase">{title}</h4>
      {children}
    </section>
  )
}

/**
 * 运行时诊断面板。
 *
 * 设计意图：出问题时用户能自己先看到"卡在哪"，而不是只能描述"界面不动了"。
 * 因此优先展示**判断方向最省事**的三类信息——锁、线程、是否 busy——
 * 而不是堆一堆配置项。
 */
export default function DebugSection() {
  const lang = useUiStore((s) => s.lang)
  const pushToast = useUiStore((s) => s.pushToast)
  const [snap, setSnap] = useState<DebugSnapshot | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")

  const load = useCallback(async () => {
    setBusy(true)
    setErr("")
    try {
      setSnap(await fetchDebug())
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const copy = async () => {
    if (!snap) return
    try {
      await navigator.clipboard.writeText(JSON.stringify(snap, null, 2))
      pushToast("debug.copied", "success")
    } catch {
      pushToast("toast.error", "error")
    }
  }

  const threadsTotal = snap?.threads.by_name
    ? Object.values(snap.threads.by_name).reduce((a, b) => a + b, 0)
    : 0

  return (
    <div className="flex flex-col gap-3">
      <section className="surface flex items-start justify-between gap-3 p-4">
        <div className="min-w-0">
          <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-ink">
            <IconBug size={13} className="text-accent" />
            {t("debug.title", lang)}
          </h3>
          <p className="mt-1 text-[11.5px] leading-relaxed text-ink3">{t("debug.desc", lang)}</p>
        </div>
        <div className="flex shrink-0 gap-1.5">
          <Button size="sm" variant="ghost" onClick={() => void copy()} disabled={!snap}>
            {t("debug.copy", lang)}
          </Button>
          <Button size="sm" variant="soft" onClick={() => void load()} disabled={busy}>
            {busy ? <Spinner size={11} /> : <IconRefresh size={12} />}
          </Button>
        </div>
      </section>

      {err && (
        <section className="rounded-[10px] border border-err/35 bg-err/8 px-3 py-2 text-[12px] text-err">
          {t("debug.failed", lang)}: {err}
        </section>
      )}

      {snap && (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Section title={t("debug.runtime", lang)}>
              <Row label="loop" value={snap.runtime.loop_ready ? "ready" : "not ready"} tone={snap.runtime.loop_ready ? "ok" : "err"} />
              <Row label="provider" value={snap.runtime.provider_class ?? "—"} />
              <Row label="tools" value={snap.runtime.registry_tools} />
              <Row label="sandbox" value={snap.runtime.sandbox_mode ?? "—"} />
              <Row label="extra roots" value={snap.runtime.extra_roots_count} />
              <Row
                label="mcp"
                value={snap.runtime.mcp_servers.length ? snap.runtime.mcp_servers.join(", ") : "none"}
                tone={snap.runtime.mcp_errors.length ? "warn" : undefined}
              />
            </Section>

            <Section title={t("debug.locks", lang)}>
              <Row
                label="turn_lock"
                value={snap.locks.turn_locked ? "HELD" : "free"}
                tone={snap.locks.turn_locked ? "warn" : "ok"}
              />
              <Row
                label="settings_lock"
                value={snap.locks.settings_locked ? "HELD" : "free"}
                tone={snap.locks.settings_locked ? "warn" : "ok"}
              />
              <Row label="busy" value={String(snap.busy)} tone={snap.busy ? "warn" : "ok"} />
              <Row label="collected" value={`${snap.collected_ms} ms`} />
            </Section>

            <Section title={t("debug.threads", lang)}>
              <Row label="total" value={snap.threads.count} />
              {Object.entries(snap.threads.by_name)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 6)
                .map(([name, n]) => (
                  <Row key={name} label={name} value={n} />
                ))}
              {threadsTotal !== snap.threads.count && (
                <Row label="(unnamed)" value={snap.threads.count - threadsTotal} />
              )}
            </Section>

            <Section title={t("debug.store", lang)}>
              <Row label="sessions" value={snap.store.sessions ?? "—"} />
              <Row label="checkpoints" value={snap.store.checkpoints_current ?? "—"} />
              <Row label="automations" value={snap.store.automations ?? "—"} />
              <Row
                label="scheduler"
                value={snap.scheduler.running ? `running (${snap.scheduler.tick_sec ?? "?"}s)` : "stopped"}
                tone={snap.scheduler.running ? "ok" : "warn"}
              />
            </Section>
          </div>

          <Section title={t("debug.features", lang)}>
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(snap.features ?? {}).map(([k, v]) => (
                <Badge key={k} tone={typeof v === "boolean" ? (v ? "ok" : "neutral") : "accent"}>
                  {k}
                  {typeof v === "boolean" ? "" : `:${v}`}
                </Badge>
              ))}
            </div>
          </Section>

          <Section title={t("debug.env", lang)}>
            <Row label="python" value={snap.env.python} />
            <Row label="platform" value={snap.env.platform} />
            <Row label="pid" value={snap.env.pid} />
            <Row label="env vars set" value={snap.env.env_present.join(", ") || "none"} />
          </Section>
        </>
      )}
    </div>
  )
}
