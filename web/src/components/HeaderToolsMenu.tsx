import { useEffect, useRef, useState } from "react"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { IconChevronDown, IconDownload, IconExternal, IconFile, IconFork, IconGlobe, IconSpark, IconTerminal } from "./icons"
import { cls } from "./ui"
import { t } from "../i18n"
import { downloadShareHtml, downloadTranscript } from "../sessionExport"

/**
 * Header overflow menu.
 *
 * Nine icon buttons in a row gave every surface equal weight, so users had no
 * visual hierarchy and the title was squeezed on narrow screens. The secondary
 * tools, including export/share, live here; the three used every session
 * (palette, right panel, help) stay visible.
 */
export function HeaderToolsMenu() {
  const lang = useUiStore((s) => s.lang)
  const openFiles = useUiStore((s) => s.openFiles)
  const openBrowser = useUiStore((s) => s.openBrowser)
  const openGraph = useUiStore((s) => s.openGraph)
  const openTerminal = useUiStore((s) => s.openTerminal)
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const frame = window.requestAnimationFrame(() => {
      ref.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus()
    })
    const onDown = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false)
        triggerRef.current?.focus()
      }
    }
    document.addEventListener("mousedown", onDown)
    document.addEventListener("keydown", onKey)
    return () => {
      window.cancelAnimationFrame(frame)
      document.removeEventListener("mousedown", onDown)
      document.removeEventListener("keydown", onKey)
    }
  }, [open])

  function onMenuKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const items = [...(ref.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? [])]
    if (items.length === 0) return
    const current = items.indexOf(document.activeElement as HTMLButtonElement)
    let next = current
    if (event.key === "ArrowDown") next = current < 0 ? 0 : (current + 1) % items.length
    else if (event.key === "ArrowUp") next = current <= 0 ? items.length - 1 : current - 1
    else if (event.key === "Home") next = 0
    else if (event.key === "End") next = items.length - 1
    else return
    event.preventDefault()
    items[next]?.focus()
  }

  const items: { label: string; hint?: string; icon: React.ReactNode; run: () => void }[] = [
    { label: t("files.title", lang), icon: <IconFile size={14} />, run: openFiles },
    { label: t("browser.title", lang), icon: <IconGlobe size={14} />, run: openBrowser },
    { label: t("graph.title", lang), icon: <IconSpark size={14} />, run: openGraph },
    {
      label: t("sidebar.fork", lang),
      icon: <IconFork size={14} />,
      run: () => void useSessionStore.getState().handleFork(),
    },
    {
      label: t("palette.exportChat", lang),
      icon: <IconDownload size={14} />,
      run: () => void downloadTranscript(),
    },
    {
      label: t("palette.shareChat", lang),
      icon: <IconExternal size={14} />,
      run: () => void downloadShareHtml(),
    },
    {
      label: t("terminal.title", lang),
      hint: "Ctrl+`",
      icon: <IconTerminal size={14} />,
      run: openTerminal,
    },
  ]

  return (
    <div ref={ref} className="relative">
      <button
        ref={triggerRef}
        type="button"
        title={t("header.tools", lang)}
        aria-haspopup="menu"
        aria-controls="header-tools-menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={cls(
          "ring-focus inline-flex h-8 items-center gap-1 rounded-[9px] px-2 text-[12px] font-medium transition-colors",
          open ? "bg-selected text-ink" : "text-ink2 hover:bg-hover hover:text-ink",
        )}
      >
        <span className="hidden sm:inline">{t("header.tools", lang)}</span>
        <IconChevronDown size={13} className={cls("transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div
          id="header-tools-menu"
          role="menu"
          aria-label={t("header.tools", lang)}
          onKeyDown={onMenuKeyDown}
          className="anim-fade absolute right-0 z-50 mt-1.5 w-60 overflow-hidden rounded-xl border border-line bg-panel p-1 shadow-[var(--shadow)]"
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              tabIndex={-1}
              onClick={() => {
                setOpen(false)
                item.run()
              }}
              className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[12.5px] text-ink2 transition-colors hover:bg-hover hover:text-ink"
            >
              <span className="text-ink3">{item.icon}</span>
              <span className="min-w-0 flex-1 truncate">{item.label}</span>
              {item.hint && <span className="shrink-0 font-mono text-[10px] text-ink3">{item.hint}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
