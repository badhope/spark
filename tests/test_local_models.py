"""Local GGUF catalog and MiniCPM/Qwen tool-call adapters."""

from __future__ import annotations

from pathlib import Path

from spark.local_models import CATALOG, LocalModelManager, get_spec
from spark.providers.llama_cpp import parse_tool_calls


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
    from spark.config import SparkConfig
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
