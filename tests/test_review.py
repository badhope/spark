from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from spark import cli
from spark.review import (
    ReviewFinding,
    ReviewResult,
    ReviewTarget,
    collect_diff,
    parse_review,
    render_review,
)

runner = CliRunner()


def _fake_git(stdout: str):
    return lambda args: stdout


class TestCollectDiff:
    def test_staged_uses_head_diff(self) -> None:
        diff = collect_diff(Path("."), ReviewTarget(), run=_fake_git("diff -- HEAD body"))
        assert diff == "diff -- HEAD body"

    def test_commit_mode_uses_show(self) -> None:
        seen = {}

        def run(args: list[str]) -> str:
            seen["args"] = args
            return "patch"

        collect_diff(Path("."), ReviewTarget(mode="commit", commit="abc123"), run=run)
        assert seen["args"] == ["show", "--format=medium", "abc123"]

    def test_base_mode_uses_triple_dot(self) -> None:
        seen = {}

        def run(args: list[str]) -> str:
            seen["args"] = args
            return "patch"

        collect_diff(Path("."), ReviewTarget(mode="base", base="main"), run=run)
        assert seen["args"] == ["diff", "main...HEAD"]

    def test_empty_diff_raises(self) -> None:
        with pytest.raises(Exception, match="没有可审查的变更"):
            collect_diff(Path("."), ReviewTarget(), run=_fake_git("  \n"))

    def test_missing_commit_arg_raises(self) -> None:
        with pytest.raises(Exception, match="--commit"):
            collect_diff(Path("."), ReviewTarget(mode="commit"), run=_fake_git("x"))

    def test_oversized_diff_is_truncated(self) -> None:
        big = "x" * 90_000
        diff = collect_diff(Path("."), ReviewTarget(), run=_fake_git(big))
        assert len(diff) < 90_000
        assert "截断" in diff


class TestParseReview:
    def test_plain_json(self) -> None:
        result = parse_review(
            json.dumps(
                {
                    "summary": "ok",
                    "findings": [
                        {"severity": "high", "file": "a.py", "line": 3, "comment": "注入"},
                        {"severity": "weird", "comment": "未知等级降级"},
                    ],
                }
            )
        )
        assert result.summary == "ok"
        assert result.findings[0].severity == "high"
        assert result.findings[0].line == 3
        assert result.findings[1].severity == "info"

    def test_json_inside_fence(self) -> None:
        payload = '{"summary": "s", "findings": []}'
        result = parse_review(f"```json\n{payload}\n```\n说明文字")
        assert result.summary == "s"
        assert result.findings == []

    def test_garbage_degrades_to_summary(self) -> None:
        result = parse_review("模型说了一堆不是 JSON 的话")
        assert result.findings[0].severity == "info"
        assert "模型说" in result.findings[0].comment


class TestRenderReview:
    def test_orders_high_first(self) -> None:
        result = ReviewResult(
            summary="总体尚可",
            findings=[
                ReviewFinding(severity="low", file="b.py", line=2, comment="小问题"),
                ReviewFinding(severity="high", file="a.py", line=1, comment="大问题"),
            ],
        )
        out = render_review(result, "header")
        assert out.index("a.py") < out.index("b.py")
        assert "[high]" in out and "[low]" in out

    def test_clean_review(self) -> None:
        out = render_review(ReviewResult(summary="干净"), "header")
        assert "未发现" in out


def test_review_rejects_mock_provider(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cli, "default_home", lambda: home)
    monkeypatch.setenv("SPARK_HOME", str(home))
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text('[provider]\nname = "mock"\nmodel = "mock"\n', encoding="utf-8")
    r = runner.invoke(
        cli.app, ["review", "--config", str(cfg_path), "--workdir", str(tmp_path)]
    )
    assert r.exit_code != 0
    assert "real model" in (r.stdout + (r.stderr or ""))


def test_review_reports_missing_changes(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cli, "default_home", lambda: home)
    monkeypatch.setenv("SPARK_HOME", str(home))
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text(
        '[provider]\nname = "openai_compat"\nbase_url = "https://example.invalid/v1"\n'
        'model = "m"\napi_key = "sk-test-1234567890"\n',
        encoding="utf-8",
    )
    r = runner.invoke(
        cli.app, ["review", "--config", str(cfg_path), "--workdir", str(tmp_path)]
    )
    # tmp_path 不是 git 仓库 → collect_diff 报错而非崩溃
    assert r.exit_code == 1
    assert "git" in (r.stdout + (r.stderr or "")).lower()


