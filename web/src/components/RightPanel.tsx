import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react"
import {
  browseUrl,
  browserAction,
  browserNavigate,
  browserShot,
  browserState,
  fetchFsRead,
  fetchFsTree,
  type BrowserLiveState,
  type FsEntry,
} from "../api"
import { ChangesPanel } from "./ChangesPanel"
import { WorkbenchPanel } from "./WorkbenchPanel"
import { IconCheck, IconChevronRight, IconCopy, IconDownload, IconExternal, IconFile, IconFileDiff, IconFolder, IconGlobe, IconLayers, IconRefresh, IconSearch, IconSpark, IconTerminal, IconX } from "./icons"
import { cls, Spinner } from "./ui"
import { t } from "../i18n"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import hljs from "highlight.js/lib/common"

const Terminal = lazy(() => import("./Terminal").then((m) => ({ default: m.Terminal })))
const ExecutionGraph = lazy(() => import("./ExecutionGraph").then((m) => ({ default: m.ExecutionGraph })))

/** 右侧常驻面板：文件树 / 文件预览 / 网页阅读三合一。
 *
 * 与左侧边栏对称，默认展开。后端能力 /api/fs/tree、/api/fs/read、
 * /api/browse 在前端均有对应观测入口（前后端 1:1）。 */
export const RightPanel = () => {
  const open = useUiStore((s) => s.rightPanelOpen)
  const setOpen = useUiStore((s) => s.setRightPanelOpen)
  const lang = useUiStore((s) => s.lang)
  const tab = useUiStore((s) => s.workbenchTab)
  const setTab = useUiStore((s) => s.setWorkbenchTab)
  const workdir = useSessionStore((s) => s.status?.workdir ?? "")

  const [previewOpen, setPreviewOpen] = useState(false)
  const [stack, setStack] = useState<string[]>([])
  const [entries, setEntries] = useState<FsEntry[] | null>(null)
  const [fileQuery, setFileQuery] = useState("")
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")
  const [file, setFile] = useState<{ name: string; content: string; truncated?: boolean; binary?: boolean } | null>(null)
  const [fileBusy, setFileBusy] = useState(false)
  const [browseUrlValue, setBrowseUrlValue] = useState("")
  const [page, setPage] = useState<{ title?: string; url?: string; content?: string; error?: string } | null>(null)
  const [browseBusy, setBrowseBusy] = useState(false)
  const [copied, setCopied] = useState("")
  const [liveMode, setLiveMode] = useState(true)
  const [live, setLive] = useState<BrowserLiveState | null>(null)
  const [liveShot, setLiveShot] = useState("")
  const [liveBusy, setLiveBusy] = useState(false)
  const [liveError, setLiveError] = useState("")
  const shotRev = useRef(-1)
  const homeStarted = useRef(false)
  const shotRef = useRef("")

  const visibleEntries = useMemo(() => {
    const q = fileQuery.trim().toLowerCase()
    if (!q) return entries ?? []
    return (entries ?? []).filter((e) => e.name.toLowerCase().includes(q))
  }, [entries, fileQuery])

  const loadDir = useCallback(async (dir: string) => {
    setBusy(true)
    setErr("")
    try {
      const data = await fetchFsTree(dir)
      if (data.error) setErr(data.error)
      else setEntries(data.entries ?? [])
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
      setEntries(null)
    } finally {
      setBusy(false)
    }
  }, [])

  const copyText = useCallback(async (text: string, kind: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(kind)
      window.setTimeout(() => setCopied((v) => (v === kind ? "" : v)), 1400)
    } catch {
      /* ignore clipboard denial */
    }
  }, [])

  useEffect(() => {
    if (open) void loadDir("")
  }, [open, loadDir])

  useEffect(() => {
    if (!open || tab !== "web" || !liveMode) homeStarted.current = false
  }, [open, tab, liveMode])

  useEffect(() => {
    if (!open || tab !== "web" || !liveMode) return
    let cancelled = false
    async function tick() {
      try {
        const next = await browserState()
        if (cancelled) return
        setLive(next)
        setLiveError(next.error || "")
        if (!next.active && !homeStarted.current) {
          homeStarted.current = true
          setLiveBusy(true)
          try {
            const opened = await browserNavigate("")
            if (!cancelled) {
              if (!opened.ok) setLiveError(opened.error || t("rightPanel.browserStartFailed", lang))
              else if (opened.url) setBrowseUrlValue(opened.url)
            }
          } finally {
            if (!cancelled) setLiveBusy(false)
          }
        }
        const rev = typeof next.revision === "number" ? next.revision : 0
        if (next.active && rev !== shotRev.current) {
          shotRev.current = rev
          const objectUrl = await browserShot()
          if (cancelled) {
            if (objectUrl) URL.revokeObjectURL(objectUrl)
            return
          }
          if (objectUrl) {
            setLiveShot((previous) => {
              if (previous) URL.revokeObjectURL(previous)
              shotRef.current = objectUrl
              return objectUrl
            })
          }
        }
      } catch (e) {
        if (!cancelled) setLiveError(e instanceof Error ? e.message : String(e))
      }
    }
    void tick()
    const timer = window.setInterval(() => void tick(), 1500)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [open, tab, liveMode])

  useEffect(
    () => () => {
      if (shotRef.current) URL.revokeObjectURL(shotRef.current)
    },
    [],
  )

  if (!open) {
    return (
      <aside className="flex w-8 shrink-0 flex-col items-center border-l border-line bg-panel py-2">
        <button
          type="button"
          title={t("rightPanel.expand", lang)}
          onClick={() => setOpen(true)}
          className="flex h-8 w-7 items-center justify-center rounded-lg text-ink3 transition-colors hover:bg-hover hover:text-ink"
        >
          <IconChevronRight size={14} className="rotate-180" />
        </button>
      </aside>
    )
  }

  async function openFile(name: string) {
    const path = stack.length ? [...stack, name].join("/") : name
    setFileBusy(true)
    setFile(null)
    setTab("files")
    setPreviewOpen(true)
    try {
      const data = await fetchFsRead(path)
      if (data.error) setFile({ name, content: data.error })
      else setFile({ name: data.name ?? name, content: data.content ?? "", truncated: data.truncated, binary: data.binary })
    } catch (e) {
      setFile({ name, content: e instanceof Error ? e.message : String(e) })
    } finally {
      setFileBusy(false)
    }
  }

  function enterDir(name: string) {
    setStack((s) => [...s, name])
    void loadDir([...stack, name].join("/"))
  }

  function goUp() {
    const next = stack.slice(0, -1)
    setStack(next)
    void loadDir(next.join("/"))
  }

  // 工作台宽度有限，标题只显示 `~` 相对路径；完整路径放在 title 里。
  // 之前这里固定渲染 workdir，进入 src/ 后看起来仍像仓库根目录，容易误判。
  const displayPath = `~${stack.length ? `/${stack.join("/")}` : ""}`
  const fullPath = stack.length ? `${workdir}/${stack.join("/")}` : workdir

  async function doBrowse() {
    const url = browseUrlValue.trim()
    if (!url) return
    setBrowseBusy(true)
    setPage(null)
    try {
      const data = await browseUrl(url)
      setPage(data)
    } catch (e) {
      setPage({ error: e instanceof Error ? e.message : String(e) })
    } finally {
      setBrowseBusy(false)
    }
  }

  async function openLive(target?: string) {
    const url = (target ?? browseUrlValue).trim()
    if (!url) return
    setLiveBusy(true)
    setLiveError("")
    setLiveMode(true)
    try {
      const result = await browserNavigate(url)
      if (!result.ok) throw new Error(result.error || t("rightPanel.browserOpenFailed", lang))
      setBrowseUrlValue(result.url || url)
    } catch (e) {
      setLiveError(e instanceof Error ? e.message : String(e))
    } finally {
      setLiveBusy(false)
    }
  }

  async function actLive(action: "back" | "forward" | "reload" | "click", payload: { x?: number; y?: number } = {}) {
    if (!live?.active) return
    setLiveBusy(true)
    setLiveError("")
    try {
      const result = await browserAction(action, payload)
      if (!result.ok) throw new Error(result.error || t("rightPanel.browserActionFailed", lang))
    } catch (e) {
      setLiveError(e instanceof Error ? e.message : String(e))
    } finally {
      setLiveBusy(false)
    }
  }

  function clickLiveViewport(event: ReactMouseEvent<HTMLImageElement>) {
    if (!live?.active || liveBusy) return
    const box = event.currentTarget.getBoundingClientRect()
    if (!box.width || !box.height) return
    const x = Math.max(0, Math.min(1000, ((event.clientX - box.left) / box.width) * 1000))
    const y = Math.max(0, Math.min(1000, ((event.clientY - box.top) / box.height) * 1000))
    void actLive("click", { x, y })
  }

  return (
    <>
      {/* 窄屏工作台必须以抽屉覆盖，否则 340–540px 的侧栏会把聊天区挤到不可用。 */}
      <div
        className="fixed inset-0 z-30 bg-black/50 backdrop-blur-[1px] lg:hidden"
        onClick={() => setOpen(false)}
        aria-hidden="true"
      />
      <aside
        data-testid="workbench"
        aria-label={t("rightPanel.title", lang)}
        className={cls(
          "fixed inset-y-0 right-0 z-40 flex max-w-[92vw] shrink-0 flex-col border-l border-line bg-panel shadow-2xl lg:static lg:z-auto lg:max-w-[72vw] lg:shadow-none",
          tab === "run" || tab === "trace" || tab === "web" ? "w-[min(92vw,540px)] lg:w-[min(72vw,540px)]" : "w-[min(92vw,380px)] lg:w-[340px]",
        )}
      >
      {/* 面板头：tab 切换 + 关闭 */}
      <div className="flex items-center gap-1 border-b border-line px-2 py-2">
        <div className="flex flex-1 items-center gap-1">
          {(
            [
              ["files", "workbench.files", <IconFolder key="f" size={12} />],
              ["changes", "workbench.changes", <IconFileDiff key="c" size={12} />],
              ["run", "workbench.run", <IconTerminal key="r" size={12} />],
              ["web", "workbench.web", <IconGlobe key="w" size={12} />],
              ["trace", "workbench.trace", <IconSpark key="t" size={12} />],
              ["tasks", "workbench2.title", <IconLayers key="k" size={12} />],
            ] as const
          ).map(([key, label, icon]) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              className={cls(
                "flex items-center gap-0.5 rounded-[7px] px-1.5 py-1.5 text-[10.5px] font-medium transition-colors duration-150 sm:gap-1",
                tab === key ? "bg-panel3 text-ink" : "text-ink3 hover:bg-hover hover:text-ink2",
              )}
              title={t(label, lang)}
            >
              {icon}
              <span className="hidden sm:inline">{t(label, lang)}</span>
            </button>
          ))}
        </div>
        <button
          type="button"
          title={t("rightPanel.close", lang)}
          onClick={() => setOpen(false)}
          className="flex h-7 w-7 items-center justify-center rounded-lg text-ink3 transition-colors hover:bg-hover hover:text-ink"
        >
          <IconX size={14} />
        </button>
      </div>

      {/* 文件树 tab */}
      {tab === "files" && !previewOpen && (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center gap-2 border-b border-line px-3 py-2">
            <IconFolder size={11} className="shrink-0 text-ink3" />
            <span className="min-w-0 flex-1 truncate font-mono text-[10.5px] text-ink3" title={fullPath}>
              {displayPath}
            </span>
            {entries !== null && (
              <span className="shrink-0 text-[10px] text-ink3">{entries.length}</span>
            )}
          </div>
          <div className="border-b border-line px-2 py-1.5">
            <div className="relative">
              <IconSearch size={11} className="pointer-events-none absolute top-2 left-2 text-ink3" />
              <input
                value={fileQuery}
                onChange={(e) => setFileQuery(e.target.value)}
                placeholder={t("rightPanel.searchPh", lang)}
                aria-label={t("rightPanel.searchPh", lang)}
                className="h-7 w-full rounded-md border border-line bg-panel2 pr-7 pl-6 text-[11px] text-ink outline-none placeholder:text-ink3 focus:border-accent"
              />
              {fileQuery && (
                <button
                  type="button"
                  onClick={() => setFileQuery("")}
                  title={t("common.clear", lang)}
                  className="absolute top-1 right-1 flex h-5 w-5 items-center justify-center rounded text-ink3 hover:bg-hover hover:text-ink"
                >
                  <IconX size={11} />
                </button>
              )}
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {stack.length > 0 && (
              <button
                type="button"
                onClick={goUp}
                className="mb-1 flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-[12px] text-ink2 transition-colors hover:bg-hover"
              >
                <IconChevronRight size={12} className="-rotate-90" />
                ..
              </button>
            )}
            {busy && entries === null ? (
              <div className="flex flex-col gap-1.5 px-2 py-2">
                {[0, 1, 2, 3].map((i) => (
                  <div key={i} className="skeleton h-6 w-full" style={{ opacity: 1 - i * 0.18 }} />
                ))}
              </div>
            ) : err ? (
              <p className="px-2 py-1 text-[11px] text-err">{err}</p>
            ) : (entries ?? []).length === 0 ? (
              <div className="flex flex-col items-center gap-1.5 px-2 py-10 text-center">
                <IconFolder size={20} className="text-ink3 opacity-60" />
                <p className="text-[11px] text-ink3">{t("rightPanel.emptyDir", lang)}</p>
              </div>
            ) : visibleEntries.length === 0 ? (
              <div className="flex flex-col items-center gap-1.5 px-2 py-10 text-center">
                <IconSearch size={20} className="text-ink3 opacity-60" />
                <p className="text-[11px] text-ink3">{t("rightPanel.noMatches", lang)}</p>
              </div>
            ) : (
              visibleEntries.map((e) =>
                e.type === "dir" ? (
                  <button
                    key={e.name}
                    type="button"
                    onClick={() => enterDir(e.name)}
                    className="flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-left text-[12px] text-ink2 transition-colors hover:bg-hover"
                  >
                    <IconFolder size={13} className="shrink-0 text-accent" />
                    <span className="min-w-0 flex-1 truncate">{e.name}</span>
                    <IconChevronRight size={11} className="shrink-0 text-ink3" />
                  </button>
                ) : (
                  <button
                    key={e.name}
                    type="button"
                    onClick={() => openFile(e.name)}
                    className="flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-left text-[12px] text-ink2 transition-colors hover:bg-hover"
                  >
                    <IconFile size={13} className="shrink-0 text-ink3" />
                    <span className="min-w-0 flex-1 truncate">{e.name}</span>
                    {typeof e.size === "number" && (
                      <span className="shrink-0 font-mono text-[10px] text-ink3">{e.size >= 1024 ? `${(e.size / 1024).toFixed(1)}k` : `${e.size}B`}</span>
                    )}
                  </button>
                ),
              )
            )}
          </div>
        </div>
      )}

      {/* 预览（Files 内） */}
      {tab === "files" && previewOpen && (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center gap-2 border-b border-line px-3 py-1.5">
            <button
              type="button"
              onClick={() => setPreviewOpen(false)}
              title={t("workbench.files", lang)}
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-ink3 hover:bg-hover hover:text-ink"
            >
              <IconChevronRight size={12} className="rotate-180" />
            </button>
            <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">
              {file?.name ?? t("rightPanel.noFile", lang)}
            </span>
            {file?.truncated && <span className="shrink-0 text-[10px] text-warn">{t("rightPanel.truncated", lang)}</span>}
            {file?.binary && <span className="shrink-0 text-[10px] text-warn">{t("rightPanel.binary", lang)}</span>}
            {file && !file.binary && (
              <>
                <button
                  type="button"
                  title={t("files.copyPath", lang)}
                  aria-label={t("files.copyPath", lang)}
                  onClick={() => void copyText(stack.length ? [...stack, file.name].join("/") : file.name, "path")}
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-ink3 hover:bg-hover hover:text-ink"
                >
                  {copied === "path" ? <IconCheck size={12} className="text-ok" /> : <IconCopy size={12} />}
                </button>
                <button
                  type="button"
                  title={t("rightPanel.copyContent", lang)}
                  aria-label={t("rightPanel.copyContent", lang)}
                  onClick={() => void copyText(file.content, "content")}
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-ink3 hover:bg-hover hover:text-ink"
                >
                  {copied === "content" ? <IconCheck size={12} className="text-ok" /> : <IconCopy size={12} />}
                </button>
                <button
                  type="button"
                  title={t("rightPanel.download", lang)}
                  aria-label={t("rightPanel.download", lang)}
                  onClick={() => downloadFile(file.name, file.content)}
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-ink3 hover:bg-hover hover:text-ink"
                >
                  <IconDownload size={12} />
                </button>
              </>
            )}
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-2">
            {fileBusy ? (
              <p className="px-2 py-1 text-[11px] text-ink3">{t("common.loading", lang)}</p>
            ) : file?.binary ? (
              <p className="px-2 py-1 text-[11px] text-ink3">{t("rightPanel.binaryHint", lang)}</p>
            ) : file ? (
              <CodePreview code={file.content} name={file.name} />
            ) : (
              <p className="px-2 py-1 text-[11px] text-ink3">{t("rightPanel.pickFile", lang)}</p>
            )}
          </div>
        </div>
      )}

      {/* 内置浏览器 / 阅读模式 tab */}
      {tab === "web" && (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center gap-1 border-b border-line p-2">
            <button
              type="button"
              title={t("browser.back", lang)}
              onClick={() => void actLive("back")}
              disabled={!live?.active || liveBusy}
              className="flex h-8 w-7 shrink-0 items-center justify-center rounded-lg text-ink3 hover:bg-hover hover:text-ink disabled:opacity-30"
            >
              <IconChevronRight size={14} className="rotate-180" />
            </button>
            <button
              type="button"
              title={t("browser.forward", lang)}
              onClick={() => void actLive("forward")}
              disabled={!live?.active || liveBusy}
              className="flex h-8 w-7 shrink-0 items-center justify-center rounded-lg text-ink3 hover:bg-hover hover:text-ink disabled:opacity-30"
            >
              <IconChevronRight size={14} />
            </button>
            <button
              type="button"
              title={t("browser.refresh", lang)}
              onClick={() => void actLive("reload")}
              disabled={!live?.active || liveBusy}
              className="flex h-8 w-7 shrink-0 items-center justify-center rounded-lg text-ink3 hover:bg-hover hover:text-ink disabled:opacity-30"
            >
              <IconRefresh size={14} />
            </button>
            <input
              value={browseUrlValue}
              onChange={(e) => setBrowseUrlValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void (liveMode ? openLive() : doBrowse())
              }}
              placeholder={t("rightPanel.urlPh", lang)}
              className="min-w-0 flex-1 rounded-lg border border-line bg-panel2 px-2.5 py-1.5 text-[12px] text-ink outline-none placeholder:text-ink3 focus:border-accent"
            />
            <button
              type="button"
              onClick={() => void (liveMode ? openLive() : doBrowse())}
              disabled={(liveMode ? liveBusy : browseBusy) || !browseUrlValue.trim()}
              className="flex h-8 shrink-0 items-center rounded-lg bg-accent px-3 text-[12px] font-medium text-white transition-colors hover:brightness-110 disabled:opacity-40"
            >
              {t("rightPanel.open", lang)}
            </button>
          </div>
          <div className="flex items-center gap-2 border-b border-line px-3 py-1.5 text-[10px] text-ink3">
            <span className={cls("inline-block h-1.5 w-1.5 rounded-full", live?.active ? "bg-ok" : "bg-ink3")} />
            <span className="min-w-0 flex-1 truncate">
              {liveMode ? t("browser.liveTitle", lang) : t("browser.readerTitle", lang)}
            </span>
            <button
              type="button"
              onClick={() => setLiveMode((value) => !value)}
              className="shrink-0 rounded-full border border-line bg-panel2 px-2 py-0.5 text-[10px] text-ink2 hover:border-accent hover:text-accent"
            >
              {liveMode ? t("browser.readerToggle", lang) : t("browser.liveToggle", lang)}
            </button>
          </div>

          {liveMode ? (
            <div className="flex min-h-0 flex-1 flex-col">
              {(live?.url || live?.title) && (
                <div className="flex items-center gap-1 border-b border-line px-3 py-1.5">
                  <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">{live?.title || live?.url}</span>
                  {live?.url && (
                    <a
                      href={live.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={t("browser.openOriginal", lang)}
                      className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-ink3 hover:bg-hover hover:text-ink"
                    >
                      <IconExternal size={12} />
                    </a>
                  )}
                </div>
              )}
              {live?.url && <span className="block truncate px-3 py-1 font-mono text-[10px] text-ink3">{live.url}</span>}
              <div className="relative min-h-0 flex-1 overflow-auto bg-panel2 p-2">
                {liveError || live?.error ? (
                  <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center">
                    <IconGlobe size={24} className="text-err" />
                    <p className="text-[11px] text-err">{liveError || live?.error}</p>
                    <p className="text-[10px] text-ink3">{t("browser.liveErrorHint", lang)}</p>
                  </div>
                ) : liveBusy && !live?.active ? (
                  <div className="flex h-full items-center justify-center gap-2 text-ink3">
                    <Spinner size={14} className="text-accent" />
                    <span className="text-[11px]">{t("browser.liveStarting", lang)}</span>
                  </div>
                ) : live?.active ? (
                  liveShot ? (
                    <div className="flex flex-col gap-2">
                      <img
                        src={liveShot}
                        alt={live?.title || live?.url || "browser"}
                        draggable={false}
                        onClick={clickLiveViewport}
                        className="block w-full cursor-crosshair rounded-lg border border-line bg-white shadow-sm"
                      />
                      <p className="text-center text-[10px] text-ink3">{t("browser.liveClickHint", lang)}</p>
                    </div>
                  ) : (
                    <div className="flex h-full items-center justify-center gap-2 text-ink3">
                      <Spinner size={14} className="text-accent" />
                      <span className="text-[11px]">{t("browser.liveWaiting", lang)}</span>
                    </div>
                  )
                ) : (
                  <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-ink3">
                    <IconGlobe size={28} />
                    <p className="text-[11px]">{t("browser.liveHint", lang)}</p>
                    <p className="text-[10px]">{t("browser.liveModelHint", lang)}</p>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="min-h-0 flex-1 overflow-y-auto p-3">
              {browseBusy ? (
                <p className="text-[11px] text-ink3">{t("common.loading", lang)}</p>
              ) : page?.error ? (
                <p className="text-[12px] text-err">{page.error}</p>
              ) : page ? (
                <div className="flex flex-col gap-2">
                  <div className="flex items-center gap-1">
                    {page.title && <h3 className="min-w-0 flex-1 text-[13px] font-semibold text-ink">{page.title}</h3>}
                    {page.content && (
                      <button
                        type="button"
                        title={t("browser.copy", lang)}
                        onClick={() => void copyText(page.content ?? "", "page")}
                        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-ink3 hover:bg-hover hover:text-ink"
                      >
                        {copied === "page" ? <IconCheck size={12} className="text-ok" /> : <IconCopy size={12} />}
                      </button>
                    )}
                    {page.url && (
                      <a
                        href={page.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        title={t("browser.openOriginal", lang)}
                        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-ink3 hover:bg-hover hover:text-ink"
                      >
                        <IconExternal size={12} />
                      </a>
                    )}
                  </div>
                  {page.url && <span className="break-all font-mono text-[10px] text-ink3">{page.url}</span>}
                  <div className="whitespace-pre-wrap break-words text-[12px] leading-relaxed text-ink2">{page.content}</div>
                </div>
              ) : (
                <p className="text-[11px] text-ink3">{t("rightPanel.browseHint", lang)}</p>
              )}
            </div>
          )}
        </div>
      )}

      {tab === "changes" && <ChangesPanel active />}

      {tab === "run" && (
        <Suspense fallback={<PanelLoading />}>
          <Terminal embedded />
        </Suspense>
      )}

      {tab === "trace" && (
        <Suspense fallback={<PanelLoading />}>
          <ExecutionGraph embedded />
        </Suspense>
      )}

      {tab === "tasks" && <WorkbenchPanel />}
      </aside>
    </>
  )
}

function PanelLoading() {
  return <div className="flex flex-1 items-center justify-center p-4 text-[11px] text-ink3">…</div>
}

function downloadFile(name: string, content: string) {
  const url = URL.createObjectURL(new Blob([content], { type: "text/plain;charset=utf-8" }))
  const a = document.createElement("a")
  a.href = url
  a.download = name
  a.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** 行号 + 高亮的精简文件预览（与文件查看器一致） */
function CodePreview({ code, name }: { code: string; name: string }) {
  const ext = name.includes(".") ? name.split(".").pop()!.toLowerCase() : ""
  let html: string
  try {
    const lang = ext && hljs.getLanguage(ext) ? ext : "plaintext"
    html = hljs.highlight(code, { language: lang, ignoreIllegals: true }).value
  } catch {
    html = code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  }
  const lines = splitHighlightedLines(html)
  return (
    <div className="overflow-auto font-mono text-[11.5px] leading-[1.55]">
      <table className="w-full border-collapse">
        <tbody>
          {lines.map((line, i) => (
            <tr key={i} className="align-top">
              <td className="w-8 pr-2.5 text-right text-ink3 select-none">{i + 1}</td>
              <td className="pr-3">
                <span dangerouslySetInnerHTML={{ __html: line }} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** 补齐 highlight.js 行级切分后未闭合的 span（与 FileViewer 同源逻辑） */
function splitHighlightedLines(html: string): string[] {
  const rawLines = html.split("\n")
  const out: string[] = []
  const openStack: string[] = []
  for (const rawLine of rawLines) {
    let prefix = ""
    const escapedOpen = openStack.length
    for (const m of rawLine.matchAll(/<span[^>]*>/g)) {
      openStack.push(m[0])
      prefix += m[0]
    }
    const closeCount = (rawLine.match(/<\/span>/g) ?? []).length
    for (let i = 0; i < closeCount; i++) {
      if (openStack.length) openStack.pop()
      if (i >= escapedOpen) prefix += "</span>"
    }
    out.push(prefix + rawLine + "</span>".repeat(openStack.length))
  }
  return out
}
