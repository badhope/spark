"""输出门禁（P2 安全）：对外展示/外发前统一脱敏，防止内部信息泄露。

触发时机：SSE 事件出口（面向前端）与对外 API 错误文案。工具内部回填
（模型上下文）仍保留原始信息——模型需要真实路径才能操作文件，因此门禁
只作用于"面向用户/外部"的出口，不碰模型侧消息。

脱敏项（按优先级）：
1. 密钥 / token（调用方传入已知的 secret 值）
2. 用户主目录绝对路径（C:\\Users\\xxx → ~，隐藏盘符与用户名）
3. Python 堆栈（Traceback ... 块折叠为 [REDACTED] + 末行异常）
设计原则：只做"缩小/折叠"，绝不误改正常业务文本；无法 100% 覆盖时宁可保守。
"""

from __future__ import annotations

import re as _re
from pathlib import Path

_REDACTED = "[REDACTED]"

# 修复：改为 lazy 函数（避免容器环境下导入时固化旧值，HOME 变动/未设置时可跟到）
_HOME_CACHE: tuple[str, str] | None = None


def _get_home_prefixes() -> tuple[str, str]:
    """惰性计算 + 进程级缓存主目录前缀（unix 和反斜杠两种写法）。"""
    global _HOME_CACHE
    if _HOME_CACHE is None:
        raw = str(Path.home()).rstrip(r"\/")
        _HOME_CACHE = (raw, raw.replace("\\", "/"))
    return _HOME_CACHE


def _strip_traceback(text: str) -> str:
    """把 Python 堆栈块折叠为 '[REDACTED] <末行异常>'；无堆栈则原样返回。"""
    if "Traceback" not in text:
        return text
    lines = text.splitlines()
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        if "Traceback (most recent call last):" in lines[i]:
            # 跳过紧随其后的栈帧行：File "..." 行 + 其后的缩进源码行
            j = i + 1
            while j < n:
                ln = lines[j]
                if ln.startswith("  File ") or (
                    ln.startswith((" ", "\t")) and not ln.lstrip().startswith(("File ", "line "))
                ):
                    j += 1
                    continue
                break
            if j < n and lines[j].strip():
                out.append(f"{_REDACTED} {lines[j].strip()}")
                i = j + 1
            else:
                out.append(_REDACTED)
                i = j if j < n else n
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def sanitize(text: str | None, secrets: tuple[str, ...] = ()) -> str:
    """对外文本脱敏：密钥 → 主目录 → 堆栈。输入为空返回空串。"""
    if not text:
        return ""
    s = text
    for sec in secrets:
        if sec and isinstance(sec, str) and len(sec) >= 8:
            s = s.replace(sec, _REDACTED)
    # 修复：使用 lazy 函数存取主目录前缀（不再依赖导入时固化的 _HOME）
    home, home_fwd = _get_home_prefixes()
    if home and home not in ("/", "\\", ""):
        # 边界感知替换：裸 replace 会把 /home/ali 前缀误伤到 /home/alibaba
        # （输出 ~baba），篡改正常业务文本。只有当前缀后面紧接名字字符
        # （字母/数字/下划线）时才不替换；路径分隔符、句点、引号都是合法边界。
        _bound = r"(?!\w)"
        s = _re.sub(_re.escape(home) + _bound, "~", s)
        if home_fwd != home:
            s = _re.sub(_re.escape(home_fwd) + _bound, "~", s)
    return _strip_traceback(s)


_MAX_SANITIZE_DEPTH = 3


def _sanitize_value(v, secrets: tuple[str, ...], depth: int):
    if depth > _MAX_SANITIZE_DEPTH:
        return v
    if isinstance(v, str):
        return sanitize(v, secrets)
    if isinstance(v, dict):
        return {k: _sanitize_value(x, secrets, depth + 1) for k, x in v.items()}
    if isinstance(v, list):
        return [_sanitize_value(x, secrets, depth + 1) for x in v]
    return v


def sanitize_sse_payload(payload: dict, secrets: tuple[str, ...] = ()) -> dict:
    """对 SSE 事件 payload 做浅递归脱敏（src 树事件协议：text/result/approval/tool/data）。

    只替换字符串值（密钥/主目录/堆栈），结构与非字符串不动；深度限 3 层。

    注意：与 secrets 是否为空无关——主目录路径与 Python 堆栈属于无条件
    脱敏项，必须走完整流程（线上本机令牌/API Key 缺失时同样生效）。
    """
    return _sanitize_value(payload, secrets, 0)


# ---------------------------------------------------------------------------
# Shared redaction (used by memory extraction and the audit trail).
# Lives here — not in memory.extractor — so low-level modules like spark.audit
# can use it without depending on the memory layer.
# ---------------------------------------------------------------------------

_SECRET_RES = [
    _re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    _re.compile(r"ghp_[A-Za-z0-9]{8,}"),
    _re.compile(r"gho_[A-Za-z0-9]{8,}"),
    _re.compile(r"github_pat_[A-Za-z0-9_]{8,}"),
    _re.compile(r"xox[baprs]-[A-Za-z0-9\-]{8,}"),
    _re.compile(r"AKIA[0-9A-Z]{16}"),
    _re.compile(r"Bearer\s+[A-Za-z0-9\-_.~+/]{8,}", _re.IGNORECASE),
    _re.compile(
        r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|authorization)\s*[=:]\s*[^\s,;'\"']{4,}"
    ),
    _re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
]


def redact_secrets(text: str) -> str:
    if not text:
        return text
    out = text
    for pat in _SECRET_RES:
        if pat.groups:
            out = pat.sub(lambda m: f"{m.group(1)}=[REDACTED]", out)
        else:
            out = pat.sub("[REDACTED]", out)
    return out


