import { memo, useEffect, useState } from "react"
import { IconHelp, IconKey, IconSpark, IconWrench, IconX } from "./icons"
import { useUiStore } from "../store/uiStore"
import { t, type TranslationKey } from "../i18n"

const FLAG = "spark.onboarding.done"

/**
 * 首次使用引导（审查报告第五节体验差距：竞品均有 onboarding）。
 *
 * 门条件：localStorage 完成标记。服务端启动即自动创建「新对话」会话
 * （state.py:286），"无会话"不是有效的新用户信号——所以老用户升级后
 * 也会看到一次，任一完成路径（跳过 / 去配置 / 开始使用）写入标记后不再出现。
 */
export const Onboarding = memo(function Onboarding() {
  const lang = useUiStore((s) => s.lang)
  const openSettingsAt = useUiStore((s) => s.openSettingsAt)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    // ?onboarding=skip：自动化测试/截图需要跳过引导（同时写入标记，
    // 与用户手动跳过走同一条持久化路径）。
    try {
      const params = new URLSearchParams(window.location.search)
      if (params.get("onboarding") === "skip") {
        localStorage.setItem(FLAG, "1")
        return
      }
    } catch {
      // localStorage 不可用（隐私模式）时按原逻辑显示
    }
    if (localStorage.getItem(FLAG) !== "1") setVisible(true)
  }, [])

  const finish = () => {
    localStorage.setItem(FLAG, "1")
    setVisible(false)
  }

  if (!visible) return null

  const steps: Array<{
    n: number
    icon: typeof IconKey
    title: TranslationKey
    desc: TranslationKey
    action?: () => void
    actionLabel?: TranslationKey
  }> = [
    {
      n: 1,
      icon: IconKey,
      title: "onboarding.step1Title",
      desc: "onboarding.step1Desc",
      action: () => {
        finish()
        openSettingsAt("model")
      },
      actionLabel: "onboarding.step1Action",
    },
    {
      n: 2,
      icon: IconWrench,
      title: "onboarding.step2Title",
      desc: "onboarding.step2Desc",
      action: () => {
        finish()
        openSettingsAt("agent")
      },
      actionLabel: "onboarding.step2Action",
    },
    {
      n: 3,
      icon: IconSpark,
      title: "onboarding.step3Title",
      desc: "onboarding.step3Desc",
    },
  ]

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-[2px]"
      role="dialog"
      aria-modal="true"
      aria-label={t("onboarding.title", lang)}
    >
      <div className="anim-fade flex max-h-[86vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-line bg-canvas shadow-[var(--shadow)]">
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <IconHelp size={15} className="text-accent" />
          <span className="text-sm font-semibold text-ink">{t("onboarding.title", lang)}</span>
          <button
            type="button"
            onClick={finish}
            title={t("onboarding.skip", lang)}
            className="ml-auto text-ink3 transition-colors hover:text-ink"
          >
            <IconX size={15} />
          </button>
        </div>
        <div className="flex flex-col gap-2 overflow-y-auto p-3">
          <p className="px-1 pb-1 text-[12px] text-ink3">{t("onboarding.subtitle", lang)}</p>
          {steps.map((s) => (
            <div
              key={s.n}
              className="flex items-start gap-3 rounded-xl border border-line bg-panel px-3.5 py-3"
            >
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-hover text-[12px] font-semibold text-accent">
                {s.n}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">{t(s.title, lang)}</span>
                <span className="mt-0.5 block text-[11.5px] leading-relaxed text-ink3">{t(s.desc, lang)}</span>
              </span>
              {s.action && s.actionLabel && (
                <button
                  type="button"
                  onClick={s.action}
                  className="shrink-0 rounded-lg border border-line bg-canvas px-2.5 py-1 text-[11.5px] font-medium text-ink2 transition-colors hover:bg-hover"
                >
                  {t(s.actionLabel, lang)}
                </button>
              )}
            </div>
          ))}
        </div>
        <div className="flex items-center justify-between border-t border-line px-4 py-2.5">
          <span className="flex items-center gap-1.5 text-[11px] text-ink3">
            <IconSpark size={12} />
            Spark
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={finish}
              className="rounded-lg px-2.5 py-1 text-[12px] text-ink3 transition-colors hover:bg-hover hover:text-ink2"
            >
              {t("onboarding.skip", lang)}
            </button>
            <button
              type="button"
              onClick={finish}
              className="rounded-lg bg-accent px-3 py-1 text-[12px] font-medium text-on-accent transition-opacity hover:opacity-90"
            >
              {t("onboarding.done", lang)}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
})
