"""设置保存（/api/settings）与模型探活（/api/test）。

从单文件 ``_handlers.py`` 按域拆出，行为不变；导入路径经
``spark.web._handlers`` facade 保持稳定。
"""

from __future__ import annotations

import asyncio as _asyncio
import logging
import re as _re
import threading as _threading
from typing import Any, Literal, cast

from pydantic import TypeAdapter, ValidationError

from spark.asyncutil import run_coroutine_sync
from spark.config import (
    ApprovalMode,
    HookConfig,
    ProviderName,
    SandboxMode,
    SparkConfig,
    require_api_key,
)
from spark.errors import ConfigError, SparkError
from spark.providers.probe import probe_provider
from spark.sandbox import WorkdirSandbox, safe_bare_filename
from spark.web._handlers_common import _save_config
from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state
from spark.web.utils import (
    HOOK_EVENTS,
    MCP_REDACTED_HEADER,
    _probe_target_allowed,
    parse_mcp_servers,
)

_ENV_NAME_RE = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SPARK_CONFIG_ADAPTER: TypeAdapter[SparkConfig] = TypeAdapter(SparkConfig)
# 只读配置段：读写路径需要独立的权限模型（deny 跨层优先、档案激活会改审批
# 语义），半成品写路径比不支持更危险。这里显式拒绝而不是静默忽略——静默
# 忽略会让调用方以为保存成功。设计见 docs/API.md。
_READ_ONLY_SETTINGS_SECTIONS = (
    "permission_rules",
    "permission_scopes",
    "approval_profiles",
    "active_approval_profile_id",
)


def _validation_error_message(exc: ValidationError) -> str:
    """把 pydantic 错误压成一条可展示文案（字段名 + 原因）。"""
    errors = exc.errors()
    if not errors:
        return "invalid value"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ())) or "config"
    return f"{location}: {first.get('msg', 'invalid value')}"


def _resolve_provider_fields(
    state,
    provider_name: str,
    base_url: str,
    model: str,
    api_key: str,
    api_key_env: str | None = None,
) -> tuple[str, str | None]:
    """校验 provider 字段，返回**要持久化的** api_key 与错误。

    两条规则分开：

    * 可用性校验——openai_compat 需要能拿到凭据（显式 key、同端点存储 key、
      或绑定且存在的环境变量）。切换端点时存储 key 与旧 ``api_key_env`` 都只
      属于旧端点，因此必须显式提供 ``api_key`` 或 ``api_key_env``，否则把凭据
      发给新主机。
    * 持久化——只写用户真正提供的东西。环境变量里的值仅用于校验，绝不落盘
      成明文 ``api_key``（否则 ``spark config show`` 之外的文件里就多了一份
      密钥副本）。

    ``mock`` / ``ollama`` 不需要凭据，因此不做可用性校验——此前 ollama 分支
    在"环境里恰好有 key"时报 400，恰好挡住了最不需要 key 的场景。
    """
    if provider_name not in {"openai_compat", "ollama", "mock"}:
        return api_key, "provider must be openai_compat, ollama or mock"
    if not base_url or not model:
        return api_key, "base_url and model are required"
    same_target = (
        provider_name == state.cfg.provider.name
        and base_url == state.cfg.provider.base_url
    )
    if provider_name != "openai_compat":
        # 同端点保留已存密钥（供 Ollama 兼容网关使用），跨端点清空。
        return (state.cfg.provider.api_key or "") if same_target else "", None
    if not same_target and not api_key and not api_key_env:
        return api_key, (
            "api_key or api_key_env is required when changing provider or endpoint"
        )
    available = api_key or (state.cfg.provider.api_key if same_target else "") or ""
    if not available and not api_key_env:
        try:
            available = require_api_key(state.cfg) or ""
        except ConfigError:
            return api_key, "api_key is required"
    return api_key or (state.cfg.provider.api_key or "" if same_target else ""), None


