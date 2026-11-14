"""spark agent eval harness（P1-6）。

设计取舍（诚实记录）：
- 调研时实测 ``deepeval==4.2.8`` 可在全新 venv 安装并 import（31 个直接依赖，
  新增 grpcio/aiohttp/posthog 等 ~50MB 闭包），但其评判层（GEval 等）必须调用
  LLM API，mock 模式的 CI 里无法生效；SWE-bench 风格的「检出仓库跑 pytest」
  验证 deepeval 本身也不提供，仍需手写。综合「CI 收益 / 安装成本」，本 harness
  仅用标准库实现，deepeval/promptfoo 留作文档中的可选 LLM-judge 路径。
- fixtures 全部本地、无网络：toy 任务在 tmp workdir 里真实跑 AgentLoop 后用
  文件检查 / pytest / 工具轨迹判定 pass/fail；真实 SWE-bench Lite 子集从 HF
  datasets-server 拉取后内嵌为 JSON，仅做 ingest smoke（不求解、不跑真实仓库）。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from spark.config import SparkConfig
from spark.core.loop import AgentLoop
from spark.models import ChatDelta, ToolCall
from spark.proc import run_bounded
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry


@dataclass
class Scenario:
    """一个 eval 场景。

    script: MockProvider 的逐轮剧本（无网络），每一轮是若干 delta：
      - {"type": "text", "text": "..."}
      - {"type": "tool_call", "tool_call": {"id": "...", "name": "...", "arguments": {...}}}
    judges: 判定器列表，全部通过才算 pass。
    """

    id: str
    suite: str
    prompt: str
    setup_files: dict[str, str] = field(default_factory=dict)
    script: list[list[dict[str, Any]]] = field(default_factory=list)
    judges: list[dict[str, Any]] = field(default_factory=list)
    expect_turn_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class JudgeResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ScenarioResult:
    id: str
    suite: str
    passed: bool
    elapsed_ms: int
    judges: list[JudgeResult] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "suite": self.suite,
            "passed": self.passed,
            "elapsed_ms": self.elapsed_ms,
            "judges": [
                {"name": j.name, "passed": j.passed, "detail": j.detail} for j in self.judges
            ],
            "error": self.error,
        }


def _build_provider(script: list[list[dict[str, Any]]]) -> MockProvider:
    rounds: list[list[ChatDelta]] = []
    for round_deltas in script:
        batch: list[ChatDelta] = []
        for item in round_deltas:
            if item.get("type") == "tool_call":
                tc = item["tool_call"]
                batch.append(
                    ChatDelta(
                        type="tool_call",
                        tool_call=ToolCall(
                            id=str(tc.get("id") or "call"),
                            name=str(tc["name"]),
                            arguments=dict(tc.get("arguments") or {}),
                        ),
                    )
                )
            else:
                batch.append(ChatDelta(type="text", text=str(item.get("text") or "")))
        rounds.append(batch)
    return MockProvider(rounds=rounds)


def _judge_files_exist(workdir: Path, spec: dict[str, Any]) -> JudgeResult:
    paths = [str(p) for p in spec.get("paths", [])]
    missing = [p for p in paths if not (workdir / p).exists()]
    return JudgeResult(
        "files_exist",
        not missing,
        "missing: " + ", ".join(missing) if missing else f"{len(paths)} file(s) exist",
    )


def _judge_file_contains(workdir: Path, spec: dict[str, Any]) -> JudgeResult:
    path = workdir / str(spec["path"])
    if not path.exists():
        return JudgeResult("file_contains", False, f"{spec['path']} missing")
    content = path.read_text(encoding="utf-8")
    bad = [s for s in spec.get("contains", []) if s not in content]
    return JudgeResult(
        "file_contains",
        not bad,
        f"{spec['path']}: missing substrings {bad}" if bad else "all substrings found",
    )


def _judge_file_equals(workdir: Path, spec: dict[str, Any]) -> JudgeResult:
    path = workdir / str(spec["path"])
    if not path.exists():
        return JudgeResult("file_equals", False, f"{spec['path']} missing")
    actual = path.read_text(encoding="utf-8")
    want = str(spec.get("content", ""))
    ok = actual.strip() == want.strip()
    return JudgeResult(
        "file_equals",
        ok,
        "exact match" if ok else f"content mismatch (got {len(actual)} chars)",
    )


def _judge_run_command(workdir: Path, spec: dict[str, Any]) -> JudgeResult:
    import shlex

    # ``{python}`` 占位符展开为当前解释器，避免不同环境下 ``python`` 指向不一致。
    cmd = shlex.split(str(spec["command"]).replace("{python}", f'"{sys.executable}"'))
    proc = run_bounded(
        cmd,
        cwd=workdir,
        timeout=int(spec.get("timeout_sec", 120)),
    )
    expected_rc = int(spec.get("expect_returncode", 0))
    out = (proc.stdout + proc.stderr)[-2000:]
    output_ok = proc.returncode == expected_rc and not proc.timed_out
    contains_bad = [s for s in spec.get("expect_output_contains", []) if s not in proc.stdout]
    passed = output_ok and not contains_bad
    detail = f"rc={proc.returncode} (want {expected_rc})"
    if contains_bad:
        detail += f"; missing output: {contains_bad}"
    return JudgeResult("run_command", passed, f"{detail}\n{out[-800:]}")


def _judge_tool_sequence(
    workdir: Path, spec: dict[str, Any], tool_calls: list[ToolCall]
) -> JudgeResult:
    """Check the required tools were used, accepting equivalent alternatives.

    A scenario may declare ``alternatives`` for a step: any call whose name is
    in that set satisfies it, and ``args_optional`` drops the argument check for
    steps where several equivalent paths are valid. Real models legitimately
    reach the right answer by reading the one file that exists instead of
    globbing first; failing them for that measures the script, not the model.
    """
    expected: list[dict[str, Any]] = list(spec.get("calls", []))
    idx = 0
    matched: list[str] = []
    for call in tool_calls:
        if idx >= len(expected):
            break
        want = expected[idx]
        allowed = {want.get("name"), *(want.get("alternatives") or [])}
        if call.name not in allowed:
            continue
        args_ok = all(
            str(v) in str(call.arguments.get(k, ""))
            for k, v in (want.get("args_contains") or {}).items()
        )
        if args_ok or want.get("args_optional"):
            matched.append(
                f"{call.name}({call.arguments.get('path') or call.arguments.get('command') or ''})"
            )
            idx += 1
    ok = idx == len(expected)
    return JudgeResult(
        "tool_sequence",
        ok,
        f"matched {idx}/{len(expected)}: {matched}" if matched else f"matched 0/{len(expected)}",
    )


def _judge_final_text(workdir: Path, spec: dict[str, Any], final_text: str) -> JudgeResult:
    bad = [s for s in spec.get("contains", []) if s not in (final_text or "")]
    return JudgeResult(
        "final_text_contains",
        not bad,
        f"missing: {bad}" if bad else f"final text = {final_text[:120]!r}",
    )


def _judge_statement_min_chars(workdir: Path, spec: dict[str, Any], prompt: str) -> JudgeResult:
    want = int(spec.get("min_chars", 100))
    ok = len(prompt or "") >= want
    return JudgeResult(
        "statement_min_chars",
        ok,
        f"prompt {len(prompt or '')} chars (want >= {want})",
    )


def judge(
    scenario: Scenario,
    workdir: Path,
    tool_calls: list[ToolCall],
    final_text: str,
    turn_error: str | None,
) -> list[JudgeResult]:
    results: list[JudgeResult] = []
    for spec in scenario.judges:
        kind = str(spec.get("type"))
        try:
            if kind == "files_exist":
                results.append(_judge_files_exist(workdir, spec))
            elif kind == "file_contains":
                results.append(_judge_file_contains(workdir, spec))
            elif kind == "file_equals":
                results.append(_judge_file_equals(workdir, spec))
            elif kind == "run_command":
                results.append(_judge_run_command(workdir, spec))
            elif kind == "tool_sequence":
                results.append(_judge_tool_sequence(workdir, spec, tool_calls))
            elif kind == "final_text_contains":
                results.append(_judge_final_text(workdir, spec, final_text))
            elif kind == "statement_min_chars":
                results.append(_judge_statement_min_chars(workdir, spec, scenario.prompt))
            else:
                results.append(JudgeResult(kind, False, f"unknown judge type: {kind}"))
        except Exception as exc:  # noqa: BLE001 - judge 自身异常也算失败
            results.append(JudgeResult(kind, False, f"judge error: {exc}"))
    # turn 级判定：expect_turn_error=True 时，turn_error 必须存在
    if scenario.expect_turn_error:
        results.append(
            JudgeResult(
                "turn_error_expected",
                turn_error is not None,
                f"turn_error={turn_error!r}"
                if turn_error
                else "expected a turn_error but turn ended cleanly",
            )
        )
    elif turn_error is not None:
        results.append(JudgeResult("turn_clean", False, f"unexpected turn_error: {turn_error}"))
    else:
        results.append(JudgeResult("turn_clean", True, "turn ended with turn_end"))
    return results


async def _run_scenario_async(scenario: Scenario, workdir: Path) -> ScenarioResult:
    for rel, content in scenario.setup_files.items():
        path = workdir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    cfg = SparkConfig()
    # Real-model mode: a scenario without a scripted transcript drives the
    # configured provider, so the eval measures the actual model instead of a
    # mock that was written to pass. Set SPARK_EVALS_REAL=1 plus the usual
    # SPARK_API_KEY / SPARK_BASE_URL / SPARK_MODEL.
    real = scenario.script is None or os.environ.get("SPARK_EVALS_REAL") == "1"
    if real:
        from spark.config import load_config
        from spark.providers.factory import create_provider

        cfg = load_config(workdir=workdir)
        cfg.agent.approval = "full-auto"
        cfg.agent.max_tool_rounds = 20
    else:
        cfg.provider.name = "mock"
        cfg.agent.approval = "full-auto"
        cfg.agent.max_tool_rounds = 12
    store = SessionStore(workdir / "evals.db")
    sid = store.create_session(
        workdir, cfg.provider.model if real else "mock", title=f"eval:{scenario.id}"
    )
    ctx = ToolContext(sandbox=WorkdirSandbox(workdir), config=cfg)
    registry = ToolRegistry(ctx)
    loop = AgentLoop(
        workdir=workdir,
        cfg=cfg,
        provider=create_provider(cfg) if real else _build_provider(scenario.script),
        registry=registry,
        store=store,
        session_id=sid,
    )
    started = time.monotonic()
    tool_calls: list[ToolCall] = []
    final_text = ""
    turn_error: str | None = None
    try:
        async for event in loop.iter_turn(scenario.prompt):
            if event.type == "tool_start" and event.tool_call is not None:
                tool_calls.append(event.tool_call)
            elif event.type == "turn_end":
                final_text = event.text or ""
            elif event.type == "turn_error":
                turn_error = event.text or "turn_error"
    except Exception as exc:  # noqa: BLE001 - 场景执行异常直接判负
        return ScenarioResult(
            id=scenario.id,
            suite=scenario.suite,
            passed=False,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-800:]}",
        )
    finally:
        store.close()

    judges = judge(scenario, workdir, tool_calls, final_text, turn_error)
    passed = all(j.passed for j in judges)
    return ScenarioResult(
        id=scenario.id,
        suite=scenario.suite,
        passed=passed,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        judges=judges,
    )


def run_scenario(scenario: Scenario, workdir: Path) -> ScenarioResult:
    """同步入口：在指定 workdir 中跑单个场景。"""
    old_home = os.environ.get("SPARK_HOME")
    os.environ["SPARK_HOME"] = str(workdir / "spark-home")
    try:
        return asyncio.run(_run_scenario_async(scenario, workdir))
    finally:
        if old_home is None:
            os.environ.pop("SPARK_HOME", None)
        else:
            os.environ["SPARK_HOME"] = old_home


def data_file(name: str) -> Path:
    return Path(str(resources.files("spark.evals"))) / "data" / name


def load_real_swe_subset() -> list[Scenario]:
    """加载内嵌的真实 SWE-bench Lite 子集（HF datasets-server 拉取后落盘）。

    这些场景只做 ingest smoke：验证真实行能被 harness 消费、loop 能跑完；
    不求解 issue、不跑真实仓库的 FAIL_TO_PASS（那需要 clone astropy/django at
    base_commit，见 docs/evals.md 的手动路径）。
    """
    path = data_file("swe_lite_real_subset.json")
    rows = json.loads(path.read_text(encoding="utf-8"))
    scenarios: list[Scenario] = []
    for row in rows:
        scenarios.append(
            Scenario(
                id=f"swe-real-{row['instance_id']}",
                suite="swe-lite-real",
                prompt=row["problem_statement"],
                script=[
                    [
                        {
                            "type": "text",
                            "text": f"[mock agent] 已读取 issue {row['instance_id']}（{row['repo']}）。"
                            "CI 内以 mock provider 记录 ingest 通过；真实求解需接仓库环境。",
                        }
                    ]
                ],
                judges=[{"type": "statement_min_chars", "min_chars": 100}],
                metadata={
                    "instance_id": row["instance_id"],
                    "repo": row["repo"],
                    "fail_to_pass": row.get("fail_to_pass", ""),
                    "gold_patch_chars": len(row.get("gold_patch", "")),
                    "source": "HuggingFace princeton-nlp/SWE-bench_Lite (test split, via datasets-server)",
                },
            )
        )
    return scenarios


def run_report(scenarios: list[Scenario], workroot: Path, stream: Any = None) -> dict[str, Any]:
    """跑全部场景并产出汇总报告。返回 dict（同时写报告到 *stream*，默认 stdout）。"""
    out = stream or sys.stdout

    def line(text: str) -> None:
        out.write(text + "\n")

    workroot.mkdir(parents=True, exist_ok=True)
    results: list[ScenarioResult] = []
    for i, scenario in enumerate(scenarios):
        wd = workroot / f"{i:03d}_{scenario.id}"
        wd.mkdir(parents=True, exist_ok=True)
        result = run_scenario(scenario, wd)
        results.append(result)
        mark = "PASS" if result.passed else "FAIL"
        line(f"[{mark}] {result.suite}/{result.id} ({result.elapsed_ms}ms)")
        for j in result.judges:
            if not j.passed:
                line(f"      - {j.name}: {j.detail[:300]}")
        if result.error:
            line(f"      - error: {result.error[:300]}")

    suites: dict[str, dict[str, int]] = {}
    for r in results:
        entry = suites.setdefault(r.suite, {"pass": 0, "fail": 0})
        entry["pass" if r.passed else "fail"] += 1
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    line("-" * 60)
    for name, entry in sorted(suites.items()):
        rate = entry["pass"] * 100 // max(1, entry["pass"] + entry["fail"])
        line(f"suite {name}: {entry['pass']}/{entry['pass'] + entry['fail']} ({rate}%)")
    line(f"TOTAL: {passed}/{total} passed")
    return {
        "total": total,
        "passed": passed,
        "suites": suites,
        "results": [r.to_dict() for r in results],
    }
