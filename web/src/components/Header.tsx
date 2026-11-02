import { memo } from "react"
import type { SessionRow } from "../types"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { ContextMeter } from "./ContextMeter"
import { IconButton } from "./ui"
import { IconCommand, IconFolder, IconFork, IconGlobe, IconHelp, IconMenu, IconSpark, IconTerminal } from "./icons"
import { t, type Lang } from "../i18n"

export const Header = memo(function Header() {
  const status = useSessionStore((s) => s.status)
  const handleCancel = useSessionStore((s) => s.handleCancel)
  const streaming = useChatStoreStreaming()
  const lang = useUiStore((s) => s.lang)
  const toggleSidebar = useUiStore((s) => s.toggleSidebar)
  const setPaletteOpen = useUiStore((s) => s.setPaletteOpen)
  const openHelp = useUiStore((s) => s.openHelp)
  const openFiles = useUiStore((s) => s.openFiles)
  const toggleRightPanel = useUiStore((s) => s.toggleRightPanel)
  const openBrowser = useUiStore((s) => s.openBrowser)
  const openTerminal = useUiStore((s) => s.openTerminal)
  const openGraph = useUiStore((s) => s.openGraph)

  const sessions = useSessionStore((s) => s.sessions)
  const title = (status && status.session_id && sessionTitle(lang, sessions, status.session_id)) || t("common.newChat", lang)

  return (
    <header className="glass flex h-12 shrink-0 items-center gap-1 border-b border-line px-2 sm:px-3">
      <IconButton title={t("header.toggleSidebar", lang)} onClick={toggleSidebar} className="lg:hidden">
        <IconMenu size={17} />
      </IconButton>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <p className="truncate text-[13px] font-semibold tracking-[-0.01em] text-ink">{title}</p>
          {status?.demo_mode && (
            <span
              title={status.demo_reason}
              className="hidden shrink-0 items-center gap-1 rounded-full border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-[10px] font-medium text-warn sm:inline-flex"
            >
              <span className="h-1.5 w-1.5 rounded-full bg-warn breathe" />
              {t("header.demo", lang)}
            </span>
          )}
        </div>
        {status?.workdir && (
          <p className="hidden truncate text-[10px] text-ink3 sm:block" title={status.workdir}>
            {status.workdir}
          </p>
        )}
      </div>

      {streaming && (
        <button
          type="button"
          onClick={handleCancel}
          className="mr-1 flex items-center gap-1.5 rounded-full border border-err/30 bg-err/10 px-2.5 py-1 text-[11px] font-medium text-err transition-colors hover:bg-err/20"
        >
          <span className="h-1.5 w-1.5 rounded-full bg-err breathe" />
          <span className="hidden sm:inline">{t("common.stop", lang)}</span>
        </button>
      )}

      <ContextMeter />
      {/* 响应式隐藏必须放在外层 span 上：IconButton 自带 inline-flex，
          与传入的 hidden 同属 display 工具类，胜负取决于 CSS 生成顺序，
          实测 390px 下 9 个按钮全显示、标题被压成 0 宽、演示徽标与计量重叠。 */}
      <span className="hidden sm:inline-flex">
        <IconButton title={t("files.title", lang)} onClick={openFiles}>
          <IconFolder size={16} />
        </IconButton>
      </span>
      <span className="hidden sm:inline-flex">
        <IconButton title={t("browser.title", lang)} onClick={openBrowser}>
          <IconGlobe size={16} />
        </IconButton>
      </span>
      <span className="hidden md:inline-flex">
        <IconButton title={t("graph.title", lang)} onClick={openGraph}>
          <IconSpark size={16} />
        </IconButton>
      </span>
      <span className="hidden md:inline-flex">
        <IconButton title={t("sidebar.fork", lang)} onClick={() => void useSessionStore.getState().handleFork()}>
          <IconFork size={16} />
        </IconButton>
      </span>
      <span className="hidden sm:inline-flex">
        <IconButton title={`${t("terminal.title", lang)} (Ctrl+\`)`} onClick={openTerminal}>
          <IconTerminal size={16} />
        </IconButton>
      </span>
      <IconButton title={`${t("palette.title", lang)} (Ctrl+K)`} onClick={() => setPaletteOpen(true)}>
        <IconCommand size={16} />
      </IconButton>
      {/* 右侧面板（文件/预览/网页）：与左侧边栏对称，默认展开 */}
      <span className="hidden md:inline-flex">
        <IconButton title={t("rightPanel.title", lang)} onClick={toggleRightPanel}>
          <IconMenu size={16} />
        </IconButton>
      </span>
      {/* 审查报告第五节体验差距：帮助此前只能靠 `?` 键，无可见入口。 */}
      <IconButton title={`${t("help.title", lang)} (?)`} onClick={openHelp}>
        <IconHelp size={16} />
      </IconButton>
    </header>
  )
})

import { useChatStore } from "../store/chatStore"
function useChatStoreStreaming() {
  return useChatStore((s) => s.streaming)
}

/** 会话标题：取当前会话标题（sessionStore.sessions 中匹配） */
/** 会话标题：必须由组件订阅 sessions 后传入，否则侧栏改名后标题不刷新。 */
function sessionTitle(lang: Lang, sessions: SessionRow[], currentId: string): string {
  const row = sessions.find((r) => r.id === currentId)
  return row && row.title && row.title !== "untitled" ? row.title : t("common.newChat", lang)
}
