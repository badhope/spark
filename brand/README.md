# Spark brand

**The loop and the spark.** The orbit is the one agent loop; the spark is the
moment a task catches fire. The mark keeps both: an ember tile, a planet-like
orbit, a four-point core. It should read at 16 px as a spark, and at 256 px as a
system.

## Assets

| File | Use |
|---|---|
| `mark.svg` | App mark, 64 viewBox; source geometry for every icon |
| `favicon.svg` | Simplified 16 px mark (orbit dropped, spark enlarged) |
| `logo.svg` / `logo-dark.svg` | Horizontal lockup for light / dark surfaces |
| `logo.png` / `logo-dark.png` | 540 px raster lockups for slides and posts |
| `mark-128.png` | Small raster mark |
| `hero-en.webp` / `hero-zh.webp` | README hero, 2000 × 800 |
| `pillars-en.webp` / `pillars-zh.webp` | Three non-negotiable promises, 2000 × 550 |
| `architecture-en.webp` / `architecture-zh.webp` | Six surfaces / one loop, 2000 × 1125 |
| `../social-preview.png` | GitHub social card, 1280 × 640 (repo root) |

The Web sidebar and empty state use the same geometry in
`web/src/components/BrandMark.tsx`. Favicon and PWA icons are copied to
`web/public/`; the desktop `.ico` and VS Code extension icon are generated too.

## Palette

| Token | Value | Role |
|---|---|---|
| Ember | `#ff7a1a` | primary accent, the spark |
| Halo | `#ffc786` | top of the ember gradient |
| Copper | `#bc3d08` | shadow end of the ember gradient |
| Ink | `#17130f` | wordmark on light surfaces |
| Paper | `#f6f1ea` | wordmark on dark surfaces |
| Graphite | `#0b0d11` | hero and social backgrounds |
| Muted | `#8a8178` / `#b9aea2` | descriptor text, light / dark |

Use one accent, not five. The UI's optional palettes (Ocean, Forest, Violet,
Mono) change the interface accent but not the core brand mark; the product mark
stays ember so it remains recognisable across screenshots and app icons.

## Type

The wordmark is **Noto Sans CJK SC Black**, converted to outlines so it renders
identically without the font installed. The descriptor is uppercase, tracked at
`0.30em`; it is a caption, never a second headline. Do not set "Spark" in a
script face, add a glow behind the wordmark, or rotate the mark.

## Clear space and minimum size

Keep clear space of at least half the tile width on every side. The mark must
not be smaller than 16 px; the horizontal lockup must not be smaller than 120 px
wide. On dark backgrounds use `logo-dark.svg`; on light backgrounds use
`logo.svg`. Do not recolour the spark to a non-ember hue.

## Regenerate

```bash
python scripts/gen_brand_suite.py       # SVG geometry, favicon, PNG/ICO icons
python scripts/render_brand_pages.py    # hero, pillars, architecture, social card
python scripts/capture_brand_shots.py   # product screenshots with the current mark
```

`gen_brand_suite.py` is the single source of truth for the mark geometry. If
you change a coordinate, rerun it and update `BrandMark.tsx` in the same commit;
do not let the app icon, favicon and in-app mark drift apart.
