import { memo, useEffect, useMemo, useState } from "react"
import { ReactFlow, Background, Controls, type Node, type Edge } from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import { IconButton } from "./ui"
import { IconX, IconSpark } from "./icons"
import { useChatStore } from "../store/chatStore"
import { fetchWorkflowGraph } from "../api"
import type { WorkflowGraphState } from "../types"
import { useUiStore } from "../store/uiStore"
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

export const ExecutionGraph = memo(function ExecutionGraph() {
  const open = useUiStore((s) => s.graphOpen)
  const close = useUiStore((s) => s.closeGraph)
  const lang = useUiStore((s) => s.lang)
  const live = useChatStore((s) => s.turnGraph)
  const streaming = useChatStore((s) => s.streaming)
  const [remote, setRemote] = useState<{ name: string; nodes: ExecutionNode[]; edges: ExecutionEdge[] } | null>(null)

  const liveNodes = useMemo<ExecutionNode[]>(
    () => live.nodes.map((n) => ({ ...n, label: n.kind === "root" || n.kind === "result" ? `graph.kind.${n.kind}` : n.label })),
    [live.nodes],
  )
  const liveEdges = useMemo<ExecutionEdge[]>(() => live.edges.map((e) => ({ ...e })), [live.edges])
  const hasLive = liveNodes.length > 0

  useEffect(() => {
    if (!open || hasLive) return
    let cancelled = false
    fetchWorkflowGraph()
      .then((data: WorkflowGraphState) => {
        if (cancelled) return
        const status = (raw: string): ExecutionNode["status"] => {
          if (raw === "completed" || raw === "done") return "done"
          if (raw === "failed" || raw === "blocked" || raw === "cancelled") return "error"
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
        setRemote({ name: data.name, nodes, edges })
      })
      .catch(() => {
        if (!cancelled) setRemote(null)
      })
    return () => {
      cancelled = true
    }
  }, [open, hasLive])

  const sourceNodes = hasLive ? liveNodes : remote?.nodes ?? []
  const sourceEdges = hasLive ? liveEdges : remote?.edges ?? []
  const hasGraph = sourceNodes.length > 0
  const { nodes, edges } = useMemo(
    () => buildGraph(sourceNodes, sourceEdges, lang),
    [sourceNodes, sourceEdges, lang],
  )

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-[2px]" onClick={close}>
      <div
        className="anim-fade flex h-[min(82vh,640px)] w-[min(94vw,960px)] flex-col overflow-hidden rounded-2xl border border-line bg-canvas shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={t("graph.title", lang)}
      >
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <IconSpark size={15} className="text-accent" />
          <span className="text-sm font-semibold text-ink">{t("graph.title", lang)}</span>
          <span className="ml-1 text-[10.5px] text-ink3">
            {hasGraph
              ? hasLive
                ? streaming
                  ? t("graph.liveRunning", lang)
                  : t("graph.liveDone", lang)
                : t("graph.workflowDone", lang)
              : t("graph.empty", lang)}
          </span>
          <IconButton title={t("common.close", lang)} onClick={close} className="ml-auto">
            <IconX size={15} />
          </IconButton>
        </div>
        <div className="min-h-0 flex-1">
          {!hasGraph ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
              <IconSpark size={28} className="text-ink3" />
              <p className="max-w-xs text-[12.5px] leading-relaxed text-ink3">{t("graph.emptyHint", lang)}</p>
            </div>
          ) : (
            <ReactFlow nodes={nodes} edges={edges} fitView proOptions={{ hideAttribution: true }}>
              <Background gap={20} color="var(--line)" />
              <Controls showInteractive={false} />
            </ReactFlow>
          )}
        </div>
      </div>
    </div>
  )
})
