import { memo, useEffect } from "react"
import type { SessionRow, Status } from "../types"
import { useChatStore } from "../store/chatStore"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { ChatView } from "./ChatView"
import { Composer } from "./Composer"
import { ModelStatusBanner } from "./ModelStatusBanner"
import { BrandMark } from "./BrandMark"
import { IconChat, IconHome, IconPlus, IconSettings, IconChevronRight } from "./icons"
import { Button, cls, Spinner } from "./ui"
import { t } from "../i18n"

function sessionTitle(lang: "zh" | "en", sessions: SessionRow[], id?: string): string {
  if (!id) return t("common.newChat", lang)
  const row = sessions.find((s) => s.id === id)
  if (!row || !row.title || row.title === "untitled") return t("common.newChat", lang)
  return row.title
}

function modelLabel(status: Status | null, lang: "zh" | "en"): string {
  if (!status) return lang === "zh" ? "连接中…" : "Connecting…"
  if (status.demo_mode) return t("mobile.home.demo", lang)
  return status.model || status.provider
}

/** Phone-first shell: home dashboard + fixed tab bar. Desktop keeps the existing shell. */
export const MobileShell = memo(function MobileShell({ recovering = false }: { recovering?: boolean }) {
  const lang = useUiStore((s) => s.lang)
  const tab = useUiStore((s) => s.mobileTab)
  const setTab = useUiStore((s) => s.setMobileTab)
  const openSettings = useUiStore((s) => s.openSettings)
  const settingsOpen = useUiStore((s) => s.settingsOpen)
  const setApprovalOpen = useUiStore((s) => s.setMobileApprovalOpen)

  const status = useSessionStore((s) => s.status)
  const sessions = useSessionStore((s) => s.sessions)
  const approval = useSessionStore((s) => s.approval)
  const approvalBusy = useSessionStore((s) => s.approvalBusy)
  const handleApproval = useSessionStore((s) => s.handleApproval)
  const handleNewSession = useSessionStore((s) => s.handleNewSession)
  const handleSwitch = useSessionStore((s) => s.handleSwitch)
  const streaming = useChatStore((s) => s.streaming)
  const itemsCount = useChatStore((s) => s.items.length)
  const handleCancel = useSessionStore((s) => s.handleCancel)
  const pushToast = useUiStore((s) => s.pushToast)

  useEffect(() => {
    if (!approval) setApprovalOpen(false)
  }, [approval, setApprovalOpen])

  const title = sessionTitle(lang, sessions, status?.session_id)
  const recent = sessions.filter((s) => s.id !== status?.session_id).slice(0, 4)

  async function startNew() {
    if (streaming) {
      pushToast("mobile.home.newWhileBusy", "info")
      return
    }
    await handleNewSession()
    setTab("chat")
  }

  async function switchTo(id: string) {
    if (id === status?.session_id) {
      setTab("chat")
      return
    }
    await handleSwitch(id)
    setTab("chat")
  }

  if (tab === "chat") {
    return (
      <div className="flex h-full min-h-0 flex-col overflow-hidden">
        <header className="glass flex h-12 shrink-0 items-center gap-1 border-b border-line px-2">
          <button
            type="button"
            onClick={() => setTab("home")}
            aria-label={t("mobile.chat.back", lang)}
            className="flex h-9 w-9 items-center justify-center rounded-lg text-[17px] text-ink2 transition-colors hover:bg-hover hover:text-ink"
          >
            ←
          </button>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13px] font-semibold tracking-[-0.01em] text-ink">{title}</p>
            <p className="truncate text-[10px] text-ink3">{modelLabel(status, lang)}</p>
          </div>
          {streaming ? (
            <button
              type="button"
              onClick={() => void handleCancel()}
              aria-label={t("mobile.chat.stop", lang)}
              className="flex h-9 items-center gap-1.5 rounded-full border border-err/30 bg-err/10 px-2.5 text-[11px] font-medium text-err"
            >
              <span className="h-1.5 w-1.5 rounded-full bg-err breathe" />
              <span className="h-2.5 w-2.5 rounded-[3px] bg-err/80" />
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void startNew()}
              aria-label={t("mobile.chat.new", lang)}
              title={t("mobile.chat.new", lang)}
              className="flex h-9 w-9 items-center justify-center rounded-lg text-ink3 transition-colors hover:bg-hover hover:text-ink"
            >
              <IconPlus size={17} />
            </button>
          )}
          <button
            type="button"
            onClick={openSettings}
            aria-label={t("mobile.nav.settings", lang)}
            className="flex h-9 w-9 items-center justify-center rounded-lg text-ink3 transition-colors hover:bg-hover hover:text-ink"
          >
            <IconSettings size={17} />
          </button>
        </header>
        <ModelStatusBanner />
        {recovering && (
          <div className="flex shrink-0 items-center gap-2 border-b border-line bg-panel2 px-3 py-1.5 text-[11px] text-ink2">
            <Spinner size={12} />
            {lang === "zh" ? "正在恢复会话" : "Recovering session…"}
          </div>
        )}
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <ChatView />
          <Composer />
        </div>
        <MobileTabBar
          lang={lang}
          tab={tab}
          settingsOpen={settingsOpen}
          hasApproval={Boolean(approval)}
          onHome={() => setTab("home")}
          onChat={() => setTab("chat")}
          onNew={() => void startNew()}
          onSettings={openSettings}
        />
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden bg-canvas px-3 pt-3 pb-4">
        <div className="mx-auto w-full max-w-[520px]">
          <header className="mb-3 flex items-center gap-2.5">
            <BrandMark size={34} />
            <div className="min-w-0 flex-1">
              <p className="text-[16px] font-semibold tracking-[-0.02em] text-ink">Spark</p>
              <p className="truncate text-[10.5px] text-ink3">{t("mobile.home.subtitle", lang)}</p>
            </div>
            <span className="flex items-center gap-1.5 rounded-full border border-line bg-panel px-2 py-1 text-[10px] text-ink3">
              <span className={cls("h-1.5 w-1.5 rounded-full", status ? "bg-ok" : "bg-warn breathe")} />
              {status ? modelLabel(status, lang) : t("common.loading", lang)}
            </span>
          </header>

          {status?.workdir && (
            <p className="mb-3 truncate rounded-lg bg-panel2 px-2.5 py-1.5 font-mono text-[10px] text-ink3" title={status.workdir}>
              {status.workdir}
            </p>
          )}

          {approval && (
            <section className="anim-fade-up mb-3 rounded-xl border border-warn/40 bg-warn/8 p-3.5">
              <div className="flex items-start gap-2">
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-warn/20 text-[11px] text-warn">!</span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="text-[12px] font-semibold text-warn">{t("mobile.home.approval", lang)}</p>
                    {approval.info.tool && (
                      <span className="max-w-[55%] truncate rounded-md bg-panel/60 px-1.5 py-0.5 font-mono text-[10px] text-ink2">
                        {approval.info.tool}
                      </span>
                    )}
                  </div>
                  <p className="mt-1 line-clamp-2 break-words text-[12px] leading-relaxed text-ink2">{approval.info.summary}</p>
                  <p className="mt-1 text-[10.5px] text-ink3">{t("mobile.home.approvalHint", lang)}</p>
                </div>
              </div>
              <div className="mt-3 flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  full
                  disabled={approvalBusy}
                  onClick={() => void handleApproval("deny")}
                >
                  {t("mobile.home.reject", lang)}
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  full
                  disabled={approvalBusy}
                  onClick={() => setApprovalOpen(true)}
                >
                  {approvalBusy ? <Spinner size={12} /> : t("mobile.home.review", lang)}
                </Button>
              </div>
              {(approval.info.diff || approval.info.arguments) && (
                <button
                  type="button"
                  onClick={() => setApprovalOpen(true)}
                  className="mt-2 w-full text-left text-[10.5px] text-ink3 underline-offset-2 hover:text-accent hover:underline"
                >
                  {t("mobile.home.details", lang)}
                </button>
              )}
            </section>
          )}

          <button
            type="button"
            onClick={() => setTab("chat")}
            className="surface mb-3 w-full rounded-xl p-3.5 text-left transition-colors hover:border-line2"
          >
            <div className="flex items-center justify-between gap-2">
              <p className="text-[10.5px] font-medium uppercase tracking-[0.08em] text-ink3">{t("mobile.home.current", lang)}</p>
              <span className="flex items-center gap-1.5 text-[10px] text-ink3">
                <span className={cls("h-1.5 w-1.5 rounded-full", streaming ? "bg-accent breathe" : "bg-ok")} />
                {streaming ? t("mobile.home.streaming", lang) : t("mobile.home.idle", lang)}
              </span>
            </div>
            <p className="mt-1.5 truncate text-[15px] font-semibold text-ink">{title}</p>
            <div className="mt-1 flex items-center gap-2 text-[11px] text-ink3">
              <span className="truncate">{modelLabel(status, lang)}</span>
              <span aria-hidden>·</span>
              <span className="shrink-0">
                {itemsCount} {t("mobile.home.messages", lang)}
              </span>
            </div>
            <p className="mt-2 flex items-center gap-1 text-[11.5px] font-medium text-accent">
              {t("mobile.home.continue", lang)} <IconChevronRight size={13} />
            </p>
          </button>

          <button
            type="button"
            onClick={() => void startNew()}
            className="surface mb-4 flex w-full items-center gap-3 rounded-xl border-accent/30 p-3.5 text-left transition-colors hover:border-accent/60 hover:bg-accent-soft/30"
          >
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
              <IconPlus size={20} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[14px] font-semibold text-ink">{t("mobile.home.newTask", lang)}</span>
              <span className="mt-0.5 block text-[11px] text-ink3">{t("mobile.home.newTaskDesc", lang)}</span>
            </span>
            <span className="shrink-0 text-[11px] font-medium text-accent">{t("mobile.home.start", lang)}</span>
          </button>

          <section>
            <div className="mb-1.5 flex items-center justify-between px-0.5">
              <h2 className="text-[11px] font-medium uppercase tracking-[0.08em] text-ink3">{t("mobile.home.recent", lang)}</h2>
              <span className="text-[10px] text-ink3">{sessions.length}</span>
            </div>
            {recent.length === 0 ? (
              <p className="rounded-xl border border-dashed border-line px-3 py-4 text-center text-[11px] text-ink3">
                {t("mobile.home.noRecent", lang)}
              </p>
            ) : (
              <div className="overflow-hidden rounded-xl border border-line bg-panel">
                {recent.map((row) => (
                  <button
                    key={row.id}
                    type="button"
                    onClick={() => void switchTo(row.id)}
                    className="flex w-full items-center gap-2 border-b border-line px-3 py-2.5 text-left transition-colors last:border-b-0 hover:bg-hover"
                  >
                    <IconChat size={14} className="shrink-0 text-ink3" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[12.5px] font-medium text-ink">
                        {!row.title || row.title === "untitled" ? t("common.newChat", lang) : row.title}
                      </span>
                      <span className="mt-0.5 block truncate text-[10.5px] text-ink3">{row.model}</span>
                    </span>
                    <IconChevronRight size={13} className="shrink-0 text-ink3" />
                  </button>
                ))}
              </div>
            )}
          </section>
        </div>
      </div>

      <MobileTabBar
        lang={lang}
        tab={tab}
        settingsOpen={settingsOpen}
        hasApproval={Boolean(approval)}
        onHome={() => setTab("home")}
        onChat={() => setTab("chat")}
        onNew={() => void startNew()}
        onSettings={openSettings}
      />
    </div>
  )
})