def test_diff_stats_parses_real_shortstat(monkeypatch) -> None:
    """真机回归：git diff --shortstat 输出含单词 insertions/deletions。"""
    from spark import review as review_mod

    def fake_run(workdir, args):
        if args[0] == "diff" and "--shortstat" in args:
            return " 12 files changed, 345 insertions(+), 67 deletions(-)"
        return "a.py\nb.py\n"

    monkeypatch.setattr(review_mod, "_run_git", fake_run)
    stats = review_mod.diff_stats(Path("."), ReviewTarget())
    assert stats == {"files": 2, "added": 345, "removed": 67}


def test_diff_stats_tolerates_singular_and_empty(monkeypatch) -> None:
    from spark import review as review_mod

    monkeypatch.setattr(
        review_mod, "_run_git",
        lambda w, a: (" 1 file changed, 1 insertion(+)" if "--shortstat" in a else "a.py\n"),
    )
    assert review_mod.diff_stats(Path("."), ReviewTarget())["added"] == 1


def test_review_cli_reports_stats_end_to_end(tmp_path, monkeypatch) -> None:
    """review 的 stats 计算不再抛异常（此前 ValueError: 'insertions'）。"""
    from spark.review import ReviewTarget, diff_stats

    stats = diff_stats(Path.cwd(), ReviewTarget())
    assert isinstance(stats, dict)
    assert stats.get("added", 0) >= 0


def test_parse_review_skips_preamble_before_json() -> None:
    """真机回归：真实模型先输出一段思考，再给目标 JSON。"""
    raw = (
        'I have enough context. Let me finalize my findings.\n\n'
        '{"summary":"整体尚可","findings":[{"severity":"medium","file":"a.yml",'
        '"comment":"建议改 smithery"}]}'
    )
    result = parse_review(raw)
    assert result.summary == "整体尚可"
    assert result.findings[0].severity == "medium"
    assert result.findings[0].file == "a.yml"


def test_parse_review_ignores_braces_in_preamble() -> None:
    raw = 'note {not json} then {"summary":"ok","findings":[]}'
    result = parse_review(raw)
    assert result.summary == "ok"


def test_reviewer_without_effort_param_still_reviews() -> None:
    """鸭子类型 provider 不接收 effort 时，审查必须照常工作而非静默降级。"""
    import asyncio

    from spark.core.review import review_action
    from spark.models import ChatDelta

    class NoEffortProvider:
        async def stream(self, messages, tools):
            yield ChatDelta(type="text", text='{"verdict": "warn", "reason": "risky"}')

    result = asyncio.new_event_loop().run_until_complete(
        review_action(NoEffortProvider(), "run_shell", {"command": "rm -rf x"}, "/w")
    )
    assert result["verdict"] == "warn"
    assert result["reviewed"] is True
    assert not result.get("degraded")


def test_diff_stats_matches_review_target(monkeypatch) -> None:
    """commit/base 模式的统计必须与被审查的 diff 同源。"""
    from spark import review as review_mod

    calls: list[list[str]] = []

    def fake_run(workdir, args):
        calls.append(list(args))
        if "--shortstat" in args:
            return " 1 file changed, 2 insertions(+)"
        return "a.py\n"

    monkeypatch.setattr(review_mod, "_run_git", fake_run)
    review_mod.diff_stats(Path("."), ReviewTarget(mode="commit", commit="abc123"))
    assert any(a[0] == "show" and "abc123" in a for a in calls), calls
    calls.clear()
    review_mod.diff_stats(Path("."), ReviewTarget(mode="base", base="main"))
    assert any(a[0] == "diff" and "main...HEAD" in a for a in calls), calls


def test_parse_review_accepts_string_line() -> None:
    raw = (
        '{"summary":"s","findings":[{"severity":"low","file":"a.py",'
        '"line":"42","comment":"c"}]}'
    )
    result = parse_review(raw)
    assert result.findings[0].line == 42
