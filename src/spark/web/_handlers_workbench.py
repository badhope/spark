"""HTTP surface for the task workbench and delivery ledger."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from spark.asyncutil import run_coroutine_sync
from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state
from spark.workbench import build_delivery, delivery_markdown, workbench_snapshot


def handle_workbench_get(handler: HandlerBase) -> None:
    state = _route_state(handler)
    sid = str(getattr(state, "session_id", "") or "")
    payload = workbench_snapshot(
        state.store,
        getattr(state, "queue", None),
        session_id=sid,
        workdir=str(state.workdir),
        cfg=state.cfg,
    )
    handler._json(200, payload)


def handle_deliveries_get(handler: HandlerBase) -> None:
    state = _route_state(handler)
    sid = str(getattr(state, "session_id", "") or "")
    rows = state.store.list_deliveries(workdir=str(state.workdir), limit=50)
    handler._json(200, {"deliveries": rows, "session_id": sid})


def handle_delivery_export(handler: HandlerBase) -> None:
    state = _route_state(handler)
    qs = parse_qs(urlparse(handler.path).query)
    delivery_id = (qs.get("id", [""])[0] or "").strip()
    if delivery_id:
        row = state.store.get_delivery(delivery_id)
    else:
        rows = state.store.list_deliveries(workdir=str(state.workdir), limit=1)
        row = rows[0] if rows else None
    if not row:
        handler._json(404, {"error": "delivery not found"})
        return
    handler._json(200, {"delivery": row, "markdown": delivery_markdown(row)})


def handle_delivery_create(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    sid = str(body.get("session_id") or getattr(state, "session_id", "") or "")
    if not sid:
        handler._json(400, {"error": "session_id is required"})
        return
    session = state.store.get_session(sid)
    if session is None:
        handler._json(404, {"error": "unknown session"})
        return
    row = build_delivery(
        state.store,
        sid,
        state.cfg,
        task_id=str(body.get("task_id") or ""),
        title=str(body.get("title") or (session.get("title") or "")),
        status=str(body.get("status") or "review"),
        summary=str(body.get("summary") or ""),
        workdir=str(session.get("workdir") or state.workdir),
    )
    handler._json(200, {"ok": True, "delivery": row})


def handle_delivery_update(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    delivery_id = str(body.get("id") or "").strip()
    row = state.store.get_delivery(delivery_id) if delivery_id else None
    if not row:
        handler._json(404, {"error": "delivery not found"})
        return
    status = str(body.get("status") or row.get("status") or "review")
    if status not in {"review", "accepted", "rejected", "rolled_back"}:
        handler._json(400, {"error": "status must be review, accepted, rejected or rolled_back"})
        return
    row["status"] = status
    updated = state.store.save_delivery(row)
    handler._json(200, {"ok": True, "delivery": updated})


def _language_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".js": "javascript",
        ".jsx": "jsx",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".kt": "kotlin",
        ".md": "markdown",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
    }.get(suffix, "")


def handle_inline_edit(handler: HandlerBase, body: dict) -> None:
    """Single editor-native model call: replace selection or suggest next edit."""
    state = _route_state(handler)
    mode = str(body.get("mode") or "edit")
    if mode not in {"edit", "nes"}:
        handler._json(400, {"error": "mode must be edit or nes"})
        return
    path = str(body.get("path") or "")
    if path:
        try:
            state.sandbox.resolve(path)
        except Exception:
            handler._json(400, {"error": "path escapes the workspace"})
            return
    if mode == "edit" and not str(body.get("selected") or "").strip():
        handler._json(400, {"error": "selected text is required for edit mode"})
        return
    request = dict(body)
    # Editors send only the selection, but a code edit without surrounding
    # context is guessing. Read the saved file and attach bounded prefix/suffix
    # when the selection is unique; apply still re-checks uniqueness and hash.
    if path and mode == "edit":
        selected = str(body.get("selected") or "")
        try:
            text = state.sandbox.resolve(path).read_text(encoding="utf-8", errors="replace")
            if text.count(selected) == 1:
                idx = text.find(selected)
                request["prefix"] = text[max(0, idx - 3000):idx]
                request["suffix"] = text[idx + len(selected):idx + len(selected) + 3000]
                request["language"] = str(body.get("language") or _language_for_path(path))
        except Exception:
            # Preview can still run from the selection alone; apply will reject a
            # stale selection rather than writing a guessed location.
            pass
    try:
        from spark.providers.factory import create_provider

        provider = create_provider(state.cfg)
        from spark.inline_edit import generate_inline_edit

        result = run_coroutine_sync(generate_inline_edit(provider, request), timeout=120)
    except Exception as exc:  # noqa: BLE001 — surface the model/transport failure
        handler._json(502, {"ok": False, "error": f"inline edit failed: {exc}"})
        return
    result["model"] = str(getattr(state.cfg.provider, "model", "") or "")
    if path:
        try:
            import hashlib

            target = state.sandbox.resolve(path)
            result["base_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        except Exception:
            result["base_sha256"] = None
    handler._json(200, result)


def handle_inline_edit_apply(handler: HandlerBase, body: dict) -> None:
    """Apply a previewed replacement after an explicit user click.

    This is the Web counterpart of VS Code's WorkspaceEdit: the model never
    writes the file directly; the client must echo the exact selection it
    previewed, and a stale/mismatched selection is rejected instead of
    guessing an offset.
    """
    import difflib

    from spark.web.device_admin import is_device_admin

    if not getattr(handler, "_is_loopback", lambda: False)() and not is_device_admin(handler):
        handler._json(403, {"error": "applying an inline edit requires local control or device_admin"})
        return
    state = _route_state(handler)
    path = str(body.get("path") or "").strip()
    selected = str(body.get("selected") or "")
    replacement = str(body.get("replacement") or "")
    base_sha256 = str(body.get("base_sha256") or "").strip()
    if not path:
        handler._json(400, {"error": "path is required"})
        return
    if not selected:
        handler._json(400, {"error": "selected is required"})
        return
    if not state.sandbox.write_allowed:
        handler._json(403, {"error": "sandbox is read-only"})
        return
    try:
        target = state.sandbox.resolve(path)
    except Exception:
        handler._json(400, {"error": "path escapes the workspace"})
        return
    try:
        before = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        handler._json(400, {"error": f"cannot read file: {exc}"})
        return
    if base_sha256:
        import hashlib

        if hashlib.sha256(target.read_bytes()).hexdigest() != base_sha256:
            handler._json(
                409,
                {"error": "file changed since the preview; generate a fresh preview", "stale": True},
            )
            return
    count = before.count(selected)
    if count != 1:
        handler._json(
            409,
            {
                "error": (
                    "selection is stale or ambiguous; refresh the preview and select again"
                    if count
                    else "selection no longer exists in the file"
                ),
                "matches": count,
            },
        )
        return
    after = before.replace(selected, replacement, 1)
    if after == before:
        handler._json(200, {"ok": True, "changed": False, "path": path})
        return
    try:
        target.write_text(after, encoding="utf-8")
    except OSError as exc:
        handler._json(500, {"error": f"write failed: {exc}"})
        return
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )[:20000]
    handler._json(200, {"ok": True, "changed": True, "path": path, "diff": diff})


def handle_code_index_get(handler: HandlerBase) -> None:
    state = _route_state(handler)
    qs = parse_qs(urlparse(handler.path).query)
    query = (qs.get("q", [""])[0] or "").strip()
    try:
        from spark.config import ensure_home
        from spark.semantic_index import make_code_index

        index = make_code_index(state.workdir, state.cfg, ensure_home())
        status = index.status()
        hits = index.search(query, top_k=8) if query and status.chunks else []
    except Exception as exc:  # noqa: BLE001
        handler._json(500, {"error": f"code index failed: {exc}"})
        return
    handler._json(
        200,
        {
            "built": status.chunks > 0,
            "files": status.files,
            "chunks": status.chunks,
            "vectors": status.vectors,
            "built_at": status.built_at,
            "query": query,
            "hits": hits,
        },
    )


def handle_code_index_build(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    try:
        from spark.config import ensure_home
        from spark.semantic_index import make_code_index

        index = make_code_index(state.workdir, state.cfg, ensure_home())
        status = index.refresh(force=bool(body.get("force", True)))
    except Exception as exc:  # noqa: BLE001
        handler._json(500, {"error": f"code index build failed: {exc}"})
        return
    handler._json(
        200,
        {
            "ok": True,
            "files": status.files,
            "chunks": status.chunks,
            "vectors": status.vectors,
            "built_at": status.built_at,
        },
    )
