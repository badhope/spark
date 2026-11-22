"""模型档案 CRUD + 一键切换（/api/models/save|delete|activate）。

从单文件 ``_handlers.py`` 按域拆出，行为不变；导入路径经
``spark.web._handlers`` facade 保持稳定。
"""

from __future__ import annotations

import uuid
from typing import cast

from spark.config import PROVIDER_NAMES, ProviderName, activate_model_profile
from spark.errors import SparkError
from spark.web._handlers_common import _save_config
from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state


def _parse_resilience(body: dict) -> tuple[list[str], list[str], str]:
    """供应商韧性字段（可缺省）：api_keys 凭据池 / fallback_models 回退链 / small_model。"""
    raw_keys = body.get("api_keys")
    api_keys = [str(x).strip() for x in raw_keys if str(x).strip()] if isinstance(raw_keys, list) else []
    raw_fb = body.get("fallback_models")
    fallback_models = [str(x).strip() for x in raw_fb if str(x).strip()] if isinstance(raw_fb, list) else []
    small_model = str(body.get("small_model") or "").strip()
    return api_keys, fallback_models, small_model


def _handle_models_save(handler: HandlerBase, body: dict) -> None:
    with _route_state(handler).settings_lock:
        _handle_models_save_locked(handler, body)


def _handle_models_save_locked(handler: HandlerBase, body: dict) -> None:
    from spark.config import ModelProfile

    name = str(body.get("name") or "").strip()
    base_url = str(body.get("base_url") or "").strip()
    model = str(body.get("model") or "").strip()
    provider_name = str(body.get("provider") or "openai_compat").strip()
    api_key = str(body.get("api_key") or "").strip()
    if provider_name not in PROVIDER_NAMES:
        handler._json(400, {"error": f"provider must be one of: {', '.join(PROVIDER_NAMES)}"})
        return
    native_without_base = provider_name in {"anthropic", "openai", "gemini", "bedrock"}
    if not model or (not base_url and not native_without_base):
        handler._json(
            400,
            {"error": "base_url and model are required (native Anthropic/OpenAI/Gemini/Bedrock omit base_url)"},
        )
        return
    state = _route_state(handler)
    pid = str(body.get("id") or "").strip()
    api_keys, fallback_models, small_model = _parse_resilience(body)
    existing = next(
        (p for p in state.cfg.model_profiles if p.id == pid), None
    )
    if existing:
        same_target = existing.provider == provider_name and existing.base_url.rstrip("/") == base_url.rstrip("/")
        existing.name = name or existing.name or existing.model
        existing.provider = cast(ProviderName, provider_name)
        existing.base_url = base_url
        existing.model = model
        if api_key or not same_target:
            existing.api_key = api_key
        if "api_key_env" in body or not same_target:
            existing.api_key_env = str(body.get("api_key_env") or "").strip()
        if "api_keys" in body or not same_target:
            existing.api_keys = api_keys
        if "fallback_models" in body:
            existing.fallback_models = fallback_models
        if "small_model" in body:
            existing.small_model = small_model
    else:
        if not api_key and not state.cfg.active_profile_id and state.cfg.provider.name == provider_name and state.cfg.provider.base_url.rstrip("/") == base_url.rstrip("/"):
            api_key = state.cfg.provider.api_key or ""
        existing = ModelProfile(
            id=uuid.uuid4().hex[:8],
            name=name or model,
            provider=cast(ProviderName, provider_name),
            base_url=base_url,
            model=model,
            api_key=api_key,
            api_key_env=str(body.get("api_key_env") or "").strip(),
            api_keys=api_keys,
            fallback_models=fallback_models,
            small_model=small_model,
        )
        state.cfg.model_profiles.append(existing)
    _save_config(state.cfg)
    handler._json(200, {"ok": True, "id": existing.id})


def _handle_models_delete(handler: HandlerBase, body: dict) -> None:
    with _route_state(handler).settings_lock:
        _handle_models_delete_locked(handler, body)


