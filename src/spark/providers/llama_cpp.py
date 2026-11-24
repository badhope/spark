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
from typing import Any, AsyncIterator, cast

from spark.models import ChatDelta, ChatMessage, ToolCall

logger = logging.getLogger(__name__)

_LT = chr(60)
_GT = chr(62)
_TOOL_OPEN = _LT + "tool_call" + _GT
_TOOL_CLOSE = _LT + "/tool_call" + _GT

_LLM_CACHE: dict[str, Any] = {}
_LLM_LOCK = threading.Lock()

# A 1B model cannot reliably choose among all ~60 Spark tools; the full schema
# block alone is ~8k tokens and pushes the model past its instruction-following
# budget. Keep the coding loop's core verbs, web lookup and ask_user. Unknown
# names are ignored, so the list stays safe if tools are renamed.
LOCAL_TOOL_ALLOWLIST = frozenset(
    {
        "read_file",
        "list_dir",
        "write_file",
        "apply_patch",
        "apply_edit",
        "run_shell",
        "run_tests",
        "grep",
        "glob",
        "web_search",
        "web_fetch",
        "ask_user",
    }
)


def _filter_tools(tools: list[dict]) -> list[dict]:
    selected = [
        tool
        for tool in tools
        if isinstance(tool, dict)
        and isinstance(tool.get("function"), dict)
        and str(tool["function"].get("name") or "") in LOCAL_TOOL_ALLOWLIST
    ]
    # Never send an empty tool block if the registry changed underneath us.
    return selected or tools


_TOOL_INTENT_RE = re.compile(
    r"(读取|查看|打开|列出|目录|搜索|查找|运行|执行|测试|构建|写入|修改|创建|删除|"
    r"修复|排查|检查|优化|实现|部署|提交|安装|下载|"
    r"\b(read|list|open|grep|glob|find|run|exec|test|build|write|edit|patch|install|git)\b)",
    re.I,
)
_FILE_TOKEN_RE = re.compile(r"[\w./\\-]+\.(py|md|json|toml|txt|ya?ml|js|ts|tsx|jsx|css|html|sh|ini|cfg)")


def _needs_tools(messages: list[ChatMessage], tools: list[dict]) -> list[dict]:
    """Only expose tools when the turn plausibly needs an action.

    A 1B model with 12 schemas still calls ``run_tests`` for "12345*6789" and
    ``read_file`` for a plain question. With no schemas those prompts stay
    plain chat; tool-result follow-ups keep the schemas because the model must
    either use the result or chain another call.
    """
    if not tools:
        return tools
    for msg in messages[-4:]:
        if msg.role == "tool" or msg.tool_calls:
            return tools
    for msg in reversed(messages):
        if msg.role == "user":
            text = msg.content or ""
            if _TOOL_INTENT_RE.search(text) or _FILE_TOKEN_RE.search(text):
                return tools
            return []
    return tools


_WRITE_INTENT_RE = re.compile(
    r"(写入|修改|创建|删除|修复|实现|优化|补全|重命名|重构|改成|替换|生成|添加|加个|"
    r"\b(write|edit|patch|apply|fix|implement|refactor|rename|create|delete|modify|generate)\b)",
    re.I,
)
_EXEC_INTENT_RE = re.compile(
    r"(运行|执行|测试|构建|安装|部署|提交|命令|终端|"
    r"\b(run|exec|shell|test|build|install|deploy|lint|git|commit)\b)",
    re.I,
)
_WEB_INTENT_RE = re.compile(
    r"(联网|上网|网上|网页|最新|查一下|搜一下|\b(web|search online|fetch|url|http)\b)",
    re.I,
)
_READ_TOOLS = frozenset({"read_file", "list_dir", "grep", "glob", "find_symbol", "ask_user"})


def _trim_tools_for_task(messages: list[ChatMessage], tools: list[dict]) -> list[dict]:
    """Narrow the schema block to the verbs the current user turn needs.

    The allowlist already removes LSP/browser/task; this is the per-task cut.
    A read-only prompt gets file inspection + ``ask_user`` only, while edit or
    command prompts add the matching write/execute schemas. Tool-result
    follow-ups keep the full set so the model can chain a second call.
    """
    if not tools:
        return tools
    for msg in messages[-4:]:
        if msg.role == "tool" or msg.tool_calls:
            return tools
    text = next((msg.content or "" for msg in reversed(messages) if msg.role == "user"), "")
    if not _TOOL_INTENT_RE.search(text) and not _FILE_TOKEN_RE.search(text):
        return []
    needed = set(_READ_TOOLS)
    if _WRITE_INTENT_RE.search(text):
        needed.update({"write_file", "apply_patch", "apply_edit"})
    if _EXEC_INTENT_RE.search(text):
        needed.update({"run_shell", "run_tests"})
    if _WEB_INTENT_RE.search(text):
        needed.update({"web_search", "web_fetch"})
    selected = [
        tool
        for tool in tools
        if isinstance(tool, dict)
        and isinstance(tool.get("function"), dict)
        and str(tool["function"].get("name") or "") in needed
    ]
    return selected


def _active_tools(messages: list[ChatMessage], tools: list[dict]) -> list[dict]:
    return _trim_tools_for_task(messages, _needs_tools(messages, _filter_tools(tools)))


def _model_path(model: str) -> Path:
    from spark.local_models import models_dir

    path = Path(model).expanduser()
    if path.is_file():
        return path
    candidate = models_dir() / path.name
    return candidate


