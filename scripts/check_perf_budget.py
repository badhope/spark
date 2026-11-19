#!/usr/bin/env python3
"""Check local performance budgets from perf/budgets.json.

This is intentionally dependency-free and does not run in the full unit-test
shard: startup/first-screen/API numbers belong to a quiet machine, not a
parallel test run. CI calls it from the dedicated ``perf`` job in ci.yml.

Usage:
    python scripts/check_perf_budget.py
    python scripts/check_perf_budget.py --json-out perf/current.json
"""
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUDGETS = ROOT / "perf" / "budgets.json"


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    idx = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * pct))))
    return ordered[idx]


def check_cli(budget: dict, runs: int = 10) -> tuple[dict, list[str]]:
    spark = shutil.which("spark")
    failures: list[str] = []
    if spark is None:
        return {}, ["spark executable not found on PATH"]
    times: list[float] = []
    for _ in range(runs):
        started = time.perf_counter()
        proc = subprocess.run(
            [spark, "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode != 0:
            return {}, [f"spark --version exited {proc.returncode}"]
        times.append((time.perf_counter() - started) * 1000.0)
    p50 = percentile(times, 0.50)
    p95 = percentile(times, 0.95)
    hard = max(times)
    if p50 > budget["cli_version_p50_ms"]:
        failures.append(f"cli p50 {p50:.1f}ms > {budget['cli_version_p50_ms']}ms")
    if p95 > budget["cli_version_p95_ms"]:
        failures.append(f"cli p95 {p95:.1f}ms > {budget['cli_version_p95_ms']}ms")
    if hard > budget["cli_version_hard_ms"]:
        failures.append(f"cli max {hard:.1f}ms > {budget['cli_version_hard_ms']}ms")
    return {"p50_ms": round(p50, 2), "p95_ms": round(p95, 2), "max_ms": round(hard, 2)}, failures


def check_bundle(budget: dict) -> tuple[dict, list[str]]:
    candidates = sorted((ROOT / "src" / "spark" / "web" / "dist" / "assets").glob("index-*.js"))
    if not candidates:
        return {}, ["no built index-*.js found (run `npm run build` in web/)"]
    # The entry bundle is the largest index-*.js in Vite output; chunk files are
    # named by feature and are not the first-screen cost.
    entry = max(candidates, key=lambda p: p.stat().st_size)
    raw = entry.read_bytes()
    gz = len(gzip.compress(raw))
    kb = gz / 1024.0
    failures: list[str] = []
    if kb > budget["first_screen_js_gzip_kb"]:
        failures.append(f"first-screen gzip {kb:.1f}KB > {budget['first_screen_js_gzip_kb']}KB")
    return {"entry": entry.name, "gzip_kb": round(kb, 2), "target_kb": budget["first_screen_js_target_kb"]}, failures


def _load_server():
    from spark.config import SparkConfig
    from spark.store import SessionStore
    from spark.web.server import build_web_server

    return SparkConfig, SessionStore, build_web_server


def check_api(budget: dict, samples: int = 20) -> tuple[dict, list[str]]:
    """Measure loopback API p95 with a real ``build_web_server`` instance."""
    spark_config, session_store, build = _load_server()
    import socket
    import threading

    failures: list[str] = []
    report: dict[str, dict[str, float]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        cfg = spark_config()
        cfg.provider.name = "mock"
        cfg.provider.model = "mock-model"
        store = session_store(Path(tmp) / "perf.db")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        server, state = build(
            workdir=Path(tmp), cfg=cfg, store=store, host="127.0.0.1", port=port
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        try:
            for path, budget_key in (
                ("/api/status", "api_status_p95_ms"),
                ("/api/sessions", "api_sessions_p95_ms"),
                ("/api/history", "api_history_p95_ms"),
                ("/api/fs/tree", "api_fs_tree_p95_ms"),
            ):
                times: list[float] = []
                for _ in range(samples):
                    started = time.perf_counter()
                    with urllib.request.urlopen(base + path, timeout=5) as resp:
                        resp.read()
                    times.append((time.perf_counter() - started) * 1000.0)
                p95 = percentile(times, 0.95)
                report[path] = {"p95_ms": round(p95, 2)}
                if p95 > budget[budget_key]:
                    failures.append(f"{path} p95 {p95:.1f}ms > {budget[budget_key]}ms")
        finally:
            server.shutdown()
            server.server_close()
            state.close()
            store.close()
    return report, failures


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--skip-api", action="store_true")
    args = parser.parse_args(argv[1:])
    budget = json.loads(BUDGETS.read_text(encoding="utf-8"))
    report: dict[str, object] = {}
    failures: list[str] = []
    cli, cli_fail = check_cli(budget)
    report["cli"] = cli
    failures.extend(cli_fail)
    bundle, bundle_fail = check_bundle(budget)
    report["bundle"] = bundle
    failures.extend(bundle_fail)
    if not args.skip_api:
        api, api_fail = check_api(budget)
        report["api"] = api
        failures.extend(api_fail)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if failures:
        print("\n".join(f"FAIL: {f}" for f in failures), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    print("OK: local performance budgets are within limits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
