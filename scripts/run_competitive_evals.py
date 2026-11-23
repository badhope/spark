"""Run the 30-task internal competitive eval matrix.

Examples:
  python scripts/run_competitive_evals.py --validate-fixtures
  python scripts/run_competitive_evals.py --profiles local-minicpm-1b --limit 6
  python scripts/run_competitive_evals.py --profiles cloud-deepseek,cloud-glm --rounds 2

The script never invents a model result.  A profile whose key/GGUF is absent is
reported as skipped, not as a pass or a fail.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from spark.config import SparkConfig  # noqa: E402
from spark.core.loop import AgentLoop  # noqa: E402
from spark.evals.competitive_tasks import TaskSpec, builtin_competitive_tasks  # noqa: E402
from spark.proc import run_bounded  # noqa: E402
from spark.providers.factory import create_provider  # noqa: E402
from spark.sandbox import WorkdirSandbox  # noqa: E402
from spark.store import SessionStore  # noqa: E402
from spark.tools.registry import ToolContext, ToolRegistry  # noqa: E402


PROFILES: dict[str, dict[str, str]] = {
    "local-minicpm-1b": {
        "provider": "llama_cpp",
        "model": os.environ.get("SPARK_EVAL_LOCAL_MODEL", "MiniCPM5-1B-Q4_K_M.gguf"),
    },
    "cloud-deepseek": {
        "provider": "openai_compat",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key_env": "SPARK_API_KEY",
    },
    "cloud-glm": {
        "provider": "openai_compat",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-5.3",
        "api_key_env": "ZHIPU_API_KEY",
    },
    # User-provided domestic aggregator. This is a real OpenAI-compatible
    # endpoint, not a mock: same harness, same 30 tasks, no special-casing.
    "cloud-qs-glm": {
        "provider": "openai_compat",
        "base_url": "https://api.qs.al/v1",
        "model": "glm-5.3-flash",
        "api_key_env": "QS_API_KEY",
    },
    "cloud-qs-deepseek": {
        "provider": "openai_compat",
        "base_url": "https://api.qs.al/v1",
        "model": "deepseek-v4.1-flash",
        "api_key_env": "QS_API_KEY",
    },
    "cloud-qs-kimi": {
        "provider": "openai_compat",
        "base_url": "https://api.qs.al/v1",
        "model": "kimi-k3",
        "api_key_env": "QS_API_KEY",
    },
    "cloud-qs-glm-pro": {
        "provider": "openai_compat",
        "base_url": "https://api.qs.al/v1",
        "model": "glm-5.3",
        "api_key_env": "QS_API_KEY",
    },
    # Cheapest model on the aggregator. Use this for the bulk matrix; escalate
    # to glm-5.3-flash only when a task actually fails for capability reasons.
    "cloud-qs-qwen": {
        "provider": "openai_compat",
        "base_url": "https://api.qs.al/v1",
        "model": "qwen-flash",
        "api_key_env": "QS_API_KEY",
    },
}


def _write_files(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _check(task: TaskSpec, root: Path) -> tuple[bool, str]:
    cmd = shlex.split(task.check.replace("{python}", f'"{sys.executable}"'))
    proc = run_bounded(cmd, cwd=root, timeout=task.timeout_sec)
    out = (proc.stdout or "") + (proc.stderr or "")
    ok = proc.returncode == 0 and not proc.timed_out
    if task.check_contains:
        missing = [needle for needle in task.check_contains if needle not in out]
        if missing:
            ok = False
            out += f"\nmissing expected output: {missing}"
    return ok, out[-5000:]


def validate_fixtures(tasks: list[TaskSpec], workroot: Path) -> dict[str, Any]:
    """Prove every task is sound: initial state fails, gold state passes."""
    rows = []
    for task in tasks:
        root = workroot / f"{task.id}-initial"
        _write_files(root, task.files)
        initial_ok, initial_out = _check(task, root)
        gold_root = workroot / f"{task.id}-gold"
        _write_files(gold_root, task.files)
        _write_files(gold_root, task.gold)
        gold_ok, gold_out = _check(task, gold_root)
        rows.append({
            "id": task.id,
            "category": task.category,
            "initial_expected_fail": not initial_ok,
            "gold_pass": gold_ok,
            "initial_output": initial_out[-1500:],
            "gold_output": gold_out[-1500:],
        })
    return {
        "total": len(rows),
        "initial_fail_count": sum(1 for r in rows if r["initial_expected_fail"]),
        "gold_pass_count": sum(1 for r in rows if r["gold_pass"]),
        "ok": all(r["initial_expected_fail"] and r["gold_pass"] for r in rows),
        "tasks": rows,
    }


def _profile_available(profile: dict[str, str]) -> tuple[bool, str]:
    if profile["provider"] == "llama_cpp":
        from spark.local_models import models_dir

        path = Path(profile["model"]).expanduser()
        if not path.is_file():
            path = models_dir() / path.name
        if path.is_file():
            return True, str(path)
        return False, f"GGUF not found: {profile['model']}"
    env = profile.get("api_key_env", "")
    if env and os.environ.get(env, "").strip():
        return True, f"{env} is set"
    return False, f"{env or 'API key'} is not set"


def _cfg_for(profile: dict[str, str], root: Path) -> SparkConfig:
    cfg = SparkConfig()
    cfg.provider.name = profile["provider"]  # type: ignore[assignment]
    cfg.provider.model = profile["model"]
    cfg.provider.base_url = profile.get("base_url", cfg.provider.base_url)
    cfg.provider.api_key_env = profile.get("api_key_env", cfg.provider.api_key_env)
    cfg.agent.approval = "full-auto"
    cfg.agent.sandbox_mode = "workspace"
    cfg.agent.max_tool_rounds = 30
    cfg.agent.show_metrics = True
    cfg.agent.tool_preset = "coding"
    cfg.context.repo_map_enabled = True
    return cfg


async def _run_one(task: TaskSpec, profile_name: str, profile: dict[str, str], root: Path) -> dict[str, Any]:
    _write_files(root, task.files)
    cfg = _cfg_for(profile, root)
    store = SessionStore(root / ".eval-store.db")
    sid = store.create_session(root, cfg.provider.model, title=f"eval:{task.id}")
    ctx = ToolContext(sandbox=WorkdirSandbox(root, cfg), config=cfg)
    registry = ToolRegistry(ctx)
    loop = AgentLoop(
        workdir=root,
        cfg=cfg,
        provider=create_provider(cfg),
        registry=registry,
        store=store,
        session_id=sid,
    )
    started = time.monotonic()
    metrics: dict[str, Any] = {}
    tool_calls: list[str] = []
    final_text = ""
    error = ""
    try:
        async for event in loop.iter_turn(task.prompt):
            if event.type == "tool_start" and event.tool_call is not None:
                tool_calls.append(event.tool_call.name)
            elif event.type == "metrics" and event.data:
                metrics = dict(event.data)
            elif event.type == "turn_error":
                error = event.text or "turn_error"
            elif event.type == "turn_end":
                final_text = event.text or ""
    except Exception as exc:  # noqa: BLE001 - one task must not abort the matrix
        error = f"{type(exc).__name__}: {exc}"
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    usage = store.session_token_usage(sid)
    store.close()
    check_ok, check_out = _check(task, root)
    return {
        "task_id": task.id,
        "category": task.category,
        "profile": profile_name,
        "passed": bool(check_ok and not error),
        "elapsed_ms": elapsed_ms,
        "metrics": metrics,
        "tool_calls": tool_calls,
        "final_text": final_text[-500:],
        "error": error,
        "check_output": check_out,
        "usage": usage,
    }


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return round(ordered[idx], 1)


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_profile: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        by_profile.setdefault(row["profile"], []).append(row)
    summary = {}
    for profile, rows in by_profile.items():
        total = len(rows)
        passed = sum(1 for r in rows if r["passed"])
        errors = sum(1 for r in rows if r.get("error"))
        valid = total - errors
        ttft = [r["metrics"].get("ttft_ms") for r in rows if r.get("metrics", {}).get("ttft_ms") is not None]
        total_ms = [r["metrics"].get("total_ms") for r in rows if r.get("metrics", {}).get("total_ms") is not None]
        cost = 0.0
        priced = 0
        for r in rows:
            value = r.get("usage", {}).get("cost_usd")
            if isinstance(value, (int, float)):
                cost += float(value)
                priced += 1
        summary[profile] = {
            "total": total,
            "passed": passed,
            # pass@1 只对真正跑完的 turn 有意义；provider 全挂时单独标出，
            # 不把“没跑起来”伪装成“模型不会做”。
            "pass@1": round(passed / valid, 3) if valid else None,
            "errors": errors,
            "error_rate": round(errors / total, 3) if total else None,
            "ttft_p50_ms": _quantile([float(v) for v in ttft], 0.50),
            "ttft_p95_ms": _quantile([float(v) for v in ttft], 0.95),
            "turn_p50_ms": _quantile([float(v) for v in total_ms], 0.50),
            "turn_p95_ms": _quantile([float(v) for v in total_ms], 0.95),
            # 没有价目表时返回 null，而不是把未知成本伪装成 $0。
            "cost_usd": round(cost, 4) if priced else None,
            "priced_turns": priced,
            "unpriced_turns": total - priced,
        }
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run-competitive-evals")
    parser.add_argument("--validate-fixtures", action="store_true")
    parser.add_argument("--profiles", default="local-minicpm-1b")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--task-timeout",
        type=float,
        default=900.0,
        help="per-task wall-clock cap in seconds; 0 disables. A timed-out task is "
        "recorded as an error, not as a model failure.",
    )
    parser.add_argument("--tasks", default="")
    parser.add_argument("--report", default="evals/competitive/report.json")
    args = parser.parse_args(argv)

    tasks = builtin_competitive_tasks()
    if args.tasks:
        wanted = {x.strip() for x in args.tasks.split(",") if x.strip()}
        tasks = [t for t in tasks if t.id in wanted]
    if args.limit > 0:
        tasks = tasks[: args.limit]

    workroot = Path(tempfile.mkdtemp(prefix="spark-competitive-evals-"))
    if args.validate_fixtures:
        report = validate_fixtures(tasks, workroot)
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: report[k] for k in ("total", "initial_fail_count", "gold_pass_count", "ok")}, ensure_ascii=False))
        return 0 if report["ok"] else 1

    profiles = [p.strip() for p in args.profiles.split(",") if p.strip()]
    unknown = [p for p in profiles if p not in PROFILES]
    if unknown:
        parser.error(f"unknown profile(s): {', '.join(unknown)}")

    results: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for name in profiles:
        profile = PROFILES[name]
        available, reason = _profile_available(profile)
        if not available:
            skipped.append({"profile": name, "reason": reason})
            continue
        for round_index in range(max(1, args.rounds)):
            for index, task in enumerate(tasks):
                root = workroot / f"{name}-r{round_index}-{index:02d}-{task.id}"
                root.mkdir(parents=True, exist_ok=True)
                if args.task_timeout and args.task_timeout > 0:
                    try:
                        result = asyncio.run(
                            asyncio.wait_for(
                                _run_one(task, name, profile, root),
                                timeout=args.task_timeout,
                            )
                        )
                    except asyncio.TimeoutError:
                        result = {
                            "task_id": task.id,
                            "category": task.category,
                            "profile": name,
                            "passed": False,
                            "elapsed_ms": round(args.task_timeout * 1000, 1),
                            "metrics": {},
                            "tool_calls": [],
                            "final_text": "",
                            "error": f"task_timeout after {args.task_timeout:g}s",
                            "check_output": "",
                            "usage": {},
                        }
                else:
                    result = asyncio.run(_run_one(task, name, profile, root))
                result["round"] = round_index + 1
                results.append(result)
                mark = "PASS" if result["passed"] else "FAIL"
                print(f"[{mark}] {name} round {round_index + 1} {task.id} ({result['elapsed_ms']}ms)")
                # 增量落盘：长矩阵跑到一半被中断，也不会把已完成的 turn 全丢掉。
                Path(args.report).parent.mkdir(parents=True, exist_ok=True)
                Path(args.report).write_text(
                    json.dumps(
                        {
                            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                            "profiles": profiles,
                            "rounds": max(1, args.rounds),
                            "task_count": len(tasks),
                            "summary": summarize(results),
                            "skipped": skipped,
                            "results": results,
                            "partial": True,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "profiles": profiles,
        "rounds": max(1, args.rounds),
        "task_count": len(tasks),
        "summary": summarize(results),
        "skipped": skipped,
        "results": results,
        "partial": False,
    }
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "skipped": skipped}, ensure_ascii=False, indent=2))
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
