"""附件物化/抽取与消息反馈的回归测试。

覆盖两个容易“看起来能用”但实际会丢数据的点：
1. PDF 抽取失败时仍要落下文件路径，不能让附件变成空块；
2. 反馈必须限定当前会话的 assistant 消息，且再次点击同方向可取消。
"""
from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

from spark.models import ChatMessage
from spark.store import SessionStore
from spark.web.utils import inject_attachments, parse_attachments, prepare_attachments, split_attachments


def _pdf_bytes(text: str) -> bytes:
    # 用 pypdf 空页写入器无法造文本；改用 reportlab（已在 office 依赖里）。
    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(72, 720, text)
    pdf.save()
    return buffer.getvalue()



def test_pdf_attachment_is_saved_and_text_extracted(tmp_path: Path) -> None:
    data = _pdf_bytes("Spark PDF fixture: answer is 42")
    attachments = parse_attachments(
        [{"name": "fixture.pdf", "mime": "application/pdf", "data": base64.b64encode(data).decode()}]
    )
    prepared = prepare_attachments(tmp_path, "s1", attachments)
    assert prepared[0]["kind"] == "pdf"
    assert prepared[0]["path"].startswith(".spark/attachments/s1/")
    assert "Spark PDF fixture" in prepared[0]["content"]
    prompt = inject_attachments("summarize", prepared)
    visible, names = split_attachments(prompt)
    assert visible == "summarize"
    assert names == ["fixture.pdf"]
    saved = tmp_path / prepared[0]["path"]
    assert saved.is_file()


def test_unknown_binary_is_saved_even_without_extracted_text(tmp_path: Path) -> None:
    attachments = parse_attachments(
        [{"name": "blob.bin", "mime": "application/octet-stream", "data": base64.b64encode(b"\x00\x01data").decode()}]
    )
    prepared = prepare_attachments(tmp_path, "s2", attachments)
    assert prepared[0]["path"].endswith("blob.bin")
    assert prepared[0]["content"] == ""
    assert "请用文件/表格工具读取上面的路径" in inject_attachments("look", prepared)
    assert (tmp_path / prepared[0]["path"]).read_bytes() == b"\x00\x01data"


def test_feedback_is_scoped_and_toggleable(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    try:
        sid = store.create_session(tmp_path, "mock")
        other = store.create_session(tmp_path, "mock")
        user_id = store.append_message(sid, ChatMessage(role="user", content="hi"))
        assistant_id = store.append_message(sid, ChatMessage(role="assistant", content="hello"))
        other_id = store.append_message(other, ChatMessage(role="assistant", content="other"))

        assert store.set_message_feedback(sid, user_id, "up") is None
        assert store.set_message_feedback(sid, other_id, "up") is None
        assert store.set_message_feedback(sid, assistant_id, "up") == {
            "message_id": assistant_id,
            "rating": "up",
            "comment": "",
        }
        assert store.list_message_feedback(sid) == {assistant_id: "up"}
        assert store.list_message_feedback(other) == {}

        store.set_message_feedback(sid, assistant_id, "down")
        assert store.list_message_feedback(sid) == {assistant_id: "down"}
        store.set_message_feedback(sid, assistant_id, None)
        assert store.list_message_feedback(sid) == {}
        assert store.schema_version() == 3
    finally:
        store.close()
