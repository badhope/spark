import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
import { fetchGitChanges } from "../api"
import { IconCheck, IconCopy, IconFileDiff, IconRefresh } from "./icons"
import { Spinner } from "./ui"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

/** Workbench「变更」页：只读展示当前工作区的 git 状态与 diff。
 * 不做 add/commit/push——写操作仍走 agent 工具与审批。 */
export const ChangesPanel = memo(function ChangesPanel({ active }: { active: boolean }) {
  const lang = useUiStore((s) => s.lang)
  const [data, setData] = useState<Awaited<ReturnType<typeof fetchGitChanges>> | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")
  const [query, setQuery] = useState("")
  const [copied, setCopied] = useState(false)
  const [jumpNotice, setJumpNotice] = useState("")
  const diffBoxRef = useRef<HTMLDivElement | null>(null)

  const refresh = useCallback(async () => {
    setBusy(true)
    setErr("")
    try {
      setData(await fetchGitChanges())
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => {
    if (active) void refresh()
  }, [active, refresh])

  const entries = useMemo(() => data?.entries ?? [], [data])
  const visibleEntries = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? entries.filter((e) => e.path.toLowerCase().includes(q)) : entries
  }, [entries, query])
  const diffLines = (data?.diff ?? "").split("\n")

  const scrollToPath = (path: string) => {
    setJumpNotice("")
    const idx = diffLines.findIndex((l) => l.startsWith("diff --git") && (l.includes(`a/${path} `) || l.includes(`b/${path}`)))
    if (idx < 0) {
      setJumpNotice(t("changes.notInDiff", lang))
      window.setTimeout(() => setJumpNotice(""), 2200)
      return
    }
    const row = diffBoxRef.current?.querySelector<HTMLElement>(`[data-line="${idx}"]`)
    row?.scrollIntoView({ block: "start" })
  }

  const copyDiff = async () => {
    try {
      await navigator.clipboard.writeText(data?.diff ?? "")
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1400)
    } catch {
      /* ignore clipboard denial */
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2">
        <IconFileDiff size={12} className="text-accent" />
        <span className="min-w-0 flex-1 truncate text-[11px] text-ink2">
          {data?.repo === false ? t("changes.notRepo", lang) : data?.branch || "—"}
        </span>
        {entries.length > 0 && <span className="text-[10px] text-ink3">{entries.length}</span>}
        <button
          type="button"
          onClick={() => void copyDiff()}
          disabled={!data?.diff}
          title={t("changes.copyDiff", lang)}
          aria-label={t("changes.copyDiff", lang)}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-ink3 transition-colors hover:bg-hover hover:text-ink disabled:opacity-40"
        >
          {copied ? <IconCheck size={12} className="text-ok" /> : <IconCopy size={12} />}
        </button>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={busy}
          title={t("changes.refresh", lang)}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-ink3 transition-colors hover:bg-hover hover:text-ink disabled:opacity-40"
        >
          {busy ? <Spinner size={11} /> : <IconRefresh size={12} />}
        </button>
      </div>

      {err ? (
        <p className="px-3 py-2 text-[11px] text-err">{err}</p>
      ) : data?.repo === false ? (
        <p className="px-3 py-3 text-[11px] text-ink3">{t("changes.notRepoHint", lang)}</p>
      ) : entries.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 px-3 text-center">
          <IconFileDiff size={22} className="text-ink3 opacity-60" />
          <p className="text-[11px] text-ink3">{t("changes.clean", lang)}</p>
        </div>
      ) : (
        <>
          <div className="max-h-48 shrink-0 overflow-y-auto border-b border-line p-1.5">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("changes.searchPh", lang)}
              aria-label={t("changes.searchPh", lang)}
              className="mb-1 w-full rounded-md border border-line bg-panel2 px-2 py-1 text-[11px] text-ink outline-none placeholder:text-ink3 focus:border-accent"
            />
            {visibleEntries.map((e) => (
              <button
                key={`${e.code}-${e.path}`}
                type="button"
                onClick={() => scrollToPath(e.path)}
                title={e.path}
                className="flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-[11px] hover:bg-hover"
              >
                <span className="w-5 shrink-0 font-mono text-[10px] font-semibold text-warn">{e.code.trim() || "?"}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-ink2">{e.path}</span>
              </button>
            ))}
            {visibleEntries.length === 0 && <p className="px-2 py-1 text-[10px] text-ink3">{t("changes.noMatches", lang)}</p>}
            {jumpNotice && <p className="px-2 py-1 text-[10px] text-warn">{jumpNotice}</p>}
          </div>
          <div ref={diffBoxRef} className="min-h-0 flex-1 overflow-auto bg-panel2/40 p-2 font-mono text-[10.5px] leading-[1.55]">
            {diffLines.map((line, i) => (
              <div
                key={i}
                data-line={i}
                className={
                  line.startsWith("+") && !line.startsWith("+++")
                    ? "text-ok"
                    : line.startsWith("-") && !line.startsWith("---")
                      ? "text-err"
                      : line.startsWith("@@")
                        ? "text-accent"
                        : "text-ink3"
                }
              >
                <span className="mr-2 inline-block w-6 select-none text-right opacity-50">{i + 1}</span>
                {line || " "}
              </div>
            ))}
            {data?.truncated && <p className="pt-2 text-[10px] text-warn">{t("changes.truncated", lang)}</p>}
          </div>
        </>
      )}
    </div>
  )
})
