import { memo, useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type KeyboardEvent } from "react"
import { useChatStore } from "../store/chatStore"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { fetchCommands, fetchFsTree, type SlashCommandInfo } from "../api"
import { IconFile, IconFolder, IconGlobe, IconImage, IconPaperclip, IconSend, IconStop, IconX } from "./icons"
import { cls } from "./ui"
import { t } from "../i18n"

const MAX_H = 200

export const Composer = memo(function Composer() {
  const input = useChatStore((s) => s.input)
  const streaming = useChatStore((s) => s.streaming)
  const setInput = useChatStore((s) => s.setInput)
  const send = useChatStore((s) => s.send)
  const runShell = useChatStore((s) => s.runShell)
  const webSearch = useChatStore((s) => s.webSearch)
  const setWebSearch = useChatStore((s) => s.setWebSearch)
  const pushToast = useUiStore((s) => s.pushToast)
  // Route through the session store so the local abort and POST /api/cancel
  // happen together (chatStore.cancel only aborts the fetch).
  const cancel = useSessionStore((s) => s.handleCancel)
  const status = useSessionStore((s) => s.status)
  const openSettings = useUiStore((s) => s.openSettings)
  const lang = useUiStore((s) => s.lang)

  const taRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const [images, setImages] = useState<string[]>([])
  const [dragging, setDragging] = useState(false)
  const [commands, setCommands] = useState<SlashCommandInfo[]>([])
  const [slashIndex, setSlashIndex] = useState(0)
  const [fileItems, setFileItems] = useState<{ name: string; type: "dir" | "file" }[]>([])
  const [fileIndex, setFileIndex] = useState(0)

  const model = status?.model

  // Slash menu: only when the message starts with "/" (like Claude Code / Codex).
  // `!` 前缀是内联 shell，不参与斜杠补全。
  const shellMode = input.startsWith("!")
  const slashQuery = input.startsWith("/") && !input.includes("\n") ? input.slice(1).split(/\s/)[0].toLowerCase() : null
  const slashItems = useMemo(() => {
    if (slashQuery === null) return []
    return commands.filter((c) => c.name.toLowerCase().includes(slashQuery))
  }, [commands, slashQuery])
  // 注意：slashOpen 只判前缀、不判 items 长度，否则「没有匹配的命令」空态
  // 分支被外层条件挡住永远渲染不出来（截图走查发现的死代码）。
  const slashOpen = slashQuery !== null

  // @-mention: the token under the caret starts with "@" (Claude Code / Codex
  // style file reference). Listing the workdir root is enough for the common
  // "point the model at this file" case and needs no index.
  const atQuery = useMemo(() => {
    const caret = taRef.current?.selectionStart ?? input.length
    const before = input.slice(0, caret)
    const m = /(^|\s)@([^\s@]*)$/.exec(before)
    return m ? m[2] : null
  }, [input])

  useEffect(() => {
    if (atQuery === null) {
      setFileItems([])
      return
    }
    let alive = true
    fetchFsTree("")
      .then((data) => {
        if (!alive) return
        const q = atQuery.toLowerCase()
        setFileItems(
          (data.entries ?? [])
            .filter((e) => !q || e.name.toLowerCase().includes(q))
            .slice(0, 8)
            .map((e) => ({ name: e.name, type: e.type })),
        )
      })
      .catch(() => {
        if (alive) setFileItems([])
      })
    return () => {
      alive = false
    }
  }, [atQuery])

  const fileOpen = atQuery !== null && fileItems.length > 0

  const applyFile = useCallback(
    (name: string) => {
      const caret = taRef.current?.selectionStart ?? input.length
      const before = input.slice(0, caret)
      const m = /(^|\s)@([^\s@]*)$/.exec(before)
      if (!m) return
      const replaced = before.slice(0, before.length - m[2].length) + name + " "
      const next = replaced + input.slice(caret)
      setInput(next)
      taRef.current?.focus()
      window.setTimeout(() => {
        const el = taRef.current
        if (el) el.selectionStart = el.selectionEnd = replaced.length
      }, 0)
    },
    [input, setInput],
  )

  useEffect(() => {
    let alive = true
    fetchCommands()
      .then((data) => {
        if (alive) setCommands(data.commands)
      })
      .catch(() => {
        /* 没有自定义命令是正常状态 */
      })
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    setSlashIndex(0)
  }, [slashQuery])

  const applySlash = useCallback((name: string) => {
    setInput(`/${name} `)
    taRef.current?.focus()
  }, [setInput])

  function autoGrow() {
    const el = taRef.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = Math.min(el.scrollHeight, MAX_H) + "px"
  }

  useEffect(() => {
    autoGrow()
  }, [input])

  const submit = useCallback(() => {
    if (streaming) return
    if (!input.trim() && images.length === 0) return
    // `!cmd`：直接在工作目录执行，结果作为本地卡片呈现（opencode / codex 同款）
    if (input.startsWith("!") && images.length === 0) {
      void runShell(input.slice(1).trim())
      return
    }
    send(images)
    setImages([])
  }, [input, images, streaming, send, runShell])

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (fileOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault()
        setFileIndex((i) => Math.min(i + 1, fileItems.length - 1))
        return
      }
      if (e.key === "ArrowUp") {
        e.preventDefault()
        setFileIndex((i) => Math.max(i - 1, 0))
        return
      }
      if (e.key === "Tab" || e.key === "Enter") {
        e.preventDefault()
        const pick = fileItems[fileIndex]
        if (pick) applyFile(pick.name)
        return
      }
      if (e.key === "Escape") {
        e.preventDefault()
        setFileItems([])
        return
      }
    }
    if (slashOpen && slashItems.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault()
        setSlashIndex((i) => Math.min(i + 1, slashItems.length - 1))
        return
      }
      if (e.key === "ArrowUp") {
        e.preventDefault()
        setSlashIndex((i) => Math.max(i - 1, 0))
        return
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault()
        const pick = slashItems[slashIndex]
        if (pick) applySlash(pick.name)
        return
      }
    }
    if (slashOpen && e.key === "Escape") {
      e.preventDefault()
      setInput("")
      return
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  // Vision models cap input size and a raw 12 MP phone photo blows past it.
  // Downscale/encode in the browser (what opencode and Codex do) instead of
  // sending a 8 MB base64 blob the provider will reject.
  const MAX_IMAGE_EDGE = 1568
  const MAX_IMAGE_BYTES = 5 * 1024 * 1024

  async function normalizeImage(file: File): Promise<string | null> {
    if (file.size > MAX_IMAGE_BYTES) {
      pushToast("toast.imageTooLarge", "error")
      return null
    }
    const dataUrl = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.onerror = () => reject(reader.error)
      reader.readAsDataURL(file)
    })
    if (file.type === "image/gif") return dataUrl // keep animation
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image()
      el.onload = () => resolve(el)
      el.onerror = () => reject(new Error("decode failed"))
      el.src = dataUrl
    }).catch(() => null)
    if (!img) return dataUrl
    const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(img.width, img.height))
    if (scale === 1 && file.size < 1_000_000) return dataUrl
    const canvas = document.createElement("canvas")
    canvas.width = Math.round(img.width * scale)
    canvas.height = Math.round(img.height * scale)
    const ctx = canvas.getContext("2d")
    if (!ctx) return dataUrl
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
    return canvas.toDataURL("image/jpeg", 0.85)
  }

  async function readFiles(list: FileList | null) {
    if (!list) return
    const images: string[] = []
    let skipped = 0
    for (const file of Array.from(list)) {
      if (!file.type.startsWith("image/")) {
        skipped += 1
        continue
      }
      const normalized = await normalizeImage(file)
      if (normalized) images.push(normalized)
    }
    if (images.length > 0) setImages((prev) => [...prev, ...images])
    if (skipped > 0) pushToast("toast.onlyImages", "info")
  }

  function onPaste(e: React.ClipboardEvent) {
    const files = e.clipboardData?.files
    if (files && files.length > 0 && Array.from(files).some((f) => f.type.startsWith("image/"))) {
      e.preventDefault()
      void readFiles(files)
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault()
    setDragging(false)
    void readFiles(e.dataTransfer.files)
  }

  const canSend = !streaming && (input.trim().length > 0 || images.length > 0)

  return (
    <div
      className={cls("border-t border-line bg-canvas/80 px-3 pt-2 pb-3 backdrop-blur-sm sm:px-4", dragging && "pt-0")}
      onDragOver={(e) => {
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      {dragging && (
        <div className="anim-fade pointer-events-none absolute inset-0 z-40 flex items-center justify-center border-2 border-dashed border-accent bg-accent/5">
          <span className="rounded-lg bg-panel px-4 py-2 text-sm font-medium text-accent shadow-lg">{t("composer.dropImage", lang)}</span>
        </div>
      )}
      <div className="mx-auto max-w-[780px]">
        {images.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {images.map((dataUrl) => (
              <div key={dataUrl.slice(-32)} className="group relative">
                <img src={dataUrl} alt="attached" className="h-16 w-16 rounded-lg border border-line object-cover" />
                <button
                  type="button"
                  onClick={() => setImages((prev) => prev.filter((u) => u !== dataUrl))}
                  className="absolute -top-1.5 -right-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-err text-white shadow transition-transform hover:scale-110"
                  title={t("common.removeImage", lang)}
                >
                  <IconX size={10} />
                </button>
              </div>
            ))}
          </div>
        )}

        <div
          className={cls(
            // 输入区是全屏最常聚焦的元素：给它更明确的"可输入"信号——
            // 更大的圆角、聚焦时一圈柔光，而不是只换边框色。
            "relative rounded-[18px] border bg-panel shadow-[var(--shadow-sm)] transition-[border-color,box-shadow] duration-200",
            dragging
              ? "border-accent shadow-[0_0_0_3px_var(--accent-soft)]"
              : "border-line focus-within:border-accent/60 focus-within:shadow-[0_0_0_3px_var(--accent-soft),var(--shadow)]",
          )}
        >
          {shellMode && (
            <div className="anim-fade absolute bottom-full left-0 mb-2 flex items-center gap-2 rounded-lg border border-line bg-panel px-3 py-1.5 text-[11.5px] text-ink2 shadow-[var(--shadow)]">
              <span className="font-mono text-accent">!</span>
              <span>{t("composer.shellHint", lang)}</span>
            </div>
          )}
          {fileOpen && (
            <div
              className="anim-fade absolute bottom-full left-0 mb-2 w-full overflow-hidden rounded-xl border border-line bg-panel shadow-[var(--shadow)]"
              role="listbox"
              aria-label={t("composer.fileHint", lang)}
            >
              {fileItems.map((f, i) => (
                <button
                  key={f.name}
                  type="button"
                  role="option"
                  aria-selected={i === fileIndex}
                  onMouseEnter={() => setFileIndex(i)}
                  onMouseDown={(e) => {
                    e.preventDefault()
                    applyFile(f.name)
                  }}
                  className={cls(
                    "flex w-full items-center gap-2 px-3 py-2 text-left text-[12.5px]",
                    i === fileIndex ? "bg-hover text-ink" : "text-ink2",
                  )}
                >
                  {f.type === "dir" ? <IconFolder size={12} className="shrink-0 text-ink3" /> : <IconFile size={12} className="shrink-0 text-ink3" />}
                  <span className="min-w-0 flex-1 truncate font-mono">{f.name}</span>
                  <span className="shrink-0 text-[10px] text-ink3">{t("composer.fileRef", lang)}</span>
                </button>
              ))}
            </div>
          )}
          {slashOpen && (
            <div
              className="anim-fade absolute bottom-full left-0 mb-2 w-full overflow-hidden rounded-xl border border-line bg-panel shadow-[var(--shadow)]"
              role="listbox"
              aria-label={t("composer.slashHint", lang)}
            >
              {slashItems.length === 0 && (
                <p className="px-3 py-2.5 text-[12px] text-ink3">{t("composer.slashEmpty", lang)}</p>
              )}
              {slashItems.map((c, i) => (
                <button
                  key={c.name}
                  type="button"
                  role="option"
                  aria-selected={i === slashIndex}
                  onMouseEnter={() => setSlashIndex(i)}
                  onMouseDown={(e) => {
                    // mousedown, not click: the textarea blur would close the menu first
                    e.preventDefault()
                    applySlash(c.name)
                  }}
                  className={cls(
                    "flex w-full items-center gap-2 px-3 py-2 text-left text-[12.5px]",
                    i === slashIndex ? "bg-hover text-ink" : "text-ink2",
                  )}
                >
                  <span className="font-mono text-accent">/{c.name}</span>
                  <span className="min-w-0 flex-1 truncate text-ink3">{c.description}</span>
                </button>
              ))}
            </div>
          )}
          <textarea
            ref={taRef}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKey}
            onPaste={onPaste}
            placeholder={streaming ? t("composer.placeholderStreaming", lang) : t("composer.placeholder", lang)}
            className="block max-h-[200px] w-full resize-none overflow-y-auto bg-transparent px-4 pt-3.5 pb-1 text-[14px] leading-relaxed outline-none placeholder:text-ink3"
          />
          <div className="flex items-center justify-between gap-2 px-2.5 pb-2.5">
            <div className="flex min-w-0 items-center gap-1">
              <button
                type="button"
                title={t("composer.attachImageHint", lang)}
                disabled={streaming}
                onClick={() => fileRef.current?.click()}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-ink3 transition-colors hover:bg-hover hover:text-ink disabled:opacity-45"
              >
                <IconPaperclip size={16} />
              </button>
              <button
                type="button"
                title={t("common.attachImage", lang)}
                disabled={streaming}
                onClick={() => fileRef.current?.click()}
                className="hidden h-8 w-8 items-center justify-center rounded-lg text-ink3 transition-colors hover:bg-hover hover:text-ink disabled:opacity-45 sm:flex"
              >
                <IconImage size={16} />
              </button>
              <button
                type="button"
                title={webSearch ? t("composer.webSearchOn", lang) : t("composer.webSearchOff", lang)}
                aria-pressed={webSearch}
                onClick={() => {
                  const next = !webSearch
                  setWebSearch(next)
                  useUiStore.getState().pushToast(next ? "toast.webSearchOn" : "toast.webSearchOff", "info")
                }}
                className={cls(
                  "flex h-8 items-center gap-1.5 rounded-lg px-2 text-[11.5px] font-medium transition-colors",
                  webSearch ? "bg-accent-soft text-accent" : "text-ink3 hover:bg-hover hover:text-ink",
                )}
              >
                <IconGlobe size={14} />
                <span className="hidden sm:inline">{t("composer.webSearch", lang)}</span>
              </button>
              {model && (
                <button
                  type="button"
                  onClick={openSettings}
                  title={t("common.currentModel", lang)}
                  className="ml-1 max-w-[10rem] truncate rounded-md bg-hover px-2 py-1 text-[11px] font-medium text-ink2 transition-colors hover:bg-selected"
                >
                  {model}
                </button>
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="hidden text-[11px] text-ink3 md:block">{t("common.enterSend", lang)}</span>
              {streaming ? (
                <button
                  type="button"
                  title={t("common.stopGenerate", lang)}
                  onClick={cancel}
                  className="flex h-8.5 w-8.5 shrink-0 items-center justify-center rounded-full border border-line bg-hover text-ink transition-all duration-150 hover:border-line2 hover:bg-selected active:scale-95"
                >
                  <IconStop size={15} />
                </button>
              ) : (
                <button
                  type="button"
                  title={t("common.send", lang)}
                  disabled={!canSend}
                  onClick={submit}
                  className={cls(
                    "flex h-8.5 w-8.5 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent transition-all duration-150 hover:brightness-110 active:scale-95",
                    canSend && "glow-accent",
                    !canSend && "disabled:bg-hover disabled:text-ink3 disabled:opacity-100",
                  )}
                >
                  <IconSend size={15} />
                </button>
              )}
            </div>
          </div>
        </div>

        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          onChange={(e) => {
            readFiles(e.target.files)
            e.target.value = ""
          }}
        />
      </div>
    </div>
  )
})
