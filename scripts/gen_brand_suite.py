"""Generate the Spark brand suite: mark, lockups, favicon and raster icons.

The geometry is defined once here and every surface is derived from it: the
README lockup, the Web sidebar badge, the favicon, the PWA icons, the desktop
``.ico`` and the VS Code extension icon. Run from the repository root::

    python scripts/gen_brand_suite.py

Requires ``cairosvg`` and Pillow (development-only). The Noto Sans CJK Black
face is converted to outlines so the wordmark does not depend on the fonts
installed on a viewer's machine. If it is unavailable the script falls back to
live ``<text>``; the committed assets remain authoritative.
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "brand"
WEB_PUBLIC = ROOT / "web" / "public"
WEB_DIST = ROOT / "src" / "spark" / "web" / "dist"
ASSETS = ROOT / "assets"

FONT_BLACK = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc")
FONT_MEDIUM = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc")
FONT_INDEX = 2  # Noto Sans CJK SC

EMBER = "#ff7a1a"
INK = "#17130f"
PAPER = "#f6f1ea"
MUTED = "#8a8178"
MUTED_DARK = "#b9aea2"


def mark_svg(prefix: str = "m") -> str:
    """The Spark mark: an ember tile, an orbit, and a four-point spark."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64" role="img" aria-label="Spark">
  <title>Spark — the loop and the spark</title>
  <defs>
    <linearGradient id="{prefix}-bg" x1="4" y1="2" x2="62" y2="64" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#ffc786"/>
      <stop offset="0.42" stop-color="#ff7a1a"/>
      <stop offset="1" stop-color="#bc3d08"/>
    </linearGradient>
    <radialGradient id="{prefix}-glow" cx="0.24" cy="0.12" r="0.95">
      <stop offset="0" stop-color="#fffaf0" stop-opacity="0.34"/>
      <stop offset="0.55" stop-color="#ffffff" stop-opacity="0.04"/>
      <stop offset="1" stop-color="#ffffff" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="{prefix}-core" x1="22" y1="8" x2="42" y2="56" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#fffdf8"/>
      <stop offset="1" stop-color="#ffe3b8"/>
    </linearGradient>
  </defs>
  <rect width="64" height="64" rx="15" fill="url(#{prefix}-bg)"/>
  <rect width="64" height="64" rx="15" fill="url(#{prefix}-glow)"/>
  <rect x="0.8" y="0.8" width="62.4" height="62.4" rx="14.2" fill="none" stroke="#fff6e8" stroke-opacity="0.18" stroke-width="1.2"/>
  <ellipse cx="32" cy="32" rx="24" ry="11.5" transform="rotate(-30 32 32)" fill="none" stroke="#fff6e8" stroke-opacity="0.42" stroke-width="1.3"/>
  <circle cx="52.8" cy="20" r="1.6" fill="#fff8ec" fill-opacity="0.92"/>
  <g transform="translate(32 32) scale(0.62) translate(-32 -32)">
    <path d="M32 4C33.9 21.2 42.8 30.1 60 32C42.8 33.9 33.9 42.8 32 60C30.1 42.8 21.2 33.9 4 32C21.2 30.1 30.1 21.2 32 4Z" fill="url(#{prefix}-core)"/>
  </g>
</svg>
"""


def favicon_svg() -> str:
    """A simplified mark for 16px: drop the orbit, enlarge the spark."""
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64" role="img" aria-label="Spark">
  <defs>
    <linearGradient id="f-bg" x1="4" y1="2" x2="62" y2="64" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#ffc786"/>
      <stop offset="0.42" stop-color="#ff7a1a"/>
      <stop offset="1" stop-color="#bc3d08"/>
    </linearGradient>
    <radialGradient id="f-glow" cx="0.24" cy="0.12" r="0.95">
      <stop offset="0" stop-color="#fffaf0" stop-opacity="0.3"/>
      <stop offset="1" stop-color="#ffffff" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="64" height="64" rx="16" fill="url(#f-bg)"/>
  <rect width="64" height="64" rx="16" fill="url(#f-glow)"/>
  <g transform="translate(32 32) scale(0.71) translate(-32 -32)">
    <path d="M32 4C33.9 21.2 42.8 30.1 60 32C42.8 33.9 33.9 42.8 32 60C30.1 42.8 21.2 33.9 4 32C21.2 30.1 30.1 21.2 32 4Z" fill="#fffdf8"/>
  </g>
</svg>
"""


def _glyph_paths(text: str, size: float, tracking: float, x: float, baseline: float, font_path: Path) -> str:
    """Convert a short wordmark to outlines, so brand type is font-independent."""
    if not font_path.exists():
        family = "Noto Sans CJK SC, ui-sans-serif, system-ui, sans-serif"
        return (
            f'<text x="{x}" y="{baseline}" font-family="{family}" font-size="{size}" '
            f'font-weight="900" letter-spacing="{tracking}" fill="inherit">{text}</text>'
        )
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont

    font = TTFont(str(font_path), fontNumber=FONT_INDEX)
    cmap = font.getBestCmap()
    glyphs = font.getGlyphSet()
    hmtx = font["hmtx"]
    upm = font["head"].unitsPerEm
    scale = size / upm
    cursor = x
    parts: list[str] = []
    for char in text:
        glyph_name = cmap[ord(char)]
        pen = SVGPathPen(glyphs)
        glyphs[glyph_name].draw(pen)
        d = pen.getCommands()
        parts.append(
            f'<path transform="translate({cursor:.2f} {baseline}) scale({scale:.6f} {-scale:.6f})" d="{d}"/>'
        )
        cursor += hmtx[glyph_name][0] * scale + tracking
    weight = "700" if "Black" in font_path.name else "500"
    return f'<g fill="inherit" data-weight="{weight}">' + "".join(parts) + "</g>"


