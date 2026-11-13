from __future__ import annotations

import logging

from spark.config import SparkConfig, require_api_key
from spark.errors import ConfigError
from spark.providers.base import Provider
from spark.providers.mock import MockProvider
from spark.providers.openai_compat import OllamaProvider, OpenAICompatProvider

logger = logging.getLogger(__name__)


def create_provider(cfg: SparkConfig, mock: MockProvider | None = None) -> Provider:
    valid = ("openai_compat", "ollama", "mock")
    if cfg.provider.name not in valid:
        raise ValueError(
            f"Unknown provider '{cfg.provider.name}'. "
            f"Valid options: {', '.join(valid)}"
        )
    if cfg.provider.name == "mock":
        return mock or MockProvider()
    try:
        key = require_api_key(cfg)
    except ConfigError:
        logger.warning(
            "未配置 API Key（provider=%s）—— 以演示模式(mock)运行；"
            "在设置中填入密钥后自动切换真实模型。",
            cfg.provider.name,
        )
        return mock or MockProvider()
    if cfg.provider.name == "ollama":
        base = cfg.provider.base_url
        if not base or base == "https://api.deepseek.com/v1":
            base = "http://127.0.0.1:11434/v1"
        return OllamaProvider(base, cfg.provider.model, key)
    return OpenAICompatProvider(
        cfg.provider.base_url,
        cfg.provider.model,
        key or "",
        fallback_models=getattr(cfg.provider, "fallback_models", None),
        api_keys=getattr(cfg.provider, "api_keys", None),
        thinking=getattr(cfg.provider, "thinking", "default"),
    )
