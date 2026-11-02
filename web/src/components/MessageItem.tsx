import { lazy, memo, Suspense, useCallback, useState } from "react"
import type { ChatMsg, Segment } from "../store/chatStore"
// Markdown + highlight.js are only needed once an assistant message with text
// exists, so they are not part of the first paint (saves ~112 KB gzip).
const Markdown = lazy(() => import("./Markdown"))
import ToolBlock from "./ToolBlock"
import { ThinkingBlock } from "./ThinkingBlock"
import { PlanCard } from "./PlanCard"
import { IconCheck, IconCopy, IconPencil, IconRefresh, IconSpark, IconTrash } from "./icons"
import { cls } from "./ui"
import { useChatStore } from "../store/chatStore"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

export interface MessageActions {
  onEdit?: (msg: ChatMsg) => void
  onDelete?: (msg: ChatMsg) => void
  onRegenerate?: (msg: ChatMsg) => void
}

function MessageAction({
  title,
  onClick,
  disabled,
  danger,
  children,
}: {
  title: string
  onClick: () => void
  disabled?: boolean
  danger?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      disabled={disabled}
      onClick={onClick}
      className={cls(
        "flex h-6 w-6 items-center justify-center rounded-md text-ink3 transition-colors hover:bg-hover hover:text-ink disabled:pointer-events-none disabled:opacity-35",
        danger && "hover:text-err",
      )}
    >
      {children}
    </button>
  )
}

function useCopy(text: string) {
  const [copied, setCopied] = useState(false)
  const copy = useCallback(() => {
    navigator.clipboard?.writeText(text).catch(() => {})
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1400)
  }, [text])
  return { copied, copy }
}

/* ---------------- 用户消息：右侧气泡 ---------------- */
const UserMessage = memo(function UserMessage({ msg, actions }: { msg: ChatMsg; actions?: MessageActions }) {
  const lang = useUiStore((s) => s.lang)
  const { copied, copy } = useCopy(msg.text)
  return (
    <div className="group flex justify-end gap-3 py-3">
      <div className="max-w-[86%] sm:max-w-[70%]">
        {msg.images && msg.images.length > 0 && (
          <div className="mb-2 flex flex-wrap justify-end gap-2">
            {msg.images.map((src, i) => (
              <img
                key={i}
                src={src}
                alt={`attached-${i + 1}`}
                className="max-h-44 max-w-full rounded-lg border border-line object-cover"
              />
            ))}
          </div>
        )}
        <div className="anim-msg rounded-[16px] rounded-br-[6px] border border-line bg-[var(--user-bubble)] px-4 py-2.5 text-[13.5px] leading-relaxed whitespace-pre-wrap break-words text-[var(--user-bubble-text)] shadow-[var(--shadow-sm)]">
          {msg.text}
        </div>
        <div className="mt-1 flex justify-end gap-0.5 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100">
          <MessageAction title={t("common.copy", lang)} onClick={copy}>
            {copied ? <IconCheck size={12} className="text-ok" /> : <IconCopy size={12} />}
          </MessageAction>
          <MessageAction
            title={t("msg.editResend", lang)}
            onClick={() => actions?.onEdit?.(msg)}
            disabled={!msg.dbId || !actions?.onEdit}
          >
            <IconPencil size={12} />
          </MessageAction>
          <MessageAction
            title={t("msg.delete", lang)}
            danger
            onClick={() => actions?.onDelete?.(msg)}
            disabled={!msg.dbId || !actions?.onDelete}
          >
            <IconTrash size={12} />
          </MessageAction>
        </div>
      </div>
      <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-hover text-[12px] font-semibold text-ink2">
        {t("chat.you", lang)}
      </div>
    </div>
  )
})

