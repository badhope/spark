import { useEffect, useRef, useState } from "react"
import { claimPairing, isAuthError, setRemoteToken } from "../api"
import { Button, Spinner } from "./ui"
import { IconKey, IconSpark } from "./icons"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

function readPairFromHash(): string {
  try {
    const params = new URLSearchParams(window.location.hash.replace(/^#/, ""))
    const value = (params.get("pair") || "").trim()
    return /^\d{6}$/.test(value) ? value : ""
  } catch {
    return ""
  }
}

function stripPairFromHash(): void {
  try {
    const params = new URLSearchParams(window.location.hash.replace(/^#/, ""))
    if (!params.has("pair")) return
    params.delete("pair")
    const rest = params.toString()
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${window.location.search}${rest ? `#${rest}` : ""}`,
    )
  } catch {
    /* malformed hash: the token is already saved; reload will recover */
  }
}

function deviceName(lang: "zh" | "en"): string {
  return /iPhone|Android|Mobile/i.test(navigator.userAgent)
    ? t("remote.devicePhone", lang)
    : t("remote.deviceBrowser", lang)
}

/** Remote Web unlock: pair this phone/browser with a one-time code.
 *
 * The control token is a break-glass credential and not something users should
 * paste from a console. Pairing exchanges a 6-digit code for a 7-day device
 * token; the token then rides on every API request via `fetchWithTimeout`.
 *
 * A QR link carries the code in the URL fragment (`#pair=123456`), which is
 * never sent to the server or a referrer. This component consumes it once and
 * reloads into the normal UI.
 */
export function RemoteUnlock() {
  const lang = useUiStore((s) => s.lang)
  const [autoCode] = useState(readPairFromHash)
  const [code, setCode] = useState(autoCode)
  const [token, setToken] = useState("")
  const [busy, setBusy] = useState(Boolean(autoCode))
  const [err, setErr] = useState("")
  const [manual, setManual] = useState(false)

  // Language is display-only here: a live locale switch must not restart the
  // one-time pairing claim and burn the code twice.
  const langRef = useRef(lang)
  langRef.current = lang

  useEffect(() => {
    if (!autoCode) return
    let cancelled = false
    setBusy(true)
    setErr("")
    claimPairing(autoCode, deviceName(langRef.current))
      .then((result) => {
        if (cancelled) return
        setRemoteToken(result.token)
        stripPairFromHash()
        window.location.reload()
      })
      .catch((e) => {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e))
      })
      .finally(() => {
        if (!cancelled) setBusy(false)
      })
    return () => {
      cancelled = true
    }
  }, [autoCode])

  async function pair() {
    if (code.trim().length !== 6) return
    setBusy(true)
    setErr("")
    try {
      const result = await claimPairing(code.trim(), deviceName(lang))
      setRemoteToken(result.token)
      stripPairFromHash()
      window.location.reload()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function saveManualToken() {
    const value = token.trim()
    if (!value) return
    setRemoteToken(value)
    window.location.reload()
  }

  return (
    <div className="flex h-full min-h-screen items-center justify-center bg-canvas p-4">
      <div className="w-full max-w-sm rounded-2xl border border-line bg-panel p-5 shadow-[var(--shadow)]">
        <div className="flex items-center gap-2">
          <IconSpark size={20} className="text-accent" />
          <h1 className="text-[17px] font-semibold text-ink">{t("remote.title", lang)}</h1>
        </div>
        <p className="mt-2 text-[12px] leading-relaxed text-ink3">{t("remote.desc", lang)}</p>

        {!manual ? (
          <>
            {autoCode && busy ? (
              <div className="mt-5 flex flex-col items-center gap-3 rounded-xl border border-line bg-panel2 px-4 py-5 text-center">
                <Spinner size={20} />
                <p className="text-[12px] font-medium text-ink2">{t("remote.pairing", lang)}</p>
                <p className="font-mono text-[11px] tracking-[0.2em] text-ink3">{autoCode}</p>
              </div>
            ) : (
              <>
                <label className="mt-4 block text-[11px] font-medium text-ink2">{t("remote.code", lang)}</label>
                <input
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void pair()
                  }}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  placeholder="000000"
                  className="mt-1.5 h-11 w-full rounded-xl border border-line bg-panel2 px-3 text-center font-mono text-[20px] tracking-[0.25em] text-ink outline-none placeholder:text-ink3 focus:border-accent"
                />
                <Button className="mt-3 w-full" variant="primary" onClick={() => void pair()} disabled={busy || code.length !== 6}>
                  {busy ? <Spinner size={12} /> : t("remote.pair", lang)}
                </Button>
              </>
            )}
            <button type="button" onClick={() => setManual(true)} className="mt-3 flex w-full items-center justify-center gap-1 text-[11px] text-ink3 hover:text-ink2">
              <IconKey size={12} /> {t("remote.useToken", lang)}
            </button>
          </>
        ) : (
          <>
            <label className="mt-4 block text-[11px] font-medium text-ink2">{t("remote.token", lang)}</label>
            <input
              value={token}
              onChange={(e) => setToken(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveManualToken()
              }}
              placeholder={t("remote.tokenPh", lang)}
              className="mt-1.5 h-10 w-full rounded-xl border border-line bg-panel2 px-3 font-mono text-[12px] text-ink outline-none placeholder:text-ink3 focus:border-accent"
            />
            <Button className="mt-3 w-full" variant="primary" onClick={saveManualToken} disabled={!token.trim()}>
              {t("remote.saveToken", lang)}
            </Button>
            <button type="button" onClick={() => setManual(false)} className="mt-3 w-full text-[11px] text-ink3 hover:text-ink2">
              {t("remote.backToCode", lang)}
            </button>
          </>
        )}

        {err && <p className="mt-3 break-words text-[11px] text-err">{err}</p>}
        {isAuthError(err) && <p className="mt-1 text-[10px] text-ink3">{t("remote.expired", lang)}</p>}
        <p className="mt-4 border-t border-line pt-3 text-[10px] leading-relaxed text-ink3">{t("remote.hint", lang)}</p>
      </div>
    </div>
  )
}
