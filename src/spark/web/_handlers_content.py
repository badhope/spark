"""技能 / 提示词 / 插件 / 知识库 —— 设置面板的四类内容管理。

* **技能** 读 ``<workdir>/.spark/skills/**/*.md``（与模型侧的 ``load_skill``
  工具同一份数据源，见 ``spark.skills``）。
* **提示词** 有两层：全局系统提示词在 ``cfg.agent.system_prompt``；可复用模板
  是 ``<workdir>/.spark/commands/*.md``（斜杠命令，见 ``spark.slash``）。
* **插件** 复用 hooks：``cfg.hooks`` 里的 shell 命令就是插件挂载点；这里提供
  发现视图，以及仅限本机请求的 enable/disable 开关。
* **知识库** 见 ``spark.knowledge``：用户显式导入的文档，检索后注入。

这些端点的共同约束：**只读写工作区与 spark home 内的文件**，不接受绝对路径，
所有写操作都走 ``_guard_mutation``（CSRF/Origin）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state
from spark.web.device_admin import is_device_admin

#: 插件/技能/命令的扫描根（相对工作区）。
SKILLS_DIR = Path(".spark") / "skills"
COMMANDS_DIR = Path(".spark") / "commands"
PLUGINS_DIR = Path(".spark") / "plugins"

_MAX_BODY_CHARS = 200_000


def _safe_rel(name: str) -> str | None:
    """只允许工作区内的相对文件名，拒绝绝对路径与 ``..``。"""
    raw = (name or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ":" in raw:
        return None
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def _skill_entries(workdir: Path) -> list[dict[str, Any]]:
    from spark.skills import discover_skills

    out: list[dict[str, Any]] = []
    for s in discover_skills(workdir):
        try:
            rel = s.path.relative_to(workdir).as_posix()
        except ValueError:
            rel = s.path.name
        out.append(
            {
                "name": s.name,
                "description": s.description,
                "when_to_use": s.when_to_use,
                "path": rel,
                "chars": len(s.content),
            }
        )
    return out


def _command_entries(workdir: Path) -> list[dict[str, Any]]:
    from spark.slash import discover_commands

    out: list[dict[str, Any]] = []
    for c in discover_commands(workdir):
        raw_path = getattr(c, "path", "")
        if isinstance(raw_path, Path):
            path_obj = raw_path
            try:
                rel = path_obj.relative_to(workdir)
            except ValueError:
                rel = path_obj
            raw_path = rel.as_posix()
        out.append(
            {
                "name": c.name,
                "description": c.description,
                "path": raw_path,
                "plugin": getattr(c, "plugin", ""),
            }
        )
    return out


def _plugin_entries(workdir: Path) -> list[dict[str, Any]]:
    """Read-only plugin view backed by :mod:`spark.plugins`.

    Installation stays in the CLI for now; the Web panel remains discovery-only
    so it cannot become a second unapproved code-execution path.
    """
    from spark.plugins import enabled_plugin_names, find_manifest, scan_plugins

    enabled = enabled_plugin_names()
    out: list[dict[str, Any]] = []
    for record in scan_plugins(workdir):
        manifest = record.manifest
        out.append(
            {
                "name": record.name,
                "description": manifest.description if manifest else "",
                "path": record.path.relative_to(workdir.resolve()).as_posix(),
                "has_manifest": find_manifest(record.path) is not None,
                "entry": manifest.entry if manifest else "",
                "version": manifest.version if manifest else "",
                "enabled": record.name in enabled,
                "legacy": record.legacy,
                "error": record.error,
                "permissions": manifest.permissions if manifest else [],
                "tools": len(manifest.tools) if manifest else 0,
                "commands": len(manifest.commands) if manifest else 0,
                "hooks": len(manifest.hooks) if manifest else 0,
                "signature_status": record.signature_status,
                "signature_error": record.signature_error,
                "publisher": manifest.publisher if manifest else "",
                "key_id": manifest.key_id if manifest else "",
            }
        )
    return out


def _skills_read(handler: HandlerBase, state, body: dict) -> None:
    rel = _safe_rel(str(body.get("path") or ""))
    if rel is None:
        handler._json(400, {"error": "invalid path"})
        return
    target = state.workdir / rel
    try:
        resolved = target.resolve()
        resolved.relative_to((state.workdir / SKILLS_DIR).resolve())
    except (ValueError, OSError):
        handler._json(400, {"error": "path outside .spark/skills"})
        return
    if not resolved.is_file():
        handler._json(404, {"error": "not found"})
        return
    handler._json(200, {"path": rel, "content": resolved.read_text(encoding="utf-8", errors="replace")})


def _skills_save(handler: HandlerBase, state, body: dict) -> None:
    name = _safe_rel(str(body.get("name") or ""))
    content = str(body.get("content") or "")
    if name is None:
        handler._json(400, {"error": "invalid name"})
        return
    if len(content) > _MAX_BODY_CHARS:
        handler._json(413, {"error": "skill too large"})
        return
    if not content.strip():
        handler._json(400, {"error": "content required"})
        return
    if not name.endswith(".md"):
        name = f"{name}.md"
    target = state.workdir / SKILLS_DIR / name
    try:
        resolved = target.resolve()
        resolved.relative_to((state.workdir / SKILLS_DIR).resolve())
    except (ValueError, OSError):
        handler._json(400, {"error": "path outside .spark/skills"})
        return
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    # A skill without ``---`` frontmatter is invisible to discovery, yet the
    # endpoint answered ``ok`` and the list stayed empty — the user believes
    # the save worked. Tell them instead of silently dropping the entry.
    from spark.skills import discover_skills

    names = {skill.name for skill in discover_skills(state.workdir)}
    saved_name = resolved.stem
    if saved_name not in names:
        handler._json(
            422,
            {
                "error": "skill saved but not discoverable: add YAML frontmatter "
                "(name/description between --- lines)",
                "path": f"{SKILLS_DIR}/{name}",
            },
        )
        return
    handler._json(200, {"ok": True, "items": _skill_entries(state.workdir)})


def _skills_delete(handler: HandlerBase, state, body: dict) -> None:
    rel = _safe_rel(str(body.get("path") or ""))
    if rel is None:
        handler._json(400, {"error": "invalid path"})
        return
    target = state.workdir / rel
    try:
        resolved = target.resolve()
        resolved.relative_to((state.workdir / SKILLS_DIR).resolve())
    except (ValueError, OSError):
        handler._json(400, {"error": "path outside .spark/skills"})
        return
    if not resolved.is_file():
        handler._json(404, {"error": "not found"})
        return
    resolved.unlink()
    handler._json(200, {"ok": True, "items": _skill_entries(state.workdir)})


def _handle_skills(handler: HandlerBase, body: dict) -> None:
    """技能：list / read / save / delete，全部限定在 ``.spark/skills`` 内。"""
    state = _route_state(handler)
    action = str(body.get("action") or "list").strip()
    if action == "list":
        handler._json(200, {"items": _skill_entries(state.workdir)})
        return
    handlers = {
        "read": _skills_read,
        "save": _skills_save,
        "delete": _skills_delete,
    }
    action_handler = handlers.get(action)
    if action_handler is not None:
        action_handler(handler, state, body)
        return
    handler._json(400, {"error": "action must be list, read, save or delete"})

def _prompts_save_system(handler: HandlerBase, state, body: dict) -> None:
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    content = str(body.get("content") or "")
    if len(content) > _MAX_BODY_CHARS:
        handler._json(413, {"error": "prompt too large"})
        return
    with state.settings_lock:
        state.cfg.agent.system_prompt = content
        try:
            from spark.web._handlers_common import _save_config

            _save_config(state.cfg)
        except Exception as exc:  # aqg: top-level boundary — 保存失败要如实回给 UI
            handler._json(500, {"error": f"save failed: {exc}"})
            return
    handler._json(200, {"ok": True, "system_prompt": content})


def _prompts_save_command(handler: HandlerBase, state, body: dict) -> None:
    name = _safe_rel(str(body.get("name") or ""))
    content = str(body.get("content") or "")
    if name is None:
        handler._json(400, {"error": "invalid name"})
        return
    if not content.strip():
        handler._json(400, {"error": "content required"})
        return
    if len(content) > _MAX_BODY_CHARS:
        handler._json(413, {"error": "command too large"})
        return
    if not name.endswith(".md"):
        name = f"{name}.md"
    target = state.workdir / COMMANDS_DIR / name
    try:
        resolved = target.resolve()
        resolved.relative_to((state.workdir / COMMANDS_DIR).resolve())
    except (ValueError, OSError):
        handler._json(400, {"error": "path outside .spark/commands"})
        return
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    handler._json(200, {"ok": True, "commands": _command_entries(state.workdir)})


def _prompts_delete_command(handler: HandlerBase, state, body: dict) -> None:
    name = _safe_rel(str(body.get("name") or ""))
    if name is None:
        handler._json(400, {"error": "invalid name"})
        return
    # 与 save_command 保持一致：允许传 "review" 或 "review.md"。
    if not name.endswith(".md"):
        name = f"{name}.md"
    target = state.workdir / COMMANDS_DIR / name
    try:
        resolved = target.resolve()
        resolved.relative_to((state.workdir / COMMANDS_DIR).resolve())
    except (ValueError, OSError):
        handler._json(400, {"error": "path outside .spark/commands"})
        return
    if not resolved.is_file():
        handler._json(404, {"error": "not found"})
        return
    resolved.unlink()
    handler._json(200, {"ok": True, "commands": _command_entries(state.workdir)})


def _handle_prompts(handler: HandlerBase, body: dict) -> None:
    """提示词：系统提示词读写 + 斜杠命令模板的增删。"""
    state = _route_state(handler)
    action = str(body.get("action") or "list").strip()
    if action == "list":
        handler._json(
            200,
            {
                "system_prompt": getattr(state.cfg.agent, "system_prompt", "") or "",
                "default_prompt": _default_system_prompt(),
                "commands": _command_entries(state.workdir),
            },
        )
        return
    handlers = {
        "save_system": _prompts_save_system,
        "save_command": _prompts_save_command,
        "delete_command": _prompts_delete_command,
    }
    action_handler = handlers.get(action)
    if action_handler is not None:
        action_handler(handler, state, body)
        return
    handler._json(400, {"error": "unknown action"})

def _default_system_prompt() -> str:
    try:
        from spark.core.context import load_system_prompt

        return load_system_prompt()
    except Exception:  # noqa: BLE001 — 读不到默认提示词不影响列表
        return ""


def _plugins_toggle(handler: HandlerBase, state, body: dict, action: str) -> None:
    # Enabling a plugin permits code execution. Keep this local-only: remote
    # token holders can use the agent but must not flip execution grants.
    if not getattr(handler, "_is_loopback", lambda: False)() and not is_device_admin(handler):
        handler._json(
            403,
            {
                "error": "plugin enable/disable requires local control or a device_admin scope",
                "hint": "grant a 30-minute device_admin scope from the local remote panel",
            },
        )
        return
    name = str(body.get("name") or "").strip()
    from spark.config import ConfigError, default_home
    from spark.plugins import scan_plugins, set_plugin_enabled

    record = next((item for item in scan_plugins(state.workdir) if item.name == name), None)
    if record is None:
        handler._json(404, {"error": f"plugin not installed: {name}"})
        return
    if record.manifest is None or record.error:
        handler._json(400, {"error": f"plugin manifest is invalid: {name}"})
        return
    if action == "enable":
        # 执行层 _signature_gate 对 invalid/revoked/untrusted 永远拒绝；
        # Web 开关不能先把它们点亮、等下一轮再报错。
        blocked = {"invalid", "revoked", "untrusted"}
        status = record.signature_status or "unknown"
        if status in blocked:
            handler._json(
                400,
                {
                    "error": (
                        f"插件签名状态为 {status}，执行层会拒绝；请先修复签名或重新信任："
                        f"{record.signature_error}"
                    )
                },
            )
            return
        if bool(getattr(state.cfg.plugins, "require_signature", False)) and status != "signed":
            handler._json(
                400,
                {
                    "error": (
                        "当前配置要求插件签名；该插件状态为 "
                        f"{status}：{record.signature_error}"
                    )
                },
            )
            return
    try:
        set_plugin_enabled(name, action == "enable")
    except ConfigError as exc:
        handler._json(400, {"error": str(exc)})
        return
    # The loop copies [plugins].enabled into its registry at build time.
    enabled = set(state.cfg.plugins.enabled)
    if action == "enable":
        enabled.add(name)
    else:
        enabled.discard(name)
    state.cfg.plugins.enabled = sorted(enabled)
    try:
        state.rebuild_loop(session_id=state.session_id or None)
    except Exception as exc:  # noqa: BLE001 - config is saved; report reload issue
        handler._json(
            200,
            {
                "ok": False,
                "name": name,
                "enabled": action == "enable",
                "error": f"plugin saved but loop reload failed: {exc}",
                "items": _plugin_entries(state.workdir),
                "config": str(default_home() / "config.toml"),
            },
        )
        return
    handler._json(
        200,
        {
            "ok": True,
            "name": name,
            "enabled": action == "enable",
            "items": _plugin_entries(state.workdir),
            "config": str(default_home() / "config.toml"),
        },
    )


def _plugins_policy(handler: HandlerBase, state, body: dict) -> None:
    if not getattr(handler, "_is_loopback", lambda: False)() and not is_device_admin(handler):
        handler._json(
            403,
            {"error": "plugin signature policy requires local control or a device_admin scope"},
        )
        return
    required = body.get("require_signature")
    if not isinstance(required, bool):
        handler._json(400, {"error": "require_signature must be a boolean"})
        return
    from spark.config import ConfigError
    from spark.plugins import set_plugin_require_signature

    try:
        set_plugin_require_signature(required)
    except ConfigError as exc:
        handler._json(400, {"error": str(exc)})
        return
    state.cfg.plugins.require_signature = required
    try:
        state.rebuild_loop(session_id=state.session_id or None)
    except Exception as exc:  # noqa: BLE001 - config saved; report reload issue
        handler._json(
            200,
            {
                "ok": False,
                "require_signature": required,
                "error": f"policy saved but loop reload failed: {exc}",
                "items": _plugin_entries(state.workdir),
            },
        )
        return
    handler._json(
        200,
        {
            "ok": True,
            "require_signature": required,
            "items": _plugin_entries(state.workdir),
        },
    )


def _handle_plugins(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    action = str(body.get("action") or "list").strip()
    if action in {"enable", "disable"}:
        _plugins_toggle(handler, state, body, action)
        return
    if action == "policy":
        _plugins_policy(handler, state, body)
        return
    if action != "list":
        handler._json(400, {"error": "unknown plugin action"})
        return
    handler._json(
        200,
        {
            "items": _plugin_entries(state.workdir),
            "hooks": [h.model_dump() if hasattr(h, "model_dump") else h for h in state.cfg.hooks],
            "plugins_dir": str(PLUGINS_DIR),
            "require_signature": bool(getattr(state.cfg.plugins, "require_signature", False)),
        },
    )

def _knowledge_add(handler: HandlerBase, state, kb, body: dict) -> None:
    raw_paths = body.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        handler._json(400, {"error": "paths required"})
        return
    added: list[dict[str, Any]] = []
    errors: list[str] = []
    for raw in raw_paths:
        rel = _safe_rel(str(raw))
        if rel is None:
            errors.append(f"{raw}: invalid path")
            continue
        target = (state.workdir / rel).resolve()
        try:
            target.relative_to(state.workdir.resolve())
        except ValueError:
            errors.append(f"{rel}: outside workdir")
            continue
        if not target.is_file():
            errors.append(f"{rel}: not found")
            continue
        try:
            meta = kb.add_file(target)
            added.append(meta.__dict__)
        except ValueError as exc:
            errors.append(f"{rel}: {exc}")
    handler._json(
        200 if not errors or added else 400,
        {"added": added, "errors": errors, "items": [d.__dict__ for d in kb.docs()]},
    )


def _knowledge_search(handler: HandlerBase, state, kb, body: dict) -> None:
    query = str(body.get("query") or "").strip()
    if not query:
        handler._json(400, {"error": "query required"})
        return
    kb.ensure_embeddings()
    hits = kb.search(query, top_k=int(getattr(state.cfg.context, "knowledge_top_k", 6)))
    handler._json(200, {"hits": hits})


def _knowledge_settings(handler: HandlerBase, state, body: dict) -> None:
    enabled = body.get("enabled")
    top_k = body.get("top_k")
    with state.settings_lock:
        if isinstance(enabled, bool):
            state.cfg.context.knowledge_enabled = enabled
        if isinstance(top_k, int) and 1 <= top_k <= 50:
            state.cfg.context.knowledge_top_k = top_k
        try:
            from spark.web._handlers_common import _save_config

            _save_config(state.cfg)
        except Exception as exc:  # aqg: top-level boundary
            handler._json(500, {"error": f"save failed: {exc}"})
            return
        handler._json(
            200,
            {
                "ok": True,
                "enabled": state.cfg.context.knowledge_enabled,
                "top_k": state.cfg.context.knowledge_top_k,
            },
        )


def _handle_knowledge(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    action = str(body.get("action") or "list").strip()

    from spark.config import ensure_home
    from spark.knowledge import KnowledgeBase, make_embedder, project_key

    kb = KnowledgeBase(
        ensure_home() / "knowledge" / project_key(state.workdir),
        embedder=make_embedder(state.cfg),
        embedding_model=state.cfg.memory.embedding_model,
    )
    if action == "list":
        handler._json(
            200,
            {
                "items": [d.__dict__ for d in kb.docs()],
                "enabled": bool(getattr(state.cfg.context, "knowledge_enabled", True)),
                "top_k": int(getattr(state.cfg.context, "knowledge_top_k", 6)),
            },
        )
        return
    if action == "add":
        _knowledge_add(handler, state, kb, body)
        return
    if action == "delete":
        doc_id = str(body.get("id") or "").strip()
        if not doc_id:
            handler._json(400, {"error": "id required"})
            return
        ok = kb.remove(doc_id)
        handler._json(200, {"ok": ok, "items": [d.__dict__ for d in kb.docs()]})
        return
    if action == "search":
        _knowledge_search(handler, state, kb, body)
        return
    if action == "settings":
        _knowledge_settings(handler, state, body)
        return
    handler._json(400, {"error": "action must be list, add, delete, search or settings"})