"""Local GGUF catalog and MiniCPM/Qwen tool-call adapters."""

from __future__ import annotations

import json
from pathlib import Path

from spark.config import SparkConfig
from spark.core.context import system_prompt
from spark.local_models import CATALOG, LocalModelManager, get_spec
from spark.providers.llama_cpp import _filter_tools, _localize_tool_results, _needs_tools, parse_tool_calls


def test_catalog_contains_strong_small_long_context_defaults() -> None:
    mini = get_spec("minicpm5-1b")
    assert mini is not None
    assert mini.params == "1.08B"
    assert mini.context == 131_072
    assert mini.size_bytes == 688_065_920
    assert mini.recommended
    assert mini.license == "Apache-2.0"
    assert any("MiniCPM5-1B-Q4_K_M.gguf" in u for u in mini.urls)


def test_all_catalog_models_are_under_two_billion_and_have_mirrors() -> None:
    for spec in CATALOG:
        assert spec.context >= 32_768
        assert spec.urls and all(u.startswith("https://") for u in spec.urls)
        assert spec.tool_parser in {"minicpm5", "qwen3.5"}


def test_parse_minicpm_function_block() -> None:
    lt, gt = chr(60), chr(62)
    text = (
        lt + 'function name="read_file"' + gt
        + lt + 'param name="path"' + gt + "README.md" + lt + "/param" + gt
        + lt + "/function" + gt
    )
    calls, cleaned = parse_tool_calls(text)
    assert [c.name for c in calls] == ["read_file"]
    assert calls[0].arguments == {"path": "README.md"}
    assert cleaned == ""


def test_parse_qwen_json_tool_block_and_keep_prose() -> None:
    lt, gt = chr(60), chr(62)
    text = "我先看一下。" + lt + "tool_call" + gt + '{"name":"shell","arguments":{"command":"pwd"}}' + lt + "/tool_call" + gt
    calls, cleaned = parse_tool_calls(text)
    assert [c.name for c in calls] == ["shell"]
    assert calls[0].arguments == {"command": "pwd"}
    assert "我先看一下" in cleaned


