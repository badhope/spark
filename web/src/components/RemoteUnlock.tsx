import { useState } from "react"
import { claimPairing, isAuthError, setRemoteToken } from "../api"
import { Button, Spinner } from "./ui"
import { IconKey, IconSpark } from "./icons"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

/** Remote Web unlock: pair this phone/browser with a one-time code.
 *
 * The control token is a break-glass credential and not something users should
 * paste from a console. Pairing exchanges a 6-digit code for a 7-day device
 * token; the token then rides on every API request via `fetchWithTimeout`.
 */
export function RemoteUnlock() {
  const lang = useUiStore((s) => s.lang)
  const [code, setCode] = useState("")
  const [token, setToken] = useState("")
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")
  const [manual, setManual] = useState(false)

  async function pair() {
    if (code.trim().length !== 6) return
    setBusy(true)
    setErr("")
    try {
      const name = /iPhone|Android|Mobile/i.test(navigator.userAgent) ? "手机浏览器" : "远程浏览器"
      const result = await claimPairing(code.trim(), name)
      setRemoteToken(result.token)
      window.location.reload()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function useManualToken() {
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
                if (e.key === "Enter") useManualToken()
              }}
              placeholder={t("remote.tokenPh", lang)}
              className="mt-1.5 h-10 w-full rounded-xl border border-line bg-panel2 px-3 font-mono text-[12px] text-ink outline-none placeholder:text-ink3 focus:border-accent"
            />
            <Button className="mt-3 w-full" variant="primary" onClick={useManualToken} disabled={!token.trim()}>
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
