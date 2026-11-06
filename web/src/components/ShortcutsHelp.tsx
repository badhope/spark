import { memo, useEffect, useMemo } from "react"
import { IconButton } from "./ui"
import { IconCommand, IconX } from "./icons"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"
import { bindingSignature, loadBindings, type ShortcutAction } from "../shortcuts"

/**
 * Rows are keyed by the rebindable action so the panel always shows the keys
 * the user actually has. Hardcoding "Ctrl+K" here was wrong the moment
 * Settings → Shortcuts shipped: a rebound palette key still rendered as Ctrl+K.
 */
const ACTION_ROWS: Array<{ action: ShortcutAction; hintKey: "shortcuts.hint.palette" | "shortcuts.hint.newSession" | "shortcuts.hint.toggleSidebar" | "shortcuts.hint.terminal" | "shortcuts.hint.send" | "shortcuts.hint.stop" | "shortcuts.hint.help" }> = [
  { action: "palette", hintKey: "shortcuts.hint.palette" },
  { action: "newSession", hintKey: "shortcuts.hint.newSession" },
  { action: "toggleSidebar", hintKey: "shortcuts.hint.toggleSidebar" },
  { action: "terminal", hintKey: "shortcuts.hint.terminal" },
  { action: "send", hintKey: "shortcuts.hint.send" },
  { action: "stop", hintKey: "shortcuts.hint.stop" },
  { action: "help", hintKey: "shortcuts.hint.help" },
]

/** Static rows: not rebindable (composer-local affordances). */
const STATIC_ROWS: Array<{ keys: string[]; hintKey: "shortcuts.hint.slash" | "shortcuts.hint.at" }> = [
  { keys: ["/"], hintKey: "shortcuts.hint.slash" },
  { keys: ["@"], hintKey: "shortcuts.hint.at" },
]

/** 绑定串（"Mod+K"）→ 逐键展示（["Ctrl", "K"]）。 */
function splitBinding(binding: string): string[] {
  return bindingSignature(binding).split("+")
}

/** 快捷键帮助（Claude Code 的 `?` 面板）。 */
export const ShortcutsHelp = memo(function ShortcutsHelp() {
  const open = useUiStore((s) => s.helpOpen)
  const close = useUiStore((s) => s.closeHelp)
  const lang = useUiStore((s) => s.lang)
  // Re-read on every open: Settings → Shortcuts may have changed bindings while
  // this panel stayed mounted.
  const bindings = useMemo(() => (open ? loadBindings() : null), [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, close])

  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-[2px]"
      onClick={close}
      role="dialog"
      aria-modal="true"
      aria-label={t("help.title", lang)}
    >
      <div
        className="anim-fade flex max-h-[86vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-line bg-canvas shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <IconCommand size={15} className="text-accent" />
          <span className="text-sm font-semibold text-ink">{t("help.title", lang)}</span>
          <IconButton title={t("common.close", lang)} onClick={close} className="ml-auto">
            <IconX size={15} />
          </IconButton>
        </div>
        <div className="overflow-y-auto p-3">
          <table className="w-full border-separate border-spacing-y-1 text-[12.5px]">
            <tbody>
              {ACTION_ROWS.map((row) => {
                const keys = bindings ? splitBinding(bindings[row.action]) : []
                return (
                  <tr key={row.action}>
                    <td className="w-[38%] pr-3 align-middle">
                      <span className="flex flex-wrap gap-1">
                        {keys.map((k) => (
                          <kbd
                            key={k}
                            className="inline-flex h-5 min-w-5 items-center justify-center rounded border border-line bg-panel2 px-1 font-mono text-[10px] text-ink2"
                          >
                            {k}
                          </kbd>
                        ))}
                      </span>
                    </td>
                    <td className="align-middle text-ink2">{t(row.hintKey, lang)}</td>
                  </tr>
                )
              })}
              {STATIC_ROWS.map((row) => (
                <tr key={row.hintKey}>
                  <td className="w-[38%] pr-3 align-middle">
                    <span className="flex flex-wrap gap-1">
                      {row.keys.map((k) => (
                        <kbd
                          key={k}
                          className="inline-flex h-5 min-w-5 items-center justify-center rounded border border-line bg-panel2 px-1 font-mono text-[10px] text-ink2"
                        >
                          {k}
                        </kbd>
                      ))}
                    </span>
                  </td>
                  <td className="align-middle text-ink2">{t(row.hintKey, lang)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="border-t border-line px-4 py-2 text-[11px] text-ink3">{t("help.footer", lang)}</div>
      </div>
    </div>
  )
})
