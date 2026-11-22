import { memo, useEffect, useRef, useState } from "react"
import { useSessionStore } from "../store/sessionStore"
import { Button, Spinner } from "./ui"
import { DiffView } from "./DiffView"
import { IconFileDiff } from "./icons"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"
import { useIsMobile } from "../useIsMobile"

/** 工具审批卡片：底部操作审批，可展开查看工具真实参数 */
export const ApprovalModal = memo(function ApprovalModal() {
  const approval = useSessionStore((s) => s.approval)
  const busy = useSessionStore((s) => s.approvalBusy)
  const handleApproval = useSessionStore((s) => s.handleApproval)
  const lang = useUiStore((s) => s.lang)
  const mobile = useIsMobile()
  const mobileTab = useUiStore((s) => s.mobileTab)
  const mobileApprovalOpen = useUiStore((s) => s.mobileApprovalOpen)
  const hiddenOnMobileHome = mobile && mobileTab === "home" && !mobileApprovalOpen
  const [showArgs, setShowArgs] = useState(false)
  const denyRef = useRef<HTMLButtonElement>(null)
  const wasOpen = useRef(false)

  // Move focus into the sheet when it appears so a keyboard/screen-reader user
  // lands on the decision instead of continuing to tab through the chat behind.
  // Focus is restored on close. The sheet is intentionally *not* fully modal
  // (the stream keeps rendering behind it), so it is an alertdialog, not a
  // dialog with aria-modal — claiming modality without inert-ing the rest was
  // a false affordance.
  useEffect(() => {
    if (hiddenOnMobileHome) {
      // The home card shows the summary and a review button; mounting the modal
      // here would cover the dashboard that RC-03 requires on first paint.
      wasOpen.current = false
      return undefined
    }
    if (approval && !wasOpen.current) {
      wasOpen.current = true
      const previous = document.activeElement as HTMLElement | null
      denyRef.current?.focus()
      return () => {
        wasOpen.current = false
        previous?.focus?.()
      }
    }
    if (!approval) wasOpen.current = false
    return undefined
  }, [approval, hiddenOnMobileHome])

  // A new approval must not inherit the previous one's expanded state.
  useEffect(() => {
    setShowArgs(false)
  }, [approval?.info?.id, approval?.tool?.id])

  if (!approval || hiddenOnMobileHome) return null
  const { info, tool } = approval
  const hasDiff = Boolean(info.diff && info.diff.trim())
  const diff = info.diff ? info.diff.replace(/^"|"$/g, "") : ""
  const args = info.arguments ?? (tool?.arguments as Record<string, unknown> | undefined)
  const hasArgs = Boolean(args && Object.keys(args).length > 0)
  const argText = hasArgs ? JSON.stringify(args, null, 2) : ""

  return (
    <div
      // Centred dialog, not a bottom sheet: the composer is also pinned to the
      // bottom of the viewport, so anchoring the card there put its action row
      // underneath the input box (buttons half-covered, ugly and easy to miss).
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-3 backdrop-blur-[2px] sm:p-6"
      role="alertdialog"
      aria-live="assertive"
      aria-label={t("approval.title", lang)}
    >
      <div className="anim-fade-up flex max-h-[86vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-line bg-panel shadow-[var(--shadow)]">
        <div className="flex items-center gap-2 border-b border-line px-3 py-2.5 sm:px-4">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-warn/15 text-warn">
            {hasDiff ? (
              <IconFileDiff size={14} />
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M12 9v4M12 17h.01" />
                <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
              </svg>
            )}
          </span>
          <span className="truncate text-sm font-semibold text-ink">{t("approval.title", lang)}</span>
          <span className="ml-auto shrink-0 rounded-md bg-hover px-2 py-0.5 font-mono text-[11px] text-ink2">
            {tool?.name ?? info.tool ?? ""}
          </span>
        </div>

        <div className="max-h-[40vh] overflow-y-auto px-3 py-3 sm:px-4">
          <p className="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-ink2">{info.summary}</p>
          {hasDiff && (
            <div className="mt-2">
              <DiffView diff={diff} />
            </div>
          )}
          {hasArgs && (
            <div className="mt-2">
              <button
                type="button"
                className="text-[12px] font-medium text-ink3 underline-offset-2 hover:text-ink hover:underline"
                onClick={() => setShowArgs((v) => !v)}
                aria-expanded={showArgs}
              >
                {showArgs ? t("approval.hideArgs", lang) : t("approval.showArgs", lang)}
              </button>
              {showArgs && (
                <pre className="mt-1.5 max-h-56 overflow-auto rounded-lg bg-panel2 px-3 py-2 font-mono text-[11.5px] leading-relaxed text-ink2">
                  <code>{argText}</code>
                </pre>
              )}
            </div>
          )}
        </div>

        {/* Buttons share the row instead of each claiming w-full: with three
            full-width buttons in a flex row the last one was pushed outside the
            card and its text wrapped to one character per line. */}
        <div className="flex shrink-0 flex-col gap-2 border-t border-line bg-panel2 px-3 py-3 sm:flex-row sm:items-center sm:justify-end sm:px-4">
          <button
            ref={denyRef}
            type="button"
            onClick={() => handleApproval("deny")}
            disabled={busy}
            className="inline-flex h-8.5 w-full shrink-0 items-center justify-center whitespace-nowrap rounded-lg px-3.5 text-sm font-medium text-ink2 transition-colors hover:bg-hover hover:text-ink disabled:pointer-events-none disabled:opacity-45 sm:w-auto"
          >
            {t("approval.deny", lang)}
          </button>
          <Button
            variant="outline"
            className="shrink-0 whitespace-nowrap"
            onClick={() => handleApproval("allow")}
            disabled={busy}
          >
            {t("approval.allowOnce", lang)}
          </Button>
          <Button
            variant="primary"
            className="shrink-0 whitespace-nowrap"
            onClick={() => handleApproval("allow_always")}
            disabled={busy}
          >
            {busy ? (
              <span className="flex items-center gap-1.5">
                <Spinner size={12} /> {t("approval.busy", lang)}
              </span>
            ) : (
              t("approval.allowAlways", lang)
            )}
          </Button>
        </div>
      </div>
    </div>
  )
})
