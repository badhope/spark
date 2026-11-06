"""版本号一致性：源码树、打包元数据、各入口必须说同一个版本。

踩过的坑：`pyproject.toml` 改成 0.13.0 后，可编辑安装留下的
``spark_agent-0.12.0.dist-info`` 仍被 ``importlib.metadata`` 优先读到，
于是 ``spark --version`` 报 0.12.0 —— 发布产物与源码不一致，而且没有任何门禁发现。
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return str(tomllib.load(fh)["project"]["version"])


def test_package_version_matches_pyproject() -> None:
    from spark import __version__

    assert __version__ == _pyproject_version()


def test_vscode_extension_version_matches_pyproject() -> None:
    """The VSIX must not advertise a version the CLI does not have."""
    import json

    pkg = json.loads((ROOT / "vscode-spark" / "package.json").read_text(encoding="utf-8"))
    assert pkg["version"] == _pyproject_version()


def test_changelog_has_an_entry_for_the_current_version() -> None:
    version = _pyproject_version()
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## \[{re.escape(version)}\]", changelog, re.M), (
        f"CHANGELOG.md has no '## [{version}]' section"
    )


def test_mcp_server_reports_the_same_version(tmp_path: Path) -> None:
    from spark import __version__
    from spark.config import SparkConfig
    from spark.mcp_expose import build_mcp_server

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    server = build_mcp_server(str(tmp_path), cfg)
    assert server._mcp_server.version == __version__
