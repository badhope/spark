import { useCallback, useEffect, useState } from "react"
import { Badge, Button, Spinner } from "./ui"
import { IconExternal } from "./icons"
import { fetchPlugins, setPluginEnabled, type PluginItem } from "../api"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

/**
 * 插件：工作区 `.spark/plugins/<name>/` 下的本地扩展。
 *
 * 这里负责发现、展示和启用开关。启用后 manifest 声明的 tools/commands 才会
 * 注册到 Agent；工具执行仍走正常审批、权限门与子进程隔离，hooks 在下方单独管理。
 */
export default function PluginsSection() {
  const lang = useUiStore((s) => s.lang)
  const pushToast = useUiStore((s) => s.pushToast)
  const [items, setItems] = useState<PluginItem[]>([])
  const [hooks, setHooks] = useState<Array<Record<string, unknown>>>([])
  const [dir, setDir] = useState(".spark/plugins")
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState("")
  const openSettingsAt = useUiStore((s) => s.openSettingsAt)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchPlugins()
      setItems(data.items ?? [])
      setHooks(data.hooks ?? [])
      setDir(data.plugins_dir ?? ".spark/plugins")
    } catch (e) {
      pushToast("common.error", "error", e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [pushToast])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-4">
        <h3 className="text-sm font-semibold text-ink">{t("plugins.title", lang)}</h3>
        <p className="text-xs leading-relaxed text-ink3">{t("plugins.desc", lang)}</p>
        <code className="rounded-lg bg-panel2 px-3 py-2 font-mono text-[11.5px] text-ink2">{dir}/&lt;name&gt;/spark-plugin.json</code>

        {loading ? (
          <div className="flex items-center gap-2 py-5 text-xs text-ink3">
            <Spinner size={13} /> {t("common.loading", lang)}
          </div>
        ) : items.length === 0 ? (
          <p className="py-3 text-xs text-ink3">{t("plugins.empty", lang)}</p>
        ) : (
          <div className="flex flex-col gap-2">
            {items.map((p) => (
              <div key={p.path} className="flex flex-col gap-1 rounded-lg border border-line bg-panel2 px-3 py-2.5">
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-ink">
                    {p.name}
                    {p.version ? <span className="ml-1.5 font-mono text-[11px] font-normal text-ink3">v{p.version}</span> : null}
                  </span>
                  {!p.error && p.has_manifest ? (
                    <Button
                      size="sm"
                      variant={p.enabled ? "ghost" : "primary"}
                      disabled={busy === p.name}
                      onClick={() => {
                        if (!p.enabled && !window.confirm(t("plugins.enableConfirm", lang))) return
                        setBusy(p.name)
                        void setPluginEnabled(p.name, !p.enabled)
                          .then((data) => {
                            if (data.items) setItems(data.items)
                            else void load()
                          })
                          .catch((e) =>
                            pushToast("common.error", "error", e instanceof Error ? e.message : String(e)),
                          )
                          .finally(() => setBusy(""))
                      }}
                    >
                      {busy === p.name ? <Spinner size={10} /> : null}
                      {p.enabled ? t("plugins.disable", lang) : t("plugins.enable", lang)}
                    </Button>
                  ) : null}
                  <Badge tone={p.error ? "warn" : p.enabled ? "ok" : "neutral"}>
                    <span title={p.error || undefined}>
                      {p.error
                        ? t("plugins.noManifest", lang)
                        : p.enabled
                          ? t("plugins.enabled", lang)
                          : t("plugins.disabled", lang)}
                    </span>
                  </Badge>
                </div>
                {p.description && <p className="text-[11.5px] leading-relaxed text-ink3">{p.description}</p>}
                {(p.tools || p.commands || p.hooks) ? (
                  <div className="flex flex-wrap gap-x-3 text-[10.5px] text-ink3">
                    {p.tools ? <span>{t("plugins.tools", lang)}: {p.tools}</span> : null}
                    {p.commands ? <span>{t("plugins.commands", lang)}: {p.commands}</span> : null}
                    {p.hooks ? <span>{t("plugins.hooksCount", lang)}: {p.hooks}</span> : null}
                  </div>
                ) : null}
                <span className="font-mono text-[10.5px] text-ink3">{p.path}</span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-4">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-ink">{t("plugins.hooksTitle", lang)}</h3>
          <Badge tone="accent">{hooks.length}</Badge>
          <Button size="sm" variant="soft" className="ml-auto shrink-0" onClick={() => openSettingsAt("security")}>
            {t("plugins.manageHooks", lang)} <IconExternal size={11} />
          </Button>
        </div>
        <p className="text-xs leading-relaxed text-ink3">{t("plugins.hooksDesc", lang)}</p>
        {hooks.length === 0 ? (
          <p className="py-2 text-xs text-ink3">{t("plugins.noHooks", lang)}</p>
        ) : (
          <div className="flex flex-col gap-1.5">
            {hooks.map((h, i) => (
              <div key={i} className="flex items-center gap-2 rounded-lg bg-panel2 px-3 py-1.5 font-mono text-[11px] text-ink3">
                <Badge tone="ok">{String(h.event ?? "")}</Badge>
                <span className="min-w-0 flex-1 truncate">{String(h.command ?? "")}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
