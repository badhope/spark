import { useId, type ReactNode } from "react"
import { cls } from "./ui"

/* ------------------------------------------------------------------ */
/* 轻量可视化基元：不引入图表库，首屏体积不变。                        */
/* 所有图形都用 CSS 变量取色，明暗主题与强调色切换自动跟随。            */
/* ------------------------------------------------------------------ */

export function Sparkline({
  values,
  width = 128,
  height = 34,
  className,
  tone = "accent",
  fill = true,
}: {
  values: number[]
  width?: number
  height?: number
  className?: string
  tone?: "accent" | "ok" | "err"
  fill?: boolean
}) {
  const id = useId().replace(/:/g, "")
  const color = tone === "ok" ? "var(--ok)" : tone === "err" ? "var(--err)" : "var(--accent)"
  const clean = values.length > 1 ? values.map((v) => (Number.isFinite(v) ? Math.max(0, v) : 0)) : [0, 0]
  const max = Math.max(1, ...clean)
  const min = Math.min(...clean)
  const span = Math.max(1, max - min)
  const pad = 3
  const points = clean.map((v, i) => {
    const x = pad + (i / Math.max(1, clean.length - 1)) * (width - pad * 2)
    const y = height - pad - ((v - min) / span) * (height - pad * 2)
    return [x, y] as const
  })
  const line = points.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ")
  const area = `${pad},${height - pad} ${line} ${width - pad},${height - pad}`

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className={cls("overflow-visible", className)} aria-hidden="true">
      <defs>
        <linearGradient id={`spark-fill-${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.24" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {fill && <polygon points={area} fill={`url(#spark-fill-${id})`} />}
      <polyline points={line} fill="none" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      {points.length > 0 && (
        <circle cx={points[points.length - 1][0]} cy={points[points.length - 1][1]} r="2.2" fill={color} />
      )}
    </svg>
  )
}

export function RingGauge({
  value,
  size = 56,
  stroke = 3,
  tone = "accent",
  label,
  className,
}: {
  value: number
  size?: number
  stroke?: number
  tone?: "accent" | "ok" | "err" | "warn"
  label?: ReactNode
  className?: string
}) {
  const pct = Math.min(100, Math.max(0, Number.isFinite(value) ? value : 0))
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const color = tone === "ok" ? "var(--ok)" : tone === "err" ? "var(--err)" : tone === "warn" ? "var(--warn)" : "var(--accent)"
  return (
    <span className={cls("relative inline-flex shrink-0 items-center justify-center", className)} style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="absolute inset-0 -rotate-90" aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--line)" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - pct / 100)}
          style={{ transition: "stroke-dashoffset .7s cubic-bezier(.16,1,.3,1)" }}
        />
      </svg>
      <span className="relative z-10 text-center leading-none">{label ?? <span className="font-mono text-[10px] text-ink2">{Math.round(pct)}%</span>}</span>
    </span>
  )
}

export function MiniBars({
  values,
  height = 30,
  className,
  tone = "accent",
}: {
  values: number[]
  height?: number
  className?: string
  tone?: "accent" | "ok" | "err"
}) {
  const clean = values.length ? values.map((v) => (Number.isFinite(v) ? Math.max(0, v) : 0)) : [0]
  const max = Math.max(1, ...clean)
  const color = tone === "ok" ? "var(--ok)" : tone === "err" ? "var(--err)" : "var(--accent)"
  return (
    <div className={cls("flex items-end gap-[3px]", className)} style={{ height }} aria-hidden="true">
      {clean.map((v, i) => (
        <span
          key={i}
          className="min-w-[2px] flex-1 rounded-[2px] transition-[height] duration-500"
          style={{
            height: `${Math.max(8, (v / max) * 100)}%`,
            background: i === clean.length - 1 ? color : `color-mix(in srgb, ${color} 42%, transparent)`,
          }}
        />
      ))}
    </div>
  )
}

export function StatCard({
  label,
  value,
  hint,
  icon,
  tone = "neutral",
  progress,
  className,
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  icon?: ReactNode
  tone?: "neutral" | "accent" | "ok" | "err" | "warn"
  progress?: number
  className?: string
}) {
  const toneClass =
    tone === "accent" ? "text-accent" : tone === "ok" ? "text-ok" : tone === "err" ? "text-err" : tone === "warn" ? "text-warn" : "text-ink"
  const meter = progress == null ? null : tone === "ok" ? "ok" : tone === "err" ? "err" : ""
  return (
    <div className={cls("stat-card group", className)}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="stat-label">{label}</div>
          <div className={cls("stat-value mt-1", toneClass)}>{value}</div>
        </div>
        {icon && <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-panel text-ink3 transition-colors group-hover:text-accent">{icon}</span>}
      </div>
      {progress != null && (
        <div className="meter-track mt-2">
          <div className={cls("meter-fill", meter)} style={{ width: `${Math.min(100, Math.max(0, progress))}%` }} />
        </div>
      )}
      {hint && <div className="stat-hint mt-1.5 truncate">{hint}</div>}
    </div>
  )
}
