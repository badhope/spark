import { memo, useEffect, useMemo, useRef, useState } from "react"
import { downloadShareHtml, downloadTranscript } from "../sessionExport"
import { useUiStore } from "../store/uiStore"
import { useSessionStore } from "../store/sessionStore"
import { t } from "../i18n"
import { cls, Kbd } from "./ui"
import {
  IconCheck,
  IconCommand,
  IconDownload,
  IconFile,
  IconGlobe,
  IconHistory,
  IconLayers,
  IconMonitor,
  IconMoon,
  IconPlus,
  IconSettings,
  IconSun,
} from "./icons"
import type { Theme } from "../theme"

interface Cmd {
  id: string
  group: string
  title: string
  hint?: string
  icon: React.ReactNode
  run: () => void
}

export const CommandPalette = memo(function CommandPalette() {
  const open = useUiStore((s) => s.paletteOpen)
  const setOpen = useUiStore((s) => s.setPaletteOpen)
  const lang = useUiStore((s) => s.lang)
  const setLang = useUiStore((s) => s.setLang)
  const theme = useUiStore((s) => s.theme)
  const setTheme = useUiStore((s) => s.setTheme)
  const openSettings = useUiStore((s) => s.openSettings)
  const openCheckpoints = useUiStore((s) => s.openCheckpoints)
  const openFiles = useUiStore((s) => s.openFiles)
  const openBrowser = useUiStore((s) => s.openBrowser)
  const handleNewSession = useSessionStore((s) => s.handleNewSession)
  const sessions = useSessionStore((s) => s.sessions)
  const handleSwitch = useSessionStore((s) => s.handleSwitch)
  const status = useSessionStore((s) => s.status)

  const [query, setQuery] = useState("")
  const [sel, setSel] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      setQuery("")
      setSel(0)
      window.setTimeout(() => inputRef.current?.focus(), 30)
    }
  }, [open])

  // 面板内的快捷键（Ctrl+K 的全局开关在 App.tsx：本组件仅在打开时挂载，
  // 把"打开自己"的快捷键放在这里会导致它永远无法被触发）
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, setOpen])

  const commands = useMemo<Cmd[]>(() => {
    const themeOrder: Theme[] = ["light", "dark", "system"]
    const nextTheme = themeOrder[(themeOrder.indexOf(theme) + 1) % themeOrder.length]
    const themeLabel = (x: Theme) => t(x === "light" ? "display.light" : x === "dark" ? "display.dark" : "display.system", lang)
    const base: Cmd[] = [
      {
        id: "new",
        group: t("palette.groupSessions", lang),
        title: t("common.newSession", lang),
        hint: "Ctrl+N",
        icon: <IconPlus size={15} />,
        run: () => {
          handleNewSession()
          setOpen(false)
        },
      },
      {
        id: "settings",
        group: t("palette.groupGeneral", lang),
        title: t("palette.openSettings", lang),
        hint: "",
        icon: <IconSettings size={15} />,
        run: () => {
          openSettings()
          setOpen(false)
        },
      },
      {
        id: "checkpoints",
        group: t("palette.groupGeneral", lang),
        title: t("sidebar.checkpoint", lang),
        hint: "",
        icon: <IconHistory size={15} />,
        run: () => {
          openCheckpoints()
          setOpen(false)
        },
      },
      {
        id: "replay",
        group: t("palette.groupGeneral", lang),
        title: t("palette.openReplay", lang),
        hint: "",
        icon: <IconHistory size={15} />,
        run: () => {
          useUiStore.getState().openReplay()
          setOpen(false)
        },
      },
      {
        id: "export",
        group: t("palette.groupGeneral", lang),
        title: t("palette.exportChat", lang),
        hint: "md",
        icon: <IconDownload size={15} />,
        run: () => {
          setOpen(false)
          void downloadTranscript()
        },
      },
      {
        id: "share-html",
        group: t("palette.groupGeneral", lang),
        title: t("palette.shareChat", lang),
        hint: "html",
        icon: <IconDownload size={15} />,
        run: () => {
          setOpen(false)
          void downloadShareHtml()
        },
      },
      {
        id: "files",
        group: t("palette.groupGeneral", lang),
        title: t("files.title", lang),
        hint: "",
        icon: <IconFile size={15} />,
        run: () => {
          openFiles()
          setOpen(false)
        },
      },
      {
        id: "browser",
        group: t("palette.groupGeneral", lang),
        title: t("browser.title", lang),
        hint: "",
        icon: <IconGlobe size={15} />,
        run: () => {
          openBrowser()
          setOpen(false)
        },
      },
      {
        id: "theme",
        group: t("palette.groupGeneral", lang),
        title: t("palette.themeSwitch", lang, { theme: themeLabel(nextTheme) }),
        hint: t("palette.themeCurrent", lang, { theme: themeLabel(theme) }),
        icon: theme === "light" ? <IconSun size={15} /> : theme === "dark" ? <IconMoon size={15} /> : <IconMonitor size={15} />,
        run: () => setTheme(nextTheme),
      },
      {
        id: "lang",
        group: t("palette.groupGeneral", lang),
        title: t("palette.switchLang", lang),
        hint: "",
        icon: <IconLayers size={15} />,
        run: () => setLang(lang === "zh" ? "en" : "zh"),
      },
    ]
    const sessionCmds: Cmd[] = sessions.slice(0, 8).map((s) => ({
      id: `session-${s.id}`,
      group: t("palette.groupSwitch", lang),
      title: s.title && s.title !== "untitled" ? s.title : t("common.newChat", lang),
      hint: s.model,
      icon: <IconCommand size={15} />,
      run: () => {
        if (s.id !== status?.session_id) handleSwitch(s.id)
        setOpen(false)
      },
    }))
    return [...base, ...sessionCmds]
  }, [theme, lang, sessions, status, handleNewSession, handleSwitch, setOpen, openSettings, openCheckpoints, openFiles, openBrowser, setTheme, setLang])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return commands
    return commands.filter((c) => (c.title + c.group + (c.hint ?? "")).toLowerCase().includes(q))
  }, [commands, query])

  useEffect(() => {
    setSel(0)
  }, [query])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/45 pt-[12vh] backdrop-blur-[2px]" onClick={() => setOpen(false)}>
      <div
        className="anim-fade-up w-[min(92vw,560px)] overflow-hidden rounded-2xl border border-line bg-panel shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={t("palette.title", lang)}
      >
        <div className="flex items-center gap-2 border-b border-line px-4">
          <IconCommand size={15} className="text-ink3" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault()
                setSel((s) => Math.min(s + 1, filtered.length - 1))
              } else if (e.key === "ArrowUp") {
                e.preventDefault()
                setSel((s) => Math.max(s - 1, 0))
              } else if (e.key === "Enter") {
                e.preventDefault()
                const c = filtered[sel]
                if (c) c.run()
              }
            }}
            placeholder={t("palette.searchPh", lang)}
            className="h-12 w-full bg-transparent text-[14px] text-ink outline-none placeholder:text-ink3"
          />
          <Kbd>esc</Kbd>
        </div>
        <div className="max-h-[52vh] overflow-y-auto py-2">
          {filtered.length === 0 && <p className="px-4 py-8 text-center text-sm text-ink3">{t("palette.noMatch", lang)}</p>}
          {filtered.map((c, i) => {
            const showGroup = i === 0 || filtered[i - 1].group !== c.group
            return (
              <div key={c.id}>
                {showGroup && <div className="px-4 pt-2 pb-1 text-[10.5px] font-semibold tracking-wider text-ink3 uppercase">{c.group}</div>}
                <button
                  type="button"
                  onMouseEnter={() => setSel(i)}
                  onClick={c.run}
                  className={cls(
                    "flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors",
                    sel === i ? "bg-hover text-ink" : "text-ink2",
                  )}
                >
                  <span className={cls("flex h-7 w-7 items-center justify-center rounded-lg", sel === i ? "bg-accent-soft text-accent" : "bg-hover text-ink3")}>
                    {c.icon}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[13px]">{c.title}</span>
                  {c.hint && <span className="shrink-0 font-mono text-[10.5px] text-ink3">{c.hint}</span>}
                  {sel === i && <IconCheck size={13} className="shrink-0 text-accent" />}
                </button>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
})
