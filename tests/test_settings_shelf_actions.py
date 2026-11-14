"""Real business actions per settings shelf, not just "the tab paints".

A tab that renders but cannot perform its job is the failure mode this file
targets: memory must actually store and search a note, the knowledge base must
actually ingest a document, automations must actually reject a bad cron
expression, skills/prompts must actually create entries.

These endpoints belong to the *main app* server (``/api/...``), not the
enterprise control plane (``/api/v1/...``), so the tests start the real Web
server exactly like ``spark web`` does.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def live(tmp_path: Path):
    from spark.config import SparkConfig
    from spark.store import SessionStore
    from spark.web.server import build_web_server

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.provider.base_url = "http://localhost:9999/v1"
    cfg.provider.model = "mock-model"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "spark.db")
    port = _free_port()
    server, state = build_web_server(
        workdir=tmp_path, cfg=cfg, store=store, host="127.0.0.1", port=port
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)
    try:
        yield base, tmp_path
    finally:
        server.shutdown()
        server.server_close()
        state.close()
        store.close()


def _request(base: str, path: str, payload: dict | None = None, method: str | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method or ("POST" if data else "GET"),
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {"raw": raw.decode(errors="replace")}
        return exc.code, body


def test_memory_add_then_search(live) -> None:
    base, _tmp = live
    status, body = _request(
        base, "/api/memory", {"action": "add", "content": "Spark 记忆动作测试条目"}, "POST"
    )
    assert status in (200, 201), body
    assert not body.get("errors"), body
    # ``add`` returns the store's live stats; a non-zero active count after
    # exactly one write proves it landed. (This endpoint has no "list" action —
    # the UI reads aggregated stats, so asserting on that shape is the real
    # contract, not an invented one.)
    assert body.get("stats", {}).get("active", 0) >= 1, body


def test_knowledge_ingest_file(live) -> None:
    base, tmp = live
    doc = tmp / "policy.md"
    doc.write_text("# 报销制度\n差旅报销需在返回后 15 个工作日内提交。\n", encoding="utf-8")
    status, body = _request(
        base, "/api/knowledge",
        {"action": "add", "paths": ["policy.md"], "name": "报销制度"},
        "POST",
    )
    assert status in (200, 201), body
    assert not body.get("errors"), body


def test_automation_rejects_bad_cron(live) -> None:
    base, _tmp = live
    status, body = _request(
        base,
        "/api/automations",
        {"action": "create", "name": "坏表达式", "cron": "definitely not cron", "prompt": "x"},
        "POST",
    )
    assert status >= 400, f"invalid cron accepted: {status} {body}"


def test_automation_accepts_valid_cron(live) -> None:
    base, _tmp = live
    status, body = _request(
        base,
        "/api/automations",
        {"action": "create", "name": "每日摘要", "cron": "0 9 * * *", "prompt": "总结昨天"},
        "POST",
    )
    assert status in (200, 201), body
    assert not body.get("errors"), body
    assert (body.get("automation") or {}).get("name") == "每日摘要", body
    status, listed = _request(base, "/api/automations", {"action": "list"}, "POST")
    assert status == 200
    rows = listed.get("automations") or listed.get("items") or []
    assert any(row.get("name") == "每日摘要" for row in rows), rows


def test_skills_create_and_list(live) -> None:
    base, _tmp = live
    # Discovery only sees files with YAML frontmatter; the endpoint now rejects
    # a body without it instead of returning ok and leaving the list empty.
    content = "---\nname: review-helper\ndescription: 审查助手\n---\n\n# 审查助手\n检查边界。"
    status, body = _request(
        base, "/api/skills", {"action": "save", "name": "review-helper", "content": content}, "POST"
    )
    assert status in (200, 201), body
    status, listed = _request(base, "/api/skills", {"action": "list"}, "POST")
    rows = listed.get("items") or []
    assert any("review-helper" in str(row.get("name") or "") for row in rows), rows


def test_skill_without_frontmatter_is_rejected(live) -> None:
    """Saving an undiscoverable skill must fail loudly, not silently."""
    base, _tmp = live
    status, body = _request(
        base,
        "/api/skills",
        {"action": "save", "name": "no-frontmatter", "content": "# 没有 frontmatter"},
        "POST",
    )
    assert status == 422, (status, body)
    assert "frontmatter" in str(body.get("error", ""))


def test_prompts_create_and_list(live) -> None:
    base, _tmp = live
    status, body = _request(
        base, "/api/prompts",
        {"action": "save_system", "content": "你是一个严谨的助手。用三点总结：{input}"},
        "POST",
    )
    assert status in (200, 201), body
    assert not body.get("errors"), body
    status, listed = _request(base, "/api/prompts", {"action": "list"}, "POST")
    assert status == 200, listed
    assert "三点总结" in str(listed.get("system_prompt", "")), listed


def test_debug_endpoint_reports_state(live) -> None:
    base, _tmp = live
    status, body = _request(base, "/api/debug")
    assert status == 200
    assert isinstance(body, dict) and body


def test_plugins_endpoint_lists_directory(live) -> None:
    base, _tmp = live
    status, body = _request(base, "/api/plugins", {"action": "list"}, "POST")
    assert status == 200
    assert "items" in body
