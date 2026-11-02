import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { SessionRow } from "../types"
import { renameSession, searchSessions, type SessionSearchHit } from "../api"
import { IconHistory, IconMonitor, IconMoon, IconPencil, IconPlus, IconSearch, IconSettings, IconSun, IconTrash, IconX } from "./icons"
import { cls, IconButton } from "./ui"
import { BrandMark } from "./BrandMark"
import { useUiStore } from "../store/uiStore"
import { useSessionStore } from "../store/sessionStore"
import { t, type Lang, type TranslationKey } from "../i18n"

function ThemeGlyph({ theme }: { theme: "light" | "dark" | "system" }) {
  if (theme === "light") return <IconSun size={14} />
  if (theme === "dark") return <IconMoon size={14} />
  return <IconMonitor size={14} />
}

function timeLabel(ts: number, lang: Lang): string {
  const d = new Date(ts * 1000)
  const now = new Date()
  const locale = lang === "zh" ? "zh-CN" : "en-US"
  const sameDay = d.toDateString() === now.toDateString()
  if (sameDay) {
    return d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" })
  }
  return d.toLocaleDateString(locale, { month: "2-digit", day: "2-digit" })
}

function groupKey(ts: number): TranslationKey {
  const d = new Date(ts * 1000)
  const now = new Date()
  const startOfDay = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diffDays = Math.floor((startOfDay(now) - startOfDay(d)) / 86400000)
  if (diffDays <= 0) return "sidebar.today"
  if (diffDays === 1) return "sidebar.yesterday"
  if (diffDays < 7) return "sidebar.last7"
  return "sidebar.earlier"
}

const GROUP_ORDER: TranslationKey[] = ["sidebar.today", "sidebar.yesterday", "sidebar.last7", "sidebar.earlier"]

const SIDEBAR_MIN = 220
const SIDEBAR_MAX = 420
const SIDEBAR_KEY = "spark.sidebarWidth"

/**
 * Drag-to-resize for the desktop sidebar.
 *
 * The width lives in a CSS variable on the <aside> so dragging never
 * re-renders the session list; only pointer moves touch the DOM. Persisted to
 * localStorage so the layout survives reloads, double-click resets it, and
 * arrow keys move it for keyboard users.
 */
function useSidebarWidth() {
  const [value, setValue] = useState(() => {
    try {
      const stored = Number(localStorage.getItem(SIDEBAR_KEY))
      return Number.isFinite(stored) && stored >= SIDEBAR_MIN && stored <= SIDEBAR_MAX ? stored : 270
    } catch {
      return 270 // storage blocked (private mode / policy): fall back to default
    }
  })
  const drag = useRef<{ startX: number; startW: number; el: HTMLElement | null; pointerId: number } | null>(null)
  const pending = useRef(0)
  const frame = useRef(0)

  // During a drag only the CSS variable is written (no React state), so a
  // pointermove never re-renders the memoised session list. State is synced
  // once when the gesture ends.
  const writeWidth = useCallback((next: number, el: HTMLElement | null) => {
    const clamped = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, Math.round(next)))
    el?.style.setProperty("--sidebar-w", `${clamped}px`)
    pending.current = clamped
    return clamped
  }, [])

  const commit = useCallback(() => {
    const clamped = pending.current
    setValue(clamped)
    try {
      localStorage.setItem(SIDEBAR_KEY, String(clamped))
    } catch {
      /* storage blocked: the width still applies for this session */
    }
  }, [])

  const onPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const container = e.currentTarget.parentElement as HTMLElement | null
    drag.current = {
      startX: e.clientX,
      startW: container?.offsetWidth ?? 270,
      el: container,
      pointerId: e.pointerId,
    }
    pending.current = container?.offsetWidth ?? 270
    e.currentTarget.setPointerCapture(e.pointerId)
  }, [])

  useEffect(() => {
    const move = (e: PointerEvent) => {
      const d = drag.current
      if (!d) return
      // A release outside the window can skip pointerup; a move with no buttons
      // means the gesture is over, so stop instead of rubber-banding.
      if (e.buttons === 0 || e.pointerId !== d.pointerId) {
        drag.current = null
        commit()
        return
      }
      if (frame.current) return
      frame.current = requestAnimationFrame(() => {
        frame.current = 0
        const cur = drag.current
        if (cur) writeWidth(cur.startW + (e.clientX - cur.startX), cur.el)
      })
    }
    const up = () => {
      if (!drag.current) return
      drag.current = null
      if (frame.current) {
        cancelAnimationFrame(frame.current)
        frame.current = 0
      }
      commit()
    }
    window.addEventListener("pointermove", move)
    window.addEventListener("pointerup", up)
    window.addEventListener("pointercancel", up)
    return () => {
      window.removeEventListener("pointermove", move)
      window.removeEventListener("pointerup", up)
      window.removeEventListener("pointercancel", up)
      if (frame.current) cancelAnimationFrame(frame.current)
    }
  }, [writeWidth, commit])

  const apply = useCallback(
    (next: number) => {
      const el = document.getElementById("session-sidebar")
      writeWidth(next, el)
      commit()
    },
    [writeWidth, commit],
  )

  const reset = useCallback(() => apply(270), [apply])

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return
      e.preventDefault()
      apply(value + (e.key === "ArrowRight" ? 16 : -16))
    },
    [apply, value],
  )

  return { value, onPointerDown, onKeyDown, reset }
}

