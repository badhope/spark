"""把模型/传输异常翻译成用户能看懂、能行动的诊断信息。

LiteLLM 的异常文本经常是 ``litellm.APIConnectionError: ...`` 或一大段
JSON。直接把它丢到聊天窗口，用户只能看到“报错了”，却不知道是没配 Key、
模型没下载、端点写错还是服务没启动。本模块只做一件事：把异常收敛成稳定的
错误码、简短结论、下一步操作和可折叠的技术细节。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from spark.config import SparkConfig, env_api_key


@dataclass(frozen=True)
class ProviderErrorInfo:
    """A user-facing model failure plus enough context to fix it."""

    code: str
    title: str
    message: str
    hint: str
    retryable: bool = False
    action: str = "settings"
    provider: str = ""
    model: str = ""
    endpoint: str = ""
    status_code: int | None = None
    detail: str = ""
    params: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": self.title,
            "message": self.message,
            "hint": self.hint,
            "retryable": self.retryable,
            "action": self.action,
            "provider": self.provider,
            "model": self.model,
            "endpoint": self.endpoint,
            "status_code": self.status_code,
            "detail": self.detail,
            "params": self.params,
        }

    @property
    def text(self) -> str:
        """Compact fallback for clients that only understand ``turn_error.text``."""
        return f"{self.title}：{self.message}"


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _is_local(endpoint: str) -> bool:
    try:
        host = (urlparse(endpoint).hostname or "").lower()
    except ValueError:
        return False
    return host in _LOCAL_HOSTS or host.endswith(".local")


def _endpoint_label(base_url: str) -> str:
    """Show scheme+host+path, never userinfo or query strings."""
    if not base_url:
        return ""
    try:
        parsed = urlparse(base_url)
    except ValueError:
        return base_url[:120]
    if not parsed.scheme:
        return base_url[:120]
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{host}{path}"[:160]


def _status_code(exc: Exception) -> int | None:
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if candidate is None:
            continue
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue
    # Spark's provider wrapper raises a plain RuntimeError after exhausting
    # retries, so the original HTTP class survives only in the message.
    lowered = str(exc).lower()
    implied = {
        "authenticationerror": 401,
        "permissiondeniederror": 403,
        "notfounderror": 404,
        "ratelimiterror": 429,
        "badrequesterror": 400,
        "internalservererror": 500,
        "serviceunavailableerror": 503,
        "timeouterror": 408,
    }
    for token, code in implied.items():
        if token in lowered:
            return code
    match = re.search(r"\b(400|401|402|403|404|408|409|422|429|500|502|503|504)\b", str(exc))
    return int(match.group(1)) if match else None


def _clean_detail(exc: Exception, api_key: str = "", limit: int = 500) -> str:
    text = " ".join(str(exc).split())
    if api_key and len(api_key) >= 6:
        text = text.replace(api_key, "***")
    # LiteLLM sometimes embeds a full request dump; keep the first actionable line.
    return text[:limit]


def _provider_fields(cfg: SparkConfig) -> tuple[str, str, str]:
    provider = str(cfg.provider.name)
    model = str(cfg.provider.model or "")
    endpoint = _endpoint_label(str(cfg.provider.base_url or ""))
    return provider, model, endpoint


def missing_key_error(cfg: SparkConfig, *, code: str = "missing_api_key") -> ProviderErrorInfo:
    provider, model, endpoint = _provider_fields(cfg)
    env_name = cfg.provider.api_key_env or "SPARK_API_KEY"
    if code == "mock_fallback":
        return ProviderErrorInfo(
            code="mock_fallback",
            title="未配置 API Key，已阻止 Mock 回退",
            message="当前配置指向真实模型，但缺少凭据，系统原本会退回 Mock 演示模型。为避免把演示回答当成真实结果，本轮请求已停止。",
            hint=f"在「设置 → 模型」填入 API Key，或设置环境变量 {env_name}。只想体验界面时，请显式切换到 Mock 演示模型。",
            retryable=False,
            action="settings",
            provider=provider,
            model=model,
            endpoint=endpoint,
            params={"env": env_name, "model": model, "endpoint": endpoint},
        )
    return ProviderErrorInfo(
        code="missing_api_key",
        title="模型未配置：缺少 API Key",
        message=f"还没有配置 {provider} 的凭据，请求没有发出。",
        hint=f"在「设置 → 模型」填入 API Key，或设置环境变量 {env_name}，然后点「测试连接」。",
        retryable=False,
        action="settings",
        provider=provider,
        model=model,
        endpoint=endpoint,
        params={"env": env_name, "model": model, "endpoint": endpoint},
    )


def provider_preflight(cfg: SparkConfig, live_provider: Any = None) -> ProviderErrorInfo | None:
    """Check configuration before a turn, so obvious mistakes never reach the wire."""
    provider = str(cfg.provider.name)
    if provider == "mock":
        return None
    model = str(cfg.provider.model or "").strip()
    if not model:
        _, _, endpoint = _provider_fields(cfg)
        return ProviderErrorInfo(
            code="missing_model",
            title="模型未配置：缺少模型名",
            message="Provider 已选中，但没有填写模型 ID，无法发起请求。",
            hint="在「设置 → 模型」填写服务商提供的模型 ID，例如 deepseek-chat、qwen3-coder-plus 或本地模型文件名。",
            retryable=False,
            action="settings",
            provider=provider,
            endpoint=endpoint,
            params={"provider": provider},
        )
    base_url = str(cfg.provider.base_url or "").strip()
    if provider in {"openai_compat", "ollama"} and not base_url:
        return ProviderErrorInfo(
            code="missing_base_url",
            title="模型未配置：缺少服务地址",
            message="没有填写 OpenAI 兼容端点地址，请求无法定位到模型服务。",
            hint="在「设置 → 模型」填写 Base URL；本地 Ollama 通常是 http://127.0.0.1:11434/v1，llama.cpp 通常是 http://127.0.0.1:8010/v1。",
            retryable=False,
            action="settings",
            provider=provider,
            model=model,
            params={"model": model},
        )
    if provider not in {"ollama", "bedrock"}:
        key = cfg.provider.api_key or env_api_key(cfg)
        if not key:
            return missing_key_error(cfg, code="mock_fallback" if live_provider is not None else "missing_api_key")
        if live_provider is not None and live_provider.__class__.__name__ == "MockProvider":
            return missing_key_error(cfg, code="mock_fallback")
    return None


def classify_provider_error(
    exc: Exception,
    cfg: SparkConfig,
    *,
    stage: str = "llm",
    live_provider: Any = None,
) -> ProviderErrorInfo:
    """Map a provider exception to a stable code and an actionable suggestion."""
    provider, model, endpoint = _provider_fields(cfg)
    api_key = str(cfg.provider.api_key or "")
    detail = _clean_detail(exc, api_key)
    lowered = detail.lower()
    status = _status_code(exc)
    params = {"provider": provider, "model": model, "endpoint": endpoint, "status": str(status or "")}
    env_name = cfg.provider.api_key_env or "SPARK_API_KEY"
    local = _is_local(str(cfg.provider.base_url or ""))
    params["env"] = env_name
    params["local"] = "1" if local else "0"

    def build(
        code: str,
        title: str,
        message: str,
        hint: str,
        *,
        retryable: bool = True,
        action: str = "settings",
    ) -> ProviderErrorInfo:
        return ProviderErrorInfo(
            code=code,
            title=title,
            message=message,
            hint=hint,
            retryable=retryable,
            action=action,
            provider=provider,
            model=model,
            endpoint=endpoint,
            status_code=status,
            detail=detail,
            params=params,
        )

    # Cancellation is not a model failure and must not tell the user to edit settings.
    if "turn cancelled" in lowered or stage == "cancelled":
        return build(
            "cancelled",
            "请求已取消",
            "本轮生成被用户或客户端中断，没有产生完整回答。",
            "可以直接重新发送；如果重复中断，检查网络或客户端连接。",
            retryable=True,
            action="none",
        )

    # Context length is a 400 at most vendors; detect it before generic bad-request.
    if any(
        word in lowered
        for word in (
            "context length",
            "context_length_exceeded",
            "maximum context",
            "too many tokens",
            "reduce the length",
            "prompt is too long",
        )
    ):
        return build(
            "context_too_long",
            "上下文超出模型窗口",
            f"这轮请求超过了 {model or '当前模型'} 的上下文上限，模型拒绝了请求。",
            "新建会话或清空较早消息；也可以在「设置 → Agent」调低历史预算 / 换用上下文更大的模型。",
            retryable=False,
        )

    if any(word in lowered for word in ("insufficient_quota", "quota exceeded", "billing", "balance", "credit")):
        return build(
            "quota_exceeded",
            "模型额度不足",
            "服务商返回余额或配额不足，当前账号无法继续调用。",
            "到服务商控制台充值或确认套餐额度；也可以切换到其他模型档案。",
            retryable=False,
        )

    if status in {401, 403} or any(
        word in lowered
        for word in ("authentication", "invalid api key", "incorrect api key", "unauthorized", "permission denied")
    ):
        return build(
            "auth_failed",
            "模型鉴权失败",
            f"{provider} 拒绝了这组凭据（HTTP {status or 401}）。",
            f"检查 API Key 是否过期、是否复制完整，以及账号是否有 {model} 的权限；环境变量名应为 {env_name}。",
            retryable=False,
        )

    model_missing_text = any(
        word in lowered
        for word in ("model_not_found", "model not found", "does not exist", "no such model", "unknown model")
    )
    if model_missing_text or (status == 404 and "model" in lowered):
        if local:
            if provider == "ollama":
                hint = (
                    f"本地 Ollama 已响应，但没有找到模型「{model}」。用 `ollama list` 查看、"
                    f"`ollama pull {model}` 拉取，然后重试。"
                )
            else:
                hint = (
                    f"本地 OpenAI 兼容服务已响应，但没有加载模型「{model}」。"
                    "检查启动参数里的模型名/GGUF 文件，以及 /v1/models 返回的 id 是否与配置一致。"
                )
        else:
            hint = f"核对模型 ID「{model}」的拼写；到服务商控制台确认该模型已开通，或换一个已开通的模型。"
        return build(
            "model_not_found",
            "模型不存在或无权访问",
            f"服务端找不到模型「{model}」，请求没有进入推理。",
            hint,
            retryable=False,
        )

    if status == 429 or "rate limit" in lowered or "too many requests" in lowered:
        return build(
            "rate_limited",
            "请求被限流",
            "模型服务暂时限流或并发超限，稍后重试通常可以恢复。",
            "等待几十秒后重试；持续限流时降低并发、换用其他模型档案，或升级服务商套餐。",
        )

    if isinstance(exc, (TimeoutError,)) or exc.__class__.__name__ in {
        "Timeout",
        "APITimeoutError",
        "ReadTimeout",
        "ConnectTimeout",
    } or "timeout" in lowered or "timed out" in lowered:
        return build(
            "timeout",
            "模型响应超时",
            "请求在等待模型返回时超时，可能是网络慢、模型排队或本地机器负载过高。",
            "先重试一次；本地模型可减少上下文或线程数，远程模型检查网络与代理。",
        )

    if isinstance(exc, ConnectionError) or any(
        word in lowered
        for word in (
            "connection refused",
            "connection error",
            "connect error",
            "failed to connect",
            "name or service not known",
            "nodename nor servname",
            "getaddrinfo",
            "network is unreachable",
            "connection reset",
            "ssl certificate",
            "certificate verify",
        )
    ) or exc.__class__.__name__ in {"APIConnectionError", "ConnectError", "ConnectionError"}:
        if local:
            hint = (
                f"本地模型服务没有连上（{endpoint or '端点为空'}）。确认 Ollama / LM Studio / llama.cpp "
                "已启动、端口正确；用浏览器或 curl 打开 /v1/models 验证。"
            )
        else:
            hint = f"检查 Base URL「{endpoint}」、网络/代理和防火墙；用 curl 访问 /v1/models 验证端点可达。"
        return build(
            "connection_failed",
            "连接不上模型服务",
            "请求没有到达模型端点，通常是服务未启动、地址/端口写错或网络被拦截。",
            hint,
        )

    if status == 404:
        return build(
            "endpoint_not_found",
            "模型端点路径不对",
            f"服务端返回 404，{endpoint or '当前 Base URL'} 可能不是正确的 OpenAI 兼容地址。",
            "多数 OpenAI 兼容端点需要以 /v1 结尾（例如 http://127.0.0.1:8010/v1）；检查 Base URL 是否多写或少写路径。",
            retryable=False,
        )

    if status is not None and 500 <= status <= 599:
        return build(
            "server_error",
            "模型服务内部错误",
            f"服务端返回 HTTP {status}，这次推理没有完成。",
            "稍后重试；如果持续出现，查看模型服务日志，或切换到备用模型/端点。",
        )

    if status in {400, 409, 422} or "bad request" in lowered or "invalid_request" in lowered:
        return build(
            "invalid_request",
            "模型拒绝了请求参数",
            "端点认为请求格式或参数不合法，常见于模型不支持当前工具/图片/参数组合。",
            f"确认 {model or '当前模型'} 支持工具调用；在设置里关闭不兼容参数，或换用兼容模型。",
            retryable=False,
        )

    # A provider that silently degraded to mock is a configuration problem, not a network one.
    preflight = provider_preflight(cfg, live_provider)
    if preflight is not None:
        return preflight

    return build(
        "unknown",
        "模型调用失败",
        "模型请求没有完成，错误不在常见分类里。",
        "展开技术细节查看原始错误；先重试一次，仍失败就到「设置 → 模型」点「测试连接」并检查服务日志。",
    )


__all__ = ["ProviderErrorInfo", "classify_provider_error", "provider_preflight", "missing_key_error"]
