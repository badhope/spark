"""Cross-language symbol graph tests for Python / TS / Go / Rust."""
from __future__ import annotations

from pathlib import Path

from spark.repo_map import RepoMapConfig, build_repo_map
from spark.symbol_graph import build_symbol_graph, format_graph_report
from spark.tools.symbols import find_symbol


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "svc.py").write_text(
        "def helper():\n"
        "    return 1\n\n"
        "def main():\n"
        "    helper()\n",
        encoding="utf-8",
    )
    (tmp_path / "ui.ts").write_text(
        "export function load() { return 1; }\n"
        "export function boot() { load(); }\n",
        encoding="utf-8",
    )
    (tmp_path / "main.go").write_text(
        "package main\n\n"
        "func World() {}\n"
        "func Hello() { World() }\n",
        encoding="utf-8",
    )
    (tmp_path / "lib.rs").write_text(
        "fn world() {}\n"
        "fn hello() { world(); }\n",
        encoding="utf-8",
    )
    (tmp_path / "other.go").write_text(
        "package main\n\n"
        "func Other() { Helper() }\n",
        encoding="utf-8",
    )
    (tmp_path / "helpers.go").write_text(
        "package main\n\n"
        "func Helper() {}\n",
        encoding="utf-8",
    )
    return tmp_path


def test_graph_extracts_four_languages_and_call_edges(tmp_path: Path) -> None:
    graph = build_symbol_graph(_repo(tmp_path))
    names = {symbol.name for symbol in graph.symbols}
    assert {"helper", "main", "load", "boot", "World", "Hello", "world", "hello", "Other", "Helper"} <= names

    by_caller = {(edge.caller_name, edge.callee_name): edge for edge in graph.calls}
    assert by_caller[("main", "helper")].confidence == "same_file"
    assert by_caller[("boot", "load")].confidence == "same_file"
    assert by_caller[("Hello", "World")].confidence == "same_file"
    assert by_caller[("hello", "world")].confidence == "same_file"
    assert by_caller[("Other", "Helper")].confidence == "unique"


def test_callers_callees_and_impact_are_readable(tmp_path: Path) -> None:
    graph = build_symbol_graph(_repo(tmp_path))
    callers = graph.callers("helper")
    assert callers and callers[0]["symbol"] == "main"
    assert callers[0]["path"] == "svc.py"

    callees = graph.callees("Hello")
    assert callees and callees[0]["symbol"] == "World"

    impact = graph.impact("Helper", depth=2)
    assert impact["definitions"][0]["path"] == "helpers.go"
    assert impact["callers"][0]["symbol"] == "Other"
    assert "callers (depth 2)" in format_graph_report(graph, "Helper")


def test_repo_map_includes_go_and_rust_symbols(tmp_path: Path) -> None:
    repo_map = build_repo_map(_repo(tmp_path), RepoMapConfig())
    assert "main.go" in repo_map and "Hello" in repo_map
    assert "lib.rs" in repo_map and "hello" in repo_map


def test_find_symbol_modes_use_graph(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    impact = find_symbol(root, "helper", mode="impact", depth=2)
    assert impact["mode"] == "impact"
    assert impact["result"]["callers"][0]["symbol"] == "main"
    callers = find_symbol(root, "Helper", mode="callers", depth=2)
    assert callers["mode"] == "callers"
    assert callers["result"][0]["symbol"] == "Other"
