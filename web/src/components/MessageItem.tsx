import { lazy, memo, Suspense, useCallback, useEffect, useState } from "react"
import type { ChatMsg, Segment } from "../store/chatStore"
// Markdown + highlight.js are only needed once an assistant message with text
// exists, so they are not part of the first paint (saves ~112 KB gzip).
const Markdown = lazy(() => import("./Markdown"))
import ToolBlock from "./ToolBlock"
import { ThinkingBlock } from "./ThinkingBlock"
import { PlanCard } from "./PlanCard"
import { IconCheck, IconChevronDown, IconCopy, IconFile, IconPencil, IconRefresh, IconSettings, IconSpark, IconThumbDown, IconThumbUp, IconTrash } from "./icons"
import { cls, formatElapsed } from "./ui"
import { modelErrorCodeLabel, modelErrorText } from "../modelErrors"
import { useChatStore } from "../store/chatStore"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

export interface MessageActions {
  onEdit?: (msg: ChatMsg) => void
  onDelete?: (msg: ChatMsg) => void
  onRegenerate?: (msg: ChatMsg) => void
  onFeedback?: (msg: ChatMsg, rating: "up" | "down") => void
}

function MessageAction({
  title,
  onClick,
  disabled,
  danger,
  active,
  children,
}: {
  title: string
  onClick: () => void
  disabled?: boolean
  danger?: boolean
  active?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      aria-pressed={active}
      disabled={disabled}
      onClick={onClick}
      className={cls(
        // 移动端 36px 触控目标；桌面 28px，仍紧凑但更容易点中。
        "flex h-9 w-9 items-center justify-center rounded-lg text-ink3 transition-colors [&_svg]:h-4 [&_svg]:w-4 sm:h-7 sm:w-7 sm:rounded-md sm:[&_svg]:h-3.5 sm:[&_svg]:w-3.5",
        "hover:bg-hover hover:text-ink disabled:pointer-events-none disabled:opacity-35",
        active && "bg-accent/10 text-accent hover:bg-accent/15 hover:text-accent",
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

/** 消息时间：只显示本地时刻，没有真实时间就不显示。 */
function formatMessageTime(ms: number, lang: "zh" | "en"): string {
  return new Date(ms).toLocaleTimeString(lang === "zh" ? "zh-CN" : "en-US", {
    hour: "2-digit",
    minute: "2-digit",
  })
}

function formatTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

/** 生成中的秒表；结束后由固定的 durationMs 取代，不再跳动。 */
const Elapsed = memo(function Elapsed({ since, className }: { since: number; className?: string }) {
  const [, tick] = useState(0)
  useEffect(() => {
    const id = window.setInterval(() => tick((n) => n + 1), 500)
    return () => window.clearInterval(id)
  }, [since])
  return <span className={className}>{formatElapsed(Date.now() - since)}</span>
})

/* ---------------- 用户消息：右侧气泡 ---------------- */
const UserMessage = memo(function UserMessage({ msg, actions }: { msg: ChatMsg; actions?: MessageActions }) {
  const lang = useUiStore((s) => s.lang)
  const { copied, copy } = useCopy(msg.text)
  return (
    <div className="group flex justify-end gap-3 py-3">
      <div className="max-w-[86%] sm:max-w-[70%]">
        {msg.attachments && msg.attachments.length > 0 && (
          <div className="mb-1.5 flex flex-wrap justify-end gap-1.5">
            {msg.attachments.map((file, i) => (
              <span key={`${file.name}-${i}`} className="inline-flex max-w-[220px] items-center gap-1 rounded-md border border-line bg-panel px-2 py-1 text-[10.5px] text-ink2">
                <IconFile size={11} className="shrink-0 text-accent" />
                <span className="truncate font-mono">{file.name}</span>
              </span>
            ))}
          </div>
        )}
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
          {msg.createdAt !== undefined && (
            <span className="mr-1 self-center font-mono text-[10px] text-ink3">{formatMessageTime(msg.createdAt, lang)}</span>
          )}
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
function SideCard({ seg }: { seg: Extract<Segment, { kind: "side" }> }) {
  const lang = useUiStore((s) => s.lang)
  return (
    <div className={cls("my-1 rounded-lg border p-3 text-sm", seg.ok ? "border-sky-500/30 bg-sky-500/5" : "border-amber-500/40 bg-amber-500/5")}>
      <div className="mb-1 flex items-center gap-2 text-xs font-semibold text-sky-300">
        <span>💬 {t("side.title", lang)}</span>
        <span className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-[10px] text-ink2">{seg.model}</span>
        {!seg.ok && <span className="text-amber-300">⚠ {t("side.unavailable", lang)}</span>}
      </div>
      <div className="mb-1 text-ink2">{t("side.question", lang)}：{seg.question}</div>
      <div className="whitespace-pre-wrap text-ink">{seg.answer || t("side.noAnswer", lang)}</div>
    </div>
  )
}

function GoalCard({ seg }: { seg: Extract<Segment, { kind: "goal" }> }) {
  const g = seg.goal
  const lang = useUiStore((s) => s.lang)
  const statusBadge =
    g.status === "done"
      ? { text: t("goal.status.done", lang), cls: "bg-emerald-500/20 text-emerald-300" }
      : g.status === "cancelled"
        ? { text: t("goal.status.cancelled", lang), cls: "bg-white/10 text-ink2" }
        : { text: t("goal.status.active", lang), cls: "bg-accent/20 text-accent" }
  return (
    <div className="my-1 rounded-lg border border-accent/30 bg-accent/5 p-3 text-sm">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-base">🎯</span>
        <span className="font-semibold text-ink">{g.objective}</span>
        <span className={cls("rounded px-1.5 py-0.5 text-[10px] font-semibold", statusBadge.cls)}>{statusBadge.text}</span>
      </div>
      {g.checklist && g.checklist.length > 0 && (
        <ul className="mb-2 space-y-1">
          {g.checklist.map((c, i) => (
            <li key={i} className="flex items-start gap-2">
              <span className={cls("mt-0.5 font-mono text-xs", c.done ? "text-emerald-400" : "text-ink3")}>{c.done ? "✓" : "○"}</span>
              <span className={cls("flex-1", c.done ? "text-ink2" : "text-ink")}>
                {c.item}
                {c.evidence ? <span className="ml-1 text-xs text-ink3">{t("goal.evidence", lang, { x: c.evidence })}</span> : null}
              </span>
            </li>
          ))}
        </ul>
      )}
      {g.note ? <div className="text-xs text-ink3">{t("goal.note", lang, { x: g.note })}</div> : null}
      {seg.label ? <div className="mt-2 border-t border-white/10 pt-2 text-xs text-ink2">{seg.label}</div> : null}
    </div>
  )
}

function SegmentRenderer({ seg, streaming }: { seg: Segment; streaming?: boolean }) {
  switch (seg.kind) {
    case "thinking":
      return <ThinkingBlock text={seg.text} streaming={streaming} startedAt={seg.startedAt} durationMs={seg.durationMs} />
    case "plan":
      return <PlanCard steps={seg.steps} />
    case "tool":
      return <ToolBlock tool={seg.tool} />
    case "side":
      return <SideCard seg={seg} />
    case "goal":
      return <GoalCard seg={seg} />
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
  const statusModel = useSessionStore((s) => s.status?.model)
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
              {msg.startedAt !== undefined && <Elapsed since={msg.startedAt} className="font-mono text-[10px] text-accent/80" />}
            </span>
          )}
          {!msg.streaming && msg.durationMs !== undefined && (
            <span className="text-[10px] text-ink3" title={t("chat.elapsed", lang)}>
              {t("chat.elapsedShort", lang)} <span className="font-mono">{formatElapsed(msg.durationMs)}</span>
            </span>
          )}
          {(msg.model || statusModel) && (
            <span className="rounded bg-hover px-1.5 py-0.5 font-mono text-[9.5px] text-ink3">{msg.model || statusModel}</span>
          )}
          {msg.usage && (
            <span className="hidden text-[10px] text-ink3 sm:inline" title={t("chat.usage", lang)}>
              in <span className="font-mono">{formatTokens(msg.usage.input_tokens ?? 0)}</span> · out{" "}
              <span className="font-mono">{formatTokens(msg.usage.output_tokens ?? 0)}</span>
            </span>
          )}
          {msg.createdAt !== undefined && (
            <span className="font-mono text-[10px] text-ink3">{formatMessageTime(msg.createdAt, lang)}</span>
          )}
          <div className="ml-auto flex items-center gap-0.5 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100">
            {msg.text && !msg.streaming && (
              <MessageAction title={t("chat.copyAll", lang)} onClick={copy}>
                {copied ? <IconCheck size={12} className="text-ok" /> : <IconCopy size={12} />}
              </MessageAction>
            )}
            {!msg.streaming && msg.dbId && actions?.onFeedback && (
              <>
                <MessageAction
                  title={t("msg.feedbackUp", lang)}
                  active={msg.feedback === "up"}
                  onClick={() => actions.onFeedback?.(msg, "up")}
                >
                  <IconThumbUp size={12} />
                </MessageAction>
                <MessageAction
                  title={t("msg.feedbackDown", lang)}
                  active={msg.feedback === "down"}
                  onClick={() => actions.onFeedback?.(msg, "down")}
                >
                  <IconThumbDown size={12} />
                </MessageAction>
              </>
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
const ErrorMessage = memo(function ErrorMessage({ msg }: { msg: ChatMsg }) {
  const lang = useUiStore((s) => s.lang)
  const retry = useChatStore((s) => s.retryLastFailed)
  const canRetry = useChatStore((s) => s.lastFailedPrompt !== null)
  const streaming = useChatStore((s) => s.streaming)
  const openSettingsAt = useUiStore((s) => s.openSettingsAt)
  const [showDetail, setShowDetail] = useState(false)
  const info = msg.error
  const copy = modelErrorText(info, lang, msg.text)
  const retryable = info?.retryable !== false
  const wantsSettings = info?.action !== "none" && info?.action !== "retry"
  const meta = info
    ? [
        info.provider,
        info.model,
        info.endpoint,
        info.status_code ? `HTTP ${info.status_code}` : "",
      ].filter(Boolean)
    : []

  return (
    <div className="flex justify-center py-3" data-testid="model-error">
      <div className="anim-msg w-full max-w-2xl rounded-xl border border-err/35 bg-err/8 px-4 py-3 text-[13px] text-err">
        <div className="flex flex-wrap items-center gap-2">
          <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-err/15 text-[11px] font-bold">
            !
          </span>
          <span className="font-semibold">{copy.title}</span>
          {info?.code && (
            <span className="rounded-full border border-err/30 bg-err/5 px-1.5 py-0.5 text-[10px] font-medium text-err/80">
              {modelErrorCodeLabel(info.code, lang)}
            </span>
          )}
        </div>
        <p className="mt-1.5 whitespace-pre-wrap break-words text-err/90">{copy.message || msg.text}</p>
        {copy.hint && (
          <p className="mt-2 rounded-lg border border-err/20 bg-canvas/50 px-2.5 py-2 text-[12px] leading-relaxed text-ink2">
            <span className="font-medium text-ink">{lang === "zh" ? "怎么处理：" : "What to do: "}</span>
            {copy.hint}
          </p>
        )}
        {meta.length > 0 && (
          <p className="mt-2 break-all font-mono text-[10.5px] text-ink3">
            {meta.join(" · ")}
          </p>
        )}
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          {canRetry && retryable && (
            <button
              type="button"
              disabled={streaming}
              onClick={() => void retry()}
              className="inline-flex h-7 items-center gap-1.5 rounded-md border border-err/40 px-2.5 text-[12px] font-medium text-err transition-colors hover:bg-err/10 disabled:opacity-50"
            >
              <IconRefresh size={12} /> {t("error.retry", lang)}
            </button>
          )}
          {wantsSettings && (
            <button
              type="button"
              onClick={() => openSettingsAt("model")}
              className="inline-flex h-7 items-center gap-1.5 rounded-md border border-line bg-panel2 px-2.5 text-[12px] font-medium text-ink2 transition-colors hover:bg-hover"
            >
              <IconSettings size={12} /> {lang === "zh" ? "打开模型设置" : "Open model settings"}
            </button>
          )}
          {info?.detail && (
            <button
              type="button"
              onClick={() => setShowDetail((v) => !v)}
              className="ml-auto inline-flex h-7 items-center gap-1 px-1.5 text-[11px] text-ink3 hover:text-ink"
              aria-expanded={showDetail}
            >
              {lang === "zh" ? "技术细节" : "Technical details"}
              <IconChevronDown size={12} className={cls("transition-transform", showDetail && "rotate-180")} />
            </button>
          )}
        </div>
        {showDetail && info?.detail && (
          <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-line bg-code px-2.5 py-2 font-mono text-[10.5px] leading-relaxed text-ink3">
            {info.detail}
          </pre>
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
      return <ErrorMessage msg={msg} />
    case "notice":
    case "summary":
      return <NoticeMessage msg={msg} />
    case "shell":
      return <ShellMessage msg={msg} />
  }
}
