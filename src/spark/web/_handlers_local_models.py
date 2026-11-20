"""Local GGUF catalog: download jobs and one-click activation."""

from __future__ import annotations

from spark.local_models import get_spec
from spark.web._handlers_common import _save_config
from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state


def _handle_local_models_download(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    model_id = str(body.get("id") or "").strip()
    try:
        job = state.local_models.start_download(model_id)
    except KeyError:
        handler._json(404, {"error": "unknown local model"})
        return
    handler._json(202, {"id": model_id, "status": job.status})


def _handle_local_models_activate(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    model_id = str(body.get("id") or "").strip()
    spec = get_spec(model_id)
    if spec is None:
        handler._json(404, {"error": "unknown local model"})
        return
    if not state.local_models.installed(spec):
        handler._json(409, {"error": "model is not downloaded yet"})
        return
    path = str(state.local_models._path(spec))
    with state.settings_lock:
        state.cfg.provider.name = "llama_cpp"
        state.cfg.provider.base_url = ""
        state.cfg.provider.model = path
        state.cfg.provider.api_key = None
        state.cfg.provider.api_key_env = ""
        state.local_models.mark_active(path)
        _save_config(state.cfg)
        state.rebuild_loop(session_id=state.session_id or None)
    handler._json(
        200,
        {
            "config": state.full_config(),
            "status": state.status(),
            "model": spec.public(),
        },
    )
