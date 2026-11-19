#!/usr/bin/env python3
"""Keep the VS Code extension's three version sources in lockstep.

Sources: ``pyproject.toml`` (the product release), ``vscode-spark/package.json``
(the extension manifest) and the checked-in VSIX filename/manifest. A stale
VSIX used to sit in the tree at 0.13.1 while package.json said 0.15.2; users
installing the documented file got a different product than the source.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "vscode-spark"


def product_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("pyproject.toml: version not found")
    return m.group(1)


def extension_manifest() -> dict:
    try:
        return json.loads((EXT / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"vscode-spark/package.json is unreadable: {exc}") from exc


def vsix_version(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as zf:
            raw = zf.read("extension/package.json").decode("utf-8")
        return str(json.loads(raw).get("version") or "") or None
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None


def verify(require_vsix: bool) -> list[str]:
    errors: list[str] = []
    version = product_version()
    manifest = extension_manifest()
    pkg_version = str(manifest.get("version") or "")
    if pkg_version != version:
        errors.append(f"package.json version {pkg_version!r} != pyproject {version!r}")

    vsix_files = sorted(EXT.glob("*.vsix"))
    if not vsix_files:
        if require_vsix:
            errors.append(f"no VSIX present (expected spark-agent-{version}.vsix)")
    for path in vsix_files:
        embedded = vsix_version(path)
        expected_name = f"spark-agent-{version}.vsix"
        if path.name != expected_name:
            errors.append(f"stale VSIX filename {path.name!r}; expected {expected_name!r}")
        if embedded != version:
            errors.append(f"{path.name}: embedded package version {embedded!r} != {version!r}")
    if len(vsix_files) > 1:
        errors.append(f"multiple VSIX files in tree: {[p.name for p in vsix_files]}")

    readme = (EXT / "README.md").read_text(encoding="utf-8")
    if f"spark-agent-{version}.vsix" not in readme:
        errors.append(f"README install command must reference spark-agent-{version}.vsix")
    install = (ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")
    if "vscode-spark/" in install and f"spark-agent-{version}.vsix" not in install:
        errors.append(f"docs/INSTALL.md must reference spark-agent-{version}.vsix")

    for rel in ("extension.js", "media/index.html", "media/main.js", "media/style.css", "src/protocol.js"):
        if not (EXT / rel).is_file():
            errors.append(f"missing extension asset: {rel}")

    commands = [c.get("command") for c in manifest.get("contributes", {}).get("commands", [])]
    if not commands:
        errors.append("package.json contributes no commands")
    if len(commands) != len(set(commands)):
        errors.append("duplicate command ids in package.json")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--require-vsix",
        action="store_true",
        help="fail when the built VSIX is missing (CI packaging job)",
    )
    args = parser.parse_args()
    errors = verify(args.require_vsix)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print(f"VS Code extension versions are in sync at {product_version()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
