import { useCallback, useEffect, useState } from "react"
import { fetchPairDevices, revokePairDevice, createPairCode } from "../api"
import { Button, IconButton, Spinner } from "./ui"
import { IconCopy, IconKey, IconRefresh, IconTrash } from "./icons"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

type Device = { id: string; name: string; created_at: number; expires_at: number; last_seen_at: number }

/** Settings → System → Remote: local-only pairing and device revocation. */
export function RemoteSection() {
  const lang = useUiStore((s) => s.lang)
  const pushToast = useUiStore((s) => s.pushToast)
  const [devices, setDevices] = useState<Device[]>([])
  const [code, setCode] = useState<{ code: string; expires_at: number; ttl_sec: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")

  const load = useCallback(async () => {
    try {
      const data = await fetchPairDevices()
      setDevices(data.devices || [])
      setErr("")
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function mint() {
    setBusy(true)
    setErr("")
    try {
      setCode(await createPairCode())
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function revoke(id: string) {
    try {
      const data = await revokePairDevice(id)
      setDevices(data.devices || [])
      pushToast("common.copied", "success")
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  function copyCode() {
    if (!code) return
    void navigator.clipboard?.writeText(code.code)
    pushToast("common.copied", "success")
  }

  const expires = code ? new Date(code.expires_at * 1000).toLocaleTimeString() : ""

  return (
    <div className="flex flex-col gap-3">
      <section className="rounded-xl border border-line bg-panel p-4">
        <div className="flex items-start gap-2">
          <IconKey size={15} className="mt-0.5 text-accent" />
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold text-ink">{t("remote.settings.title", lang)}</h3>
            <p className="mt-1 text-[11px] leading-relaxed text-ink3">{t("remote.settings.desc", lang)}</p>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button size="sm" variant="primary" onClick={() => void mint()} disabled={busy}>
            {busy ? <Spinner size={11} /> : t("remote.settings.mint", lang)}
          </Button>
          {code && (
            <>
              <button type="button" onClick={copyCode} title={t("remote.settings.copy", lang)} className="flex items-center gap-2 rounded-lg border border-line bg-panel2 px-3 py-1.5 font-mono text-[18px] tracking-[0.18em] text-ink hover:border-accent">
                {code.code}
                <IconCopy size={12} className="text-ink3" />
              </button>
              <span className="text-[10px] text-ink3">{t("remote.settings.expires", lang, { time: expires })}</span>
            </>
          )}
        </div>
        {err && <p className="mt-2 break-words text-[11px] text-err">{err}</p>}
      </section>

      <section className="rounded-xl border border-line bg-panel p-4">
        <div className="flex items-center gap-2">
          <IconRefresh size={14} className="text-accent" />
          <h3 className="text-sm font-semibold text-ink">{t("remote.settings.devices", lang)}</h3>
          <span className="text-[10px] text-ink3">{devices.length}</span>
        </div>
        {devices.length === 0 ? (
          <p className="mt-2 text-[11px] text-ink3">{t("remote.settings.none", lang)}</p>
        ) : (
          <div className="mt-2 flex flex-col gap-1">
            {devices.map((d) => (
              <div key={d.id} className="flex items-center gap-2 rounded-lg border border-line bg-panel2 px-3 py-2">
                <span className="min-w-0 flex-1 truncate text-[12px] text-ink2">{d.name}</span>
                <span className="font-mono text-[10px] text-ink3">{new Date(d.created_at * 1000).toLocaleDateString()}</span>
                <span className="hidden font-mono text-[10px] text-ink3 sm:inline">exp {new Date(d.expires_at * 1000).toLocaleDateString()}</span>
                <IconButton title={t("remote.settings.revoke", lang)} onClick={() => void revoke(d.id)}>
                  <IconTrash size={13} />
                </IconButton>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
