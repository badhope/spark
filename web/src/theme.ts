export type Theme = "light" | "dark" | "system"

const STORAGE_KEY = "spark-theme"

/**
 * 默认亮色。
 *
 * 之前默认 "system"，在深色系统的机器上首次打开就是深色。产品定位是
 * 白天对着代码长时间使用的工具，亮色对多数人更友好；暗色仍然是一键可切
 * 的一等公民，只是不再替用户做决定。
 */
export const DEFAULT_THEME: Theme = "light"

export function getStoredTheme(): Theme {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === "light" || v === "dark" || v === "system") return v
  } catch {
    /* ignore */
  }
  return DEFAULT_THEME
}

export function systemPrefersDark(): boolean {
  return window.matchMedia("(prefers-color-scheme: dark)").matches
}

/** 解析实际生效的肤色（system 时跟随系统） */
export function resolveTheme(theme: Theme): "light" | "dark" {
  if (theme === "system") return systemPrefersDark() ? "dark" : "light"
  return theme
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.add("theme-transition")
  const resolved = resolveTheme(theme)
  document.documentElement.setAttribute("data-theme", resolved)
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    /* ignore */
  }
  const meta = document.querySelector('meta[name="theme-color"]')
  // 与 index.css 的 --bg 保持同源：旧值 #fafafa/#101012 是上一版设计的色，
  // 换肤后浏览器地址栏会和新界面不一致。
  if (meta) meta.setAttribute("content", resolved === "light" ? "#f2f2f4" : "#0b0b0d")
  // 过渡结束移除 class，避免常驻 transition 干扰滚动性能
  window.setTimeout(() => {
    document.documentElement.classList.remove("theme-transition")
  }, 220)
}

export function initTheme(): Theme {
  const theme = getStoredTheme()
  applyTheme(theme)
  // 系统主题实时跟随
  try {
    window
      .matchMedia("(prefers-color-scheme: dark)")
      .addEventListener("change", () => {
        const cur = getStoredTheme()
        if (cur === "system") {
          document.documentElement.setAttribute("data-theme", resolveTheme(cur))
        }
      })
  } catch {
    /* ignore */
  }
  return theme
}

/* ------------------------------------------------------------------ */
/* 强调色主题（palette）                                               */
/* 明暗是"模式"，配色是"主题"——两者正交，可以任意组合。              */
/* 只覆盖强调色系，不动中性色：中性色改了会破坏对比度与可读性。        */
/* ------------------------------------------------------------------ */

export type Palette = "ember" | "ocean" | "forest" | "violet" | "mono"

const PALETTE_KEY = "spark-palette"

export const PALETTES: Array<{ id: Palette; label: string }> = [
  { id: "ember", label: "Ember" },
  { id: "ocean", label: "Ocean" },
  { id: "forest", label: "Forest" },
  { id: "violet", label: "Violet" },
  { id: "mono", label: "Mono" },
]

export function getStoredPalette(): Palette {
  try {
    const v = localStorage.getItem(PALETTE_KEY)
    if (v === "ember" || v === "ocean" || v === "forest" || v === "violet" || v === "mono") return v
  } catch {
    /* ignore */
  }
  return "ember"
}

export function applyPalette(palette: Palette): void {
  // data-palette 挂在 <html> 上，由 index.css 覆盖 --accent 系列变量。
  // 用属性而不是内联样式：主题切换时不必重算 JS，CSS 直接级联。
  document.documentElement.setAttribute("data-palette", palette)
  try {
    localStorage.setItem(PALETTE_KEY, palette)
  } catch {
    /* ignore */
  }
}
