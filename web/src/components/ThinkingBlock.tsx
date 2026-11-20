import { memo, useEffect, useState } from "react"
import { Spinner, cls, formatElapsed } from "./ui"
import { IconChevronRight } from "./icons"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

/** 思维链折叠块（reasoning） */
export const ThinkingBlock = memo(function ThinkingBlock({
  text,
  streaming,
  startedAt,
  durationMs,
}: {
  text: string
  streaming?: boolean
  startedAt?: number
  durationMs?: number
}) {
  const [open, setOpen] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const lang = useUiStore((s) => s.lang)

  useEffect(() => {
    if (!streaming || startedAt === undefined) return
    const tick = () => setElapsed(Date.now() - startedAt)
    tick()
    const id = window.setInterval(tick, 500)
    return () => window.clearInterval(id)
  }, [streaming, startedAt])

  const shownMs = durationMs ?? (streaming && startedAt !== undefined ? elapsed : undefined)
  const meta =
    shownMs !== undefined
      ? `${formatElapsed(shownMs)} · ${streaming ? t("common.thinking", lang) : t("common.charCount", lang, { n: text.length })}`
      : streaming
        ? t("common.thinking", lang)
        : t("common.charCount", lang, { n: text.length })

  return (
    <div className="my-1 overflow-hidden rounded-lg border border-line bg-panel">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors hover:bg-hover"
        aria-expanded={open}
      >
        <span className={cls("shrink-0 text-ink3 transition-transform", open && "rotate-90")}>
          <IconChevronRight size={12} />
        </span>
        {streaming ? <Spinner size={11} className="text-accent" /> : <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-warn" />}
        <span className="text-xs font-medium text-ink2">{t("common.thinkingProcess", lang)}</span>
        <span className="ml-auto font-mono text-[10px] text-ink3">{meta}</span>
      </button>
      {open && (
        <div className="anim-fade border-t border-line px-3 py-2">
          <p className="text-[12.5px] leading-relaxed whitespace-pre-wrap text-ink2">{text}</p>
        </div>
      )}
    </div>
  )
})
