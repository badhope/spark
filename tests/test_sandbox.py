from pathlib import Path

import pytest

from spark.config import SparkConfig
from spark.errors import PathEscapeError
from spark.sandbox import SandboxPolicyError, WorkdirSandbox


def _cfg(mode: str = "workspace") -> SparkConfig:
    cfg = SparkConfig()
    cfg.agent.sandbox_mode = mode
    cfg.agent.protected_paths = []
    return cfg


def test_resolve_inside(tmp_path: Path) -> None:
    box = WorkdirSandbox(tmp_path)
    target = box.resolve("src/app.py")
    assert target == (tmp_path / "src" / "app.py").resolve()


def test_reject_escape(tmp_path: Path) -> None:
    box = WorkdirSandbox(tmp_path)
    with pytest.raises(PathEscapeError):
        box.resolve("../secret")


def test_self_source_is_not_readable(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    box = WorkdirSandbox(repo, _cfg())
    with pytest.raises(SandboxPolicyError):
        box.resolve("src/spark/config.py")
    assert box.check_shell("cat src/spark/config.py") is not None


def test_secret_files_are_blocked_but_examples_allowed(tmp_path: Path) -> None:
    box = WorkdirSandbox(tmp_path, _cfg())
    (tmp_path / ".env").write_text("SPARK_API_KEY=secret", encoding="utf-8")
    (tmp_path / ".env.example").write_text("SPARK_API_KEY=", encoding="utf-8")
    (tmp_path / ".secrets").mkdir()
    with pytest.raises(SandboxPolicyError):
        box.resolve(".env")
    with pytest.raises(SandboxPolicyError):
        box.resolve(".secrets")
    assert box.resolve(".env.example").name == ".env.example"


def test_inline_interpreter_code_is_blocked_in_workspace(tmp_path: Path) -> None:
    box = WorkdirSandbox(tmp_path, _cfg("workspace"))
    assert "inline interpreter" in (box.check_shell('python -c "print(1)"') or "")
    assert "inline interpreter" in (box.check_shell('node -e "console.log(1)"') or "")
    assert box.check_shell("python -m pytest -q") is None


def test_inline_interpreter_code_allowed_in_unrestricted(tmp_path: Path) -> None:
    box = WorkdirSandbox(tmp_path, _cfg("unrestricted"))
    assert box.check_shell('python -c "print(1)"') is None
