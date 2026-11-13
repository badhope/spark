"""Unified provider backed by LiteLLM.

Replaces the hand-rolled httpx streaming + retry implementation with LiteLLM's
battle-tested completion engine.  Preserves the same class names
(``OpenAICompatProvider``, ``OllamaProvider``) and constructor signatures so that
``factory.py`` and the test suite keep working unchanged.

Capabilities gained for free versus the old implementation
----------------------------------------------------------
* 100+ LLM providers (OpenAI, Anthropic, DeepSeek, Gemini, Ollama, …)
* Provider-native authentication / parameter mapping
* Automatic retry with exponential backoff (configurable via ``num_retries``)
* Streaming token usage reporting
* ``reasoning_content`` passthrough (DeepSeek R1, etc.)
* Optional multi-config fallbacks (LiteLLM ``fallbacks``)

Capabilities preserved from the original
-----------------------------------------
* ``<think>…</think>`` inline-tag splitting  (``_split_think``)
* Incremental ``tool_calls`` aggregation across chunks
* OpenAI-message format conversion including images, ``summary`` role, etc.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, NoReturn

import litellm

from spark.models import ChatDelta, ChatMessage, ToolCall

logger = logging.getLogger(__name__)

# HTTP status codes that warrant a retry (transient / rate-limit)
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# LiteLLM/httpx exception types that indicate transient failures
_RETRYABLE_EXCEPTIONS = (TimeoutError, ConnectionError, OSError)

# ---------------------------------------------------------------------------
# Message format conversion  (preserved verbatim from the original impl)
# ---------------------------------------------------------------------------


def _to_openai(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "summary":
            out.append(
                {
                    "role": "user",
                    "content": f"<conversation_summary>\n{msg.content or ''}\n</conversation_summary>",
                }
            )
            continue
        item: dict[str, Any] = {"role": msg.role}
        if msg.images and msg.role == "user":
            parts: list[dict[str, Any]] = []
            if msg.content:
                parts.append({"type": "text", "text": msg.content})
            for image in msg.images:
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{image.media_type};base64,{image.data}"},
                    }
                )
            item["content"] = parts
        elif msg.content is not None:
            item["content"] = msg.content
        if msg.tool_calls:
            item["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                }
                for c in msg.tool_calls
            ]
        if msg.tool_call_id:
            item["tool_call_id"] = msg.tool_call_id
        if msg.name:
            item["name"] = msg.name
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Inline <think> tag splitter  (preserved for models that embed reasoning
# inside the ``content`` stream instead of using a separate field)
# ---------------------------------------------------------------------------


_THINK_STATE: dict[str, bool] = {"open": False, "closed": False}


def _split_think(text: str, state: dict) -> list[tuple[str, str]]:
    """Split *text* pieces that may contain ``<think>…</think>`` markup.

    Returns ``(piece, kind)`` tuples where *kind* is ``"reasoning"`` or
    ``"text"``.  *state* is mutated so that the split is correct even when a
    single ``<think>`` pair is fragmented across multiple stream chunks.
    """
    out: list[tuple[str, str]] = []
    buf = text
    while buf:
        if state["open"]:
            end = buf.find("</think>")
            if end >= 0:
                if end > 0:
                    out.append((buf[:end], "reasoning"))
                buf = buf[end + 8 :]
                state["open"] = False
                state["closed"] = True
                continue
            out.append((buf, "reasoning"))
            buf = ""
            continue
        start = buf.find("<think>")
        if start >= 0 and not state["closed"]:
            if start > 0:
                out.append((buf[:start], "text"))
            buf = buf[start + 7 :]
            state["open"] = True
            continue
        if buf:
            out.append((buf, "text"))
        buf = ""
    return out


# ---------------------------------------------------------------------------
# LiteLLM-powered provider
# ---------------------------------------------------------------------------


def litellm_model_name(name: str) -> tuple[str, str | None]:
    """Return ``(model, custom_llm_provider)`` for a LiteLLM call.

    裸模型名一律按 OpenAI 兼容处理（``api_base`` 已显式给出）：LiteLLM 只能
    按内置规则推断 provider，任何自定义模型名（glm-5.3-flash、Qwen-… 等）
    都会直接报 "LLM Provider NOT provided"。带 "/" 的名字视为用户显式使用
    LiteLLM 前缀语法（openai/xxx、deepseek/xxx），原样尊重。

    模块级函数：``OpenAICompatProvider._litellm_model`` 与
    :mod:`spark.providers.auxiliary` 共用同一规则（单一真相源）。
    """
    if "/" in name:
        return name, None
    return name, "openai"


class OpenAICompatProvider:
    """Unified LLM provider backed by `LiteLLM <https://litellm.ai>`_.

    Constructor signature is identical to the previous hand-rolled
    ``OpenAICompatProvider`` so that ``factory.py`` needs only minimal changes.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        max_retries: int = 3,
        fallback_models: list[str] | None = None,
        api_keys: list[str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.max_retries = max_retries
        # P1：fallback 链（去重，排除主模型）；凭据池（api_key 永远是池首，去重保序）
        self.fallback_models = [m for m in (fallback_models or []) if m and m != model]
        pool: list[str] = []
        for k in [api_key, *(api_keys or [])]:
            if k and k not in pool:
                pool.append(k)
        self._key_pool: list[str] = pool or [api_key]
        self._key_cursor = 0

    def _next_key(self) -> str:
        """P1：凭据池轮换——每次尝试取下一把钥匙（单钥匙时恒等）。"""
        if not self._key_pool:
            return self.api_key
        key = self._key_pool[self._key_cursor % len(self._key_pool)]
        self._key_cursor += 1
        return key

    def _litellm_model(self, model: str | None = None) -> tuple[str, str | None]:
        """Return ``(model, custom_llm_provider)`` for the LiteLLM call.

        ``model`` 传入时覆盖 ``self.model``（fallback 目标用）；规则见
        :func:`litellm_model_name`。
        """
        return litellm_model_name(model or self.model)

    # -- public interface (Provider protocol) ----------------------------------

    @staticmethod
    def _extract_status_code(exc: Exception) -> int | None:
        """Best-effort extraction of an HTTP status code from an exception."""
        # LiteLLM wraps httpx errors; the status code may live in
        # exc.status_code or in the underlying HTTPStatusError.
        code = getattr(exc, "status_code", None)
        if isinstance(code, int):
            return code
        # Check args for numeric status codes
        for arg in getattr(exc, "args", ()):
            if isinstance(arg, int) and 100 <= arg <= 599:
                return arg
        return None

    async def stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict],
        effort: str | None = None,
    ) -> AsyncIterator[ChatDelta]:
        """Stream completion deltas.

        Uses LiteLLM's retry, timeout, and provider-mapping machinery
        with an outer retry loop that handles 429/5xx with exponential
        backoff (1s / 2s / 4s).  Client errors (4xx except 429) are NOT
        retried — they indicate a problem with the request itself.
        Yields :class:`~spark.models.ChatDelta` chunks identical to the previous
        implementation so upstream consumers (``AgentLoop``) are unaffected.

        *effort* maps to provider-native reasoning/thinking budget:
        ``low|medium|high`` → OpenAI-style ``reasoning_effort``; for DeepSeek
        chat models it additionally enables the official ``thinking`` block
        with a budget scaled to the effort tier (cache-friendly: the fixed
        system + tools stay a stable prefix, effort only varies the tail).
        """
        # P0-9: OTel span 包裹整个流式调用（含重试），观测模型/effort/时长/结果
        span = None
        try:
            from spark.otel import get_tracer

            span = get_tracer().start_span(
                "llm.stream",
                attributes={
                    "llm.model": self.model,
                    "gen_ai.request.model": self.model,
                    "gen_ai.usage.input_tokens": 0,
                    "spark.effort": effort or "",
                },
            )
        except Exception:  # pragma: no cover - 观测不可用时不影响主流程
            span = None

        try:
            async for delta in self._stream_impl(messages, tools, effort):
                yield delta
            if span is not None:
                from opentelemetry.trace import Status, StatusCode

                span.set_status(Status(StatusCode.OK))
        except Exception as exc:
            if span is not None:
                from opentelemetry.trace import Status, StatusCode

                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR))
            raise
        finally:
            if span is not None:
                span.end()

    def _request_kwargs(
        self,
        target: str,
        messages: list[ChatMessage],
        tools: list[dict],
        effort: str | None,
    ) -> dict[str, Any]:
        model_arg, provider_hint = self._litellm_model(target)
        request_key = self._next_key()
        completion_kwargs: dict[str, Any] = dict(
            model=model_arg,
            messages=_to_openai(messages),
            tools=tools or None,
            stream=True,
            # P1：请求流尾 usage 帧（choices 为空、usage 有值）；
            # drop_params=True 兜底不支持该参数的 provider
            stream_options={"include_usage": True},
            api_base=self.base_url or None,
            api_key=request_key or None,
            num_retries=0,  # we handle retries explicitly for fine-grained control
            drop_params=True,  # pass through non-standard fields (e.g. reasoning_content)
            timeout=120.0,  # httpx.Timeout(120.0) equivalent for LiteLLM
            # LiteLLM >=1.100 auto-routes any call carrying `tools`
            # through litellm.responses.mcp, which imports the mcp 2.x
            # API (mcp.types.CacheableResult / the `mcp_types`
            # package). Spark pins mcp 1.x for the official SDK, so
            # that import fails and *every* tool-carrying completion
            # dies with "cannot import name 'CacheableResult'".
            # Spark does its own MCP bridged through tool names, so
            # LiteLLM's MCP handler is never what we want here.
            _skip_mcp_handler=True,
        )
        if provider_hint:
            completion_kwargs["custom_llm_provider"] = provider_hint
        if effort:
            completion_kwargs["reasoning_effort"] = effort
            if "deepseek" in target.lower():
                completion_kwargs["extra_body"] = {
                    "thinking": {
                        "type": "enabled",
                        "budget_tokens": {
                            "low": 2048,
                            "medium": 4096,
                            "high": 8192,
                        }[effort],
                    }
                }
        return completion_kwargs

    async def _request_with_retries(
        self,
        target: str,
        messages: list[ChatMessage],
        tools: list[dict],
        effort: str | None,
    ) -> tuple[Any, Exception | None]:
        """单目标重试：可重试错误指数退避，不可重试立即返回；成功返回 (response, None)。"""
        last_exc: Exception | None = None
        effective_effort = effort
        dropped_effort = False
        attempt = 0
        while attempt <= self.max_retries:
            try:
                response = await litellm.acompletion(
                    **self._request_kwargs(target, messages, tools, effective_effort)
                )
            except Exception as exc:  # aqg: top-level boundary
                last_exc = exc
                status = self._extract_status_code(exc)
                # Some OpenAI-compatible gateways reject `reasoning_effort`
                # outright. It is an optional tuning hint, never a
                # correctness requirement, so drop it once and retry before
                # giving up on this target. This retry does not consume a
                # retry budget slot: it is a request-shape correction.
                if status == 400 and effective_effort and not dropped_effort:
                    dropped_effort = True
                    effective_effort = None
                    logger.info(
                        "provider_effort_unsupported model=%s base_url=%s; retrying without reasoning_effort",
                        target,
                        self.base_url,
                    )
                    continue
                is_retryable = (
                    status in _RETRYABLE_STATUS_CODES
                    or (
                        status is None
                        and isinstance(exc, _RETRYABLE_EXCEPTIONS)
                    )
                )
                if is_retryable and attempt < self.max_retries:
                    backoff = 2**attempt  # 1, 2, 4 seconds
                    logger.warning(
                        "provider_retry model=%s attempt=%d/%d status=%s base_url=%s backoff=%ds error=%s",
                        target,
                        attempt + 1,
                        self.max_retries,
                        status,
                        self.base_url,
                        backoff,
                        str(exc)[:200],
                    )
                    attempt += 1
                    await asyncio.sleep(backoff)
                    continue
                # 本目标不可重试（如 4xx）——交给外层决定是否切下一目标
                break
            # Successful request — proceed to stream
            return response, None
        return None, last_exc

    def _raise_all_failed(
        self, targets: list[str], last_exc: Exception | None
    ) -> NoReturn:
        msg = str(last_exc) if last_exc else "no response"
        # Never leak credentials into error output（全池脱敏）
        for k in self._key_pool:
            if k and k in msg:
                msg = msg.replace(k, "***")
        raise RuntimeError(
            f"Provider request to '{self.base_url}' failed after "
            f"{self.max_retries} retries (models tried: {', '.join(targets)}): {msg}"
        ) from last_exc

    async def _stream_impl(
        self,
        messages: list[ChatMessage],
        tools: list[dict],
        effort: str | None = None,
    ) -> AsyncIterator[ChatDelta]:
        # P1：目标链 = 主模型 + fallback_models；每个目标独立重试，凭据池逐次轮换。
        # 任一目标成功即返回；全部失败才抛错（错误信息脱敏全部池内密钥）。
        targets: list[str] = [self.model, *self.fallback_models]
        response = None
        last_exc: Exception | None = None
        for ti, target in enumerate(targets):
            response, last_exc = await self._request_with_retries(
                target, messages, tools, effort
            )
            if response is not None:
                break
            if ti < len(targets) - 1:
                logger.warning(
                    "provider_fallback: 模型 %s 不可用，切换到 %s",
                    target,
                    targets[ti + 1],
                )
        # If all targets exhausted without success
        if response is None:
            self._raise_all_failed(targets, last_exc)

        tool_acc: dict[int, dict[str, str]] = {}
        think_state: dict[str, bool] = {"open": False, "closed": False}
        reported_usage: dict[str, int] = {}

        async for chunk in response:
            _collect_usage(chunk, reported_usage)
            # Guard against LiteLLM returning None/empty chunks
            if not chunk or not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta if choice.delta else None
            if delta is None:
                continue

            # 1) Separate reasoning content (DeepSeek R1 etc.)
            reasoning = getattr(delta, "reasoning_content", None)
            if not reasoning:
                # Some providers use a plain "reasoning" attribute
                reasoning = getattr(delta, "reasoning", None)
            if reasoning:
                yield ChatDelta(type="reasoning", text=str(reasoning))

            # 2) Regular text content (may still carry <think> tags)
            content = delta.content
            if content:
                for piece, kind in _split_think(str(content), think_state):
                    if kind == "reasoning":
                        yield ChatDelta(type="reasoning", text=piece)
                    elif piece:
                        yield ChatDelta(type="text", text=piece)

            # 3) Tool-call deltas (OpenAI-style incremental aggregation)
            for tc in delta.tool_calls or []:
                _accumulate_tool_call(tool_acc, tc)

        # Yield aggregated tool calls after the stream ends
        for delta in _tool_call_deltas(tool_acc):
            yield delta
        yield ChatDelta(type="end", usage=reported_usage or None)


