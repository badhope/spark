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
    if provider == "llama_cpp":
        from pathlib import Path

        path = Path(model).expanduser()
        if not path.is_file():
            from spark.local_models import models_dir

            path = models_dir() / path.name
        if not path.is_file():
            return ProviderErrorInfo(
                code="local_model_missing",
                title="本地模型还没有下载",
                message=f"找不到本地 GGUF 文件：{path}",
                hint="到「设置 → 模型 → 本地模型」选择 MiniCPM5-1B 等推荐模型，点下载并启用。",
                retryable=False,
                action="settings",
                provider=provider,
                model=model,
                endpoint=str(path),
                params={"model": model, "local": "1"},
            )
        return None
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
            # 不再把“缺 Key”说成 Mock 回退：真实模式就该如实报配置错误。
            return missing_key_error(cfg)
    if live_provider is not None and live_provider.__class__.__name__ == "MockProvider":
        _, model, endpoint = _provider_fields(cfg)
        return ProviderErrorInfo(
            code="provider_mismatch",
            title="运行状态异常：真实配置下出现了 Mock",
            message="配置指向真实模型，但当前运行实例是脚本化 Mock。为避免假回答，本轮已停止。",
            hint="到「设置 → 模型」点「退出演示，恢复真实模型」，或重新保存真实模型配置。",
            retryable=False,
            action="settings",
            provider=provider,
            model=model,
            endpoint=endpoint,
            params={"provider": provider, "model": model},
        )
    return None


