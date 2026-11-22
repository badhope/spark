import { useEffect, useState } from "react"
import { fetchPushStatus, testPush, type PushStatus } from "../api"
import { currentPushSubscription, disablePush, enablePush, notificationSupported, pushSupported } from "../remotePush"
import { Button, Spinner } from "./ui"
import { IconBell, IconSmartphone } from "./icons"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

/** Background approval notifications for the phone that scanned the QR. */
export function RemotePush() {
  const lang = useUiStore((s) => s.lang)
  const pushToast = useUiStore((s) => s.pushToast)
  const [status, setStatus] = useState<PushStatus | null>(null)
  const [active, setActive] = useState(false)
  const [busy, setBusy] = useState(false)
  const [testing, setTesting] = useState(false)
  const [err, setErr] = useState("")
  const supported = pushSupported() && notificationSupported()

  useEffect(() => {
    void fetchPushStatus().then(setStatus).catch(() => setStatus(null))
    void currentPushSubscription().then((sub) => setActive(Boolean(sub))).catch(() => setActive(false))
  }, [])

  async function turnOn() {
    setBusy(true)
    setErr("")
    try {
      const next = await enablePush()
      setStatus(next)
      setActive(true)
      pushToast("remote.push.enabledToast", "success")
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function turnOff() {
    setBusy(true)
    setErr("")
    try {
      const next = await disablePush()
      setStatus(next)
      setActive(false)
      pushToast("remote.push.disabledToast", "info")
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function sendTest() {
    setTesting(true)
    setErr("")
    try {
      const result = await testPush()
      pushToast("remote.push.testSent", "success", String(result.selected))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setTesting(false)
    }
  }

  return (
    <section className="mt-3 rounded-xl border border-line bg-panel p-4">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-[9px] bg-accent-soft text-accent">
          <IconBell size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-ink">{t("remote.push.title", lang)}</h3>
            <span className="text-[10px] text-ink3">
              {active ? t("remote.push.on", lang) : t("remote.push.off", lang)}
            </span>
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-ink3">{t("remote.push.desc", lang)}</p>
        </div>
      </div>

      {!supported ? (
        <p className="mt-3 rounded-lg bg-panel2 px-3 py-2 text-[10.5px] leading-relaxed text-ink3">
          {t("remote.push.unsupported", lang)}
        </p>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {active ? (
            <Button size="sm" variant="primary" onClick={() => void turnOff()} disabled={busy}>
              {busy ? <Spinner size={11} /> : <IconBell size={12} />}
              {t("remote.push.disable", lang)}
            </Button>
          ) : (
            <Button size="sm" variant="primary" onClick={() => void turnOn()} disabled={busy || !status?.available}>
              {busy ? <Spinner size={11} /> : <IconBell size={12} />}
              {t("remote.push.enable", lang)}
            </Button>
          )}
          {active && (
            <Button size="sm" onClick={() => void sendTest()} disabled={testing}>
              {testing ? <Spinner size={11} /> : <IconSmartphone size={12} />}
              {t("remote.push.test", lang)}
            </Button>
          )}
          {status && (
            <span className="text-[10px] text-ink3">
              {t("remote.push.subscribers", lang, { count: String(status.subscriptions) })}
            </span>
          )}
        </div>
      )}
      {err && <p className="mt-2 break-words text-[11px] text-err">{err}</p>}
      {!status?.available && supported && !err && (
        <p className="mt-2 text-[10px] text-ink3">{t("remote.push.missingDependency", lang)}</p>
      )}
    </section>
  )
}