def _agent_validation_error(approval: str, sandbox_mode: str, effort: str) -> str | None:
    if approval not in {"suggest", "auto-edit", "full-auto", "plan-only", "approve-plan", "manual"}:
        return "approval must be suggest, auto-edit, full-auto, plan-only, approve-plan or manual"
    if sandbox_mode not in {
        "sandbox-only",
        "workspace",
        "full-access",
        "unrestricted",
    }:
        return "sandbox_mode must be sandbox-only, workspace, full-access or unrestricted"
    if effort not in {"low", "medium", "high"}:
        return "effort must be low, medium or high"
    return None


def _parse_agent_numbers(agent_in: dict, state) -> dict[str, Any] | str:
    """解析 agent 数值字段；失败返回错误字符串。

    只做类型转换，范围交给 :func:`_validate_candidate`（与 ``config.py`` 的
    ``ge``/``le`` 同源）。此前在这里静默钳制下限，越界值被"修正"后 200 落盘，
    下次 ``load_config`` 直接抛 ConfigError 并自愈重置——用户看到的是设置丢失。
    """
    try:
        workdir_only = bool(
            agent_in.get("workdir_only", state.cfg.agent.workdir_only)
        )
        shell_timeout_sec = int(
            agent_in.get("shell_timeout_sec", state.cfg.agent.shell_timeout_sec)
        )
        max_tool_rounds = int(
            agent_in.get("max_tool_rounds", state.cfg.agent.max_tool_rounds)
        )
        max_output_chars = int(
            agent_in.get("max_output_chars", state.cfg.agent.max_output_chars)
        )
        repeat_in = int(
            agent_in.get("max_repeat_calls", state.cfg.agent.max_repeat_calls)
        )
        token_in = int(
            agent_in.get("max_turn_tokens", state.cfg.agent.max_turn_tokens)
        )
    except (TypeError, ValueError):
        return "invalid agent numbers"
    return {
        "workdir_only": workdir_only,
        "shell_timeout_sec": shell_timeout_sec,
        "max_tool_rounds": max_tool_rounds,
        "max_output_chars": max_output_chars,
        "repeat_in": repeat_in,
        "token_in": token_in,
    }


def _context_updates(context_in: Any) -> tuple[dict[str, Any], str | None]:
    """Project 页上下文设置：返回 (updates, error)。非 dict 输入视为不更新。

    范围校验同样下沉到 ``_validate_candidate``：这里只做类型转换，避免两套
    边界各说各话。
    """
    updates: dict[str, Any] = {}
    if not isinstance(context_in, dict):
        return updates, None
    try:
        for key, convert in (
            ("compact_threshold", float),
            ("max_context_tokens", int),
            ("keep_recent_messages", int),
            ("history_budget_chars", int),
            ("max_fragment_chars", int),
            ("repo_map_max_chars", int),
            ("knowledge_top_k", int),
            ("knowledge_max_chars", int),
        ):
            if key in context_in:
                updates[key] = convert(context_in[key])
        if "repo_map_enabled" in context_in:
            updates["repo_map_enabled"] = bool(context_in["repo_map_enabled"])
        if "knowledge_enabled" in context_in:
            updates["knowledge_enabled"] = bool(context_in["knowledge_enabled"])
        if "agents_md" in context_in:
            name = str(context_in["agents_md"] or "").strip()
            if not name:
                raise ValueError("agents_md is required")
            if safe_bare_filename(name) is None:
                raise ValueError("agents_md must be a bare filename inside the workdir")
            updates["agents_md"] = name
    except (TypeError, ValueError) as exc:
        return {}, f"invalid context setting: {exc}"
    return updates, None


