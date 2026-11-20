import { useCallback, useEffect, useState } from "react"
import type { FullConfig, Status } from "../types"
import { activateLocalModel, downloadLocalModel, fetchLocalModels, type LocalModelInfo } from "../api"
import { Badge, Button, Spinner, cls } from "./ui"
import { IconCheck, IconDownload, IconRefresh, IconSpark } from "./icons"

interface Props {
  lang: "zh" | "en"
  onActivated: (name: string, config: FullConfig, status: Status) => void
}

function sizeLabel(bytes: number): string {
  return bytes >= 1024 * 1024 * 1024
    ? `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
    : `${Math.round(bytes / (1024 * 1024))} MB`
}

function ctxLabel(ctx: number): string {
  return ctx >= 1024 ? `${Math.round(ctx / 1024)}K` : String(ctx)
}

export default function LocalModelsSection({ lang, onActivated }: Props) {
  const zh = lang === "zh"
  const [models, setModels] = useState<LocalModelInfo[]>([])
  const [dir, setDir] = useState("")
  const [busy, setBusy] = useState("")
  const [error, setError] = useState("")

  const refresh = useCallback(async () => {
    try {
      const data = await fetchLocalModels()
      setModels(data.models)
      setDir(data.models_dir)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    if (!models.some((m) => m.download && ["downloading", "verifying"].includes(m.download.status))) return
    const timer = window.setInterval(() => { void refresh() }, 1200)
    return () => window.clearInterval(timer)
  }, [models, refresh])

  async function download(id: string) {
    setBusy(id)
    setError("")
    try {
      await downloadLocalModel(id)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy("")
    }
  }

  async function activate(model: LocalModelInfo) {
    setBusy(model.id)
    setError("")
    try {
      const data = await activateLocalModel(model.id)
      onActivated(model.name, data.config, data.status)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy("")
    }
  }

  return (
    <section className="space-y-3" data-testid="local-models">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-1.5 text-[12px] font-semibold text-ink">
            <IconSpark size={13} />
            {zh ? "本地模型（内置推荐）" : "Local models (curated)"}
          </h3>
          <p className="mt-1 text-[11px] text-ink3">
            {zh
              ? "下载后直接在应用内运行，不需要 Ollama。模型文件保存在 " + dir + "。"
              : "Runs in-process after download, no Ollama needed. Files live in " + dir + "."}
          </p>
        </div>
        <Button variant="ghost" className="shrink-0 text-[11px]" onClick={() => void refresh()}>
          <IconRefresh size={12} /> {zh ? "刷新" : "Refresh"}
        </Button>
      </div>

      {error && <p className="rounded-lg bg-danger/10 px-3 py-2 text-[11px] text-danger">{error}</p>}

      <ul className="space-y-2">
        {models.map((model) => {
          const dl = model.download
          const downloading = dl && ["downloading", "verifying"].includes(dl.status)
          return (
            <li
              key={model.id}
              data-testid={`local-model-${model.id}`}
              className={cls(
                "rounded-xl border p-3 transition",
                model.active ? "border-accent/50 bg-accent/5" : "border-line bg-surface",
              )}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-[12px] font-semibold text-ink">{model.name}</span>
                    {model.recommended && <Badge tone="accent">{zh ? "推荐" : "Recommended"}</Badge>}
                    {model.active && <Badge tone="ok">{zh ? "使用中" : "Active"}</Badge>}
                    {model.installed && !model.active && <Badge tone="neutral">{zh ? "已安装" : "Installed"}</Badge>}
                  </div>
                  <p className="mt-1 text-[11px] leading-relaxed text-ink3">{model.summary}</p>
                  <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-ink3">
                    <span>{model.params}</span>
                    <span>{ctxLabel(model.context)} context</span>
                    <span>{sizeLabel(model.size_bytes)}</span>
                    <span>{model.languages}</span>
                    <span>{model.license}</span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {model.strengths.map((s) => <Badge key={s} tone="neutral">{s}</Badge>)}
                  </div>
                  {model.note && <p className="mt-1 text-[10px] text-ink3">{model.note}</p>}
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  {model.installed ? (
                    <Button
                      variant={model.active ? "ghost" : "primary"}
                      className="text-[11px]"
                      disabled={model.active || busy === model.id}
                      onClick={() => void activate(model)}
                    >
                      {model.active ? <><IconCheck size={12} /> {zh ? "已启用" : "Active"}</> : <>{zh ? "使用此模型" : "Use this model"}</>}
                    </Button>
                  ) : downloading ? (
                    <div className="w-40">
                      <div className="mb-1 flex justify-between text-[10px] text-ink3">
                        <span>{dl?.status === "verifying" ? (zh ? "校验中" : "Verifying") : `${dl?.percent ?? 0}%`}</span>
                        <span>{dl?.speed_bps ? `${Math.round(dl.speed_bps / 1024 / 1024)} MB/s` : ""}</span>
                      </div>
                      <div className="h-1.5 overflow-hidden rounded-full bg-line">
                        <div className="h-full bg-accent transition-all" style={{ width: `${dl?.percent ?? 0}%` }} />
                      </div>
                    </div>
                  ) : (
                    <Button
                      variant="primary"
                      className="text-[11px]"
                      disabled={busy === model.id}
                      onClick={() => void download(model.id)}
                    >
                      {busy === model.id ? <Spinner size={11} /> : <IconDownload size={12} />}
                      {dl?.status === "failed" ? (zh ? "重新下载" : "Retry download") : `${zh ? "下载" : "Download"} ${sizeLabel(model.size_bytes)}`}
                    </Button>
                  )}
                </div>
              </div>
              {dl?.status === "failed" && dl.error && (
                <p className="mt-2 rounded-lg bg-danger/10 px-2.5 py-1.5 text-[10px] text-danger">{dl.error}</p>
              )}
            </li>
          )
        })}
      </ul>
      {models.length === 0 && <p className="text-[11px] text-ink3">{zh ? "正在读取模型目录…" : "Loading catalog…"}</p>}
    </section>
  )
}
