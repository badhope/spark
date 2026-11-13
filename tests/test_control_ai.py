"""Model-assisted office text: enrichment, fallback, opt-in."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from spark.config import SparkConfig
from spark.control.app import create_app
from spark.control.gateway import ModelGateway
from spark.control.settings import ControlSettings

TRANSCRIPT = """参会：王敏、李哲
议题：办公工作台
决定：首批覆盖会议纪要与报告。
李哲：行动项：接入模型摘要，负责人：李哲，截止：2026-12-01
"""


class FakeGateway(ModelGateway):
    def __init__(self, *, fail: bool = False) -> None:
        super().__init__(SparkConfig())
        self.fail = fail
        self.calls: list[list[dict[str, str]]] = []

    @property
    def configured(self) -> bool:  # type: ignore[override]
        return True

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        timeout: float = 45.0,
        max_tokens: int = 900,
        temperature: float = 0.2,
    ) -> str:
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("model down")
        if "会议纪要" in messages[0]["content"]:
            return "模型摘要：会议确认了办公工作台的范围与行动项。"
        return "## 结论\n- 指标整体达成。\n## 风险\n- 样本量偏小。"


def _settings(tmp_path: Path, **overrides: Any) -> ControlSettings:
    return ControlSettings(
        database_url=f"sqlite:///{(tmp_path / 'control.db').as_posix()}",
        artifact_root=str(tmp_path / "artifacts"),
        auth_mode="dev",
        allow_dev_auth=True,
        **overrides,
    )


def test_meeting_gets_ai_summary_and_audit(tmp_path: Path) -> None:
    gateway = FakeGateway()
    app = create_app(_settings(tmp_path, model_assist=True), gateway=gateway)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/office/meetings",
            json={"title": "产品周会", "transcript": TRANSCRIPT},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["minutes"]["ai_summary"].startswith("模型摘要")
        task_id = body["task_id"]

        md = client.get(f"/api/v1/office/meetings/{task_id}/download?format=md")
        assert md.status_code == 200
        assert "AI 摘要" in md.text
        assert "模型摘要" in md.text

        audit = client.get("/api/v1/audit")
        assert audit.status_code == 200
        event = audit.json()[0]
        assert event["action"] == "meeting.minutes.create"
        assert event["meta"]["ai_summary"] is True
    assert len(gateway.calls) == 1


def test_report_gets_ai_sections(tmp_path: Path) -> None:
    gateway = FakeGateway()
    app = create_app(_settings(tmp_path, model_assist=True), gateway=gateway)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/office/reports",
            json={
                "title": "Q4 报告",
                "metrics": "会议纪要产出,42,30,份",
                "sections": "## 结论\n人工复核完成。",
            },
        )
        assert created.status_code == 201, created.text
        report = created.json()["report"]
        headings = [section["heading"] for section in report["sections"]]
        assert "结论" in headings
        assert "AI 风险" in headings
        md = client.get(
            f"/api/v1/office/reports/{created.json()['task_id']}/download?format=md"
        )
        assert md.status_code == 200
        assert "样本量偏小" in md.text


def test_model_failure_falls_back_to_deterministic(tmp_path: Path) -> None:
    gateway = FakeGateway(fail=True)
    app = create_app(_settings(tmp_path, model_assist=True), gateway=gateway)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/office/meetings",
            json={"title": "产品周会", "transcript": TRANSCRIPT},
        )
        assert created.status_code == 201, created.text
        assert created.json()["minutes"]["ai_summary"] == ""
    assert len(gateway.calls) == 1


def test_model_assist_is_opt_in(tmp_path: Path) -> None:
    gateway = FakeGateway()
    app = create_app(_settings(tmp_path), gateway=gateway)  # default off
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/office/meetings",
            json={"title": "产品周会", "transcript": TRANSCRIPT},
        )
        assert created.status_code == 201, created.text
        assert created.json()["minutes"]["ai_summary"] == ""
    assert gateway.calls == []
