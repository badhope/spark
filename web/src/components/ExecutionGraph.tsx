import { memo, useEffect, useMemo, useState } from "react"
import { ReactFlow, Background, Controls, useReactFlow, type Node, type Edge } from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import { IconButton } from "./ui"
import { IconX, IconSpark, IconDownload, IconCopy } from "./icons"
import { useChatStore } from "../store/chatStore"
import { fetchReplays, fetchReplayTurn, fetchWorkflowGraph, type ReplayRecord } from "../api"
import { useUiStore } from "../store/uiStore"
import { useSessionStore } from "../store/sessionStore"
import { t, type Lang, type TranslationKey } from "../i18n"

/* ------------------------------------------------------------------ */
/* 执行图：agent 执行轨迹 / 子代理树可视化。                             */
/*                                                                     */
/* 数据来自 chatStore.turnGraph —— 由 SSE 事件流（tool_start /          */
/* tool_end / subtask_* / turn_end）实时累积，事件流是唯一事实来源。    */
/* 没有可绘制的轨迹时（新会话、尚未运行过一轮），显示诚实空状态，      */
/* 不再用静态样例冒充执行结果。                                        */
/* ------------------------------------------------------------------ */

export interface ExecutionNode extends Record<string, unknown> {
  id: string
  label: string
  kind: "root" | "agent" | "tool" | "result"
  status: "running" | "done" | "error" | "pending"
}

export interface ExecutionEdge {
  from: string
  to: string
  label?: string
}

const STATUS_COLOR: Record<ExecutionNode["status"], string> = {
  running: "var(--accent)",
  done: "#3fb950",
  error: "#f85149",
  pending: "#8b949e",
}

const KIND_LABEL: Record<ExecutionNode["kind"], TranslationKey> = {
  root: "graph.kind.root",
  agent: "graph.kind.agent",
  tool: "graph.kind.tool",
  result: "graph.kind.result",
}

function nodeColor(kind: ExecutionNode["kind"]): string {
  if (kind === "root") return "#1f6feb"
  if (kind === "agent") return "#8957e5"
  if (kind === "tool") return "#2d76a4"
  return "#238636"
}

export function buildGraph(nodes: ExecutionNode[], edges: ExecutionEdge[], lang: Lang): {
  nodes: Node[]
  edges: Edge[]
} {
  // Simple layered layout: one row per kind, each row centred on the widest one
  // so a single-tool turn does not draw three boxes glued to the left edge.
  // Depth-aware rows: a tool owned by a sub-agent sits one row below that
  // sub-agent, so read_file/glob are visibly nested instead of looking like
  // siblings of the `task` call.
  const childOf = new Map<string, string>()
  for (const e of edges) childOf.set(e.to, e.from)
  const rowOf = (id: string, seen = 0): number => {
    if (seen > 8) return 2
    const parent = childOf.get(id)
    if (!parent) return 1
    return rowOf(parent, seen + 1) + 1
  }
  const perRow: Record<number, ExecutionNode[]> = {}
  let maxRow = 0
  for (const n of nodes) {
    if (n.kind === "result") continue
    const row = n.kind === "root" ? 0 : rowOf(n.id)
    maxRow = Math.max(maxRow, row)
    ;(perRow[row] ||= []).push(n)
  }
  // "Result" sits directly below the deepest node instead of at a fixed row,
  // otherwise a flat turn leaves a large empty band above it.
  for (const n of nodes.filter((x) => x.kind === "result")) {
    ;(perRow[maxRow + 1] ||= []).push(n)
  }
  const COL_W = 240
  const widest = Math.max(1, ...Object.values(perRow).map((r) => r.length))
  const pos: Record<string, { x: number; y: number }> = {}
  for (const [rowText, rowNodes] of Object.entries(perRow)) {
    const row = Number(rowText)
    const offset = ((widest - rowNodes.length) * COL_W) / 2
    rowNodes.forEach((n, i) => {
      pos[n.id] = { x: offset + i * COL_W, y: row * 120 }
    })
  }
  const STATUS_TEXT: Record<ExecutionNode["status"], string> = {
    running: t("graph.status.running", lang),
    done: t("graph.status.done", lang),
    error: t("graph.status.error", lang),
    pending: t("graph.status.pending", lang),
  }
  const rfNodes: Node[] = nodes.map((n) => ({
    id: n.id,
    position: pos[n.id] ?? { x: 0, y: 0 },
    data: {
      execution: n,
      label: (
        <div style={{ borderLeft: `3px solid ${nodeColor(n.kind)}`, paddingLeft: 8 }}>
          <div style={{ fontSize: 12, fontWeight: 600 }}>{t(n.label as TranslationKey, lang)}</div>
          <div style={{ fontSize: 10, opacity: 0.75 }}>
            {t(KIND_LABEL[n.kind], lang)} · <span style={{ color: STATUS_COLOR[n.status] }}>{STATUS_TEXT[n.status]}</span>
          </div>
        </div>
      ),
    },
    style: {
      background: "var(--panel)",
      color: "var(--ink)",
      border: "1px solid var(--line)",
      borderRadius: 8,
      padding: "6px 10px",
      width: 200,
    },
  }))
  const rfEdges: Edge[] = edges.map((e, i) => ({
    id: `e${i}`,
    source: e.from,
    target: e.to,
    label: e.label ? t(e.label as TranslationKey, lang) : undefined,
    animated: true,
    style: { stroke: "var(--ink3)" },
    markerEnd: { type: "arrowclosed" },
  }))
  return { nodes: rfNodes, edges: rfEdges }
}

