"""Provider error classification and configuration preflight.

These tests are intentionally transport-free: they pin the mapping from the
messy exception shapes LiteLLM emits to the stable codes the UI relies on.
"""

from __future__ import annotations

from spark.config import SparkConfig
from spark.providers.errors import classify_provider_error, provider_preflight
from spark.providers.mock import MockProvider


def _cfg(name: str = "openai_compat", *, key: str | None = "test-key", model: str = "demo-model") -> SparkConfig:
    cfg = SparkConfig()
    cfg.provider.name = name
    cfg.provider.base_url = "https://api.example.com/v1"
    cfg.provider.model = model
    cfg.provider.api_key = key
    cfg.provider.api_key_env = "SPARK_ERROR_TEST_KEY"
    return cfg


def _exc(message: str = "", status: int | None = None) -> Exception:
    exc = Exception(message)
    if status is not None:
        exc.status_code = status  # type: ignore[attr-defined]
    return exc


def test_auth_failure_is_actionable() -> None:
    info = classify_provider_error(_exc("Unauthorized", 401), _cfg())
    assert info.code == "auth_failed"
    assert info.retryable is False
    assert "API Key" in info.hint
    assert info.status_code == 401


def test_model_not_found_and_endpoint_not_found_are_distinct() -> None:
    missing_model = classify_provider_error(
        _exc("model_not_found: model demo-model does not exist", 404), _cfg()
    )
    assert missing_model.code == "model_not_found"
    assert "demo-model" in missing_model.message

    wrong_path = classify_provider_error(_exc("404 page not found", 404), _cfg())
    assert wrong_path.code == "endpoint_not_found"
    assert "/v1" in wrong_path.hint


def test_rate_limit_timeout_connection_and_context() -> None:
    assert classify_provider_error(_exc("rate limit reached", 429), _cfg()).code == "rate_limited"
    assert classify_provider_error(TimeoutError("request timed out"), _cfg()).code == "timeout"
    assert classify_provider_error(ConnectionRefusedError("connection refused"), _cfg()).code == "connection_failed"
    assert (
        classify_provider_error(
            _exc("maximum context length is 16384 tokens", 400), _cfg()
        ).code
        == "context_too_long"
    )


def test_quota_and_server_error() -> None:
    assert classify_provider_error(_exc("insufficient_quota: balance exhausted", 402), _cfg()).code == "quota_exceeded"
    assert classify_provider_error(_exc("upstream failure", 502), _cfg()).code == "server_error"


def test_local_model_404_explains_how_to_install() -> None:
    cfg = _cfg(name="ollama", model="qwen3:8b")
    cfg.provider.base_url = "http://127.0.0.1:11434/v1"
    info = classify_provider_error(_exc("model 'qwen3:8b' not found", 404), cfg)
    assert info.code == "model_not_found"
    assert "ollama pull qwen3:8b" in info.hint


def test_preflight_blocks_missing_key_and_mock_fallback() -> None:
    cfg = _cfg(key=None)
    info = provider_preflight(cfg)
    assert info is not None and info.code == "missing_api_key"

    fallback = provider_preflight(cfg, MockProvider())
    assert fallback is not None and fallback.code == "mock_fallback"
    assert "Mock" in fallback.message


def test_preflight_allows_mock_and_ollama_without_key() -> None:
    mock_cfg = _cfg(name="mock", key=None)
    assert provider_preflight(mock_cfg) is None

    ollama_cfg = _cfg(name="ollama", key=None)
    ollama_cfg.provider.base_url = "http://127.0.0.1:11434/v1"
    assert provider_preflight(ollama_cfg) is None


def test_secret_is_redacted_from_detail() -> None:
    cfg = _cfg(key="sk-super-secret-value")
    info = classify_provider_error(
        Exception("request failed with key sk-super-secret-value"), cfg
    )
    assert "sk-super-secret-value" not in info.detail
    assert "***" in info.detail
