"""Report/spreadsheet office slice: parsing, artifacts, API, UI."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spark.control.app import create_app
from spark.control.report import ReportService
from spark.control.settings import ControlSettings

METRICS = "\n".join(
    [
        "会议纪要产出,42,30,份",
        "审批平均时长,1.8,2.5,天",
        "文档生成量,128,100,份",
        "人工返工率,6,10,%",
    ]
)
SECTIONS = "## 结论\n办公自动化覆盖提升。\n## 风险\n指标口径待对齐。"


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


def test_report_service_builds_metrics_and_artifacts() -> None:
    report = ReportService().build("Q4 报告", "", METRICS, SECTIONS)
    assert len(report.metrics) == 4
    assert report.metrics[0].name == "会议纪要产出"
    assert report.metrics[0].value == 42
    assert report.metrics[0].target == 30
    assert report.metrics[0].achievement == pytest.approx(1.4)
    assert "Q4 报告" in report.markdown()
    assert report.xlsx_bytes()[:2] == b"PK"
    assert report.docx_bytes()[:2] == b"PK"
    assert report.pdf_bytes()[:5] == b"%PDF-"

    lower = ReportService().build("返工率", "", "人工返工率,6,10,%,lower", "")
    assert lower.metrics[0].direction == "lower"
    assert lower.metrics[0].achievement == pytest.approx(10 / 6)


def test_report_api_create_download_approve(client: TestClient) -> None:
    created = client.post(
        "/api/v1/office/reports",
        json={"title": "Q4 报告", "summary": "", "metrics": METRICS, "sections": SECTIONS},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "draft"
    assert len(body["report"]["metrics"]) == 4
    kinds = {item["kind"] for item in body["artifacts"]}
    assert {"report_md", "report_xlsx", "report_docx", "report_pptx", "report_pdf"} <= kinds

    task_id = body["task_id"]
    xlsx = client.get(f"/api/v1/office/reports/{task_id}/download?format=xlsx")
    assert xlsx.status_code == 200
    assert xlsx.content[:2] == b"PK"
    md = client.get(f"/api/v1/office/reports/{task_id}/download?format=md")
    assert md.status_code == 200
    assert "指标" in md.text
    pptx = client.get(f"/api/v1/office/reports/{task_id}/download?format=pptx")
    assert pptx.status_code == 200
    assert pptx.content[:2] == b"PK"
    pdf = client.get(f"/api/v1/office/reports/{task_id}/download?format=pdf")
    assert pdf.status_code == 200
    assert pdf.content[:5] == b"%PDF-"

    approved = client.post(f"/api/v1/office/reports/{task_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    listing = client.get("/api/v1/office/reports")
    assert listing.status_code == 200
    assert listing.json()[0]["metric_count"] == 4


def test_report_rejects_empty_metrics(client: TestClient) -> None:
    response = client.post(
        "/api/v1/office/reports",
        json={"title": "bad", "metrics": "not a metric"},
    )
    assert response.status_code == 422


def test_office_ui_has_report_tab(client: TestClient) -> None:
    page = client.get("/office")
    assert page.status_code == 200
    assert "报告 / 表格" in page.text
