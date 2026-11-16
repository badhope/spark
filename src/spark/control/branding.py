"""Tenant branding for generated office artifacts.

Enterprises put their own name, colour and logo on meeting minutes and reports.
Everything here is presentation-only: it never changes the structured content,
the citations or the approval flow, so a branded document is still auditable.

Values come from ``SPARK_CONTROL_BRAND_*`` and are validated once at startup.
An invalid colour or a missing logo degrades to the Spark defaults instead of
failing a request — a broken brand string must not stop someone from producing
minutes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_LOGO_SUFFIXES = {".png", ".jpg", ".jpeg"}
MAX_LOGO_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Brand:
    #: Shown in the document footer and the PDF page header.
    name: str = "Spark"
    #: Headings and table headers.
    primary: str = "#1F4E79"
    #: Titles and the accent rule.
    accent: str = "#17365D"
    #: Footer line; ``{page}`` is replaced with the page number by the PDF
    #: renderer and left literal elsewhere.
    footer: str = "本文件由{name}自动生成，请人工复核"
    logo_path: str = ""

    def footer_text(self) -> str:
        return self.footer.replace("{name}", self.name)

    def logo(self) -> Path | None:
        """The logo file, or ``None`` when unset/unusable.

        Validated on every use rather than at construction: the path is usually
        mounted into a container, so a file can legitimately appear later.
        """
        if not self.logo_path:
            return None
        path = Path(self.logo_path).expanduser()
        try:
            if not path.is_file():
                logger.warning("brand logo not found: %s", path)
                return None
            if path.suffix.lower() not in _LOGO_SUFFIXES:
                logger.warning("brand logo must be png/jpg: %s", path)
                return None
            if path.stat().st_size > MAX_LOGO_BYTES:
                logger.warning("brand logo is too large (>4MB): %s", path)
                return None
        except OSError as exc:  # pragma: no cover - permission/IO edge
            logger.warning("brand logo unreadable: %s (%s)", path, exc)
            return None
        return path

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "primary": self.primary,
            "accent": self.accent,
            "footer": self.footer_text(),
            # Only advertise a logo the renderers can actually use.
            "logo": self.logo_path if self.logo() else "",
        }


def _colour(value: str, fallback: str, label: str) -> str:
    text = (value or "").strip()
    if not text:
        return fallback
    if not _HEX.match(text):
        logger.warning("ignoring invalid brand %s %r; using %s", label, value, fallback)
        return fallback
    return text


def brand_from_settings(settings: object) -> Brand:
    """Build a ``Brand`` from a ``ControlSettings``-shaped object."""
    get = lambda key, default="": getattr(settings, key, default) or default  # noqa: E731
    default = Brand()
    return Brand(
        name=get("brand_name").strip() or default.name,
        primary=_colour(get("brand_primary"), default.primary, "primary colour"),
        accent=_colour(get("brand_accent"), default.accent, "accent colour"),
        footer=get("brand_footer").strip() or default.footer,
        logo_path=get("brand_logo").strip(),
    )


DOCX_FALLBACK_FOOTER = "本文件由 Spark 自动生成，请人工复核。"
PPTX_FALLBACK_SUBTITLE = "Spark 自动生成 · 请人工复核"


def docx_footer(brand: Brand | None) -> str:
    return brand.footer_text() if brand is not None else DOCX_FALLBACK_FOOTER


def docx_decorate(document: object, brand: Brand | None) -> None:
    """Put the logo at the top of an already-built Word document.

    Called *after* the body is composed so the logo lands above the title even
    though python-docx can only append. Everything is best-effort: a broken logo
    or an exotic python-docx build must not lose the document.
    """
    if brand is None:
        return
    logo = brand.logo()
    if logo is None:
        return
    try:
        from docx.shared import Inches

        paragraph = document.paragraphs[0].insert_paragraph_before()  # type: ignore[attr-defined]
        paragraph.add_run().add_picture(str(logo), width=Inches(1.4))
    except Exception:  # noqa: BLE001 - presentation only
        logger.warning("could not embed the brand logo into the docx", exc_info=True)


def pptx_decorate(
    presentation: object, title_slide: object, brand: Brand | None
) -> None:
    """Place the logo in the bottom-right corner of a deck's title slide."""
    if brand is None:
        return
    logo = brand.logo()
    if logo is None:
        return
    try:
        from pptx.util import Inches

        title_slide.shapes.add_picture(  # type: ignore[attr-defined]
            str(logo),
            presentation.slide_width - Inches(2.2),  # type: ignore[attr-defined]
            presentation.slide_height - Inches(1.1),  # type: ignore[attr-defined]
            height=Inches(0.6),
        )
    except Exception:  # noqa: BLE001 - presentation only
        logger.warning("could not embed the brand logo into the pptx", exc_info=True)


def hex_colour(value: str | None, fallback: str = "1F4E79") -> str:
    """A bare 6-digit hex string for libraries that reject ``#``."""
    text = (value or "").lstrip("#")
    return text.upper() if _HEX.match("#" + text) else fallback
