import { cls } from "./ui"

/**
 * BrandMark — Spark 品牌徽标：渐变圆角方块 + 四角星火符号。
 *
 * 几何与 `brand/mark.svg` / `web/public/favicon.svg` 完全一致（同一渐变与同一
 * 路径），任何一处修改都要三处同步——图标和 favicon 一旦漂移，品牌就开始腐烂。
 */
export function BrandMark({ size = 28, className }: { size?: number; className?: string }) {
  const id = `spark-bg-${size}`
  return (
    <span
      className={cls("brand-halo inline-flex shrink-0 items-center justify-center", className)}
      style={{
        width: size,
        height: size,
        borderRadius: size * 0.24,
        background: "linear-gradient(135deg, #ffc48c 0%, #f97316 45%, #c2410c 100%)",
        boxShadow: "0 1px 2px rgba(0,0,0,.12), 0 6px 16px -6px rgba(249,115,22,.55)",
      }}
      aria-hidden="true"
    >
      <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden="true">
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="64" y2="64" gradientUnits="userSpaceOnUse">
            <stop stopColor="#ffc48c" />
            <stop offset="0.45" stopColor="#f97316" />
            <stop offset="1" stopColor="#c2410c" />
          </linearGradient>
        </defs>
        <g transform="translate(32 32) scale(0.74) translate(-32 -32)">
          <path
            d="M32 4C33.9 21.2 42.8 30.1 60 32C42.8 33.9 33.9 42.8 32 60C30.1 42.8 21.2 33.9 4 32C21.2 30.1 30.1 21.2 32 4Z"
            fill="white"
          />
        </g>
      </svg>
    </span>
  )
}
