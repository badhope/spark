import { memo, useEffect, useRef, useState, type ReactNode } from "react"
import { useVirtualizer } from "@tanstack/react-virtual"
import type { ChatMsg } from "../store/chatStore"
import { useUiStore } from "../store/uiStore"
import { IconChevronDown } from "./icons"
import { t } from "../i18n"

/* ------------------------------------------------------------------ */
/* 长会话虚拟列表（P1-5）：@tanstack/react-virtual 替代手写偏移窗口。  */
/* 只渲染可视窗口内的消息，长会话（数百条）不卡。                     */
/* ------------------------------------------------------------------ */

interface Props {
  items: ChatMsg[]
  estimateHeight: (m: ChatMsg) => number
  renderItem: (m: ChatMsg) => ReactNode
  /** 贴底模式：消息/流式变化时自动滚到底部（用户上翻则取消贴底）。 */
  stickToBottom?: boolean
  /** 会话切换时重置为贴底，避免把上一会话的滚动位置带过来。 */
  resetKey?: string
  footer?: ReactNode
}

export const VirtualMessageList = memo(function VirtualMessageList({
  items,
  estimateHeight,
  renderItem,
  stickToBottom = true,
  resetKey,
  footer,
}: Props) {
  const parentRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(stickToBottom)
  const [atBottom, setAtBottom] = useState(true)
  const [hasNew, setHasNew] = useState(false)
  const lastResetKey = useRef(resetKey)
  const lang = useUiStore((s) => s.lang)

  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: (i) => estimateHeight(items[i]),
    overscan: 10,
  })

  // 会话切换时重置滚动位置；上一会话上翻的位置不应带到新会话。
  useEffect(() => {
    if (lastResetKey.current === resetKey) return
    lastResetKey.current = resetKey
    stickRef.current = true
    setAtBottom(true)
    setHasNew(false)
    if (items.length > 0) {
      requestAnimationFrame(() => virtualizer.scrollToIndex(items.length - 1, { align: "end" }))
    }
  }, [resetKey, items.length, virtualizer])

  // 贴底跟随：items / 流式变化时滚到底；用户上翻时只提示新输出。
  useEffect(() => {
    if (items.length === 0) return
    if (!stickRef.current) {
      setHasNew(true)
      return
    }
    virtualizer.scrollToIndex(items.length - 1, { align: "end" })
  }, [items, virtualizer])

  function scrollToLatest() {
    stickRef.current = true
    setAtBottom(true)
    setHasNew(false)
    virtualizer.scrollToIndex(Math.max(0, items.length - 1), { align: "end" })
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div
        ref={parentRef}
        onScroll={(e) => {
          const el = e.currentTarget
          const nearBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 120
          stickRef.current = nearBottom
          setAtBottom(nearBottom)
          if (nearBottom) setHasNew(false)
        }}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain"
      >
        <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
          <div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
            }}
          >
            {virtualizer.getVirtualItems().map((vi) => (
              <div
                key={items[vi.index].id}
                data-index={vi.index}
                ref={virtualizer.measureElement}
                style={{
                  transform: `translateY(${vi.start}px)`,
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                }}
              >
                {renderItem(items[vi.index])}
              </div>
            ))}
          </div>
        </div>
        {footer}
      </div>

      {!atBottom && (
        <button
          type="button"
          onClick={scrollToLatest}
          title={hasNew ? t("chat.newOutput", lang) : t("chat.scrollBottom", lang)}
          aria-label={hasNew ? t("chat.newOutput", lang) : t("chat.scrollBottom", lang)}
          className="absolute bottom-3 left-1/2 z-20 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-line bg-panel/95 px-3 py-1.5 text-[11px] font-medium text-ink2 shadow-[0_6px_18px_rgba(0,0,0,0.14)] backdrop-blur transition-all duration-150 hover:border-line2 hover:text-ink active:scale-[0.98] sm:bottom-4"
        >
          {hasNew && <span className="h-1.5 w-1.5 rounded-full bg-accent breathe" />}
          <IconChevronDown size={13} className="text-ink3" />
          <span>{hasNew ? t("chat.newOutput", lang) : t("chat.scrollBottom", lang)}</span>
        </button>
      )}
    </div>
  )
})
