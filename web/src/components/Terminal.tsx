import { memo, useEffect, useRef, useState } from "react"
import { Terminal as XTerm } from "@xterm/xterm"
import { FitAddon } from "@xterm/addon-fit"
import "@xterm/xterm/css/xterm.css"
import { ptyClose, ptyOpen, ptyResize, ptyWrite, streamPty } from "../api"
import { Button, IconButton, Spinner, cls } from "./ui"
import { IconCopy, IconExternal, IconRefresh, IconTerminal, IconX } from "./icons"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

const TAB_ID = "main"

/**
 * 内置终端（P1-5）：xterm.js 6.0.0 + 后端真实 PTY。
 *
 * 通道：后端不支持 WebSocket，读侧走 SSE（streamPty），写侧走 POST。
 * 关闭弹窗只是断开前端附着（shell 保持后台常驻，重开自动恢复）；
 * 「结束会话」才真正 kill shell。
 */
export const Terminal = memo(function Terminal({ embedded = false }: { embedded?: boolean }) {
  const storeOpen = useUiStore((s) => s.terminalOpen)
  const open = embedded || storeOpen
  const close = useUiStore((s) => s.closeTerminal)
  const lang = useUiStore((s) => s.lang)
  const containerRef = useRef<HTMLDivElement>(null)
  const xtermRef = useRef<XTerm | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const [ready, setReady] = useState(false)
  const [hasSelection, setHasSelection] = useState(false)
  const exitedRef = useRef(false)
  const [cwd, setCwd] = useState("")
  /** 重启 shell 通过递增 epoch 重建 xterm 与 SSE，而不是复用旧 PTY 的残流。 */
  const [epoch, setEpoch] = useState(0)
  const [maximized, setMaximized] = useState(false)

  useEffect(() => {
    if (!open) return
    setReady(false)
    setHasSelection(false)
    exitedRef.current = false
    const el = containerRef.current
    if (!el) return

    const term = new XTerm({
      fontSize: 12.5,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      theme: {
        background: "#0d1117",
        foreground: "#d6deeb",
        cursor: "#7aa2f7",
        selectionBackground: "#283550",
      },
      scrollback: 5000,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(el)
    fit.fit()
    xtermRef.current = term
    fitRef.current = fit
    term.focus()

    let cancelled = false

    async function boot() {
      try {
        const info = await ptyOpen(TAB_ID)
        setCwd(info.cwd)
      } catch {
        term.writeln(`\x1b[31m${t("terminal.connectFailed", useUiStore.getState().lang)}\x1b[0m`)
        setReady(true)
        return
      }
      // 同步终端尺寸给后端
      const dims = fitRef.current
      if (dims) {
        try {
          const { cols, rows } = term
          await ptyResize(TAB_ID, rows, cols)
        } catch { /* ignore */ }
      }
      setReady(true)

      const ac = new AbortController()
      abortRef.current = ac
      try {
        await streamPty(
          TAB_ID,
          (ev) => {
            if (cancelled) return
            if (ev.type === "pty_data") {
              term.write(ev.data)
            } else if (ev.type === "pty_exit") {
              exitedRef.current = true
              term.writeln(`\r\n\x1b[90m${t("terminal.exited", useUiStore.getState().lang)}\x1b[0m`)
            }
          },
          ac.signal,
        )
      } catch {
        /* 弹窗关闭时 abort 会走这里 */
      }
    }

    const onDataSub = term.onData((d) => {
      if (exitedRef.current) {
        // 退出后按键重启 shell
        exitedRef.current = false
        void ptyOpen(TAB_ID).catch(() => undefined)
        return
      }
      void ptyWrite(TAB_ID, d).catch(() => undefined)
    })

    const onSelection = term.onSelectionChange(() => setHasSelection(Boolean(term.getSelection())))
    const onResize = () => {
      try {
        fit.fit()
        void ptyResize(TAB_ID, term.rows, term.cols).catch(() => undefined)
      } catch { /* ignore */ }
    }
    window.addEventListener("resize", onResize)

    void boot()

    return () => {
      cancelled = true
      window.removeEventListener("resize", onResize)
      onDataSub.dispose()
      onSelection.dispose()
      abortRef.current?.abort()
      abortRef.current = null
      try { term.dispose() } catch { /* ignore */ }
      xtermRef.current = null
      fitRef.current = null
    }
  }, [open, epoch])

  function copySelection() {
    const selected = xtermRef.current?.getSelection() ?? ""
    if (!selected) return
    navigator.clipboard?.writeText(selected).catch(() => {})
  }

  function clearScreen() {
    xtermRef.current?.clear()
    setHasSelection(false)
  }

  function copyAll() {
    const term = xtermRef.current
    if (!term) return
    const lines: string[] = []
    const buffer = term.buffer.active
    for (let i = 0; i < buffer.length; i += 1) {
      const line = buffer.getLine(i)?.translateToString(true) ?? ""
      // 只裁掉尾部空白，保留代码缩进。
      lines.push(line.replace(/\s+$/g, ""))
    }
    while (lines.length > 0 && lines[lines.length - 1] === "") lines.pop()
    navigator.clipboard?.writeText(lines.join("\n")).catch(() => {})
  }

  function restartShell() {
    void ptyClose(TAB_ID)
      .catch(() => undefined)
      .then(() => setEpoch((value) => value + 1))
  }

  if (!open) return null

  if (embedded) {
    return (
      <div className="flex min-h-0 flex-1 flex-col bg-[#0d1117]">
        <div className="flex items-center gap-2 border-b border-white/10 px-3 py-2">
          <IconTerminal size={13} className="text-accent" />
          <span className="text-[12px] font-semibold text-ink">{t("terminal.title", lang)}</span>
          {cwd && <span className="ml-1 hidden truncate font-mono text-[10px] text-ink3 sm:inline">{cwd}</span>}
          {!ready && <Spinner size={11} className="ml-1 text-ink3" />}
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto"
            onClick={copySelection}
            disabled={!hasSelection}
            title={t("terminal.copy", lang)}
          >
            <IconCopy size={12} />
            {t("terminal.copy", lang)}
          </Button>
          <Button size="sm" variant="ghost" onClick={clearScreen} title={t("terminal.clear", lang)}>
            {t("terminal.clear", lang)}
          </Button>
          <IconButton title={t("terminal.copyAll", lang)} onClick={copyAll} disabled={!ready}>
            <IconCopy size={12} />
          </IconButton>
          <IconButton title={t("terminal.restart", lang)} onClick={restartShell}>
            <IconRefresh size={13} />
          </IconButton>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              void ptyClose(TAB_ID).then(() => {
                exitedRef.current = true
                xtermRef.current?.writeln(`\r\n\x1b[90m${t("terminal.sessionEnded", lang)}\x1b[0m`)
              })
            }}
          >
            {t("terminal.closeSession", lang)}
          </Button>
        </div>
        <div ref={containerRef} className="min-h-0 flex-1 p-2" />
      </div>
    )
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40 p-0 backdrop-blur-[2px]" onClick={close}>
      <div
        className={cls(
          "anim-fade flex w-full flex-col overflow-hidden border-t border-line bg-[#0d1117] shadow-[var(--shadow)]",
          maximized ? "h-screen sm:h-[96vh] sm:w-[96vw] sm:rounded-2xl sm:border" : "h-[82vh] sm:w-[min(94vw,980px)] sm:rounded-2xl sm:border",
        )}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={t("terminal.title", lang)}
      >
        <div className="flex items-center gap-2 border-b border-white/10 px-4 py-2.5">
          <IconTerminal size={15} className="text-accent" />
          <span className="text-sm font-semibold text-ink">{t("terminal.title", lang)}</span>
          {cwd && <span className="ml-1 hidden font-mono text-[11px] text-ink3 sm:inline">{cwd}</span>}
          {!ready && <Spinner size={12} className="ml-2 text-ink3" />}
          <div className="ml-auto flex items-center gap-1.5">
            <Button
              size="sm"
              variant="ghost"
              onClick={copySelection}
              disabled={!hasSelection}
              title={t("terminal.copy", lang)}
            >
              <IconCopy size={12} />
              {t("terminal.copy", lang)}
            </Button>
            <Button size="sm" variant="ghost" onClick={clearScreen} title={t("terminal.clear", lang)}>
              {t("terminal.clear", lang)}
            </Button>
            <IconButton title={t("terminal.copyAll", lang)} onClick={copyAll} disabled={!ready}>
              <IconCopy size={12} />
            </IconButton>
            <IconButton title={t("terminal.restart", lang)} onClick={restartShell}>
              <IconRefresh size={13} />
            </IconButton>
            <IconButton title={maximized ? t("terminal.restore", lang) : t("terminal.maximize", lang)} onClick={() => setMaximized((v) => !v)}>
              <IconExternal size={13} className={maximized ? "rotate-180" : ""} />
            </IconButton>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                void ptyClose(TAB_ID).then(() => {
                  exitedRef.current = true
                  xtermRef.current?.writeln(`\r\n\x1b[90m${t("terminal.sessionEnded", lang)}\x1b[0m`)
                })
              }}
            >
              {t("terminal.closeSession", lang)}
            </Button>
            <IconButton title={t("common.close", lang)} onClick={close}>
              <IconX size={15} />
            </IconButton>
          </div>
        </div>
        <div ref={containerRef} className="min-h-0 flex-1 p-2" />
      </div>
    </div>
  )
})
