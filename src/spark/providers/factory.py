from __future__ import annotations

import logging

from spark.config import SparkConfig, require_api_key
from spark.errors import ConfigError
from spark.providers.base import Provider
from spark.providers.llama_cpp import LlamaCppProvider
from spark.providers.mock import MockProvider, UnavailableProvider
from spark.providers.openai_compat import OllamaProvider, OpenAICompatProvider

logger = logging.getLogger(__name__)


def create_provider(cfg: SparkConfig, mock: MockProvider | None = None) -> Provider:
    valid = ("openai_compat", "openai", "anthropic", "gemini", "azure", "bedrock", "ollama", "llama_cpp", "mock")
    if cfg.provider.name not in valid:
        raise ValueError(
            f"Unknown provider '{cfg.provider.name}'. "
            f"Valid options: {', '.join(valid)}"
        )
    if cfg.provider.name == "mock":
        return mock or MockProvider()
    if cfg.provider.name == "llama_cpp":
        return LlamaCppProvider(cfg.provider.model)
    try:
        key = require_api_key(cfg)
    except ConfigError as exc:
        logger.error(
            "配置不可用（provider=%s）：%s；不会回退到 Mock 演示模型。",
            cfg.provider.name,
            exc,
        )
        return UnavailableProvider(str(exc))
    if cfg.provider.name == "ollama":
        base = cfg.provider.base_url
        if not base or base == "https://api.deepseek.com/v1":
            base = "http://127.0.0.1:11434/v1"
        return OllamaProvider(base, cfg.provider.model, key)
    native = str(cfg.provider.name) if cfg.provider.name != "openai_compat" else ""
    return OpenAICompatProvider(
        cfg.provider.base_url,
        cfg.provider.model,
        key or "",
        fallback_models=getattr(cfg.provider, "fallback_models", None),
        api_keys=getattr(cfg.provider, "api_keys", None),
        thinking=getattr(cfg.provider, "thinking", "default"),
        native=native,
        prompt_cache=native == "anthropic",
        responses_api=native == "openai",
    )
