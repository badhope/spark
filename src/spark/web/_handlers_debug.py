"""Debug surface: ``GET /api/debug``.

为什么单独做一个端点，而不是把字段塞进 ``/api/status``：

* status 是**产品态**（会话、模型、上下文用量），它被 UI 高频轮询，
  必须保持小而稳定。调试信息是**运维态**，字段会随排查需要变化，
  混在一起会让 status 的契约反复漂移。
* 调试数据里有些东西不适合默认展示（配置摘要、队列深度、线程名），
  单独端点也方便日后加"仅本地可用"这类门禁。

安全边界：只暴露**结构不是内容**。密钥一律不出现，提示词/消息内容不返回，
路径只给工作目录本身。要做的是"看清楚系统在做什么"，不是把用户数据端出去。
"""

from __future__ import annotations

import os
import platform
import sys
import threading
import time
from typing import Any

from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state


def _thread_summary() -> dict[str, Any]:
    """当前线程概览：只给名字与存活数，不 dump 栈（栈可能含路径与参数值）。"""
    threads = threading.enumerate()
    names: dict[str, int] = {}
    for t in threads:
        names[t.name] = names.get(t.name, 0) + 1
    return {"count": len(threads), "by_name": names}


def _lock_state(state) -> dict[str, Any]:
    """锁状态：判断"卡住了吗"的第一手信息。

    只看 locked 标志有竞态（读完就可能变），但对排查而言"此刻是否被持有"
    已经足够定位方向；真要精确需要 stack dump，那属于下一层工具。
    """
    turn = getattr(state, "turn_lock", None)
    settings = getattr(state, "settings_lock", None)
    return {
        "turn_locked": bool(turn.locked()) if turn is not None else None,
        "settings_locked": bool(settings.locked()) if settings is not None else None,
    }


def _runtime_summary(state) -> dict[str, Any]:
    loop = getattr(state, "loop", None)
    bridge = getattr(state, "mcp_bridge", None)
    return {
        "loop_ready": loop is not None,
        "loop_session": getattr(loop, "session_id", None) if loop else None,
        "provider_class": type(getattr(loop, "provider", None)).__name__ if loop else None,
        "registry_tools": len(loop.registry.schemas()) if loop else 0,
        "mcp_servers": sorted(bridge.servers) if bridge else [],
        "mcp_errors": list(bridge.errors)[:10] if bridge else [],
        "sandbox_mode": getattr(state.sandbox, "sandbox_mode", None),
        # 额外根目录只回数量与是否可用，不回完整路径（可能含用户名）。
        "extra_roots_count": len(getattr(state.sandbox, "extra_roots", []) or []),
    }


def _scheduler_summary(state) -> dict[str, Any]:
    sched = getattr(state, "automations", None)
    thread = getattr(sched, "_thread", None) if sched else None
    return {
        "running": bool(thread and thread.is_alive()),
        "tick_sec": getattr(sched, "_tick", None),
    }


def _store_summary(state) -> dict[str, Any]:
    store = getattr(state, "store", None)
    if store is None:
        return {"available": False}
    try:
        sessions = store.list_sessions()
        return {
            "available": True,
            "sessions": len(sessions),
            "checkpoints_current": len(store.list_checkpoints(state.session_id)),
            "automations": len(store.list_automations()),
        }
    except Exception as exc:  # aqg: top-level boundary
        # 诊断端点自己不能因为某块数据取不到就整体 500。
        return {"available": False, "error": type(exc).__name__}


def _env_summary() -> dict[str, Any]:
    """进程环境摘要。刻意不含任何环境变量的值。"""
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(terse=True),
        "cwd": os.getcwd(),
        "pid": os.getpid(),
        "spark_home": os.environ.get("SPARK_HOME", ""),
        # 只报"是否设置了"，不报值——密钥常常就藏在这些变量里。
        "env_present": sorted(
            name for name in ("SPARK_API_KEY", "SPARK_BASE_URL", "SPARK_MODEL")
            if os.environ.get(name)
        ),
    }


def handle_debug(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    started = time.monotonic()
    payload: dict[str, Any] = {
        "ok": True,
        "collected_ms": 0,
        "env": _env_summary(),
        "runtime": _runtime_summary(state),
        "locks": _lock_state(state),
        "threads": _thread_summary(),
        "scheduler": _scheduler_summary(state),
        "store": _store_summary(state),
        "busy": bool(getattr(state, "busy", False)),
    }
    # 配置摘要：只给"哪些能力开着"，不给任何密钥或路径内容。
    cfg = getattr(state, "cfg", None)
    if cfg is not None:
        payload["features"] = {
            "auto_review": cfg.agent.auto_review,
            "network_access": cfg.agent.network_access,
            "format_on_write": cfg.agent.format_on_write,
            "extra_dirs": len(cfg.agent.extra_dirs),
            "protected_paths": len(cfg.agent.protected_paths),
            "permission_rules": len(cfg.permission_rules),
            "approval_profiles": len(cfg.approval_profiles),
            "model_profiles": len(cfg.model_profiles),
            "mcp_servers": len(cfg.mcp_servers),
            "hooks": len(cfg.hooks),
            "embedding_enabled": cfg.memory.embedding_enabled,
            "knowledge_enabled": cfg.context.knowledge_enabled,
        }
    payload["collected_ms"] = round((time.monotonic() - started) * 1000, 2)
    handler._json(200, payload)


def handle_debug_client_log(handler: HandlerBase, body: dict) -> None:
    """接收前端上报的客户端错误（``POST /api/debug/client_log``）。

    为什么需要它：前端异常（渲染崩溃、懒加载 chunk 404、Promise 未捕获）
    只出现在浏览器里，服务端日志完全看不到。没有这条通道，"界面白了"
    就只能靠用户口述。

    安全与噪声控制：只收结构化字段，限长，永远只写日志不落盘；
    绝不接收整段 DOM 或用户输入内容。
    """
    import logging

    log = logging.getLogger("spark.web.client")
    kind = str(body.get("kind") or "error")[:32]
    message = str(body.get("message") or "")[:1000]
    stack = str(body.get("stack") or "")[:4000]
    where = str(body.get("where") or "")[:200]
    log.warning("client %s at %s: %s\n%s", kind, where, message, stack)
    handler._json(200, {"ok": True})
