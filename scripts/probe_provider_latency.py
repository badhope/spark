"""Measure provider-only first-token latency, outside AgentLoop.

The point is to separate two numbers that are easy to conflate:

* gateway/model TTFT: time from request to the first streamed delta;
* agent turn latency: gateway TTFT + context assembly + tool rounds + tools.

Usage:
  QS_API_KEY=... python scripts/probe_provider_latency.py \
      --profiles cloud-qs-glm,cloud-qs-kimi --repeat 5 \
      --report evals/competitive/latency-probe.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_competitive_evals import PROFILES, _profile_available  # noqa: E402
from spark.config import SparkConfig  # noqa: E402
from spark.models import ChatMessage  # noqa: E402
from spark.providers.factory import create_provider  # noqa: E402


async def probe(profile: dict[str, str], repeat: int) -> dict:
    cfg = SparkConfig()
    cfg.provider.name = profile["provider"]  # type: ignore[assignment]
    cfg.provider.model = profile["model"]
    cfg.provider.base_url = profile.get("base_url", cfg.provider.base_url)
    cfg.provider.api_key_env = profile.get("api_key_env", cfg.provider.api_key_env)
    provider = create_provider(cfg)
    rows = []
    for index in range(repeat):
        started = time.monotonic()
        first = None
        error = ""
        try:
            async for _delta in provider.stream(
                [ChatMessage(role="user", content="Reply with exactly OK. No tools.")],
                [],
                effort=None,
            ):
                if first is None:
                    first = time.monotonic() - started
        except Exception as exc:  # noqa: BLE001 - one probe must not abort the set
            error = f"{type(exc).__name__}: {exc}"[:300]
        rows.append(
            {
                "index": index + 1,
                "ttft_ms": round(first * 1000, 1) if first is not None else None,
                "total_ms": round((time.monotonic() - started) * 1000, 1),
                "error": error,
            }
        )
    ok = [r["ttft_ms"] for r in rows if r["ttft_ms"] is not None]
    return {
        "repeat": repeat,
        "ok": len(ok),
        "errors": len(rows) - len(ok),
        "ttft_min_ms": min(ok) if ok else None,
        "ttft_p50_ms": round(statistics.median(ok), 1) if ok else None,
        "ttft_max_ms": max(ok) if ok else None,
        "runs": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="probe-provider-latency")
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--report", default="evals/competitive/latency-probe.json")
    args = parser.parse_args()

    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "profiles": {}}
    for name in [p.strip() for p in args.profiles.split(",") if p.strip()]:
        if name not in PROFILES:
            parser.error(f"unknown profile: {name}")
        available, reason = _profile_available(PROFILES[name])
        if not available:
            report["profiles"][name] = {"skipped": reason}
            continue
        report["profiles"][name] = asyncio.run(probe(PROFILES[name], max(1, args.repeat)))
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    compact = {
        name: {k: v.get(k) for k in ("ttft_p50_ms", "ttft_min_ms", "ttft_max_ms", "errors")}
        for name, v in report["profiles"].items()
        if "skipped" not in v
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