/** 节点是流式增量加入的，ReactFlow 的 fitView 只在挂载时跑一次。
 * 新增 tool/result 节点后视口不会跟着变，用户只能看到最初的 root 节点。
 * 这里在图签名变化后重新 fit，保证一屏能看到完整链路；用户手动缩放后
 * 再次收到新节点时也会重新适配，这是执行图“跟随本轮”的预期行为。 */
function AutoFitView({ signature }: { signature: string }) {
  const { fitView } = useReactFlow()
  useEffect(() => {
    const timer = window.setTimeout(() => {
      void fitView({ padding: 0.16, duration: 220 })
    }, 120)
    return () => window.clearTimeout(timer)
  }, [signature, fitView])
  return null
}

/** 把 replay 里的一轮工具轨迹还原成执行图。
 *
 * replay 是 turn 级持久化合同，刷新/重启后仍可读；实时 SSE 只负责正在
 * 跑的这一轮。历史记录没有子代理层级，但工具名、顺序、成败都是真实的，
 * 不再用“尚未执行”掩盖已经发生过的回合。
 */
function graphFromReplay(record: ReplayRecord): {
  nodes: ExecutionNode[]
  edges: ExecutionEdge[]
  status: "done" | "error"
} {
  const status: "done" | "error" = record.turn.status === "ok" ? "done" : "error"
  const toolCalls = Array.isArray(record.turn.tool_calls) ? record.turn.tool_calls : []
  const nodes: ExecutionNode[] = [
    { id: "root", label: "graph.kind.root", kind: "root", status },
  ]
  const edges: ExecutionEdge[] = []
  toolCalls.forEach((call, index) => {
    const id = `tool-${call.id || index}`
    nodes.push({
      id,
      label: call.name || "tool",
      kind: "tool",
      status: call.ok === true ? "done" : call.ok === false ? "error" : "pending",
    })
    edges.push({ from: "root", to: id })
  })
  nodes.push({ id: "result", label: "graph.kind.result", kind: "result", status })
  for (const node of nodes) {
    if (node.kind === "tool") edges.push({ from: node.id, to: "result" })
  }
  edges.push({ from: "root", to: "result" })
  return { nodes, edges, status }
}

function escapeXml(value: string): string {
  return value.replace(/[<>&"']/g, (char) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", "\"": "&quot;", "'": "&apos;" })[char] as string)
}

