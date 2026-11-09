"""AI 审 AI：对 diff 做模型审查（codex Guardian 对标）。

设计约束：

* 审查永远只读 —— 强制 ``approval=plan-only`` + ``sandbox_mode=sandbox-only``，
  模型在审查轮里连写文件都不可执行；
* diff 是唯一输入面 —— 不执行 diff 内容、不注入仓库其它文件（模型可自行
  用只读工具补上下文，但默认只见 diff）；
* 模型输出严格 JSON（summary + findings），解析失败降级为单条 summary
  finding，绝不因模型输出不规范而崩溃。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_MAX_DIFF_CHARS = 80_000


class ReviewError(Exception):
    """diff 收集失败（非 git 目录 / 无变化 / 无效 ref）。"""


@dataclass
class ReviewTarget:
    """审查范围：三选一 —— staged（默认，工作区 vs HEAD）/ commit / base。"""

    mode: str = "staged"  # staged | commit | base
    commit: str = ""
    base: str = ""


@dataclass
class ReviewFinding:
    severity: str  # high | medium | low | info
    file: str = ""
    line: int | None = None
    comment: str = ""


@dataclass
class ReviewResult:
    summary: str = ""
    findings: list[ReviewFinding] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    raw: str = ""


def _run_git(workdir: Path, args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(workdir), *args],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReviewError(f"git 执行失败：{exc}") from exc
    if proc.returncode != 0:
        raise ReviewError((proc.stderr or "git failed").strip()[:400])
    return proc.stdout


def collect_diff(workdir: Path, target: ReviewTarget, run=None) -> str:
    """按 target 收集 unified diff；超出上限截断并在尾部标注。"""
    runner = run or (lambda args: _run_git(workdir, args))
    if target.mode == "commit":
        if not target.commit.strip():
            raise ReviewError("--commit 需要一个 commit/branch 参数")
        args = ["show", "--format=medium", target.commit]
    elif target.mode == "base":
        if not target.base.strip():
            raise ReviewError("--base 需要一个分支或 commit 参数")
        args = ["diff", f"{target.base}...HEAD"]
    else:
        args = ["diff", "HEAD"]
    diff = runner(args)
    if not diff.strip():
        raise ReviewError("没有可审查的变更（工作区与 HEAD 一致）")
    if len(diff) > _MAX_DIFF_CHARS:
        diff = diff[:_MAX_DIFF_CHARS] + "\n…（diff 过长已截断，超出部分未审查）"
    return diff


def diff_stats(workdir: Path, target: ReviewTarget) -> dict:
    """文件数与增删行数（用于展示，失败不阻塞审查）。

    参数按 ``target.mode`` 生成，与 :func:`collect_diff` 实际审查的 diff
    同源——此前固定 ``git diff HEAD``，commit/base 模式下展示的是工作区
    统计，与审查范围不一致（CLI 只能对这两种模式干脆不显示统计）。

    ``git diff --shortstat`` 的真实输出是
    `` 12 files changed, 345 insertions(+), 67 deletions(-)``，早先按
    ``(+)`` 后缀切分会先撞上单词 ``insertions`` 而抛 ValueError —— 用正则。
    """
    if target.mode == "commit":
        stat_args = ["show", "--shortstat", "--format=", target.commit]
        name_args = ["show", "--name-only", "--format=", target.commit]
    elif target.mode == "base":
        stat_args = ["diff", "--shortstat", f"{target.base}...HEAD"]
        name_args = ["diff", "--name-only", f"{target.base}...HEAD"]
    else:
        stat_args = ["diff", "--shortstat", "HEAD"]
        name_args = ["diff", "--name-only", "HEAD"]
    try:
        stat = _run_git(workdir, stat_args)
        files = _run_git(workdir, name_args).splitlines()
    except ReviewError:
        return {}
    import re

    adds = re.search(r"(\d+) insertion", stat)
    drops = re.search(r"(\d+) deletion", stat)
    return {
        "files": len([f for f in files if f.strip()]),
        "added": int(adds.group(1)) if adds else 0,
        "removed": int(drops.group(1)) if drops else 0,
    }


def build_review_prompt(diff: str) -> str:
    return (
        "你是严格的代码审查员。审查下面的 diff，只报告有把握的问题：\n"
        "1) 正确性缺陷与边界条件\n2) 安全问题（注入/越权/密钥泄露/路径逃逸）\n"
        "3) 明显的回归风险与性能问题\n不要吹毛求疵，不要评论风格。"
        "没有问题就返回空 findings 数组。\n\n"
        "严格输出一行 JSON（不要 markdown 围栏、不要其他文字）：\n"
        '{"summary": "一句话总体评价", "findings": '
        '[{"severity": "high|medium|low", "file": "文件路径", "line": 行号或null, '
        '"comment": "问题与建议"}]}\n\n'
        f"```diff\n{diff}\n```"
    )


def _extract_json_object(raw: str) -> dict | None:
    """从模型输出里找第一个**合法** JSON 对象。

    实测真实模型经常这样回：先一段英文思考（"I have enough context…"），
    再给目标 JSON。用 ``find("{")`` + ``rfind("}")`` 取区间会连思考一起
    切片而解析失败；直接对每个 ``{`` 位置尝试 ``raw_decode`` 才能拿到。
    """
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(raw[index:])
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def parse_review(text: str) -> ReviewResult:
    """从模型输出提取 findings JSON；失败降级为单条 info finding。"""
    raw = (text or "").strip()
    result = ReviewResult(raw=raw)
    data: dict | None = None
    if "```" in raw:
        for chunk in raw.split("```"):
            chunk = chunk.strip()
            if chunk.startswith("json"):
                chunk = chunk[4:]
            if chunk.lstrip().startswith("{"):
                data = _extract_json_object(chunk)
                if data is not None:
                    break
    if data is None:
        data = _extract_json_object(raw)
    if data is None:
        result.summary = raw[:2000] or "（模型没有返回可解析的审查结果）"
        result.findings = [ReviewFinding(severity="info", comment=result.summary)]
        return result
    result.summary = str(data.get("summary") or "").strip()
    for item in data.get("findings") or []:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity") or "info").lower()
        if severity not in {"high", "medium", "low", "info"}:
            severity = "info"
        line = item.get("line")
        if isinstance(line, bool):
            line = None
        elif isinstance(line, str):
            # 真实模型常把行号输出成字符串（"line": "42"），此前被静默丢弃。
            digits = line.strip()
            line = int(digits) if digits.lstrip("-").isdigit() else None
        elif not isinstance(line, int):
            line = None
        result.findings.append(
            ReviewFinding(
                severity=severity,
                file=str(item.get("file") or ""),
                line=line,
                comment=str(item.get("comment") or "").strip(),
            )
        )
    if not result.summary and not result.findings:
        result.summary = raw[:2000]
    return result


ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def render_review(result: ReviewResult, header: str) -> str:
    lines = [header, ""]
    if result.summary:
        lines.append(f"总体：{result.summary}")
        lines.append("")
    if not result.findings or all(f.severity == "info" and not f.file for f in result.findings):
        lines.append("未发现需要报告的问题。")
        return "\n".join(lines)
    for f in sorted(result.findings, key=lambda x: ORDER.get(x.severity, 9)):
        loc = f"{f.file}:{f.line}" if f.file and f.line else (f.file or "-")
        lines.append(f"[{f.severity}] {loc} — {f.comment}")
    return "\n".join(lines)
