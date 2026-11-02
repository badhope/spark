"""模型档案 CRUD + 一键切换（/api/models/save|delete|activate）。

从单文件 ``_handlers.py`` 按域拆出，行为不变；导入路径经
``spark.web._handlers`` facade 保持稳定。
"""

from __future__ import annotations

import uuid
from typing import cast

from spark.config import ProviderName, activate_model_profile
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
    if provider_name not in {"openai_compat", "ollama", "mock"}:
        handler._json(400, {"error": "provider must be openai_compat, ollama or mock"})
        return
    if not base_url or not model:
        handler._json(400, {"error": "base_url and model are required"})
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
