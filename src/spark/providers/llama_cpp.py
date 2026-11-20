"""In-process llama.cpp provider for downloaded GGUF models.

Spark ships no weights.  The local model catalog downloads a GGUF, then this
provider loads it through llama-cpp-python.  Tool schemas are injected into the
system prompt because llama-cpp-python's chat endpoint does not implement the
OpenAI ``tools`` field; MiniCPM5/Qwen XML tool calls are parsed back into
``ToolCall`` deltas.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, AsyncIterator

from spark.models import ChatDelta, ChatMessage, ToolCall

logger = logging.getLogger(__name__)

_LT = chr(60)
_GT = chr(62)
_TOOL_OPEN = _LT + "tool_call" + _GT
_TOOL_CLOSE = _LT + "/tool_call" + _GT

_LLM_CACHE: dict[str, Any] = {}
_LLM_LOCK = threading.Lock()


def _model_path(model: str) -> Path:
    from spark.local_models import models_dir

    path = Path(model).expanduser()
    if path.is_file():
        return path
    candidate = models_dir() / path.name
    return candidate


def _tool_prompt(tools: list[dict]) -> str:
    if not tools:
        return ""
    lines = [
        "# Tools",
        "You may call one or more functions to assist with the user query.",
        "Available functions are inside the tools block:",
        _LT + "tools" + _GT,
    ]
    for tool in tools:
        fn = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(fn, dict):
            lines.append(json.dumps({"type": "function", "function": fn}, ensure_ascii=False))
    lines.extend([
        _LT + "/tools" + _GT,
        "For each call return:",
        _LT + 'function name="tool_name"' + _GT,
        _LT + 'param name="argument"' + _GT + "value" + _LT + "/param" + _GT,
        _LT + "/function" + _GT,
        "Return only the call block, with no prose around it.",
    ])
    return "\n".join(lines)


def _fmt_tool_call(call: ToolCall) -> str:
    params = "".join(
        _LT + f'param name="{html.escape(str(k))}"' + _GT
        + html.escape(json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v)
        + _LT + "/param" + _GT
        for k, v in call.arguments.items()
    )
    return _LT + f'function name="{html.escape(call.name)}"' + _GT + params + _LT + "/function" + _GT


def _to_local_messages(messages: list[ChatMessage], tools: list[dict]) -> list[dict]:
    system_extra = _tool_prompt(tools)
    out: list[dict] = []
    for msg in messages:
        content = msg.content or ""
        if msg.role == "system":
            if system_extra:
                content = (content + "\n\n" + system_extra).strip()
            out.append({"role": "system", "content": content})
            continue
        if msg.role == "summary":
            out.append({"role": "user", "content": "<conversation_summary>" + content + "</conversation_summary>"})
            continue
        if msg.role == "assistant" and msg.tool_calls:
            content = (content + "\n" + "\n".join(_fmt_tool_call(c) for c in msg.tool_calls)).strip()
            out.append({"role": "assistant", "content": content})
            continue
        if msg.role == "tool":
            out.append({"role": "user", "content": _LT + "tool_response" + _GT + content + _LT + "/tool_response" + _GT})
            continue
        if msg.images:
            content = (content + "\n[image omitted: local text-only model]").strip()
        out.append({"role": msg.role, "content": content})
    return out


_FUNC_RE = re.compile(_LT + r'function\s+name="([^"^>]+)"\s*' + _GT + r"(.*?)" + _LT + r"/function" + _GT, re.S)
_PARAM_RE = re.compile(_LT + r'param\s+name="([^"^>]+)"\s*' + _GT + r"(.*?)" + _LT + r"/param" + _GT, re.S)
_JSON_BLOCK_RE = re.compile(_TOOL_OPEN + r"\s*(\{.*?\})\s*" + _TOOL_CLOSE, re.S)
_FUNC_EQ_RE = re.compile(_LT + r"function=([^\s>]+)\s*" + _GT + r"(.*?)" + _LT + r"/function" + _GT, re.S)
_PARAM_EQ_RE = re.compile(_LT + r"parameter=([^\s>]+)\s*" + _GT + r"(.*?)" + _LT + r"/parameter" + _GT, re.S)


def _coerce(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("<![CDATA[") and text.endswith("]]>"):
        text = text[9:-3].strip()
    text = html.unescape(text)
    try:
        return json.loads(text)
    except ValueError:
        return text


def parse_tool_calls(text: str) -> tuple[list[ToolCall], str]:
    calls: list[ToolCall] = []
    for m in _FUNC_RE.finditer(text):
        args = {pm.group(1).strip(): _coerce(pm.group(2)) for pm in _PARAM_RE.finditer(m.group(2))}
        if m.group(1).strip():
            calls.append(ToolCall(id=f"call_local_{len(calls)}", name=m.group(1).strip(), arguments=args))
    for m in _FUNC_EQ_RE.finditer(text):
        args = {pm.group(1).strip(): _coerce(pm.group(2)) for pm in _PARAM_EQ_RE.finditer(m.group(2))}
        if m.group(1).strip():
            calls.append(ToolCall(id=f"call_local_{len(calls)}", name=m.group(1).strip(), arguments=args))
    for m in _JSON_BLOCK_RE.finditer(text):
        try:
            obj = json.loads(m.group(1))
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        name = str(obj.get("name") or "").strip()
        args = obj.get("arguments") or obj.get("parameters") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"value": args}
        if name:
            calls.append(ToolCall(id=f"call_local_{len(calls)}", name=name, arguments=args if isinstance(args, dict) else {}))
    cleaned = _FUNC_RE.sub("", text)
    cleaned = _FUNC_EQ_RE.sub("", cleaned)
    cleaned = _JSON_BLOCK_RE.sub("", cleaned)
    cleaned = cleaned.replace(_TOOL_OPEN, "").replace(_TOOL_CLOSE, "").strip()
    return calls, cleaned


class LlamaCppProvider:
    """Local GGUF provider loaded in-process (no separate server/port)."""

    def __init__(self, model: str, n_ctx: int | None = None, chat_format: str | None = None) -> None:
        self.model = model
        self.path = _model_path(model)
        self.chat_format = chat_format
        self.n_ctx = int(n_ctx or os.environ.get("SPARK_LOCAL_CTX", "16384"))
        self._llm = None

    def _ensure(self):
        if self._llm is not None:
            return self._llm
        if not self.path.is_file():
            raise FileNotFoundError(f"本地模型不存在：{self.path}（先在设置 → 本地模型下载）")
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise RuntimeError("缺少 llama-cpp-python；请安装 spark-agent[local] 或 pip install llama-cpp-python") from exc
        key = f"{self.path}:{self.n_ctx}:{self.chat_format}"
        with _LLM_LOCK:
            if key not in _LLM_CACHE:
                logger.info("loading local model %s n_ctx=%s", self.path, self.n_ctx)
                kwargs = dict(
                    model_path=str(self.path),
                    n_ctx=self.n_ctx,
                    n_threads=max(2, min(8, (os.cpu_count() or 4))),
                    n_batch=512,
                    verbose=False,
                )
                if self.chat_format:
                    kwargs["chat_format"] = self.chat_format
                _LLM_CACHE[key] = Llama(**kwargs)
            self._llm = _LLM_CACHE[key]
        return self._llm

    def _generate(self, messages: list[ChatMessage], tools: list[dict], effort: str | None) -> dict:
        llm = self._ensure()
        max_tokens = {"low": 256, "medium": 768, "high": 1280}.get(effort or "medium", 768)
        template = str(llm.metadata.get("tokenizer.chat_template") or "")
        if template:
            from llama_cpp.llama_chat_format import Jinja2ChatFormatter

            from spark.providers.openai_compat import _to_openai

            formatter = Jinja2ChatFormatter(
                template=template,
                eos_token="</s>",
                bos_token="<s>",
                add_generation_prompt=True,
            )
            converted = _to_openai(messages)
            for item in converted:
                for call in item.get("tool_calls") or []:
                    fn = call.get("function") if isinstance(call, dict) else None
                    if isinstance(fn, dict) and isinstance(fn.get("arguments"), str):
                        try:
                            fn["arguments"] = json.loads(fn["arguments"])
                        except ValueError:
                            fn["arguments"] = {"value": fn["arguments"]}
            rendered = formatter(
                messages=converted,
                tools=tools or None,
                tool_choice="auto" if tools else None,
                enable_thinking=False,
            )
            tokens = llm.tokenize(rendered.prompt.encode("utf-8"), add_bos=False, special=True)
            stop_ids = {llm.token_eos(), llm.token_bos()}
            from llama_cpp import StoppingCriteriaList

            im_end_ids = llm.tokenize(b"<|im_end|>", add_bos=False, special=True)

            def _hit_end(ids, _logits):
                tail = list(ids[-len(im_end_ids):])
                return len(tail) == len(im_end_ids) and tail == im_end_ids

            criteria = StoppingCriteriaList([_hit_end])
            generated: list[int] = []
            for token in llm.generate(
                tokens,
                temp=0.7,
                top_p=0.95,
                min_p=0.0,
                top_k=40,
                repeat_penalty=1.0,
                reset=True,
                stopping_criteria=criteria,
            ):
                if token in stop_ids:
                    break
                generated.append(token)
                if len(generated) >= max_tokens:
                    break
            text = llm.detokenize(generated, special=True).decode("utf-8", "replace")
            text = text.split("<|im_end|>")[0]
            for stop in rendered.stop or []:
                if isinstance(stop, str) and stop:
                    text = text.split(stop)[0]
            return {
                "choices": [{"message": {"role": "assistant", "content": text}}],
                "usage": {
                    "prompt_tokens": len(tokens),
                    "completion_tokens": len(generated),
                },
            }
        try:
            return llm.create_chat_completion(
                messages=_to_local_messages(messages, tools),
                temperature=0.7,
                top_p=0.95,
                min_p=0.0,
                max_tokens=max_tokens,
                stream=False,
                stop=[_LT + "|im_end|" + _GT],
            )
        except TypeError:
            return llm.create_chat_completion(
                messages=_to_local_messages(messages, tools),
                temperature=0.7,
                top_p=0.95,
                max_tokens=max_tokens,
                stream=False,
            )

    async def stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict],
        effort: str | None = None,
    ) -> AsyncIterator[ChatDelta]:
        result = await asyncio.to_thread(self._generate, messages, tools, effort)
        message = (result.get("choices") or [{}])[0].get("message") or {}
        content = str(message.get("content") or "")
        think_open = _LT + "think" + _GT
        think_close = _LT + "/think" + _GT
        if think_open in content and think_close in content:
            before, rest = content.split(think_open, 1)
            reasoning, after = rest.split(think_close, 1)
            if before.strip():
                yield ChatDelta(type="text", text=before)
            if reasoning.strip():
                yield ChatDelta(type="reasoning", text=reasoning.strip())
            content = after
        calls, text = parse_tool_calls(content)
        if text:
            for piece in re.split(r"(?<=[。！？.!?])\s*", text):
                if piece:
                    yield ChatDelta(type="text", text=piece)
        for call in calls:
            yield ChatDelta(type="tool_call", tool_call=call)
        usage_raw = result.get("usage") if isinstance(result.get("usage"), dict) else None
        usage = None
        if usage_raw:
            usage = {
                "prompt_tokens": int(usage_raw.get("prompt_tokens") or 0),
                "completion_tokens": int(usage_raw.get("completion_tokens") or 0),
            }
        yield ChatDelta(type="end", usage=usage)