def logo_svg(dark: bool = False) -> str:
    """Horizontal lockup: tile + lowercase wordmark + descriptor."""
    ink = PAPER if dark else INK
    muted = MUTED_DARK if dark else MUTED
    word = _glyph_paths("spark", 32, -1.2, 70, 38, FONT_BLACK)
    descriptor = _glyph_paths("AI CODING AGENT", 7.4, 2.2, 71.6, 54, FONT_MEDIUM)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 64" width="180" height="64" role="img" aria-label="Spark — AI coding agent">
  <title>Spark — AI coding agent</title>
  <defs>
    <linearGradient id="l-bg" x1="4" y1="2" x2="62" y2="64" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#ffc786"/>
      <stop offset="0.42" stop-color="#ff7a1a"/>
      <stop offset="1" stop-color="#bc3d08"/>
    </linearGradient>
    <radialGradient id="l-glow" cx="0.24" cy="0.12" r="0.95">
      <stop offset="0" stop-color="#fffaf0" stop-opacity="0.34"/>
      <stop offset="1" stop-color="#ffffff" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="l-core" x1="22" y1="8" x2="42" y2="56" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#fffdf8"/>
      <stop offset="1" stop-color="#ffe3b8"/>
    </linearGradient>
  </defs>
  <g transform="translate(1 4) scale(0.875)">
    <rect width="64" height="64" rx="15" fill="url(#l-bg)"/>
    <rect width="64" height="64" rx="15" fill="url(#l-glow)"/>
    <rect x="0.8" y="0.8" width="62.4" height="62.4" rx="14.2" fill="none" stroke="#fff6e8" stroke-opacity="0.18" stroke-width="1.2"/>
    <ellipse cx="32" cy="32" rx="24" ry="11.5" transform="rotate(-30 32 32)" fill="none" stroke="#fff6e8" stroke-opacity="0.42" stroke-width="1.3"/>
    <circle cx="52.8" cy="20" r="1.6" fill="#fff8ec" fill-opacity="0.92"/>
    <g transform="translate(32 32) scale(0.62) translate(-32 -32)">
      <path d="M32 4C33.9 21.2 42.8 30.1 60 32C42.8 33.9 33.9 42.8 32 60C30.1 42.8 21.2 33.9 4 32C21.2 30.1 30.1 21.2 32 4Z" fill="url(#l-core)"/>
    </g>
  </g>
  <g style="color:{ink};fill:{ink}">{word}</g>
  <g style="color:{muted};fill:{muted}">{descriptor}</g>
</svg>
"""


def write_vectors() -> None:
    BRAND.mkdir(exist_ok=True)
    (BRAND / "mark.svg").write_text(mark_svg("m"), encoding="utf-8")
    (BRAND / "favicon.svg").write_text(favicon_svg(), encoding="utf-8")
    (BRAND / "logo.svg").write_text(logo_svg(False), encoding="utf-8")
    (BRAND / "logo-dark.svg").write_text(logo_svg(True), encoding="utf-8")
    ASSETS.mkdir(exist_ok=True)
    shutil.copyfile(BRAND / "mark.svg", ASSETS / "logo.svg")


def _render(svg: str, out: Path, width: int, height: int | None = None) -> None:
    import cairosvg

    out.parent.mkdir(parents=True, exist_ok=True)
    cairosvg.svg2png(
        bytestring=svg.encode("utf-8"),
        write_to=str(out),
        output_width=width,
        output_height=height or width,
    )


def write_rasters() -> None:
    mark = mark_svg("r")
    _render(mark, ASSETS / "logo.png", 512)
    _render(mark, BRAND / "mark-128.png", 128)
    _render(mark, WEB_PUBLIC / "icon-180.png", 180)
    _render(mark, WEB_PUBLIC / "icon-192.png", 192)
    _render(mark, WEB_PUBLIC / "icon-512.png", 512)
    _render(mark, ROOT / "vscode-spark" / "icon.png", 256)
    _render(logo_svg(False), BRAND / "logo.png", 540, 192)
    _render(logo_svg(True), BRAND / "logo-dark.png", 540, 192)

    shutil.copyfile(BRAND / "favicon.svg", WEB_PUBLIC / "favicon.svg")
    for name in ("favicon.svg", "icon-180.png", "icon-192.png", "icon-512.png"):
        shutil.copyfile(WEB_PUBLIC / name, WEB_DIST / name)

    from PIL import Image

    png = Image.open(ASSETS / "logo.png").convert("RGBA")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    png.save(ASSETS / "logo.ico", format="ICO", sizes=[(s, s) for s in sizes])


def main() -> int:
    write_vectors()
    write_rasters()
    print("brand vectors + icons regenerated")
    print(f"  mark:      {BRAND / 'mark.svg'}")
    print(f"  lockups:   {BRAND / 'logo.svg'} · {BRAND / 'logo-dark.svg'}")
    print(f"  favicon:   {WEB_PUBLIC / 'favicon.svg'}")
    print(f"  app icons: {ASSETS / 'logo.ico'} · {WEB_PUBLIC / 'icon-512.png'} · vscode-spark/icon.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