export const Sidebar = memo(function Sidebar() {
  const sessions = useSessionStore((s) => s.sessions)
  const status = useSessionStore((s) => s.status)
  const handleNewSession = useSessionStore((s) => s.handleNewSession)
  const handleSwitch = useSessionStore((s) => s.handleSwitch)
  const handleDelete = useSessionStore((s) => s.handleDelete)
  const refreshSessions = useSessionStore((s) => s.refreshSessions)
  const pushToast = useUiStore((s) => s.pushToast)
  const sidebarOpen = useUiStore((s) => s.sidebarOpen)
  const setSidebarOpen = useUiStore((s) => s.setSidebarOpen)
  const openSettings = useUiStore((s) => s.openSettings)
  const openCheckpoints = useUiStore((s) => s.openCheckpoints)
  const cycleTheme = useUiStore((s) => s.cycleTheme)
  const theme = useUiStore((s) => s.theme)
  const lang = useUiStore((s) => s.lang)
  const showKeywords = useSessionStore((s) => s.status?.display?.show_keywords ?? true)

  const [query, setQuery] = useState("")
  // 服务端全文检索结果（FTS5）。本地过滤只覆盖标题/keywords/model，
  // 搜不到消息正文；这里在输入后防抖查一次补充正文命中。
  const [contentHits, setContentHits] = useState<SessionSearchHit[]>([])
  const [confirmId, setConfirmId] = useState<string | null>(null)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState("")

  async function commitRename(sessionId: string) {
    const title = renameDraft.trim()
    setRenamingId(null)
    if (!title) return
    try {
      await renameSession(sessionId, title)
      pushToast("toast.sessionRenamed", "success")
      await refreshSessions()
    } catch {
      pushToast("toast.renameFailed", "error")
    }
  }

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase()
    const local = q
      ? sessions.filter((s) => (s.title + " " + (s.keywords ?? "") + " " + (s.model ?? "")).toLowerCase().includes(q))
      : sessions
    // 正文命中但本地过滤没选中的会话，补进来（按 updated_at 排序）。
    const localIds = new Set(local.map((s) => s.id))
    const fromContent = contentHits
      .filter((h) => !localIds.has(h.id))
      .map((h) => sessions.find((s) => s.id === h.id))
      .filter((s): s is SessionRow => Boolean(s))
    const filtered = q ? [...local, ...fromContent].sort((a, b) => b.updated_at - a.updated_at) : local
    const map = new Map<string, SessionRow[]>()
    for (const s of filtered) {
      const k = groupKey(s.updated_at)
      map.set(k, [...(map.get(k) ?? []), s])
    }
    return GROUP_ORDER.filter((k) => map.has(k)).map((k) => ({ key: k, items: map.get(k)! }))
  }, [sessions, query, contentHits])

  // 防抖查全文索引；只在 2 字符以上才查（更短的前缀几乎必然全量命中）。
  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) {
      setContentHits([])
      return
    }
    let alive = true
    const timer = setTimeout(() => {
      void searchSessions(q, 30)
        .then((data) => {
          if (alive) setContentHits(data.hits ?? [])
        })
        .catch(() => {
          if (alive) setContentHits([])
        })
    }, 220)
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [query])

  const currentId = status?.session_id
  const themeLabel = t(theme === "light" ? "display.light" : theme === "dark" ? "display.dark" : "display.system", lang)
  const width = useSidebarWidth()

  return (
    <>
      {sidebarOpen && (
        <div className="fixed inset-0 z-30 bg-black/50 lg:hidden" onClick={() => setSidebarOpen(false)} aria-hidden="true" />
      )}
      <aside
        id="session-sidebar"
        aria-hidden={!sidebarOpen}
        inert={!sidebarOpen}
        style={{ "--sidebar-w": `${width.value}px` } as React.CSSProperties}
        className={cls(
          // The width utility lives in the open branch only: emitting the same
          // lg:w-[var(--sidebar-w)] at base level kept it winning over
          // lg:w-0 in the closed branch (same media block, later source order),
          // so collapsing left a 270px dead strip on desktop.
          "fixed inset-y-0 left-0 z-40 flex w-[270px] shrink-0 flex-col border-r border-line bg-sidebar transition-transform duration-200 lg:static lg:translate-x-0 lg:z-auto",
          sidebarOpen
            ? "translate-x-0 lg:w-[var(--sidebar-w)]"
            : "-translate-x-full lg:w-0 lg:border-r-0 lg:overflow-hidden",
        )}
      >
        {/* 桌面端拖拽调宽：手机/平板隐藏 */}
        {sidebarOpen && (
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label={t("sidebar.resize", lang)}
            aria-valuenow={width.value}
            aria-valuemin={SIDEBAR_MIN}
            aria-valuemax={SIDEBAR_MAX}
            tabIndex={0}
            onPointerDown={width.onPointerDown}
            onDoubleClick={width.reset}
            onKeyDown={width.onKeyDown}
            className="absolute inset-y-0 -right-1 z-10 hidden w-2 cursor-col-resize touch-none lg:block hover:bg-accent/25 active:bg-accent/40"
          />
        )}
        {/* 头部 */}
        <div className="flex items-center gap-2 px-3 pt-3.5 pb-2.5">
          <BrandMark size={26} />
          <span className="text-[14.5px] font-bold tracking-[-0.02em] text-ink">Spark</span>
          <div className="ml-auto flex items-center gap-0.5">
            <IconButton title={t("sidebar.collapse", lang)} onClick={() => setSidebarOpen(false)} className="lg:hidden">
              <IconX size={16} />
            </IconButton>
          </div>
        </div>

        {/* 新建 + 搜索 */}
        <div className="space-y-2 px-3 pb-2">
          <button
            type="button"
            onClick={handleNewSession}
            className="ring-focus flex h-9 w-full items-center justify-center gap-1.5 rounded-[9px] border border-line bg-panel text-[13px] font-medium text-ink transition-[background-color,border-color,transform] duration-150 hover:border-line2 hover:bg-hover active:scale-[0.99]"
          >
            <IconPlus size={14} className="text-accent" />
            {t("common.newSession", lang)}
          </button>
          <div className="relative">
            <IconSearch size={13} className="pointer-events-none absolute top-[11px] left-2.5 text-ink3" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("sidebar.searchPh", lang)}
              className="h-8.5 w-full rounded-[9px] border border-line bg-panel pr-7 pl-8 text-[12.5px] text-ink outline-none placeholder:text-ink3 transition-[border-color,box-shadow] duration-150 focus:border-accent focus:shadow-[0_0_0_3px_var(--accent-soft)]"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery("")}
                className="absolute top-1.5 right-1.5 flex h-5 w-5 items-center justify-center rounded text-ink3 hover:bg-hover hover:text-ink"
              >
                <IconX size={12} />
              </button>
            )}
          </div>
        </div>

        {/* 会话列表 */}
        <div className="flex-1 overflow-y-auto px-3 pb-3">
          {groups.length === 0 && (
            <div className="flex flex-col items-center gap-2 py-10 text-center">
              <IconHistory size={22} className="text-ink3" />
              <p className="text-xs text-ink3">{query ? t("sidebar.noMatch", lang) : t("sidebar.empty", lang)}</p>
            </div>
          )}
          {groups.map((g) => (
            <div key={g.key} className="mt-3">
              <div className="mb-1 px-1 text-[10.5px] font-semibold tracking-wider text-ink3 uppercase">{t(g.key, lang)}</div>
              <ul className="flex flex-col gap-0.5">
                {g.items.map((s) => {
                  const active = s.id === currentId
                  return (
                    <li key={s.id} className="group relative">
                      {renamingId === s.id ? (
                        <div className="rounded-lg bg-panel px-2 py-1.5">
                          <input
                            autoFocus
                            value={renameDraft}
                            onChange={(e) => setRenameDraft(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                e.preventDefault()
                                void commitRename(s.id)
                              } else if (e.key === "Escape") {
                                e.preventDefault()
                                setRenamingId(null)
                              }
                            }}
                            onBlur={() => void commitRename(s.id)}
                            maxLength={200}
                            aria-label={t("sidebar.rename", lang)}
                            className="h-7 w-full rounded border border-accent/60 bg-canvas px-2 text-[13px] text-ink outline-none"
                          />
                        </div>
                      ) : (
                      <button
                        type="button"
                        onClick={() => {
                          if (s.id !== currentId) handleSwitch(s.id)
                          setSidebarOpen(false)
                        }}
                        onDoubleClick={(e) => {
                          e.preventDefault()
                          setRenamingId(s.id)
                          setRenameDraft(s.title && s.title !== "untitled" ? s.title : "")
                        }}
                        className={cls(
                          "group/item relative w-full rounded-[9px] px-2.5 py-2 pr-14 text-left transition-colors duration-150",
                          active
                            ? "bg-selected text-ink"
                            : "text-ink2 hover:bg-hover hover:text-ink",
                        )}
                      >
                        {/* 激活态左侧强调条：比整块反色更克制，也更容易扫读 */}
                        {active && (
                          <span
                            aria-hidden
                            className="absolute top-1/2 left-0 h-4 w-[2px] -translate-y-1/2 rounded-r bg-accent"
                          />
                        )}
                        <span className="block truncate text-[12.5px] font-medium">
                          {s.title && s.title !== "untitled" ? s.title : t("common.newChat", lang)}
                        </span>
                        {showKeywords && s.keywords && (
                          <span className="mt-1 flex flex-wrap gap-1">
                            {s.keywords
                              .split(/\s+/)
                              .filter(Boolean)
                              .slice(0, 3)
                              .map((kw) => (
                                <span key={kw} className="rounded bg-hover px-1.5 py-0.5 text-[9.5px] text-ink3">
                                  {kw}
                                </span>
                              ))}
                          </span>
                        )}
                        {(() => {
                          const hit = contentHits.find((h) => h.id === s.id)
                          if (!hit || hit.match.kind !== "content") return null
                          return (
                            <span className="mt-0.5 block truncate text-[10px] text-ink3">
                              {hit.match.snippet}
                            </span>
                          )
                        })()}
                        <span className="mt-0.5 block text-[10px] text-ink3">
                          {timeLabel(s.updated_at, lang)} · {s.model}
                        </span>
                      </button>
                      )}
                      <div className="absolute top-1.5 right-1.5 flex items-center gap-0.5">
                        <button
                          type="button"
                          title={t("sidebar.rename", lang)}
                          onClick={(e) => {
                            e.stopPropagation()
                            setRenamingId(s.id)
                            setRenameDraft(s.title && s.title !== "untitled" ? s.title : "")
                          }}
                          className={cls(
                            "flex h-6 w-6 items-center justify-center rounded-md text-ink3 opacity-0 transition-all hover:bg-hover hover:text-ink focus-visible:opacity-100",
                            active && "opacity-100",
                            "group-hover:opacity-100",
                          )}
                        >
                          <IconPencil size={12} />
                        </button>
                        {confirmId === s.id ? (
                          <span className="anim-fade flex items-center gap-1 rounded-md border border-err/40 bg-panel p-0.5 shadow-sm">
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation()
                                handleDelete(s.id)
                                setConfirmId(null)
                              }}
                              className="rounded px-1.5 py-0.5 text-[10px] font-bold text-err hover:bg-err/10"
                            >
                              {t("common.delete", lang)}
                            </button>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation()
                                setConfirmId(null)
                              }}
                              className="rounded px-1.5 py-0.5 text-[10px] text-ink2 hover:bg-hover"
                            >
                              {t("common.cancel", lang)}
                            </button>
                          </span>
                        ) : (
                          <button
                            type="button"
                            title={t("sidebar.deleteSession", lang)}
                            onClick={(e) => {
                              e.stopPropagation()
                              setConfirmId(s.id)
                            }}
                            className={cls(
                              "flex h-6 w-6 items-center justify-center rounded-md text-ink3 opacity-0 transition-all hover:bg-err/10 hover:text-err focus-visible:opacity-100",
                              active && "opacity-100",
                              "group-hover:opacity-100",
                            )}
                          >
                            <IconTrash size={12} />
                          </button>
                        )}
                      </div>
                    </li>
                  )
                })}
              </ul>
            </div>
          ))}
        </div>

        {/* 底部：模型 + 工作目录 + 设置入口 */}
        <div className="border-t border-line p-2.5">
          <div className="flex items-center gap-2 rounded-[9px] bg-panel px-2.5 py-2">
            <span className="relative flex h-6 w-6 shrink-0 items-center justify-center rounded-[7px] bg-accent-soft text-[9.5px] font-bold text-accent">
              {status?.model?.slice(0, 2).toUpperCase() ?? "AI"}
              <span
                title={status?.demo_mode ? (status.demo_reason ?? "") : t("sidebar.liveModel", lang)}
                className={cls(
                  "absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full ring-2 ring-panel",
                  status?.demo_mode ? "bg-warn" : "bg-ok",
                )}
              />
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[11.5px] font-semibold text-ink">{status?.model ?? t("sidebar.noModel", lang)}</p>
              <p className="truncate text-[10px] text-ink3" title={status?.workdir}>
                {status?.workdir ?? ""}
              </p>
            </div>
          </div>
          <div className="mt-1.5 grid grid-cols-3 gap-1.5">
            <button
              type="button"
              onClick={openSettings}
              className="ring-focus flex h-8 items-center justify-center gap-1.5 rounded-[9px] border border-line bg-panel text-[11.5px] font-medium text-ink2 transition-colors duration-150 hover:border-line2 hover:bg-hover hover:text-ink active:scale-[0.99]"
            >
              <IconSettings size={13} />
              {t("settings.title", lang)}
            </button>
            <button
              type="button"
              title={t("sidebar.checkpoint", lang)}
              onClick={openCheckpoints}
              className="ring-focus flex h-8 items-center justify-center rounded-[9px] border border-line bg-panel text-ink2 transition-colors duration-150 hover:border-line2 hover:bg-hover hover:text-ink active:scale-[0.99]"
            >
              <IconHistory size={14} />
            </button>
            <button
              type="button"
              title={t("sidebar.themeTitle", lang, { theme: themeLabel })}
              onClick={cycleTheme}
              className="ring-focus flex h-8 items-center justify-center rounded-[9px] border border-line bg-panel text-ink2 transition-colors duration-150 hover:border-line2 hover:bg-hover hover:text-ink active:scale-[0.99]"
            >
              <ThemeGlyph theme={theme} />
            </button>
          </div>
        </div>
      </aside>
    </>
  )
})
