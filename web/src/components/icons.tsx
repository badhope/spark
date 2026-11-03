import type { ReactNode, SVGProps } from "react"

type P = SVGProps<SVGSVGElement> & { size?: number }

function base({ size = 16, ...rest }: P, children: ReactNode) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  )
}

export const IconPlus = (p: P) => base(p, <path d="M12 5v14M5 12h14" />)
export const IconPencil = (p: P) =>
  base(p, (
    <>
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
    </>
  ))
export const IconRefresh = (p: P) =>
  base(p, (
    <>
      <path d="M3 12a9 9 0 0 1 15.5-6.2L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-15.5 6.2L3 16" />
      <path d="M3 21v-5h5" />
    </>
  ))
export const IconFork = (p: P) =>
  base(p, (
    <>
      <circle cx="6" cy="5" r="2.5" />
      <circle cx="18" cy="5" r="2.5" />
      <circle cx="12" cy="19" r="2.5" />
      <path d="M6 7.5v2a3 3 0 0 0 3 3h6a3 3 0 0 0 3-3v-2M12 12.5v4" />
    </>
  ))
export const IconStethoscope = (p: P) =>
  base(p, (
    <>
      <path d="M5 3v6a4 4 0 0 0 8 0V3" />
      <path d="M9 13v3a5 5 0 0 0 10 0v-2" />
      <circle cx="19" cy="11" r="2" />
    </>
  ))
export const IconWrench = (p: P) =>
  base(p, (
    <>
      <path d="M14.7 6.3a4 4 0 1 0 5 5L21 21l-4-4-9.7-9.7a4 4 0 0 1 5-5l2.4 2.4Z" />
    </>
  ))
export const IconSend = (p: P) => base(p, <path d="M12 19V5M5 12l7-7 7 7" />)
export const IconStop = (p: P) => base(p, <rect x="7" y="7" width="10" height="10" rx="2" />)
export const IconCopy = (p: P) =>
  base(p, (
    <>
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M5 15V5a2 2 0 0 1 2-2h10" />
    </>
  ))
export const IconCheck = (p: P) => base(p, <path d="M20 6 9 17l-5-5" />)
export const IconDownload = (p: P) =>
  base(p, (
    <>
      <path d="M12 3v12" />
      <path d="m7 11 5 5 5-5" />
      <path d="M4 21h16" />
    </>
  ))
export const IconChevronRight = (p: P) => base(p, <path d="m9 18 6-6-6-6" />)
export const IconChevronDown = (p: P) => base(p, <path d="m6 9 6 6 6-6" />)
export const IconX = (p: P) => base(p, <path d="M18 6 6 18M6 6l12 12" />)
export const IconSettings = (p: P) =>
  base(p, (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h0a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </>
  ))
export const IconSearch = (p: P) => base(p, (<><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></>))
export const IconFileDiff = (p: P) =>
  base(p, (
    <>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <path d="M9 15h6M9 11h6" />
    </>
  ))
export const IconTrash = (p: P) =>
  base(p, (
    <>
      <path d="M3 6h18" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </>
  ))
export const IconClock = (p: P) => base(p, (<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>))
export const IconHistory = (p: P) =>
  base(p, (
    <>
      <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
      <path d="M3 3v5h5" />
      <path d="M12 7v5l4 2" />
    </>
  ))
export const IconImage = (p: P) =>
  base(p, (
    <>
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <circle cx="8.5" cy="8.5" r="1.5" />
      <path d="m21 15-5-5L5 21" />
    </>
  ))
export const IconFile = (p: P) =>
  base(p, (
    <>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
    </>
  ))
export const IconCommand = (p: P) => base(p, <path d="M18 9a3 3 0 1 0 0 6 3 3 0 1 0-6 6V9a3 3 0 1 0 6 0 3 3 0 1 0-6 0V3h6" />)
export const IconSun = (p: P) =>
  base(p, (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
    </>
  ))
