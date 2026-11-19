"""P1：models.dev 开放模型目录数据源（替代硬编码路由矩阵的元数据层）。

能力：
- ``fetch_catalog()``：拉取 https://models.dev/api.json（232+ 供应商、全模型元数据），
  带本地 TTL 缓存（默认 24h，文件落在 ``$SPARK_HOME/cache/models-dev.json``），
  拉取失败自动回退缓存/内置精简快照——离线环境行为不变。
- ``lookup(provider, model)``：查模型是否存在与价格（input/output，USD/1M tokens）。
- ``estimate_cost(provider, model, in_tokens, out_tokens)``：真实 usage 回填后即可用
  于此计算任务成本（P3 成本台账的数据基础）。

设计约束：纯函数 + 显式缓存路径，可被 routing/CLI/未来成本台账复用；
不 import spark.config（避免循环依赖，调用方传 home）。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CATALOG_URL = "https://models.dev/api.json"
CATALOG_TTL_SECONDS = 24 * 3600
CATALOG_TIMEOUT = 20.0

# 内置精简快照：models.dev 不可达且无缓存时的兜底（覆盖路由矩阵用到的档位）
_FALLBACK_SNAPSHOT: dict[str, Any] = {
    "deepseek": {
        "models": {
            "deepseek-chat": {
                "name": "DeepSeek Chat",
                "cost": {"input": 0.27, "output": 1.10},
                "limit": {"context": 131072, "output": 8192},
            },
            "deepseek-reasoner": {
                "name": "DeepSeek Reasoner",
                "cost": {"input": 0.55, "output": 2.19},
                "limit": {"context": 131072, "output": 65536},
            },
        }
    },
    # Native pricing snapshot (USD / 1M tokens).  It exists so the cost panel
    # can show a number offline; models.dev's live catalog wins when available.
    "anthropic": {
        "models": {
            "claude-sonnet-4-5": {
                "name": "Claude Sonnet 4.5",
                "cost": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
                "limit": {"context": 200000, "output": 8192},
            }
        }
    },
    "openai": {
        "models": {
            "gpt-5": {
                "name": "GPT-5",
                "cost": {"input": 1.25, "output": 10.0, "cache_read": 0.125},
                "limit": {"context": 400000, "output": 16384},
            },
            "gpt-4.1": {
                "name": "GPT-4.1",
                "cost": {"input": 2.50, "output": 10.0, "cache_read": 0.25},
                "limit": {"context": 1000000, "output": 32768},
            },
        }
    },
    "gemini": {
        "models": {
            "gemini-2.5-pro": {
                "name": "Gemini 2.5 Pro",
                "cost": {"input": 1.25, "output": 5.0},
                "limit": {"context": 1000000, "output": 8192},
            }
        }
    },
    "azure": {
        "models": {
            "gpt-4.1": {
                "name": "Azure GPT-4.1",
                "cost": {"input": 2.50, "output": 10.0},
                "limit": {"context": 1000000, "output": 32768},
            }
        }
    },
    "bedrock": {
        "models": {
            "anthropic.claude-3-5-sonnet-20240620-v1:0": {
                "name": "Bedrock Claude 3.5 Sonnet",
                "cost": {"input": 3.0, "output": 15.0, "cache_read": 0.30},
                "limit": {"context": 200000, "output": 8192},
            }
        }
    },
}


def _cache_path(home: Path) -> Path:
    return home / "cache" / "models-dev.json"


def _load_cache(home: Path, max_age: int = CATALOG_TTL_SECONDS) -> dict[str, Any] | None:
    p = _cache_path(home)
    try:
        if not p.exists() or time.time() - p.stat().st_mtime > max_age:
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _save_cache(home: Path, data: dict[str, Any]) -> None:
    try:
        _cache_path(home).parent.mkdir(parents=True, exist_ok=True)
        _cache_path(home).write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        logger.debug("model catalog cache write failed", exc_info=True)


def fetch_catalog(home: Path, *, force: bool = False) -> dict[str, Any]:
    """获取完整目录：内存无 → 缓存（TTL 内）→ 网络 → 过期缓存 → 内置快照。"""
    if not force:
        cached = _load_cache(home)
        if cached is not None:
            return cached
    try:
        resp = httpx.get(CATALOG_URL, timeout=CATALOG_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data:
            _save_cache(home, data)
            return data
    except Exception:
        logger.debug("models.dev fetch failed, falling back", exc_info=True)
    # 过期缓存仍优于快照
    stale = _load_cache(home, max_age=10**9)
    if stale is not None:
        return stale
    return _FALLBACK_SNAPSHOT


_PROVIDER_ALIASES = {
    "gemini": ("google", "google-ai"),
    "bedrock": ("amazon-bedrock", "aws"),
    "azure": ("azure-openai",),
    "openai": ("openai",),
}


def lookup(catalog: dict[str, Any], provider: str, model: str) -> dict[str, Any] | None:
    """查供应商下的模型条目；provider 名或模型名不匹配时返回 None。"""
    candidates = [provider, provider.lower(), *_PROVIDER_ALIASES.get(provider, ())]
    entry = next(
        (catalog[key] for key in candidates if isinstance(catalog.get(key), dict)),
        None,
    )
    if not isinstance(entry, dict):
        return None
    models = entry.get("models") or {}
    hit = models.get(model) or models.get(model.lower())
    return hit if isinstance(hit, dict) else None


def search(catalog: dict[str, Any], query: str, limit: int = 20) -> list[dict[str, Any]]:
    """按模型名/供应商名模糊搜索，返回 [{provider, model, name, cost, context}]。"""
    q = query.lower().strip()
    out: list[dict[str, Any]] = []
    for prov, entry in catalog.items():
        if not isinstance(entry, dict):
            continue
        for model_id, m in (entry.get("models") or {}).items():
            if not isinstance(m, dict):
                continue
            if q and q not in model_id.lower() and q not in prov.lower() and q not in str(m.get("name", "")).lower():
                continue
            out.append(
                {
                    "provider": prov,
                    "model": model_id,
                    "name": m.get("name", model_id),
                    "cost": m.get("cost"),
                    "context": (m.get("limit") or {}).get("context"),
                }
            )
            if len(out) >= limit:
                return out
    return out


def estimate_cost(
    catalog: dict[str, Any],
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """按目录价格估算成本（USD）；无价格数据返回 None。

    Cached input is billed at ``cache_read`` when the catalog provides it;
    otherwise it falls back to the normal input rate (never pretend cache is
    free when pricing is unknown).  ``input_tokens`` is treated as the total
    prompt count, with cached tokens as a subset.
    """
    m = lookup(catalog, provider, model)
    if not m:
        return None
    cost = m.get("cost") or {}
    try:
        in_rate = float(cost.get("input") or 0.0)
        out_rate = float(cost.get("output") or 0.0)
        cache_read_rate = float(cost.get("cache_read") or in_rate)
        cache_write_rate = float(cost.get("cache_write") or in_rate)
    except (TypeError, ValueError):
        return None
    if in_rate == 0 and out_rate == 0:
        return None
    total_input = max(0, int(input_tokens))
    cached = max(0, min(int(cache_read_tokens), total_input))
    uncached = total_input - cached
    return round(
        uncached / 1_000_000 * in_rate
        + cached / 1_000_000 * cache_read_rate
        + max(0, int(cache_write_tokens)) / 1_000_000 * cache_write_rate
        + int(output_tokens) / 1_000_000 * out_rate,
        6,
    )


__all__ = [
    "CATALOG_URL",
    "CATALOG_TTL_SECONDS",
    "fetch_catalog",
    "lookup",
    "search",
    "estimate_cost",
]