/* ---------------- 助手消息 ---------------- */
function SegmentRenderer({ seg, streaming }: { seg: Segment; streaming?: boolean }) {
  switch (seg.kind) {
    case "thinking":
      return <ThinkingBlock text={seg.text} streaming={streaming} />
    case "plan":
      return <PlanCard steps={seg.steps} />
    case "tool":
      return <ToolBlock tool={seg.tool} />
    case "text":
      if (!seg.text) return null
      return (
        <div className={cls("md-body-wrap", streaming && "caret")}>
          <Suspense fallback={<span className="whitespace-pre-wrap">{seg.text}</span>}>
            <Markdown text={seg.text} />
          </Suspense>
        </div>
      )
  }
}

const AssistantMessage = memo(function AssistantMessage({ msg, actions }: { msg: ChatMsg; actions?: MessageActions }) {
  const { copied, copy } = useCopy(msg.text)
  const lang = useUiStore((s) => s.lang)
  const lastTextStreaming = msg.streaming && msg.segments.some((s) => s.kind === "text" && s.text.length > 0)
  const d = useSessionStore((s) => s.status?.display)
  const show = {
    thinking: d?.show_thinking ?? true,
    tools: d?.show_tools ?? true,
    plan: d?.show_plan ?? true,
    notices: d?.show_notices ?? true,
  }
  const segments = show.tools && show.plan && show.thinking ? msg.segments : msg.segments.filter((s) => (s.kind === "tool" && !show.tools) || (s.kind === "plan" && !show.plan) || (s.kind === "thinking" && !show.thinking) ? false : true)

  return (
    <div className="group flex gap-3 py-3.5">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-[9px] bg-accent-soft ring-1 ring-accent/20">
        <IconSpark size={14} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="mb-1.5 flex items-center gap-2">
          <span className="text-[11.5px] font-semibold tracking-[0.01em] text-ink">Spark</span>
          {msg.streaming && !lastTextStreaming && (
            <span className="flex items-center gap-1 text-[11px] text-accent">
              <span className="flex gap-0.5">
                {[0, 1, 2].map((i) => (
                  <span
                    key={i}
                    className="h-1 w-1 rounded-full bg-accent"
                    style={{ animation: `pulse-dot 1.2s ease-in-out ${i * 0.2}s infinite` }}
                  />
                ))}
              </span>
              {t("chat.working", lang)}
            </span>
          )}
          <div className="ml-auto flex items-center gap-0.5 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100">
            {msg.text && !msg.streaming && (
              <MessageAction title={t("chat.copyAll", lang)} onClick={copy}>
                {copied ? <IconCheck size={12} className="text-ok" /> : <IconCopy size={12} />}
              </MessageAction>
            )}
            {!msg.streaming && (
              <MessageAction
                title={t("msg.regenerate", lang)}
                onClick={() => actions?.onRegenerate?.(msg)}
                disabled={!msg.dbId || !actions?.onRegenerate}
              >
                <IconRefresh size={12} />
              </MessageAction>
            )}
            {!msg.streaming && (
              <MessageAction
                title={t("msg.delete", lang)}
                danger
                onClick={() => actions?.onDelete?.(msg)}
                disabled={!msg.dbId || !actions?.onDelete}
              >
                <IconTrash size={12} />
              </MessageAction>
            )}
          </div>
        </div>
        <div className="min-w-0 space-y-1">
          {segments.map((seg, i) => (
            <SegmentRenderer key={i} seg={seg} streaming={msg.streaming && i === segments.length - 1} />
          ))}
          {msg.streaming && segments.length === 0 && (
            <div className="flex items-center gap-2 py-1">
              <span className="flex gap-0.5">
                {[0, 1, 2].map((i) => (
                  <span key={i} className="h-1.5 w-1.5 rounded-full bg-accent" style={{ animation: `pulse-dot 1.2s ease-in-out ${i * 0.2}s infinite` }} />
                ))}
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
})

/* ---------------- 错误 / 通知 / 摘要 ---------------- */
const ErrorMessage = memo(function ErrorMessage({ text }: { text: string }) {
  const lang = useUiStore((s) => s.lang)
  const retry = useChatStore((s) => s.retryLastFailed)
  const canRetry = useChatStore((s) => s.lastFailedPrompt !== null)
  const streaming = useChatStore((s) => s.streaming)
  return (
    <div className="flex justify-center py-3">
      <div className="anim-msg w-full max-w-2xl rounded-xl border border-err/35 bg-err/8 px-4 py-2.5 text-[13px] text-err">
        <div className="mb-0.5 font-semibold">{t("common.error", lang)}</div>
        <p className="whitespace-pre-wrap break-words text-err/90">{text}</p>
        {canRetry && (
          // A transport failure used to leave the user with a dead error card:
          // the prompt was gone and there was no way to resend without retyping.
          <button
            type="button"
            disabled={streaming}
            onClick={() => void retry()}
            className="mt-2 inline-flex h-7 items-center gap-1.5 rounded-md border border-err/40 px-2.5 text-[12px] font-medium text-err transition-colors hover:bg-err/10 disabled:opacity-50"
          >
            <IconRefresh size={12} /> {t("error.retry", lang)}
          </button>
        )}
      </div>
    </div>
  )
})

const NoticeMessage = memo(function NoticeMessage({ msg }: { msg: ChatMsg }) {
  const show = useSessionStore((s) => s.status?.display?.show_notices ?? true)
  if (!show) return null
  return (
    <div className="flex justify-center py-2">
      <div className="anim-msg flex items-center gap-2 rounded-full border border-line bg-panel2 px-3.5 py-1.5 text-[11.5px] text-ink3">
        {msg.meta?.compacted ? <span className="text-warn">⟳</span> : <span>·</span>}
        <span className="break-words">{msg.text}</span>
      </div>
    </div>
  )
})


const ShellMessage = memo(function ShellMessage({ msg }: { msg: ChatMsg }) {
  const lang = useUiStore((s) => s.lang)
  const shell = msg.meta?.shell
  if (!shell) return null
  const running = shell.exitCode === -1 && !shell.stderr
  const failed = shell.exitCode !== 0 || Boolean(shell.stderr)
  return (
    <div className="anim-msg py-2">
      <div className="overflow-hidden rounded-xl border border-line bg-panel">
        <div className="flex items-center gap-2 border-b border-line bg-panel2 px-3 py-2">
          <span className="rounded bg-hover px-1.5 py-0.5 font-mono text-[10.5px] font-semibold text-accent">
            {t("composer.shellTag", lang)}
          </span>
          <code className="min-w-0 flex-1 truncate font-mono text-[12px] text-ink">!{shell.command}</code>
          <span
            className={cls(
              "shrink-0 rounded-full px-2 py-0.5 text-[10.5px] font-medium",
              running ? "bg-hover text-ink3" : failed ? "bg-err/10 text-err" : "bg-ok/10 text-ok",
            )}
          >
            {running ? "…" : `${t("shell.exit", lang)} ${shell.exitCode}`}
          </span>
        </div>
        {(shell.stdout || shell.stderr) && (
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words bg-[#0f1115] px-3 py-2.5 font-mono text-[12px] leading-relaxed text-[#cfe3ff]">
            {shell.stdout}
            {shell.stderr && <span className="text-[#ff9b9b]">{shell.stdout ? "\n" : ""}{shell.stderr}</span>}
          </pre>
        )}
        {shell.truncated && <div className="border-t border-line px-3 py-1 text-[10.5px] text-warn">{t("shell.truncated", lang)}</div>}
      </div>
    </div>
  )
})

export default function MessageItem({ msg, actions }: { msg: ChatMsg; actions?: MessageActions }) {
  switch (msg.role) {
    case "user":
      return <UserMessage msg={msg} actions={actions} />
    case "assistant":
      return <AssistantMessage msg={msg} actions={actions} />
    case "error":
      return <ErrorMessage text={msg.text} />
    case "notice":
    case "summary":
      return <NoticeMessage msg={msg} />
    case "shell":
      return <ShellMessage msg={msg} />
  }
}
