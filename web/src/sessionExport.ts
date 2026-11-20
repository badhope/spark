import { exportSession } from "./api"
import { useUiStore } from "./store/uiStore"

function stamp(): string {
  return new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")
}

function saveBlob(content: string, mime: string, filename: string): void {
  const blob = new Blob([content], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/** 会话分享：单文件 HTML（零外链、零 JS），下载后可直接发给别人。 */
export async function downloadShareHtml(): Promise<void> {
  const ui = useUiStore.getState()
  try {
    const data = await exportSession("html")
    saveBlob(data.content, "text/html;charset=utf-8", `spark-share-${stamp()}.html`)
    ui.pushToast("toast.shareExported", "success")
  } catch {
    ui.pushToast("toast.exportFailed", "error")
  }
}

/** 会话导出：走 /api/sessions/export，用浏览器下载 Markdown 文件。 */
export async function downloadTranscript(): Promise<void> {
  const ui = useUiStore.getState()
  try {
    const data = await exportSession("markdown")
    saveBlob(data.content, "text/markdown;charset=utf-8", `spark-${stamp()}.md`)
    ui.pushToast("toast.exported", "success")
  } catch {
    ui.pushToast("toast.exportFailed", "error")
  }
}
