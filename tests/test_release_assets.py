from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_release_assets.py"


def _make_assets(root: Path) -> None:
    for name, body in {
        "spark-windows-x86_64.zip": b"win",
        "spark-linux-x86_64.tar.gz": b"linux",
        "spark-macos-arm64.tar.gz": b"mac",
        "spark-sbom.json": json.dumps(
            {"bomFormat": "CycloneDX", "specVersion": "1.5", "components": [{"name": "x"}]}
        ).encode(),
    }.items():
        (root / name).write_bytes(body)
    lines = []
    for name in (
        "spark-windows-x86_64.zip",
        "spark-linux-x86_64.tar.gz",
        "spark-macos-arm64.tar.gz",
        "spark-sbom.json",
    ):
        lines.append(f"{hashlib.sha256((root / name).read_bytes()).hexdigest()}  {name}")
    (root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), str(root)], text=True, capture_output=True)


def test_release_assets_accept_complete_set(tmp_path: Path) -> None:
    _make_assets(tmp_path)
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_release_assets_reject_missing_file(tmp_path: Path) -> None:
    _make_assets(tmp_path)
    (tmp_path / "spark-linux-x86_64.tar.gz").unlink()
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "missing asset" in result.stderr


def test_release_assets_reject_bad_hash(tmp_path: Path) -> None:
    _make_assets(tmp_path)
    (tmp_path / "SHA256SUMS").write_text("0" * 64 + "  spark-windows-x86_64.zip\n")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "hash mismatch" in result.stderr


def test_release_assets_reject_bad_sbom(tmp_path: Path) -> None:
    _make_assets(tmp_path)
    (tmp_path / "spark-sbom.json").write_text("{}")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "CycloneDX" in result.stderr
