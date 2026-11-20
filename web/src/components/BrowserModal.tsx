import { memo, useEffect, useState } from "react"
import { browseUrl } from "../api"
import { Button, cls, IconButton, Spinner } from "./ui"
import { IconCheck, IconChevronRight, IconCopy, IconExternal, IconGlobe, IconRefresh, IconX } from "./icons"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

/** 阅读模式浏览器：经后端 web_fetch（含 SSRF 防护）抓取网页纯文本 */
export const BrowserModal = memo(function BrowserModal() {
  const open = useUiStore((s) => s.browserOpen)
  const close = useUiStore((s) => s.closeBrowser)
  const lang = useUiStore((s) => s.lang)
  const [url, setUrl] = useState("")
  const [busy, setBusy] = useState(false)
  const [page, setPage] = useState<{ title?: string; url?: string; content?: string; error?: string } | null>(null)
  const [copied, setCopied] = useState(false)
  /** 阅读模式自己的 URL 历史：后端只返回正文，不维护浏览器会话历史。 */
  const [history, setHistory] = useState<string[]>([])
  const [historyIndex, setHistoryIndex] = useState(-1)

  async function openUrl(target?: string, push = true) {
    const u = (target ?? url).trim()
    if (!u) return
    setUrl(u)
    setBusy(true)
    setPage(null)
    try {
      const data = await browseUrl(u)
      setPage(data)
      if (push && data.url && data.url !== history[historyIndex]) {
        const next = [...history.slice(0, historyIndex + 1), data.url]
        setHistory(next)
        setHistoryIndex(next.length - 1)
      }
    } catch (e) {
      setPage({ error: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  function goHistory(step: -1 | 1) {
    const next = historyIndex + step
    if (next < 0 || next >= history.length) return
    setHistoryIndex(next)
    void openUrl(history[next], false)
  }

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, close])

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-[2px]" onClick={close}>
      <div
        className="anim-fade flex h-[min(84vh,620px)] w-[min(96vw,860px)] flex-col overflow-hidden rounded-2xl border border-line bg-canvas shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={t("browser.title", lang)}
      >
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <IconGlobe size={15} className="text-accent" />
          <span className="text-sm font-semibold text-ink">{t("browser.title", lang)}</span>
          {page?.url && (
            <div className="ml-auto flex items-center gap-0.5">
              <IconButton
                title={copied ? t("common.copied", lang) : t("browser.copy", lang)}
                onClick={() => {
                  navigator.clipboard?.writeText(page.content ?? "").catch(() => {})
                  setCopied(true)
                  window.setTimeout(() => setCopied(false), 1400)
                }}
              >
                {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
              </IconButton>
              <IconButton title={t("browser.refresh", lang)} onClick={() => void openUrl(page.url)} disabled={busy}>
                <IconRefresh size={14} />
              </IconButton>
              <a
                href={page.url}
                target="_blank"
                rel="noopener noreferrer"
                title={t("browser.openOriginal", lang)}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[9px] text-ink2 transition-colors hover:bg-hover hover:text-ink"
              >
                <IconExternal size={14} />
              </a>
            </div>
          )}
          <IconButton title={t("common.close", lang)} onClick={close} className={page?.url ? "" : "ml-auto"}>
            <IconX size={15} />
          </IconButton>
        </div>

        {/* 地址栏 */}
        <div className="flex items-center gap-1 border-b border-line px-3 py-2.5">
          <IconButton title={t("browser.back", lang)} onClick={() => goHistory(-1)} disabled={busy || historyIndex <= 0}>
            <IconChevronRight size={15} className="rotate-180" />
          </IconButton>
          <IconButton title={t("browser.forward", lang)} onClick={() => goHistory(1)} disabled={busy || historyIndex >= history.length - 1}>
            <IconChevronRight size={15} />
          </IconButton>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void openUrl()
            }}
            placeholder={t("browser.urlPh", lang)}
            spellCheck={false}
            className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-panel px-3 font-mono text-[12.5px] text-ink outline-none placeholder:text-ink3 focus:border-accent"
          />
          <Button size="sm" variant="primary" onClick={() => void openUrl()} disabled={busy || !url.trim()}>
            {busy ? <Spinner size={11} /> : t("browser.open", lang)}
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {busy ? (
            <div className="flex h-full items-center justify-center gap-2 text-ink3">
              <Spinner size={16} className="text-accent" /> {t("browser.fetching", lang)}
            </div>
          ) : page?.error ? (
            <div className="mx-auto max-w-md py-16 text-center">
              <IconGlobe size={28} className="mx-auto text-err" />
              <p className="mt-3 text-sm font-medium text-err">{t("browser.openFailed", lang)}</p>
              <p className="mt-1 break-all text-xs text-ink3">{page.error}</p>
            </div>
          ) : page ? (
            <article className="mx-auto max-w-3xl">
              {page.title && <h2 className="text-lg font-bold text-ink">{page.title}</h2>}
              {page.url && (
                <a
                  href={page.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-1 block break-all font-mono text-[11px] text-accent hover:underline"
                >
                  {page.url}
                </a>
              )}
              <div className="mt-4 whitespace-pre-wrap text-[13.5px] leading-relaxed text-ink2">{page.content}</div>
            </article>
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-ink3">
              <IconGlobe size={28} />
              <p className="text-xs">{t("browser.hint", lang)}</p>
              <div className="mt-3 flex flex-wrap justify-center gap-1.5">
                {["https://github.com", "https://react.dev", "https://docs.deepseek.com"].map((u) => (
                  <button
                    key={u}
                    type="button"
                    onClick={() => {
                      setUrl(u)
                      void openUrl(u)
                    }}
                    className={cls("rounded-full border border-line bg-panel px-2.5 py-1 font-mono text-[10.5px] text-ink2 transition-colors hover:border-accent hover:text-accent")}
                  >
                    {u.replace("https://", "")}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
})
