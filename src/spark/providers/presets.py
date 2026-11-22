"""Provider presets — one-click model targets, domestic and native.

This is the single source of truth for the Web settings preset picker and the
CLI `spark providers` command.  It deliberately contains no secrets: presets
name the environment variable that should hold the key, and the actual
credential stays in config/env.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from spark.config import ProviderName

PresetId = Literal[
    "deepseek",
    "qwen",
    "kimi",
    "glm",
    "doubao",
    "anthropic",
    "openai",
    "gemini",
    "azure",
    "bedrock",
    "ollama",
    "mock",
]


@dataclass(frozen=True)
class ProviderPreset:
    id: PresetId
    label: str
    provider: ProviderName
    base_url: str
    model: str
    api_key_env: str
    native: bool = False
    prompt_cache: bool = False
    tier: str = "cost"
    note: str = ""
    label_en: str = ""

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        id="deepseek",
        label="DeepSeek",
        provider="openai_compat",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
        tier="cost",
        note="国产统一入口；thinking 模型走 deepseek-reasoner。",
    ),
    ProviderPreset(
        id="qwen",
        label="Qwen / 百炼",
        label_en="Qwen / Bailian",
        provider="openai_compat",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3-coder-plus",
        api_key_env="DASHSCOPE_API_KEY",
        tier="capability",
    ),
    ProviderPreset(
        id="kimi",
        label="Kimi / Moonshot",
        provider="openai_compat",
        base_url="https://api.moonshot.cn/v1",
        model="kimi-k2-0905-preview",
        api_key_env="MOONSHOT_API_KEY",
        tier="capability",
    ),
    ProviderPreset(
        id="glm",
        label="GLM / 智谱",
        label_en="GLM / Zhipu",
        provider="openai_compat",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        model="glm-4.5",
        api_key_env="ZHIPUAI_API_KEY",
        tier="capability",
    ),
    ProviderPreset(
        id="doubao",
        label="豆包 / 方舟",
        label_en="Doubao / Ark",
        provider="openai_compat",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="doubao-seed-code-preview",
        api_key_env="ARK_API_KEY",
        tier="cost",
    ),
    ProviderPreset(
        id="anthropic",
        label="Anthropic Claude",
        provider="anthropic",
        base_url="",
        model="claude-sonnet-4-5",
        api_key_env="ANTHROPIC_API_KEY",
        native=True,
        prompt_cache=True,
        tier="capability",
        note="Messages API；system prompt 带 ephemeral cache_control。",
    ),
    ProviderPreset(
        id="openai",
        label="OpenAI",
        provider="openai",
        base_url="",
        model="gpt-5",
        api_key_env="OPENAI_API_KEY",
        native=True,
        tier="capability",
        note="LiteLLM 原生 OpenAI 通道；Responses 参数按模型能力透传。",
    ),
    ProviderPreset(
        id="gemini",
        label="Google Gemini",
        provider="gemini",
        base_url="",
        model="gemini-2.5-pro",
        api_key_env="GEMINI_API_KEY",
        native=True,
        tier="capability",
    ),
    ProviderPreset(
        id="azure",
        label="Azure OpenAI",
        provider="azure",
        base_url="https://YOUR-RESOURCE.openai.azure.com",
        model="gpt-4.1",
        api_key_env="AZURE_OPENAI_API_KEY",
        native=True,
        tier="capability",
        note="base_url 换成自己的 Azure 资源；model 填 deployment 名。",
    ),
    ProviderPreset(
        id="bedrock",
        label="AWS Bedrock",
        provider="bedrock",
        base_url="",
        model="anthropic.claude-3-5-sonnet-20240620-v1:0",
        api_key_env="AWS_ACCESS_KEY_ID",
        native=True,
        tier="capability",
        note="使用标准 AWS 凭据链；不需要在 Spark 里再填 API key。",
    ),
    ProviderPreset(
        id="ollama",
        label="Ollama 本地",
        label_en="Ollama (local)",
        provider="ollama",
        base_url="http://127.0.0.1:11434/v1",
        model="qwen3-coder:latest",
        api_key_env="",
        tier="offline",
    ),
    ProviderPreset(
        id="mock",
        label="Mock 演示",
        label_en="Mock demo",
        provider="mock",
        base_url="http://localhost",
        model="mock-model",
        api_key_env="",
        tier="offline",
    ),
)

_PRESET_BY_ID: dict[str, ProviderPreset] = {preset.id: preset for preset in PRESETS}
NATIVE_PRESETS = tuple(preset for preset in PRESETS if preset.native)


def get_preset(preset_id: str) -> ProviderPreset | None:
    return _PRESET_BY_ID.get(preset_id.strip().lower())


def presets_payload(*, native_only: bool = False) -> list[dict[str, object]]:
    source = NATIVE_PRESETS if native_only else PRESETS
    return [preset.as_dict() for preset in source]


__all__ = [
    "NATIVE_PRESETS",
    "PRESETS",
    "PresetId",
    "ProviderPreset",
    "get_preset",
    "presets_payload",
]