/** 不引第三方截图库：直接按 ReactFlow 的布局坐标生成 SVG，
 * 导出的是可缩放图片，不依赖当前浏览器窗口大小。 */
function exportGraphSvg(nodes: Node[], edges: Edge[], sourceNodes: ExecutionNode[], lang: Lang): string {
  const W = 220
  const H = 64
  let maxX = 0
  let maxY = 0
  for (const n of nodes) {
    maxX = Math.max(maxX, n.position.x + W)
    maxY = Math.max(maxY, n.position.y + H)
  }
  const width = Math.max(520, maxX + 40)
  const height = Math.max(220, maxY + 80)
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const sourceById = new Map(sourceNodes.map((n) => [n.id, n]))
  const edgeSvg = edges
    .map((e) => {
      const from = byId.get(e.source)
      const to = byId.get(e.target)
      if (!from || !to) return ""
      const x1 = from.position.x + W / 2
      const y1 = from.position.y + H
      const x2 = to.position.x + W / 2
      const y2 = to.position.y
      const label = e.label ? `<text x="${(x1 + x2) / 2}" y="${(y1 + y2) / 2 - 4}" text-anchor="middle" font-size="10" fill="#64748b">${escapeXml(String(e.label))}</text>` : ""
      return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#94a3b8" stroke-width="1.4"/>${label}`
    })
    .join("")
  const nodeSvg = nodes
    .map((n) => {
      const source = sourceById.get(n.id)
      const kind = source?.kind ?? "tool"
      const label = source ? t(source.label as TranslationKey, lang) : n.id
      const status = source ? t(`graph.status.${source.status}` as TranslationKey, lang) : ""
      const x = n.position.x
      const y = n.position.y
      return `<g>
        <rect x="${x}" y="${y}" width="${W}" height="${H}" rx="10" fill="#ffffff" stroke="#d7dbe2"/>
        <rect x="${x}" y="${y}" width="5" height="${H}" rx="2" fill="${nodeColor(kind as ExecutionNode["kind"])}"/>
        <text x="${x + 16}" y="${y + 25}" font-size="13" font-weight="600" fill="#111827">${escapeXml(String(label))}</text>
        <text x="${x + 16}" y="${y + 44}" font-size="10" fill="#64748b">${escapeXml(`${t(KIND_LABEL[kind as ExecutionNode["kind"]], lang)} · ${status}`)}</text>
      </g>`
    })
    .join("")
  const legend = (["root", "agent", "tool", "result"] as const)
    .map((kind, i) => `<g><circle cx="${28 + i * 110}" cy="${height - 24}" r="5" fill="${nodeColor(kind)}"/><text x="${40 + i * 110}" y="${height - 20}" font-size="11" fill="#475569">${escapeXml(t(KIND_LABEL[kind], lang))}</text></g>`)
    .join("")
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><rect width="${width}" height="${height}" fill="#f8fafc"/>${edgeSvg}${nodeSvg}${legend}</svg>`
}

interface RemoteGraph {
  name: string
  nodes: ExecutionNode[]
  edges: ExecutionEdge[]
  source: "replay" | "workflow"
  status: "done" | "error"
}

function GraphLegend({ lang }: { lang: Lang }) {
  return (
    <div className="pointer-events-none absolute bottom-3 left-3 flex flex-wrap items-center gap-3 rounded-lg border border-line bg-panel/90 px-3 py-1.5 text-[10px] text-ink2 shadow-sm backdrop-blur">
      {(["root", "agent", "tool", "result"] as const).map((kind) => (
        <span key={kind} className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full" style={{ background: nodeColor(kind) }} />
          {t(KIND_LABEL[kind], lang)}
        </span>
      ))}
      <span className="text-ink3">{t("graph.legendHint", lang)}</span>
    </div>
  )
}

function GraphNodeDetails({
  node,
  edges,
  nodes,
  lang,
  onClose,
}: {
  node: ExecutionNode
  edges: ExecutionEdge[]
  nodes: ExecutionNode[]
  lang: Lang
  onClose: () => void
}) {
  const byId = new Map(nodes.map((n) => [n.id, n]))
  return (
    <div className="anim-fade absolute right-3 top-3 z-20 w-[min(84vw,280px)] rounded-xl border border-line bg-panel/95 p-3 text-xs shadow-[var(--shadow)] backdrop-blur">
      <div className="mb-2 flex items-start gap-2">
        <span className="mt-0.5 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: nodeColor(node.kind) }} />
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold text-ink">{t(node.label as TranslationKey, lang)}</div>
          <div className="mt-0.5 text-[10px] text-ink3">
            {t(KIND_LABEL[node.kind], lang)} · {t(`graph.status.${node.status}` as TranslationKey, lang)}
          </div>
        </div>
        <button type="button" className="rounded p-1 text-ink3 hover:bg-hover hover:text-ink" onClick={onClose} aria-label={t("common.close", lang)}>
          <IconX size={12} />
        </button>
      </div>
      <div className="mb-1.5 font-mono text-[10px] text-ink3">{node.id}</div>
      {edges.length === 0 ? (
        <div className="text-[11px] text-ink3">{t("graph.noEdges", lang)}</div>
      ) : (
        <div className="space-y-1.5">
          {edges.map((e, i) => {
            const from = byId.get(e.from)
            const to = byId.get(e.to)
            return (
              <div key={`${e.from}-${e.to}-${i}`} className="rounded border border-line bg-canvas px-2 py-1.5 text-[10.5px] text-ink2">
                <div className="break-words">
                  {from ? t(from.label as TranslationKey, lang) : e.from}
                  <span className="mx-1 text-ink3">→</span>
                  {to ? t(to.label as TranslationKey, lang) : e.to}
                </div>
                {e.label && <div className="mt-0.5 text-[10px] text-ink3">{t(e.label as TranslationKey, lang)}</div>}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export const ExecutionGraph = memo(function ExecutionGraph({ embedded = false }: { embedded?: boolean }) {
  const storeOpen = useUiStore((s) => s.graphOpen)
  const open = embedded || storeOpen
  const close = useUiStore((s) => s.closeGraph)
  const lang = useUiStore((s) => s.lang)
  const sessionId = useSessionStore((s) => s.status?.session_id ?? "")
  const live = useChatStore((s) => s.turnGraph)
  const streaming = useChatStore((s) => s.streaming)
  const [remote, setRemote] = useState<RemoteGraph | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [graphNotice, setGraphNotice] = useState("")

  const liveNodes = useMemo<ExecutionNode[]>(
    () => live.nodes.map((n) => ({ ...n, label: n.kind === "root" || n.kind === "result" ? `graph.kind.${n.kind}` : n.label })),
    [live.nodes],
  )
  const liveEdges = useMemo<ExecutionEdge[]>(() => live.edges.map((e) => ({ ...e })), [live.edges])
  // turnGraph 是全局单槽位；只有它属于当前会话时才可用，否则切换会话会
  // 把上一个会话的工具链画到当前会话下面。
  const hasLive = liveNodes.length > 0 && live.sessionId === sessionId

  useEffect(() => {
    if (!open || hasLive || !sessionId) return
    let cancelled = false
    const load = async () => {
      setRemote(null)
      if (sessionId) {
        try {
          const replays = await fetchReplays()
          if (cancelled || replays.session_id !== sessionId || !replays.turns.length) {
            // fall through to workflow state (or empty)
          } else {
            const latest = replays.turns[replays.turns.length - 1]
            const record = await fetchReplayTurn(latest.index)
            if (!cancelled && record.session_id === sessionId) {
              const graph = graphFromReplay(record)
              setRemote({
                name: record.turn.prompt || `#${latest.index + 1}`,
                ...graph,
                source: "replay",
              })
              return
            }
          }
        } catch {
          // replay disabled/未落盘：继续尝试 workflow 图，失败则空态
        }
      }
      try {
        const data = await fetchWorkflowGraph()
        if (cancelled) return
        const status = (raw: string): ExecutionNode["status"] => {
          if (raw === "completed" || raw === "done") return "done"
          if (raw === "failed" || raw === "blocked" || raw === "cancelled" || raw === "budget-exceeded") return "error"
          if (raw === "running") return "running"
          return "pending"
        }
        const nodes: ExecutionNode[] = data.graph.nodes.map((n) => ({
          id: n.id,
          label: `${n.id} · ${n.role}`,
          kind: "agent" as const,
          status: status(n.status),
        }))
        const edges: ExecutionEdge[] = data.graph.edges.map((e) => ({ from: e.from, to: e.to }))
        const rootId = "workflow-root"
        const targets = new Set(data.graph.edges.map((e) => e.to))
        nodes.unshift({ id: rootId, label: data.name, kind: "root", status: "done" })
        for (const node of data.graph.nodes) {
          if (!targets.has(node.id)) edges.push({ from: rootId, to: node.id })
        }
        setRemote({ name: data.name, nodes, edges, source: "workflow", status: "done" })
      } catch {
        if (!cancelled) setRemote(null)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [open, hasLive, sessionId])

  const sourceNodes = useMemo(
    () => (hasLive ? liveNodes : remote?.nodes ?? []),
    [hasLive, liveNodes, remote],
  )
  const sourceEdges = useMemo(
    () => (hasLive ? liveEdges : remote?.edges ?? []),
    [hasLive, liveEdges, remote],
  )
  const hasGraph = sourceNodes.length > 0
  const selectedNode = selectedId ? sourceNodes.find((n) => n.id === selectedId) : undefined
  const relatedEdges = selectedId ? sourceEdges.filter((e) => e.from === selectedId || e.to === selectedId) : []
  const flashNotice = (text: string) => {
    setGraphNotice(text)
    window.setTimeout(() => setGraphNotice(""), 1800)
  }
  const downloadGraph = () => {
    const svg = exportGraphSvg(nodes, edges, sourceNodes, lang)
    const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `spark-execution-${Date.now()}.svg`
    a.style.display = "none"
    document.body.appendChild(a)
    a.click()
    a.remove()
    // 立即 revoke 会让部分浏览器取消尚未开始的下载；延迟回收。
    window.setTimeout(() => URL.revokeObjectURL(url), 1500)
    flashNotice(t("graph.exported", lang))
  }
  const copyGraph = async () => {
    const payload = JSON.stringify({ nodes: sourceNodes, edges: sourceEdges }, null, 2)
    try {
      await navigator.clipboard.writeText(payload)
      flashNotice(t("graph.copied", lang))
    } catch {
      flashNotice(t("graph.copyFailed", lang))
    }
  }
  const { nodes, edges } = useMemo(
    () => buildGraph(sourceNodes, sourceEdges, lang),
    [sourceNodes, sourceEdges, lang],
  )
  const graphSignature = useMemo(
    () => `${nodes.map((n) => n.id).join("|")}#${edges.length}`,
    [nodes, edges],
  )
  const remoteStatus =
    remote?.source === "replay"
      ? t(remote.status === "error" ? "graph.historyError" : "graph.historyDone", lang)
      : t("graph.workflowDone", lang)
  const statusText = hasGraph
    ? hasLive
      ? streaming
        ? t("graph.liveRunning", lang)
        : t("graph.liveDone", lang)
      : remoteStatus
    : t("graph.empty", lang)

  if (!open) return null

  if (embedded) {
    return (
      <div className="flex min-h-0 flex-1 flex-col bg-canvas">
        <div className="flex items-center gap-2 border-b border-line px-3 py-2">
          <IconSpark size={13} className="text-accent" />
          <span className="text-[12px] font-semibold text-ink">{t("graph.title", lang)}</span>
          <span className="ml-1 truncate text-[10px] text-ink3">
            {statusText}
          </span>
          {graphNotice && <span className="text-[10px] text-ok">{graphNotice}</span>}
          {hasGraph && (
            <div className="ml-auto flex items-center gap-0.5">
              <IconButton title={t("graph.copy", lang)} aria-label={t("graph.copy", lang)} onClick={() => void copyGraph()}>
                <IconCopy size={13} />
              </IconButton>
              <IconButton title={t("graph.export", lang)} aria-label={t("graph.export", lang)} onClick={downloadGraph}>
                <IconDownload size={13} />
              </IconButton>
            </div>
          )}
        </div>
        <div className="relative min-h-0 flex-1">
          {!hasGraph ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center">
              <IconSpark size={24} className="text-ink3" />
              <p className="max-w-xs text-[11.5px] leading-relaxed text-ink3">{t("graph.emptyHint", lang)}</p>
            </div>
          ) : (
            <>
              <ReactFlow
                nodes={nodes}
                edges={edges}
                fitView
                fitViewOptions={{ padding: 0.16 }}
                proOptions={{ hideAttribution: true }}
                onNodeClick={(_, node) => setSelectedId(node.id)}
                onPaneClick={() => setSelectedId(null)}
              >
                <AutoFitView signature={graphSignature} />
                <Background gap={20} color="var(--line)" />
                <Controls showInteractive={false} />
              </ReactFlow>
              <GraphLegend lang={lang} />
              {selectedNode && (
                <GraphNodeDetails
                  node={selectedNode}
                  edges={relatedEdges}
                  nodes={sourceNodes}
                  lang={lang}
                  onClose={() => setSelectedId(null)}
                />
              )}
            </>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-[2px]" onClick={close}>
      <div
        className="anim-fade flex h-[min(82vh,640px)] w-[min(94vw,960px)] flex-col overflow-hidden rounded-2xl border border-line bg-canvas shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={t("graph.title", lang)}
      >
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <IconSpark size={15} className="text-accent" />
          <span className="text-sm font-semibold text-ink">{t("graph.title", lang)}</span>
          <span className="ml-1 text-[10.5px] text-ink3">
            {statusText}
          </span>
          {graphNotice && <span className="text-[10px] text-ok">{graphNotice}</span>}
          {hasGraph && (
            <div className="ml-auto flex items-center gap-0.5">
              <IconButton title={t("graph.copy", lang)} aria-label={t("graph.copy", lang)} onClick={() => void copyGraph()}>
                <IconCopy size={14} />
              </IconButton>
              <IconButton title={t("graph.export", lang)} aria-label={t("graph.export", lang)} onClick={downloadGraph}>
                <IconDownload size={14} />
              </IconButton>
              <IconButton title={t("common.close", lang)} onClick={close}>
                <IconX size={15} />
              </IconButton>
            </div>
          )}
          {!hasGraph && (
            <IconButton title={t("common.close", lang)} onClick={close} className="ml-auto">
              <IconX size={15} />
            </IconButton>
          )}
        </div>
        <div className="relative min-h-0 flex-1">
          {!hasGraph ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
              <IconSpark size={28} className="text-ink3" />
              <p className="max-w-xs text-[12.5px] leading-relaxed text-ink3">{t("graph.emptyHint", lang)}</p>
            </div>
          ) : (
            <>
              <ReactFlow
                nodes={nodes}
                edges={edges}
                fitView
                fitViewOptions={{ padding: 0.16 }}
                proOptions={{ hideAttribution: true }}
                onNodeClick={(_, node) => setSelectedId(node.id)}
                onPaneClick={() => setSelectedId(null)}
              >
                <AutoFitView signature={graphSignature} />
                <Background gap={20} color="var(--line)" />
                <Controls showInteractive={false} />
              </ReactFlow>
              <GraphLegend lang={lang} />
              {selectedNode && (
                <GraphNodeDetails
                  node={selectedNode}
                  edges={relatedEdges}
                  nodes={sourceNodes}
                  lang={lang}
                  onClose={() => setSelectedId(null)}
                />
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
})
