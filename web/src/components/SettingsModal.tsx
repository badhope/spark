import { useCallback, useEffect, useRef, useState } from "react"
import type { FullConfig, HookEntry, McpServer, ModelProfile, ProbeResult, SettingsPayload, Status } from "../types"
import { fetchConfig, fetchAgentsMd, saveAgentsMd, saveSettings, testConnection, listModelProfiles, saveModelProfile, deleteModelProfile, activateModelProfile, fetchDiagnostics, runSelfHeal, savePermissionRules, deletePermissionRule, saveApprovalProfile, deleteApprovalProfile, activateApprovalProfile, type HealthRow } from "../api"
import MemorySection from "./MemorySection"
import SkillsSection from "./SkillsSection"
import PromptsSection from "./PromptsSection"
import PluginsSection from "./PluginsSection"
import ShortcutsSection from "./ShortcutsSection"
import KnowledgeSection from "./KnowledgeSection"
import WikiSection from "./WikiSection"
import AutomationsSection from "./AutomationsSection"
import DebugSection from "./DebugSection"
import { McpRegistryModal } from "./McpRegistryModal"
import type { McpRegistryItem } from "../api"
import { PALETTES, type Palette, type Theme } from "../theme"
import { Badge, Button, Field, IconButton, Select, Spinner, TextArea, TextInput, Toggle, cls } from "./ui"
import { IconBrain, IconBug, IconCheck, IconClock, IconCommand, IconFile, IconFolder, IconGlobe, IconKey, IconLayers, IconMoon, IconPlus, IconRefresh, IconSettings, IconSpark, IconStethoscope, IconSun, IconTerminal, IconTrash, IconWrench, IconX } from "./icons"
import { useSessionStore } from "../store/sessionStore"
import { useUiStore } from "../store/uiStore"
import { t, type TranslationKey } from "../i18n"
import { notificationsEnabled, notificationPermission, requestNotificationPermission, setNotificationsEnabled } from "../notify"

// Mirrors spark.hooks.HookRegistry.EVENTS (backend validates against it).
/** Split a hook argument line, honouring "double" and 'single' quotes. */
function splitArgs(raw: string): string[] {
  const out: string[] = []
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g
  let m: RegExpExecArray | null
  while ((m = re.exec(raw)) !== null) out.push(m[1] ?? m[2] ?? m[3])
  return out
}

/** 色块取样用静态色：CSS 变量要到 applyPalette 之后才变，静态表更直观 */
const PALETTE_SWATCH: Record<Palette, string> = {
  ember: "#ff7a2f",
  ocean: "#2f9dff",
  forest: "#35c98a",
  violet: "#a07dff",
  mono: "#b9b9c4",
}

const HOOK_EVENTS = [
  "turn_start", "pre_tool", "post_tool", "turn_end",
  "app_start", "app_stop", "session_create", "session_load", "session_switch",
  "compaction", "memory_extract", "context_threshold",
  "approval_needed", "tool_error", "plan_update",
  "turn_error", "provider_error",
]

type Tab =
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
  | "wiki"
  | "project"
  | "automations"
  | "debug"
  | "advanced"

