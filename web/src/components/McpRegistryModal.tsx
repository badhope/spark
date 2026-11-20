import { memo, useEffect, useState } from "react"
import { searchMcpRegistry, type McpRegistryItem } from "../api"
import { Badge, Button, IconButton, Spinner, cls } from "./ui"
import { IconGlobe, IconSearch, IconX } from "./icons"
import { t } from "../i18n"
import { useUiStore } from "../store/uiStore"

interface Props {
  onClose: () => void
  onPick: (item: McpRegistryItem) => void
}

function ChinaBadge({ verdict }: { verdict: McpRegistryItem["china"]["verdict"] }) {
  const lang = useUiStore((s) => s.lang)
  if (verdict === "available") return <Badge tone="ok">{t("mcpRegistry.chinaOk", lang)}</Badge>
  if (verdict === "risk") return <Badge tone="warn">{t("mcpRegistry.chinaRisk", lang)}</Badge>
  return <Badge tone="err">{t("mcpRegistry.chinaBlocked", lang)}</Badge>
}

/** MCP registry 浏览面板：搜索官方 registry + Glama 评分 + 国内可用性标签。 */
export const McpRegistryModal = memo(function McpRegistryModal({ onClose, onPick }: Props) {
  const lang = useUiStore((s) => s.lang)
  const [query, setQuery] = useState("")
  const [applied, setApplied] = useState("")
  const [includeUnavailable, setIncludeUnavailable] = useState(false)
  const [busy, setBusy] = useState(false)
  const [items, setItems] = useState<McpRegistryItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [source, setSource] = useState<"live" | "offline">("live")
  const [filteredOut, setFilteredOut] = useState(0)

  async function runSearch(q: string, inc: boolean) {
    setBusy(true)
    setError(null)
    try {
      const data = await searchMcpRegistry(q, inc)
      setItems(data.items)
      setSource(data.source)
      setFilteredOut(data.filtered_out)
      setError(data.error)
    } catch (e) {
      setItems([])
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    void runSearch("", false)
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  function submit() {
    setApplied(query)
    void runSearch(query, includeUnavailable)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-[2px]" onClick={onClose}>
      <div
        className="anim-fade flex h-[min(84vh,640px)] w-[min(96vw,760px)] flex-col overflow-hidden rounded-2xl border border-line bg-canvas shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={t("mcpRegistry.title", lang)}
      >
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <IconGlobe size={15} className="text-accent" />
          <span className="text-sm font-semibold text-ink">{t("mcpRegistry.title", lang)}</span>
          <span className="text-[11px] text-ink3">registry.modelcontextprotocol.io + Glama</span>
          <button
            className="ml-auto flex items-center gap-1 text-[11px] text-ink3 hover:text-ink"
            onClick={() => {
              setIncludeUnavailable((v) => {
                const next = !v
                void runSearch(applied, next)
                return next
              })
            }}
          >
            <input type="checkbox" checked={includeUnavailable} readOnly className="accent-[var(--accent)]" />
            {t("mcpRegistry.showBlocked", lang)}
          </button>
          <IconButton title={t("common.close", lang)} onClick={onClose}>
            <IconX size={15} />
          </IconButton>
        </div>

        <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
          <div className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-lg border border-line bg-panel px-3 focus-within:border-accent">
            <IconSearch size={13} className="shrink-0 text-ink3" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submit()
              }}
              placeholder={t("mcpRegistry.searchPh", lang)}
              spellCheck={false}
              className="h-full min-w-0 flex-1 bg-transparent text-[12.5px] text-ink outline-none placeholder:text-ink3"
            />
          </div>
          <Button size="sm" variant="primary" onClick={submit} disabled={busy}>
            {busy ? <Spinner size={11} /> : t("common.search", lang)}
          </Button>
        </div>

        {(error || source === "offline") && (
          <div className="border-b border-line bg-warn/10 px-4 py-2 text-[11.5px] leading-relaxed text-warn">
            {error ?? t("mcpRegistry.offline", lang)}
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {busy && items === null ? (
            <div className="flex h-full items-center justify-center gap-2 text-ink3">
              <Spinner size={16} className="text-accent" /> {t("mcpRegistry.searching", lang)}
            </div>
          ) : items && items.length === 0 ? (
            <p className="py-16 text-center text-xs text-ink3">
              {t("mcpRegistry.noMatch", lang)}{filteredOut > 0 ? t("mcpRegistry.filteredOut", lang, { n: filteredOut }) : ""}。
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {items?.map((it) => (
                <div key={it.name} className="flex flex-col gap-1.5 rounded-lg border border-line bg-panel p-3">
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-ink">{it.title}</span>
                    <ChinaBadge verdict={it.china.verdict} />
                    {it.glama && (
                      <Badge tone="accent">★ {it.glama.score.toFixed(1)}</Badge>
                    )}
                  </div>
                  <p className="line-clamp-2 text-[11.5px] leading-relaxed text-ink3">{it.description}</p>
                  <div className="flex items-center gap-2 text-[11px] text-ink3">
                    <span className="font-mono">{it.name}</span>
                    <span>{it.transport === "stdio" ? "stdio" : it.transport === "remote" ? "remote http" : it.transport}</span>
                    {(it.suggested_command || it.remote_url) && (
                      <code className="max-w-[46%] truncate rounded bg-hover px-1.5 py-0.5 font-mono text-[10.5px] text-ink2">
                        {it.suggested_command ? `${it.suggested_command} ${it.suggested_args.join(" ")}` : it.remote_url}
                      </code>
                    )}
                    <Button
                      size="sm"
                      variant="soft"
                      className={cls("ml-auto", !it.suggested_command && !it.remote_url && "opacity-40")}
                      disabled={!it.suggested_command && !it.remote_url}
                      title={it.suggested_command || it.remote_url ? "" : t("mcpRegistry.remoteOnly", lang)}
                      onClick={() => onPick(it)}
                    >
                      {t("mcpRegistry.addOneClick", lang)}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
})
