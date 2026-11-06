import { lazy, Suspense, useCallback, useEffect, useState } from "react"
import { Badge, Button, Field, IconButton, Spinner, TextInput, cls } from "./ui"
import { IconFile, IconRefresh, IconTrash } from "./icons"
import { wikiAction } from "../api"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

// Same reason as MessageItem: markdown + highlight.js are ~157 KB and only
// matter once a document is actually open.
const Markdown = lazy(() => import("./Markdown"))

/**
 * Repo Wiki：把 `spark wiki` 的产物读进设置面板。
 *
 * 与知识库的分工写在界面上，否则两个都叫「文档」的东西用户分不清：
 * 知识库是**人写给模型看的**参考原文；Wiki 是**从代码反向生成给人看的**
 * 结构手册，产物可 diff、可追溯行号。
 *
 * 生成走的是后端同一份 `spark.wiki` 实现，所以 Web 与 CLI 结果一致。
 */
export default function WikiSection() {
  const lang = useUiStore((s) => s.lang)
  const pushToast = useUiStore((s) => s.pushToast)
  const [exists, setExists] = useState(false)
  const [modules, setModules] = useState(0)
  const [maxFiles, setMaxFiles] = useState(240)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [doc, setDoc] = useState<{ name: string; content: string; truncated: boolean } | null>(null)
  const [moduleName, setModuleName] = useState("")
  const [stats, setStats] = useState("")

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await wikiAction({ action: "status" })
      setExists(Boolean(data.exists))
      setModules(Number(data.modules ?? 0))
      setMaxFiles(Number(data.max_files ?? 240))
      // Load the overview as soon as we know it exists: showing a populated
      // counter next to "nothing generated yet" is a contradiction the user
      // has to resolve by clicking.
      if (data.exists) {
        const index = await wikiAction({ action: "read" })
        setDoc({ name: index.name ?? "INDEX.md", content: index.content ?? "", truncated: Boolean(index.truncated) })
      }
    } catch (e) {
      pushToast("common.error", "error", e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [pushToast])

  useEffect(() => {
    void load()
  }, [load])

  async function generate() {
    setBusy(true)
    setStats("")
    try {
      const data = await wikiAction({ action: "generate", max_files: maxFiles })
      setExists(true)
      setModules(Number(data.modules ?? 0))
      setStats(
        t("wiki.stats", lang, {
          n: Number(data.modules ?? 0),
          s: Number(data.symbols ?? 0),
          b: Number(data.built ?? 0),
        }),
      )
      const index = await wikiAction({ action: "read" })
      setDoc({ name: index.name ?? "INDEX.md", content: index.content ?? "", truncated: Boolean(index.truncated) })
    } catch (e) {
      pushToast("common.error", "error", e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function open(name: string) {
    setBusy(true)
    try {
      const data = await wikiAction({ action: "read", name })
      setDoc({ name: data.name ?? name, content: data.content ?? "", truncated: Boolean(data.truncated) })
      setModuleName(name === "INDEX.md" ? "" : name)
    } catch (e) {
      pushToast("common.error", "error", e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function removeAll() {
    setBusy(true)
    try {
      await wikiAction({ action: "delete" })
      setExists(false)
      setModules(0)
      setDoc(null)
      setStats("")
    } catch (e) {
      pushToast("common.error", "error", e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-8 text-xs text-ink3">
        <Spinner size={13} /> {t("common.loading", lang)}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-ink">{t("wiki.title", lang)}</h3>
          <Badge tone={exists ? "accent" : "neutral"}>{exists ? modules : 0}</Badge>
          <span className="ml-auto flex items-center gap-1.5">
            <Button size="sm" variant="ghost" onClick={() => void open("INDEX.md")} disabled={!exists || busy}>
              <IconFile size={13} /> {t("wiki.openIndex", lang)}
            </Button>
            <IconButton title={t("wiki.refresh", lang)} onClick={() => void load()} disabled={busy}>
              <IconRefresh size={13} />
            </IconButton>
          </span>
        </div>
        <p className="text-xs leading-relaxed text-ink3">{t("wiki.desc", lang)}</p>

        <Field label={t("wiki.maxFiles", lang)}>
          <TextInput
            type="number"
            min={1}
            max={2000}
            value={maxFiles}
            onChange={(e) => setMaxFiles(Math.max(1, Math.min(2000, Number(e.target.value) || 240)))}
            spellCheck={false}
          />
        </Field>

        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => void generate()} disabled={busy}>
            {busy ? <Spinner size={12} /> : null}
            {exists ? t("wiki.regenerate", lang) : t("wiki.generate", lang)}
          </Button>
          {exists && (
            <Button size="sm" variant="ghost" onClick={() => void removeAll()} disabled={busy}>
              <IconTrash size={13} /> {t("wiki.clear", lang)}
            </Button>
          )}
          {stats && <span className="text-[11.5px] text-ink3">{stats}</span>}        </div>

        <p className="text-[11px] leading-relaxed text-ink3">{t("wiki.hint", lang)}</p>
      </section>

      <section className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-4">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-ink">{t("wiki.viewer", lang)}</h3>
          {doc && <span className="truncate font-mono text-[11px] text-ink3">{doc.name}</span>}
        </div>
        {!doc ? (
          <p className="py-6 text-center text-xs text-ink3">{t("wiki.empty", lang)}</p>
        ) : (
          <>
            {doc.truncated && <p className="text-[11px] text-warn">{t("wiki.truncated", lang)}</p>}
            <div className="max-h-[46vh] overflow-auto rounded-lg bg-code p-3 text-[12px] leading-relaxed">
              <Suspense fallback={<Spinner size={12} />}>
                <Markdown text={doc.content} />
              </Suspense>
            </div>
          </>
        )}
        {exists && moduleName && (
          <p className="text-[11px] text-ink3">
            <span className="font-mono">{moduleName}</span>
          </p>
        )}
      </section>

      <div className={cls("rounded-xl border border-line bg-panel p-3 text-[11px] leading-relaxed text-ink3")}>
        {t("wiki.cli", lang)}
        <code className="ml-1 rounded bg-code px-1.5 py-0.5 font-mono">spark wiki --refresh</code>
      </div>
    </div>
  )
}
