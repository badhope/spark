"""Shared PDF rendering for office artifacts.

Uses ReportLab with the built-in Adobe CJK CID font so Chinese text works
without shipping a font file. Callers pass simple blocks instead of ReportLab
objects, keeping the meeting/report builders independent of the PDF library.
"""

from __future__ import annotations

from html import escape
from io import BytesIO
from typing import Any

_CJK_FONT = "STSong-Light"


def _register_font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    if _CJK_FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(_CJK_FONT))
    return _CJK_FONT


def _text(value: Any) -> str:
    return escape(str(value)).replace("\n", "<br/>")


def render_pdf(
    title: str,
    subtitle: str,
    blocks: list[tuple[str, Any]],
    footer: str = "Spark 自动生成 · 请人工复核",
) -> bytes:
    """Render blocks to PDF bytes.

    Block kinds: ``heading`` (str), ``paragraph`` (str), ``bullets``
    (list[str]), ``table`` ((headers, rows)), ``note`` (str).
    """

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "pdf export requires reportlab (install spark-agent[office])"
        ) from exc

    font = _register_font()
    base = ParagraphStyle(
        "SparkBody", fontName=font, fontSize=10.5, leading=16, spaceAfter=4
    )
    heading_style = ParagraphStyle(
        "SparkHeading",
        parent=base,
        fontSize=13,
        leading=19,
        textColor=colors.HexColor("#1F4E79"),
        spaceBefore=12,
        spaceAfter=6,
    )
    title_style = ParagraphStyle(
        "SparkTitle",
        parent=base,
        fontSize=19,
        leading=27,
        textColor=colors.HexColor("#17365D"),
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "SparkSubtitle", parent=base, fontSize=9, textColor=colors.HexColor("#666666")
    )
    note_style = ParagraphStyle(
        "SparkNote",
        parent=base,
        fontSize=8.5,
        leading=13,
        textColor=colors.HexColor("#777777"),
    )
    cell_style = ParagraphStyle("SparkCell", parent=base, fontSize=9, leading=13)
    cell_head = ParagraphStyle(
        "SparkCellHead",
        parent=cell_style,
        textColor=colors.white,
        fontSize=9,
        leading=13,
    )

    width = A4[0] - 36 * mm
    story: list[Any] = [Paragraph(_text(title), title_style)]
    if subtitle:
        story.append(Paragraph(_text(subtitle), subtitle_style))

    for kind, value in blocks:
        if kind == "heading":
            story.append(Paragraph(_text(value), heading_style))
        elif kind == "paragraph":
            story.append(Paragraph(_text(value), base))
        elif kind == "bullets":
            for item in value:
                story.append(Paragraph("• " + _text(item), base))
        elif kind == "note":
            story.append(Spacer(1, 8))
            story.append(Paragraph(_text(value), note_style))
        elif kind == "table":
            headers, rows = value
            data = [
                [Paragraph(_text(cell), cell_head) for cell in headers],
                *[
                    [Paragraph(_text(cell), cell_style) for cell in row]
                    for row in rows
                ],
            ]
            columns = len(headers)
            if columns == 4:
                widths = [width * 0.46, width * 0.18, width * 0.18, width * 0.18]
            elif columns == 5:
                widths = [
                    width * 0.34,
                    width * 0.16,
                    width * 0.16,
                    width * 0.17,
                    width * 0.17,
                ]
            else:
                widths = [width / columns] * columns
            table = Table(data, colWidths=widths, repeatRows=1)
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F5597")),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B8C6DB")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F6FB")]),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ]
                )
            )
            story.append(table)
            story.append(Spacer(1, 6))

    def _page(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(18 * mm, 10 * mm, footer)
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, str(doc.page))
        canvas.restoreState()

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="Spark",
    )
    document.build(story, onFirstPage=_page, onLaterPages=_page)
    return buffer.getvalue()
