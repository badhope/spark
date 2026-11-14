"""Regression: large diffs are reviewed in chunks instead of timing out."""
from __future__ import annotations

from spark.review import ReviewFinding, ReviewResult, build_review_prompt, merge_reviews, split_diff


def test_small_diff_is_passed_through() -> None:
    diff = "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
    assert split_diff(diff, limit=1000) == [diff]


def test_large_diff_splits_on_file_boundaries() -> None:
    diff = "".join(
        f"diff --git a/f{i}.py b/f{i}.py\n@@ -1 +1 @@\n" + "+line\n" * 60
        for i in range(20)
    )
    chunks = split_diff(diff, limit=4000)
    assert len(chunks) > 1
    assert all(len(chunk) <= 4000 for chunk in chunks)
    assert all("diff --git" in chunk for chunk in chunks)
    assert "".join(chunks).count("diff --git") == 20


def test_single_huge_file_keeps_header_on_every_slice() -> None:
    diff = "diff --git a/big.py b/big.py\n@@ -1 +1 @@\n" + "+x\n" * 5000
    chunks = split_diff(diff, limit=3000)
    assert len(chunks) > 1
    assert all(chunk.startswith("diff --git a/big.py") for chunk in chunks)


def test_prompt_mentions_chunk_scope() -> None:
    prompt = build_review_prompt("diff", part=(2, 5))
    assert "2/5" in prompt


def test_merge_deduplicates_and_orders_by_severity() -> None:
    low = ReviewFinding(severity="low", file="a.py", line=1, comment="minor")
    high = ReviewFinding(severity="high", file="b.py", line=2, comment="bad")
    merged = merge_reviews(
        [
            ReviewResult(summary="one", findings=[low]),
            ReviewResult(summary="two", findings=[low, high]),
        ]
    )
    assert [f.severity for f in merged.findings] == ["high", "low"]
    assert "one" in merged.summary and "two" in merged.summary