def _memory_updates(memory_in: Any) -> tuple[dict[str, Any], str | None]:
    """记忆 tab 配置：返回 (updates, error)。非 dict 输入视为不更新。"""
    updates: dict[str, Any] = {}
    if not isinstance(memory_in, dict):
        return updates, None
    try:
        if "enabled" in memory_in:
            updates["enabled"] = bool(memory_in["enabled"])
        for key in ("top_k", "capacity"):
            if key in memory_in:
                updates[key] = int(memory_in[key])
        if "embedding_model" in memory_in:
            name = str(memory_in["embedding_model"] or "").strip()
            if not name:
                raise ValueError("embedding_model is required")
            updates["embedding_model"] = name
        if "auto_extract" in memory_in:
            updates["auto_extract"] = bool(memory_in["auto_extract"])
        if "embedding_enabled" in memory_in:
            updates["embedding_enabled"] = bool(memory_in["embedding_enabled"])
        if "embedding_base_url" in memory_in:
            updates["embedding_base_url"] = str(
                memory_in["embedding_base_url"] or ""
            ).strip()
        if "rerank_model" in memory_in:
            updates["rerank_model"] = str(memory_in["rerank_model"] or "").strip()
        # 空串 = 保留已存密钥（与 provider.api_key 同一契约），不把密钥抹掉。
        explicit_key = str(memory_in.get("embedding_api_key") or "").strip()
        if explicit_key:
            updates["embedding_api_key"] = explicit_key
    except (TypeError, ValueError) as exc:
        return {}, f"invalid memory setting: {exc}"
    return updates, None


_TEST_PROBE_SEMAPHORE = _threading.BoundedSemaphore(2)
_MAX_TEST_BASE_URL_CHARS = 2048
_MAX_TEST_MODEL_CHARS = 256
_MAX_TEST_API_KEY_CHARS = 4096


def _restore_redacted_mcp_headers(state, mcp_in: list) -> list:
    existing: dict[str, tuple[str, dict[str, str]]] = {}
    for server in state.cfg.mcp_servers:
        if server.http is not None:
            existing[server.name] = (
                server.http.url.strip(),
                {name.lower(): value for name, value in server.http.headers.items()},
            )
    restored: list = []
    for item in mcp_in:
        if not isinstance(item, dict):
            restored.append(item)
            continue
        copy = dict(item)
        previous_server = next((server for server in state.cfg.mcp_servers if server.name == str(item.get("name") or "")), None)
        env = item.get("env")
        transport = str(item.get("transport") or "stdio").strip() or "stdio"
        http_data: dict = item["http"] if isinstance(item.get("http"), dict) else {}
        same_destination = previous_server is not None and transport == previous_server.transport and (
            (transport == "stdio" and str(item.get("command") or "").strip() == previous_server.command and item.get("args", []) == previous_server.args)
            or (transport != "stdio" and previous_server.http is not None and str(http_data.get("url") or "").strip() == previous_server.http.url.strip())
        )
        if item.get("env_replace") is not True and previous_server is not None:
            if not same_destination and previous_server.env:
                raise ValueError("MCP env requires explicit replacement when changing server")
            env = {**previous_server.env, **(env if isinstance(env, dict) else {})}
        if isinstance(env, dict):
            restored_env = {}
            for name, value in env.items():
                if value == MCP_REDACTED_HEADER:
                    if not same_destination or previous_server is None or name not in previous_server.env:
                        raise ValueError("redacted MCP env cannot be restored for changed server")
                    restored_env[name] = previous_server.env[name]
                else:
                    restored_env[name] = value
            copy["env"] = restored_env
        http = item.get("http")
        if isinstance(http, dict):
            http_copy = dict(http)
            headers = http.get("headers")
            if isinstance(headers, dict):
                previous_url, previous = existing.get(
                    str(item.get("name") or ""), ("", {})
                )
                current_url = str(http.get("url") or "").strip()
                can_restore = current_url == previous_url
                http_copy["headers"] = {
                    str(name): (
                        previous[str(name).lower()]
                        if can_restore
                        and value == MCP_REDACTED_HEADER
                        and str(name).lower() in previous
                        else str(value)
                    )
                    for name, value in headers.items()
                    if value != MCP_REDACTED_HEADER
                    or (can_restore and str(name).lower() in previous)
                }
            copy["http"] = http_copy
        restored.append(copy)
    return restored


