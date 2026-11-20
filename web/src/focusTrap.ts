import { useEffect } from "react"

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "textarea:not([disabled])",
  "select:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
  "[contenteditable='true']",
].join(",")

function isVisible(el: HTMLElement): boolean {
  if (el.hasAttribute("hidden") || el.getAttribute("aria-hidden") === "true") return false
  const style = window.getComputedStyle(el)
  return style.display !== "none" && style.visibility !== "hidden" && el.getClientRects().length > 0
}

function currentDialog(): HTMLElement | null {
  // 后挂载的确认框/二级弹窗在 DOM 末尾，取最后一个可见 dialog。
  const dialogs = Array.from(document.querySelectorAll<HTMLElement>("[role='dialog'][aria-modal='true']"))
  for (let i = dialogs.length - 1; i >= 0; i -= 1) {
    if (isVisible(dialogs[i])) return dialogs[i]
  }
  return null
}

/**
 * 全站统一的弹窗焦点陷阱。
 *
 * 不要求每个弹窗自己实现 trap：监听 DOM，发现最上层 aria-modal 弹窗后
 * 把焦点送进去；Tab/Shift+Tab 在弹窗内循环，关闭后把焦点还给打开前的元素。
 * 嵌套确认框自动以最后挂载的弹窗为准。
 */
export function useGlobalFocusTrap(): void {
  useEffect(() => {
    let activeDialog: HTMLElement | null = null
    let previousFocus: HTMLElement | null = null

    const focusFirst = (dialog: HTMLElement) => {
      const candidates = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(isVisible)
      const preferred = dialog.querySelector<HTMLElement>("[autofocus]") ?? candidates[0]
      if (preferred && isVisible(preferred)) preferred.focus({ preventScroll: true })
      else {
        dialog.setAttribute("tabindex", "-1")
        dialog.focus({ preventScroll: true })
      }
    }

    const sync = () => {
      const dialog = currentDialog()
      if (dialog === activeDialog) return
      if (!activeDialog && dialog) {
        previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
      }
      activeDialog = dialog
      if (dialog) focusFirst(dialog)
      else if (previousFocus && document.contains(previousFocus)) {
        previousFocus.focus({ preventScroll: true })
        previousFocus = null
      }
    }

    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || !activeDialog) return
      const candidates = Array.from(activeDialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(isVisible)
      if (candidates.length === 0) {
        event.preventDefault()
        activeDialog.focus({ preventScroll: true })
        return
      }
      const first = candidates[0]
      const last = candidates[candidates.length - 1]
      const active = document.activeElement
      if (event.shiftKey) {
        if (active === first || !activeDialog.contains(active as Node)) {
          event.preventDefault()
          last.focus()
        }
      } else if (active === last || !activeDialog.contains(active as Node)) {
        event.preventDefault()
        first.focus()
      }
    }

    const observer = new MutationObserver(sync)
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-hidden", "hidden", "class"] })
    document.addEventListener("keydown", onKey, true)
    sync()
    return () => {
      observer.disconnect()
      document.removeEventListener("keydown", onKey, true)
      if (previousFocus && document.contains(previousFocus)) previousFocus.focus({ preventScroll: true })
    }
  }, [])
}
