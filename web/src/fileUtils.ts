/** 附件大小与类型的统一展示，避免输入框和消息气泡各写一套。 */
export function formatFileSize(bytes: number | undefined): string {
  if (!bytes || bytes < 0) return ""
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function attachmentKind(name: string, mime = ""): string {
  const ext = name.split(".").pop()?.toLowerCase() || ""
  if (mime === "application/pdf" || ext === "pdf") return "PDF"
  if (["doc", "docx", "odt", "rtf"].includes(ext) || mime.includes("word")) return "DOC"
  if (["xls", "xlsx", "ods", "csv"].includes(ext) || mime.includes("sheet")) return "SHEET"
  if (["ppt", "pptx", "odp"].includes(ext) || mime.includes("presentation")) return "SLIDE"
  if (mime.startsWith("text/") || ["txt", "md", "json", "yaml", "yml", "toml", "log", "py", "ts", "tsx", "js", "jsx", "css", "html", "sh", "sql"].includes(ext)) return "TEXT"
  return "BIN"
}
