import { lazy, Suspense, useCallback, useEffect, useState } from "react"
import { Sidebar } from "./components/Sidebar"
import { RightPanel } from "./components/RightPanel"
import { Header } from "./components/Header"
import { ChatView } from "./components/ChatView"
import { Composer } from "./components/Composer"
import { ApprovalModal } from "./components/ApprovalModal"
import { ShortcutsHelp } from "./components/ShortcutsHelp"
import { QuestionCard } from "./components/QuestionCard"
import { Onboarding } from "./components/Onboarding"
import Toast from "./components/Toast"
import { ConfirmDialog } from "./components/ConfirmDialog"
import { fetchStatus } from "./api"
import type { Status } from "./types"
import { useChatStore } from "./store/chatStore"
import { useSessionStore } from "./store/sessionStore"
import { useUiStore } from "./store/uiStore"
import { loadBindings, matchesBinding } from "./shortcuts"
import { installNotificationWatcher } from "./notify"
import { activateModelProfile, listModelProfiles } from "./api"

// Heavy, rarely-first-action surfaces are split out of the initial bundle:
// xterm, xyflow and the diff/checkpoint/replay UIs would otherwise all load
// before the first message is even typed.
const SettingsModal = lazy(() => import("./components/SettingsModal"))
const CommandPalette = lazy(() => import("./components/CommandPalette").then((m) => ({ default: m.CommandPalette })))
const CheckpointModal = lazy(() => import("./components/CheckpointModal").then((m) => ({ default: m.CheckpointModal })))
const FileViewer = lazy(() => import("./components/FileViewer").then((m) => ({ default: m.FileViewer })))
const BrowserModal = lazy(() => import("./components/BrowserModal").then((m) => ({ default: m.BrowserModal })))
const Terminal = lazy(() => import("./components/Terminal").then((m) => ({ default: m.Terminal })))
const ReplayModal = lazy(() => import("./components/ReplayModal").then((m) => ({ default: m.ReplayModal })))
const ExecutionGraph = lazy(() => import("./components/ExecutionGraph").then((m) => ({ default: m.ExecutionGraph })))

