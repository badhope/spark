from __future__ import annotations

from pathlib import Path

from spark.sbom import build_sbom, write_sbom


def test_sbom_is_cyclonedx_and_sorted() -> None:
    data = build_sbom()
    assert data["bomFormat"] == "CycloneDX"
    assert data["specVersion"] == "1.5"
    assert data["metadata"]["component"]["name"] == "spark-agent"
    names = [item["name"] for item in data["components"]]
    assert names == sorted(names)
    assert len(names) > 0
    assert all(item["purl"].startswith("pkg:pypi/") for item in data["components"])


def test_write_sbom_creates_parent(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "sbom.json"
    data = write_sbom(target)
    assert target.is_file()
    assert data["specVersion"] == "1.5"
    assert target.read_text(encoding="utf-8").endswith("\n")
