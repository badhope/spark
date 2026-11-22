import { useCallback, useEffect, useState } from "react"
import { Button, Field, IconButton, Spinner, TextArea, TextInput, cls } from "./ui"
import { IconPlus, IconTrash, IconX } from "./icons"
import { promptsAction, type CommandItem } from "../api"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

// i18n-exempt: default user-content template; EN variant below is selected by lang
const COMMAND_TEMPLATE_ZH = `# 命令说明（会显示在 / 补全里）
把这段替换成命令正文。用户输入 /{{name}} 时，这段文字会替换掉斜杠命令，
作为 user 消息发给模型。
`

const COMMAND_TEMPLATE_EN = `# Command description (shown in / completion)
Replace this with the command body. When the user types /{{name}}, this text
replaces the slash command and is sent to the model as a user message.
`

/** 提示词：系统提示词（覆盖内置）+ 斜杠命令模板（.spark/commands）。 */
export default function PromptsSection() {
  const lang = useUiStore((s) => s.lang)
  const pushToast = useUiStore((s) => s.pushToast)
  const [systemPrompt, setSystemPrompt] = useState("")
  const [defaultPrompt, setDefaultPrompt] = useState("")
  const [commands, setCommands] = useState<CommandItem[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [showDefault, setShowDefault] = useState(false)
  const [editing, setEditing] = useState<{ name: string; content: string } | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await promptsAction({ action: "list" })
      setSystemPrompt(data.system_prompt ?? "")
      setDefaultPrompt(data.default_prompt ?? "")
      setCommands(data.commands ?? [])
    } catch (e) {
      pushToast("common.error", "error", e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [pushToast])

  useEffect(() => {
    void load()
  }, [load])

  async function saveSystem() {
    setBusy(true)
    try {
      await promptsAction({ action: "save_system", content: systemPrompt })
      pushToast("prompts.saved", "success")
    } catch (e) {
      pushToast("common.error", "error", e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function saveCommand() {
    if (!editing) return
    setBusy(true)
    try {
      const data = (await promptsAction({
        action: "save_command",
        name: editing.name,
        content: editing.content,
      })) as { commands?: CommandItem[] }
      setCommands(data.commands ?? [])
      setEditing(null)
      pushToast("prompts.saved", "success")
    } catch (e) {
      pushToast("common.error", "error", e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function removeCommand(name: string) {
    useUiStore.getState().requestConfirm({
      title: t("prompts.deleteTitle", lang),
      message: t("prompts.deleteConfirm", lang),
      danger: true,
      onConfirm: async () => {
        setBusy(true)
        try {
          const data = (await promptsAction({ action: "delete_command", name })) as { commands?: CommandItem[] }
          setCommands(data.commands ?? [])
        } catch (e) {
          pushToast("common.error", "error", e instanceof Error ? e.message : String(e))
        } finally {
          setBusy(false)
        }
      },
    })
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-8 text-xs text-ink3">
        <Spinner size={13} /> {t("common.loading", lang)}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-4">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-ink">{t("prompts.systemTitle", lang)}</h3>
          {!systemPrompt.trim() && (
            <span className="rounded-[6px] bg-accent-soft px-1.5 py-0.5 text-[10.5px] font-medium text-accent">
              {t("prompts.builtinExpert", lang)}
            </span>
          )}
          <Button size="sm" variant="soft" className="ml-auto shrink-0" onClick={() => setShowDefault((v) => !v)}>
            {showDefault ? t("prompts.hideDefault", lang) : t("prompts.showDefault", lang)}
          </Button>
        </div>
        <p className="text-xs leading-relaxed text-ink3">{t("prompts.systemDesc", lang)}</p>
        {showDefault && (
          <pre className="max-h-48 overflow-auto rounded-lg bg-panel2 px-3 py-2 font-mono text-[11px] leading-relaxed text-ink3">
            {defaultPrompt}
          </pre>
        )}
        <TextArea
          rows={10}
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          placeholder={t("prompts.systemPh", lang)}
          className="font-mono text-[12px]"
          spellCheck={false}
        />
        <div className="flex items-center gap-2">
          <Button variant="primary" className="flex-1" onClick={() => void saveSystem()} disabled={busy}>
            {busy ? <Spinner size={12} /> : null} {t("prompts.saveSystem", lang)}
          </Button>
          <Button
            variant="outline"
            className="shrink-0 whitespace-nowrap"
            onClick={() => setSystemPrompt("")}
            disabled={busy || !systemPrompt}
          >
            {t("prompts.reset", lang)}
          </Button>
        </div>
        <p className="text-[10.5px] leading-relaxed text-ink3">{t("prompts.systemHint", lang)}</p>
      </section>

      <section className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-4">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-ink">{t("prompts.commandsTitle", lang)}</h3>
          <Button
            size="sm"
            variant="soft"
            className="shrink-0"
            onClick={() => setEditing({ name: "", content: lang === "zh" ? COMMAND_TEMPLATE_ZH : COMMAND_TEMPLATE_EN })}
          >
            <IconPlus size={12} /> {t("prompts.newCommand", lang)}
          </Button>
        </div>
        <p className="text-xs leading-relaxed text-ink3">{t("prompts.commandsDesc", lang)}</p>
        {commands.length === 0 ? (
          <p className="py-3 text-xs text-ink3">{t("prompts.noCommands", lang)}</p>
        ) : (
          <div className="flex flex-col gap-1.5">
            {commands.map((c) => (
              <div key={c.name} className="flex items-center gap-2 rounded-lg border border-line bg-panel2 px-3 py-2">
                <span className="shrink-0 font-mono text-[12.5px] text-accent">/{c.name}</span>
                <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink3">{c.description}</span>
                <IconButton
                  title={t("common.delete", lang)}
                  onClick={() => void removeCommand(c.name)}
                  className="h-7 w-7 text-ink3 hover:text-err"
                >
                  <IconTrash size={12} />
                </IconButton>
              </div>
            ))}
          </div>
        )}
      </section>

      {editing && (
        <section className="flex flex-col gap-2 rounded-xl border border-accent/40 bg-panel p-4">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-ink">{t("prompts.newCommand", lang)}</h3>
            <IconButton title={t("common.close", lang)} className="ml-auto h-7 w-7" onClick={() => setEditing(null)}>
              <IconX size={14} />
            </IconButton>
          </div>
          <Field label={t("prompts.commandName", lang)}>
            <TextInput
              value={editing.name}
              onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              placeholder="review.md"
              spellCheck={false}
            />
          </Field>
          <TextArea
            rows={12}
            value={editing.content}
            onChange={(e) => setEditing({ ...editing, content: e.target.value })}
            className={cls("font-mono text-[12px]")}
            spellCheck={false}
          />
          <div className="flex items-center gap-2">
            <Button variant="primary" className="flex-1" onClick={() => void saveCommand()} disabled={busy || !editing.name.trim()}>
              {busy ? <Spinner size={12} /> : null} {t("common.save", lang)}
            </Button>
            <Button variant="outline" className="shrink-0" onClick={() => setEditing(null)}>
              {t("common.cancel", lang)}
            </Button>
          </div>
        </section>
      )}
    </div>
  )
}
