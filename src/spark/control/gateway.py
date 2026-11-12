"""LiteLLM Router adapter for the enterprise model gateway.

The control plane does not call a model itself yet; this is the single place
where provider fallbacks, retries and (later) budgets/rate limits will live.
Existing AgentLoop providers keep working unchanged.
"""

from __future__ import annotations

from typing import Any

from spark.config import SparkConfig, env_api_key


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

        provider = self.cfg.provider
        params: dict[str, Any] = {
            "model": provider.model,
            "api_base": provider.base_url,
        }
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
            model_list.append(
                {
                    "model_name": name,
                    "litellm_params": {**params, "model": model},
                }
            )

        kwargs: dict[str, Any] = {"model_list": model_list, "num_retries": 2}
        if fallback_names:
            kwargs["fallbacks"] = [{"spark-primary": fallback_names}]
        self._router = Router(**kwargs)
        return self._router