def _plain_tool_result(raw: str) -> str:
    """Turn Spark's tool JSON envelope into plain text for a small model.

    ``read_file`` returns ``{"path": ..., "content": "...", ...}``. Feeding the
    whole JSON blob makes MiniCPM5 summarize metadata ("3 lines, title,
    paragraph") instead of the actual file body. Prefer the payload's
    ``content``/``stdout``/``output`` field and pretty-print the rest.
    """
    try:
        obj = json.loads(raw)
    except ValueError:
        return raw
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        for key in ("content", "stdout", "output", "text", "result"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _localize_tool_results(messages: list[dict]) -> list[dict]:
    """Flatten tool payloads and add a final answer-only instruction."""
    converted = messages
    had_tool = False
    for item in converted:
        if item.get("role") == "tool":
            had_tool = True
            content = item.get("content")
            if isinstance(content, str):
                item["content"] = _plain_tool_result(content)
    if had_tool and converted and converted[-1].get("role") == "tool":
        converted.append(
            {
                "role": "user",
                "content": (
                    "以上是工具返回的真实结果。请只依据这个结果，回答最后一条用户任务；"
                    "不要重复历史里的旧回答，不要编造，也不要回复“收到”。"
                ),
            }
        )
    return converted


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

    def _generate_jinja(self, llm, messages: list[ChatMessage], tools: list[dict], max_tokens: int) -> dict:
        template = str(llm.metadata.get("tokenizer.chat_template") or "")
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
        converted = _localize_tool_results(converted)
        rendered = formatter(
            messages=cast(Any, converted),
            tools=cast(Any, tools or None),
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
            temp=0.2,
            top_p=0.9,
            min_p=0.0,
            top_k=20,
            repeat_penalty=1.1,
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

    def _generate_chat(self, llm, messages: list[ChatMessage], tools: list[dict], max_tokens: int) -> dict:
        try:
            return llm.create_chat_completion(
                messages=_to_local_messages(messages, tools),
                temperature=0.2,
                top_p=0.9,
                repeat_penalty=1.1,
                min_p=0.0,
                max_tokens=max_tokens,
                stream=False,
                stop=[_LT + "|im_end|" + _GT],
            )
        except TypeError:
            return llm.create_chat_completion(
                messages=_to_local_messages(messages, tools),
                temperature=0.2,
                top_p=0.9,
                repeat_penalty=1.1,
                max_tokens=max_tokens,
                stream=False,
            )

    def _generate(self, messages: list[ChatMessage], tools: list[dict], effort: str | None) -> dict:
        llm = self._ensure()
        tools = _active_tools(messages, tools)
        max_tokens = {"low": 256, "medium": 768, "high": 1280}.get(effort or "medium", 768)
        template = str(llm.metadata.get("tokenizer.chat_template") or "")
        if template:
            return self._generate_jinja(llm, messages, tools, max_tokens)
        return self._generate_chat(llm, messages, tools, max_tokens)
    def _thinking_deltas(self, content: str) -> tuple[list[ChatDelta], str]:
        think_open = _LT + "think" + _GT
        think_close = _LT + "/think" + _GT
        deltas: list[ChatDelta] = []
        if think_open in content and think_close in content:
            before, rest = content.split(think_open, 1)
            reasoning, after = rest.split(think_close, 1)
            if before.strip():
                deltas.append(ChatDelta(type="text", text=before))
            if reasoning.strip():
                deltas.append(ChatDelta(type="reasoning", text=reasoning.strip()))
            return deltas, after
        if think_close in content:
            # Some chat templates open the thinking block implicitly at the
            # assistant prefix; only the closing tag reaches detokenized text.
            reasoning, content = content.split(think_close, 1)
            reasoning = reasoning.replace(think_open, "").strip()
            if reasoning:
                deltas.append(ChatDelta(type="reasoning", text=reasoning))
        return deltas, content

    def _usage_delta(self, result: dict) -> ChatDelta:
        usage_raw = result.get("usage") if isinstance(result.get("usage"), dict) else None
        usage = None
        if usage_raw:
            usage = {
                "prompt_tokens": int(usage_raw.get("prompt_tokens") or 0),
                "completion_tokens": int(usage_raw.get("completion_tokens") or 0),
            }
        return ChatDelta(type="end", usage=usage)

    async def stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict],
        effort: str | None = None,
    ) -> AsyncIterator[ChatDelta]:
        result = await asyncio.to_thread(self._generate, messages, tools, effort)
        active_tools = _active_tools(messages, tools)
        allowed = {
            str(t.get("function", {}).get("name") or "")
            for t in active_tools
            if isinstance(t, dict) and isinstance(t.get("function"), dict)
        }
        message = (result.get("choices") or [{}])[0].get("message") or {}
        content = str(message.get("content") or "")
        deltas, content = self._thinking_deltas(content)
        for delta in deltas:
            yield delta
        calls, text = parse_tool_calls(content)
        # A model can hallucinate a familiar tool name even when its schema was
        # not supplied. Never execute a call outside the active tool set.
        calls = [call for call in calls if call.name in allowed]
        if text:
            for piece in re.split(r"(?<=[。！？.!?])\s*", text):
                if piece:
                    yield ChatDelta(type="text", text=piece)
        for call in calls:
            yield ChatDelta(type="tool_call", tool_call=call)
        yield self._usage_delta(result)