import { memo, useEffect, useState } from "react"
import { fetchFsRead, fetchFsTree, type FsEntry } from "../api"
import { cls, IconButton, Spinner } from "./ui"
import { IconCheck, IconChevronRight, IconCopy, IconDownload, IconFile, IconFolder, IconSearch, IconX } from "./icons"
import { useUiStore } from "../store/uiStore"
import { useSessionStore } from "../store/sessionStore"
import { t } from "../i18n"
import { CodePreview } from "./CodePreview"

export const FileViewer = memo(function FileViewer() {
  const open = useUiStore((s) => s.filesOpen)
  const close = useUiStore((s) => s.closeFiles)
  const lang = useUiStore((s) => s.lang)
  const workdir = useSessionStore((s) => s.status?.workdir ?? "")

  const [stack, setStack] = useState<string[]>([]) // 相对路径栈（[] = 工作目录根）
  const [entries, setEntries] = useState<FsEntry[] | null>(null)
  const [query, setQuery] = useState("")
  const [copiedPath, setCopiedPath] = useState(false)
  const [copiedContent, setCopiedContent] = useState(false)
  const [err, setErr] = useState("")
  const [busy, setBusy] = useState(false)
  const [file, setFile] = useState<{ name: string; content: string; truncated?: boolean; binary?: boolean } | null>(null)
  const [fileBusy, setFileBusy] = useState(false)

  const visibleEntries = entries?.filter((e) => e.name.toLowerCase().includes(query.trim().toLowerCase())) ?? []

  async function loadDir(dir: string) {
    setBusy(true)
    setErr("")
    setFile(null)
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
  }

  useEffect(() => {
    if (open) void loadDir("")
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, close])

  async function openFile(name: string) {
    const path = stack.length ? [...stack, name].join("/") : name
    setFileBusy(true)
    setFile(null)
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

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-[2px]" onClick={close}>
      <div
        className="anim-fade flex h-[min(88vh,640px)] w-[min(96vw,980px)] flex-col overflow-hidden rounded-2xl border border-line bg-canvas shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={t("files.title", lang)}
      >
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <IconFolder size={15} className="text-accent" />
          <span className="text-sm font-semibold text-ink">{t("files.title", lang)}</span>
          <span className="ml-2 min-w-0 flex-1 truncate font-mono text-[11px] text-ink3" title={workdir}>
            {workdir || "—"}
          </span>
          <IconButton title={t("common.close", lang)} onClick={close}>
            <IconX size={15} />
          </IconButton>
        </div>

        <div className="flex min-h-0 flex-1">
          {/* 左栏：目录树 */}
          <div className="flex w-[260px] shrink-0 flex-col border-r border-line">
            {/* 面包屑 */}
            <div className="flex items-center gap-1 border-b border-line px-3 py-2 text-[11px]">
              <button
                type="button"
                onClick={() => {
                  setStack([])
                  void loadDir("")
                }}
                className={cls("rounded px-1 py-0.5 font-medium", stack.length === 0 ? "text-ink" : "text-ink3 hover:bg-hover hover:text-ink2")}
              >
                ~
              </button>
              {stack.map((seg, i) => (
                <span key={i} className="flex min-w-0 items-center gap-1 text-ink3">
                  <IconChevronRight size={10} className="shrink-0" />
                  <button
                    type="button"
                    onClick={() => {
                      const prev = stack.slice(0, i + 1)
                      setStack(prev)
                      void loadDir(prev.join("/"))
                    }}
                    className={cls("truncate rounded px-1 py-0.5", i === stack.length - 1 ? "text-ink" : "hover:bg-hover hover:text-ink2")}
                  >
                    {seg}
                  </button>
                </span>
              ))}
            </div>

            <div className="border-b border-line px-2 py-1.5">
              <div className="relative">
                <IconSearch size={11} className="pointer-events-none absolute top-2 left-2 text-ink3" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={t("files.searchPh", lang)}
                  aria-label={t("files.searchPh", lang)}
                  className="h-7 w-full rounded-md border border-line bg-panel2 pr-7 pl-6 text-[11px] text-ink outline-none placeholder:text-ink3 focus:border-accent"
                />
                {query && (
                  <button
                    type="button"
                    onClick={() => setQuery("")}
                    title={t("common.clear", lang)}
                    className="absolute top-1 right-1 flex h-5 w-5 items-center justify-center rounded text-ink3 hover:bg-hover hover:text-ink"
                  >
                    <IconX size={11} />
                  </button>
                )}
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-2">
              {busy ? (
                <div className="flex justify-center py-10">
                  <Spinner size={16} className="text-accent" />
                </div>
              ) : err ? (
                <p className="px-2 py-4 text-xs text-err">{err}</p>
              ) : (
                <ul className="flex flex-col gap-0.5">
                  {visibleEntries.map((e) =>
                    e.type === "dir" ? (
                      <li key={e.name}>
                        <button
                          type="button"
                          onClick={() => {
                            const next = stack.length ? [...stack, e.name] : [e.name]
                            setStack(next)
                            void loadDir(next.join("/"))
                          }}
                          className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[12.5px] text-ink2 transition-colors hover:bg-hover"
                        >
                          <IconFolder size={13} className="shrink-0 text-accent" />
                          <span className="truncate font-medium">{e.name}</span>
                          <IconChevronRight size={11} className="ml-auto shrink-0 text-ink3" />
                        </button>
                      </li>
                    ) : (
                      <li key={e.name}>
                        <button
                          type="button"
                          onClick={() => void openFile(e.name)}
                          className={cls(
                            "flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[12.5px] transition-colors hover:bg-hover",
                            file?.name === e.name ? "bg-selected text-ink" : "text-ink2",
                          )}
                        >
                          <IconFile size={13} className="shrink-0 text-ink3" />
                          <span className="truncate">{e.name}</span>
                          {e.size !== undefined && <span className="ml-auto shrink-0 font-mono text-[10px] text-ink3">{fmtSize(e.size)}</span>}
                        </button>
                      </li>
                    ),
                  )}
                  {entries?.length === 0 ? (
                    <p className="px-2 py-6 text-center text-xs text-ink3">{t("files.emptyDir", lang)}</p>
                  ) : visibleEntries.length === 0 ? (
                    <p className="px-2 py-6 text-center text-xs text-ink3">{t("files.noMatches", lang)}</p>
                  ) : null}
                </ul>
              )}
            </div>
          </div>

          {/* 右栏：文件内容 */}
          <div className="flex min-w-0 flex-1 flex-col bg-panel2/60">
            {fileBusy ? (
              <div className="flex flex-1 items-center justify-center">
                <Spinner size={18} className="text-accent" />
              </div>
            ) : file ? (
              <>
                <div className="flex items-center gap-2 border-b border-line px-4 py-2">
                  <IconFile size={13} className="text-ink3" />
                  <span className="truncate font-mono text-[12px] font-medium text-ink">{file.name}</span>
                  <button
                    type="button"
                    title={copiedPath ? t("common.copied", lang) : t("files.copyPath", lang)}
                    aria-label={copiedPath ? t("common.copied", lang) : t("files.copyPath", lang)}
                    onClick={() => {
                      const rel = stack.length ? [...stack, file.name].join("/") : file.name
                      navigator.clipboard?.writeText(rel).catch(() => {})
                      setCopiedPath(true)
                      window.setTimeout(() => setCopiedPath(false), 1400)
                    }}
                    className="flex h-6 shrink-0 items-center gap-1 rounded-md px-1.5 text-[10px] text-ink3 transition-colors hover:bg-hover hover:text-ink"
                  >
                    {copiedPath ? <IconCheck size={11} /> : <IconCopy size={11} />}
                    <span>{copiedPath ? t("common.copied", lang) : t("common.copy", lang)}</span>
                  </button>
                  {file.binary && <span className="rounded bg-hover px-1.5 py-0.5 text-[10px] text-ink3">{t("files.binary", lang)}</span>}
                  {file.truncated && <span className="rounded bg-hover px-1.5 py-0.5 text-[10px] text-warn">{t("files.truncated", lang)}</span>}
                  {!file.binary && (
                    <div className="ml-auto flex shrink-0 items-center gap-0.5">
                      <button
                        type="button"
                        title={t("rightPanel.copyContent", lang)}
                        aria-label={t("rightPanel.copyContent", lang)}
                        onClick={() => {
                          navigator.clipboard?.writeText(file.content).catch(() => {})
                          setCopiedContent(true)
                          window.setTimeout(() => setCopiedContent(false), 1400)
                        }}
                        className="flex h-6 w-6 items-center justify-center rounded-md text-ink3 hover:bg-hover hover:text-ink"
                      >
                        {copiedContent ? <IconCheck size={12} className="text-ok" /> : <IconCopy size={12} />}
                      </button>
                      <button
                        type="button"
                        title={t("rightPanel.download", lang)}
                        aria-label={t("rightPanel.download", lang)}
                        onClick={() => downloadFile(file.name, file.content)}
                        className="flex h-6 w-6 items-center justify-center rounded-md text-ink3 hover:bg-hover hover:text-ink"
                      >
                        <IconDownload size={12} />
                      </button>
                    </div>
                  )}
                </div>
                <div className="min-h-0 flex-1 overflow-auto p-3">
                  {file.binary ? (
                    <p className="py-8 text-center text-xs text-ink3">{t("files.binaryNote", lang)}</p>
                  ) : (
                    <CodePreview code={file.content} name={file.name} />
                  )}
                </div>
              </>
            ) : (
              <div className="flex flex-1 flex-col items-center justify-center gap-2 text-ink3">
                <IconFile size={26} />
                <p className="text-xs">{t("files.selectHint", lang)}</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
})

function downloadFile(name: string, content: string) {
  const url = URL.createObjectURL(new Blob([content], { type: "text/plain;charset=utf-8" }))
  const a = document.createElement("a")
  a.href = url
  a.download = name
  a.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function fmtSize(n: number): string {
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${n} B`
}
