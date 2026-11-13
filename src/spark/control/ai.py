"""Model-assisted office text with deterministic fallback.

The deterministic parsers stay the source of truth for citations and action
items. The model only writes an additional summary / narrative layer; any
failure, timeout or empty answer silently falls back to the deterministic
artifact.
"""

from __future__ import annotations

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from spark.control.gateway import ModelGateway
from spark.control.office import MeetingMinutes
from spark.control.report import Report, ReportSection
from spark.control.settings import ControlSettings

logger = logging.getLogger(__name__)

_MINUTES_SYSTEM = (
    "你是企业会议纪要助手。只根据给定材料写 2-4 句中文摘要，"
    "突出结论、决策和风险；不要编造事实，不要输出标题或 Markdown 列表。"
)
_REPORT_SYSTEM = (
    "你是企业报告助手。根据给定指标和已有章节，写两个 Markdown 小节："
    "## 结论 和 ## 风险。每个小节 1-3 条，用 - 开头；"
    "只依据给定数据，不编造数字，不重复原文。"
)


def _run_sync(coro: Any) -> Any:
    """Run a coroutine from sync FastAPI handlers or tests."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class OfficeAssistant:
    def __init__(self, gateway: ModelGateway, settings: ControlSettings) -> None:
        self.gateway = gateway
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.model_assist and self.gateway.configured)

    def minutes_summary(self, minutes: MeetingMinutes, transcript: str) -> str:
        if not self.enabled:
            return ""
        material = [
            f"标题：{minutes.title}",
            f"参会：{'、'.join(minutes.attendees) or '未知'}",
            f"议题：{'；'.join(minutes.topics[:8]) or '无'}",
            "决议：" + ("；".join(item["text"] for item in minutes.decisions[:8]) or "无"),
            "行动项："
            + (
                "；".join(
                    f"{a.text}（{a.owner or '待确认'}，{a.due or '待确认'}）"
                    for a in minutes.actions[:8]
                )
                or "无"
            ),
            "原始材料（截断）：",
            transcript[:3500],
        ]
        try:
            text = _run_sync(
                self.gateway.complete(
                    [
                        {"role": "system", "content": _MINUTES_SYSTEM},
                        {"role": "user", "content": "\n".join(material)},
                    ],
                    timeout=self.settings.model_timeout_sec,
                    max_tokens=3000,
                )
            )
        except Exception as exc:  # noqa: BLE001 - optional enrichment
            logger.warning("office_minutes_summary_failed: %s", str(exc)[:200])
            return ""
        return " ".join(text.split())[:1200]

    def report_sections(self, report: Report) -> list[ReportSection]:
        if not self.enabled:
            return []
        metrics = "\n".join(
            f"- {m.name}: 实际 {m.value:g}{m.unit}, 目标 "
            f"{m.target:g}{m.unit}" if m.target is not None else f"- {m.name}: 实际 {m.value:g}{m.unit}"
            for m in report.metrics[:20]
        )
        existing = "\n\n".join(
            f"## {section.heading}\n{section.body}" for section in report.sections[:8]
        )
        material = [
            f"报告：{report.title}",
            f"摘要：{report.summary}",
            "指标：",
            metrics or "无",
            "已有章节：",
            existing or "无",
        ]
        try:
            text = _run_sync(
                self.gateway.complete(
                    [
                        {"role": "system", "content": _REPORT_SYSTEM},
                        {"role": "user", "content": "\n".join(material)},
                    ],
                    timeout=self.settings.model_timeout_sec,
                    max_tokens=6000,
                )
            )
        except Exception as exc:  # noqa: BLE001 - optional enrichment
            logger.warning("office_report_sections_failed: %s", str(exc)[:200])
            return []
        return self._parse_sections(text)

    @staticmethod
    def _parse_sections(text: str) -> list[ReportSection]:
        sections: list[ReportSection] = []
        for block in re.split(r"^##\s+", text, flags=re.MULTILINE)[1:]:
            heading, _, body = block.partition("\n")
            heading = heading.strip()[:80]
            body = body.strip()[:1500]
            if heading and body:
                if not heading.startswith("AI "):
                    heading = f"AI {heading}"
                sections.append(ReportSection(heading=heading, body=body))
            if len(sections) >= 4:
                break
        return sections