def _parse_mcp_servers_for_state(state, mcp_in: Any) -> tuple[list | None, str | None]:
    if not isinstance(mcp_in, list):
        return None, None
    try:
        return parse_mcp_servers(_restore_redacted_mcp_headers(state, mcp_in)), None
    except ValueError as exc:
        return None, str(exc)


def _apply_mcp_servers(state, mcp_in: Any) -> tuple[list[str], str | None]:
    """更新 MCP servers 并重启；返回 (mcp_errors, fatal_error)。非 list 输入不更新。"""
    servers, error = _parse_mcp_servers_for_state(state, mcp_in)
    if error or servers is None:
        return [], error
    state.cfg.mcp_servers = servers
    try:
        return state.boot_mcp(), None
    except Exception as exc:  # aqg: top-level boundary
        return [f"MCP boot failed: {exc}"], None


def _parse_hooks(
    hooks_in: Any, existing: list[HookConfig] | None = None
) -> tuple[list[HookConfig] | None, str | None]:
    """校验 hooks 列表；返回 (parsed, error)。非 list 输入返回 (None, None) 表示不更新。

    ``on_deny`` 缺省时沿用同名同事件的既有 hook（UI 未必回传该字段），否则
    任何一次 Web 保存都会把用户配置的 ``warn`` 悄悄改回 ``block``。
    """
    if not isinstance(hooks_in, list):
        return None, None
    previous: dict[tuple[str, str], str] = {
        (hook.event, hook.command): hook.on_deny for hook in (existing or [])
    }
    parsed_hooks: list[HookConfig] = []
    for item in hooks_in:
        if not isinstance(item, dict):
            return None, "invalid hook entry"
        event_name = str(item.get("event") or "").strip()
        command = str(item.get("command") or "").strip()
        if event_name not in HOOK_EVENTS or not command:
            return None, (
                f"invalid hook: event must be one of "
                f"{', '.join(HOOK_EVENTS)} and command is required"
            )
        try:
            timeout_sec = max(1, int(item.get("timeout_sec") or 15))
        except (TypeError, ValueError):
            return None, "invalid hook timeout_sec"
        raw_args = item.get("args") or []
        if not isinstance(raw_args, list):
            return None, "invalid hook args"
        if "on_deny" in item and item["on_deny"] not in (None, ""):
            on_deny = str(item["on_deny"]).strip()
            if on_deny not in ("warn", "block"):
                return None, "invalid hook on_deny: must be warn or block"
        else:
            on_deny = previous.get((event_name, command), "block")
        parsed_hooks.append(
            HookConfig(
                event=event_name,
                command=command,
                args=[str(part) for part in raw_args],
                name=str(item.get("name") or "")[:120],
                timeout_sec=timeout_sec,
                on_deny=cast(Literal["warn", "block"], on_deny),
            )
        )
    return parsed_hooks, None


def _extract_request_fields(state, body: dict) -> dict[str, Any]:
    """从请求体提取本次保存的全部标量字段（缺省回落当前配置）。"""
    provider_raw = body.get("provider")
    provider_in: dict = provider_raw if isinstance(provider_raw, dict) else {}
    memory_raw = body.get("memory")
    agent_raw = body.get("agent")
    agent_in: dict = agent_raw if isinstance(agent_raw, dict) else {}
    replay_in = body.get("replay")
    updater_in = body.get("updater")
    return {
        "provider_in": provider_in,
        "agent_in": agent_in,
        "base_url": str(
            provider_in.get("base_url")
            or body.get("base_url")
            or state.cfg.provider.base_url
        ).strip(),
        "model": str(
            provider_in.get("model")
            or body.get("model")
            or state.cfg.provider.model
        ).strip(),
        "api_key": str(
            provider_in.get("api_key") or body.get("api_key") or ""
        ).strip(),
        "provider_name": str(
            provider_in.get("name") or state.cfg.provider.name
        ).strip(),
        "api_key_env": (
            str(provider_in["api_key_env"]).strip()
            if "api_key_env" in provider_in
            else None
        ),
        "approval": str(
            agent_in.get("approval") or state.cfg.agent.approval
        ).strip(),
        "sandbox_mode": str(
            agent_in.get("sandbox_mode") or state.cfg.agent.sandbox_mode
        ).strip(),
        "effort": str(
            agent_in.get("effort") or state.cfg.agent.effort
        ).strip(),
        "provider_resilience": (
            {
                "api_keys": provider_in.get("api_keys"),
                "fallback_models": provider_in.get("fallback_models"),
                "small_model": provider_in.get("small_model"),
            }
            if isinstance(provider_raw, dict)
            else {}
        ),
        "memory_in": memory_raw if isinstance(memory_raw, dict) else {},
        "replay_in": replay_in if isinstance(replay_in, dict) else {},
        "updater_in": updater_in if isinstance(updater_in, dict) else {},
    }


