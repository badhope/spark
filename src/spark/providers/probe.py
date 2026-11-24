from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# 设计定位：探活刻意使用裸 httpx 而非 LiteLLM——它要验证的是端点的
# **原始线上协议**（SSE 帧格式、finish_reason / [DONE]、非流式结构）。
# LiteLLM 的宽容解析会掩盖不兼容端点，使 ``spark test`` / 设置页"测试连接"
# 失去诊断价值。其余一次性辅助调用（摘要/标题/记忆）已收敛到
# :mod:`spark.providers.auxiliary`，本模块是唯一保留的直连实现。

PROBE_PROMPT = (
    "Reply with exactly the token SPARK_OK followed by one short sentence "
    "about local coding agents. Do not refuse."
)
MIN_CONTENT_CHARS = 8
#: Budget for the *answer*. Reasoning models (glm-5.x, DeepSeek-R1, …) spend
#: hundreds of tokens thinking before emitting content, so a small cap made
#: them look broken: finish_reason=length with an empty content field.
#: 1200 leaves room for the reasoning pass plus the answer.
PROBE_MAX_TOKENS = 1200


@dataclass
class ProbeResult:
    ok: bool
    model: str
    base_url: str
    content: str = ""
    stream_content: str = ""
    rounds: int = 0
    latency_ms: int = 0
    error: str | None = None
    details: list[str] = field(default_factory=list)

    def public_dict(self) -> dict:
        return {
            "ok": self.ok,
            "model": self.model,
            "base_url": self.base_url,
            "content": self.content,
            "stream_content": self.stream_content,
            "rounds": self.rounds,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "details": self.details,
        }


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _extract_reasoning(payload: dict) -> str:
    """Return reasoning_content / reasoning from the first choice, if any."""
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _finish_reason(payload: dict) -> str:
    choices = payload.get("choices") or []
    return str((choices[0] if choices else {}).get("finish_reason") or "?")


def _content_parts(content: list) -> str:
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") in {None, "text"}:
            parts.append(str(item.get("text") or item.get("content") or ""))
        elif isinstance(item, str):
            parts.append(item)
    return "".join(parts)


def _reasoning_text(
    payload: dict, keys: tuple[str, ...] = ("reasoning_content", "reasoning", "thinking")
) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _extract_message_content(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        joined = _content_parts(content)
        if joined.strip():
            return joined
    # Reasoning models (DeepSeek-R1 family, Step, many domestic endpoints) put
    # the answer in `reasoning_content` and leave `content` null.
    reasoning = _reasoning_text(message)
    if reasoning:
        return reasoning
    delta = choices[0].get("delta") or {}
    delta_content = delta.get("content")
    if isinstance(delta_content, str) and delta_content.strip():
        return delta_content
    return _reasoning_text(delta, ("reasoning_content", "reasoning"))

def _chat_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/chat/completions"


async def _complete_once(client: httpx.AsyncClient, url: str, api_key: str, model: str, prompt: str) -> str:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": PROBE_MAX_TOKENS,
        "temperature": 0,
    }
    resp = await client.post(url, headers=_headers(api_key), json=body)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:400]}")
    payload = resp.json()
    text = _extract_message_content(payload).strip()
    if len(text) < MIN_CONTENT_CHARS:
        # A reasoning model may put everything in reasoning_content when the
        # budget still runs out; that is a truncated answer, not a live one —
        # but a non-empty reasoning field proves the endpoint *works*.
        reasoning = _extract_reasoning(payload).strip()
        if len(reasoning) < MIN_CONTENT_CHARS:
            raise RuntimeError(
                "model returned empty content and empty reasoning "
                f"(finish_reason={_finish_reason(payload)})"
            )
        text = reasoning
    return text


async def _stream_once(client: httpx.AsyncClient, url: str, api_key: str, model: str, prompt: str) -> str:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "max_tokens": PROBE_MAX_TOKENS,
        "temperature": 0,
    }
    parts: list[str] = []
    finished = False
    async with client.stream("POST", url, headers=_headers(api_key), json=body) as resp:
        if resp.status_code >= 400:
            raw = (await resp.aread()).decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {resp.status_code}: {raw[:400]}")
        async for line in resp.aiter_lines():
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                finished = True
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if isinstance(content, str) and content:
                parts.append(content)
            else:
                # Reasoning-only streams (same family as the non-stream path).
                for key in ("reasoning_content", "reasoning"):
                    value = delta.get(key)
                    if isinstance(value, str) and value:
                        parts.append(value)
                        break
            if choice.get("finish_reason"):
                finished = True
    text = "".join(parts).strip()
    if not finished:
        raise RuntimeError("stream ended without finish_reason or [DONE]")
    if len(text) < MIN_CONTENT_CHARS:
        raise RuntimeError("stream returned empty or too-short content")
    return text


async def probe_provider(
    *,
    base_url: str,
    api_key: str,
    model: str,
    rounds: int = 2,
    client: httpx.AsyncClient | None = None,
) -> ProbeResult:
    if not base_url.strip():
        return ProbeResult(ok=False, model=model, base_url=base_url, error="base_url is required")
    if not api_key.strip():
        return ProbeResult(ok=False, model=model, base_url=base_url, error="api_key is required")
    if not model.strip():
        return ProbeResult(ok=False, model=model, base_url=base_url, error="model is required")
    url = _chat_url(base_url)
    timeout = httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=10.0)
    started = time.monotonic()
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    contents: list[str] = []
    streams: list[str] = []
    details: list[str] = []
    try:
        for i in range(max(rounds, 1)):
            complete = await _complete_once(client, url, api_key, model, PROBE_PROMPT)
            stream = await _stream_once(client, url, api_key, model, PROBE_PROMPT)
            contents.append(complete)
            streams.append(stream)
            details.append(f"round {i + 1}: complete={len(complete)} chars, stream={len(stream)} chars, finished=yes")
        latency_ms = int((time.monotonic() - started) * 1000)
        return ProbeResult(
            ok=True,
            model=model,
            base_url=base_url.rstrip("/"),
            content=contents[-1],
            stream_content=streams[-1],
            rounds=len(contents),
            latency_ms=latency_ms,
            details=details,
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        return ProbeResult(
            ok=False,
            model=model,
            base_url=base_url.rstrip("/"),
            content=contents[-1] if contents else "",
            stream_content=streams[-1] if streams else "",
            rounds=len(contents),
            latency_ms=latency_ms,
            error=str(exc),
            details=details,
        )
    finally:
        if own_client:
            await client.aclose()


async def list_models(
    *, base_url: str, api_key: str, client: httpx.AsyncClient | None = None
) -> list[str]:
    """Return the model ids the endpoint advertises at ``GET /models``.

    Separate from :func:`probe_provider`: listing tells you what *exists* on the
    account, probing tells you what actually answers. Both are needed — several
    gateways list models they cannot serve.
    """
    if not base_url.strip() or not api_key.strip():
        return []
    url = base_url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    url += "/models"
    own_client = client is None
    client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0)
        )
    try:
        resp = await client.get(url, headers=_headers(api_key))
        if resp.status_code != 200:
            return []
        payload = resp.json()
    except Exception:  # noqa: BLE001 - listing is best-effort
        return []
    finally:
        if own_client:
            await client.aclose()
    data = payload.get("data") if isinstance(payload, dict) else None
    out: list[str] = []
    for item in data or []:
        if isinstance(item, dict):
            mid = item.get("id") or item.get("name")
            if mid:
                out.append(str(mid))
    return out
