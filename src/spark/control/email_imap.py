"""IMAP connector for the office document inbox.

Pulls the latest messages from a configured mailbox, stores each raw message as
an ``.eml`` file under ``document_root/inbox`` and reuses DocumentIngestor for
text extraction. Credentials are read from settings and never logged or
returned.
"""

from __future__ import annotations

import imaplib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from spark.control.documents import DocumentIngestor, ExtractedDocument
from spark.control.settings import ControlSettings


@dataclass
class FetchedEmail:
    filename: str
    subject: str
    document: ExtractedDocument

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "subject": self.subject,
            "document": self.document.to_dict(),
        }


class ImapConnector:
    def __init__(self, settings: ControlSettings, ingestor: DocumentIngestor) -> None:
        self.settings = settings
        self.ingestor = ingestor

    @property
    def configured(self) -> bool:
        return self.settings.imap_configured

    def fetch_latest(
        self,
        limit: int = 5,
        mailbox: str = "",
        search: str = "ALL",
    ) -> list[FetchedEmail]:
        if not self.configured:
            raise RuntimeError(
                "IMAP is not configured: set SPARK_CONTROL_IMAP_HOST, "
                "SPARK_CONTROL_IMAP_USER and SPARK_CONTROL_IMAP_PASSWORD"
            )
        settings = self.settings
        client: imaplib.IMAP4
        if settings.imap_ssl:
            client = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port)
        else:
            client = imaplib.IMAP4(settings.imap_host, settings.imap_port)
        try:
            client.login(settings.imap_user, settings.imap_password)
            client.select(mailbox or settings.imap_mailbox, readonly=True)
            status, data = client.search(None, search or "ALL")
            if status != "OK" or not data or not data[0]:
                return []
            ids = data[0].split()[-limit:]
            inbox = Path(settings.document_root).expanduser().resolve() / "inbox"
            inbox.mkdir(parents=True, exist_ok=True)
            out: list[FetchedEmail] = []
            for message_id in reversed(ids):
                status, payload = client.fetch(message_id, "(RFC822)")
                if status != "OK":
                    continue
                raw = self._raw_message(payload)
                if raw is None:
                    continue
                import email
                from email import policy

                message = email.message_from_bytes(raw, policy=policy.default)
                subject = str(message.get("subject") or "(no subject)")
                filename = f"inbox/{self._safe_name(subject)}-{uuid4().hex[:8]}.eml"
                path = Path(settings.document_root).expanduser().resolve() / filename
                path.write_bytes(raw)
                out.append(
                    FetchedEmail(
                        filename=filename,
                        subject=subject,
                        document=self.ingestor.extract(path),
                    )
                )
            return out
        finally:
            try:
                client.logout()
            except Exception:  # noqa: BLE001 - logout must not mask fetch errors
                pass

    @staticmethod
    def _raw_message(payload: Any) -> bytes | None:
        for item in payload or []:
            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
                return item[1]
        return None

    @staticmethod
    def _safe_name(subject: str) -> str:
        stem = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", subject).strip("._-")
        return (stem or "mail")[:40]
