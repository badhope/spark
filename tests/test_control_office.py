"""Meeting-minutes first office slice: deterministic parsing, API, artifacts, UI."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spark.control.app import create_app
from spark.control.office import MeetingMinutesService
from spark.control.settings import ControlSettings

TRANSCRIPT = """参会：王敏、李哲、陈雪
议题：会议纪要试点
王敏：本周先做会议纪要与行动项。
决定：首批只覆盖会议转写、结构化纪要、行动项和 docx 导出。
李哲：行动项：接入控制面 API，负责人：李哲，截止：2026-11-20
陈雪：行动项：补 3 个真实会议样例，负责人：陈雪，截止：2026-11-18
决定：所有生成内容必须带来源行号，人工批准后才能对外发送。
"""


@pytest.fixture()
def client(tmp_path: Path):
    settings = ControlSettings(
        database_url=f"sqlite:///{(tmp_path / 'control.db').as_posix()}",
        artifact_root=str(tmp_path / "artifacts"),
        auth_mode="dev",
        allow_dev_auth=True,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def test_service_extracts_decisions_actions_and_citations() -> None:
    minutes = MeetingMinutesService().build("产品周会", TRANSCRIPT)
    assert minutes.attendees == ["王敏", "李哲", "陈雪"]
    assert len(minutes.decisions) == 2
    assert len(minutes.actions) == 2
    assert minutes.actions[0].owner == "李哲"
    assert minutes.actions[0].due == "2026-11-20"
    assert minutes.citations == 4
    assert "来源行号" in minutes.markdown()


def test_narrated_english_is_not_mistaken_for_a_decision() -> None:
    """A spoken sentence is narration; only an explicit marker records a decision.

    Before the marker had to introduce the line, "We decided to ship on Friday"
    inside a paragraph matched, so an entire English transcript collapsed into a
    single "decision" and the topics list came out empty.
    """
    minutes = MeetingMinutesService().build(
        "standup",
        "Good morning. We decided to ship the new release on Friday. "
        "Alice will prepare the documentation.",
    )
    assert minutes.decisions == []
    assert minutes.actions == []
    assert minutes.topics, "the paragraph should still yield a topic"


def test_explicit_english_markers_still_parse() -> None:
    minutes = MeetingMinutesService().build(
        "standup",
        "Decision: ship the release on Friday.\n"
        "Action item: prepare the docs, owner: Alice, due: 2026-11-20",
    )
    assert len(minutes.decisions) == 1
    assert len(minutes.actions) == 1
    assert minutes.actions[0].owner == "Alice"
    assert minutes.actions[0].due == "2026-11-20"


def test_meeting_create_download_approve(client: TestClient) -> None:
    created = client.post(
        "/api/v1/office/meetings",
        json={"title": "产品周会", "transcript": TRANSCRIPT},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "draft"
    assert len(body["minutes"]["actions"]) == 2
    kinds = {item["kind"] for item in body["artifacts"]}
    assert {
        "meeting_minutes_md",
        "meeting_minutes_docx",
        "meeting_minutes_pptx",
        "meeting_minutes_pdf",
    } <= kinds

    task_id = body["task_id"]
    md = client.get(f"/api/v1/office/meetings/{task_id}/download?format=md")
    assert md.status_code == 200
    assert "行动项" in md.text
    docx = client.get(f"/api/v1/office/meetings/{task_id}/download?format=docx")
    assert docx.status_code == 200
    assert docx.content[:2] == b"PK"
    pptx = client.get(f"/api/v1/office/meetings/{task_id}/download?format=pptx")
    assert pptx.status_code == 200
    assert pptx.content[:2] == b"PK"
    pdf = client.get(f"/api/v1/office/meetings/{task_id}/download?format=pdf")
    assert pdf.status_code == 200
    assert pdf.content[:5] == b"%PDF-"
    assert pdf.headers["content-type"] == "application/pdf"

    approved = client.post(f"/api/v1/office/meetings/{task_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    listing = client.get("/api/v1/office/meetings")
    assert listing.status_code == 200
    assert listing.json()[0]["task_id"] == task_id


def test_member_can_read_but_not_create(client: TestClient) -> None:
    member = {"X-Spark-Roles": "member"}
    assert client.get("/api/v1/office/meetings", headers=member).status_code == 200
    denied = client.post(
        "/api/v1/office/meetings",
        json={"title": "x", "transcript": TRANSCRIPT},
        headers=member,
    )
    assert denied.status_code == 403


def test_office_ui_is_served(client: TestClient) -> None:
    page = client.get("/office")
    assert page.status_code == 200
    assert "会议纪要与行动项" in page.text
    assert 'id="document-file"' in page.text
    assert "上传文档" in page.text
