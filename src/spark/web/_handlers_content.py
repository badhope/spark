"""技能 / 提示词 / 插件 / 知识库 —— 设置面板的四类内容管理。

* **技能** 读 ``<workdir>/.spark/skills/**/*.md``（与模型侧的 ``load_skill``
  工具同一份数据源，见 ``spark.skills``）。
* **提示词** 有两层：全局系统提示词在 ``cfg.agent.system_prompt``；可复用模板
  是 ``<workdir>/.spark/commands/*.md``（斜杠命令，见 ``spark.slash``）。
* **插件** 复用 hooks：``cfg.hooks`` 里的 shell 命令就是插件挂载点，这里额外
  提供"发现工作区里声明的插件清单"的只读视图。
* **知识库** 见 ``spark.knowledge``：用户显式导入的文档，检索后注入。

这些端点的共同约束：**只读写工作区与 spark home 内的文件**，不接受绝对路径，
所有写操作都走 ``_guard_mutation``（CSRF/Origin）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state

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
        out.append(
            {
                "name": c.name,
                "description": c.description,
                "path": getattr(c, "path", ""),
            }
        )
    return out


def _plugin_entries(workdir: Path) -> list[dict[str, Any]]:
    """``.spark/plugins/*``：约定每个插件是一个目录，含可执行入口。

    这里只做发现与展示；真正的执行仍由 hooks 负责（避免出现第二套
    不受审批约束的代码执行通道）。
    """
    root = workdir / PLUGINS_DIR
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith("."):
            continue
        manifest = entry / "plugin.json"
        meta: dict[str, Any] = {}
        if manifest.is_file():
            try:
                import json

                loaded = json.loads(manifest.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    meta = loaded
            except (OSError, ValueError):
                meta = {}
        out.append(
            {
                "name": str(meta.get("name") or entry.name),
                "description": str(meta.get("description") or ""),
                "path": entry.relative_to(workdir).as_posix(),
                "has_manifest": manifest.is_file(),
                "entry": str(meta.get("entry") or ""),
            }
        )
    return out


def _handle_skills(handler: HandlerBase, body: dict) -> None:
    """技能：list / read / save / delete，全部限定在 ``.spark/skills`` 内。"""
    state = _route_state(handler)
    action = str(body.get("action") or "list").strip()

    if action == "list":
        handler._json(200, {"items": _skill_entries(state.workdir)})
        return

    if action == "read":
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
        return

    if action == "save":
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

        names = {s.name for s in discover_skills(state.workdir)}
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
        return

    if action == "delete":
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
        return

    handler._json(400, {"error": "action must be list, read, save or delete"})


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

    if action == "save_system":
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
        return

    if action == "save_command":
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
        return

    if action == "delete_command":
        name = _safe_rel(str(body.get("name") or ""))
        if name is None:
            handler._json(400, {"error": "invalid name"})
            return
        # 与 save_command 保持一致：允许传 "review" 或 "review.md"。
        # 早期只按原样拼接，传裸名时永远 404，界面上的删除按钮等于失效。
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
        return

    handler._json(400, {"error": "unknown action"})


def _default_system_prompt() -> str:
    try:
        from spark.core.context import load_system_prompt

        return load_system_prompt()
    except Exception:  # noqa: BLE001 — 读不到默认提示词不影响列表
        return ""


def _handle_plugins(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    action = str(body.get("action") or "list").strip()
    if action != "list":
        handler._json(400, {"error": "plugins are read-only; use hooks to run code"})
        return
    handler._json(
        200,
        {
            "items": _plugin_entries(state.workdir),
            "hooks": [h.model_dump() if hasattr(h, "model_dump") else h for h in state.cfg.hooks],
            "plugins_dir": str(PLUGINS_DIR),
        },
    )


def _handle_knowledge(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    action = str(body.get("action") or "list").strip()

    from spark.config import ensure_home
    from spark.knowledge import KnowledgeBase, project_key

    kb = KnowledgeBase(ensure_home() / "knowledge" / project_key(state.workdir))

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
        query = str(body.get("query") or "").strip()
        if not query:
            handler._json(400, {"error": "query required"})
            return
        hits = kb.search(query, top_k=int(getattr(state.cfg.context, "knowledge_top_k", 6)))
        handler._json(200, {"hits": hits})
        return

    if action == "settings":
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
        return

    handler._json(400, {"error": "action must be list, add, delete, search or settings"})