def _collect_usage(chunk: Any, reported_usage: dict[str, int]) -> None:
    """流尾 usage 帧（choices 为空、usage 有值）先提取，提取失败静默跳过。"""
    usage_obj = getattr(chunk, "usage", None)
    if usage_obj is None:
        return
    try:
        if getattr(usage_obj, "prompt_tokens", None):
            reported_usage["prompt_tokens"] = int(usage_obj.prompt_tokens)
        if getattr(usage_obj, "completion_tokens", None):
            reported_usage["completion_tokens"] = int(
                usage_obj.completion_tokens
            )
        details = getattr(usage_obj, "completion_tokens_details", None)
        reasoning_tok = (
            getattr(details, "reasoning_tokens", None) if details else None
        )
        if reasoning_tok:
            reported_usage["reasoning_tokens"] = int(reasoning_tok)
    except (TypeError, ValueError):
        pass


def _accumulate_tool_call(tool_acc: dict[int, dict[str, str]], tc: Any) -> None:
    """OpenAI 风格增量工具调用按 index 聚合。"""
    idx = int(getattr(tc, "index", 0) or 0)
    acc = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
    tc_id = getattr(tc, "id", None)
    if tc_id:
        acc["id"] += tc_id
    fn = tc.function if tc.function else None
    if fn:
        fn_name = getattr(fn, "name", None)
        fn_args = getattr(fn, "arguments", None)
        if fn_name:
            acc["name"] += fn_name
        if fn_args:
            acc["arguments"] += fn_args


def _tool_call_deltas(tool_acc: dict[int, dict[str, str]]):
    """流结束后吐出聚合完成的工具调用。"""
    for acc in tool_acc.values():
        if not acc["name"]:
            continue
        try:
            args = json.loads(acc["arguments"] or "{}")
        except json.JSONDecodeError:
            args = {}
        yield ChatDelta(
            type="tool_call",
            tool_call=ToolCall(
                id=acc["id"] or "call_0",
                name=acc["name"],
                arguments=args,
            ),
        )


class OllamaProvider(OpenAICompatProvider):
    """LiteLLM provider pre-configured for a local Ollama instance."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
    ) -> None:
        super().__init__(
            base_url or "http://127.0.0.1:11434/v1",
            model,
            api_key or "ollama",
        )
