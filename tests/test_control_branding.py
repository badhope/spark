"""Tenant branding for generated office artifacts.

The point of these tests is that branding is *presentation only*: the same
transcript must produce the same decisions, actions and citations whether or not
a logo and a custom colour are configured. They also pin the degradation rules —
an invalid colour or an unusable logo must never fail a request.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spark.control.app import create_app
from spark.control.branding import (
    MAX_LOGO_BYTES,
    Brand,
    brand_from_settings,
    docx_footer,
    hex_colour,
)
from spark.control.office import MeetingMinutesService
from spark.control.settings import ControlSettings

TRANSCRIPT = """参会：王敏、李哲
议题：季度回顾
决定：本季度按期发布。
李哲：行动项：补齐回归用例，负责人：李哲，截止：2026-11-20
"""


def _png(path: Path, width: int = 40, height: int = 16) -> Path:
    """A tiny valid RGB PNG, so the logo path is exercised for real."""
    rows = b"".join(b"\x00" + bytes([31, 78, 121] * width) for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
    return path


def _client(tmp_path: Path, **overrides) -> TestClient:
    settings = ControlSettings(
        database_url=f"sqlite:///{(tmp_path / 'brand.db').as_posix()}",
        artifact_root=str(tmp_path / "artifacts"),
        document_root=str(tmp_path / "documents"),
        auth_mode="dev",
        allow_dev_auth=True,
        **overrides,
    )
    return TestClient(create_app(settings))


def test_defaults_are_neutral() -> None:
    brand = Brand()
    assert brand.name == "Spark"
    assert brand.footer_text() == "本文件由Spark自动生成，请人工复核"
    assert brand.logo() is None
    assert docx_footer(None) == "本文件由 Spark 自动生成，请人工复核。"


def test_invalid_values_degrade(tmp_path: Path) -> None:
    settings = ControlSettings(
        brand_name="  ",
        brand_primary="not-a-colour",
        brand_accent="#12345",
        brand_footer="",
        brand_logo=str(tmp_path / "missing.png"),
    )
    brand = brand_from_settings(settings)
    assert brand.name == "Spark", "blank name must fall back, not blank the document"
    assert brand.primary == "#1F4E79"
    assert brand.accent == "#17365D"
    assert brand.footer_text().startswith("本文件由Spark")
    assert brand.logo() is None
    # to_dict only advertises a logo the renderers can use.
    assert brand.to_dict()["logo"] == ""


def test_logo_validation_rules(tmp_path: Path) -> None:
    png = _png(tmp_path / "logo.png")
    assert Brand(logo_path=str(png)).logo() == png
    svg = tmp_path / "logo.svg"
    svg.write_text("<svg/>")
    assert Brand(logo_path=str(svg)).logo() is None, "only raster logos are accepted"
    big = tmp_path / "big.png"
    with big.open("wb") as handle:
        handle.truncate(MAX_LOGO_BYTES + 1)
    assert Brand(logo_path=str(big)).logo() is None


def test_hex_colour_for_spreadsheet_libraries() -> None:
    assert hex_colour("#8b1e3f") == "8B1E3F"
    assert hex_colour("") == "1F4E79"
    assert hex_colour("nope") == "1F4E79"


def test_branding_changes_presentation_not_content(tmp_path: Path) -> None:
    plain = MeetingMinutesService().build("季度回顾", TRANSCRIPT)
    branded = MeetingMinutesService().build("季度回顾", TRANSCRIPT)
    branded.brand = Brand(
        name="星火科技",
        primary="#8B1E3F",
        accent="#4A0F23",
        footer="星火科技 · 内部资料",
        logo_path=str(_png(tmp_path / "logo.png")),
    )
    # Content is identical: only the footer line differs.
    assert plain.attendees == branded.attendees
    assert plain.decisions == branded.decisions
    assert plain.actions == branded.actions
    assert plain.citations == branded.citations
    assert "Spark" in plain.markdown()
    assert "星火科技 · 内部资料" in branded.markdown()
    assert "Spark" not in branded.markdown()


def test_brand_endpoint_reports_effective_values(tmp_path: Path) -> None:
    logo = _png(tmp_path / "logo.png")
    with _client(tmp_path, brand_name="星火科技", brand_primary="#8B1E3F", brand_logo=str(logo)) as client:
        body = client.get("/api/v1/office/brand").json()
    assert body["name"] == "星火科技"
    assert body["primary"] == "#8B1E3F"
    assert body["logo"] == str(logo)
    assert "星火科技" in body["footer"]


def test_exports_carry_the_logo(tmp_path: Path) -> None:
    logo = _png(tmp_path / "logo.png")
    zipfile = pytest.importorskip("zipfile")
    import io

    with _client(tmp_path, brand_name="星火科技", brand_logo=str(logo),
                 brand_footer="星火科技·内部资料") as client:
        created = client.post(
            "/api/v1/office/meetings",
            json={"title": "品牌会议", "transcript": TRANSCRIPT},
        )
        assert created.status_code == 201, created.text
        task_id = created.json()["task_id"]

        docx = client.get(f"/api/v1/office/meetings/{task_id}/download?format=docx")
        assert docx.status_code == 200
        archive = zipfile.ZipFile(io.BytesIO(docx.content))
        assert any(name.startswith("word/media/") for name in archive.namelist())
        document_xml = archive.read("word/document.xml").decode("utf-8")
        assert "星火科技·内部资料" in document_xml

        pptx = client.get(f"/api/v1/office/meetings/{task_id}/download?format=pptx")
        assert pptx.status_code == 200
        deck = zipfile.ZipFile(io.BytesIO(pptx.content))
        assert any(name.startswith("ppt/media/") for name in deck.namelist())

        pdf = client.get(f"/api/v1/office/meetings/{task_id}/download?format=pdf")
        assert pdf.status_code == 200
        reader = pytest.importorskip("pypdf").PdfReader(io.BytesIO(pdf.content))
        first_page = reader.pages[0]
        assert "星火科技·内部资料" in first_page.extract_text()
        assert len(first_page.images) >= 1, "logo missing from the PDF"


def test_broken_logo_never_blocks_the_export(tmp_path: Path) -> None:
    # A file that exists but is not a decodable image: renderers must survive.
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not really a png")
    with _client(tmp_path, brand_logo=str(broken)) as client:
        created = client.post(
            "/api/v1/office/meetings",
            json={"title": "坏 logo", "transcript": TRANSCRIPT},
        )
        assert created.status_code == 201
        artifacts = {item["kind"] for item in created.json()["artifacts"]}
    assert {"meeting_minutes_md", "meeting_minutes_docx", "meeting_minutes_pdf"} <= artifacts