def _validate_candidate(cfg: SparkConfig) -> str | None:
    """用 ``config.py`` 的模型规则整体校验候选配置（单一事实来源）。

    设置端点此前每个字段各写一套边界，和 pydantic 的 ``ge``/``le`` 漂移后越界值
    会 200 落盘；下一次 ``load_config`` 抛 ConfigError 并走自愈重置。这里复用
    同一份模型定义，越界即返回错误文案，调用方回滚并返回 400。
    """
    try:
        _SPARK_CONFIG_ADAPTER.validate_python(cfg.model_dump())
    except ValidationError as exc:
        return f"invalid settings: {_validation_error_message(exc)}"
    return None


def _apply_validated_settings(
    state,
    fields: dict[str, Any],
    numbers: dict[str, Any],
    display_flags: dict[str, bool],
    protected_paths_update: list[str] | None,
    system_prompt: str,
    context_updates: dict[str, Any],
    memory_updates: dict[str, Any],
    parsed_hooks: list[HookConfig] | None,
    parsed_mcp: list | None = None,
) -> None:
    """全部校验通过后统一提交：任何失败都不会走到这里，不留下半改状态。"""
    cfg = state.cfg
    same_target = (
        fields["provider_name"] == cfg.provider.name
        and fields["base_url"] == cfg.provider.base_url
    )
    old_embedding_url = cfg.memory.embedding_base_url or cfg.provider.base_url
    new_embedding_url = memory_updates.get("embedding_base_url", cfg.memory.embedding_base_url) or fields["base_url"]
    if old_embedding_url.rstrip("/") != new_embedding_url.rstrip("/") and "embedding_api_key" not in memory_updates:
        memory_updates["embedding_api_key"] = ""
    cfg.provider.name = cast(ProviderName, fields["provider_name"])
    cfg.provider.base_url = fields["base_url"]
    cfg.provider.model = fields["model"]
    cfg.provider.api_key = fields["api_key"] or None
    # 环境变量绑定与档案激活（``activate_model_profile``）同一语义：显式提交即
    # 覆盖；切换端点而未提交则清空——旧端点绑定的变量名对新主机没有意义，
    # 留着会让 ``env_api_key`` 把旧端点的环境凭据发给新地址。
    if fields.get("api_key_env") is not None:
        cfg.provider.api_key_env = fields["api_key_env"]
    elif not same_target:
        cfg.provider.api_key_env = ""
    # 供应商韧性（凭据池 / 回退链 / 小模型分工）：请求携带即覆盖
    resilience = fields.get("provider_resilience") or {}
    if "api_keys" in resilience and resilience["api_keys"] is not None:
        raw_keys = resilience["api_keys"]
        cfg.provider.api_keys = (
            [str(x).strip() for x in raw_keys if str(x).strip()]
            if isinstance(raw_keys, list)
            else []
        )
    if "fallback_models" in resilience and resilience["fallback_models"] is not None:
        raw_fb = resilience["fallback_models"]
        cfg.provider.fallback_models = (
            [str(x).strip() for x in raw_fb if str(x).strip()]
            if isinstance(raw_fb, list)
            else []
        )
    if resilience.get("small_model") is not None:
        cfg.provider.small_model = str(resilience["small_model"] or "").strip()
    cfg.agent.approval = cast(ApprovalMode, fields["approval"])
    cfg.agent.sandbox_mode = cast(SandboxMode, fields["sandbox_mode"])
    cfg.agent.effort = cast(Literal["low", "medium", "high"], fields["effort"])
    cfg.agent.workdir_only = numbers["workdir_only"]
    cfg.agent.shell_timeout_sec = numbers["shell_timeout_sec"]
    cfg.agent.max_tool_rounds = numbers["max_tool_rounds"]
    cfg.agent.max_output_chars = numbers["max_output_chars"]
    cfg.agent.max_repeat_calls = numbers["repeat_in"]
    cfg.agent.max_turn_tokens = numbers["token_in"]
    cfg.agent.system_prompt = system_prompt
    for key, value in display_flags.items():
        setattr(cfg.agent, key, value)
    if protected_paths_update is not None:
        cfg.agent.protected_paths = protected_paths_update
    for key, value in context_updates.items():
        # Field types were validated above; setattr keeps this version-tolerant
        # if a new context field is added to the model.
        setattr(cfg.context, key, value)  # type: ignore[arg-type]
    for key, value in memory_updates.items():
        setattr(cfg.memory, key, value)  # type: ignore[arg-type]
    replay_in = fields.get("replay_in") or {}
    if "enabled" in replay_in:
        cfg.replay.enabled = bool(replay_in["enabled"])
    updater_in = fields.get("updater_in") or {}
    if "mode" in updater_in:
        cfg.updater.mode = cast(Literal["notify", "off"], updater_in["mode"])
    if parsed_hooks is not None:
        cfg.hooks = parsed_hooks
    if parsed_mcp is not None:
        cfg.mcp_servers = parsed_mcp
    state.sandbox = WorkdirSandbox(state.workdir, state.cfg)