// 预装模型预设：国内可用优先，用户选一个自动填 provider/base_url/model，只需填 API Key
const MODEL_PRESETS: { labelKey: TranslationKey; provider: string; base_url: string; model: string }[] = [
  { labelKey: "preset.deepseek", provider: "openai_compat", base_url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  { labelKey: "preset.qwen", provider: "openai_compat", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
  { labelKey: "preset.kimi", provider: "openai_compat", base_url: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k" },
  { labelKey: "preset.glm", provider: "openai_compat", base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4-flash" },
  { labelKey: "preset.doubao", provider: "openai_compat", base_url: "https://ark.cn-beijing.volces.com/api/v3", model: "doubao-pro-32k" },
  { labelKey: "preset.agnes", provider: "openai_compat", base_url: "https://api.agnes-ai.cn/v1", model: "agnes-2.5-flash" },
  { labelKey: "preset.ollama", provider: "ollama", base_url: "http://localhost:11434/v1", model: "qwen2.5-coder" },
  { labelKey: "preset.mock", provider: "mock", base_url: "http://localhost", model: "mock-model" },
]

const TABS: { key: Tab; labelKey: TranslationKey; icon: React.ReactNode }[] = [
  { key: "model", labelKey: "tab.model", icon: <IconLayers size={14} /> },
  { key: "agent", labelKey: "tab.agent", icon: <IconTerminal size={14} /> },
  { key: "security", labelKey: "tab.security", icon: <IconKey size={14} /> },
  { key: "display", labelKey: "tab.display", icon: <IconSun size={14} /> },
  { key: "memory", labelKey: "tab.memory", icon: <IconBrain size={14} /> },
  { key: "mcp", labelKey: "tab.mcp", icon: <IconGlobe size={14} /> },
  { key: "skills", labelKey: "tab.skills", icon: <IconSpark size={14} /> },
  { key: "prompts", labelKey: "tab.prompts", icon: <IconFile size={14} /> },
  { key: "plugins", labelKey: "tab.plugins", icon: <IconFolder size={14} /> },
  { key: "shortcuts", labelKey: "tab.shortcuts", icon: <IconCommand size={14} /> },
  { key: "knowledge", labelKey: "tab.knowledge", icon: <IconBrain size={14} /> },
  { key: "wiki", labelKey: "tab.wiki", icon: <IconFile size={14} /> },
  { key: "project", labelKey: "tab.project", icon: <IconSettings size={14} /> },
  { key: "automations", labelKey: "tab.automations", icon: <IconClock size={14} /> },
  { key: "debug", labelKey: "tab.debug", icon: <IconBug size={14} /> },
  { key: "advanced", labelKey: "tab.advanced", icon: <IconStethoscope size={14} /> },
]

interface Props {
  onClose: () => void
  onSaved: (s: Status) => void
}

/**
 * 环境体检 + 一键修复（Project 页底部）。
 *
 * 和 CLI 的 `spark doctor` 共用同一份后端报告（/api/diagnostics），
 * 所以界面不会再出现「CLI 说有问题、界面说一切正常」的分裂。
 */
function HealthSection({ lang }: { lang: "zh" | "en" }) {
  const [rows, setRows] = useState<HealthRow[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const pushToast = useUiStore((s) => s.pushToast)

  const load = useCallback(async () => {
    try {
      const data = await fetchDiagnostics()
      setRows(data.rows)
    } catch {
      setRows([])
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const blockers = rows?.filter((r) => r.level === "bad") ?? []
  const warnings = rows?.filter((r) => r.level === "warn") ?? []
  const autoFixable = [...blockers, ...warnings].filter((r) => r.auto_fixable)
  const visible = expanded ? (rows ?? []) : [...blockers, ...warnings].slice(0, 4)

  async function repair() {
    setBusy(true)
    try {
      const report = await runSelfHeal()
      setRows(report.rows)
      pushToast("toast.selfhealDone", "success")
      if (report.remaining.length > 0) setExpanded(true)
    } catch {
      pushToast("toast.selfhealFailed", "error")
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-ink">
          <IconStethoscope size={14} className="text-accent" />
          {t("health.title", lang)}
        </h3>
        <div className="flex items-center gap-1.5">
          <Button size="sm" variant="ghost" onClick={() => void load()} disabled={busy}>
            <IconRefresh size={12} /> {t("health.refresh", lang)}
          </Button>
          {autoFixable.length > 0 && (
            <Button size="sm" variant="primary" onClick={() => void repair()} disabled={busy}>
              {busy ? <Spinner size={11} /> : <IconWrench size={12} />} {t("health.fix", lang, { n: autoFixable.length })}
            </Button>
          )}
        </div>
      </div>
      <p className="text-xs leading-relaxed text-ink3">{t("health.desc", lang)}</p>
      {rows === null && <p className="py-2 text-xs text-ink3">{t("common.loading", lang)}</p>}
      {rows !== null && blockers.length === 0 && warnings.length === 0 && (
        <p className="flex items-center gap-1.5 py-1 text-xs text-ok">
          <IconCheck size={12} /> {t("health.allGood", lang)}
        </p>
      )}
      <ul className="flex flex-col gap-1">
        {visible.map((row, i) => (
          <li
            key={`${row.kind}-${i}`}
            className={cls(
              "rounded-lg border px-2.5 py-1.5 text-[11.5px]",
              row.level === "bad"
                ? "border-err/30 bg-err/5 text-err"
                : row.level === "warn"
                  ? "border-warn/30 bg-warn/5 text-warn"
                  : "border-line bg-panel2 text-ink2",
            )}
          >
            <div className="flex items-start gap-1.5">
              <span className="mt-0.5 shrink-0">{row.level === "bad" ? "!" : row.level === "warn" ? "?" : "✓"}</span>
              <div className="min-w-0">
                <p className="break-words">{row.message}</p>
                {row.fix && <p className="mt-0.5 break-words text-ink3">{row.fix}</p>}
              </div>
            </div>
          </li>
        ))}
      </ul>
      {(rows?.length ?? 0) > visible.length && (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="self-start text-[11px] text-accent hover:underline"
        >
          {t("health.showAll", lang)}
        </button>
      )}
    </section>
  )
}
export default function SettingsDrawer({ onClose, onSaved }: Props) {
  const lang = useUiStore((s) => s.lang)
  const setLang = useUiStore((s) => s.setLang)
  const requestedTab = useUiStore((s) => s.settingsTab)
  const [tab, setTab] = useState<Tab>((requestedTab as Tab) || "model")
  const [cfg, setCfg] = useState<FullConfig | null>(null)
  const [mcpRows, setMcpRows] = useState<McpServer[]>([])
  const [agentsMd, setAgentsMd] = useState<{ filename: string; content: string; chars: number; max_fragment_chars: number } | null>(null)
  const [agentsMdName, setAgentsMdName] = useState("")
  const [probe, setProbe] = useState<ProbeResult | null>(null)
  const [apiKeyInput, setApiKeyInput] = useState("")
  const [notice, setNotice] = useState("")
  const [busy, setBusy] = useState<"save" | "test" | "profile" | null>(null)
  const [profiles, setProfiles] = useState<ModelProfile[]>([])
  const [showProfileForm, setShowProfileForm] = useState(false)
  const [profileForm, setProfileForm] = useState({ id: "", name: "", provider: "openai_compat", base_url: "", model: "", api_key: "", api_key_env: "", api_keysText: "", fallbackModelsText: "", small_model: "" })
  const [protectedText, setProtectedText] = useState("")
  const [providerApiKeysText, setProviderApiKeysText] = useState("")
  const [providerFallbackText, setProviderFallbackText] = useState("")
  const [providerSmallModel, setProviderSmallModel] = useState("")
  const resilienceDirty = useRef({ api_keys: false, fallback_models: false, small_model: false })
  const [hookRows, setHookRows] = useState<HookEntry[]>([])
  // Raw argument text per row, so quotes survive typing; rows are re-split on
  // every change but the input keeps what the user actually typed.
  const [argsText, setArgsText] = useState<Record<number, string>>({})
  // Raw HTTP header text per MCP server (keyed by name), so a half-typed
  // "Authorization:" line is not parsed away mid-keystroke.
  const [headerText, setHeaderText] = useState<Record<string, string>>({})
  // Empty = keep the stored embedding key (same contract as provider.api_key).
  const [embeddingKeyInput, setEmbeddingKeyInput] = useState("")
  // Debug 页签（运行时诊断）
  // 治理面：权限规则/审批档案走各自的即时端点（与模型档案同构），
  // 不塞进 /api/settings 的统一保存——它们是安全边界变更，需要独立反馈。
  const [ruleBusy, setRuleBusy] = useState(false)
  const [ruleDraft, setRuleDraft] = useState<{ action: "allow" | "prompt" | "deny"; resource: string; tool: string }>({ action: "deny", resource: "", tool: "" })
  const [profileDraft, setProfileDraft] = useState({ name: "", approval: "suggest", tools: "" })
  // Single source of truth is the UI store: a local copy left the command
  // palette computing "next theme" from a stale value.
  const theme = useUiStore((s) => s.theme)
  const setTheme = useUiStore((s) => s.setTheme)
  const palette = useUiStore((s) => s.palette)
  const setPalette = useUiStore((s) => s.setPalette)
  const pushToast = useUiStore((s) => s.pushToast)
  // 注意：hook 必须全部在 `if (!cfg)` 早退之前，否则首次渲染（cfg 仍是 null）
  // 与数据到达后的渲染 hook 数量不一致，React 直接抛 #310 白屏。
  const [notifyOn, setNotifyOn] = useState(() => notificationsEnabled())
  const [notifyPerm, setNotifyPerm] = useState(() => notificationPermission())
  const [showRegistry, setShowRegistry] = useState(false)
  const dirtyRef = useRef({ cfg: false, mcp: false, md: false, memory: false, advanced: false })
  // Baseline of the Project context block as loaded from the backend. The new
  // context keys are only submitted when their value differs from the baseline:
  // the Knowledge tab saves some of them immediately through its own endpoint,
  // so blindly re-sending a stale form would revert that change.
  const contextBaselineRef = useRef<FullConfig["context"] | null>(null)
  // Loaded env-var name, so an untouched field is never re-submitted (a legacy
  // config value that fails today's validation must not break unrelated saves).
  const apiKeyEnvRef = useRef("")

  function handleRegistryPick(item: McpRegistryItem) {
    if (busy === "save") return
    // stdio 条目给 command/args；remote 条目只有 URL，走 streamable_http。
    // 早期实现只接受 stdio，导致 registry 首页 20 条 remote 全部无法添加。
    const remoteUrl = item.remote_url?.trim() || ""
    if (!item.suggested_command && !remoteUrl) return
    dirtyRef.current.mcp = true
    // registry 名多是反向域名（ac.inference.sh/mcp），只取最后一段会让一批
    // server 全叫 "mcp"。整串清洗后保留，并在 set 回调里对已有行去重。
    const base = (item.name || item.title || "mcp").replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "")
    // Seed required env vars as empty placeholders so the card shows what the
    // server needs; an empty value is dropped at save time instead of starting
    // a server that is guaranteed to fail.
    const env: Record<string, string> = {}
    for (const field of item.required_env ?? []) {
      if (field.required) env[field.name] = ""
    }
    const isRemote = !item.suggested_command
    setMcpRows((rows) => {
      const taken = new Set(rows.map((r) => r.name))
      let name = base || "mcp"
      for (let n = 2; taken.has(name); n += 1) name = `${base}-${n}`
      return [
        ...rows,
        {
          name,
          command: isRemote ? "" : item.suggested_command!,
          args: isRemote ? [] : [...item.suggested_args],
          env,
          readonly_tools: [],
          transport: isRemote ? "streamable_http" : "stdio",
          http: isRemote ? { url: remoteUrl, headers: {}, timeout_sec: 30 } : null,
        },
      ]
    })
    setShowRegistry(false)
  }

  function patchAgent(patch: Partial<FullConfig["agent"]>) {
    dirtyRef.current.cfg = true
    setCfg((c) => (c ? { ...c, agent: { ...c.agent, ...patch } } : c))
  }

  function patchMcp(i: number, patch: Partial<McpServer>) {
    dirtyRef.current.mcp = true
    setMcpRows((rows) => rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))
  }

  function patchContext(patch: Partial<FullConfig["context"]>) {
    dirtyRef.current.cfg = true
    setCfg((c) => (c ? { ...c, context: { ...c.context, ...patch } } : c))
  }

  /** True when a newly exposed context key differs from what was loaded. */
  function contextChanged(key: keyof FullConfig["context"]): boolean {
    const base = contextBaselineRef.current
    return base !== null && cfg?.context[key] !== base[key]
  }

  function patchMemory(patch: Partial<NonNullable<FullConfig["memory"]>>) {
    dirtyRef.current.memory = true
    setCfg((c) =>
      c
        ? {
            ...c,
            memory: {
              ...(c.memory ?? { enabled: true, top_k: 6, capacity: 500, embedding_model: "", auto_extract: true }),
              ...patch,
            },
          }
        : c,
    )
  }

  function patchReplay(patch: Partial<NonNullable<FullConfig["replay"]>>) {
    dirtyRef.current.advanced = true
    setCfg((c) => (c ? { ...c, replay: { ...(c.replay ?? { enabled: true }), ...patch } } : c))
  }

  function patchUpdater(patch: Partial<NonNullable<FullConfig["updater"]>>) {
    dirtyRef.current.advanced = true
    setCfg((c) => (c ? { ...c, updater: { ...(c.updater ?? { mode: "notify" }), ...patch } } : c))
  }

  function patchHook(i: number, patch: Partial<HookEntry>) {
    dirtyRef.current.cfg = true
    setHookRows((rows) => rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))
  }

  function headersToText(headers: Record<string, string> | undefined): string {
    return Object.entries(headers ?? {}).map(([k, v]) => `${k}: ${v}`).join("\n")
  }

  function textToHeaders(raw: string): Record<string, string> {
    const out: Record<string, string> = {}
    for (const line of raw.split("\n")) {
      const idx = line.indexOf(":")
      if (idx <= 0) continue
      const key = line.slice(0, idx).trim()
      if (key) out[key] = line.slice(idx + 1).trim()
    }
    return out
  }

  async function refreshProfiles() {
    try {
      const data = await listModelProfiles()
      setProfiles(data.profiles)
    } catch (e) {
      setNotice(t("model.loadFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    }
  }

  useEffect(() => {
    refreshProfiles()
    fetchConfig()
      .then((data) => {
        if (!dirtyRef.current.cfg) setCfg(data)
        if (!dirtyRef.current.mcp) {
          setMcpRows(data.mcp_servers.map((s) => ({ ...s, args: [...s.args], readonly_tools: [...s.readonly_tools] })))
          setHeaderText(Object.fromEntries(data.mcp_servers.map((s) => [s.name, headersToText(s.http?.headers)])))
        }
        // Baseline for the Project block only taken on the initial load; a
        // re-fetch must not erase unsaved edits.
        if (contextBaselineRef.current === null) contextBaselineRef.current = { ...data.context }
        setProtectedText(data.agent.protected_paths?.join("\n") ?? "")
        setHookRows((data.hooks || []).map((h) => ({ ...h, args: [...(h.args || [])] })))
        setAgentsMdName(data.context.agents_md ?? "")
        apiKeyEnvRef.current = data.provider.api_key_env ?? ""
      })
      .catch((e) => setNotice(t("settings.loadFailed", lang, { msg: e instanceof Error ? e.message : String(e) })))
    fetchAgentsMd()
      .then((d) => {
        if (!dirtyRef.current.md) setAgentsMd(d)
      })
      .catch((e) => setNotice(t("settings.memoryLoadFailed", lang, { msg: e instanceof Error ? e.message : String(e) })))
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
    // 挂载时初始化一次：open 时组件才挂载，语言切换不应重拉配置
    // （会覆盖用户尚未保存的编辑）。refreshProfiles 是组件内 useCallback，
    // 加入依赖会随重渲染反复触发网络请求。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onClose])

  async function handleSave() {
    if (!cfg) return
    const trimmedMdName = agentsMdName.trim()
    // A bare filename only: same guard as the backend, so an obvious typo is
    // rejected in the form instead of being silently rewritten.
    const agentsMdFilename = trimmedMdName && !/[\\/]/.test(trimmedMdName) ? trimmedMdName : null
    const agentsMdNameChanged = agentsMdFilename !== null && agentsMdFilename !== cfg.context.agents_md
    setBusy("save")
    setNotice("")
    try {
      const payload: SettingsPayload = {
        provider: {
          name: cfg.provider.name,
          base_url: cfg.provider.base_url,
          model: cfg.provider.model,
          // Empty means "keep the stored key": the backend falls back to
          // cfg.provider.api_key / the env var when this is blank.
          api_key: apiKeyInput.trim(),
          ...(resilienceDirty.current.api_keys ? { api_keys: providerApiKeysText.split("\n").map((s) => s.trim()).filter(Boolean) } : {}),
          ...(resilienceDirty.current.fallback_models ? { fallback_models: providerFallbackText.split("\n").map((s) => s.trim()).filter(Boolean) } : {}),
          ...(resilienceDirty.current.small_model ? { small_model: providerSmallModel.trim() } : {}),
          // 环境变量名是可编辑字段；只有用户确实改过才提交，避免把历史配置
          // 里不合法的名字在无关保存时再次送上校验。
          ...(cfg.provider.api_key_env !== undefined && cfg.provider.api_key_env !== apiKeyEnvRef.current
            ? { api_key_env: cfg.provider.api_key_env }
            : {}),
        },
        agent: {
          approval: cfg.agent.approval,
          workdir_only: cfg.agent.workdir_only,
          sandbox_mode: cfg.agent.sandbox_mode,
          protected_paths: protectedText.split("\n").map((s) => s.trim()).filter(Boolean),
          shell_timeout_sec: cfg.agent.shell_timeout_sec,
          max_tool_rounds: cfg.agent.max_tool_rounds,
          max_repeat_calls: cfg.agent.max_repeat_calls,
          max_turn_tokens: cfg.agent.max_turn_tokens,
          max_output_chars: cfg.agent.max_output_chars,
          show_thinking: cfg.agent.show_thinking,
          show_tools: cfg.agent.show_tools,
          show_plan: cfg.agent.show_plan,
          show_context: cfg.agent.show_context,
          show_keywords: cfg.agent.show_keywords,
          show_notices: cfg.agent.show_notices,
          effort: cfg.agent.effort,
          system_prompt: cfg.agent.system_prompt ?? "",
          // 只提交后端已回显的字段：老版本后端不认这些键时不要凭空造值。
          ...(cfg.agent.auto_review !== undefined ? { auto_review: cfg.agent.auto_review } : {}),
          ...(cfg.agent.network_access !== undefined ? { network_access: cfg.agent.network_access } : {}),
          ...(cfg.agent.format_on_write !== undefined ? { format_on_write: cfg.agent.format_on_write } : {}),
        },
        // Project tab edits land here; without this field every context change
        // was silently discarded on save. Optional keys are omitted when the
        // backend did not send them (older builds), so we never invent defaults.
        context: {
          compact_threshold: cfg.context.compact_threshold,
          max_context_tokens: cfg.context.max_context_tokens,
          keep_recent_messages: cfg.context.keep_recent_messages,
          history_budget_chars: cfg.context.history_budget_chars,
          max_fragment_chars: cfg.context.max_fragment_chars,
          repo_map_enabled: cfg.context.repo_map_enabled,
          ...(cfg.context.repo_map_max_chars !== undefined ? { repo_map_max_chars: cfg.context.repo_map_max_chars } : {}),
          // 知识库开关/条数在「知识库」页也会即时保存；只有本页确实改过才提交，
          // 否则回到本页保存会用挂载时的旧值覆盖另一处刚保存的设置。
          ...(cfg.context.knowledge_enabled !== undefined && contextChanged("knowledge_enabled") ? { knowledge_enabled: cfg.context.knowledge_enabled } : {}),
          ...(cfg.context.knowledge_top_k !== undefined && contextChanged("knowledge_top_k") ? { knowledge_top_k: cfg.context.knowledge_top_k } : {}),
          ...(cfg.context.knowledge_max_chars !== undefined && contextChanged("knowledge_max_chars") ? { knowledge_max_chars: cfg.context.knowledge_max_chars } : {}),
          ...(agentsMdNameChanged && agentsMdFilename !== null ? { agents_md: agentsMdFilename } : {}),
        },
        // 只在记忆配置区块有改动时提交，避免覆盖 toml 手改值
        ...(dirtyRef.current.memory
          ? {
              memory: {
                enabled: cfg.memory?.enabled ?? true,
                top_k: cfg.memory?.top_k ?? 6,
                capacity: cfg.memory?.capacity ?? 500,
                // Empty is rejected by the backend, and a toggle-only save must
                // not fail because the stored model name was blank.
                ...(cfg.memory?.embedding_model ? { embedding_model: cfg.memory.embedding_model } : {}),
                auto_extract: cfg.memory?.auto_extract ?? true,
                ...(cfg.memory?.embedding_enabled !== undefined ? { embedding_enabled: cfg.memory.embedding_enabled } : {}),
                ...(cfg.memory?.embedding_base_url !== undefined ? { embedding_base_url: cfg.memory.embedding_base_url } : {}),
                // Blank input = keep the stored key; only send a value the user typed.
                ...(embeddingKeyInput.trim() ? { embedding_api_key: embeddingKeyInput.trim() } : {}),
                ...(cfg.memory?.rerank_model !== undefined ? { rerank_model: cfg.memory.rerank_model } : {}),
              },
            }
          : {}),
        ...(dirtyRef.current.advanced && cfg.replay ? { replay: { enabled: cfg.replay.enabled } } : {}),
        ...(dirtyRef.current.advanced && cfg.updater ? { updater: { mode: cfg.updater.mode } } : {}),
        mcp_servers: mcpRows.map((row) => ({
          ...row,
          env_replace: true,
          env: Object.fromEntries(
            Object.entries(row.env ?? {}).filter(([, value]) => String(value).trim() !== ""),
          ),
        })),
        hooks: hookRows,
      }
      const data = await saveSettings(payload)
      setMcpRows(data.config.mcp_servers.map((s) => ({ ...s, args: [...s.args], readonly_tools: [...s.readonly_tools] })))
      setHeaderText(Object.fromEntries(data.config.mcp_servers.map((s) => [s.name, headersToText(s.http?.headers)])))
      // Re-base the form on what the backend actually stored, so masked echoes
      // and server-side normalisation are reflected without a reload.
      setCfg(data.config)
      setProtectedText(data.config.agent.protected_paths?.join("\n") ?? "")
      setHookRows((data.config.hooks || []).map((h) => ({ ...h, args: [...(h.args || [])] })))
      // Saved settings are authoritative: drop per-row raw buffers so a
      // re-saved row cannot resurrect stale text.
      setArgsText({})
      setEmbeddingKeyInput("")
      resilienceDirty.current = { api_keys: false, fallback_models: false, small_model: false }
      setProviderApiKeysText("")
      setProviderFallbackText("")
      setProviderSmallModel("")
      setAgentsMdName(data.config.context.agents_md ?? "")
      apiKeyEnvRef.current = data.config.provider.api_key_env ?? ""
      contextBaselineRef.current = { ...data.config.context }
      // Renaming the memory file changes which file the editor shows; refetch
      // instead of leaving the previous filename's content on screen.
      if (agentsMdFilename !== null && agentsMdFilename !== cfg.context.agents_md) {
        fetchAgentsMd()
          .then((d) => setAgentsMd(d))
          .catch(() => setAgentsMd(null))
      }
      // Surface MCP boot failures instead of a blanket "saved": a server that
      // could not start is a saved-but-broken state the user must see.
      if (data.mcp_errors && data.mcp_errors.length > 0) {
        setNotice(t("settings.savedWithMcpErrors", lang, { errors: data.mcp_errors.join("; ") }))
      } else {
        setNotice(t("settings.saved", lang))
      }
      dirtyRef.current.cfg = false
      dirtyRef.current.mcp = false
      dirtyRef.current.memory = false
      dirtyRef.current.advanced = false
      onSaved(data.status)
      useSessionStore.getState().refreshSessions()
    } catch (e) {
      setNotice(t("settings.saveFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setBusy(null)
    }
  }

  async function handleTest() {
    setBusy("test")
    setProbe(null)
    setNotice("")
    try {
      const res = await testConnection({
        base_url: cfg!.provider.base_url,
        model: cfg!.provider.model,
        api_key: apiKeyInput.trim(),
      })
      setProbe(res)
    } catch (e) {
      setNotice(t("settings.testFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setBusy(null)
    }
  }

  async function handleSaveProfile() {
    setBusy("profile")
    setNotice("")
    try {
      const res = await saveModelProfile({
        id: profileForm.id || undefined,
        name: profileForm.name,
        provider: profileForm.provider,
        base_url: profileForm.base_url,
        model: profileForm.model,
        api_key: profileForm.api_key || undefined,
        api_key_env: profileForm.api_key_env.trim(),
        // 留空 = 保持该档案已有韧性（掩码回显不参与保存）
        ...(profileForm.api_keysText.trim() ? { api_keys: profileForm.api_keysText.split("\n").map((s) => s.trim()).filter(Boolean) } : {}),
        ...(profileForm.fallbackModelsText.trim() ? { fallback_models: profileForm.fallbackModelsText.split("\n").map((s) => s.trim()).filter(Boolean) } : {}),
        ...(profileForm.small_model.trim() ? { small_model: profileForm.small_model.trim() } : {}),
      })
      await refreshProfiles()
      const act = await activateModelProfile(res.id)
      onSaved(act.status)
      setShowProfileForm(false)
      setProfileForm({ id: "", name: "", provider: "openai_compat", base_url: "", model: "", api_key: "", api_key_env: "", api_keysText: "", fallbackModelsText: "", small_model: "" })
      setNotice(t("model.saved", lang))
    } catch (e) {
      setNotice(t("settings.saveFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setBusy(null)
    }
  }

  async function handleActivate(p: ModelProfile) {
    setBusy("profile")
    try {
      const act = await activateModelProfile(p.id)
      onSaved(act.status)
      await refreshProfiles()
      setNotice(t("model.switched", lang, { name: p.name }))
    } catch (e) {
      setNotice(t("settings.switchFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setBusy(null)
    }
  }

  async function refreshGovernance() {
    const data = await fetchConfig()
    setCfg((c) => (c ? { ...c, permission_rules: data.permission_rules, approval_profiles: data.approval_profiles, active_approval_profile_id: data.active_approval_profile_id } : c))
  }

  async function addRule() {
    const next = [...(cfg?.permission_rules ?? []), { action: ruleDraft.action, resource: ruleDraft.resource.trim(), tool: ruleDraft.tool.trim() }]
    setRuleBusy(true)
    try {
      await savePermissionRules(next)
      await refreshGovernance()
      setRuleDraft({ action: "deny", resource: "", tool: "" })
      setNotice(t("advanced.ruleSaved", lang))
    } catch (e) {
      setNotice(t("settings.saveFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setRuleBusy(false)
    }
  }

  async function removeRule(index: number) {
    setRuleBusy(true)
    try {
      await deletePermissionRule(index)
      await refreshGovernance()
    } catch (e) {
      setNotice(t("settings.saveFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setRuleBusy(false)
    }
  }

  async function addProfile() {
    setRuleBusy(true)
    try {
      await saveApprovalProfile({
        name: profileDraft.name.trim(),
        approval: profileDraft.approval,
        tools: profileDraft.tools.split(/\s+/).filter(Boolean),
      })
      await refreshGovernance()
      setProfileDraft({ name: "", approval: "suggest", tools: "" })
      setNotice(t("advanced.profileSaved", lang))
    } catch (e) {
      setNotice(t("settings.saveFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setRuleBusy(false)
    }
  }

  async function removeProfile(id: string) {
    setRuleBusy(true)
    try {
      await deleteApprovalProfile(id)
      await refreshGovernance()
    } catch (e) {
      setNotice(t("settings.saveFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setRuleBusy(false)
    }
  }

  async function activateProfile(id: string) {
    setRuleBusy(true)
    try {
      const act = await activateApprovalProfile(id)
      onSaved(act.status)
      await refreshGovernance()
      setNotice(t("advanced.profileActivated", lang))
    } catch (e) {
      setNotice(t("settings.switchFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setRuleBusy(false)
    }
  }

  async function handleDeleteProfile(id: string) {
    setBusy("profile")
    try {
      await deleteModelProfile(id)
      await refreshProfiles()
    } catch (e) {
      setNotice(t("settings.deleteFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setBusy(null)
    }
  }

  async function handleSaveMd() {
    if (!agentsMd) return
    setBusy("save")
    try {
      const res = await saveAgentsMd(agentsMd.content)
      setAgentsMd((a) => (a ? { ...a, chars: res.chars, max_fragment_chars: res.max_fragment_chars } : a))
      dirtyRef.current.md = false
      setNotice(t("project.memorySaved", lang))
    } catch (e) {
      setNotice(t("settings.saveFailed", lang, { msg: e instanceof Error ? e.message : String(e) }))
    } finally {
      setBusy(null)
    }
  }

  if (!cfg) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-[2px]" onClick={onClose}>
        <div className="flex h-[min(92vh,680px)] w-[min(96vw,920px)] items-center justify-center rounded-2xl border border-line bg-canvas shadow-[var(--shadow)]">
          <Spinner size={20} className="text-accent" />
        </div>
      </div>
    )
  }

  const displayKeys: { key: keyof FullConfig["agent"] & `show_${string}`; label: string }[] = [
    { key: "show_thinking", label: t("display.thinking", lang) },
    { key: "show_tools", label: t("display.tools", lang) },
    { key: "show_plan", label: t("display.plan", lang) },
    { key: "show_context", label: t("display.context", lang) },
    { key: "show_keywords", label: t("display.keywords", lang) },
    { key: "show_notices", label: t("display.notices", lang) },
  ]

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-[2px]" onClick={onClose}>
      <aside
        className="anim-fade flex h-[min(92vh,680px)] w-[min(96vw,920px)] flex-col overflow-hidden rounded-2xl border border-line bg-canvas shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={t("settings.title", lang)}
      >
        <div className="flex h-full min-h-0">
          {/* 左导航（桌面） */}
          <nav className="hidden w-[200px] shrink-0 flex-col border-r border-line bg-sidebar sm:flex">
            <div className="flex items-center gap-2 px-4 py-3.5">
              <IconSettings size={16} className="text-accent" />
              <span className="text-[14px] font-semibold text-ink">{t("settings.title", lang)}</span>
            </div>
            <div className="flex flex-1 flex-col gap-1 overflow-y-auto px-2 py-1">
              {TABS.map((tb) => (
                <button
                  key={tb.key}
                  type="button"
                  onClick={() => setTab(tb.key)}
                  aria-current={tab === tb.key ? "page" : undefined}
                  className={cls(
                    "relative flex items-center gap-2.5 rounded-[9px] px-3 py-2 text-left text-[12.5px] font-medium transition-colors duration-150",
                    tab === tb.key ? "bg-selected text-ink" : "text-ink2 hover:bg-hover hover:text-ink",
                  )}
                >
                  {/* 激活态用左侧细条标记，与侧栏会话项同一套语言；
                      右侧孤立小圆点看起来像未读提示，容易误读。 */}
                  {tab === tb.key && (
                    <span aria-hidden className="absolute top-1/2 left-0 h-4 w-[2px] -translate-y-1/2 rounded-r bg-accent" />
                  )}
                  <span className={cls("shrink-0", tab === tb.key ? "text-accent" : "text-ink3")}>{tb.icon}</span>
                  {t(tb.labelKey, lang)}
                </button>
              ))}
            </div>
            <div className="px-3 pb-3 pt-1">
              <div className="rounded-lg border border-line bg-panel2 px-3 py-2">
                <p className="text-[10px] font-bold tracking-wide text-ink3">SPARK</p>
                <p className="mt-0.5 text-[11px] leading-snug text-ink2">{t("settings.brandDesc", lang)}</p>
              </div>
            </div>
          </nav>

          {/* 右栏 */}
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <div className="flex items-center gap-2 border-b border-line px-4 py-3">
              <span className="text-sm font-semibold text-ink">{t("settings.title", lang)}</span>
              <IconButton title={t("common.close", lang)} onClick={onClose} className="ml-auto">
                <IconX size={15} />
              </IconButton>
            </div>

            {/* 移动端：横向可滚动 tab。左导航在 <sm 时隐藏，没有这一条就只剩首个 tab 可达 */}
            <div className="flex gap-1 overflow-x-auto border-b border-line px-2 py-1.5 sm:hidden">
              {TABS.map((tb) => (
                <button
                  key={tb.key}
                  type="button"
                  onClick={() => setTab(tb.key)}
                  aria-current={tab === tb.key ? "page" : undefined}
                  className={cls(
                    "flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] font-medium transition-colors",
                    tab === tb.key ? "bg-selected text-ink" : "text-ink2 hover:bg-hover",
                  )}
                >
                  <span className={tab === tb.key ? "text-accent" : "text-ink3"}>{tb.icon}</span>
                  {t(tb.labelKey, lang)}
                </button>
              ))}
            </div>

        {/* 内容 */}
        <div className="flex-1 overflow-y-auto p-4">
          {notice && (
            <div className="mb-3 flex items-start justify-between gap-2 rounded-lg border border-line bg-panel2 px-3 py-2 text-xs text-ink2">
              <span className="break-words">{notice}</span>
              <button type="button" onClick={() => setNotice("")} className="shrink-0 text-ink3 hover:text-ink">
                <IconX size={12} />
              </button>
            </div>
          )}

          {tab === "model" && (
            <div className="flex flex-col gap-4">
              <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-ink">{t("settings.currentModel", lang)}</h3>
                  <Button size="sm" variant="outline" onClick={handleTest} disabled={busy === "test"}>
                    {busy === "test" ? <Spinner size={11} /> : t("model.testConnection", lang)}
                  </Button>
                </div>
                <Field label={t("model.provider", lang)}>
                  <Select value={cfg.provider.name} onChange={(e) => { dirtyRef.current.cfg = true; setCfg((c) => (c ? { ...c, provider: { ...c.provider, name: e.target.value } } : c)) }}>
                    <option value="openai_compat">openai_compat</option>
                    <option value="ollama">ollama</option>
                  </Select>
                </Field>
                <Field label={t("settings.presets", lang)}>
                  <Select
                    value=""
                    onChange={(e) => {
                      const p = MODEL_PRESETS.find((x) => x.labelKey === e.target.value)
                      if (!p) return
                      dirtyRef.current.cfg = true
                      setCfg((c) => (c ? { ...c, provider: { ...c.provider, name: p.provider, base_url: p.base_url, model: p.model } } : c))
                      setNotice(t("settings.presetFilled", lang, { name: t(p.labelKey, lang) }))
                    }}
                  >
                    <option value="">{t("settings.selectPreset", lang)}</option>
                    {MODEL_PRESETS.map((p) => (
                      <option key={p.labelKey} value={p.labelKey}>{t(p.labelKey, lang)}</option>
                    ))}
                  </Select>
                </Field>
                <Field label={t("model.baseUrl", lang)}>
                  <TextInput value={cfg.provider.base_url} onChange={(e) => { dirtyRef.current.cfg = true; setCfg((c) => (c ? { ...c, provider: { ...c.provider, base_url: e.target.value } } : c)) }} placeholder={t("model.baseUrlPh", lang)} />
                </Field>
                <Field label={t("model.model", lang)}>
                  <TextInput value={cfg.provider.model} onChange={(e) => { dirtyRef.current.cfg = true; setCfg((c) => (c ? { ...c, provider: { ...c.provider, model: e.target.value } } : c)) }} placeholder={t("model.modelPh", lang)} />
                </Field>
                <Field label={t("model.apiKey", lang)} hint={t("model.apiKeyHint", lang)}>
                  <TextInput
                    type="password"
                    autoComplete="off"
                    value={apiKeyInput}
                    onChange={(e) => setApiKeyInput(e.target.value)}
                    placeholder={cfg.provider.has_api_key ? t("model.apiKeyKeep", lang, { masked: cfg.provider.api_key_masked }) : t("model.apiKeyPhNew", lang)}
                  />
                </Field>
                {cfg.provider.api_key_env !== undefined && (
                  <Field label={t("model.apiKeyEnv", lang)} hint={t("model.apiKeyEnvEditableHint", lang)}>
                    <TextInput
                      value={cfg.provider.api_key_env}
                      onChange={(e) => {
                        dirtyRef.current.cfg = true
                        setCfg((c) => (c ? { ...c, provider: { ...c.provider, api_key_env: e.target.value } } : c))
                      }}
                      placeholder="SPARK_API_KEY"
                      className="font-mono"
                    />
                  </Field>
                )}
                <Field label={t("settings.effort", lang)} hint={t("settings.effortDesc", lang)}>
                  <Select value={cfg.agent.effort ?? "medium"} onChange={(e) => patchAgent({ effort: e.target.value as "low" | "medium" | "high" })}>
                    <option value="low">{t("settings.effortLow", lang)}</option>
                    <option value="medium">{t("settings.effortMedium", lang)}</option>
                    <option value="high">{t("settings.effortHigh", lang)}</option>
                  </Select>
                </Field>
                {probe && (
                  <div className={cls("rounded-lg border px-3 py-2 text-xs", probe.ok ? "border-ok/40 bg-ok/8 text-ok" : "border-err/40 bg-err/8 text-err")}>
                    {probe.ok ? t("settings.testOk", lang, { latency: probe.latency_ms ?? 0, model: probe.model ?? "" }) : t("settings.testFail", lang, { error: probe.error ?? "" })}
                  </div>
                )}
              </section>

              <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("model.resilience", lang)}</h3>
                <p className="text-xs leading-relaxed text-ink3">{t("model.resilienceDesc", lang)}</p>
                <Field label={t("model.apiKeys", lang)} hint={cfg.provider.api_keys_count > 0 ? t("model.apiKeysHint", lang, { n: cfg.provider.api_keys_count }) : undefined}>
                  <TextArea value={providerApiKeysText} onChange={(e) => { dirtyRef.current.cfg = true; resilienceDirty.current.api_keys = true; setProviderApiKeysText(e.target.value) }} placeholder={t("model.apiKeysPh", lang)} rows={2} />
                  <Button size="sm" variant="ghost" onClick={() => { dirtyRef.current.cfg = true; resilienceDirty.current.api_keys = true; setProviderApiKeysText("") }}>{t("model.clearValue", lang)}</Button>
                </Field>
                <Field label={t("model.fallbackModels", lang)}>
                  <TextArea value={providerFallbackText} onChange={(e) => { dirtyRef.current.cfg = true; resilienceDirty.current.fallback_models = true; setProviderFallbackText(e.target.value) }} placeholder={t("model.fallbackModelsPh", lang)} rows={2} />
                  <Button size="sm" variant="ghost" onClick={() => { dirtyRef.current.cfg = true; resilienceDirty.current.fallback_models = true; setProviderFallbackText("") }}>{t("model.clearValue", lang)}</Button>
                </Field>
                <Field label={t("model.smallModel", lang)}>
                  <TextInput value={providerSmallModel} onChange={(e) => { dirtyRef.current.cfg = true; resilienceDirty.current.small_model = true; setProviderSmallModel(e.target.value) }} placeholder={t("model.smallModelPh", lang)} />
                  <Button size="sm" variant="ghost" onClick={() => { dirtyRef.current.cfg = true; resilienceDirty.current.small_model = true; setProviderSmallModel("") }}>{t("model.clearValue", lang)}</Button>
                </Field>
              </section>

              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-ink">{t("model.profiles", lang)}</h3>
                  <Button size="sm" variant="soft" onClick={() => setShowProfileForm((v) => !v)}>
                    <IconPlus size={12} /> {t("common.add", lang)}
                  </Button>
                </div>

                {showProfileForm && (
                  <div className="flex flex-col gap-2.5 rounded-lg border border-line bg-panel2 p-3">
                    <Field label={t("model.displayName", lang)}>
                      <TextInput value={profileForm.name} onChange={(e) => setProfileForm((p) => ({ ...p, name: e.target.value }))} placeholder={t("model.displayNamePh", lang)} />
                    </Field>
                    <Field label={t("model.provider", lang)}>
                      <Select value={profileForm.provider} onChange={(e) => setProfileForm((p) => ({ ...p, provider: e.target.value }))}>
                        <option value="openai_compat">openai_compat</option>
                        <option value="ollama">ollama</option>
                      </Select>
                    </Field>
                    <Field label={t("model.baseUrl", lang)}>
                      <TextInput value={profileForm.base_url} onChange={(e) => setProfileForm((p) => ({ ...p, base_url: e.target.value }))} placeholder={t("model.baseUrlPh", lang)} />
                    </Field>
                    <Field label={t("model.model", lang)}>
                      <TextInput value={profileForm.model} onChange={(e) => setProfileForm((p) => ({ ...p, model: e.target.value }))} placeholder={t("model.modelPh", lang)} />
                    </Field>
                    <Field label={t("model.apiKey", lang)}>
                      <TextInput value={profileForm.api_key} onChange={(e) => setProfileForm((p) => ({ ...p, api_key: e.target.value }))} placeholder={t("model.apiKeyPhNew", lang)} type="password" />
                    </Field>
                    <Field label={t("model.profileEnv", lang)} hint={t("model.profileEnvHint", lang)}>
                      <TextInput value={profileForm.api_key_env} onChange={(e) => setProfileForm((p) => ({ ...p, api_key_env: e.target.value }))} placeholder="SPARK_API_KEY" />
                    </Field>
                    <Field label={t("model.apiKeys", lang)} hint={t("model.apiKeysProfileHint", lang)}>
                      <TextArea value={profileForm.api_keysText} onChange={(e) => setProfileForm((p) => ({ ...p, api_keysText: e.target.value }))} placeholder={t("model.apiKeysPh", lang)} rows={2} />
                    </Field>
                    <Field label={t("model.fallbackModels", lang)}>
                      <TextArea value={profileForm.fallbackModelsText} onChange={(e) => setProfileForm((p) => ({ ...p, fallbackModelsText: e.target.value }))} placeholder={t("model.fallbackModelsPh", lang)} rows={2} />
                    </Field>
                    <Field label={t("model.smallModel", lang)}>
                      <TextInput value={profileForm.small_model} onChange={(e) => setProfileForm((p) => ({ ...p, small_model: e.target.value }))} placeholder={t("model.smallModelPh", lang)} />
                    </Field>
                    <div className="flex gap-2">
                      <Button variant="primary" full onClick={handleSaveProfile} disabled={busy === "profile" || !profileForm.name || !profileForm.base_url || !profileForm.model}>
                        {busy === "profile" ? <Spinner size={12} /> : t("settings.saveAndActivate", lang)}
                      </Button>
                      <Button variant="ghost" onClick={() => setShowProfileForm(false)}>{t("common.cancel", lang)}</Button>
                    </div>
                  </div>
                )}

                <div className="flex max-h-64 flex-col gap-1.5 overflow-y-auto">
                  {profiles.length === 0 && <p className="py-2 text-xs text-ink3">{t("model.noProfiles", lang)}</p>}
                  {profiles.map((p) => (
                    <div key={p.id} className={cls("flex items-center gap-2.5 rounded-lg border px-3 py-2", p.active ? "border-accent/50 bg-accent-soft/50" : "border-line bg-panel2")}>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-[13px] font-medium text-ink">
                          {p.name}
                          {p.active && <Badge tone="accent" className="ml-1.5">{t("model.active", lang)}</Badge>}
                        </p>
                        <p className="truncate font-mono text-[10.5px] text-ink3">{p.model} · {p.base_url}</p>
                      </div>
                      {!p.active && (
                        <Button size="sm" variant="soft" onClick={() => handleActivate(p)} disabled={busy === "profile"}>
                          {t("model.use", lang)}
                        </Button>
                      )}
                      <IconButton title={t("settings.deleteProfile", lang)} onClick={() => handleDeleteProfile(p.id)} className="h-7 w-7 text-ink3 hover:text-err">
                        <IconTrash size={12} />
                      </IconButton>
                    </div>
                  ))}
                </div>
              </section>
            </div>
          )}

          {tab === "agent" && (
            <div className="flex flex-col gap-4">
              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("agent.approval", lang)}</h3>
                {(["full-auto", "approve-plan", "auto-edit", "suggest", "plan-only", "manual"] as const).map((m) => (
                  <label key={m} className="flex cursor-pointer items-center gap-2.5">
                    <input type="radio" name="approval" className="accent-accent" checked={cfg.agent.approval === m} onChange={() => patchAgent({ approval: m })} />
                    <span className="text-[13px] text-ink2">
                      {m === "full-auto" ? t("agent.fullAuto", lang) :
                       m === "approve-plan" ? t("agent.approvePlan", lang) :
                       m === "auto-edit" ? t("agent.autoEdit", lang) :
                       m === "suggest" ? t("agent.suggest", lang) :
                       m === "plan-only" ? t("agent.planOnly", lang) :
                       t("agent.manual", lang)}
                    </span>
                  </label>
                ))}
                <label className="flex items-center justify-between gap-2 rounded-lg bg-panel2 px-3 py-2.5">
                  <span className="flex flex-col">
                    <span className="text-[13px] text-ink2">{t("agent.workdirOnly", lang)}</span>
                    <span className="text-[10.5px] text-ink3">{t("agent.workdirOnlyHint", lang)}</span>
                  </span>
                  {/* The legacy workdir_only flag was never read by the sandbox,
                      so this switch wrote a value nothing obeyed. It now drives
                      sandbox_mode directly — the one field enforcement reads. */}
                  <Toggle
                    checked={cfg.agent.sandbox_mode === "workspace" || cfg.agent.sandbox_mode === "sandbox-only"}
                    onChange={(v) =>
                      patchAgent({
                        workdir_only: v,
                        sandbox_mode: v ? "workspace" : "full-access",
                        ...(v ? {} : { approval: cfg.agent.approval === "full-auto" ? "full-auto" : cfg.agent.approval }),
                      })
                    }
                  />
                </label>
              </section>

              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("agent.execParams", lang)}</h3>
                <Field label={t("agent.maxToolRounds", lang)}>
                  <TextInput type="number" value={cfg.agent.max_tool_rounds} onChange={(e) => patchAgent({ max_tool_rounds: Number(e.target.value) || 0 })} />
                </Field>
                <Field label={t("agent.shellTimeout", lang)}>
                  <TextInput type="number" value={cfg.agent.shell_timeout_sec} onChange={(e) => patchAgent({ shell_timeout_sec: Number(e.target.value) || 0 })} />
                </Field>
                <Field label={t("agent.maxOutput", lang)}>
                  <TextInput type="number" value={cfg.agent.max_output_chars} onChange={(e) => patchAgent({ max_output_chars: Number(e.target.value) || 0 })} />
                </Field>
              </section>

              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("agent.guardrails", lang)}</h3>
                <p className="text-xs leading-relaxed text-ink3">{t("agent.guardrailsHint", lang)}</p>
                <Field label={t("agent.repeatLimit", lang)}>
                  <TextInput type="number" value={cfg.agent.max_repeat_calls} onChange={(e) => patchAgent({ max_repeat_calls: Number(e.target.value) || 0 })} />
                </Field>
                <Field label={t("agent.tokenBudget", lang)}>
                  <TextInput type="number" value={cfg.agent.max_turn_tokens} onChange={(e) => patchAgent({ max_turn_tokens: Number(e.target.value) || 0 })} />
                </Field>
              </section>

              <section className="surface flex flex-col gap-3 p-4">
                <div>
                  <h3 className="text-[13px] font-semibold text-ink">{t("agent.safety", lang)}</h3>
                  <p className="mt-1 text-[11.5px] leading-relaxed text-ink3">{t("agent.safetyDesc", lang)}</p>
                </div>

                <label className="flex items-start justify-between gap-3">
                  <span className="flex min-w-0 flex-col">
                    <span className="text-[12.5px] text-ink">{t("agent.autoReview", lang)}</span>
                    <span className="text-[10.5px] leading-snug text-ink3">{t("agent.autoReviewHint", lang)}</span>
                  </span>
                  <Toggle checked={cfg.agent.auto_review ?? false} onChange={(v) => patchAgent({ auto_review: v })} />
                </label>

                <label className="flex items-start justify-between gap-3">
                  <span className="flex min-w-0 flex-col">
                    <span className="text-[12.5px] text-ink">{t("agent.networkAccess", lang)}</span>
                    <span className="text-[10.5px] leading-snug text-ink3">{t("agent.networkAccessHint", lang)}</span>
                  </span>
                  <Toggle checked={cfg.agent.network_access ?? true} onChange={(v) => patchAgent({ network_access: v })} />
                </label>

                <label className="flex items-start justify-between gap-3">
                  <span className="flex min-w-0 flex-col">
                    <span className="text-[12.5px] text-ink">{t("agent.formatOnWrite", lang)}</span>
                    <span className="text-[10.5px] leading-snug text-ink3">{t("agent.formatOnWriteHint", lang)}</span>
                  </span>
                  <Toggle checked={cfg.agent.format_on_write ?? false} onChange={(v) => patchAgent({ format_on_write: v })} />
                </label>
              </section>
            </div>
          )}

          {tab === "security" && (
            <div className="flex flex-col gap-4">
              <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("security.accessLevel", lang)}</h3>
                {([
                  ["sandbox-only", "security.sandboxOnly", "security.sandboxOnlyDesc"],
                  ["workspace", "security.workspace", "security.workspaceDesc"],
                  ["full-access", "security.fullAccess", "security.fullAccessDesc"],
                  ["unrestricted", "security.unrestrictedDanger", "security.unrestrictedDesc"],
                ] as const).map(([v, labelKey, descKey]) => (
                  <label key={v} className={cls("flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2.5 transition-colors", cfg.agent.sandbox_mode === v ? "border-accent/50 bg-accent-soft/40" : "border-line bg-panel2 hover:bg-hover")}>
                    <input type="radio" name="sandbox" className="mt-0.5 accent-accent" checked={cfg.agent.sandbox_mode === v} onChange={() => patchAgent({ sandbox_mode: v })} />
                    <span className="min-w-0">
                      <span className="block text-[13px] font-medium text-ink">{t(labelKey, lang)}</span>
                      <span className="block text-xs text-ink3">{t(descKey, lang)}</span>
                    </span>
                  </label>
                ))}
              </section>

              <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("security.protectedPaths", lang)}</h3>
                <p className="text-xs leading-relaxed text-ink3">{t("security.protectedPathsDesc", lang)}</p>
                <TextArea rows={4} value={protectedText} onChange={(e) => { dirtyRef.current.cfg = true; setProtectedText(e.target.value) }} placeholder={"/etc\n/root/.spark"} />
              </section>

              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("security.hooks", lang)}</h3>
                <p className="text-xs leading-relaxed text-ink3">{t("security.hooksDesc", lang)}</p>
                <div className="flex flex-col gap-3">
                  {hookRows.length === 0 && (
                    <p className="text-xs text-ink3">{t("security.noHooks", lang)}</p>
                  )}
                  {hookRows.map((h, i) => (
                    <div key={i} className="flex flex-col gap-2 rounded-lg border border-line bg-panel2 px-3 py-2.5">
                      <div className="flex items-center gap-2">
                        <Select
                          value={h.event}
                          onChange={(e) => { dirtyRef.current.cfg = true; setHookRows((rows) => rows.map((r, idx) => idx === i ? { ...r, event: e.target.value } : r)) }}
                          aria-label={t("security.hookEvent", lang)}
                        >
                          {HOOK_EVENTS.map((ev) => <option key={ev} value={ev}>{ev}</option>)}
                        </Select>
                        <IconButton title={t("security.deleteHook", lang)} onClick={() => { dirtyRef.current.cfg = true; setHookRows((rows) => rows.filter((_, idx) => idx !== i)) }} className="h-7 w-7 shrink-0 text-ink3 hover:text-err">
                          <IconTrash size={12} />
                        </IconButton>
                      </div>
                      <TextInput
                        value={h.command}
                        onChange={(e) => { dirtyRef.current.cfg = true; setHookRows((rows) => rows.map((r, idx) => idx === i ? { ...r, command: e.target.value } : r)) }}
                        placeholder={t("security.hookCommandPh", lang)}
                        className="font-mono"
                      />
                      <TextInput
                        value={argsText[i] ?? h.args.join(" ")}
                        onChange={(e) => {
                          const raw = e.target.value
                          // Keep the raw text while typing so grouped quotes stay
                          // intact; split only on save (splitting per keystroke
                          // made "hello world" impossible to enter).
                          dirtyRef.current.cfg = true
                          setArgsText((m) => ({ ...m, [i]: raw }))
                          setHookRows((rows) => rows.map((r, idx) => (idx === i ? { ...r, args: splitArgs(raw) } : r)))
                        }}
                        placeholder={t("security.hookArgsPh", lang)}
                        className="font-mono"
                      />
                      <div className="grid grid-cols-2 gap-2">
                        <Field label={t("security.hookName", lang)}>
                          <TextInput
                            value={h.name}
                            onChange={(e) => patchHook(i, { name: e.target.value })}
                            placeholder={t("security.hookNamePh", lang)}
                          />
                        </Field>
                        <Field label={t("security.hookTimeout", lang)}>
                          <TextInput
                            type="number"
                            min={1}
                            max={3600}
                            value={h.timeout_sec}
                            onChange={(e) => {
                              const raw = e.target.value
                              if (raw === "") return
                              patchHook(i, { timeout_sec: Math.min(3600, Math.max(1, Number(raw))) })
                            }}
                          />
                        </Field>
                      </div>
                      {/* Optional on newer backends: rows loaded without it keep
                          the key undefined and it is omitted from the payload,
                          so an older API never receives a field it drops. */}
                      <Field label={t("security.hookOnDeny", lang)}>
                        <Select
                          value={h.on_deny ?? "block"}
                          onChange={(e) => patchHook(i, { on_deny: e.target.value as "warn" | "block" })}
                        >
                          <option value="warn">{t("security.hookOnDenyWarn", lang)}</option>
                          <option value="block">{t("security.hookOnDenyBlock", lang)}</option>
                        </Select>
                      </Field>
                    </div>
                  ))}
                  <Button size="sm" variant="soft" onClick={() => { dirtyRef.current.cfg = true; setHookRows((rows) => [...rows, { event: "pre_tool", command: "", args: [], name: "", timeout_sec: 10, on_deny: "block" }]) }}>
                    <IconPlus size={12} /> {t("security.addHook", lang)}
                  </Button>
                </div>
              </section>

              <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("security.envProbe", lang)}</h3>
                <p className="text-xs leading-relaxed text-ink3">{t("security.envProbeDesc", lang)}</p>
                {!cfg.env_probe || cfg.env_probe.length === 0 ? (
                  <p className="text-xs text-ink3">{t("security.noProbeData", lang)}</p>
                ) : (
                  <div className="flex flex-col divide-y divide-line rounded-lg border border-line">
                    {cfg.env_probe.map((row) => (
                      <div key={row.name} className="flex items-center gap-3 px-3 py-2">
                        <span className="w-20 shrink-0 font-mono text-[12px] font-medium text-ink">{row.name}</span>
                        <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-ink3" title={row.path}>{row.path || t("common.notFound", lang)}</span>
                        <span className={cls("shrink-0 rounded-md px-2 py-0.5 text-[11px] font-medium", row.version === "unavailable" ? "bg-err/10 text-err" : "bg-ok/10 text-ok")}>{row.version}</span>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            </div>
          )}

          {tab === "display" && (
            <div className="flex flex-col gap-4">
              <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("display.theme", lang)}</h3>
                <div className="grid grid-cols-3 gap-2">
                  {(["light", "dark", "system"] as Theme[]).map((th) => (
                    <button
                      key={th}
                      type="button"
                      onClick={() => setTheme(th)}
                      className={cls("flex flex-col items-center gap-1.5 rounded-lg border px-2 py-3 transition-colors", theme === th ? "border-accent bg-accent-soft/40" : "border-line bg-panel2 hover:bg-hover")}
                    >
                      {th === "light" ? <IconSun size={16} /> : th === "dark" ? <IconMoon size={16} /> : <IconLayers size={16} />}
                      <span className="text-xs font-medium text-ink">{th === "light" ? t("display.light", lang) : th === "dark" ? t("display.dark", lang) : t("display.system", lang)}</span>
                      {theme === th && <IconCheck size={12} className="text-accent" />}
                    </button>
                  ))}
                </div>
              </section>

              <section className="surface flex flex-col gap-3 p-4">
                <div>
                  <h3 className="text-[13px] font-semibold text-ink">{t("display.palette", lang)}</h3>
                  <p className="mt-1 text-[11.5px] leading-relaxed text-ink3">{t("display.paletteDesc", lang)}</p>
                </div>
                <div className="grid grid-cols-5 gap-2">
                  {PALETTES.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => setPalette(p.id)}
                      title={p.label}
                      className={cls(
                        "flex flex-col items-center gap-1.5 rounded-[10px] border px-1 py-2.5 transition-[border-color,background-color] duration-150",
                        palette === p.id ? "border-accent bg-accent-soft/40" : "border-line bg-panel2 hover:bg-hover",
                      )}
                    >
                      <span
                        className="h-4 w-4 rounded-full ring-1 ring-line"
                        style={{ background: PALETTE_SWATCH[p.id] }}
                      />
                      <span className="text-[10px] font-medium text-ink2">{p.label}</span>
                    </button>
                  ))}
                </div>
              </section>

              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("display.language", lang)}</h3>
                <div className="flex gap-2">
                  {(["zh", "en"] as const).map((l) => (
                    <Button key={l} variant={lang === l ? "primary" : "outline"} size="sm" onClick={() => setLang(l)}>
                      {l === "zh" ? "中文" : "English"}
                    </Button>
                  ))}
                </div>
              </section>

              <section className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("display.toggles", lang)}</h3>
                {displayKeys.map(({ key, label }) => (
                  <label key={key} className="flex items-center justify-between rounded-lg px-2 py-2">
                    <span className="text-[13px] text-ink2">{label}</span>
                    <Toggle checked={Boolean(cfg.agent[key])} onChange={(v) => patchAgent({ [key]: v })} />
                  </label>
                ))}
              </section>

              <section className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-4">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="text-sm font-semibold text-ink">{t("display.notifications", lang)}</h3>
                    <p className="mt-0.5 text-[11.5px] leading-relaxed text-ink3">{t("display.notificationsHint", lang)}</p>
                  </div>
                  <Toggle
                    checked={notifyOn}
                    onChange={async (v) => {
                      if (v && notificationPermission() === "default") {
                        const perm = await requestNotificationPermission()
                        setNotifyPerm(perm)
                        if (perm !== "granted") {
                          pushToast("toast.notifyBlocked", "error")
                          return
                        }
                      }
                      if (v && notificationPermission() === "denied") {
                        pushToast("toast.notifyBlocked", "error")
                        return
                      }
                      setNotifyOn(v)
                      setNotificationsEnabled(v)
                    }}
                  />
                </div>
                {notifyPerm === "denied" && (
                  <p className="text-[11px] text-warn">{t("display.notifyDenied", lang)}</p>
                )}
              </section>

              <section className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-ink">{t("settings.systemPrompt", lang)}</h3>
                  <Button
                    size="sm"
                    variant="soft"
                    onClick={() => {
                      patchAgent({ system_prompt: "" })
                      setNotice(t("settings.systemPromptReset", lang))
                    }}
                  >
                    {t("settings.restoreDefault", lang)}
                  </Button>
                </div>
                <p className="text-xs leading-relaxed text-ink3">{t("settings.systemPromptDesc", lang)}</p>
                <TextArea
                  value={cfg.agent.system_prompt ?? ""}
                  onChange={(e) => patchAgent({ system_prompt: e.target.value })}
                  placeholder={t("settings.systemPromptPh", lang)}
                  rows={8}
                  className="font-mono text-xs"
                />
              </section>
            </div>
          )}

          {tab === "memory" && (
            <div className="flex flex-col gap-4">
              <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("memory.configTitle", lang)}</h3>
                <p className="text-xs leading-relaxed text-ink3">{t("memory.configDesc", lang)}</p>
                <label className="flex items-center justify-between gap-3">
                  <span className="text-[12.5px] text-ink">{t("memory.enabled", lang)}</span>
                  <Toggle
                    checked={cfg.memory?.enabled ?? true}
                    onChange={(v) => { dirtyRef.current.memory = true; setCfg((c) => (c ? { ...c, memory: { ...(c.memory ?? { enabled: true, top_k: 6, capacity: 500, embedding_model: "", auto_extract: true }), enabled: v } } : c)) }}
                  />
                </label>
                <Field label={t("memory.topK", lang)}>
                  <TextInput type="number" value={cfg.memory?.top_k ?? 6} onChange={(e) => { dirtyRef.current.memory = true; setCfg((c) => (c ? { ...c, memory: { ...(c.memory ?? { enabled: true, top_k: 6, capacity: 500, embedding_model: "", auto_extract: true }), top_k: Number(e.target.value) || 6 } } : c)) }} />
                </Field>
                <Field label={t("memory.capacity", lang)}>
                  <TextInput type="number" value={cfg.memory?.capacity ?? 500} onChange={(e) => { dirtyRef.current.memory = true; setCfg((c) => (c ? { ...c, memory: { ...(c.memory ?? { enabled: true, top_k: 6, capacity: 500, embedding_model: "", auto_extract: true }), capacity: Number(e.target.value) || 500 } } : c)) }} />
                </Field>
                <Field label={t("memory.embeddingModel", lang)}>
                  <TextInput value={cfg.memory?.embedding_model ?? ""} onChange={(e) => { dirtyRef.current.memory = true; setCfg((c) => (c ? { ...c, memory: { ...(c.memory ?? { enabled: true, top_k: 6, capacity: 500, embedding_model: "", auto_extract: true }), embedding_model: e.target.value } } : c)) }} placeholder={t("memory.embeddingModelPh", lang)} />
                </Field>
                {cfg.memory?.embedding_enabled !== undefined && (
                  <label className="flex items-start justify-between gap-3">
                    <span className="flex min-w-0 flex-col">
                      <span className="text-[12.5px] text-ink">{t("memory.embeddingEnabled", lang)}</span>
                      <span className="text-[10.5px] leading-snug text-ink3">{t("memory.embeddingEnabledHint", lang)}</span>
                    </span>
                    <Toggle
                      checked={cfg.memory.embedding_enabled}
                      onChange={(v) => patchMemory({ embedding_enabled: v })}
                    />
                  </label>
                )}
                {cfg.memory?.embedding_base_url !== undefined && (
                  <Field label={t("memory.embeddingBaseUrl", lang)}>
                    <TextInput
                      value={cfg.memory.embedding_base_url}
                      onChange={(e) => patchMemory({ embedding_base_url: e.target.value })}
                      placeholder={t("memory.embeddingBaseUrlPh", lang)}
                    />
                  </Field>
                )}
                {cfg.memory?.embedding_api_key_masked !== undefined || cfg.memory?.has_embedding_api_key !== undefined ? (
                  <Field
                    label={t("memory.embeddingApiKey", lang)}
                    hint={cfg.memory?.has_embedding_api_key ? t("memory.embeddingApiKeyKeep", lang, { masked: cfg.memory.embedding_api_key_masked ?? "" }) : undefined}
                  >
                    <TextInput
                      type="password"
                      autoComplete="off"
                      value={embeddingKeyInput}
                      onChange={(e) => {
                        dirtyRef.current.memory = true
                        setEmbeddingKeyInput(e.target.value)
                      }}
                      placeholder={t("memory.embeddingApiKeyPh", lang)}
                    />
                  </Field>
                ) : null}
                {cfg.memory?.rerank_model !== undefined && (
                  <Field label={t("memory.rerankModel", lang)}>
                    <TextInput
                      value={cfg.memory.rerank_model}
                      onChange={(e) => patchMemory({ rerank_model: e.target.value })}
                      placeholder={t("memory.rerankModelPh", lang)}
                    />
                  </Field>
                )}
                <label className="flex items-center justify-between gap-3">
                  <span className="text-[12.5px] text-ink">{t("memory.autoExtract", lang)}</span>
                  <Toggle
                    checked={cfg.memory?.auto_extract ?? true}
                    onChange={(v) => { dirtyRef.current.memory = true; setCfg((c) => (c ? { ...c, memory: { ...(c.memory ?? { enabled: true, top_k: 6, capacity: 500, embedding_model: "", auto_extract: true }), auto_extract: v } } : c)) }}
                  />
                </label>
              </section>
              <MemorySection />
            </div>
          )}

          {tab === "skills" && <SkillsSection />}

          {tab === "prompts" && <PromptsSection />}

          {tab === "plugins" && <PluginsSection />}

          {tab === "shortcuts" && <ShortcutsSection />}

          {tab === "knowledge" && <KnowledgeSection />}
          {tab === "wiki" && <WikiSection />}

          {tab === "mcp" && (
            <fieldset disabled={busy === "save"} className="flex flex-col gap-4">
              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("mcp.title", lang)}</h3>
                <p className="text-xs leading-relaxed text-ink3">{t("mcp.desc", lang)}</p>
                <div className="flex flex-col gap-2">
                  {mcpRows.length === 0 && <p className="py-2 text-xs text-ink3">{t("mcp.empty", lang)}</p>}
                  {mcpRows.map((s, i) => (
                    <div key={i} className="flex flex-col gap-2 rounded-lg border border-line bg-panel2 p-3">
                      <div className="flex items-center gap-2">
                        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-hover text-ink2">
                          <IconGlobe size={13} />
                        </span>
                        <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-ink">{s.name}</span>
                        <IconButton title={t("common.delete", lang)} onClick={() => setMcpRows((rows) => rows.filter((_, idx) => idx !== i))} className="h-7 w-7 text-ink3 hover:text-err">
                          <IconTrash size={12} />
                        </IconButton>
                      </div>
                      <Field label={t("mcp.transport", lang)}>
                        <Select
                          value={s.transport ?? "stdio"}
                          onChange={(e) => {
                            const transport = e.target.value as McpServer["transport"]
                            patchMcp(i, {
                              transport,
                              http:
                                transport === "stdio"
                                  ? null
                                  : s.http ?? { url: "", headers: {}, timeout_sec: 30 },
                            })
                          }}
                        >
                          <option value="stdio">stdio</option>
                          <option value="sse">sse</option>
                          <option value="streamable_http">streamable_http</option>
                        </Select>
                      </Field>
                      {(s.transport ?? "stdio") === "stdio" ? (
                        <div className="grid grid-cols-2 gap-2">
                          <Field label={t("common.command", lang)}>
                            <TextInput value={s.command} onChange={(e) => patchMcp(i, { command: e.target.value })} placeholder={t("mcp.commandPh", lang)} />
                          </Field>
                          <Field label={t("common.args", lang)}>
                            <TextInput value={s.args.join(" ")} onChange={(e) => patchMcp(i, { args: e.target.value.split(/\s+/).filter(Boolean) })} placeholder={t("mcp.argsPh", lang)} />
                          </Field>
                        </div>
                      ) : (
                        <Field label={t("mcp.url", lang)}>
                          <TextInput
                            value={s.http?.url ?? ""}
                            onChange={(e) =>
                              patchMcp(i, {
                                http: { url: e.target.value, headers: s.http?.headers ?? {}, timeout_sec: s.http?.timeout_sec ?? 30 },
                              })
                            }
                            placeholder="https://example.com/mcp"
                          />
                        </Field>
                      )}
                      <Field label={t("mcp.readonly", lang)}>
                        <TextInput value={s.readonly_tools.join(" ")} onChange={(e) => patchMcp(i, { readonly_tools: e.target.value.split(/\s+/).filter(Boolean) })} placeholder={t("mcp.readonlyPh", lang)} />
                      </Field>
                      <Field label={t("mcp.allowedTools", lang)} hint={<span className="text-[10px] text-ink3">{t("mcp.allowedToolsHint", lang)}</span>}>
                        <TextInput
                          value={(s.allowed_tools ?? []).join(" ")}
                          onChange={(e) => patchMcp(i, { allowed_tools: e.target.value.split(/\s+/).filter(Boolean) })}
                          placeholder="read_* write_file"
                          className="font-mono"
                        />
                      </Field>
                      {(s.transport ?? "stdio") !== "stdio" && (
                        <>
                          <Field label={t("mcp.headers", lang)} hint={<span className="text-[10px] text-ink3">{t("mcp.headersHint", lang)}</span>}>
                            <TextArea
                              rows={2}
                              value={headerText[s.name] ?? headersToText(s.http?.headers)}
                              onChange={(e) => {
                                const raw = e.target.value
                                setHeaderText((m) => ({ ...m, [s.name]: raw }))
                                patchMcp(i, {
                                  http: {
                                    url: s.http?.url ?? "",
                                    headers: textToHeaders(raw),
                                    timeout_sec: s.http?.timeout_sec ?? 30,
                                  },
                                })
                              }}
                              placeholder={t("mcp.headersPh", lang)}
                              className="font-mono text-xs"
                            />
                          </Field>
                          <Field label={t("mcp.timeout", lang)}>
                            <TextInput
                              type="number"
                              min={1}
                              max={600}
                              value={s.http?.timeout_sec ?? 30}
                              onChange={(e) => {
                                const raw = e.target.value
                                if (raw === "") return
                                patchMcp(i, {
                                  http: {
                                    url: s.http?.url ?? "",
                                    headers: s.http?.headers ?? {},
                                    timeout_sec: Math.min(600, Math.max(1, Number(raw))),
                                  },
                                })
                              }}
                            />
                          </Field>
                        </>
                      )}
                      {s.env && Object.keys(s.env).length > 0 && (
                        <div className="flex flex-col gap-2 rounded-lg border border-warn/30 bg-warn/5 p-2.5">
                          <p className="text-[11px] text-warn">{t("mcp.envHint", lang)}</p>
                          {Object.keys(s.env).map((key) => (
                            <Field key={key} label={key}>
                              <TextInput
                                type="password"
                                autoComplete="off"
                                value={s.env?.[key] ?? ""}
                                onChange={(e) => patchMcp(i, { env: { ...(s.env ?? {}), [key]: e.target.value } })}
                                placeholder={t("mcp.envPh", lang)}
                              />
                            </Field>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                  <div className="flex items-center gap-2">
                    <Button size="sm" variant="soft" onClick={() => setMcpRows((rows) => [...rows, { name: `mcp-${rows.length + 1}`, command: "", args: [], env: {}, readonly_tools: [] }])}>
                      <IconPlus size={12} /> {t("mcp.add", lang)}
                    </Button>
                    <Button size="sm" variant="soft" onClick={() => setShowRegistry(true)}>
                      <IconGlobe size={12} /> {t("mcp.browseRegistry", lang)}
                    </Button>
                  </div>
                </div>
              </section>
            </fieldset>
          )}

          {tab === "project" && (
            <div className="flex flex-col gap-4">
              <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-ink">{t("project.memoryTitle", lang)}</h3>
                  <Button size="sm" variant="soft" onClick={handleSaveMd} disabled={busy === "save"}>
                    {busy === "save" ? <Spinner size={11} /> : t("project.saveMemory", lang)}
                  </Button>
                </div>
                <p className="text-xs leading-relaxed text-ink3">{t("project.memoryDesc", lang)}</p>
                {agentsMd ? (
                  <>
                    <TextArea
                      rows={8}
                      value={agentsMd.content}
                      onChange={(e) => {
                        dirtyRef.current.md = true
                        setAgentsMd((a) => (a ? { ...a, content: e.target.value, chars: e.target.value.length } : a))
                      }}
                      placeholder={t("project.memoryPh", lang)}
                      className="font-mono text-[12.5px]"
                    />
                    <span className="text-right text-[10.5px] text-ink3">
                      {t("project.charCount", lang, { current: agentsMd.chars, max: agentsMd.max_fragment_chars })}
                    </span>
                  </>
                ) : (
                  <p className="text-xs text-ink3">{t("common.loading", lang)}</p>
                )}
              </section>

              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("project.contextMgmt", lang)}</h3>
                <p className="text-xs leading-relaxed text-ink3">{t("project.contextDesc", lang)}</p>
                <Field label={t("project.compactThreshold", lang)}>
                  {/* Stored as a fraction (0.1–0.99) but shown as a percentage:
                      typing 85 must mean 85%, and clearing the box must keep the
                      old value instead of injecting one the backend rejects. */}
                  <TextInput
                    type="number"
                    min={10}
                    max={99}
                    step={1}
                    value={Math.round((cfg.context.compact_threshold ?? 0.85) * 100)}
                    onChange={(e) => {
                      const raw = e.target.value
                      if (raw === "") return
                      const pct = Math.min(99, Math.max(10, Number(raw)))
                      dirtyRef.current.cfg = true
                      setCfg((c) => (c ? { ...c, context: { ...c.context, compact_threshold: pct / 100 } } : c))
                    }}
                  />
                </Field>
                <Field label={t("project.contextBudget", lang)}>
                  <TextInput
                    type="number"
                    min={1024}
                    value={cfg.context.max_context_tokens ?? 32768}
                    onChange={(e) => {
                      const raw = e.target.value
                      if (raw === "") return
                      dirtyRef.current.cfg = true
                      setCfg((c) => (c ? { ...c, context: { ...c.context, max_context_tokens: Math.max(1024, Number(raw)) } } : c))
                    }}
                  />
                </Field>
                <Field label={t("project.keepRecent", lang)}>
                  <TextInput
                    type="number"
                    min={2}
                    max={200}
                    value={cfg.context.keep_recent_messages ?? 8}
                    onChange={(e) => {
                      const raw = e.target.value
                      if (raw === "") return
                      const keep = Math.min(200, Math.max(2, Number(raw)))
                      dirtyRef.current.cfg = true
                      setCfg((c) => (c ? { ...c, context: { ...c.context, keep_recent_messages: keep } } : c))
                    }}
                  />
                </Field>
                <Field label={t("project.fragmentLimit", lang)}>
                  <TextInput
                    type="number"
                    min={100}
                    max={200000}
                    value={cfg.context.max_fragment_chars ?? 8000}
                    onChange={(e) => {
                      const raw = e.target.value
                      if (raw === "") return
                      const frag = Math.min(200000, Math.max(100, Number(raw)))
                      dirtyRef.current.cfg = true
                      setCfg((c) => (c ? { ...c, context: { ...c.context, max_fragment_chars: frag } } : c))
                    }}
                  />
                </Field>
                <Field label={t("project.historyBudget", lang)}>
                  <TextInput
                    type="number"
                    min={1000}
                    value={cfg.context.history_budget_chars}
                    onChange={(e) => {
                      const raw = e.target.value
                      if (raw === "") return
                      patchContext({ history_budget_chars: Math.max(1000, Number(raw)) })
                    }}
                  />
                </Field>
              </section>

              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("project.advancedTitle", lang)}</h3>
                <p className="text-xs leading-relaxed text-ink3">{t("project.advancedDesc", lang)}</p>
                <label className="flex items-center justify-between gap-3">
                  <span className="flex min-w-0 flex-col">
                    <span className="text-[12.5px] text-ink">{t("project.repoMap", lang)}</span>
                    <span className="text-[10.5px] leading-snug text-ink3">{t("project.repoMapHint", lang)}</span>
                  </span>
                  <Toggle
                    checked={cfg.context.repo_map_enabled ?? false}
                    onChange={(v) => patchContext({ repo_map_enabled: v })}
                  />
                </label>
                {cfg.context.repo_map_max_chars !== undefined && (
                  <Field label={t("project.repoMapMax", lang)}>
                    <TextInput
                      type="number"
                      min={500}
                      max={60000}
                      value={cfg.context.repo_map_max_chars}
                      onChange={(e) => {
                        const raw = e.target.value
                        if (raw === "") return
                        patchContext({ repo_map_max_chars: Math.min(60000, Math.max(500, Number(raw))) })
                      }}
                    />
                  </Field>
                )}
                <label className="flex items-center justify-between gap-3">
                  <span className="text-[12.5px] text-ink">{t("project.knowledgeEnabled", lang)}</span>
                  <Toggle
                    checked={cfg.context.knowledge_enabled ?? false}
                    onChange={(v) => patchContext({ knowledge_enabled: v })}
                  />
                </label>
                {cfg.context.knowledge_top_k !== undefined && (
                  <Field label={t("project.knowledgeTopK", lang)}>
                    <TextInput
                      type="number"
                      min={1}
                      max={50}
                      value={cfg.context.knowledge_top_k}
                      onChange={(e) => {
                        const raw = e.target.value
                        if (raw === "") return
                        patchContext({ knowledge_top_k: Math.min(50, Math.max(1, Number(raw))) })
                      }}
                    />
                  </Field>
                )}
                {cfg.context.knowledge_max_chars !== undefined && (
                  <Field label={t("project.knowledgeMaxChars", lang)}>
                    <TextInput
                      type="number"
                      min={500}
                      max={60000}
                      value={cfg.context.knowledge_max_chars}
                      onChange={(e) => {
                        const raw = e.target.value
                        if (raw === "") return
                        patchContext({ knowledge_max_chars: Math.min(60000, Math.max(500, Number(raw))) })
                      }}
                    />
                  </Field>
                )}
                <Field
                  label={t("project.agentsMd", lang)}
                  hint={/[\\/]/.test(agentsMdName) ? <span className="text-err">{t("project.agentsMdInvalid", lang)}</span> : undefined}
                >
                  <TextInput
                    value={agentsMdName}
                    onChange={(e) => {
                      dirtyRef.current.cfg = true
                      setAgentsMdName(e.target.value)
                    }}
                    placeholder="AGENTS.md"
                    spellCheck={false}
                    className="font-mono"
                  />
                </Field>
                <p className="text-[10.5px] leading-relaxed text-ink3">{t("project.agentsMdHint", lang)}</p>
              </section>

              <section className="rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("project.workdir", lang)}</h3>
                <p className="mt-1 font-mono text-xs text-ink2">{cfg.workdir}</p>
              </section>

              <HealthSection lang={lang} />
            </div>
          )}

          {tab === "automations" && <AutomationsSection />}

          {tab === "debug" && <DebugSection />}

          {tab === "advanced" && (
            <div className="flex flex-col gap-4">
              <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
                <h3 className="text-sm font-semibold text-ink">{t("advanced.title", lang)}</h3>
                <p className="text-xs leading-relaxed text-ink3">{t("advanced.desc", lang)}</p>
              </section>

              {(cfg.replay !== undefined || cfg.updater !== undefined) && (
                <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
                  <h3 className="text-sm font-semibold text-ink">{t("advanced.runtime", lang)}</h3>
                  {cfg.replay && (
                    <label className="flex items-start justify-between gap-3">
                      <span className="flex min-w-0 flex-col">
                        <span className="text-[12.5px] text-ink">{t("advanced.replayEnabled", lang)}</span>
                        <span className="text-[10.5px] leading-snug text-ink3">{t("advanced.replayHint", lang)}</span>
                      </span>
                      <Toggle checked={cfg.replay.enabled} onChange={(v) => patchReplay({ enabled: v })} />
                    </label>
                  )}
                  {cfg.updater && (
                    <Field label={t("advanced.updaterMode", lang)} hint={<span className="text-[10px] text-ink3">{t("advanced.updaterHint", lang)}</span>}>
                      <Select
                        value={cfg.updater.mode}
                        onChange={(e) => patchUpdater({ mode: e.target.value as "notify" | "off" })}
                      >
                        <option value="notify">{t("advanced.updaterNotify", lang)}</option>
                        <option value="off">{t("advanced.updaterOff", lang)}</option>
                      </Select>
                    </Field>
                  )}
                </section>
              )}

              {cfg.permission_rules !== undefined && (
                <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
                  <h3 className="text-sm font-semibold text-ink">{t("advanced.permissionRules", lang)}</h3>
                  <p className="text-xs leading-relaxed text-ink3">{t("advanced.permissionRulesDesc", lang)}</p>
                  {cfg.permission_rules.length === 0 ? (
                    <p className="text-xs text-ink3">{t("advanced.permissionEmpty", lang)}</p>
                  ) : (
                    <div className="flex flex-col divide-y divide-line rounded-lg border border-line">
                      <div className="grid grid-cols-[80px_1fr_120px_28px] gap-2 bg-panel2 px-3 py-1.5 text-[10.5px] font-medium text-ink3">
                        <span>{t("advanced.ruleAction", lang)}</span>
                        <span>{t("advanced.ruleResource", lang)}</span>
                        <span>{t("advanced.ruleTool", lang)}</span>
                        <span />
                      </div>
                      {cfg.permission_rules.map((r, i) => (
                        <div key={`${r.resource}-${i}`} className="grid grid-cols-[80px_1fr_120px_28px] items-center gap-2 px-3 py-1.5">
                          <Badge tone={r.action === "allow" ? "ok" : r.action === "deny" ? "err" : "warn"}>{r.action}</Badge>
                          <span className="truncate font-mono text-[11.5px] text-ink2" title={r.resource}>{r.resource}</span>
                          <span className="truncate font-mono text-[11.5px] text-ink3" title={r.tool ?? ""}>{r.tool || "*"}</span>
                          <button
                            type="button"
                            title={t("common.remove", lang)}
                            className="text-ink3 transition-colors hover:text-err disabled:opacity-40"
                            disabled={ruleBusy}
                            onClick={() => void removeRule(i)}
                          >
                            <IconX size={12} />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="flex flex-col gap-2 rounded-lg border border-line bg-panel2 p-2.5">
                    <div className="grid grid-cols-[92px_1fr] gap-2">
                      <Select value={ruleDraft.action} onChange={(e) => setRuleDraft((d) => ({ ...d, action: e.target.value as "allow" | "prompt" | "deny" }))}>
                        <option value="deny">deny</option>
                        <option value="prompt">prompt</option>
                        <option value="allow">allow</option>
                      </Select>
                      <TextInput value={ruleDraft.resource} onChange={(e) => setRuleDraft((d) => ({ ...d, resource: e.target.value }))} placeholder={t("advanced.ruleResourcePh", lang)} className="font-mono" />
                    </div>
                    <div className="flex gap-2">
                      <TextInput value={ruleDraft.tool} onChange={(e) => setRuleDraft((d) => ({ ...d, tool: e.target.value }))} placeholder={t("advanced.ruleToolPh", lang)} className="font-mono" />
                      <Button size="sm" variant="soft" disabled={ruleBusy || !ruleDraft.resource.trim()} onClick={() => void addRule()}>
                        <IconPlus size={12} /> {t("advanced.ruleAdd", lang)}
                      </Button>
                    </div>
                  </div>
                </section>
              )}

              {cfg.approval_profiles !== undefined && (
                <section className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4">
                  <h3 className="text-sm font-semibold text-ink">{t("advanced.approvalProfiles", lang)}</h3>
                  <p className="text-xs leading-relaxed text-ink3">{t("advanced.approvalProfilesDesc", lang)}</p>
                  {cfg.approval_profiles.length === 0 ? (
                    <p className="text-xs text-ink3">{t("advanced.approvalEmpty", lang)}</p>
                  ) : (
                    <div className="flex flex-col gap-1.5">
                      {cfg.approval_profiles.map((p) => (
                        <div key={p.id} className="flex flex-col gap-1 rounded-lg border border-line bg-panel2 px-3 py-2">
                          <div className="flex items-center gap-2">
                            <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-ink">{p.name || p.id}</span>
                            {cfg.active_approval_profile_id === p.id && <Badge tone="ok">{t("advanced.profileActive", lang)}</Badge>}
                            <Badge tone="accent">{p.approval}</Badge>
                          </div>
                          <p className="truncate font-mono text-[10.5px] text-ink3">
                            {t("advanced.profileTools", lang)}: {p.tools && p.tools.length > 0 ? p.tools.join(" ") : "*"}
                            {p.model_profile_id ? ` · ${t("advanced.profileModel", lang)}: ${p.model_profile_id}` : ""}
                          </p>
                          <div className="flex gap-2 pt-0.5">
                            <Button size="sm" variant="ghost" disabled={ruleBusy || cfg.active_approval_profile_id === p.id} onClick={() => void activateProfile(p.id)}>
                              {t("advanced.profileActivate", lang)}
                            </Button>
                            <Button size="sm" variant="ghost" disabled={ruleBusy} onClick={() => void removeProfile(p.id)}>
                              {t("common.remove", lang)}
                            </Button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="flex flex-col gap-2 rounded-lg border border-line bg-panel2 p-2.5">
                    <div className="grid grid-cols-2 gap-2">
                      <TextInput value={profileDraft.name} onChange={(e) => setProfileDraft((d) => ({ ...d, name: e.target.value }))} placeholder={t("advanced.profileNewNamePh", lang)} />
                      <Select value={profileDraft.approval} onChange={(e) => setProfileDraft((d) => ({ ...d, approval: e.target.value }))}>
                        <option value="suggest">suggest</option>
                        <option value="auto-edit">auto-edit</option>
                        <option value="full-auto">full-auto</option>
                        <option value="plan-only">plan-only</option>
                        <option value="approve-plan">approve-plan</option>
                        <option value="manual">manual</option>
                      </Select>
                    </div>
                    <TextInput value={profileDraft.tools} onChange={(e) => setProfileDraft((d) => ({ ...d, tools: e.target.value }))} placeholder={t("advanced.profileToolsPh", lang)} className="font-mono" />
                    <Button size="sm" variant="soft" disabled={ruleBusy || !profileDraft.name.trim()} onClick={() => void addProfile()}>
                      <IconPlus size={12} /> {t("advanced.profileCreate", lang)}
                    </Button>
                  </div>
                </section>
              )}
            </div>
          )}
        </div>

        {/* 底部保存：skills/prompts/plugins/shortcuts/knowledge/wiki/automations
            都有自己的即时操作，不该出现统一的"保存设置"按钮；
            memory 页含记忆配置区块，需要统一保存 */}
        {tab !== "skills" && tab !== "prompts" && tab !== "plugins" && tab !== "shortcuts" && tab !== "knowledge" && tab !== "wiki" && tab !== "automations" && tab !== "debug" && (
          <div className="border-t border-line bg-panel2 px-4 py-3">
            <Button variant="primary" full onClick={handleSave} disabled={busy === "save"}>
              {busy === "save" ? (
                <span className="flex items-center gap-1.5"><Spinner size={12} /> {t("settings.saving", lang)}</span>
              ) : (
                t("settings.saveAll", lang)
              )}
            </Button>
          </div>
          )}
          </div>
        </div>
      </aside>
      {showRegistry && <McpRegistryModal onClose={() => setShowRegistry(false)} onPick={handleRegistryPick} />}
    </div>
  )
}
