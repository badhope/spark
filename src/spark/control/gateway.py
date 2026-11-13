"""LiteLLM Router adapter for the enterprise model gateway.

The control plane does not call a model itself yet; this is the single place
where provider fallbacks, retries and (later) budgets/rate limits will live.
Existing AgentLoop providers keep working unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

from spark.config import SparkConfig, env_api_key

logger = logging.getLogger(__name__)


class ModelGateway:
    def __init__(self, cfg: SparkConfig) -> None:
        self.cfg = cfg
        self._router: Any = None

    def _api_key(self) -> str:
        provider = self.cfg.provider
        return provider.api_key or env_api_key(self.cfg)

    @property
    def configured(self) -> bool:
        provider = self.cfg.provider
        if provider.name == "mock":
            return False
        if provider.name == "ollama":
            return bool(provider.base_url and provider.model)
        return bool(provider.base_url and provider.model and self._api_key())

    def status(self) -> dict[str, Any]:
        provider = self.cfg.provider
        return {
            "configured": self.configured,
            "provider": provider.name,
            "model": provider.model,
            "base_url": provider.base_url,
            "fallbacks": list(provider.fallback_models),
        }

    def router(self) -> Any:
        if not self.configured:
            raise RuntimeError(
                "model gateway is not configured: set an OpenAI-compatible "
                "endpoint and API key, or use provider=mock"
            )
        if self._router is not None:
            return self._router

        from litellm import Router

        from spark.providers.openai_compat import litellm_model_name

        provider = self.cfg.provider
        model_name, provider_hint = litellm_model_name(provider.model)
        params: dict[str, Any] = {
            "model": model_name,
            "api_base": provider.base_url,
        }
        if provider_hint:
            params["custom_llm_provider"] = provider_hint
        key = "ollama" if provider.name == "ollama" else self._api_key()
        if key:
            params["api_key"] = key

        model_list: list[dict[str, Any]] = [
            {"model_name": "spark-primary", "litellm_params": dict(params)}
        ]
        fallback_names: list[str] = []
        for index, model in enumerate(provider.fallback_models):
            name = f"spark-fallback-{index}"
            fallback_names.append(name)
            fallback_model, fallback_hint = litellm_model_name(model)
            fallback_params: dict[str, Any] = {
                **params,
                "model": fallback_model,
            }
            if fallback_hint:
                fallback_params["custom_llm_provider"] = fallback_hint
            model_list.append(
                {
                    "model_name": name,
                    "litellm_params": fallback_params,
                }
            )

        kwargs: dict[str, Any] = {"model_list": model_list, "num_retries": 2}
        if fallback_names:
            kwargs["fallbacks"] = [{"spark-primary": fallback_names}]
        self._router = Router(**kwargs)
        return self._router

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        timeout: float = 45.0,
        max_tokens: int = 900,
        temperature: float = 0.2,
    ) -> str:
        """One-shot completion through the router (office summaries, etc.).

        Kept separate from the streaming provider used by the local agent: the
        control plane only needs plain text and must degrade cleanly when the
        model is slow or unavailable.
        """
        if not self.configured:
            raise RuntimeError(
                "model gateway is not configured: set an OpenAI-compatible "
                "endpoint and API key, or use provider=mock"
            )
        response = await self.router().acompletion(
            model="spark-primary",
            messages=messages,
            timeout=timeout,
            max_tokens=max_tokens,
            temperature=temperature,
            num_retries=1,
        )
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""
        choice = choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            # A truncated sentence is worse than no AI enrichment at all.
            logger.warning("model gateway output truncated; discarding partial content")
            return ""
        message = getattr(choice, "message", None)
        content = getattr(message, "content", "") if message is not None else ""
        return str(content or "").strip()
