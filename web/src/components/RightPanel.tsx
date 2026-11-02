import { useCallback, useEffect, useState } from "react"
import { browseUrl, fetchFsRead, fetchFsTree, type FsEntry } from "../api"
import { IconChevronRight, IconFile, IconFolder, IconGlobe, IconX } from "./icons"
import { cls } from "./ui"
import { t } from "../i18n"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import hljs from "highlight.js/lib/common"

/** 右侧常驻面板：文件树 / 文件预览 / 网页阅读三合一。
 *
 * 与左侧边栏对称，默认展开。后端能力 /api/fs/tree、/api/fs/read、
 * /api/browse 在前端均有对应观测入口（前后端 1:1）。 */
export const RightPanel = () => {
  const open = useUiStore((s) => s.rightPanelOpen)
  const close = useUiStore((s) => s.setRightPanelOpen)
  const lang = useUiStore((s) => s.lang)
  const workdir = useSessionStore((s) => s.status?.workdir ?? "")

  const [tab, setTab] = useState<"files" | "preview" | "browse">("files")
  const [stack, setStack] = useState<string[]>([])
  const [entries, setEntries] = useState<FsEntry[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")
  const [file, setFile] = useState<{ name: string; content: string; truncated?: boolean; binary?: boolean } | null>(null)
  const [fileBusy, setFileBusy] = useState(false)
  const [browseUrlValue, setBrowseUrlValue] = useState("")
  const [page, setPage] = useState<{ title?: string; url?: string; content?: string; error?: string } | null>(null)
  const [browseBusy, setBrowseBusy] = useState(false)

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

  useEffect(() => {
    if (open) void loadDir("")
  }, [open, loadDir])

  if (!open) return null

  async function openFile(name: string) {
    const path = stack.length ? [...stack, name].join("/") : name
    setFileBusy(true)
    setFile(null)
    setTab("preview")
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

  return (
    <aside className="flex w-[340px] shrink-0 flex-col border-l border-line bg-panel">
      {/* 面板头：tab 切换 + 关闭 */}
      <div className="flex items-center gap-1 border-b border-line px-2 py-2">
        <div className="flex flex-1 items-center gap-1">
          {(
            [
              ["files", "rightPanel.files", <IconFolder key="f" size={13} />],
              ["preview", "rightPanel.preview", <IconFile key="p" size={13} />],
              ["browse", "rightPanel.browse", <IconGlobe key="b" size={13} />],
            ] as const
          ).map(([key, label, icon]) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              className={cls(
                "flex items-center gap-1 rounded-[8px] px-2.5 py-1.5 text-[11.5px] font-medium transition-colors duration-150",
                tab === key ? "bg-panel3 text-ink" : "text-ink3 hover:bg-hover hover:text-ink2",
              )}
            >
              {icon}
              {t(label, lang)}
            </button>
          ))}
        </div>
        <button
          type="button"
          title={t("rightPanel.close", lang)}
          onClick={() => close(false)}
          className="flex h-7 w-7 items-center justify-center rounded-lg text-ink3 transition-colors hover:bg-hover hover:text-ink"
        >
          <IconX size={14} />
        </button>
      </div>

      {/* 文件树 tab */}
      {tab === "files" && (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center gap-2 border-b border-line px-3 py-2">
            <IconFolder size={11} className="shrink-0 text-ink3" />
            <span className="min-w-0 flex-1 truncate font-mono text-[10.5px] text-ink3" title={workdir}>
              {workdir || "—"}
            </span>
            {entries !== null && (
              <span className="shrink-0 text-[10px] text-ink3">{entries.length}</span>
            )}
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
            ) : (
              (entries ?? []).map((e) =>
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

      {/* 预览 tab */}
      {tab === "preview" && (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center gap-2 border-b border-line px-3 py-1.5">
            <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">
              {file?.name ?? t("rightPanel.noFile", lang)}
            </span>
            {file?.truncated && <span className="shrink-0 text-[10px] text-warn">{t("rightPanel.truncated", lang)}</span>}
            {file?.binary && <span className="shrink-0 text-[10px] text-warn">{t("rightPanel.binary", lang)}</span>}
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

      {/* 网页阅读 tab */}
      {tab === "browse" && (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center gap-1.5 border-b border-line p-2">
            <input
              value={browseUrlValue}
              onChange={(e) => setBrowseUrlValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void doBrowse()
              }}
              placeholder={t("rightPanel.urlPh", lang)}
              className="min-w-0 flex-1 rounded-lg border border-line bg-panel2 px-2.5 py-1.5 text-[12px] text-ink outline-none placeholder:text-ink3 focus:border-accent"
            />
            <button
              type="button"
              onClick={() => void doBrowse()}
              disabled={browseBusy || !browseUrlValue.trim()}
              className="flex h-8 shrink-0 items-center rounded-lg bg-accent px-3 text-[12px] font-medium text-white transition-colors hover:brightness-110 disabled:opacity-40"
            >
              {t("rightPanel.open", lang)}
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {browseBusy ? (
              <p className="text-[11px] text-ink3">{t("common.loading", lang)}</p>
            ) : page?.error ? (
              <p className="text-[12px] text-err">{page.error}</p>
            ) : page ? (
              <div className="flex flex-col gap-2">
                {page.title && <h3 className="text-[13px] font-semibold text-ink">{page.title}</h3>}
                {page.url && <span className="break-all font-mono text-[10px] text-ink3">{page.url}</span>}
                <div className="whitespace-pre-wrap break-words text-[12px] leading-relaxed text-ink2">{page.content}</div>
              </div>
            ) : (
              <p className="text-[11px] text-ink3">{t("rightPanel.browseHint", lang)}</p>
            )}
          </div>
        </div>
      )}
    </aside>
  )
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
