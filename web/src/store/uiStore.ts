import { create } from "zustand"
import { getDefaultLang, setDefaultLang, type Lang, type TranslationKey } from "../i18n"
import type { Palette, Theme } from "../theme"
import { applyPalette, applyTheme, getStoredPalette, getStoredTheme, initTheme } from "../theme"
import type { CheckpointRow } from "../api"

export type SettingsTab =
  | "model"
  | "agent"
  | "security"
  | "display"
  | "memory"
  | "mcp"
  | "skills"
  | "prompts"
  | "plugins"
  | "shortcuts"
  | "knowledge"
  | "project"
  | "automations"
  | "advanced"

export interface ToastItem {
  id: number
  key: TranslationKey
  kind: "error" | "success" | "info"
  /** 原文消息（API 返回的具体错误）。有它时优先显示，避免只弹一个 "错误"。 */
  message?: string
}

/** 统一的非阻塞确认请求（替代原生 window.confirm，风格与全站一致）。 */
export interface ConfirmRequest {
  title: string
  message?: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
  onConfirm: () => void
}

interface UiState {
  theme: Theme
  palette: Palette
  setPalette: (p: Palette) => void
  settingsOpen: boolean
  sidebarOpen: boolean
  rightPanelOpen: boolean
  paletteOpen: boolean
  checkpointsOpen: boolean
  filesOpen: boolean
  browserOpen: boolean
  terminalOpen: boolean
  graphOpen: boolean
  replayOpen: boolean
  helpOpen: boolean
  checkpoints: CheckpointRow[]
  cpsBusy: boolean
  toasts: ToastItem[]
  lang: Lang
  confirm: ConfirmRequest | null
  setTheme: (t: Theme) => void
  cycleTheme: () => void
  openSettings: () => void
  openSettingsAt: (tab: SettingsTab) => void
  closeSettings: () => void
  settingsTab: SettingsTab | null
  setSettingsTab: (tab: SettingsTab | null) => void
  toggleSidebar: () => void
  setSidebarOpen: (open: boolean) => void
  toggleRightPanel: () => void
  setRightPanelOpen: (open: boolean) => void
  setPaletteOpen: (open: boolean) => void
  openCheckpoints: () => void
  closeCheckpoints: () => void
  openFiles: () => void
  closeFiles: () => void
  openBrowser: () => void
  closeBrowser: () => void
  openTerminal: () => void
  closeTerminal: () => void
  openGraph: () => void
  closeGraph: () => void
  openReplay: () => void
  closeReplay: () => void
  openHelp: () => void
  closeHelp: () => void
  setCheckpoints: (rows: CheckpointRow[]) => void
  setCpsBusy: (busy: boolean) => void
  pushToast: (key: TranslationKey, kind?: ToastItem["kind"], message?: string) => void
  dismissToast: (id: number) => void
  setLang: (lang: Lang) => void
  requestConfirm: (req: ConfirmRequest) => void
  dismissConfirm: () => void
}

let toastCounter = 0

export const useUiStore = create<UiState>((set) => ({
  theme: initTheme(),
  palette: getStoredPalette(),
  settingsOpen: false,
  sidebarOpen: window.innerWidth >= 1024,
  rightPanelOpen: true,
  paletteOpen: false,
  checkpointsOpen: false,
  filesOpen: false,
  browserOpen: false,
  terminalOpen: false,
  graphOpen: false,
  replayOpen: false,
  helpOpen: false,
  checkpoints: [],
  cpsBusy: false,
  toasts: [],
  lang: getDefaultLang(),
  confirm: null,

  setTheme: (t) => {
    applyTheme(t)
    set({ theme: t })
  },
  setPalette: (p) => {
    applyPalette(p)
    set({ palette: p })
  },
  cycleTheme: () => {
    const order: Theme[] = ["light", "dark", "system"]
    const cur = getStoredTheme()
    const next = order[(order.indexOf(cur) + 1) % order.length]
    applyTheme(next)
    set({ theme: next })
  },

  openSettings: () => set({ settingsOpen: true, settingsTab: null, checkpointsOpen: false, replayOpen: false, graphOpen: false, terminalOpen: false }),
  openSettingsAt: (tab) => set({ settingsOpen: true, settingsTab: tab, checkpointsOpen: false, replayOpen: false, graphOpen: false, terminalOpen: false }),
  closeSettings: () => set({ settingsOpen: false }),
  settingsTab: null,
  setSettingsTab: (tab) => set({ settingsTab: tab }),
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  toggleRightPanel: () => set((s) => ({ rightPanelOpen: !s.rightPanelOpen })),
  setRightPanelOpen: (open) => set({ rightPanelOpen: open }),
  setPaletteOpen: (open) => set({ paletteOpen: open }),

  openCheckpoints: () => set({ settingsOpen: false, checkpointsOpen: true, filesOpen: false, browserOpen: false, graphOpen: false, replayOpen: false, terminalOpen: false }),
  closeCheckpoints: () => set({ checkpointsOpen: false }),
  openFiles: () => set({ settingsOpen: false, checkpointsOpen: false, filesOpen: true, browserOpen: false, graphOpen: false, replayOpen: false, terminalOpen: false }),
  closeFiles: () => set({ filesOpen: false }),
  openBrowser: () => set({ settingsOpen: false, checkpointsOpen: false, filesOpen: false, browserOpen: true, graphOpen: false, replayOpen: false, terminalOpen: false }),
  closeBrowser: () => set({ browserOpen: false }),
  openTerminal: () => set({ settingsOpen: false, checkpointsOpen: false, filesOpen: false, browserOpen: false, graphOpen: false, replayOpen: false, terminalOpen: true }),
  closeTerminal: () => set({ terminalOpen: false }),
  openGraph: () => set({ settingsOpen: false, checkpointsOpen: false, filesOpen: false, browserOpen: false, graphOpen: true, replayOpen: false, terminalOpen: false }),
  closeGraph: () => set({ graphOpen: false }),
  openReplay: () => set({ settingsOpen: false, checkpointsOpen: false, filesOpen: false, browserOpen: false, graphOpen: false, replayOpen: true, terminalOpen: false }),
  closeReplay: () => set({ replayOpen: false }),
  openHelp: () => set({ helpOpen: true }),
  closeHelp: () => set({ helpOpen: false }),
  setCheckpoints: (rows) => set({ checkpoints: rows }),
  setCpsBusy: (busy) => set({ cpsBusy: busy }),

  pushToast: (key, kind = "error", message) => {
    toastCounter += 1
    const id = toastCounter
    set((s) => ({ toasts: [...s.toasts.slice(-3), { id, key, kind, message }] }))
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

  setLang: (lang) => {
    setDefaultLang(lang)
    set({ lang })
  },

  requestConfirm: (req) => set({ confirm: req }),
  dismissConfirm: () => set({ confirm: null }),
}))
