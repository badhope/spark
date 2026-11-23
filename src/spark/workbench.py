"""Task workbench v1 + delivery ledger + privacy receipt.

The workbench is a read-mostly composition layer.  It does not invent a second
task engine: queue rows, sessions, checkpoints, tool events, turn metrics and
Git changes already exist.  It answers one user question: "what did I ask,
what changed, what proved it, what left the machine, and can I roll it back?"
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

from spark.store import SessionStore


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _safe_workdir(store: SessionStore, session_id: str, fallback: str = "") -> str:
    row = store.get_session(session_id) if session_id else None
    return str((row or {}).get("workdir") or fallback or "")


_TEST_COMMAND_RE = re.compile(
    r"(?i)(?:pytest|py\.test|unittest|tox|nox|vitest|jest|mocha|npm\s+(?:run\s+)?test|"
    r"pnpm\s+(?:run\s+)?test|yarn\s+test|go\s+test|cargo\s+test|mvn\s+.*test|"
    r"gradle\s+.*test|make\s+test|ctest|dotnet\s+test|rspec|phpunit)"
)


def _is_test_event(name: str, arguments: dict) -> bool:
    """Only count commands that actually prove something.

    ``run_tests`` is explicit. ``run_shell`` is accepted only when its command
    matches a known test runner; a random ``ls`` or ``git status`` is not
    evidence and must not be rendered as PASS/FAIL in the ledger.
    """
    if name == "run_tests":
        return True
    if name != "run_shell":
        return False
    command = str(arguments.get("command") or "")
    return bool(_TEST_COMMAND_RE.search(command))


def _tests_from_events(events: list[dict]) -> list[dict]:
    tests: list[dict] = []
    for event in events:
        name = str(event.get("name") or "")
        arguments = _json(event.get("arguments_json"), {}) or {}
        if not isinstance(arguments, dict) or not _is_test_event(name, arguments):
            continue
        result = _json(event.get("result_json"), {})
        payload = result.get("payload") if isinstance(result, dict) else {}
        if not isinstance(payload, dict):
            payload = {}
        output = str(payload.get("stdout") or payload.get("output") or payload.get("stderr") or "")
        if not output:
            continue
        tests.append(
            {
                "name": name,
                "command": str(arguments.get("command") or ("pytest" if name == "run_tests" else "")),
                "ok": bool(result.get("ok")) if isinstance(result, dict) else None,
                "returncode": payload.get("returncode"),
                "output": output[-1200:],
                "created_at": event.get("created_at"),
            }
        )
    return tests[-8:]


def _approvals_from_events(events: list[dict]) -> list[dict]:
    approvals = []
    for event in events:
        if not event.get("approval"):
            continue
        approvals.append(
            {
                "tool": event.get("name"),
                "action": event.get("approval"),
                "arguments": _json(event.get("arguments_json"), {}),
                "created_at": event.get("created_at"),
            }
        )
    return approvals[-20:]


_WRITE_TOOLS = {"write_file", "apply_patch", "apply_edit", "notebook_edit", "git_commit"}


def _paths_from_events(events: list[dict]) -> set[str]:
    """Workspace paths the agent actually touched, from tool arguments.

    This is deliberately conservative: a path only enters the ledger when a
    write-capable tool names it. ``git_commit`` has no path and therefore falls
    back to the worktree diff rather than pretending it knows the file set.
    """
    paths: set[str] = set()
    for event in events:
        name = str(event.get("name") or "")
        if name not in _WRITE_TOOLS or name == "git_commit":
            continue
        args = _json(event.get("arguments_json"), {}) or {}
        if not isinstance(args, dict):
            continue
        for key in ("path", "file", "target"):
            value = str(args.get(key) or "").strip()
            if value:
                paths.add(value)
    return paths


def _git_diff_paths(workdir: str, paths: set[str], fallback: str) -> str:
    """Bounded diff for the touched paths; falls back to the worktree diff."""
    if not paths:
        return fallback
    try:
        import subprocess

        proc = subprocess.run(
            ["git", "-C", workdir, "diff", "--no-color", "--no-ext-diff", "HEAD", "--", *sorted(paths)],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode == 0 and (proc.stdout or "").strip():
            return proc.stdout[:20000]
    except Exception:
        pass
    return fallback


def build_privacy_receipt(
    store: SessionStore,
    session_id: str,
    cfg: Any,
    *,
    workdir: str = "",
    events: list[dict] | None = None,
) -> dict[str, Any]:
    """What left the machine this turn/session, in plain language.

    Local GGUF/Ollama traffic is labelled local.  A cloud provider is labelled
    with its endpoint and token count; Spark never claims it has no egress.
    """
    events = events if events is not None else store.list_tool_events(session_id, limit=500)
    read_files: list[str] = []
    network_tools: list[str] = []
    for event in events:
        name = str(event.get("name") or "")
        args = _json(event.get("arguments_json"), {}) or {}
        if name in {"read_file", "grep", "glob", "list_dir"}:
            path = args.get("path") or args.get("pattern")
            if path:
                read_files.append(str(path))
        if name.startswith("browser_") or name in {"web_search", "web_fetch"}:
            network_tools.append(name)
    usage = store.session_token_usage(session_id)
    provider = str(getattr(cfg.provider, "name", "") or "")
    local = provider in {"llama_cpp", "ollama"}
    return {
        "mode": "local" if local else "remote",
        "provider": provider,
        "model": str(getattr(cfg.provider, "model", "") or ""),
        "endpoint": "" if local else str(getattr(cfg.provider, "base_url", "") or ""),
        "tokens_sent": int(usage.get("input_tokens") or 0),
        "tokens_received": int(usage.get("output_tokens") or 0),
        "files_read": sorted(set(read_files))[:80],
        "network_tools": sorted(set(network_tools)),
        "telemetry": "local-only",
        "update_check": "github (can be disabled)",
        "workdir": workdir or _safe_workdir(store, session_id),
    }


def build_delivery(
    store: SessionStore,
    session_id: str,
    cfg: Any,
    *,
    task_id: str = "",
    title: str = "",
    status: str = "review",
    summary: str = "",
    workdir: str = "",
    since_id: int = 0,
) -> dict[str, Any]:
    """Build (and persist) the ledger entry for one turn/task.

    The ledger is intentionally evidence-shaped: changed files, command
    output, approvals, cost, checkpoint and privacy receipt.  A task can be
    "review" without being "done"; only the user can accept it.
    """
    now = time.time()
    wd = workdir or _safe_workdir(store, session_id)
    events = [
        event
        for event in store.list_tool_events(session_id, limit=500)
        if int(event.get("id") or 0) > int(since_id or 0)
    ]
    try:
        from spark.web.git_api import git_changes

        changes = git_changes(wd)
    except Exception:
        changes = {"repo": False, "entries": [], "diff": "", "status_count": 0}
    touched = _paths_from_events(events)
    raw_entries = list(changes.get("entries") or [])
    if touched:
        entries = [item for item in raw_entries if str(item.get("path") or "") in touched]
        for path in sorted(touched):
            if not any(str(item.get("path") or "") == path for item in entries):
                entries.append({"code": "M", "path": path})
    else:
        entries = raw_entries
    diff_text = _git_diff_paths(wd, touched, str(changes.get("diff") or ""))[:20000]
    checkpoints = store.list_checkpoints(session_id)
    metrics = store.list_turn_metrics(session_id, limit=1)
    usage = store.session_token_usage(session_id, model=getattr(cfg.provider, "model", "") or None)
    usage = {**usage, "scope": "session"}
    delivery = {
        "id": uuid.uuid4().hex,
        "session_id": session_id,
        "task_id": task_id,
        "title": title or f"Session {session_id[:8]}",
        "status": status,
        "summary": summary,
        "workdir": wd,
        "files": entries[:200],
        "diff_summary": {
            "repo": bool(changes.get("repo")),
            "branch": changes.get("branch") or "",
            "status_count": len(entries),
            "truncated": bool(changes.get("truncated")) or len(diff_text) >= 20000,
            "diff": diff_text,
            "attribution": "tool_events" if touched else "worktree",
        },
        "tests": _tests_from_events(events),
        "approvals": _approvals_from_events(events),
        "cost": usage,
        "checkpoint_id": (checkpoints[-1].get("id") if checkpoints else None),
        "metrics": (metrics[0].get("metrics") if metrics else {}),
        "privacy": build_privacy_receipt(store, session_id, cfg, workdir=wd, events=events),
        "created_at": now,
    }
    return store.save_delivery(delivery)


def delivery_markdown(delivery: dict[str, Any]) -> str:
    lines = [
        f"# {delivery.get('title') or 'Delivery'}",
        "",
        f"- 任务：`{delivery.get('task_id') or '-'}`",
        f"- 会话：`{delivery.get('session_id') or '-'}`",
        f"- 状态：**{delivery.get('status') or '-'}**",
        f"- 工作目录：`{delivery.get('workdir') or '-'}`",
        f"- 检查点：`{delivery.get('checkpoint_id') or '-'}`",
        f"- 代码出境：**{'否（本地模型）' if (delivery.get('privacy') or {}).get('mode') == 'local' else '是（远程端点）'}**",
        "",
        "## 变更文件",
    ]
    for item in delivery.get("files") or []:
        lines.append(f"- `{item.get('code', '')}` {item.get('path', '')}")
    if not delivery.get("files"):
        lines.append("- （无工作区变更）")
    diff = str((delivery.get("diff_summary") or {}).get("diff") or "")
    if diff.strip():
        lines.extend(["", "## Diff（bounded）", "```diff", diff.rstrip(), "```"])
    lines.extend(["", "## 验证证据"])
    for test in delivery.get("tests") or []:
        mark = "✅" if test.get("ok") else "❌"
        lines.append(f"- {mark} `{test.get('command') or test.get('name')}` rc={test.get('returncode')}")
        if test.get("output"):
            lines.append("  ```")
            lines.extend("  " + line for line in str(test["output"]).splitlines()[-8:])
            lines.append("  ```")
    if not delivery.get("tests"):
        lines.append("- （未记录测试/命令输出）")
    lines.extend(["", "## 审批记录"])
    for approval in delivery.get("approvals") or []:
        lines.append(f"- {approval.get('action')} · `{approval.get('tool')}`")
    if not delivery.get("approvals"):
        lines.append("- （无审批记录）")
    lines.extend(["", "## 隐私回执"])
    privacy = delivery.get("privacy") or {}
    lines.append(f"- 模式：{privacy.get('mode')}；provider={privacy.get('provider')}；model={privacy.get('model')}")
    lines.append(f"- 发送 token：{privacy.get('tokens_sent')}；接收 token：{privacy.get('tokens_received')}")
    lines.append(f"- 读取文件：{', '.join(privacy.get('files_read') or []) or '无'}")
    lines.append(f"- 网络工具：{', '.join(privacy.get('network_tools') or []) or '无'}")
    cost = delivery.get("cost") or {}
    lines.extend(
        [
            "",
            "## 成本",
            f"- 口径：{cost.get('scope') or 'session'}；calls={cost.get('calls', 0)}",
            f"- input={cost.get('input_tokens', 0)} output={cost.get('output_tokens', 0)}",
            f"- cost_usd={cost.get('cost_usd')}",
        ]
    )
    return "\n".join(lines) + "\n"


def workbench_snapshot(
    store: SessionStore,
    queue: Any,
    *,
    session_id: str,
    workdir: str,
    cfg: Any,
    limit: int = 30,
) -> dict[str, Any]:
    """One response for the Workbench tab: tasks, review queue, ledger."""
    tasks = queue.list_tasks(limit=limit) if queue is not None else []
    deliveries = store.list_deliveries(workdir=workdir, limit=limit)
    # list_deliveries is newest-first; setdefault keeps the newest row per session.
    by_session: dict[str, dict] = {}
    for delivery in deliveries:
        by_session.setdefault(str(delivery.get("session_id") or ""), delivery)
    cards = []
    for task in tasks:
        delivery = by_session.get(str(task.get("session_id") or ""))
        cards.append(
            {
                "id": task.get("id"),
                "title": task.get("title") or (task.get("prompt") or "")[:80],
                "prompt": task.get("prompt"),
                "status": task.get("status"),
                "priority": task.get("priority"),
                "attempts": task.get("attempts"),
                "session_id": task.get("session_id"),
                "error": task.get("error"),
                "result": task.get("result"),
                "created_at": task.get("created_at"),
                "finished_at": task.get("finished_at"),
                "delivery_id": delivery.get("id") if delivery else None,
                "delivery_status": delivery.get("status") if delivery else None,
            }
        )
    try:
        from spark.web.git_api import git_changes

        changes = git_changes(workdir)
    except Exception:
        changes = {"repo": False, "entries": [], "diff": "", "status_count": 0}
    session = store.get_session(session_id) if session_id else None
    metrics = store.list_turn_metrics(session_id, limit=10) if session_id else []
    privacy = build_privacy_receipt(store, session_id, cfg, workdir=workdir) if session_id else {}
    return {
        "session_id": session_id,
        "workdir": workdir,
        "session": session,
        "queue": {"paused": bool(getattr(queue, "paused", False)), "counts": store.queue_counts()},
        "tasks": cards,
        "deliveries": deliveries,
        "review": [d for d in deliveries if d.get("status") == "review"],
        "changes": {
            "repo": changes.get("repo"),
            "branch": changes.get("branch"),
            "status_count": changes.get("status_count"),
            "entries": list(changes.get("entries") or [])[:200],
            "truncated": changes.get("truncated"),
        },
        "checkpoints": store.list_checkpoints(session_id)[-10:] if session_id else [],
        "metrics": metrics,
        "privacy": privacy,
        "cost": store.session_token_usage(session_id) if session_id else {},
    }
