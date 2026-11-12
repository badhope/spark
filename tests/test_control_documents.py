"""Document ingestion: docx/xlsx/pptx/text, path safety, meeting integration."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spark.control.app import create_app
from spark.control.documents import DocumentIngestor
from spark.control.settings import ControlSettings


def _make_docx(path: Path) -> None:
    from docx import Document

    document = Document()
    document.add_heading("产品周会", level=1)
    document.add_paragraph("参会：王敏、李哲")
    document.add_paragraph("决定：首批只做会议纪要。")
    document.add_paragraph("李哲：行动项：接入文档导入，负责人：李哲，截止：2026-11-30")
    document.save(path)


def _make_xlsx(path: Path) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "指标"
    sheet.append(("指标", "实际", "目标"))
    sheet.append(("会议纪要产出", 42, 30))
    workbook.save(path)


def _make_pptx(path: Path) -> None:
    from pptx import Presentation

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "季度复盘"
    slide.placeholders[1].text = "会议纪要与报告已形成稳定链路"
    presentation.save(path)


def _make_scan(path: Path, suffix: str = "png") -> None:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (900, 220), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 42)
    except Exception:
        font = ImageFont.load_default()
    draw.text((30, 50), "会议纪要 OCR 测试 2026", fill="black", font=font)
    draw.text((30, 120), "Action: review report", fill="black", font=font)
    image.save(path, format=suffix.upper() if suffix == "pdf" else None)


def _make_eml(path: Path) -> None:
    from email.message import EmailMessage

    message = EmailMessage()
    message["Subject"] = "会议纪要试点范围"
    message["From"] = "wang@example.com"
    message["To"] = "team@example.com"
    message["Date"] = "Wed, 12 Nov 2026 10:00:00 +0800"
    message.set_content("决定：首批只做会议纪要。\n行动项：接入邮件导入，负责人：王敏，截止：2026-12-01")
    path.write_bytes(bytes(message))


@pytest.fixture()
def client(tmp_path: Path):
    documents = tmp_path / "documents"
    documents.mkdir()
    _make_docx(documents / "meeting.docx")
    _make_xlsx(documents / "metrics.xlsx")
    _make_pptx(documents / "review.pptx")
    _make_eml(documents / "mail.eml")
    _make_scan(documents / "scan.png", "png")
    _make_scan(documents / "scanned.pdf", "pdf")
    (documents / "notes.md").write_text("会议纪要试点范围", encoding="utf-8")
    settings = ControlSettings(
        database_url=f"sqlite:///{(tmp_path / 'control.db').as_posix()}",
        artifact_root=str(tmp_path / "artifacts"),
        document_root=str(documents),
        auth_mode="dev",
        allow_dev_auth=True,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "filename,needle",
    [
        ("meeting.docx", "首批只做会议纪要"),
        ("metrics.xlsx", "会议纪要产出"),
        ("review.pptx", "季度复盘"),
        ("notes.md", "试点范围"),
        ("mail.eml", "接入邮件导入"),
        ("scan.png", "OCR测试"),
        ("scanned.pdf", "OCR测试"),
    ],
)
def test_extract_supported_documents(client: TestClient, filename: str, needle: str) -> None:
    response = client.post("/api/v1/office/documents/extract", json={"filename": filename})
    assert response.status_code == 200, response.text
    body = response.json()
    assert needle in body["text"]
    assert body["chars"] > 0


def test_extract_rejects_path_escape(client: TestClient) -> None:
    response = client.post(
        "/api/v1/office/documents/extract", json={"filename": "../secret.txt"}
    )
    assert response.status_code in {400, 404}


def test_extract_rejects_unsupported_type(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "documents" / "bad.exe").write_bytes(b"MZ")
    response = client.post(
        "/api/v1/office/documents/extract", json={"filename": "bad.exe"}
    )
    assert response.status_code == 422


def test_extract_msg_maps_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import extract_msg

    class FakeMessage:
        subject = "季度复盘"
        sender = "wang@example.com"
        to = "team@example.com"
        date = "Wed, 12 Nov 2026 10:00:00 +0800"
        body = "决定：继续推进。"
        attachments = []

        def close(self) -> None:
            return None

    monkeypatch.setattr(extract_msg, "Message", lambda _path: FakeMessage())
    text, _parts = DocumentIngestor()._extract_msg(tmp_path / "mail.msg")
    assert "季度复盘" in text
    assert "决定：继续推进" in text


def test_meeting_can_use_document_without_transcript(client: TestClient) -> None:
    response = client.post(
        "/api/v1/office/meetings",
        json={"title": "文档会议", "document_filename": "meeting.docx"},
    )
    assert response.status_code == 201, response.text
    minutes = response.json()["minutes"]
    assert minutes["decisions"]
    assert minutes["actions"]


def test_upload_document_roundtrip(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "upload.docx"
    _make_docx(path)
    response = client.post(
        "/api/v1/office/documents/upload",
        files={
            "file": (
                "upload.docx",
                path.read_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["filename"].startswith("uploads/")
    assert body["filename"].endswith(".docx")
    assert "首批只做会议纪要" in body["text"]

    meeting = client.post(
        "/api/v1/office/meetings",
        json={"title": "上传文档会议", "document_filename": body["filename"]},
    )
    assert meeting.status_code == 201, meeting.text
    assert meeting.json()["minutes"]["decisions"]


def test_upload_rejects_unsupported_type(client: TestClient) -> None:
    response = client.post(
        "/api/v1/office/documents/upload",
        files={"file": ("bad.exe", b"MZ", "application/octet-stream")},
    )
    assert response.status_code == 422


def test_upload_rejects_empty_file(client: TestClient) -> None:
    response = client.post(
        "/api/v1/office/documents/upload",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert response.status_code == 422


def test_upload_sanitizes_name_and_enforces_size(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = client.post(
        "/api/v1/office/documents/upload",
        files={"file": ("../../evil.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 201, response.text
    stored = response.json()["filename"]
    assert stored.startswith("uploads/")
    assert ".." not in stored
    assert (tmp_path / "documents" / stored).is_file()

    monkeypatch.setattr("spark.control.app.MAX_FILE_BYTES", 8)
    too_large = client.post(
        "/api/v1/office/documents/upload",
        files={"file": ("big.txt", b"123456789", "text/plain")},
    )
    assert too_large.status_code == 413


def test_imap_fetch_requires_configuration(client: TestClient) -> None:
    response = client.post("/api/v1/office/documents/imap", json={"limit": 3})
    assert response.status_code == 503
    assert "SPARK_CONTROL_IMAP_HOST" in response.json()["detail"]


def test_imap_fetch_stores_and_extracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import imaplib
    from email.message import EmailMessage

    raw = EmailMessage()
    raw["Subject"] = "会议纪要邮件"
    raw["From"] = "wang@example.com"
    raw["To"] = "team@example.com"
    raw.set_content("决定：采用 IMAP 导入。\n行动项：接入邮箱，负责人：王敏，截止：2026-12-05")
    payload = bytes(raw)

    class FakeImap:
        def __init__(self, host: str, port: int) -> None:
            self.host = host
            self.port = port
            self.mailbox = ""

        def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
            assert user == "dev@example.com"
            assert password == "secret"
            return "OK", []

        def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
            self.mailbox = mailbox
            assert readonly is True
            return "OK", [b"2"]

        def search(self, charset, criteria: str) -> tuple[str, list[bytes]]:
            assert criteria == "ALL"
            return "OK", [b"1 2"]

        def fetch(self, message_id: bytes, spec: str) -> tuple[str, list]:
            assert spec == "(RFC822)"
            return "OK", [(b"1 (RFC822 {123}", payload), b")"]

        def logout(self) -> tuple[str, list[bytes]]:
            return "BYE", []

    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeImap)
    documents = tmp_path / "documents"
    settings = ControlSettings(
        database_url=f"sqlite:///{(tmp_path / 'control.db').as_posix()}",
        artifact_root=str(tmp_path / "artifacts"),
        document_root=str(documents),
        auth_mode="dev",
        allow_dev_auth=True,
        imap_host="imap.example.com",
        imap_user="dev@example.com",
        imap_password="secret",
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/office/documents/imap",
            json={"limit": 2, "search": "ALL"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["fetched"] == 2
        first = body["documents"][0]
        assert first["subject"] == "会议纪要邮件"
        assert "采用 IMAP 导入" in first["text"]
        assert first["filename"].startswith("inbox/")
        assert (documents / first["filename"]).is_file()

        meeting = client.post(
            "/api/v1/office/meetings",
            json={"title": "邮件会议", "document_filename": first["filename"]},
        )
        assert meeting.status_code == 201, meeting.text
        assert meeting.json()["minutes"]["actions"]
