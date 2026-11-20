import { memo, useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type KeyboardEvent } from "react"
import type { ChatAttachment } from "../types"
import { attachmentKind, formatFileSize } from "../fileUtils"
import { useChatStore } from "../store/chatStore"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { fetchCommands, fetchFsTree, type SlashCommandInfo } from "../api"
import { IconFile, IconFolder, IconGlobe, IconPaperclip, IconSend, IconStop, IconX } from "./icons"
import { cls, Spinner } from "./ui"
import { t } from "../i18n"

const MAX_H = 200

interface AtFileItem {
  name: string
  type: "dir" | "file"
  /** 相对 workdir 的完整路径，目录带尾斜杠。 */
  path: string
  parent?: boolean
}

/** 子序列匹配：@ 里输入 "rsp" 也能找到 RightPanel.tsx。 */
function fuzzyScore(name: string, query: string): number {
  if (!query) return 0
  const lower = name.toLowerCase()
  const q = query.toLowerCase()
  if (lower === q) return 100
  if (lower.startsWith(q)) return 80
  if (lower.includes(q)) return 60
  let cursor = 0
  let gaps = 0
  for (const char of q) {
    const found = lower.indexOf(char, cursor)
    if (found < 0) return -1
    gaps += found - cursor
    cursor = found + 1
  }
  return Math.max(1, 40 - gaps)
}

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
  const [files, setFiles] = useState<ChatAttachment[]>([])
  const [dragging, setDragging] = useState(false)
  const [commands, setCommands] = useState<SlashCommandInfo[]>([])
  const [slashIndex, setSlashIndex] = useState(0)
  const [fileItems, setFileItems] = useState<AtFileItem[]>([])
  const [fileIndex, setFileIndex] = useState(0)
  const [fileDir, setFileDir] = useState("")
  const [fileLoading, setFileLoading] = useState(false)
  const [fileError, setFileError] = useState("")
  const [fileDismissed, setFileDismissed] = useState(false)
  const [caret, setCaret] = useState(0)

  const model = status?.model
  // provider=mock 时 status.model 仍是配置文件里保存的真实模型名（如
  // deepseek-chat），直接展示会让人以为正在请求 DeepSeek。这里以实际
  // provider 为准，明确显示“Mock 演示”。
  const modelLabel = status?.provider === "mock" ? t("composer.mockModel", lang) : model

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

  // @-mention: 光标所在的 token 以 @ 开头。token 里的 / 代表目录层级：
  // `@web/src/` 会列出 web/src 的子项，选中目录继续下钻，选中文件才结束补全。
  const atQuery = useMemo(() => {
    const pos = Math.min(caret, input.length)
    const before = input.slice(0, pos)
    const m = /(^|\s)@([^\s@]*)$/.exec(before)
    return m ? m[2] : null
  }, [input, caret])

  const atSplit = useMemo(() => {
    if (atQuery === null) return { dir: "", query: "" }
    const slash = atQuery.lastIndexOf("/")
    return slash >= 0
      ? { dir: atQuery.slice(0, slash + 1), query: atQuery.slice(slash + 1) }
      : { dir: "", query: atQuery }
  }, [atQuery])

  useEffect(() => {
    if (atQuery === null) {
      setFileItems([])
      setFileLoading(false)
      setFileError("")
      return
    }
    let alive = true
    setFileDismissed(false)
    const dir = atSplit.dir.replace(/^\//, "").replace(/\/$/, "")
    setFileDir(dir)
    setFileLoading(true)
    setFileError("")
    fetchFsTree(dir)
      .then((data) => {
        if (!alive) return
        if (data.error) {
          setFileItems([])
          setFileError(data.error)
          return
        }
        const q = atSplit.query
        const entries = (data.entries ?? [])
          .map((entry) => {
            const path = dir ? `${dir}/${entry.name}` : entry.name
            return {
              name: entry.name,
              type: entry.type,
              path: entry.type === "dir" ? `${path}/` : path,
              score: fuzzyScore(entry.name, q),
            }
          })
          .filter((entry) => entry.score >= 0)
          .sort((a, b) => b.score - a.score || (a.type === b.type ? a.name.localeCompare(b.name) : a.type === "dir" ? -1 : 1))
        const visible: AtFileItem[] = entries.slice(0, 10).map(({ score: _score, ...entry }) => entry)
        if (dir) {
          const parent = dir.split("/").slice(0, -1).join("/")
          visible.unshift({ name: "..", type: "dir", path: parent ? `${parent}/` : "", parent: true })
        }
        setFileItems(visible)
      })
      .catch(() => {
        if (alive) {
          setFileItems([])
          setFileError("failed")
        }
      })
      .finally(() => {
        if (alive) setFileLoading(false)
      })
    return () => {
      alive = false
    }
  }, [atQuery, atSplit.dir, atSplit.query])

  const fileOpen = atQuery !== null && !fileDismissed

  const applyFile = useCallback(
    (item: AtFileItem) => {
      const caret = taRef.current?.selectionStart ?? input.length
      const before = input.slice(0, caret)
      const m = /(^|\s)@([^\s@]*)$/.exec(before)
      if (!m) return
      // 目录：保留菜单并继续列出子项；文件：插入完整路径并补一个空格。
      const insert = item.type === "dir" ? `@${item.path}` : `@${item.path} `
      const start = before.length - m[2].length - 1
      const replaced = before.slice(0, start) + insert
      const next = replaced + input.slice(caret)
      setInput(next)
      setFileDismissed(false)
      taRef.current?.focus()
      window.setTimeout(() => {
        const el = taRef.current
        if (el) el.selectionStart = el.selectionEnd = replaced.length
        setCaret(replaced.length)
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
    if (!input.trim() && images.length === 0 && files.length === 0) return
    // `!cmd`：直接在工作目录执行，结果作为本地卡片呈现（opencode / codex 同款）
    if (input.startsWith("!") && images.length === 0 && files.length === 0) {
      void runShell(input.slice(1).trim())
      return
    }
    send(images, files)
    setImages([])
    setFiles([])
  }, [input, images, files, streaming, send, runShell])

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (fileOpen && fileItems.length > 0) {
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
        if (pick) applyFile(pick)
        return
      }
    }
    if (fileOpen && e.key === "Escape") {
      e.preventDefault()
      setFileDismissed(true)
      return
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
    const nextImages: string[] = []
    const nextFiles: ChatAttachment[] = []
    let tooLarge = 0
    for (const file of Array.from(list)) {
      if (file.type.startsWith("image/")) {
        const normalized = await normalizeImage(file)
        if (normalized) nextImages.push(normalized)
        continue
      }
      const looksText = file.type.startsWith("text/") || /^(\.(txt|md|markdown|json|jsonl|yaml|yml|toml|csv|tsv|log|py|ts|tsx|js|jsx|css|html|xml|ini|cfg|sh|bash|zsh|sql|go|rs|java|kt|c|h|cpp|hpp|rb|php|vue|svelte)$)/i.test(`.${file.name.split(".").pop() || ""}`)
      if (looksText && file.size <= 256 * 1024) {
        const content = await file.text()
        nextFiles.push({ name: file.name, content, mime: file.type || "text/plain", size: file.size, kind: "text" })
        continue
      }
      if (file.size > 2 * 1024 * 1024) {
        tooLarge += 1
        continue
      }
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result || ""))
        reader.onerror = () => reject(new Error("read failed"))
        reader.readAsDataURL(file)
      })
      nextFiles.push({
        name: file.name,
        data: dataUrl.split(",", 2)[1] || "",
        mime: file.type || "application/octet-stream",
        size: file.size,
        kind: attachmentKind(file.name, file.type).toLowerCase(),
      })
    }
    if (nextImages.length > 0) setImages((prev) => [...prev, ...nextImages])
    if (nextFiles.length > 0) setFiles((prev) => [...prev, ...nextFiles])
    if (tooLarge > 0) pushToast("toast.fileTooLarge", "error")
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

  const canSend = !streaming && (input.trim().length > 0 || images.length > 0 || files.length > 0)

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
        {files.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {files.map((file, i) => (
              <div key={`${file.name}-${i}`} className="group flex max-w-[260px] items-center gap-1.5 rounded-lg border border-line bg-panel px-2 py-1.5 text-[11.5px] text-ink2 shadow-sm">
                <IconFile size={13} className="shrink-0 text-accent" />
                <span className="min-w-0 flex-1 truncate font-mono" title={file.name}>{file.name}</span>
                {file.kind && file.kind !== "text" && <span className="shrink-0 rounded bg-hover px-1 py-0.5 text-[9px] font-semibold text-ink3">{file.kind.toUpperCase()}</span>}
                <span className="shrink-0 text-[10px] text-ink3">{formatFileSize(file.size ?? file.content?.length)}</span>
                <button
                  type="button"
                  onClick={() => setFiles((prev) => prev.filter((_, index) => index !== i))}
                  className="shrink-0 rounded p-0.5 text-ink3 hover:bg-hover hover:text-ink"
                  title={t("common.remove", lang)}
                >
                  <IconX size={10} />
                </button>
              </div>
            ))}
          </div>
        )}
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
              <div className="flex items-center gap-1 border-b border-line px-3 py-1.5 text-[10.5px] text-ink3">
                <IconFolder size={11} />
                <span className="truncate font-mono">{fileDir || "."}</span>
                {fileLoading && <Spinner size={10} className="ml-auto text-ink3" />}
                {!fileLoading && <span className="ml-auto">{fileItems.length}</span>}
              </div>
              {fileError && <p className="px-3 py-2.5 text-[12px] text-err">{t("composer.fileLoadFailed", lang)}</p>}
              {!fileLoading && !fileError && fileItems.length === 0 && (
                <p className="px-3 py-2.5 text-[12px] text-ink3">{t("composer.fileNoMatch", lang)}</p>
              )}
              {fileItems.map((f, i) => (
                <button
                  key={`${f.parent ? "parent" : f.path}-${i}`}
                  type="button"
                  role="option"
                  aria-selected={i === fileIndex}
                  onMouseEnter={() => setFileIndex(i)}
                  onMouseDown={(e) => {
                    e.preventDefault()
                    applyFile(f)
                  }}
                  className={cls(
                    "flex w-full items-center gap-2 px-3 py-2 text-left text-[12.5px]",
                    i === fileIndex ? "bg-hover text-ink" : "text-ink2",
                  )}
                >
                  {f.type === "dir" ? <IconFolder size={12} className="shrink-0 text-ink3" /> : <IconFile size={12} className="shrink-0 text-ink3" />}
                  <span className="min-w-0 flex-1 truncate font-mono">{f.parent ? t("composer.parentDir", lang) : f.name}</span>
                  <span className="shrink-0 text-[10px] text-ink3">{f.type === "dir" ? (f.parent ? ".." : "↵") : t("composer.fileRef", lang)}</span>
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
            onChange={(e) => {
              setInput(e.target.value)
              setCaret(e.target.selectionStart ?? e.target.value.length)
            }}
            onSelect={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
            onKeyDown={onKey}
            onPaste={onPaste}
            placeholder={streaming ? t("composer.placeholderStreaming", lang) : t("composer.placeholder", lang)}
            className="block max-h-[200px] w-full resize-none overflow-y-auto bg-transparent px-4 pt-3.5 pb-1 text-[14px] leading-relaxed outline-none placeholder:text-ink3"
          />
          <div className="flex items-center justify-between gap-2 px-2.5 pb-2.5">
            <div className="flex min-w-0 items-center gap-1">
              <button
                type="button"
                title={t("composer.attachFileHint", lang)}
                aria-label={t("composer.attachFileHint", lang)}
                disabled={streaming}
                onClick={() => fileRef.current?.click()}
                className="flex h-9 w-9 items-center justify-center rounded-lg text-ink3 transition-colors hover:bg-hover hover:text-ink disabled:opacity-45 sm:h-8 sm:w-8"
              >
                <IconPaperclip size={16} />
              </button>
              <button
                type="button"
                title={webSearch ? t("composer.webSearchOn", lang) : t("composer.webSearchOff", lang)}
                aria-label={webSearch ? t("composer.webSearchOn", lang) : t("composer.webSearchOff", lang)}
                aria-pressed={webSearch}
                onClick={() => {
                  const next = !webSearch
                  setWebSearch(next)
                  useUiStore.getState().pushToast(next ? "toast.webSearchOn" : "toast.webSearchOff", "info")
                }}
                className={cls(
                  "flex h-9 items-center gap-1.5 rounded-lg px-2 text-[11.5px] font-medium transition-colors sm:h-8",
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
                  {modelLabel}
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
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-line bg-hover text-ink transition-all duration-150 hover:border-line2 hover:bg-selected active:scale-95 sm:h-8.5 sm:w-8.5"
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
                    "flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent transition-all duration-150 hover:brightness-110 active:scale-95 sm:h-8.5 sm:w-8.5",
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
          accept="*/*"
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
