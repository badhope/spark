import { memo } from "react"
import { BrandMark } from "./BrandMark"
import { IconSpark } from "./icons"
import { t, type TranslationKey } from "../i18n"
import { useUiStore } from "../store/uiStore"

const suggestions: Array<{ title: TranslationKey; desc: TranslationKey; prompt: TranslationKey }> = [
  { title: "empty.suggest.explore", desc: "empty.suggest.exploreDesc", prompt: "empty.suggest.explorePrompt" },
  { title: "empty.suggest.code", desc: "empty.suggest.codeDesc", prompt: "empty.suggest.codePrompt" },
  { title: "empty.suggest.review", desc: "empty.suggest.reviewDesc", prompt: "empty.suggest.reviewPrompt" },
]

const features: TranslationKey[] = [
  "empty.feature.code",
  "empty.feature.memory",
  "empty.feature.sandbox",
  "sidebar.checkpoint",
]

/**
 * 首屏空态。设计要点：
 * - 背景网格 + 顶部径向光晕，给平面一点空间纵深，而不是一块死黑。
 * - 标题用渐变色而不是纯白，弱化"大标题压人"的感觉。
 * - 建议卡做成可扫读的行（图标 / 标题 / 说明 / 箭头分列），
 *   而不是堆三段文字；hover 时整体上浮 1px 并亮边。
 */
export const EmptyState = memo(function EmptyState({ onPick }: { onPick: (prompt: string) => void }) {
  const lang = useUiStore((s) => s.lang)
  return (
    <div className="relative flex h-full items-center justify-center overflow-hidden">
      <div aria-hidden className="grid-bg pointer-events-none absolute inset-0" />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-[260px]"
        style={{
          background: "radial-gradient(50% 100% at 50% 0%, var(--accent-soft), transparent 72%)",
          opacity: 0.55,
        }}
      />

      <div className="anim-fade-up relative mx-auto w-full max-w-[560px] px-6 py-10">
        <div className="flex flex-col items-center text-center">
          <div className="relative mb-6">
            <div
              aria-hidden
              className="absolute inset-0 -z-10 rounded-full blur-2xl"
              style={{ background: "var(--accent-glow)", transform: "scale(0.9)" }}
            />
            <BrandMark size={56} />
          </div>

          <h1
            className="text-[28px] font-bold tracking-[-0.03em]"
            style={{
              background: "linear-gradient(180deg, var(--text) 30%, color-mix(in srgb, var(--text) 55%, transparent))",
              WebkitBackgroundClip: "text",
              backgroundClip: "text",
              color: "transparent",
            }}
          >
            Spark
          </h1>
          <p className="mt-2.5 max-w-[420px] text-[13px] leading-relaxed text-ink2">
            {t("empty.tagline", lang)}
          </p>
        </div>

        <div className="mt-9 flex flex-col gap-2">
          {suggestions.map((s, i) => (
            <button
              key={s.title}
              type="button"
              onClick={() => onPick(t(s.prompt, lang))}
              style={{ animationDelay: `${60 + i * 45}ms` }}
              className="anim-fade-up ring-focus group surface flex items-center gap-3 px-3.5 py-3 text-left transition-[transform,border-color,box-shadow] duration-200 ease-out hover:-translate-y-px hover:border-line2 hover:shadow-[var(--shadow)]"
            >
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[9px] bg-accent-soft text-accent transition-colors group-hover:bg-accent group-hover:text-on-accent">
                <IconSpark size={15} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-ink">{t(s.title, lang)}</span>
                <span className="mt-0.5 block truncate text-[11.5px] text-ink3">{t(s.desc, lang)}</span>
              </span>
              <span className="shrink-0 text-[12px] text-ink3 opacity-0 transition-all duration-200 group-hover:translate-x-0.5 group-hover:opacity-100">
                →
              </span>
            </button>
          ))}
        </div>

        <div className="mt-10 flex flex-wrap items-center justify-center gap-x-1.5 gap-y-2">
          {features.map((f) => (
            <span
              key={f}
              className="rounded-full border border-line bg-panel/60 px-2.5 py-1 text-[10.5px] text-ink3"
            >
              {t(f, lang)}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
})
