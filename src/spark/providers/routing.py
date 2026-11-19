"""P1-10 国产 agentic 模型路由矩阵。

本模块是「文档 + 决策函数」的纯路由层：它**不直连任何海外模型平台**，
所有国产模型都复用现有 ``openai_compat`` / ``ollama`` 两条通道（见
``spark.providers.factory`` 与 ``spark.providers.openai_compat``）。

设计要点
--------
* **三档矩阵**（依据调研报告第 1/10 章）：

  ============  ============  ====================================
  档位           定位          代表模型
  ============  ============  ====================================
  ``capability`` 能力档       DeepSeek-V3.2(thinking) / Qwen3-Coder /
                               Kimi K2 / GLM-4.5
  ``cost``       成本档       DeepSeek-V3.2(非思考) / 豆包(火山方舟)
  ``offline``    离线档       Ollama 本地 Qwen3-Coder GGUF
                               （native tool calling）
  ============  ============  ====================================

* 路由决策只改写 :class:`spark.config.ProviderConfig` 的字段，随后调用
  现有 :func:`spark.providers.factory.create_provider` 即可拿到对应 provider。
  因此 mock / openai_compat / ollama 三条既有路径**完全不受影响**。

* 可用性降级：若所选档位对应的 API key / 本地端点不可用，按
  ``capability -> cost -> offline`` 链降级，保证「任何环境都能选出一档」。

* 所有接入细节（base_url / model 名 / thinking 参数）以厂商最新文档为准，
  本文件中的默认值为 2025 年下半年可核实的配置。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from spark.config import ProviderName, SparkConfig

TierId = Literal[
    "deepseek_thinking",
    "qwen_coder",
    "kimi_k2",
    "glm_45",
    "deepseek_chat",
    "doubao",
    "ollama_local",
]
TierClass = Literal["capability", "cost", "offline"]
TaskType = Literal[
    "code", "debug", "chat", "offline", "plan", "edit", "review", "compact", "title"
]
Budget = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class ModelEntry:
    """一个可路由的模型档位。"""

    id: TierId
    cls: TierClass
    label: str
    provider: ProviderName
    base_url: str
    model: str
    api_key_env: str = ""
    thinking: bool = False
    note: str = ""


# ---------------------------------------------------------------------------
# 档位注册表（默认值；接入细节以厂商文档为准）
# ---------------------------------------------------------------------------

MODEL_REGISTRY: dict[TierId, ModelEntry] = {
    # ---- 能力档 ----------------------------------------------------------
    "deepseek_thinking": ModelEntry(
        id="deepseek_thinking",
        cls="capability",
        label="DeepSeek-V3.2 (thinking / deepseek-reasoner)",
        provider="openai_compat",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-reasoner",
        api_key_env="DEEPSEEK_API_KEY",
        thinking=True,
        note="thinking 融入工具调用；effort 链路已在 openai_compat.py 实现。",
    ),
    "qwen_coder": ModelEntry(
        id="qwen_coder",
        cls="capability",
        label="Qwen3-Coder (阿里云百炼)",
        provider="openai_compat",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3-coder-plus",
        api_key_env="DASHSCOPE_API_KEY",
        thinking=True,
        note="agentic coder；enable_thinking 映射到 effort。model 名以百炼文档为准。",
    ),
    "kimi_k2": ModelEntry(
        id="kimi_k2",
        cls="capability",
        label="Kimi K2 (月之暗面开放平台)",
        provider="openai_compat",
        base_url="https://api.moonshot.cn/v1",
        model="kimi-k2-0905-preview",
        api_key_env="MOONSHOT_API_KEY",
        thinking=False,
        note="长上下文 + 工具调用强项；model 名以 Moonshot 文档为准。",
    ),
    "glm_45": ModelEntry(
        id="glm_45",
        cls="capability",
        label="GLM-4.5 (智谱 bigmodel.cn)",
        provider="openai_compat",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        model="glm-4.5",
        api_key_env="ZHIPUAI_API_KEY",
        thinking=True,
        note="reasoning/coding/agentic 统一档；model 名以智谱文档为准。",
    ),
    # ---- 成本档 ----------------------------------------------------------
    "deepseek_chat": ModelEntry(
        id="deepseek_chat",
        cls="cost",
        label="DeepSeek-V3.2 (非思考 / deepseek-chat)",
        provider="openai_compat",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
        thinking=False,
        note="日常问答/补全的默认便宜档；磁盘 prompt 缓存自动命中。",
    ),
    "doubao": ModelEntry(
        id="doubao",
        cls="cost",
        label="豆包 / Doubao-Seed-Code (火山引擎方舟)",
        provider="openai_compat",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="doubao-seed-code-preview",
        api_key_env="ARK_API_KEY",
        thinking=False,
        note="方舟 endpoint 通常填 ep-xxx 接入点 ID；model 以火山文档为准。",
    ),
    # ---- 离线档 ----------------------------------------------------------
    "ollama_local": ModelEntry(
        id="ollama_local",
        cls="offline",
        label="Ollama 本地 Qwen3-Coder GGUF",
        provider="ollama",
        base_url="http://127.0.0.1:11434/v1",
        model="qwen3-coder:latest",
        api_key_env="",
        thinking=False,
        note="native tool calling；ollama pull qwen3-coder 后即可用。",
    ),
}

# 每一档位类的首选顺序（按偏好排列）
_CLASS_PREFERRED: dict[TierClass, list[TierId]] = {
    "capability": ["deepseek_thinking", "qwen_coder", "kimi_k2", "glm_45"],
    "cost": ["deepseek_chat", "doubao"],
    "offline": ["ollama_local"],
}

# 降级链：能力 -> 成本 -> 离线
_FALLBACK_CHAIN: list[TierClass] = ["capability", "cost", "offline"]


@dataclass
class RouteDecision:
    """路由决策结果。"""

    tier: ModelEntry
    task_type: TaskType
    budget: Budget
    offline: bool
    degraded_from: list[str] = field(default_factory=list)
    reason: str = ""


# ---------------------------------------------------------------------------
# 可用性探测
# ---------------------------------------------------------------------------


def is_tier_available(tier: ModelEntry, environ: dict[str, str] | None = None) -> bool:
    """判断某档位当前是否「可用」。

    * ``ollama`` 离线档：不查 key（本地无需 key），由调用方在真正连接时探测。
    * ``openai_compat`` 在线档：要求对应 ``api_key_env`` 环境变量存在。
      （mock 注入场景由测试直接覆盖决策结果，不走此函数。）
    """
    env = os.environ if environ is None else environ
    if tier.provider == "ollama":
        return True
    key_env = tier.api_key_env
    if not key_env:
        # 无 key 要求的在线端点：视为可用（例如已在 config 内联 key）
        return True
    return bool(env.get(key_env))


def _class_for_task(task_type: TaskType, budget: Budget, offline: bool) -> TierClass:
    """按任务类型 + 预算推导目标档位类。"""
    if offline:
        return "offline"
    if task_type in ("code", "debug", "plan", "review", "edit"):
        # 复杂代码/调试/规划/审查：能力档（debug 尤其需要 thinking）
        return "capability"
    if task_type in ("compact", "title"):
        return "cost"
    if budget == "low":
        return "cost"
    # chat 类默认成本档；high 预算的简单问答仍走能力档
    return "capability" if budget == "high" else "cost"


def decide_tier(
    task_type: TaskType = "chat",
    budget: Budget = "medium",
    offline: bool = False,
    *,
    available: set[str] | None = None,
    preferred: TierId | None = None,
    environ: dict[str, str] | None = None,
) -> RouteDecision:
    """路由主决策函数。

    参数
    ----
    task_type:
        ``code`` 多文件编程 / ``debug`` 复杂调试 / ``chat`` 日常问答 /
        ``offline`` 强制离线。
    budget:
        ``low`` 省钱 / ``medium`` 平衡 / ``high`` 不计成本。
    offline:
        强制走离线档（无网络 / 隐私场景）。
    available:
        白名单：仅这些 tier id 可用；为 ``None`` 时现场探测 key。
    preferred:
        用户显式指定的首选 tier id（仍会做可用性降级）。
    environ:
        可注入环境变量（测试用）。
    """
    target_cls = _class_for_task(task_type, budget, offline)

    def usable(t: ModelEntry) -> bool:
        if available is not None:
            return t.id in available
        return is_tier_available(t, environ)

    degraded: list[str] = []

    # 1) 用户指定首选档
    if preferred is not None and preferred in MODEL_REGISTRY:
        entry = MODEL_REGISTRY[preferred]
        if usable(entry):
            return RouteDecision(
                tier=entry,
                task_type=task_type,
                budget=budget,
                offline=offline,
                reason="user-preferred",
            )
        degraded.append(preferred)

    # 2) 目标档位类内按偏好顺序挑第一个可用
    for tier_id in _CLASS_PREFERRED[target_cls]:
        entry = MODEL_REGISTRY[tier_id]
        if usable(entry):
            return RouteDecision(
                tier=entry,
                task_type=task_type,
                budget=budget,
                offline=offline,
                degraded_from=degraded,
                reason=f"matched:{target_cls}",
            )
        degraded.append(tier_id)

    # 3) 跨类降级链（capability -> cost -> offline）
    for cls in _FALLBACK_CHAIN:
        if cls == target_cls:
            continue
        for tier_id in _CLASS_PREFERRED[cls]:
            entry = MODEL_REGISTRY[tier_id]
            if usable(entry):
                return RouteDecision(
                    tier=entry,
                    task_type=task_type,
                    budget=budget,
                    offline=offline,
                    degraded_from=degraded,
                    reason=f"fallback:{cls}",
                )
            degraded.append(tier_id)

    # 4) 兜底：离线档永远返回（本地无 key 也能跑空模型/占位）
    entry = MODEL_REGISTRY["ollama_local"]
    return RouteDecision(
        tier=entry,
        task_type=task_type,
        budget=budget,
        offline=True,
        degraded_from=degraded,
        reason="fallback:offline-last-resort",
    )


def describe_decision(decision: RouteDecision) -> dict[str, str | bool | list[str]]:
    """Machine-readable decision payload for CLI/Web; no secrets included."""
    return {
        "tier": decision.tier.id,
        "class": decision.tier.cls,
        "label": decision.tier.label,
        "model": decision.tier.model,
        "provider": decision.tier.provider,
        "task_type": decision.task_type,
        "budget": decision.budget,
        "offline": decision.offline,
        "degraded_from": list(decision.degraded_from),
        "reason": decision.reason,
        "base_url": decision.tier.base_url,
    }


def route_for_cli(
    cfg: SparkConfig,
    *,
    tier_class: str = "auto",
    task_type: TaskType = "code",
    budget: Budget = "medium",
) -> RouteDecision:
    """Resolve a ``--tier capability|cost|offline`` override.

    Availability is derived from env plus an inline key whose base_url matches
    the tier, so a user who pasted a key into config is not forced to export
    the same key as an environment variable.
    """
    env = dict(os.environ)
    available: set[str] = set()
    for tier_id, entry in MODEL_REGISTRY.items():
        if entry.provider == "ollama":
            available.add(tier_id)
        elif entry.api_key_env and env.get(entry.api_key_env):
            available.add(tier_id)
        elif cfg.provider.api_key and cfg.provider.base_url.rstrip("/") == entry.base_url.rstrip("/"):
            available.add(tier_id)
    offline = tier_class == "offline"
    if tier_class in ("capability", "cost"):
        # Explicit class: use a task type that maps to that class, without
        # accidentally upgrading a cost request through high budget.
        target_task: TaskType = "code" if tier_class == "capability" else "compact"
        target_budget: Budget = "medium"
    else:
        target_task = task_type
        target_budget = budget
    return decide_tier(
        task_type=target_task,
        budget=target_budget,
        offline=offline,
        available=available,
    )


# ---------------------------------------------------------------------------
# 应用到 SparkConfig（与现有 create_provider 衔接）
# ---------------------------------------------------------------------------


def apply_routed_provider(cfg: SparkConfig, decision: RouteDecision) -> SparkConfig:
    """把路由结果写回 ``cfg.provider``，随后即可走原有 ``create_provider``。

    只改写 provider 连接字段，不触碰 agent/context/memory 等其它配置段。
    """
    entry = decision.tier
    cfg.provider.name = entry.provider
    cfg.provider.base_url = entry.base_url
    cfg.provider.model = entry.model
    if entry.api_key_env:
        cfg.provider.api_key_env = entry.api_key_env
    return cfg


def route_and_build(
    cfg: SparkConfig,
    *,
    task_type: TaskType = "chat",
    budget: Budget = "medium",
    offline: bool = False,
    preferred: TierId | None = None,
):
    """一步完成「决策 -> 写回 cfg -> 构造 provider」。

    返回 ``(provider, decision)``。provider 由现有 ``create_provider`` 产出，
    因此 mock / openai_compat / ollama 三条既有路径行为不变。
    """
    from spark.providers.factory import create_provider

    decision = decide_tier(
        task_type=task_type,
        budget=budget,
        offline=offline,
        preferred=preferred,
    )
    apply_routed_provider(cfg, decision)
    return create_provider(cfg), decision