def test_snapshot_marks_uninstalled_models(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SPARK_MODEL_DIR", str(tmp_path / "models"))
    manager = LocalModelManager()
    snap = manager.snapshot()
    assert snap["models_dir"].endswith("models")
    assert all(not m["installed"] for m in snap["models"])
    assert snap["models"][0]["name"] == "MiniCPM5-1B"


def test_preflight_blocks_missing_local_file(monkeypatch) -> None:
    from spark.providers.errors import provider_preflight

    cfg = SparkConfig()
    cfg.provider.name = "llama_cpp"
    cfg.provider.model = str(Path("/tmp") / "definitely-not-here.gguf")
    issue = provider_preflight(cfg, live_provider=object())
    assert issue is not None
    assert issue.code == "local_model_missing"
    assert "下载" in issue.hint


def test_parse_qwen35_function_equals_block() -> None:
    lt, gt = chr(60), chr(62)
    text = (
        lt + "function=read_file" + gt + "\n"
        + lt + "parameter=path" + gt + "\nREADME.md\n" + lt + "/parameter" + gt + "\n"
        + lt + "/function" + gt
    )
    calls, cleaned = parse_tool_calls(text)
    assert [c.name for c in calls] == ["read_file"]
    assert calls[0].arguments == {"path": "README.md"}
    assert cleaned.strip() == ""


def test_local_provider_uses_compact_prompt(tmp_path: Path) -> None:
    cfg = SparkConfig()
    cfg.provider.name = "llama_cpp"
    cfg.agent.sandbox_mode = "workspace"
    prompt = system_prompt(tmp_path, cfg)
    assert "工具结果" in prompt
    assert "## Tools" not in prompt
    # 1B 模型不该被技能目录/子代理清单/仓库地图淹没
    assert "## Skills" not in prompt
    assert len(prompt) < 3000


def test_local_tool_result_is_flattened_before_followup() -> None:
    payload = json.dumps(
        {"path": "/tmp/README.md", "content": "# Title\n\nbody", "total_lines": 3},
        ensure_ascii=False,
    )
    messages = [
        {"role": "system", "content": "s"},
        {"role": "tool", "content": payload, "tool_call_id": "call_1"},
    ]
    out = _localize_tool_results(messages)
    assert out[1]["content"] == "# Title\n\nbody"
    assert out[-1]["role"] == "user"
    assert "不要回复“收到”" in out[-1]["content"]


def test_local_tool_schema_is_limited_to_core_verbs() -> None:
    names = ["read_file", "list_dir", "run_tests", "lsp_rename", "browser_open", "task", "web_search"]
    tools = [{"type": "function", "function": {"name": name, "description": name}} for name in names]
    out = _filter_tools(tools)
    kept = {t["function"]["name"] for t in out}
    assert {"read_file", "list_dir", "run_tests", "web_search"} <= kept
    assert "lsp_rename" not in kept
    assert "browser_open" not in kept
    assert "task" not in kept


def test_only_action_prompts_expose_tools() -> None:
    from spark.models import ChatMessage, ToolCall

    tools = [{"type": "function", "function": {"name": "read_file", "description": "r"}}]
    math = _needs_tools([ChatMessage(role="user", content="计算 12345 * 6789，只给结果。")], tools)
    assert math == []
    chat = _needs_tools([ChatMessage(role="user", content="你好，介绍一下你自己。")], tools)
    assert chat == []
    read = _needs_tools([ChatMessage(role="user", content="请读取 README.md 并总结。")], tools)
    assert read == tools
    followup = _needs_tools(
        [
            ChatMessage(role="user", content="看下这个文件"),
            ChatMessage(role="assistant", content="", tool_calls=[ToolCall(id="c", name="read_file", arguments={})]),
        ],
        tools,
    )
    assert followup == tools


def test_hallucinated_tool_name_is_not_executed() -> None:
    import asyncio

    from spark.models import ChatMessage
    from spark.providers.llama_cpp import LlamaCppProvider

    provider = LlamaCppProvider("/tmp/fake.gguf")
    lt, gt = chr(60), chr(62)
    provider._generate = lambda messages, tools, effort: {  # type: ignore[method-assign]
        "choices": [
            {
                "message": {
                    "content": (
                        lt + 'function name="run_tests"' + gt
                        + lt + 'param name="cmd"' + gt + "pwd" + lt + "/param" + gt
                        + lt + "/function" + gt
                    )
                }
            }
        ]
    }
    msgs = [ChatMessage(role="user", content="计算 12345 * 6789")]
    out = asyncio.run(_collect(provider.stream(msgs, [{"type": "function", "function": {"name": "read_file"}}])))
    assert [d.type for d in out] == ["end"]


async def _collect(agen):
    return [item async for item in agen]


def test_download_resumes_verifies_and_marks_installed(tmp_path, monkeypatch) -> None:
    import http.server
    import threading
    import time

    import spark.local_models as lm

    payload_dir = tmp_path / "payload"
    payload_dir.mkdir()
    payload = payload_dir / "MiniCPM5-1B-Q4_K_M.gguf"
    payload.write_bytes(b"GGUF" + b"x" * (1024 * 1024 - 4))
    import functools

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(payload_dir)
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/{payload.name}"
    spec = lm.LocalModelSpec(
        id="download-test",
        name="Download test",
        params="1B",
        filename=payload.name,
        size_bytes=payload.stat().st_size,
        context=1024,
        license="Apache-2.0",
        languages="中文",
        summary="test",
        strengths=("test",),
        tool_parser="minicpm5",
        chat_format="default",
        urls=(url,),
    )
    monkeypatch.setenv("SPARK_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(lm, "CATALOG", (spec,))
    monkeypatch.setattr(lm, "get_spec", lambda model_id: spec if model_id == spec.id else None)
    try:
        manager = lm.LocalModelManager()
        manager.start_download(spec.id)
        for _ in range(100):
            if manager.installed(spec):
                break
            time.sleep(0.1)
        assert manager.installed(spec), manager._jobs.get(spec.id)
        assert (tmp_path / "models" / (spec.filename + ".sha256")).is_file()
        assert manager.snapshot()["models"][0]["installed"] is True
    finally:
        server.shutdown()
        server.server_close()
