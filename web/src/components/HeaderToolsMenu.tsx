import { useEffect, useRef, useState } from "react"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { IconChevronDown, IconFile, IconFork, IconGlobe, IconSpark, IconTerminal } from "./icons"
import { cls } from "./ui"
import { t } from "../i18n"

/**
 * Header overflow menu.
 *
 * Nine icon buttons in a row gave every surface equal weight, so users had no
 * visual hierarchy and the title was squeezed on narrow screens. The five
 * secondary tools live here now; the three used every session (palette, right
 * panel, help) stay visible.
 */
export function HeaderToolsMenu() {
  const lang = useUiStore((s) => s.lang)
  const openFiles = useUiStore((s) => s.openFiles)
  const openBrowser = useUiStore((s) => s.openBrowser)
  const openGraph = useUiStore((s) => s.openGraph)
  const openTerminal = useUiStore((s) => s.openTerminal)
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false)
    }
    document.addEventListener("mousedown", onDown)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onDown)
      document.removeEventListener("keydown", onKey)
    }
  }, [open])

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
      label: t("terminal.title", lang),
      hint: "Ctrl+`",
      icon: <IconTerminal size={14} />,
      run: openTerminal,
    },
  ]

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        title={t("header.tools", lang)}
        aria-haspopup="menu"
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
          role="menu"
          className="anim-fade absolute right-0 z-50 mt-1.5 w-52 overflow-hidden rounded-xl border border-line bg-panel p-1 shadow-[var(--shadow)]"
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
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