def _handle_models_delete_locked(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    pid = str(body.get("id") or "").strip()
    before = len(state.cfg.model_profiles)
    state.cfg.model_profiles = [
        p for p in state.cfg.model_profiles if p.id != pid
    ]
    if len(state.cfg.model_profiles) == before:
        handler._json(404, {"error": "unknown profile"})
        return
    if state.cfg.active_profile_id == pid:
        state.cfg.active_profile_id = ""
    _save_config(state.cfg)
    handler._json(200, {"ok": True})


def _handle_models_activate(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    with state.settings_lock:
        if not state.turn_lock.acquire(blocking=False):
            handler._json(409, {"error": "agent is busy"})
            return
        try:
            _handle_models_activate_locked(handler, body)
        finally:
            state.turn_lock.release()


def _handle_models_activate_locked(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    pid = str(body.get("id") or "").strip()
    profile = next(
        (p for p in state.cfg.model_profiles if p.id == pid), None
    )
    if not profile:
        handler._json(404, {"error": "unknown profile"})
        return
    activate_model_profile(state.cfg, profile)
    _save_config(state.cfg)
    try:
        state.rebuild_loop(session_id=state.session_id)
    except SparkError as exc:
        handler._json(400, {"error": str(exc)})
        return
    handler._json(200, {"ok": True, "status": state.status()})


DEMO_PROFILE_ID = "__last_real__"


def _handle_demo_toggle(handler: HandlerBase, body: dict) -> None:
    """一键进入/退出 Mock 展示模式，且不丢失进入前的真实配置。

    ``mock`` 只作为显式展示态存在：进入时把当前真实 provider 存成
    一个专用档案，退出时原样恢复。缺 Key / 模型未下载等错误由
    ``status().model_status`` 如实返回，不在这里兜底成 Mock。
    """
    state = _route_state(handler)
    with state.settings_lock:
        if not state.turn_lock.acquire(blocking=False):
            handler._json(409, {"error": "agent is busy"})
            return
        try:
            demo = body.get("demo")
            if not isinstance(demo, bool):
                handler._json(400, {"error": "demo must be true or false"})
                return
            if demo:
                if state.cfg.provider.name != "mock":
                    from spark.config import ModelProfile

                    saved = next(
                        (p for p in state.cfg.model_profiles if p.id == DEMO_PROFILE_ID),
                        None,
                    )
                    if saved is None:
                        saved = ModelProfile(id=DEMO_PROFILE_ID)
                        state.cfg.model_profiles.append(saved)
                    saved.name = "真实模型（进入演示前）"
                    saved.provider = state.cfg.provider.name
                    saved.base_url = state.cfg.provider.base_url
                    saved.model = state.cfg.provider.model
                    saved.api_key = state.cfg.provider.api_key or ""
                    saved.api_key_env = state.cfg.provider.api_key_env
                    saved.api_keys = state.cfg.provider.api_keys.copy()
                    saved.fallback_models = state.cfg.provider.fallback_models.copy()
                    saved.small_model = state.cfg.provider.small_model
                state.cfg.provider.name = "mock"
                state.cfg.provider.base_url = ""
                state.cfg.provider.model = "mock"
                state.cfg.provider.api_key = None
                state.cfg.provider.api_key_env = ""
                state.cfg.active_profile_id = ""
            else:
                from spark.config import activate_model_profile

                saved = next(
                    (p for p in state.cfg.model_profiles if p.id == DEMO_PROFILE_ID),
                    None,
                )
                if saved is None:
                    handler._json(
                        409,
                        {
                            "error": "没有可恢复的真实模型：请先在「设置 → 模型」配置真实 provider 并保存，再进入演示模式。",
                        },
                    )
                    return
                activate_model_profile(state.cfg, saved)
            _save_config(state.cfg)
            try:
                state.rebuild_loop(session_id=state.session_id)
            except SparkError as exc:
                handler._json(400, {"error": str(exc)})
                return
            handler._json(200, {"ok": True, "demo_mode": state.cfg.provider.name == "mock", "status": state.status()})
        finally:
            state.turn_lock.release()
