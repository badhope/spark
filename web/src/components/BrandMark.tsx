import { useId } from "react"

import { cls } from "./ui"

/**
 * BrandMark — Spark 品牌徽标：火种色圆角方块、行星轨道与四角星火。
 *
 * 几何以 `scripts/gen_brand_suite.py` 为唯一来源，`brand/mark.svg`、
 * `web/public/favicon.svg` 与这里都从同一组坐标派生。改形之前先改生成器，
 * 再重跑并比对三处输出——图标和 favicon 一旦漂移，品牌就开始腐烂。
 */
export function BrandMark({ size = 28, className }: { size?: number; className?: string }) {
  const uid = `spark-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`
  const bg = `${uid}-bg`
  const glow = `${uid}-glow`
  const core = `${uid}-core`
  return (
    <span
      className={cls("brand-halo inline-flex shrink-0 items-center justify-center", className)}
      style={{
        width: size,
        height: size,
        filter: "drop-shadow(0 1px 1px rgba(0,0,0,.12)) drop-shadow(0 7px 16px rgba(249,115,22,.28))",
      }}
      aria-hidden="true"
    >
      <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden="true">
        <defs>
          <linearGradient id={bg} x1="4" y1="2" x2="62" y2="64" gradientUnits="userSpaceOnUse">
            <stop stopColor="#ffc786" />
            <stop offset="0.42" stopColor="#ff7a1a" />
            <stop offset="1" stopColor="#bc3d08" />
          </linearGradient>
          <radialGradient id={glow} cx="0.24" cy="0.12" r="0.95">
            <stop stopColor="#fffaf0" stopOpacity="0.34" />
            <stop offset="0.55" stopColor="#ffffff" stopOpacity="0.04" />
            <stop offset="1" stopColor="#ffffff" stopOpacity="0" />
          </radialGradient>
          <linearGradient id={core} x1="22" y1="8" x2="42" y2="56" gradientUnits="userSpaceOnUse">
            <stop stopColor="#fffdf8" />
            <stop offset="1" stopColor="#ffe3b8" />
          </linearGradient>
        </defs>
        <rect width="64" height="64" rx="15" fill={`url(#${bg})`} />
        <rect width="64" height="64" rx="15" fill={`url(#${glow})`} />
        <rect x="0.8" y="0.8" width="62.4" height="62.4" rx="14.2" stroke="#fff6e8" strokeOpacity="0.18" strokeWidth="1.2" />
        <ellipse
          cx="32"
          cy="32"
          rx="24"
          ry="11.5"
          transform="rotate(-30 32 32)"
          stroke="#fff6e8"
          strokeOpacity="0.42"
          strokeWidth="1.3"
        />
        <circle cx="52.8" cy="20" r="1.6" fill="#fff8ec" fillOpacity="0.92" />
        <g transform="translate(32 32) scale(0.62) translate(-32 -32)">
          <path d="M32 4C33.9 21.2 42.8 30.1 60 32C42.8 33.9 33.9 42.8 32 60C30.1 42.8 21.2 33.9 4 32C21.2 30.1 30.1 21.2 32 4Z" fill={`url(#${core})`} />
        </g>
      </svg>
    </span>
  )
}
