import { memo } from "react"
import { cls } from "./ui"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

/** 上下文用量环（顶部） */
export const ContextMeter = memo(function ContextMeter() {
  const context = useSessionStore((s) => s.status?.context)
  const show = useSessionStore((s) => s.status?.display?.show_context ?? true)
  const lang = useUiStore((s) => s.lang)
  const openSettingsAt = useUiStore((s) => s.openSettingsAt)
  if (!show || !context) return null

  const { used, limit, percent } = context
  const pct = Math.min(100, Math.max(0, Math.round(percent ?? (limit ? (used / limit) * 100 : 0))))
  const danger = pct >= 85
  const warn = pct >= 60 && pct < 85

  const R = 8.5
  const C = 2 * Math.PI * R

  return (
    <button
      type="button"
      onClick={() => openSettingsAt("project")}
      title={t("context.meterTitle", lang, { used: Math.round(used / 1000), limit: Math.round(limit / 1000), percent: pct })}
      aria-label={t("context.meterTitle", lang, { used: Math.round(used / 1000), limit: Math.round(limit / 1000), percent: pct })}
      className="group flex items-center gap-1.5 rounded-full px-1.5 py-1 transition-colors hover:bg-hover"
    >
      <span className="relative flex h-5.5 w-5.5 items-center justify-center">
        <svg width="22" height="22" viewBox="0 0 24 24" className="-rotate-90">
          <circle cx="12" cy="12" r={R} fill="none" stroke="var(--line2)" strokeWidth="2.5" />
          <circle
            cx="12"
            cy="12"
            r={R}
            fill="none"
            stroke={danger ? "var(--err)" : warn ? "var(--warn)" : "var(--accent)"}
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeDasharray={C}
            strokeDashoffset={C * (1 - pct / 100)}
            className="transition-[stroke-dashoffset] duration-500"
          />
        </svg>
      </span>
      <span className={cls("font-mono text-[11px] tabular-nums", danger ? "text-err" : warn ? "text-warn" : "text-ink2")}>
        {pct}%
      </span>
    </button>
  )
})
