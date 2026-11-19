import { memo, useCallback, useEffect, useState } from "react"
import { fetchGitChanges } from "../api"
import { IconFileDiff, IconRefresh } from "./icons"
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

  const entries = data?.entries ?? []
  const diffLines = (data?.diff ?? "").split("\n")

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
          <div className="max-h-40 shrink-0 overflow-y-auto border-b border-line p-1.5">
            {entries.map((e) => (
              <div key={`${e.code}-${e.path}`} className="flex items-center gap-2 rounded-md px-2 py-1 text-[11px] hover:bg-hover">
                <span className="w-5 shrink-0 font-mono text-[10px] font-semibold text-warn">{e.code.trim() || "?"}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-ink2" title={e.path}>{e.path}</span>
              </div>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-auto bg-panel2/40 p-2 font-mono text-[10.5px] leading-[1.55]">
            {diffLines.map((line, i) => (
              <div
                key={i}
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
