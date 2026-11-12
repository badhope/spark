"""Report / spreadsheet first office slice.

A deterministic report builder: structured metrics + narrative sections in,
Markdown + Excel (with a bar chart) + Word out. Later a model-backed agent can
fill the same contract; the artifact schema and API stay unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any


@dataclass
class Metric:
    name: str
    value: float
    target: float | None = None
    unit: str = ""
    #: "higher" = larger is better (revenue, output); "lower" = smaller is
    #: better (latency, rework rate). Without this, a 6% rework rate against a
    #: 10% target would incorrectly show 60% achievement.
    direction: str = "higher"

    @property
    def achievement(self) -> float | None:
        if not self.target:
            return None
        if self.direction == "lower":
            return self.target / self.value if self.value else None
        return self.value / self.target

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "target": self.target,
            "unit": self.unit,
            "direction": self.direction,
            "achievement": self.achievement,
        }


@dataclass
class ReportSection:
    heading: str
    body: str

    def to_dict(self) -> dict[str, str]:
        return {"heading": self.heading, "body": self.body}


@dataclass
class Report:
    title: str
    summary: str
    metrics: list[Metric] = field(default_factory=list)
    sections: list[ReportSection] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "metrics": [m.to_dict() for m in self.metrics],
            "sections": [s.to_dict() for s in self.sections],
        }

    def markdown(self) -> str:
        lines = [f"# {self.title}", "", "## 摘要", self.summary, "", "## 指标", "| 指标 | 实际 | 目标 | 达成率 | 单位 |", "|---|---|---|---|---|"]
        for metric in self.metrics:
            achievement = f"{metric.achievement:.1%}" if metric.achievement is not None else "—"
            target = f"{metric.target:g}" if metric.target is not None else "—"
            lines.append(
                f"| {metric.name} | {metric.value:g} | {target} | {achievement} | {metric.unit or '—'} |"
            )
        for section in self.sections:
            lines += ["", f"## {section.heading}", section.body]
        lines += ["", "> 本报告由 Spark 自动生成；指标口径与数据来源需人工复核。"]
        return "\n".join(lines)

    def pdf_bytes(self) -> bytes:
        from spark.control.pdf import render_pdf

        blocks: list[tuple[str, Any]] = [
            ("heading", "摘要"),
            ("paragraph", self.summary),
            ("heading", "指标"),
            (
                "table",
                (
                    ["指标", "实际", "目标", "达成率", "单位"],
                    [
                        [
                            metric.name,
                            f"{metric.value:g}",
                            f"{metric.target:g}" if metric.target is not None else "—",
                            f"{metric.achievement:.1%}" if metric.achievement is not None else "—",
                            metric.unit or "—",
                        ]
                        for metric in self.metrics
                    ],
                ),
            ),
        ]
        for section in self.sections:
            blocks += [("heading", section.heading), ("paragraph", section.body)]
        blocks.append(("note", "本报告由 Spark 自动生成；指标口径与数据来源需人工复核。"))
        return render_pdf(self.title, "经营报告", blocks)

    def xlsx_bytes(self) -> bytes:
        try:
            from openpyxl import Workbook
            from openpyxl.chart import BarChart, Reference
            from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "xlsx export requires openpyxl (install spark-agent[office])"
            ) from exc

        workbook = Workbook()
        summary = workbook.active
        summary.title = "Summary"
        summary.sheet_view.showGridLines = False
        summary.merge_cells("A1:E1")
        summary["A1"] = self.title
        summary["A1"].font = Font(size=16, bold=True, color="FFFFFF")
        summary["A1"].fill = PatternFill("solid", fgColor="1F4E79")
        summary["A1"].alignment = Alignment(vertical="center")
        summary.row_dimensions[1].height = 30
        summary.merge_cells("A3:E3")
        summary["A3"] = self.summary
        summary["A3"].alignment = Alignment(wrap_text=True, vertical="top")
        summary["A3"].fill = PatternFill("solid", fgColor="EAF1F8")
        summary.row_dimensions[3].height = 46

        header = ("指标", "实际", "目标", "达成率", "单位")
        for column, title in enumerate(header, 1):
            cell = summary.cell(row=5, column=column, value=title)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="2F5597")
            cell.alignment = Alignment(horizontal="center")
        thin = Side(style="thin", color="D9E2F3")
        for row, metric in enumerate(self.metrics, 6):
            values = (
                metric.name,
                metric.value,
                metric.target,
                metric.achievement,
                metric.unit,
            )
            for column, value in enumerate(values, 1):
                cell = summary.cell(row=row, column=column, value=value)
                cell.border = Border(bottom=thin)
                if column == 4 and isinstance(value, float):
                    cell.number_format = "0.0%"
        summary.column_dimensions["A"].width = 28
        summary.column_dimensions["B"].width = 14
        summary.column_dimensions["C"].width = 14
        summary.column_dimensions["D"].width = 12
        summary.column_dimensions["E"].width = 12

        metrics_sheet = workbook.create_sheet("Metrics")
        metrics_sheet.append(("指标", "实际", "目标"))
        for metric in self.metrics:
            metrics_sheet.append((metric.name, metric.value, metric.target))
        if self.metrics:
            chart = BarChart()
            chart.title = "实际 vs 目标"
            chart.y_axis.title = "数值"
            chart.x_axis.title = "指标"
            data = Reference(metrics_sheet, min_col=2, max_col=3, min_row=1, max_row=len(self.metrics) + 1)
            categories = Reference(metrics_sheet, min_col=1, min_row=2, max_row=len(self.metrics) + 1)
            chart.add_data(data, titles_from_data=True)
            chart.set_categories(categories)
            metrics_sheet.add_chart(chart, "E2")
        metrics_sheet.column_dimensions["A"].width = 28
        metrics_sheet.column_dimensions["B"].width = 14
        metrics_sheet.column_dimensions["C"].width = 14

        if self.sections:
            notes = workbook.create_sheet("Narrative")
            notes.append(("章节", "内容"))
            for section in self.sections:
                notes.append((section.heading, section.body))
            notes.column_dimensions["A"].width = 20
            notes.column_dimensions["B"].width = 80

        buffer = BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()

    def docx_bytes(self) -> bytes:
        try:
            from docx import Document
            from docx.shared import Pt
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "docx export requires python-docx (install spark-agent[office])"
            ) from exc

        document = Document()
        document.add_heading(self.title, level=0)
        document.add_heading("摘要", level=1)
        document.add_paragraph(self.summary)
        document.add_heading("指标", level=1)
        table = document.add_table(rows=1, cols=5)
        table.style = "Table Grid"
        for cell, text in zip(table.rows[0].cells, ("指标", "实际", "目标", "达成率", "单位")):
            cell.text = text
        for metric in self.metrics:
            row = table.add_row().cells
            row[0].text = metric.name
            row[1].text = f"{metric.value:g}"
            row[2].text = f"{metric.target:g}" if metric.target is not None else "—"
            row[3].text = f"{metric.achievement:.1%}" if metric.achievement is not None else "—"
            row[4].text = metric.unit
        for section in self.sections:
            document.add_heading(section.heading, level=1)
            document.add_paragraph(section.body)
        document.add_paragraph("本报告由 Spark 自动生成，请人工复核。")
        for paragraph in document.paragraphs:
            for run in paragraph.runs:
                run.font.size = Pt(10.5)
        buffer = BytesIO()
        document.save(buffer)
        return buffer.getvalue()

    def pptx_bytes(self) -> bytes:
        try:
            from pptx import Presentation
            from pptx.util import Inches, Pt
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "pptx export requires python-pptx (install spark-agent[office])"
            ) from exc

        presentation = Presentation()
        title_slide = presentation.slides.add_slide(presentation.slide_layouts[0])
        title_slide.shapes.title.text = self.title
        title_slide.placeholders[1].text = "Spark 自动生成 · 请人工复核"

        metrics_slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        metrics_slide.shapes.title.text = "关键指标"
        rows = min(len(self.metrics), 10) + 1
        table = metrics_slide.shapes.add_table(
            rows, 5, Inches(0.5), Inches(1.4), Inches(9.0), Inches(0.35 * rows)
        ).table
        for index, heading in enumerate(("指标", "实际", "目标", "达成率", "单位")):
            table.cell(0, index).text = heading
        for row_index, metric in enumerate(self.metrics[:10], 1):
            table.cell(row_index, 0).text = metric.name
            table.cell(row_index, 1).text = f"{metric.value:g}"
            table.cell(row_index, 2).text = (
                f"{metric.target:g}" if metric.target is not None else "—"
            )
            table.cell(row_index, 3).text = (
                f"{metric.achievement:.1%}" if metric.achievement is not None else "—"
            )
            table.cell(row_index, 4).text = metric.unit
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.text_frame.paragraphs:
                    for run in paragraph.runs:
                        run.font.size = Pt(10.5)

        if self.sections:
            narrative_slide = presentation.slides.add_slide(presentation.slide_layouts[1])
            narrative_slide.shapes.title.text = "结论与风险"
            frame = narrative_slide.placeholders[1].text_frame
            frame.text = f"{self.sections[0].heading}：{self.sections[0].body}"
            for section in self.sections[1:5]:
                paragraph = frame.add_paragraph()
                paragraph.text = f"{section.heading}：{section.body}"
                paragraph.level = 1

        buffer = BytesIO()
        presentation.save(buffer)
        return buffer.getvalue()


class ReportService:
    _METRIC_SPLIT = re.compile(r"[,，\t]")

    def build(
        self,
        title: str,
        summary: str,
        metrics_text: str,
        sections_text: str,
    ) -> Report:
        metrics: list[Metric] = []
        for line in metrics_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [part.strip() for part in self._METRIC_SPLIT.split(line) if part.strip()]
            if len(parts) < 2:
                continue
            try:
                value = float(parts[1].replace("%", ""))
            except ValueError:
                continue
            target: float | None = None
            if len(parts) >= 3 and parts[2] not in {"", "-", "—"}:
                try:
                    target = float(parts[2].replace("%", ""))
                except ValueError:
                    target = None
            direction = "higher"
            if len(parts) >= 5 and parts[4].strip().lower() in {"lower", "low", "低", "越低越好"}:
                direction = "lower"
            metrics.append(
                Metric(
                    name=parts[0],
                    value=value,
                    target=target,
                    unit=parts[3] if len(parts) >= 4 else "",
                    direction=direction,
                )
            )
        if not metrics:
            raise ValueError("at least one metric line is required (name,value[,target[,unit]])")

        sections: list[ReportSection] = []
        heading = ""
        body: list[str] = []
        for line in sections_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                if heading:
                    sections.append(ReportSection(heading, "\n".join(body).strip()))
                heading = stripped.lstrip("#").strip() or "说明"
                body = []
            elif stripped:
                body.append(stripped)
        if heading:
            sections.append(ReportSection(heading, "\n".join(body).strip()))

        if not summary.strip():
            achieved = sum(1 for metric in metrics if metric.achievement is not None and metric.achievement >= 1)
            summary = (
                f"本期共跟踪 {len(metrics)} 项指标，"
                f"其中 {achieved} 项达到目标；详见下方指标与说明。"
            )
        return Report(
            title=title.strip() or "未命名报告",
            summary=summary.strip(),
            metrics=metrics,
            sections=sections,
        )
