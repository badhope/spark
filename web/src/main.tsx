import React, { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "./index.css"
import App from "./App"
import { initTheme } from "./theme"
import { reportClientError } from "./api"

initTheme()

/* ------------------------------------------------------------------ */
/* 全局错误上报                                                         */
/* 浏览器里的异常只存在于浏览器里：没有这条通道，"页面白了"就只能靠      */
/* 用户口述。三个来源都要接：未捕获异常、未处理 Promise、React 渲染崩溃。 */
/* ------------------------------------------------------------------ */

function installGlobalErrorHooks() {
  window.addEventListener("error", (event) => {
    // 资源加载失败（img/script）没有 error 对象，单独归类，方便区分
    // "代码崩了" 和 "某个 chunk 404"。
    const target = event.target as HTMLElement | null
    if (target && target.tagName && target !== (window as unknown as HTMLElement)) {
      const el = target as HTMLImageElement & HTMLScriptElement
      reportClientError({
        kind: "resource",
        message: `${target.tagName} failed to load: ${el.src ?? ""}`,
        where: "window.error:resource",
      })
      return
    }
    reportClientError({
      kind: "uncaught",
      message: event.message || "unknown error",
      stack: event.error?.stack ?? "",
      where: `${event.filename ?? ""}:${event.lineno ?? 0}:${event.colno ?? 0}`,
    })
  })

  window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason
    reportClientError({
      kind: "unhandledrejection",
      message: reason instanceof Error ? reason.message : String(reason),
      stack: reason instanceof Error ? (reason.stack ?? "") : "",
      where: "window.unhandledrejection",
    })
  })
}

installGlobalErrorHooks()

interface BoundaryState {
  error: Error | null
  info: string
  showDetail: boolean
}

class RootErrorBoundary extends React.Component<{ children: React.ReactNode }, BoundaryState> {
  state: BoundaryState = { error: null, info: "", showDetail: false }

  static getDerivedStateFromError(error: Error): Partial<BoundaryState> {
    return { error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // 组件栈是定位"哪个组件炸了"的唯一线索，必须一起上报。
    reportClientError({
      kind: "react",
      message: error.message,
      stack: `${error.stack ?? ""}\n--- componentStack ---\n${info.componentStack ?? ""}`,
      where: "RootErrorBoundary",
    })
    this.setState({ info: info.componentStack ?? "" })
  }

  render() {
    if (!this.state.error) return this.props.children

    const { error, info, showDetail } = this.state
    return (
      <div className="flex h-full items-center justify-center bg-canvas p-6 text-ink">
        <div className="anim-fade-up w-full max-w-lg space-y-4">
          <div className="space-y-1.5 text-center">
            <div className="text-2xl">⚠️</div>
            <p className="text-[14px] font-semibold">界面渲染出错</p>
            <p className="text-[12px] leading-relaxed text-ink3">
              已自动上报到服务端日志。可以先刷新重试；如果反复出现，把下面的详情发出来。
            </p>
          </div>

          <div className="surface space-y-2 p-3">
            <p className="font-mono text-[11.5px] break-words text-err">{error.message || String(error)}</p>
            <button
              type="button"
              className="text-[11px] text-accent underline underline-offset-2"
              onClick={() => this.setState({ showDetail: !showDetail })}
            >
              {showDetail ? "收起详情" : "展开详情"}
            </button>
            {showDetail && (
              <pre className="max-h-56 overflow-auto rounded-md bg-code p-2 font-mono text-[10.5px] leading-relaxed whitespace-pre-wrap text-ink3">
                {error.stack || "(no stack)"}
                {info ? `\n--- componentStack ---${info}` : ""}
              </pre>
            )}
          </div>

          <div className="flex justify-center gap-2">
            <button
              className="rounded-[9px] bg-accent px-4 py-1.5 text-[13px] font-medium text-on-accent"
              onClick={() => window.location.reload()}
            >
              刷新重试
            </button>
            <button
              className="rounded-[9px] border border-line px-4 py-1.5 text-[13px] text-ink2"
              onClick={() => this.setState({ error: null, info: "", showDetail: false })}
            >
              尝试继续
            </button>
          </div>
        </div>
      </div>
    )
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RootErrorBoundary>
      <App />
    </RootErrorBoundary>
  </StrictMode>,
)
