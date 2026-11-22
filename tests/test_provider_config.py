import pytest

from spark.config import SparkConfig, require_api_key
from spark.errors import ConfigError


def test_missing_key_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPARK_API_KEY", raising=False)
    monkeypatch.delenv("USER_KEY", raising=False)
    cfg = SparkConfig()
    cfg.provider.name = "openai_compat"
    cfg.provider.api_key_env = "USER_KEY"
    with pytest.raises(ConfigError, match="USER_KEY"):
        require_api_key(cfg)


def test_mock_skips_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPARK_API_KEY", raising=False)
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    # mock provider should not raise ConfigError without an API key
    result = require_api_key(cfg)
    assert result is None or isinstance(result, str)


def test_missing_key_returns_unavailable_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPARK_API_KEY", raising=False)
    monkeypatch.delenv("USER_KEY", raising=False)
    cfg = SparkConfig()
    cfg.provider.name = "openai_compat"
    cfg.provider.api_key = None
    cfg.provider.api_key_env = "USER_KEY"
    from spark.providers.factory import create_provider
    from spark.providers.mock import UnavailableProvider

    # 缺 Key 不得回退 Mock：返回 UnavailableProvider，任何回合都会如实报错。
    assert isinstance(create_provider(cfg), UnavailableProvider)


def test_key_present_builds_openai_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USER_KEY", "sk-test-not-real")
    cfg = SparkConfig()
    cfg.provider.name = "openai_compat"
    cfg.provider.base_url = "https://example.invalid/v1"
    cfg.provider.model = "m"
    cfg.provider.api_key_env = "USER_KEY"
    from spark.providers.factory import create_provider
    from spark.providers.openai_compat import OpenAICompatProvider

    assert isinstance(create_provider(cfg), OpenAICompatProvider)


def test_bare_model_gets_openai_provider_hint() -> None:
    """自定义模型名必须显式按 OpenAI 兼容处理。

    LiteLLM 只能按内置规则推断 provider；裸自定义名（glm-5.3-flash 等）会报
    "LLM Provider NOT provided"。带 "/" 的前缀写法是用户显式指定，原样尊重。
    """
    from spark.providers.openai_compat import OpenAICompatProvider

    p = OpenAICompatProvider("https://api.example.com/v1", "glm-5.3-flash", "k")
    assert p._litellm_model() == ("glm-5.3-flash", "openai")
    p2 = OpenAICompatProvider("https://api.example.com/v1", "openai/gpt-4o", "k")
    assert p2._litellm_model() == ("openai/gpt-4o", None)