export default function App() {
  // Selector subscriptions only: subscribing to whole stores re-rendered this
  // root on every streamed token (rAF-batched), which defeated the memoised
  // virtual list underneath.
  const setStatus = useSessionStore((s) => s.setStatus)
  const refreshSessions = useSessionStore((s) => s.refreshSessions)
  const loadInitialHistory = useChatStore((s) => s.loadInitialHistory)
  const closeSettings = useUiStore((s) => s.closeSettings)
  const toasts = useUiStore((s) => s.toasts)
  const dismissToast = useUiStore((s) => s.dismissToast)

  useEffect(() => {
    fetchStatus()
      .then((s) => {
        setStatus(s)
        useSessionStore.getState().setPlan(s.plan || [])
      })
      .catch(() => useUiStore.getState().pushToast("toast.backendError", "error"))
    refreshSessions()
    loadInitialHistory()
  }, [setStatus, refreshSessions, loadInitialHistory])

  // 回合结束 / 等待审批时的桌面通知（设置 → 展示页可开；默认关）
  useEffect(() => installNotificationWatcher(), [])

  // 深链：`?panel=settings&tab=wiki` / `?panel=palette` / `?panel=files`。
  //
  // 两个用途：截图自动化能直接进到某个面板（不用模拟点击），
  // 用户也能把"这个设置页在哪"直接发给同事。参数非法时静默忽略——
  // 链接写错不该让应用弹错。
  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search)
      const panel = params.get("panel")
      if (!panel) return
      const ui = useUiStore.getState()
      if (panel === "settings") {
        const tab = params.get("tab")
        ui.openSettingsAt((tab || "model") as Parameters<typeof ui.openSettingsAt>[0])
      } else if (panel === "palette") {
        ui.setPaletteOpen(true)
      } else if (panel === "files") {
        ui.openFiles()
      } else if (panel === "checkpoints") {
        ui.openCheckpoints()
      }
    } catch {
      /* malformed URL: ignore */
    }
  }, [])

  // 全局快捷键（可在设置 → 快捷键里改，绑定存 localStorage）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const b = loadBindings()
      if (matchesBinding(e, b.newSession)) {
        e.preventDefault()
        if (!useChatStore.getState().streaming) void useSessionStore.getState().handleNewSession()
      }
      // Ctrl+K must live here, not in CommandPalette: that component is only
      // mounted while the palette is open, so a shortcut defined inside it can
      // never open it.
      if (matchesBinding(e, b.palette)) {
        e.preventDefault()
        const ui = useUiStore.getState()
        ui.setPaletteOpen(!ui.paletteOpen)
      }
      if (matchesBinding(e, b.toggleSidebar)) {
        e.preventDefault()
        useUiStore.getState().toggleSidebar()
      }
      if (matchesBinding(e, b.terminal)) {
        e.preventDefault()
        const s = useUiStore.getState()
        if (s.terminalOpen) s.closeTerminal()
        else s.openTerminal()
      }
      // 循环最近模型（opencode F2 / Shift+F2 的 Web 等价物）
      if (e.ctrlKey && e.altKey && e.key.toLowerCase() === "m") {
        e.preventDefault()
        void cycleModel()
      }
      // `?` 打开快捷键帮助（Claude Code 习惯）；输入框内不抢键。
      if (matchesBinding(e, b.help)) {
        e.preventDefault()
        useUiStore.getState().openHelp()
      }
      // Esc 停止生成。跳过输入框（用户在用 Esc 取消输入）与任何弹层
      // （它们自己处理 Esc），也跳过待审批状态（Esc 应留给审批动作）。
      if (e.key === "Escape" && !e.defaultPrevented) {
        const target = e.target as HTMLElement | null
        const tag = target?.tagName?.toLowerCase()
        if (tag === "input" || tag === "textarea" || target?.isContentEditable) return
        const ui = useUiStore.getState()
        const overlayOpen =
          ui.paletteOpen || ui.settingsOpen || ui.checkpointsOpen || ui.filesOpen ||
          ui.browserOpen || ui.graphOpen || ui.replayOpen || ui.terminalOpen ||
          ui.helpOpen
        if (overlayOpen) return
        if (useSessionStore.getState().approval) return
        if (useChatStore.getState().streaming) {
          e.preventDefault()
          void useSessionStore.getState().handleCancel()
        }
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  const handleSaved = useCallback((s: Status) => {
    setStatus(s)
    useSessionStore.getState().setPlan(s.plan || [])
  }, [setStatus])

  const settingsOpen = useUiStore((s) => s.settingsOpen)
  const paletteOpen = useUiStore((s) => s.paletteOpen)
  const checkpointsOpen = useUiStore((s) => s.checkpointsOpen)
  const filesOpen = useUiStore((s) => s.filesOpen)
  const browserOpen = useUiStore((s) => s.browserOpen)
  const terminalOpen = useUiStore((s) => s.terminalOpen)
  const graphOpen = useUiStore((s) => s.graphOpen)
  const replayOpen = useUiStore((s) => s.replayOpen)
  const seen = useSeenSurfaces({
    palette: paletteOpen,
    checkpoints: checkpointsOpen,
    files: filesOpen,
    browser: browserOpen,
    terminal: terminalOpen,
    graph: graphOpen,
    replay: replayOpen,
  })

  return (
    <div className="flex h-full overflow-hidden bg-canvas text-ink">
      <Toast toasts={toasts} onDismiss={dismissToast} />
      <ConfirmDialog />
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header />
        <ChatView />
        <Composer />
      </div>
      <RightPanel />
      <ApprovalModal />
      <QuestionCard />
      <ShortcutsHelp />
      <Onboarding />
      {/* Chunks are fetched on first open (keeps the initial bundle at ~140 KB),
          but each surface then stays mounted: unmounting on close threw away
          the browse URL, the file viewer's current directory and the replay
          drill-down, which felt like the app forgot what you were doing.
          Every component already returns null while its own flag is false. */}
      <Suspense fallback={<SurfaceLoading />}>
        {seen.palette && <CommandPalette />}
        {seen.checkpoints && <CheckpointModal />}
        {seen.files && <FileViewer />}
        {seen.browser && <BrowserModal />}
        {seen.terminal && <Terminal />}
        {seen.graph && <ExecutionGraph />}
        {seen.replay && <ReplayModal />}
      </Suspense>
      {settingsOpen && (
        <Suspense fallback={null}>
          <SettingsModal onClose={closeSettings} onSaved={handleSaved} />
        </Suspense>
      )}
    </div>
  )
}


/** 在当前模型的最近档案间循环切换（写入后端 active_profile_id）。 */
async function cycleModel(): Promise<void> {
  try {
    const { profiles } = await listModelProfiles()
    const active = profiles.findIndex((p) => p.active)
    if (profiles.length === 0) return
    const next = profiles[(active + 1) % profiles.length]
    if (!next || next.active) return
    const res = await activateModelProfile(next.id)
    useSessionStore.getState().setStatus(res.status)
    useUiStore.getState().pushToast("toast.modelSwitched", "info")
  } catch {
    useUiStore.getState().pushToast("toast.modelSwitchFailed", "error")
  }
}

/** Latch each surface on its first open so local UI state survives a close. */
function useSeenSurfaces(open: Record<string, boolean>): Record<string, boolean> {
  const [seen, setSeen] = useState<Record<string, boolean>>({})
  useEffect(() => {
    const next = Object.keys(open).filter((k) => open[k] && !seen[k])
    // Only re-render when a new surface was opened for the first time.
    if (next.length > 0) {
      setSeen((prev) => {
        const merged = { ...prev }
        for (const key of next) merged[key] = true
        return merged
      })
    }
  }, [open, seen])
  return seen
}

/** Minimal placeholder while a lazily-loaded surface chunk arrives. */
function SurfaceLoading() {
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/20 backdrop-blur-[1px]" role="status" aria-live="polite">
      <div className="flex items-center gap-2 rounded-lg border border-line bg-panel px-3 py-2 text-xs text-ink2">
        <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
        <span>Loading…</span>
      </div>
    </div>
  )
}