function MobileTabBar({
  lang,
  tab,
  settingsOpen,
  hasApproval,
  onHome,
  onChat,
  onNew,
  onSettings,
}: {
  lang: "zh" | "en"
  tab: "home" | "chat"
  settingsOpen: boolean
  hasApproval: boolean
  onHome: () => void
  onChat: () => void
  onNew: () => void
  onSettings: () => void
}) {
  return (
    <nav
      aria-label={t("mobile.nav.home", lang)}
      className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-panel/95 backdrop-blur-md"
      style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
    >
      <div className="mx-auto grid h-16 max-w-[520px] grid-cols-4 items-center">
        <TabButton
          label={t("mobile.nav.home", lang)}
          active={tab === "home" && !settingsOpen}
          badge={hasApproval}
          onClick={onHome}
          icon={<IconHome size={19} />}
        />
        <TabButton
          label={t("mobile.nav.chat", lang)}
          active={tab === "chat" && !settingsOpen}
          onClick={onChat}
          icon={<IconChat size={19} />}
        />
        <TabButton
          label={t("mobile.nav.new", lang)}
          active={false}
          onClick={onNew}
          icon={
            <span className="flex h-10 w-10 items-center justify-center rounded-full bg-accent text-on-accent shadow-[0_4px_14px_-4px_var(--accent-glow)]">
              <IconPlus size={21} />
            </span>
          }
        />
        <TabButton
          label={t("mobile.nav.settings", lang)}
          active={settingsOpen}
          onClick={onSettings}
          icon={<IconSettings size={19} />}
        />
      </div>
    </nav>
  )
}

function TabButton({
  label,
  active,
  badge = false,
  onClick,
  icon,
}: {
  label: string
  active: boolean
  badge?: boolean
  onClick: () => void
  icon: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-current={active ? "page" : undefined}
      className={cls(
        "relative flex h-16 flex-col items-center justify-center gap-1 text-[10.5px] font-medium transition-colors",
        active ? "text-accent" : "text-ink3 hover:text-ink2",
      )}
    >
      <span className="relative">
        {icon}
        {badge && (
          <span className="absolute -right-2 -top-1 h-2 w-2 rounded-full border border-panel bg-warn" aria-hidden />
        )}
      </span>
      {label}
    </button>
  )
}