def _handle_settings(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    with state.settings_lock:
        if not state.turn_lock.acquire(blocking=False):
            handler._json(409, {"error": "agent is busy"})
            return
        try:
            _handle_settings_locked(handler, body)
        finally:
            state.turn_lock.release()


def _handle_settings_locked(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return

    rejected = [key for key in _READ_ONLY_SETTINGS_SECTIONS if key in body]
    if rejected:
        handler._json(
            400,
            {
                "error": (
                    f"{', '.join(rejected)} is read-only over /api/settings; "
                    "edit config.toml (write API design in docs/API.md)"
                )
            },
        )
        return

    fields = _extract_request_fields(state, body)
    agent_in: dict = fields["agent_in"]

    api_key, provider_error = _resolve_provider_fields(
        state, fields["provider_name"], fields["base_url"], fields["model"],
        fields["api_key"], fields.get("api_key_env"),
    )
    if provider_error:
        handler._json(400, {"error": provider_error})
        return

    agent_error = _agent_validation_error(
        fields["approval"], fields["sandbox_mode"], fields["effort"]
    )
    if agent_error:
        handler._json(400, {"error": agent_error})
        return

    display_flags = {
        k: bool(agent_in[k])
        for k in (
            "show_thinking",
            "show_tools",
            "show_plan",
            "show_context",
            "show_keywords",
            "show_notices",
            # 安全开关与展示开关同为布尔，走同一条"缺省即保持现状"的路径。
            "auto_review",
            "network_access",
            "format_on_write",
        )
        if k in agent_in
    }
    # Deferred until every validator below passes: applying early meant a request
    # that later returned 400 had already cleared `protected_paths` and flipped
    # display flags. A rejected save must change nothing.
    protected_paths_update: list[str] | None = None
    if "protected_paths" in agent_in:
        pp = agent_in.get("protected_paths")
        if isinstance(pp, list):
            protected_paths_update = [str(x).strip() for x in pp if str(x).strip()]

    numbers = _parse_agent_numbers(agent_in, state)
    if isinstance(numbers, str):
        handler._json(400, {"error": numbers})
        return

    if "system_prompt" in agent_in:
        system_prompt = str(agent_in.get("system_prompt") or "")
    else:
        system_prompt = state.cfg.agent.system_prompt

    # ── 上下文管理（Project 页）：此前前端编辑了却从不提交，改完刷新即丢 ──
    context_updates, context_error = _context_updates(body.get("context"))
    if context_error:
        handler._json(400, {"error": context_error})
        return

    # ── 记忆配置（Memory 页）：开关 / 容量 / 嵌入模型 / 自动抽取 / 检索条数 ──
    memory_updates, memory_error = _memory_updates(fields["memory_in"])
    if memory_error:
        handler._json(400, {"error": memory_error})
        return

    # 环境变量名会进入 require_api_key 的报错与子进程环境，必须是合法标识符。
    api_key_env = fields.get("api_key_env")
    if api_key_env and not _ENV_NAME_RE.fullmatch(api_key_env):
        handler._json(400, {"error": "api_key_env must be a valid environment variable name"})
        return
    updater_mode = (fields.get("updater_in") or {}).get("mode")
    if updater_mode is not None and updater_mode not in {"notify", "off"}:
        handler._json(400, {"error": "updater.mode must be notify or off"})
        return

    parsed_hooks, hooks_error = _parse_hooks(body.get("hooks"), state.cfg.hooks)
    if hooks_error:
        handler._json(400, {"error": hooks_error})
        return
    parsed_mcp, mcp_error = _parse_mcp_servers_for_state(
        state, body.get("mcp_servers")
    )
    if mcp_error:
        handler._json(400, {"error": mcp_error})
        return
    if parsed_mcp == state.cfg.mcp_servers:
        bridge = state.mcp_bridge
        retry_failed = bool(parsed_mcp) and (
            bridge is None or (bridge.startup_errors and not bridge.servers)
        )
        if not retry_failed:
            parsed_mcp = None

    same_target = (
        fields["provider_name"] == state.cfg.provider.name
        and fields["base_url"] == state.cfg.provider.base_url
    )
    # ``api_key`` 已由 _resolve_provider_fields 解析完毕：openai_compat 只写入用户
    # 真正提交或同端点已存的密钥（环境变量的值绝不落盘）；mock/ollama 不需要凭据，
    # 同端点保留旧值、跨端点清空，避免把旧端点的密钥带到新主机。
    if fields["provider_name"] == "openai_compat":
        fields["api_key"] = api_key
    else:
        fields["api_key"] = (state.cfg.provider.api_key or "") if same_target else ""
    if not same_target and fields["provider_resilience"].get("api_keys") is None:
        fields["provider_resilience"]["api_keys"] = []
    old_cfg = state.cfg.model_copy(deep=True)
    old_sandbox, old_loop = state.sandbox, state.loop
    old_session, old_approver = state.session_id, state.approver
    old_bridge = state.mcp_bridge
    candidate = None
    try:
        _apply_validated_settings(
            state, fields, numbers, display_flags, protected_paths_update,
            system_prompt, context_updates, memory_updates, parsed_hooks,
            parsed_mcp,
        )
        # 整份候选配置按 config.py 的模型规则复核：任何越界/非法值在这里变成
        # 400 + 回滚，绝不落盘（此前越界值 200 落盘，下次启动被自愈重置）。
        candidate_error = _validate_candidate(state.cfg)
        if candidate_error:
            raise ConfigError(candidate_error)
        if parsed_mcp is not None:
            candidate = state.prepare_mcp(parsed_mcp)
            state.mcp_bridge = candidate
        state.rebuild_loop(session_id=state.session_id)
        _save_config(state.cfg)
    except Exception as exc:  # aqg: top-level boundary
        for name in type(state.cfg).model_fields:
            setattr(state.cfg, name, getattr(old_cfg, name))
        state.sandbox, state.loop = old_sandbox, old_loop
        state.session_id, state.approver = old_session, old_approver
        state.mcp_bridge = old_bridge
        if old_bridge is not None and old_loop is not None and parsed_mcp is None:
            old_bridge.register_into(old_loop.registry)
            old_bridge.set_sampling_handler(old_loop.provider)
        if candidate is not None:
            try:
                run_coroutine_sync(candidate.close())
            except Exception:  # aqg: top-level boundary
                logging.getLogger(__name__).warning("MCP candidate cleanup failed", exc_info=True)
        if isinstance(exc, SparkError):
            handler._json(400, {"error": str(exc)})
        else:
            handler._json(500, {"error": "settings update failed"})
        return
    if parsed_mcp is not None and old_bridge is not None:
        try:
            run_coroutine_sync(old_bridge.close())
        except Exception:  # aqg: top-level boundary
            logging.getLogger(__name__).warning("Old MCP bridge cleanup failed", exc_info=True)

    mcp_errors: list[str] = list(state.mcp_bridge.errors) if state.mcp_bridge else []
    handler._json(
        200,
        {
            "ok": True,
            "status": state.status(),
            "mcp_errors": mcp_errors,
            "config": state.full_config(),
        },
    )


TEST_PROBE_TIMEOUT_SEC = 120


def _handle_test(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    base_url = str(
        body.get("base_url") or state.cfg.provider.base_url
    ).strip()
    model = str(body.get("model") or state.cfg.provider.model).strip()
    explicit_key = str(body.get("api_key") or "").strip()
    if len(base_url) > _MAX_TEST_BASE_URL_CHARS:
        handler._json(400, {"error": "base_url is too long"})
        return
    if len(model) > _MAX_TEST_MODEL_CHARS:
        handler._json(400, {"error": "model is too long"})
        return
    if len(explicit_key) > _MAX_TEST_API_KEY_CHARS:
        handler._json(400, {"error": "api_key is too long"})
        return
    stored_key = state.cfg.provider.api_key or ""
    # The stored credential may only travel to the endpoint it belongs to. When
    # the user is testing a *different* endpoint before saving it, they must
    # bring their own key — otherwise "Test connection" could exfiltrate the
    # saved key to any host the form names.
    if not _probe_target_allowed(base_url, state.cfg.provider.base_url) and not explicit_key:
        handler._json(
            400,
            {
                "error": (
                    "切换端点测试时请一并填入该端点的 API Key；"
                    "存储的密钥只会发往当前已配置的地址"
                ),
                "needs_api_key": True,
            },
        )
        return
    api_key = explicit_key or stored_key
    if not api_key:
        try:
            api_key = require_api_key(state.cfg) or ""
        except ConfigError as exc:
            handler._json(400, {"ok": False, "error": str(exc)})
            return
    if len(api_key) > _MAX_TEST_API_KEY_CHARS:
        handler._json(400, {"error": "api_key is too long"})
        return
    probe_slots = getattr(state, "test_probe_semaphore", _TEST_PROBE_SEMAPHORE)
    if not probe_slots.acquire(blocking=False):
        handler._json(429, {"error": "too many connection tests; retry later"})
        return
    try:
        result = _asyncio.run(
            _asyncio.wait_for(
                probe_provider(
                    base_url=base_url, api_key=api_key, model=model, rounds=2
                ),
                timeout=TEST_PROBE_TIMEOUT_SEC,
            )
        )
    except _asyncio.TimeoutError:
        handler._json(504, {"ok": False, "error": "connection test timed out"})
        return
    except Exception as exc:  # aqg: top-level boundary
        handler._json(
            502,
            {"ok": False, "error": f"connection test failed: {type(exc).__name__}"},
        )
        return
    finally:
        probe_slots.release()
    handler._json(200 if result.ok else 502, result.public_dict())
