"""Meeting minutes / action items first office slice.

This is intentionally deterministic: it turns a transcript into a structured,
citable artifact without requiring a model. A model-backed summarizer can
implement the same ``build`` contract later; the artifact schema and API stay
unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

_ATTENDEES = re.compile(r"^\s*(?:参会(?:人员)?|attendees?)\s*[:：]\s*(.+)$", re.IGNORECASE)
_TOPIC = re.compile(r"^\s*(?:#{1,4}\s+|\d+[.、)]\s*)(.+)$")
_TOPIC_LINE = re.compile(r"^\s*(?:议题|主题|topic)\s*[:：]\s*(.+)$", re.IGNORECASE)
_DECISION = re.compile(
    r"(决定\s*[:：]|决议\s*[:：]|结论\s*[:：]|达成一致|decision\b|decided\b|agreed\b)",
    re.IGNORECASE,
)
# "行动" alone is too broad: "本周先做会议纪要与行动项" is a topic sentence,
# and a risk mentioning "负责人可能缺失" is not an assigned action. Require an
# explicit action marker or an owner field (`负责人:`).
_ACTION = re.compile(
    r"(行动项\s*[:：]|行动\s*[:：]|待办\s*[:：]?|todo\b|action item\b|负责人\s*[:：]|owner\s*[:：]|@[A-Za-z0-9_\-\u4e00-\u9fff]+)",
    re.IGNORECASE,
)
_OWNER = re.compile(r"(?:负责人|owner)\s*[:：]\s*([^\s,，;；]+)|@([A-Za-z0-9_\-\u4e00-\u9fff]+)", re.IGNORECASE)
_DUE = re.compile(r"(?:截止|到期|due)\s*[:：]?\s*([0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2}|[0-9]{1,2}月[0-9]{1,2}日)", re.IGNORECASE)


@dataclass
class ActionItem:
    text: str
    owner: str = ""
    due: str = ""
    line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "owner": self.owner, "due": self.due, "line": self.line}


@dataclass
class MeetingMinutes:
    title: str
    summary: str
    attendees: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    actions: list[ActionItem] = field(default_factory=list)
    citations: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "attendees": list(self.attendees),
            "topics": list(self.topics),
            "decisions": list(self.decisions),
            "actions": [a.to_dict() for a in self.actions],
            "citations": self.citations,
        }

    def markdown(self) -> str:
        lines = [f"# {self.title}", "", "## 摘要", self.summary, ""]
        if self.attendees:
            lines += ["## 参会人员", "、".join(self.attendees), ""]
        if self.topics:
            lines += ["## 议题", *[f"- {topic}" for topic in self.topics], ""]
        if self.decisions:
            lines += ["## 决议与结论"]
            lines += [f"- {item['text']}（第 {item['line']} 行）" for item in self.decisions]
            lines.append("")
        if self.actions:
            lines += ["## 行动项", "| 行动 | 负责人 | 截止 | 来源 |", "|---|---|---|---|"]
            for action in self.actions:
                lines.append(
                    f"| {action.text} | {action.owner or '待确认'} | "
                    f"{action.due or '待确认'} | 第 {action.line} 行 |"
                )
            lines.append("")
        lines += [
            "## 待确认",
            "- 负责人/截止时间缺失的行动项已标注“待确认”，需人工补齐。",
            "",
            "> 本纪要由 Spark 自动生成；决议与行动项均附来源行号，请人工复核。",
        ]
        return "\n".join(lines)

    def pdf_bytes(self) -> bytes:
        from spark.control.pdf import render_pdf

        blocks: list[tuple[str, Any]] = [("heading", "摘要"), ("paragraph", self.summary)]
        if self.attendees:
            blocks += [("heading", "参会人员"), ("paragraph", "、".join(self.attendees))]
        if self.topics:
            blocks += [("heading", "议题"), ("bullets", list(self.topics))]
        if self.decisions:
            blocks += [
                ("heading", "决议与结论"),
                (
                    "bullets",
                    [f"{item['text']}（第 {item['line']} 行）" for item in self.decisions],
                ),
            ]
        if self.actions:
            blocks += [
                ("heading", "行动项"),
                (
                    "table",
                    (
                        ["行动", "负责人", "截止", "来源"],
                        [
                            [
                                action.text,
                                action.owner or "待确认",
                                action.due or "待确认",
                                f"第 {action.line} 行",
                            ]
                            for action in self.actions
                        ],
                    ),
                ),
            ]
        blocks.append(("note", "本纪要由 Spark 自动生成；决议与行动项均附来源行号，请人工复核。"))
        return render_pdf(self.title, "会议纪要", blocks)

    def docx_bytes(self) -> bytes:
        try:
            from docx import Document
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "docx export requires python-docx (install spark-agent[office])"
            ) from exc
        document = Document()
        document.add_heading(self.title, level=0)
        document.add_heading("摘要", level=1)
        document.add_paragraph(self.summary)
        if self.attendees:
            document.add_heading("参会人员", level=1)
            document.add_paragraph("、".join(self.attendees))
        if self.topics:
            document.add_heading("议题", level=1)
            for topic in self.topics:
                document.add_paragraph(topic, style="List Bullet")
        if self.decisions:
            document.add_heading("决议与结论", level=1)
            for item in self.decisions:
                document.add_paragraph(
                    f"{item['text']}（第 {item['line']} 行）", style="List Bullet"
                )
        if self.actions:
            document.add_heading("行动项", level=1)
            table = document.add_table(rows=1, cols=4)
            table.style = "Table Grid"
            for cell, text in zip(table.rows[0].cells, ("行动", "负责人", "截止", "来源")):
                cell.text = text
            for action in self.actions:
                row = table.add_row().cells
                row[0].text = action.text
                row[1].text = action.owner or "待确认"
                row[2].text = action.due or "待确认"
                row[3].text = f"第 {action.line} 行"
        document.add_paragraph("本纪要由 Spark 自动生成，请人工复核。")
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

        summary_slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        summary_slide.shapes.title.text = "摘要与议题"
        body = summary_slide.placeholders[1].text_frame
        body.text = self.summary
        for topic in self.topics[:6]:
            paragraph = body.add_paragraph()
            paragraph.text = f"议题：{topic}"
            paragraph.level = 1
        if self.attendees:
            body.add_paragraph().text = "参会：" + "、".join(self.attendees[:12])

        if self.decisions:
            decisions_slide = presentation.slides.add_slide(presentation.slide_layouts[1])
            decisions_slide.shapes.title.text = "决议与结论"
            frame = decisions_slide.placeholders[1].text_frame
            frame.text = self.decisions[0]["text"]
            for item in self.decisions[1:8]:
                paragraph = frame.add_paragraph()
                paragraph.text = item["text"]
                paragraph.level = 1

        if self.actions:
            actions_slide = presentation.slides.add_slide(presentation.slide_layouts[5])
            actions_slide.shapes.title.text = "行动项"
            rows = min(len(self.actions), 8) + 1
            table = actions_slide.shapes.add_table(
                rows, 4, Inches(0.6), Inches(1.5), Inches(8.8), Inches(0.4 * rows)
            ).table
            for index, heading in enumerate(("行动", "负责人", "截止", "来源")):
                table.cell(0, index).text = heading
            for row_index, action in enumerate(self.actions[:8], 1):
                table.cell(row_index, 0).text = action.text[:80]
                table.cell(row_index, 1).text = action.owner or "待确认"
                table.cell(row_index, 2).text = action.due or "待确认"
                table.cell(row_index, 3).text = f"第 {action.line} 行"
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.text_frame.paragraphs:
                        for run in paragraph.runs:
                            run.font.size = Pt(11)

        buffer = BytesIO()
        presentation.save(buffer)
        return buffer.getvalue()


class MeetingMinutesService:
    def build(self, title: str, transcript: str) -> MeetingMinutes:
        raw_lines = [line.strip() for line in transcript.splitlines() if line.strip()]
        attendees: list[str] = []
        topics: list[str] = []
        decisions: list[dict[str, Any]] = []
        actions: list[ActionItem] = []

        for index, line in enumerate(raw_lines, 1):
            match = _ATTENDEES.match(line)
            if match:
                attendees = [
                    part.strip()
                    for part in re.split(r"[,，、;；]", match.group(1))
                    if part.strip()
                ]
                continue
            topic_line = _TOPIC_LINE.match(line)
            if topic_line:
                topics.append(topic_line.group(1).strip()[:60])
                continue
            topic = _TOPIC.match(line)
            if topic and len(topic.group(1)) <= 60:
                topics.append(topic.group(1).strip())
                continue
            if _DECISION.search(line):
                decisions.append({"text": line[:240], "line": index})
                continue
            if _ACTION.search(line):
                owner_match = _OWNER.search(line)
                owner = ""
                if owner_match:
                    owner = (owner_match.group(1) or owner_match.group(2) or "").strip()
                due_match = _DUE.search(line)
                due = due_match.group(1) if due_match else ""
                actions.append(
                    ActionItem(text=line[:240], owner=owner, due=due, line=index)
                )

        if not topics:
            fallback = [
                line
                for line in raw_lines
                if not _DECISION.search(line) and not _ACTION.search(line)
            ][:3]
            topics = [line[:60] for line in fallback]
        summary = (
            f"本次会议围绕 {len(topics)} 个议题展开，"
            f"形成 {len(decisions)} 项决议、{len(actions)} 项行动。"
        )
        if decisions:
            summary += f" 首项决议：{decisions[0]['text'][:80]}"
        return MeetingMinutes(
            title=title.strip() or "未命名会议",
            summary=summary,
            attendees=attendees,
            topics=topics,
            decisions=decisions,
            actions=actions,
            citations=len(decisions) + len(actions),
        )
