from __future__ import annotations

import base64
import binascii
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse

from spark.hooks import HookRegistry
from spark.sandbox import safe_bare_filename

logger = logging.getLogger(__name__)

INDEX_HTML = Path(__file__).with_name("index.html")

HOOK_EVENTS = tuple(HookRegistry.EVENTS)

MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_AGENTS_MD_BYTES = 1 * 1024 * 1024
MCP_REDACTED_HEADER = "[REDACTED]"


ATTACHMENT_OPEN = "\n\n⟦SPARK-ATTACHMENT:{name}⟧\n"
ATTACHMENT_CLOSE = "\n⟦/SPARK-ATTACHMENT⟧"
MAX_ATTACHMENTS = 8
MAX_ATTACHMENT_BYTES = 256 * 1024
MAX_BINARY_ATTACHMENT_BYTES = 2 * 1024 * 1024
MAX_EXTRACTED_CHARS = 240_000
MAX_PDF_PAGES = 80
MAX_SHEET_ROWS = 2_000
_ATTACHMENT_RE = re.compile(r"⟦SPARK-ATTACHMENT:([^⟧\n]+)⟧\n(.*?)\n⟦/SPARK-ATTACHMENT⟧", re.S)
_OFFICE_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx"}


def _safe_attachment_name(value: object) -> str:
    return safe_bare_filename(str(value or "")) or "attachment.bin"


def _decode_attachment_data(value: object) -> bytes | None:
    if not isinstance(value, str) or not value:
        return None
    raw = value.split(",", 1)[-1] if value.startswith("data:") else value
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(data) > MAX_BINARY_ATTACHMENT_BYTES:
        return None
    return data or None


def parse_attachments(items) -> list[dict[str, Any]]:
    """校验前端附件；文本保留正文，PDF/Office/二进制保留有界原始字节。

    图片走独立 images 字段。这里不展开文件系统路径；二进制落盘由
    :func:`prepare_attachments` 在当前会话工作区内完成。
    """
    out: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    for item in items[:MAX_ATTACHMENTS]:
        if not isinstance(item, dict):
            continue
        name = _safe_attachment_name(item.get("name"))
        content = item.get("content")
        if isinstance(content, str) and content:
            if len(content.encode("utf-8", errors="ignore")) > MAX_ATTACHMENT_BYTES:
                continue
            out.append({"name": name, "content": content, "size": len(content.encode("utf-8", errors="ignore")), "kind": "text"})
            continue
        data = _decode_attachment_data(item.get("data"))
        if data is None:
            continue
        mime = str(item.get("mime") or "application/octet-stream").strip()[:120]
        out.append({"name": name, "data": data, "mime": mime, "size": len(data)})
    return out


def _truncate_extracted(text: str) -> str:
    text = text.replace("\x00", "").strip()
    if len(text) > MAX_EXTRACTED_CHARS:
        return text[:MAX_EXTRACTED_CHARS] + "\n[…内容过长，已截断…]"
    return text


def _extract_pdf(data: bytes) -> tuple[str, str]:
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    chunks: list[str] = []
    for page in reader.pages[:MAX_PDF_PAGES]:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            logger.debug("PDF 页文本抽取失败", exc_info=True)
    text = "\n\n".join(chunk for chunk in chunks if chunk.strip())
    if len(reader.pages) > MAX_PDF_PAGES:
        text += f"\n\n[仅解析前 {MAX_PDF_PAGES} 页，共 {len(reader.pages)} 页]"
    return _truncate_extracted(text), "pdf"


def _extract_docx(data: bytes) -> tuple[str, str]:
    from io import BytesIO

    import docx

    document = docx.Document(BytesIO(data))
    chunks = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                chunks.append(" | ".join(cells))
    return _truncate_extracted("\n".join(chunks)), "docx"


def _extract_xlsx(data: bytes) -> tuple[str, str]:
    from io import BytesIO

    import openpyxl

    workbook = openpyxl.load_workbook(BytesIO(data), read_only=True, data_only=True)
    chunks: list[str] = []
    try:
        for sheet in workbook.worksheets:
            chunks.append(f"## Sheet: {sheet.title}")
            rows = 0
            for row in sheet.iter_rows(values_only=True):
                if rows >= MAX_SHEET_ROWS:
                    chunks.append("[…sheet 行数过多，已截断…]")
                    break
                values = ["" if cell is None else str(cell) for cell in row]
                if any(values):
                    chunks.append("\t".join(values))
                rows += 1
    finally:
        workbook.close()
    return _truncate_extracted("\n".join(chunks)), "xlsx"


def _extract_pptx(data: bytes) -> tuple[str, str]:
    from io import BytesIO

    from pptx import Presentation

    presentation = Presentation(BytesIO(data))
    chunks: list[str] = []
    for index, slide in enumerate(presentation.slides, start=1):
        chunks.append(f"## Slide {index}")
        for shape in slide.shapes:
            text = getattr(shape, "text", "")
            if text and text.strip():
                chunks.append(text.strip())
    return _truncate_extracted("\n".join(chunks)), "pptx"


