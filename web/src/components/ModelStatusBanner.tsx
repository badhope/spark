import { memo } from "react"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { modelErrorCodeLabel, modelErrorText } from "../modelErrors"
import type { ModelErrorInfo } from "../types"
import { IconSettings } from "./icons"

/**
 * Configuration problems are visible before the first token, so show them as a
 * persistent strip instead of waiting for the user to send a doomed request.
 * Runtime/connectivity failures still arrive as a structured error card in chat.
 */
export const ModelStatusBanner = memo(function ModelStatusBanner() {
  const status = useSessionStore((s) => s.status)
  const lang = useUiStore((s) => s.lang)
  const openSettingsAt = useUiStore((s) => s.openSettingsAt)
  const issue = status?.model_status?.issue as ModelErrorInfo | undefined
  if (!status?.model_status || status.model_status.ready || !issue) return null
  const copy = modelErrorText(issue, lang)
  return (
    <div
      data-testid="model-status-banner"
      role="status"
      className="flex flex-wrap items-start gap-x-3 gap-y-1 border-b border-warn/35 bg-warn/8 px-3 py-2 text-[11.5px] leading-relaxed text-ink2"
    >
      <span className="flex min-w-0 flex-1 items-start gap-2">
        <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-warn" />
        <span className="min-w-0">
          <span className="font-semibold text-warn">{copy.title}</span>
          <span className="ml-1.5 rounded-full border border-warn/30 bg-warn/10 px-1.5 py-0.5 text-[10px] text-warn/90">
            {modelErrorCodeLabel(issue.code, lang)}
          </span>
          <span className="mt-0.5 block text-ink3">{copy.hint}</span>
        </span>
      </span>
      <button
        type="button"
        onClick={() => openSettingsAt("model")}
        className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-warn/40 bg-panel/60 px-2.5 text-[11.5px] font-medium text-warn transition-colors hover:bg-warn/10"
      >
        <IconSettings size={12} /> {lang === "zh" ? "去配置模型" : "Configure model"}
      </button>
    </div>
  )
})