_CONTEXT_WORDS = (
    "context length",
    "context_length_exceeded",
    "maximum context",
    "too many tokens",
    "reduce the length",
    "prompt is too long",
    "memory slot",
    "n_ctx",
)
_QUOTA_WORDS = ("insufficient_quota", "quota exceeded", "billing", "balance", "credit")
_AUTH_WORDS = (
    "authentication",
    "invalid api key",
    "incorrect api key",
    "unauthorized",
    "permission denied",
)
_MODEL_MISSING_WORDS = (
    "model_not_found",
    "model not found",
    "does not exist",
    "no such model",
    "unknown model",
)
_CONNECTION_WORDS = (
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


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


class _ProviderErrorClassifier:
    """Ordered rule chain for provider exceptions.

    Each rule returns ``None`` when it does not apply, so the mapping stays
    explicit and the public entry point remains a single ordered pass.
    """

    def __init__(
        self,
        exc: Exception,
        cfg: SparkConfig,
        *,
        stage: str,
        live_provider: Any,
    ) -> None:
        self.exc = exc
        self.cfg = cfg
        self.stage = stage
        self.live_provider = live_provider
        self.provider, self.model, self.endpoint = _provider_fields(cfg)
        api_key = str(cfg.provider.api_key or "")
        self.detail = _clean_detail(exc, api_key)
        self.lowered = self.detail.lower()
        self.status = _status_code(exc)
        self.env_name = cfg.provider.api_key_env or "SPARK_API_KEY"
        self.local = self.provider == "llama_cpp" or _is_local(
            str(cfg.provider.base_url or "")
        )
        self.params = {
            "provider": self.provider,
            "model": self.model,
            "endpoint": self.endpoint,
            "status": str(self.status or ""),
            "env": self.env_name,
            "local": "1" if self.local else "0",
        }

    def build(
        self,
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
            provider=self.provider,
            model=self.model,
            endpoint=self.endpoint,
            status_code=self.status,
            detail=self.detail,
            params=self.params,
        )

    def cancelled(self) -> ProviderErrorInfo | None:
        if "turn cancelled" not in self.lowered and self.stage != "cancelled":
            return None
        return self.build(
            "cancelled",
            "请求已取消",
            "本轮生成被用户或客户端中断，没有产生完整回答。",
            "可以直接重新发送；如果重复中断，检查网络或客户端连接。",
            retryable=True,
            action="none",
        )

    def context_too_long(self) -> ProviderErrorInfo | None:
        if not _contains_any(self.lowered, _CONTEXT_WORDS):
            return None
        return self.build(
            "context_too_long",
            "上下文超出模型窗口",
            f"这轮请求超过了 {self.model or '当前模型'} 的上下文上限，模型拒绝了请求。",
            "新建会话或清空较早消息；也可以在「设置 → Agent」调低历史预算 / 换用上下文更大的模型。",
            retryable=False,
        )

    def quota_exceeded(self) -> ProviderErrorInfo | None:
        if not _contains_any(self.lowered, _QUOTA_WORDS):
            return None
        return self.build(
            "quota_exceeded",
            "模型额度不足",
            "服务商返回余额或配额不足，当前账号无法继续调用。",
            "到服务商控制台充值或确认套餐额度；也可以切换到其他模型档案。",
            retryable=False,
        )

    def auth_failed(self) -> ProviderErrorInfo | None:
        if self.status not in {401, 403} and not _contains_any(self.lowered, _AUTH_WORDS):
            return None
        return self.build(
            "auth_failed",
            "模型鉴权失败",
            f"{self.provider} 拒绝了这组凭据（HTTP {self.status or 401}）。",
            f"检查 API Key 是否过期、是否复制完整，以及账号是否有 {self.model} 的权限；环境变量名应为 {self.env_name}。",
            retryable=False,
        )

    def model_not_found(self) -> ProviderErrorInfo | None:
        text_missing = _contains_any(self.lowered, _MODEL_MISSING_WORDS)
        if not text_missing and not (self.status == 404 and "model" in self.lowered):
            return None
        if self.local:
            hint = self._local_model_hint()
        else:
            hint = f"核对模型 ID「{self.model}」的拼写；到服务商控制台确认该模型已开通，或换一个已开通的模型。"
        return self.build(
            "model_not_found",
            "模型不存在或无权访问",
            f"服务端找不到模型「{self.model}」，请求没有进入推理。",
            hint,
            retryable=False,
        )

    def _local_model_hint(self) -> str:
        if self.provider == "ollama":
            return (
                f"本地 Ollama 已响应，但没有找到模型「{self.model}」。用 `ollama list` 查看、"
                f"`ollama pull {self.model}` 拉取，然后重试。"
            )
        return (
            f"本地 OpenAI 兼容服务已响应，但没有加载模型「{self.model}」。"
            "检查启动参数里的模型名/GGUF 文件，以及 /v1/models 返回的 id 是否与配置一致。"
        )

    def rate_limited(self) -> ProviderErrorInfo | None:
        if self.status != 429 and "rate limit" not in self.lowered and "too many requests" not in self.lowered:
            return None
        return self.build(
            "rate_limited",
            "请求被限流",
            "模型服务暂时限流或并发超限，稍后重试通常可以恢复。",
            "等待几十秒后重试；持续限流时降低并发、换用其他模型档案，或升级服务商套餐。",
        )

    def local_model_load_failed(self) -> ProviderErrorInfo | None:
        if not (
            self.provider == "llama_cpp"
            and (
                isinstance(self.exc, FileNotFoundError)
                or "local model" in self.lowered
                or "llama.cpp" in self.lowered
                or "gguf" in self.lowered
            )
        ):
            return None
        return self.build(
            "local_model_load_failed",
            "本地模型加载失败",
            "GGUF 文件存在，但 llama.cpp 没能加载它；常见原因是文件不完整、量化格式不支持或内存不足。",
            "删除后重新下载并校验；确认 llama-cpp-python 版本支持该模型；降低上下文（SPARK_LOCAL_CTX）或关闭其他占内存程序。",
            retryable=False,
        )

    def timeout(self) -> ProviderErrorInfo | None:
        if not (
            isinstance(self.exc, TimeoutError)
            or self.exc.__class__.__name__
            in {"Timeout", "APITimeoutError", "ReadTimeout", "ConnectTimeout"}
            or "timeout" in self.lowered
            or "timed out" in self.lowered
        ):
            return None
        return self.build(
            "timeout",
            "模型响应超时",
            "请求在等待模型返回时超时，可能是网络慢、模型排队或本地机器负载过高。",
            "先重试一次；本地模型可减少上下文或线程数，远程模型检查网络与代理。",
        )

    def connection_failed(self) -> ProviderErrorInfo | None:
        if not (
            isinstance(self.exc, ConnectionError)
            or _contains_any(self.lowered, _CONNECTION_WORDS)
            or self.exc.__class__.__name__
            in {"APIConnectionError", "ConnectError", "ConnectionError"}
        ):
            return None
        if self.local:
            hint = (
                f"本地模型服务没有连上（{self.endpoint or '端点为空'}）。确认 Ollama / LM Studio / llama.cpp "
                "已启动、端口正确；用浏览器或 curl 打开 /v1/models 验证。"
            )
        else:
            hint = f"检查 Base URL「{self.endpoint}」、网络/代理和防火墙；用 curl 访问 /v1/models 验证端点可达。"
        return self.build(
            "connection_failed",
            "连接不上模型服务",
            "请求没有到达模型端点，通常是服务未启动、地址/端口写错或网络被拦截。",
            hint,
        )

    def endpoint_not_found(self) -> ProviderErrorInfo | None:
        if self.status != 404:
            return None
        return self.build(
            "endpoint_not_found",
            "模型端点路径不对",
            f"服务端返回 404，{self.endpoint or '当前 Base URL'} 可能不是正确的 OpenAI 兼容地址。",
            "多数 OpenAI 兼容端点需要以 /v1 结尾（例如 http://127.0.0.1:8010/v1）；检查 Base URL 是否多写或少写路径。",
            retryable=False,
        )

    def server_error(self) -> ProviderErrorInfo | None:
        if self.status is None or not 500 <= self.status <= 599:
            return None
        return self.build(
            "server_error",
            "模型服务内部错误",
            f"服务端返回 HTTP {self.status}，这次推理没有完成。",
            "稍后重试；如果持续出现，查看模型服务日志，或切换到备用模型/端点。",
        )

    def invalid_request(self) -> ProviderErrorInfo | None:
        if self.status not in {400, 409, 422} and "bad request" not in self.lowered and "invalid_request" not in self.lowered:
            return None
        return self.build(
            "invalid_request",
            "模型拒绝了请求参数",
            "端点认为请求格式或参数不合法，常见于模型不支持当前工具/图片/参数组合。",
            f"确认 {self.model or '当前模型'} 支持工具调用；在设置里关闭不兼容参数，或换用兼容模型。",
            retryable=False,
        )

    def unknown(self) -> ProviderErrorInfo:
        preflight = provider_preflight(self.cfg, self.live_provider)
        if preflight is not None:
            return preflight
        reason = self.detail or "错误不在常见分类里。"
        return self.build(
            "unknown",
            "模型调用失败",
            f"模型请求没有完成：{reason}",
            "展开技术细节查看原始错误；先重试一次，仍失败就到「设置 → 模型」点「测试连接」并检查服务日志。",
        )

    def classify(self) -> ProviderErrorInfo:
        for rule in (
            self.cancelled,
            self.context_too_long,
            self.quota_exceeded,
            self.auth_failed,
            self.model_not_found,
            self.rate_limited,
            self.local_model_load_failed,
            self.timeout,
            self.connection_failed,
            self.endpoint_not_found,
            self.server_error,
            self.invalid_request,
        ):
            info = rule()
            if info is not None:
                return info
        return self.unknown()


def classify_provider_error(
    exc: Exception,
    cfg: SparkConfig,
    *,
    stage: str = "llm",
    live_provider: Any = None,
) -> ProviderErrorInfo:
    """Map a provider exception to a stable code and an actionable suggestion."""
    return _ProviderErrorClassifier(
        exc,
        cfg,
        stage=stage,
        live_provider=live_provider,
    ).classify()


__all__ = ["ProviderErrorInfo", "classify_provider_error", "provider_preflight", "missing_key_error"]