def _extract_binary(name: str, mime: str, data: bytes) -> tuple[str, str]:
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf" or mime == "application/pdf":
        return _extract_pdf(data)
    if suffix == ".docx" or "wordprocessingml" in mime:
        return _extract_docx(data)
    if suffix == ".xlsx" or "spreadsheetml" in mime:
        return _extract_xlsx(data)
    if suffix == ".pptx" or "presentationml" in mime:
        return _extract_pptx(data)
    if mime.startswith("text/"):
        try:
            return _truncate_extracted(data.decode("utf-8")), suffix.lstrip(".") or "text"
        except UnicodeDecodeError:
            pass
    return "", suffix.lstrip(".") or "binary"


def prepare_attachments(workdir: Path | str, session_id: str, attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把二进制附件落到工作区 ``.spark/attachments`` 并抽取可读文本。

    文本附件保持原样；二进制路径相对工作区，模型可直接用现有工具读取。
    路径被限制在 ``<workdir>/.spark/attachments/<session>/``。
    """
    prepared: list[dict[str, Any]] = []
    base_dir = Path(workdir) / ".spark" / "attachments" / (safe_bare_filename(session_id) or "session")
    for item in attachments:
        data = item.get("data")
        if not isinstance(data, bytes):
            prepared.append(dict(item))
            continue
        name = _safe_attachment_name(item.get("name"))
        mime = str(item.get("mime") or "application/octet-stream")
        base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = base_dir / name
        target.write_bytes(data)
        try:
            target.chmod(0o600)
        except OSError:
            pass
        text, kind = _extract_binary(name, mime, data)
        prepared.append(
            {
                "name": name,
                "kind": kind,
                "mime": mime,
                "size": len(data),
                "path": target.relative_to(Path(workdir)).as_posix(),
                "content": text,
            }
        )
    return prepared


def remove_session_attachments(workdir: Path | str, session_id: str) -> None:
    """删除会话时清理临时附件目录，避免隐私文件随会话删除后继续落盘。"""
    base_dir = Path(workdir) / ".spark" / "attachments" / (safe_bare_filename(session_id) or "session")
    shutil.rmtree(base_dir, ignore_errors=True)


def inject_attachments(prompt: str, attachments: list[dict[str, Any]]) -> str:
    """把附件正文/二进制路径附在用户问题后，格式可被历史读取时无损剥离。"""
    if not attachments:
        return prompt
    parts = [prompt]
    for item in attachments:
        parts.append(ATTACHMENT_OPEN.format(name=item["name"]))
        if item.get("data") is not None and not item.get("path"):
            # prepare_attachments() 之前的二进制不应进入上下文。
            parts.append("[附件未物化，已跳过]")
        else:
            metadata = []
            if item.get("kind"):
                metadata.append(f"类型={item['kind']}")
            if item.get("size"):
                metadata.append(f"大小={int(item['size'])} bytes")
            if item.get("path"):
                metadata.append(f"路径={item['path']}")
            if metadata:
                parts.append(f"[附件 {'；'.join(metadata)}]\n")
            if item.get("content"):
                parts.append(str(item["content"]))
            elif item.get("path"):
                parts.append("无法自动抽取正文；请用文件/表格工具读取上面的路径。")
        parts.append(ATTACHMENT_CLOSE)
    return "".join(parts)


def split_attachments(content: str) -> tuple[str, list[str]]:
    """历史展示只返回用户原文 + 附件名；正文块仍在模型上下文里。"""
    if "⟦SPARK-ATTACHMENT:" not in content:
        return content, []
    names = [m.group(1) for m in _ATTACHMENT_RE.finditer(content)]
    marker = content.find("⟦SPARK-ATTACHMENT:")
    # 标记前的两个换行是注入时加的，展示时一并去掉。
    visible = content[:marker].rstrip()
    return visible, names


def is_sensitive_mcp_header(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    return normalized in {
        "authorization",
        "proxyauthorization",
        "cookie",
        "setcookie",
        "apikey",
        "xapikey",
    } or normalized.endswith(("authorization", "apikey", "authtoken", "sessionid")) or any(
        token in normalized for token in ("token", "secret", "password", "credential", "session")
    )


def redact_mcp_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        name: MCP_REDACTED_HEADER if is_sensitive_mcp_header(name) else value
        for name, value in headers.items()
    }


_CTYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


def ui_dir_for() -> Path | None:
    for c in (
        Path(__file__).with_name("dist"),
        Path(__file__).resolve().parents[3] / "web" / "dist",
    ):
        if (c / "index.html").is_file():
            return c
    return None


def parse_mcp_servers(items: list) -> list:
    from spark.config import McpServerConfig, McpTransportConfig

    out: list[McpServerConfig] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        transport = cast(
            Literal["stdio", "sse", "streamable_http"],
            str(item.get("transport") or "stdio").strip() or "stdio",
        )
        if transport not in {"stdio", "sse", "streamable_http"}:
            transport = "stdio"
        command = str(item.get("command") or "").strip()
        http_raw = item.get("http") if isinstance(item.get("http"), dict) else None
        http: McpTransportConfig | None = None
        if transport == "stdio":
            if not command:
                raise ValueError("each MCP server needs name and command")
        else:
            url = str((http_raw or {}).get("url") or "").strip()
            if not url:
                raise ValueError(f"MCP server '{name}' needs http.url for {transport}")
            headers_raw = (http_raw or {}).get("headers")
            headers = (
                {str(k): str(v) for k, v in headers_raw.items()}
                if isinstance(headers_raw, dict)
                else {}
            )
            try:
                timeout_sec = int((http_raw or {}).get("timeout_sec") or 30)
            except (TypeError, ValueError):
                timeout_sec = 30
            http = McpTransportConfig(url=url, headers=headers, timeout_sec=timeout_sec)
        args_raw = item.get("args")
        ro_raw = item.get("readonly_tools")
        allowed_raw = item.get("allowed_tools")
        env_raw = item.get("env")
        out.append(
            McpServerConfig(
                name=name,
                command=command,
                args=[str(a) for a in args_raw] if isinstance(args_raw, list) else [],
                env={str(k): str(v) for k, v in env_raw.items()}
                if isinstance(env_raw, dict)
                else {},
                readonly_tools=[str(a) for a in ro_raw]
                if isinstance(ro_raw, list)
                else [],
                transport=transport,
                http=http,
                allowed_tools=[str(a) for a in allowed_raw]
                if isinstance(allowed_raw, list)
                else [],
            )
        )
    return out


def _cache_control(path: str) -> str | None:
    if path in {"/", "/index.html"}:
        return "no-cache"
    if path.startswith("/assets/"):
        return "public, max-age=31536000, immutable"
    return None


def _safe_agents_md_name(filename: str) -> str | None:
    """兼容入口：实现收敛到 :func:`spark.sandbox.safe_bare_filename`。"""
    return safe_bare_filename(filename)


def _probe_target_allowed(base_url: str, trusted_base_url: str) -> bool:
    """Only probe the exact origin the user already configured.

    Hostname-only comparison leaked credentials to the same host on a different
    scheme or port: ``https://api.example`` -> ``http://api.example:8080`` would
    still receive the saved key, and a local endpoint could hand it to an
    unrelated service on another port.
    """

    def origin_of(value: str) -> tuple[str, str, int] | None:
        try:
            parsed = urlparse(value)
            scheme = parsed.scheme.lower()
            host = (parsed.hostname or "").lower()
            if scheme not in {"http", "https"} or not host:
                return None
            port = parsed.port
            if port is None:
                port = 443 if scheme == "https" else 80
            return scheme, host, int(port)
        except ValueError:
            return None

    target = origin_of(base_url)
    trusted = origin_of(trusted_base_url)
    if target is None or trusted is None:
        return False
    return target == trusted


def _port_in_use(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def pick_free_port(host: str, preferred: int, attempts: int = 64) -> int:
    """从 *preferred* 起返回第一个未被监听的端口；范围内全被占用时抛 OSError。"""
    for port in range(preferred, min(preferred + attempts, 65536)):
        if not _port_in_use(host, port):
            return port
    raise OSError(
        f"no free port on {host} in range {preferred}-{preferred + attempts - 1}"
    )


def _event_payload(event) -> dict:
    payload: dict = {"type": event.type}
    if event.text:
        payload["text"] = event.text
    if event.tool_call:
        payload["tool"] = {
            "id": event.tool_call.id,
            "name": event.tool_call.name,
            "arguments": event.tool_call.arguments,
        }
    if event.result:
        payload["ok"] = event.result.ok
        payload["result"] = event.result.payload
    if event.approval:
        payload["approval"] = {
            "summary": event.approval.summary,
            "diff": event.approval.diff,
            # The full arguments travel with the request so the approval card can
            # show what will actually run. A truncated summary is not enough to
            # judge a shell command.
            "tool": event.approval.tool_call.name,
            "arguments": event.approval.tool_call.arguments,
        }
    if event.data:
        payload["data"] = event.data
    return payload


def probe_env() -> list[dict[str, str]]:
    """运行环境探测：常见工具链的路径与版本（只读，永不执行用户输入）。

    供「设置 → 权限 → 运行环境探测」展示；探测失败的工具标记 unavailable。
    """
    import shutil

    from spark.proc import run_bounded

    tools = [
        ("python", ["python", "--version"]),
        ("python3", ["python3", "--version"]),
        ("git", ["git", "--version"]),
        ("node", ["node", "--version"]),
        ("npm", ["npm", "--version"]),
        ("uv", ["uv", "--version"]),
    ]
    out: list[dict[str, str]] = []
    for name, argv in tools:
        exe = shutil.which(name)
        if not exe:
            out.append({"name": name, "path": "", "version": "unavailable"})
            continue
        version = ""
        try:
            proc = run_bounded(argv, timeout=5)
        except OSError:
            proc = None
        if proc is None:
            version = "unavailable"
        elif proc.timed_out:
            version = "timeout"
        else:
            version = (proc.stdout or proc.stderr).strip().splitlines()[0] if (
                proc.stdout or proc.stderr
            ) else ""
        out.append({"name": name, "path": exe, "version": version or "?"})
    return out
