"""`_handlers_content` 的路由级测试：直接驱动 handler 函数，断言响应码与副作用。

不构造 `SparkWebState`——它会在构造时 boot MCP 并重建 agent loop，属于另一个
量级的依赖。这里只给 handler 需要的四个字段：workdir / cfg / busy / memory。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from spark.config import SparkConfig
from spark.web._handlers_content import (
    _handle_knowledge,
    _handle_plugins,
    _handle_prompts,
    _handle_skills,
)


class FakeHandler:
    def __init__(self, state: Any) -> None:
        self.server = SimpleNamespace(state=state)
        self.code: int | None = None
        self.body: Any = None

    def _json(self, code: int, body: Any) -> None:
        self.code = code
        self.body = body


@pytest.fixture()
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    # 知识库写在 spark home 下，测试里指到临时目录，避免污染真实 home。
    monkeypatch.setenv("SPARK_HOME", str(tmp_path / "home"))
    cfg = SparkConfig()
    cfg.agent.system_prompt = ""
    work = tmp_path / "work"
    work.mkdir()
    return SimpleNamespace(workdir=work, cfg=cfg, busy=False, settings_lock=threading.Lock())


def call(fn, state, body: dict) -> tuple[int | None, Any]:
    h = FakeHandler(state)
    fn(h, body)
    return h.code, h.body


class TestSkillsEndpoint:
    def test_list_empty(self, state) -> None:
        code, body = call(_handle_skills, state, {"action": "list"})
        assert code == 200 and body["items"] == []

    def test_save_then_list(self, state) -> None:
        code, body = call(
            _handle_skills,
            state,
            {"action": "save", "name": "demo", "content": "---\nname: demo\ndescription: d\n---\n\nbody"},
        )
        assert code == 200 and body["ok"] is True
        assert (state.workdir / ".spark" / "skills" / "demo.md").is_file()

        code, body = call(_handle_skills, state, {"action": "list"})
        assert [i["name"] for i in body["items"]] == ["demo"]

    def test_save_requires_content(self, state) -> None:
        code, body = call(_handle_skills, state, {"action": "save", "name": "x", "content": "  "})
        assert code == 400 and "content" in body["error"]

    def test_save_rejects_traversal(self, state) -> None:
        code, body = call(_handle_skills, state, {"action": "save", "name": "../../evil", "content": "x"})
        assert code == 400

    def test_save_adds_md_suffix(self, state) -> None:
        call(_handle_skills, state, {"action": "save", "name": "auto", "content": "---\nname: auto\n---\nbody"})
        assert (state.workdir / ".spark" / "skills" / "auto.md").is_file()

    def test_save_rejects_oversized(self, state) -> None:
        code, body = call(
            _handle_skills, state, {"action": "save", "name": "big", "content": "x" * 200_001}
        )
        assert code == 413

    def test_read_roundtrip(self, state) -> None:
        call(_handle_skills, state, {"action": "save", "name": "r", "content": "---\nname: r\n---\nhello body"})
        code, body = call(_handle_skills, state, {"action": "read", "path": ".spark/skills/r.md"})
        assert code == 200 and "hello body" in body["content"]

    def test_read_missing(self, state) -> None:
        code, _ = call(_handle_skills, state, {"action": "read", "path": ".spark/skills/nope.md"})
        assert code == 404

    def test_read_outside_skills_dir(self, state) -> None:
        (state.workdir / "secret.md").write_text("no", encoding="utf-8")
        code, body = call(_handle_skills, state, {"action": "read", "path": "secret.md"})
        assert code == 400 and "outside" in body["error"]

    def test_delete(self, state) -> None:
        call(_handle_skills, state, {"action": "save", "name": "d", "content": "---\nname: d\n---\nx"})
        code, body = call(_handle_skills, state, {"action": "delete", "path": ".spark/skills/d.md"})
        assert code == 200 and body["items"] == []

    def test_delete_missing(self, state) -> None:
        code, _ = call(_handle_skills, state, {"action": "delete", "path": ".spark/skills/nope.md"})
        assert code == 404

    def test_unknown_action(self, state) -> None:
        code, body = call(_handle_skills, state, {"action": "wat"})
        assert code == 400


class TestPromptsEndpoint:
    def test_list_includes_default(self, state) -> None:
        code, body = call(_handle_prompts, state, {"action": "list"})
        assert code == 200
        assert body["default_prompt"], "built-in prompt should be readable"
        assert body["system_prompt"] == ""

    def test_save_system_prompt(self, state) -> None:
        code, body = call(_handle_prompts, state, {"action": "save_system", "content": "custom"})
        assert code == 200 and body["system_prompt"] == "custom"
        assert state.cfg.agent.system_prompt == "custom"

    def test_save_system_prompt_busy(self, state) -> None:
        state.busy = True
        code, _ = call(_handle_prompts, state, {"action": "save_system", "content": "x"})
        assert code == 409

    def test_save_system_prompt_oversized(self, state) -> None:
        code, _ = call(_handle_prompts, state, {"action": "save_system", "content": "x" * 200_001})
        assert code == 413

    def test_save_command(self, state) -> None:
        code, body = call(
            _handle_prompts,
            state,
            {"action": "save_command", "name": "review", "content": "# 审查\n\n按规范审查"},
        )
        assert code == 200
        assert [c["name"] for c in body["commands"]] == ["review"]
        assert (state.workdir / ".spark" / "commands" / "review.md").is_file()

    def test_save_command_requires_content(self, state) -> None:
        code, _ = call(_handle_prompts, state, {"action": "save_command", "name": "x", "content": ""})
        assert code == 400

    def test_delete_command(self, state) -> None:
        call(_handle_prompts, state, {"action": "save_command", "name": "c", "content": "body"})
        code, body = call(_handle_prompts, state, {"action": "delete_command", "name": "c"})
        assert code == 200 and body["commands"] == []

    def test_delete_command_missing(self, state) -> None:
        code, _ = call(_handle_prompts, state, {"action": "delete_command", "name": "nope"})
        assert code == 404

    def test_unknown_action(self, state) -> None:
        code, _ = call(_handle_prompts, state, {"action": "wat"})
        assert code == 400


class TestPluginsEndpoint:
    def test_list_empty(self, state) -> None:
        code, body = call(_handle_plugins, state, {"action": "list"})
        assert code == 200
        assert body["items"] == [] and body["hooks"] == []

    def test_list_with_plugin(self, state) -> None:
        d = state.workdir / ".spark" / "plugins" / "p1"
        d.mkdir(parents=True)
        (d / "plugin.json").write_text(json.dumps({"name": "p1", "description": "d"}), encoding="utf-8")
        code, body = call(_handle_plugins, state, {"action": "list"})
        assert code == 200 and body["items"][0]["name"] == "p1"

    def test_write_rejected(self, state) -> None:
        code, body = call(_handle_plugins, state, {"action": "install"})
        assert code == 400
        assert "read-only" in body["error"]


class TestKnowledgeEndpoint:
    def test_list_empty(self, state) -> None:
        code, body = call(_handle_knowledge, state, {"action": "list"})
        assert code == 200 and body["items"] == []
        assert body["enabled"] is True

    def test_add_and_search(self, state) -> None:
        doc = state.workdir / "spec.md"
        doc.write_text("鉴权走三层作用域，deny 优先。", encoding="utf-8")
        code, body = call(_handle_knowledge, state, {"action": "add", "paths": ["spec.md"]})
        assert code == 200 and len(body["added"]) == 1

        code, body = call(_handle_knowledge, state, {"action": "search", "query": "鉴权"})
        assert code == 200 and body["hits"]
        assert body["hits"][0]["doc_name"] == "spec.md"

    def test_add_requires_paths(self, state) -> None:
        code, _ = call(_handle_knowledge, state, {"action": "add", "paths": []})
        assert code == 400

    def test_add_rejects_outside_workdir(self, state) -> None:
        code, body = call(_handle_knowledge, state, {"action": "add", "paths": ["../escape.md"]})
        assert code == 400 and body["errors"]

    def test_add_reports_missing_file(self, state) -> None:
        code, body = call(_handle_knowledge, state, {"action": "add", "paths": ["nope.md"]})
        assert code == 400
        assert "not found" in body["errors"][0]

    def test_add_unsupported_suffix(self, state) -> None:
        (state.workdir / "a.exe").write_bytes(b"\x00")
        code, body = call(_handle_knowledge, state, {"action": "add", "paths": ["a.exe"]})
        assert code == 400

    def test_delete(self, state) -> None:
        doc = state.workdir / "d.md"
        doc.write_text("content", encoding="utf-8")
        _, body = call(_handle_knowledge, state, {"action": "add", "paths": ["d.md"]})
        doc_id = body["added"][0]["id"]
        code, body = call(_handle_knowledge, state, {"action": "delete", "id": doc_id})
        assert code == 200 and body["ok"] is True and body["items"] == []

    def test_delete_requires_id(self, state) -> None:
        code, _ = call(_handle_knowledge, state, {"action": "delete"})
        assert code == 400

    def test_search_requires_query(self, state) -> None:
        code, _ = call(_handle_knowledge, state, {"action": "search", "query": ""})
        assert code == 400

    def test_settings_toggle(self, state) -> None:
        code, body = call(
            _handle_knowledge, state, {"action": "settings", "enabled": False, "top_k": 12}
        )
        assert code == 200
        assert body["enabled"] is False and body["top_k"] == 12
        assert state.cfg.context.knowledge_enabled is False

    def test_settings_clamps_top_k(self, state) -> None:
        code, body = call(_handle_knowledge, state, {"action": "settings", "top_k": 999})
        assert code == 200
        assert body["top_k"] == 6  # 越界值被忽略，保持原值

    def test_unknown_action(self, state) -> None:
        code, _ = call(_handle_knowledge, state, {"action": "wat"})
        assert code == 400
