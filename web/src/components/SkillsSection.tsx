import { useCallback, useEffect, useState } from "react"
import { Button, Field, IconButton, Spinner, TextArea, TextInput, cls } from "./ui"
import { IconPlus, IconTrash, IconPencil, IconCheck, IconX } from "./icons"
import { skillsAction, type SkillItem } from "../api"
import { useUiStore } from "../store/uiStore"
import { t } from "../i18n"

// i18n-exempt: default user-content template; EN variant below is selected by lang
const TEMPLATE_ZH = `---
name: my-skill
description: 一句话说明这个技能解决什么问题
when_to_use: 用户提到 X 时使用
---

在这里写技能正文。模型调用 load_skill 后会按这段文字执行。
`

const TEMPLATE_EN = `---
name: my-skill
description: One sentence describing what this skill solves
when_to_use: Use when the user mentions X
---

Write the skill body here. The model follows this text after calling load_skill.
`

/** 技能：工作区 .spark/skills 下的 Markdown，与模型侧 load_skill 工具同源。 */
export default function SkillsSection() {
  const lang = useUiStore((s) => s.lang)
  const pushToast = useUiStore((s) => s.pushToast)
  const [items, setItems] = useState<SkillItem[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<{ name: string; content: string; isNew: boolean } | null>(null)
  const [preview, setPreview] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = (await skillsAction({ action: "list" })) as { items: SkillItem[] }
      setItems(data.items ?? [])
    } catch (e) {
      pushToast("common.error", "error", e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [pushToast])

  useEffect(() => {
    void load()
  }, [load])

  async function open(path: string) {
    try {
      const data = (await skillsAction({ action: "read", path })) as { content: string }
      setPreview(data.content)
    } catch (e) {
      pushToast("common.error", "error", e instanceof Error ? e.message : String(e))
    }
  }

  async function save() {
    if (!editing) return
    setBusy(true)
    try {
      const data = (await skillsAction({
        action: "save",
        name: editing.name,
        content: editing.content,
      })) as { items: SkillItem[] }
      setItems(data.items ?? [])
      setEditing(null)
      pushToast("skills.saved", "success")
    } catch (e) {
      pushToast("common.error", "error", e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function remove(path: string) {
    useUiStore.getState().requestConfirm({
      title: t("skills.deleteTitle", lang),
      message: t("skills.deleteConfirm", lang),
      danger: true,
      onConfirm: async () => {
        setBusy(true)
        try {
          const data = (await skillsAction({ action: "delete", path })) as { items: SkillItem[] }
          setItems(data.items ?? [])
          if (preview !== null) setPreview(null)
        } catch (e) {
          pushToast("common.error", "error", e instanceof Error ? e.message : String(e))
        } finally {
          setBusy(false)
        }
      },
    })
  }

  /** 文件名决定 frontmatter 里的 name：两者不一致时列表标题会与磁盘文件名对不上，
   *  用户改完名字回来找不到自己刚建的文件。改名时同步重写模板里的 name 行。 */
  function rename(next: string) {
    if (!editing) return
    const stem = next.replace(/\.md$/i, "")
    const synced = editing.isNew
      ? editing.content.replace(/^name:.*$/m, stem ? `name: ${stem}` : "name: ")
      : editing.content
    setEditing({ ...editing, name: next, content: synced })
  }

  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-4">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-ink">{t("skills.title", lang)}</h3>
          <Button
            size="sm"
            variant="soft"
            className="shrink-0"
            onClick={() => setEditing({ name: "", content: lang === "zh" ? TEMPLATE_ZH : TEMPLATE_EN, isNew: true })}
          >
            <IconPlus size={12} /> {t("skills.new", lang)}
          </Button>
        </div>
        <p className="text-xs leading-relaxed text-ink3">{t("skills.desc", lang)}</p>

        {loading ? (
          <div className="flex items-center gap-2 py-6 text-xs text-ink3">
            <Spinner size={13} /> {t("common.loading", lang)}
          </div>
        ) : items.length === 0 ? (
          <p className="py-4 text-xs text-ink3">{t("skills.empty", lang)}</p>
        ) : (
          <div className="flex flex-col gap-2">
            {items.map((s) => (
              <div key={s.path} className="flex flex-col gap-1 rounded-lg border border-line bg-panel2 px-3 py-2.5">
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-ink">{s.name}</span>
                  {/* frontmatter 的 name 与文件名可以不同；只显示 name 时用户
                      在磁盘上找不到对应文件，所以把路径一并给出。 */}
                  {!s.path.endsWith(`${s.name}.md`) && (
                    <span className="shrink-0 truncate font-mono text-[10px] text-ink3">{s.path}</span>
                  )}
                  <span className="shrink-0 text-[10.5px] text-ink3">{s.chars} chars</span>
                  <IconButton title={t("skills.view", lang)} onClick={() => void open(s.path)} className="h-7 w-7">
                    <IconPencil size={12} />
                  </IconButton>
                  <IconButton
                    title={t("common.delete", lang)}
                    onClick={() => void remove(s.path)}
                    className="h-7 w-7 text-ink3 hover:text-err"
                  >
                    <IconTrash size={12} />
                  </IconButton>
                </div>
                {s.description && <p className="text-[11.5px] leading-relaxed text-ink3">{s.description}</p>}
              </div>
            ))}
          </div>
        )}
      </section>

      {editing && (
        <section className="flex flex-col gap-2 rounded-xl border border-accent/40 bg-panel p-4">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-ink">
              {editing.isNew ? t("skills.new", lang) : t("skills.edit", lang)}
            </h3>
            <IconButton title={t("common.close", lang)} className="ml-auto h-7 w-7" onClick={() => setEditing(null)}>
              <IconX size={14} />
            </IconButton>
          </div>
          <Field label={t("skills.fileName", lang)}>
            <TextInput
              value={editing.name}
              onChange={(e) => rename(e.target.value)}
              placeholder="my-skill.md"
              spellCheck={false}
            />
          </Field>
          <TextArea
            rows={14}
            value={editing.content}
            onChange={(e) => setEditing({ ...editing, content: e.target.value })}
            className="font-mono text-[12px]"
            spellCheck={false}
          />
          <div className="flex items-center gap-2">
            <Button variant="primary" className="flex-1" onClick={() => void save()} disabled={busy || !editing.name.trim()}>
              {busy ? <Spinner size={12} /> : <IconCheck size={13} />} {t("common.save", lang)}
            </Button>
            <Button variant="outline" className="shrink-0" onClick={() => setEditing(null)}>
              {t("common.cancel", lang)}
            </Button>
          </div>
        </section>
      )}

      {preview !== null && (
        <section className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-4">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-ink">{t("skills.content", lang)}</h3>
            <IconButton title={t("common.close", lang)} className="ml-auto h-7 w-7" onClick={() => setPreview(null)}>
              <IconX size={14} />
            </IconButton>
          </div>
          <pre className={cls("max-h-72 overflow-auto rounded-lg bg-panel2 px-3 py-2 font-mono text-[11.5px] leading-relaxed text-ink2")}>
            {preview}
          </pre>
        </section>
      )}
    </div>
  )
}
