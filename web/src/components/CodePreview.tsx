import { memo } from "react"
import hljs from "highlight.js/lib/common"

/** highlight.js 整段高亮后按行切分渲染。
 *
 * 跨行 token（块注释/多行字符串）会生成跨行的 `<span>`；直接按 "\n" 切 HTML
 * 会让第二行起丢失颜色或错色。这里维护未闭合 span 栈：每行前缀重开、行尾
 * 补齐闭合，保证每一行都是独立合法的 HTML 片段。 */
export function splitHighlightedLines(html: string): string[] {
  const out: string[] = []
  const openStack: string[] = []
  for (const rawLine of html.split("\n")) {
    const prefix = openStack.join("")
    for (const m of rawLine.matchAll(/<span[^>]*>/g)) openStack.push(m[0])
    const closeCount = (rawLine.match(/<\/span>/g) ?? []).length
    for (let i = 0; i < closeCount; i++) {
      if (openStack.length) openStack.pop()
    }
    out.push(prefix + rawLine + "</span>".repeat(openStack.length))
  }
  return out
}

/** 行号 + 语言高亮的只读代码预览；文件树与右侧面板共用同一实现。 */
export const CodePreview = memo(function CodePreview({
  code,
  name,
  compact = false,
}: {
  code: string
  name: string
  compact?: boolean
}) {
  const ext = name.includes(".") ? name.split(".").pop()!.toLowerCase() : ""
  let html: string
  try {
    const lang = ext && hljs.getLanguage(ext) ? ext : "plaintext"
    html = hljs.highlight(code, { language: lang, ignoreIllegals: true }).value
  } catch {
    html = code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  }
  const lines = splitHighlightedLines(html)
  return (
    <div className={`overflow-auto font-mono ${compact ? "text-[11.5px]" : "text-[12px]"} leading-[1.55]`}>
      <table className="w-full border-collapse">
        <tbody>
          {lines.map((line, i) => (
            <tr key={i} className="align-top">
              <td className={`${compact ? "w-8 pr-2.5" : "w-9 pr-3"} text-right text-ink3 select-none`}>{i + 1}</td>
              <td className={compact ? "pr-3" : "pr-4"}>
                <span dangerouslySetInnerHTML={{ __html: line }} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
})
