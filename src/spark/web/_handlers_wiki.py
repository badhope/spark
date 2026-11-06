"""Repo Wiki 的 HTTP 面：生成、读取索引、读取单个模块文档。

与 CLI ``spark wiki`` 走同一份 ``spark.wiki`` 实现，不存在两套生成逻辑。
端点只在工作区内读写（产物固定落 ``.spark/wiki/``），所有写操作经
``_guard_mutation``（Origin/Sec-Fetch-Site 校验），与其它写端点一致。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state

#: wiki 产物根（相对 workdir）。与 spark.wiki.WikiConfig 默认值一致。
WIKI_DIR = Path(".spark") / "wiki"
INDEX_NAME = "INDEX.md"
MODULE_DIR = "modules"
#: 单次读取上限，避免把整份 wiki 塞进一个 JSON 响应。
MAX_DOC_CHARS = 400_000


def _wiki_root(workdir: Path) -> Path:
    return (workdir / WIKI_DIR).resolve()


#: 记录上一次生成实际使用的 max_files，供 status 回显。
META_NAME = ".meta.json"


def _read_meta(root: Path) -> dict:
    try:
        return json.loads((root / META_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _last_max_files(root: Path) -> int:
    try:
        return int(_read_meta(root).get("max_files") or 0)
    except (TypeError, ValueError):
        return 0


def _write_meta(root: Path, max_files: int, stats: dict) -> None:
    payload = {"max_files": max_files, "modules": stats.get("modules", 0), "written_at": int(time.time())}
    try:
        (root / META_NAME).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _safe_module(name: str) -> str | None:
    """模块文件名必须是 ``.spark/wiki/modules/`` 下的裸文件名。"""
    raw = (name or "").strip().replace("\\", "/")
    if not raw or "/" in raw or ":" in raw or raw.startswith("."):
        return None
    if not raw.endswith(".md"):
        return None
    return raw


def _handle_wiki(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    action = str(body.get("action") or "status").strip()
    root = _wiki_root(state.workdir)

    if action == "status":
        index = root / INDEX_NAME
        modules = sorted(p.name for p in (root / MODULE_DIR).glob("*.md")) if root.is_dir() else []
        handler._json(
            200,
            {
                "exists": index.is_file(),
                "index_path": str(index) if index.is_file() else "",
                "modules": len(modules),
                "out_dir": str(root),
                # Report what the last generation actually used, not the
                # form's default: the panel showed 240 next to a 30-file wiki.
                "max_files": _last_max_files(root) or 240,
            },
        )
        return

    if action == "generate":
        if state.busy:
            handler._json(409, {"error": "agent is busy"})
            return
        from spark.wiki import WikiConfig, build_wiki

        raw_files = body.get("max_files", 240)
        try:
            max_files = max(1, min(int(raw_files), 2000))
        except (TypeError, ValueError):
            handler._json(400, {"error": "max_files must be an integer"})
            return
        try:
            stats = build_wiki(
                state.workdir.resolve(),
                WikiConfig(max_files=max_files),
            )
        except OSError as exc:
            handler._json(500, {"error": f"wiki generation failed: {exc}"})
            return
        _write_meta(root, max_files, stats)
        handler._json(200, {"ok": True, "max_files": max_files, **stats})
        return

    if action == "read":
        raw = str(body.get("name") or "")
        if not raw or raw == INDEX_NAME:
            target = root / INDEX_NAME
        else:
            safe = _safe_module(raw)
            if safe is None:
                handler._json(400, {"error": "invalid module name"})
                return
            target = root / MODULE_DIR / safe
        if not target.is_file():
            handler._json(404, {"error": "wiki not generated yet", "name": raw})
            return
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as exc:
            handler._json(500, {"error": f"read failed: {exc}"})
            return
        truncated = len(text) > MAX_DOC_CHARS
        handler._json(
            200,
            {
                "name": target.name,
                "content": text[:MAX_DOC_CHARS],
                "truncated": truncated,
                "chars": len(text),
            },
        )
        return

    if action == "delete":
        # 产物可随时重建；删除只是清理，不做确认对话框（前端二次确认）。
        import shutil

        if not root.is_dir():
            handler._json(200, {"ok": True, "removed": False})
            return
        try:
            root.relative_to(state.workdir.resolve())
        except ValueError:
            handler._json(400, {"error": "wiki directory is outside the workdir"})
            return
        shutil.rmtree(root)
        handler._json(200, {"ok": True, "removed": True})
        return

    handler._json(400, {"error": f"unknown action: {action}"})
