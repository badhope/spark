#!/usr/bin/env python3
"""Verify release assets before upload: required names, complete hashes, SBOM shape.

This closes the silent-failure gap where a build matrix job uploads nothing, the
merge step has no asset, and `sha256sum` merely lists what remains. The script is
also a local pre-flight for a dry-run release directory.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

EXPECTED = {
    "spark-windows-x86_64.zip",
    "spark-linux-x86_64.tar.gz",
    "spark-macos-arm64.tar.gz",
    "spark-sbom.json",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_sums(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 64:
            out[parts[-1].lstrip("*")] = parts[0].lower()
    return out


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else "dist-assets")
    if not root.is_dir():
        print(f"missing asset directory: {root}", file=sys.stderr)
        return 2
    sums_path = root / "SHA256SUMS"
    if not sums_path.is_file():
        print("missing SHA256SUMS", file=sys.stderr)
        return 2
    sums = parse_sums(sums_path)
    failures: list[str] = []
    for name in sorted(EXPECTED):
        path = root / name
        if not path.is_file():
            failures.append(f"missing asset: {name}")
            continue
        digest = sha256(path)
        if sums.get(name) != digest:
            failures.append(f"hash mismatch/missing in SHA256SUMS: {name}")
        if name == "spark-sbom.json":
            try:
                bom = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                failures.append("spark-sbom.json is not valid JSON")
                continue
            if bom.get("bomFormat") != "CycloneDX" or bom.get("specVersion") != "1.5":
                failures.append("spark-sbom.json is not CycloneDX 1.5")
            if not bom.get("components"):
                failures.append("spark-sbom.json has no components")
    extra = sorted(
        p.name for p in root.iterdir() if p.is_file() and p.name not in EXPECTED and p.name != "SHA256SUMS"
    )
    if extra:
        failures.append(f"unexpected assets: {', '.join(extra)}")
    if failures:
        print("\n".join(f"FAIL: {item}" for item in failures), file=sys.stderr)
        return 1
    print(f"OK: {len(EXPECTED)} release assets verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
