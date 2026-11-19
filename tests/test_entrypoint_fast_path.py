from __future__ import annotations

import sys

from spark.entrypoint import main


def test_version_uses_lightweight_entrypoint(monkeypatch, capsys) -> None:
    """``spark --version`` must not need the full Typer command tree."""
    monkeypatch.setattr(sys, "argv", ["spark", "--version"])
    main()
    out = capsys.readouterr().out
    assert "Spark" in out
    assert "本地编程智能体" in out


def test_version_short_flag(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["spark", "-V"])
    main()
    assert "Spark" in capsys.readouterr().out
