import { useCallback, useEffect, useMemo, useState } from "react"
import {
  createRemoteLink,
  fetchPairDevices,
  fetchRemoteAudit,
  fetchRemoteStatus,
  grantRemoteAdmin,
  revokePairDevice,
  stopRemoteAccess,
  type RemoteAccessStatus,
  type RemoteAuditEntry,
  type RemoteDevice,
  type RemoteLink,
} from "../api"
import { Button, IconButton, Spinner } from "./ui"
import { IconCopy, IconGlobe, IconKey, IconRefresh, IconSmartphone, IconTrash } from "./icons"
import { useUiStore } from "../store/uiStore"
import { RemotePush } from "./RemotePush"
import { t } from "../i18n"

type Device = RemoteDevice

function modeLabel(mode: RemoteAccessStatus["mode"], lang: "zh" | "en"): string {
  if (mode === "public") return t("remote.settings.modePublic", lang)
  if (mode === "lan") return t("remote.settings.modeLan", lang)
  return t("remote.settings.modeLocal", lang)
}

/** Settings → System → Remote: one-click QR pairing for a phone. */
export function RemoteSection() {
  const lang = useUiStore((s) => s.lang)
  const pushToast = useUiStore((s) => s.pushToast)
  const requestConfirm = useUiStore((s) => s.requestConfirm)
  const [devices, setDevices] = useState<Device[]>([])
  const [audit, setAudit] = useState<RemoteAuditEntry[]>([])
  const [status, setStatus] = useState<RemoteAccessStatus | null>(null)
  const [link, setLink] = useState<RemoteLink | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const [busy, setBusy] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [err, setErr] = useState("")

  const loadAudit = useCallback(async () => {
    try {
      const data = await fetchRemoteAudit(12)
      setAudit(data.entries || [])
    } catch {
      setAudit([])
    }
  }, [])

  const load = useCallback(async () => {
    try {
      const [remote, paired] = await Promise.all([fetchRemoteStatus(), fetchPairDevices()])
      setStatus(remote)
      setDevices(paired.devices || [])
      setErr("")
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
    void loadAudit()
  }, [loadAudit])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    if (!link) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [link])

  async function generate() {
    setBusy(true)
    setErr("")
    try {
      const data = await createRemoteLink("auto")
      setLink(data)
      setStatus(data.status)
      setNow(Date.now())
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function stop() {
    setStopping(true)
    setErr("")
    try {
      const data = await stopRemoteAccess()
      setStatus(data.status)
      setDevices([])
      setLink(null)
      void loadAudit()
      pushToast("remote.settings.stopped", "success")
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setStopping(false)
    }
  }

  function askStop() {
    requestConfirm({
      title: t("remote.settings.stopTitle", lang),
      message: t("remote.settings.stopDesc", lang),
      confirmLabel: t("remote.settings.stop", lang),
      danger: true,
      onConfirm: () => void stop(),
    })
  }

  async function grantAdmin(id: string, action: "grant" | "revoke") {
    try {
      const data = await grantRemoteAdmin(id, action)
      setDevices(data.devices || [])
      void loadAudit()
      pushToast(
        action === "grant" ? "remote.settings.adminGranted" : "remote.settings.adminRevoked",
        "success",
      )
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  function askGrant(id: string, name: string) {
    requestConfirm({
      title: t("remote.settings.adminGrantTitle", lang),
      message: t("remote.settings.adminGrantDesc", lang, { name }),
      confirmLabel: t("remote.settings.adminGrant", lang),
      onConfirm: () => void grantAdmin(id, "grant"),
    })
  }

  async function revoke(id: string) {
    try {
      const data = await revokePairDevice(id)
      setDevices(data.devices || [])
      void loadAudit()
      pushToast("remote.settings.revoked", "success")
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  function copyText(value: string, key: "remote.settings.copyLink" | "remote.settings.copyCode") {
    void navigator.clipboard?.writeText(value)
    pushToast("common.copied", "success", t(key, lang))
  }

  const expires = link ? new Date(link.expires_at * 1000).toLocaleTimeString() : ""
  const remaining = link ? Math.max(0, link.expires_at - Math.floor(now / 1000)) : 0
  const expired = Boolean(link && remaining <= 0)
  const mode = status?.mode ?? "local"

  const countdown = useMemo(() => {
    if (!link) return ""
    const minutes = Math.floor(remaining / 60)
    const seconds = remaining % 60
    return `${minutes}:${seconds.toString().padStart(2, "0")}`
  }, [link, remaining])

  const modeIcon = mode === "public" ? <IconGlobe size={13} /> : <IconSmartphone size={13} />

  return (
    <div className="flex flex-col gap-3">
      <section className="overflow-hidden rounded-xl border border-line bg-panel">
        <div className="flex items-start gap-2 border-b border-line px-4 py-3">
          <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-[9px] bg-accent-soft text-accent">
            <IconSmartphone size={16} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-semibold text-ink">{t("remote.settings.title", lang)}</h3>
              <span className="inline-flex items-center gap-1 rounded-full border border-line bg-panel2 px-2 py-0.5 text-[10px] font-medium text-ink2">
                {modeIcon}
                {modeLabel(mode, lang)}
              </span>
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-ink3">{t("remote.settings.desc", lang)}</p>
          </div>
        </div>

        <div className="p-4">
          {link ? (
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
              <div className="mx-auto shrink-0 rounded-xl border border-line bg-panel2 p-2.5 sm:mx-0">
                <img
                  src={link.qr_data_url}
                  alt={t("remote.settings.scan", lang)}
                  className="h-[176px] w-[176px] rounded-lg bg-white object-contain"
                />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 text-[12px] font-semibold text-ink">
                  <IconSmartphone size={14} className="text-accent" />
                  {t("remote.settings.scan", lang)}
                </div>
                <p className="mt-1 text-[11px] leading-relaxed text-ink3">
                  {t("remote.settings.autoPair", lang)}
                </p>
                <div className="mt-3 rounded-lg border border-line bg-panel2 p-2.5">
                  <div className="break-all font-mono text-[10.5px] leading-relaxed text-ink2">{link.url}</div>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <Button size="sm" variant="primary" onClick={() => copyText(link.url, "remote.settings.copyLink")}>
                      <IconCopy size={12} /> {t("remote.settings.copyLink", lang)}
                    </Button>
                    <Button size="sm" onClick={() => void generate()} disabled={busy}>
                      {busy ? <Spinner size={11} /> : <IconRefresh size={12} />}
                      {t("remote.settings.refresh", lang)}
                    </Button>
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-2 text-[10.5px] text-ink3">
                  <span className={expired ? "text-err" : ""}>
                    {expired
                      ? t("remote.settings.expired", lang)
                      : t("remote.settings.countdown", lang, { time: countdown })}
                  </span>
                  <span className="text-ink3/60">·</span>
                  <button
                    type="button"
                    onClick={() => copyText(link.code, "remote.settings.copyCode")}
                    className="inline-flex items-center gap-1 rounded border border-line bg-panel2 px-1.5 py-0.5 font-mono text-[10.5px] tracking-[0.16em] text-ink2 hover:border-accent"
                    title={t("remote.settings.copyCode", lang)}
                  >
                    {link.code}
                    <IconCopy size={10} />
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center py-3 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-line bg-panel2 text-accent">
                <IconSmartphone size={24} />
              </div>
              <p className="mt-3 max-w-sm text-[11.5px] leading-relaxed text-ink3">
                {t("remote.settings.empty", lang)}
              </p>
              <Button className="mt-4 min-w-[168px]" variant="primary" onClick={() => void generate()} disabled={busy}>
                {busy ? <Spinner size={12} /> : <IconSmartphone size={13} />}
                {t("remote.settings.generate", lang)}
              </Button>
            </div>
          )}

          {status?.warning && (
            <p className="mt-3 rounded-lg bg-panel2 px-3 py-2 text-[10px] leading-relaxed text-ink3">
              {status.warning}
            </p>
          )}
          {err && <p className="mt-2 break-words text-[11px] text-err">{err}</p>}

          {(link || devices.length > 0) && (
            <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3">
              <p className="text-[10px] leading-relaxed text-ink3">
                {t("remote.settings.keepLocal", lang)}
              </p>
              <button
                type="button"
                onClick={askStop}
                disabled={stopping}
                className="ml-auto inline-flex items-center gap-1 rounded-lg border border-err/30 px-2.5 py-1 text-[10.5px] font-medium text-err transition-colors hover:bg-err/10 disabled:opacity-50"
              >
                {stopping ? <Spinner size={10} /> : <IconTrash size={11} />}
                {t("remote.settings.stop", lang)}
              </button>
            </div>
          )}
        </div>
      </section>

      <RemotePush />

      <section className="rounded-xl border border-line bg-panel p-4">
        <div className="flex items-center gap-2">
          <IconKey size={14} className="text-accent" />
          <h3 className="text-sm font-semibold text-ink">{t("remote.settings.devices", lang)}</h3>
          <span className="text-[10px] text-ink3">{devices.length}</span>
          <IconButton title={t("remote.settings.refresh", lang)} onClick={() => void load()} className="ml-auto">
            <IconRefresh size={12} />
          </IconButton>
        </div>
        {devices.length === 0 ? (
          <p className="mt-2 text-[11px] text-ink3">{t("remote.settings.none", lang)}</p>
        ) : (
          <div className="mt-2 flex flex-col gap-1">
            {devices.map((d) => (
              <div key={d.id} className="flex items-center gap-2 rounded-lg border border-line bg-panel2 px-3 py-2">
                <span
                  className={`h-2 w-2 shrink-0 rounded-full ${d.online ? "bg-emerald-500" : "bg-ink3/40"}`}
                  title={d.online ? t("remote.settings.online", lang) : t("remote.settings.offline", lang)}
                />
                <span className="min-w-0 flex-1 truncate text-[12px] text-ink2">
                  {d.name}
                  {d.last_path && (
                    <span className="ml-1 font-mono text-[9.5px] text-ink3">{d.last_path}</span>
                  )}
                </span>
                {d.scope === "device_admin" ? (
                  <span className="shrink-0 rounded-md border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[9.5px] font-medium text-amber-600">
                    {t("remote.settings.adminActive", lang, {
                      min: Math.max(1, Math.ceil(d.admin_expires_in / 60)),
                    })}
                  </span>
                ) : d.admin_used ? (
                  <span className="shrink-0 text-[9.5px] text-ink3" title={t("remote.settings.adminUsed", lang)}>
                    {t("remote.settings.adminUsedShort", lang)}
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => askGrant(d.id, d.name)}
                    className="shrink-0 rounded-md border border-line px-1.5 py-0.5 text-[9.5px] text-ink2 transition-colors hover:border-accent/40 hover:text-accent"
                    title={t("remote.settings.adminGrantDesc", lang, { name: d.name })}
                  >
                    {t("remote.settings.adminGrantShort", lang)}
                  </button>
                )}
                {d.scope === "device_admin" && (
                  <button
                    type="button"
                    onClick={() => void grantAdmin(d.id, "revoke")}
                    className="shrink-0 rounded-md border border-line px-1.5 py-0.5 text-[9.5px] text-ink2 transition-colors hover:border-err/40 hover:text-err"
                  >
                    {t("remote.settings.adminRevokeShort", lang)}
                  </button>
                )}
                <span className="hidden font-mono text-[10px] text-ink3 sm:inline">
                  {t("remote.settings.lastSeen", lang, {
                    time: d.last_seen_at ? new Date(d.last_seen_at * 1000).toLocaleTimeString() : "—",
                  })}
                </span>
                <IconButton title={t("remote.settings.revoke", lang)} onClick={() => void revoke(d.id)}>
                  <IconTrash size={13} />
                </IconButton>
              </div>
            ))}
          </div>
        )}
        {link && (
          <p className="mt-3 border-t border-line pt-2 text-[10px] text-ink3">
            {t("remote.settings.linkNote", lang, { time: expires })}
          </p>
        )}
      </section>

      <section className="rounded-xl border border-line bg-panel p-4">
        <div className="flex items-center gap-2">
          <IconKey size={14} className="text-accent" />
          <h3 className="text-sm font-semibold text-ink">{t("remote.settings.audit", lang)}</h3>
          <IconButton title={t("remote.settings.refresh", lang)} onClick={() => void loadAudit()} className="ml-auto">
            <IconRefresh size={12} />
          </IconButton>
        </div>
        {audit.length === 0 ? (
          <p className="mt-2 text-[11px] text-ink3">{t("remote.settings.auditEmpty", lang)}</p>
        ) : (
          <div className="mt-2 flex flex-col gap-1.5">
            {audit.map((entry) => (
              <div key={`${entry.at}-${entry.actor_id}-${entry.action}`} className="flex items-start gap-2 text-[10.5px]">
                <span className="w-14 shrink-0 font-mono text-ink3">
                  {new Date(entry.at * 1000).toLocaleTimeString()}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="font-medium text-ink2">{entry.action}</span>
                  <span className="text-ink3"> · {entry.actor_name}</span>
                  {entry.detail && <span className="block truncate text-ink3">{entry.detail}</span>}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
