"""Model gateway status/config tests (no network calls)."""
from __future__ import annotations

import pytest

from spark.config import SparkConfig
from spark.control.gateway import ModelGateway


def test_mock_provider_is_not_configured() -> None:
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    gateway = ModelGateway(cfg)
    assert gateway.configured is False
    assert gateway.status()["configured"] is False
    with pytest.raises(RuntimeError):
        gateway.router()


def test_configured_provider_reports_status() -> None:
    cfg = SparkConfig()
    cfg.provider.name = "openai_compat"
    cfg.provider.model = "test-model"
    cfg.provider.base_url = "https://example.invalid/v1"
    cfg.provider.api_key = "test-key"
    cfg.provider.fallback_models = ["backup-model"]
    gateway = ModelGateway(cfg)
    assert gateway.configured is True
    status = gateway.status()
    assert status["model"] == "test-model"
    assert status["fallbacks"] == ["backup-model"]


def test_router_accepts_custom_model_name() -> None:
    """LiteLLM Router requires a provider hint for non-built-in model names."""
    cfg = SparkConfig()
    cfg.provider.name = "openai_compat"
    cfg.provider.model = "spark-x2.6-4b"
    cfg.provider.base_url = "https://example.invalid/v2"
    cfg.provider.api_key = "test-key"
    gateway = ModelGateway(cfg)
    assert gateway.router() is not None