export const IconMoon = (p: P) => base(p, <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />)
export const IconMonitor = (p: P) =>
  base(p, (
    <>
      <rect x="2" y="3" width="20" height="14" rx="2" />
      <path d="M8 21h8M12 17v4" />
    </>
  ))
export const IconMenu = (p: P) => base(p, (<><path d="M3 6h18" /><path d="M3 12h18" /><path d="M3 18h18" /></>))
export const IconPaperclip = (p: P) =>
  base(p, (
    <>
      <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" />
    </>
  ))
export const IconExternal = (p: P) =>
  base(p, (
    <>
      <path d="M15 3h6v6" />
      <path d="M10 14 21 3" />
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
    </>
  ))
export const IconFolder = (p: P) =>
  base(p, (
    <>
      <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" />
    </>
  ))
export const IconSpark = ({ size = 20, ...p }: P) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true" {...p}>
    <path
      d="M12 2c.7 3.9 2.4 6.1 6.2 7.3-.7 1.1-1.3 2-1.7 3.5M12 2C9.6 5.4 7.6 7.3 3.9 8.2c.8 1.4 1.6 2.4 2.5 3.6M12 2c1.2 4.9 3.4 7.9 7.9 9.4M12 22c-.6-3.4-2.1-5.3-5.4-6.4.6-1 1.1-1.7 1.5-3M12 22c2-3 3.7-4.6 7-5.5-.7-1.2-1.4-2-2.2-3M12 22c-1-4.2-2.9-6.8-6.8-8.1"
      stroke="url(#spark-grad)"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
    <defs>
      <linearGradient id="spark-grad" x1="4" y1="2" x2="20" y2="22" gradientUnits="userSpaceOnUse">
        <stop stopColor="var(--accent-strong)" />
        <stop offset="1" stopColor="var(--accent)" />
      </linearGradient>
    </defs>
  </svg>
)
export const IconLayers = (p: P) =>
  base(p, (
    <>
      <path d="m12 2 10 6-10 6L2 8Z" />
      <path d="m2 16 10 6 10-6" />
    </>
  ))
export const IconKey = (p: P) =>
  base(p, (
    <>
      <circle cx="7.5" cy="15.5" r="5.5" />
      <path d="m21 2-9.6 9.6M15.5 7.5l3 3L22 7l-3-3" />
    </>
  ))
export const IconGlobe = (p: P) =>
  base(p, (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3a15 15 0 0 1 0 18 15 15 0 0 1 0-18" />
    </>
  ))
export const IconTerminal = (p: P) =>
  base(p, (
    <>
      <path d="m5 8 4 4-4 4M13 16h6" />
      <rect x="2" y="3" width="20" height="18" rx="2" />
    </>
  ))
export const IconInfo = (p: P) =>
  base(p, (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 16v-4M12 8h.01" />
    </>
  ))
export const IconHelp = (p: P) =>
  base(p, (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.1 9a3 3 0 0 1 5.83 1c0 2-3 2.5-3 4.5" />
      <path d="M12 17.5h.01" />
    </>
  ))
export const IconBrain = (p: P) =>
  base(p, (
    <>
      <path d="M12 5a3 3 0 1 0-5.997.118 4 4 0 0 0-2.276 6.482A4 4 0 0 0 4 17.5V18a2 2 0 0 0 2 2h1a3 3 0 0 0 3 3h2a3 3 0 0 0 3-3h1a2 2 0 0 0 2-2v-1.5a4 4 0 0 0 .273-6.9A4 4 0 0 0 12 5Z" />
      <path d="M9 9h.01M14 9h.01M9 14h.01M14 14h.01M10 11h4M10 14.5h4" />
    </>
  ))

// Bug glyph: 调试面板入口。用"甲虫"轮廓，与 Spark/Spark 等产品图标区分开。
export const IconBug = (p: P) => base(p, (
  <>
    <path d="M8 6a4 4 0 0 1 8 0" />
    <rect x="6" y="8" width="12" height="11" rx="5" />
    <path d="M3 11h3M18 11h3M3 16h3M18 16h3M12 8v11" />
  </>
))
