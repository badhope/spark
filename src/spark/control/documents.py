"""Office document ingestion for the control plane.

Extracts readable text from docx/pdf/xlsx/pptx/text files using the same
mature libraries the office workbench already depends on. Files are read from a
dedicated document root; the API never accepts arbitrary server paths.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARS = 300_000
MAX_PDF_PAGES = 100
MAX_SHEET_ROWS = 1000
MAX_OCR_PDF_PAGES = 30

_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".toml", ".log"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
SUPPORTED_SUFFIXES = (
    _TEXT_SUFFIXES
    | _IMAGE_SUFFIXES
    | {".docx", ".xlsx", ".pptx", ".pdf", ".html", ".htm", ".eml", ".msg"}
)


@dataclass
class ExtractedDocument:
    filename: str
    kind: str
    text: str
    parts: int = 1
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "kind": self.kind,
            "text": self.text,
            "parts": self.parts,
            "chars": len(self.text),
            "warnings": list(self.warnings),
        }


class DocumentIngestor:
    _OCR_ENGINE = None
    _OCR_LOCK = threading.Lock()

    @classmethod
    def _get_ocr(cls):
        with cls._OCR_LOCK:
            if cls._OCR_ENGINE is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                except Exception as exc:  # pragma: no cover - optional dependency
                    raise RuntimeError(
                        "image/scanned-PDF OCR requires rapidocr-onnxruntime"
                    ) from exc
                cls._OCR_ENGINE = RapidOCR()
            return cls._OCR_ENGINE

    @staticmethod
    def _ocr_result_text(result) -> str:
        lines: list[str] = []
        for item in result or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                text = str(item[1]).strip()
                if text:
                    lines.append(text)
        return "\n".join(lines)

    def _extract_image(self, path: Path) -> tuple[str, int]:
        engine = self._get_ocr()
        result, _elapsed = engine(str(path))
        text = self._ocr_result_text(result)
        return text, 1

    @staticmethod
    def _ocr_pdf(path: Path, warnings: list[str]) -> tuple[str, int]:
        try:
            import numpy as np
            import pypdfium2 as pdfium
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("scanned PDF OCR requires pypdfium2 and numpy") from exc
        engine = DocumentIngestor._get_ocr()
        document = pdfium.PdfDocument(str(path))
        lines: list[str] = []
        pages = min(len(document), MAX_OCR_PDF_PAGES)
        if len(document) > MAX_OCR_PDF_PAGES:
            warnings.append(f"only first {MAX_OCR_PDF_PAGES} PDF pages were OCR'd")
        for index in range(pages):
            page = document[index]
            bitmap = page.render(scale=2.0)
            image = bitmap.to_pil()
            result, _elapsed = engine(np.array(image))
            lines.append(f"# Page {index + 1}")
            lines.append(DocumentIngestor._ocr_result_text(result))
        return "\n".join(lines), pages

    def extract(self, path: Path) -> ExtractedDocument:
        if not path.is_file():
            raise FileNotFoundError(f"document not found: {path.name}")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError(
                f"document is too large ({size} bytes, limit {MAX_FILE_BYTES})"
            )
        suffix = path.suffix.lower()
        warnings: list[str] = []
        if suffix in _TEXT_SUFFIXES:
            text = self._read_text(path)
            parts = 1
        elif suffix == ".docx":
            text, parts = self._extract_docx(path)
        elif suffix == ".xlsx":
            text, parts = self._extract_xlsx(path)
        elif suffix == ".pptx":
            text, parts = self._extract_pptx(path)
        elif suffix == ".pdf":
            text, parts = self._extract_pdf(path, warnings)
        elif suffix in {".html", ".htm"}:
            text, parts = self._extract_html(path)
        elif suffix == ".eml":
            text, parts = self._extract_eml(path)
        elif suffix == ".msg":
            text, parts = self._extract_msg(path)
        elif suffix in _IMAGE_SUFFIXES:
            text, parts = self._extract_image(path)
        else:
            raise ValueError(f"unsupported document type: {suffix or 'unknown'}")
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS]
            warnings.append(f"text truncated at {MAX_TEXT_CHARS} chars")
        if not text.strip():
            warnings.append("no readable text extracted")
        return ExtractedDocument(
            filename=path.name,
            kind=suffix.lstrip(".") or "text",
            text=text,
            parts=parts,
            warnings=warnings,
        )

    @staticmethod
    def _read_text(path: Path) -> str:
        raw = path.read_bytes()
        for encoding in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _extract_docx(path: Path) -> tuple[str, int]:
        try:
            from docx import Document
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("docx ingestion requires python-docx") from exc
        document = Document(str(path))
        lines = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
        for table in document.tables:
            for row in table.rows:
                values = [cell.text.strip() for cell in row.cells]
                if any(values):
                    lines.append(" | ".join(values))
        return "\n".join(lines), 1

    @staticmethod
    def _extract_xlsx(path: Path) -> tuple[str, int]:
        try:
            from openpyxl import load_workbook
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("xlsx ingestion requires openpyxl") from exc
        workbook = load_workbook(str(path), read_only=True, data_only=True)
        lines: list[str] = []
        sheet_count = 0
        for sheet in workbook.worksheets:
            sheet_count += 1
            lines.append(f"# Sheet: {sheet.title}")
            for index, row in enumerate(sheet.iter_rows(values_only=True)):
                if index >= MAX_SHEET_ROWS:
                    lines.append("[sheet truncated]")
                    break
                values = [str(value).strip() for value in row if value is not None]
                if values:
                    lines.append(" | ".join(values))
        workbook.close()
        return "\n".join(lines), sheet_count

    @staticmethod
    def _extract_pptx(path: Path) -> tuple[str, int]:
        try:
            from pptx import Presentation
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("pptx ingestion requires python-pptx") from exc
        presentation = Presentation(str(path))
        lines: list[str] = []
        for index, slide in enumerate(presentation.slides, 1):
            lines.append(f"# Slide {index}")
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False):
                    text = shape.text_frame.text.strip()
                    if text:
                        lines.append(text)
                if getattr(shape, "has_table", False):
                    for row in shape.table.rows:
                        values = [cell.text.strip() for cell in row.cells]
                        if any(values):
                            lines.append(" | ".join(values))
            if slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    lines.append(f"[notes] {notes}")
        return "\n".join(lines), len(presentation.slides)

    @staticmethod
    def _extract_pdf(path: Path, warnings: list[str]) -> tuple[str, int]:
        try:
            from pypdf import PdfReader
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("pdf ingestion requires pypdf") from exc
        reader = PdfReader(str(path))
        lines: list[str] = []
        pages = reader.pages[:MAX_PDF_PAGES]
        if len(reader.pages) > MAX_PDF_PAGES:
            warnings.append(f"only first {MAX_PDF_PAGES} PDF pages were read")
        for index, page in enumerate(pages, 1):
            lines.append(f"# Page {index}")
            lines.append(page.extract_text() or "")
        text = "\n".join(lines)
        if len(text.replace("# Page", "").strip()) < 20:
            warnings.append("PDF has little embedded text; OCR fallback used")
            return DocumentIngestor._ocr_pdf(path, warnings)
        return text, len(pages)

    @staticmethod
    def _extract_eml(path: Path) -> tuple[str, int]:
        import email
        from email import policy

        message = email.message_from_bytes(path.read_bytes(), policy=policy.default)
        lines = [
            f"# Email: {message.get('subject', '(no subject)')}",
            f"From: {message.get('from', '')}",
            f"To: {message.get('to', '')}",
            f"Date: {message.get('date', '')}",
            "",
        ]
        body = message.get_body(preferencelist=("plain", "html"))
        if body is not None:
            content = body.get_content()
            if body.get_content_type() == "text/html":
                try:
                    import trafilatura

                    extracted = trafilatura.extract(content, include_comments=False, include_tables=True)
                except Exception:
                    extracted = None
                if extracted:
                    content = extracted
            lines.append(str(content))
        attachments = list(message.iter_attachments())
        if attachments:
            lines += ["", "Attachments:"] + [
                f"- {part.get_filename() or '(unnamed)'}" for part in attachments
            ]
        return "\n".join(lines), 1

    @staticmethod
    def _extract_msg(path: Path) -> tuple[str, int]:
        try:
            import extract_msg
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Outlook .msg ingestion requires extract-msg") from exc
        message = extract_msg.Message(str(path))
        try:
            lines = [
                f"# Email: {message.subject or '(no subject)'}",
                f"From: {message.sender or ''}",
                f"To: {message.to or ''}",
                f"Date: {message.date or ''}",
                "",
                str(message.body or ""),
            ]
            attachments = list(message.attachments or [])
            if attachments:
                lines += ["", "Attachments:"] + [
                    f"- {getattr(item, 'longFilename', None) or getattr(item, 'shortFilename', None) or '(unnamed)'}"
                    for item in attachments
                ]
            return "\n".join(lines), 1
        finally:
            message.close()

    @staticmethod
    def _extract_html(path: Path) -> tuple[str, int]:
        raw = path.read_text(encoding="utf-8", errors="replace")
        try:
            import trafilatura

            text = trafilatura.extract(raw, include_comments=False, include_tables=True)
            if text:
                return text, 1
        except Exception:
            pass
        from html.parser import HTMLParser

        class _Text(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.parts: list[str] = []

            def handle_data(self, data: str) -> None:
                if data.strip():
                    self.parts.append(data.strip())

        parser = _Text()
        parser.feed(raw)
        return "\n".join(parser.parts), 1
