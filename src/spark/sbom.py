"""Generate a CycloneDX 1.5 SBOM for the installed Spark environment.

The SBOM intentionally includes the full Python environment rather than only
first-party code: release binaries bundle transitive wheels, and an SBOM that
lists only ``spark-agent`` would be useless for vulnerability response.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

from spark import __version__

_NAME_RE = re.compile(r"^[-_.]+")


def _normalize(name: str) -> str:
    return _NAME_RE.sub("-", name).lower()


def _purl(name: str, version: str) -> str:
    return f"pkg:pypi/{_normalize(name)}@{version}"


def build_sbom() -> dict[str, Any]:
    components: dict[str, dict[str, Any]] = {}
    for dist in metadata.distributions():
        try:
            name = dist.metadata["Name"] or ""
            version = dist.version or ""
        except (KeyError, ValueError):
            continue
        if not name or not version:
            continue
        key = _normalize(name)
        if key in components:
            continue
        components[key] = {
            "type": "library",
            "bom-ref": f"{key}@{version}",
            "name": key,
            "version": version,
            "purl": _purl(name, version),
        }
    # The project itself is the subject, not a dependency.
    components.pop(_normalize("spark-agent"), None)
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "tools": [{"vendor": "Spark", "name": "spark-sbom", "version": __version__}],
            "component": {
                "type": "application",
                "bom-ref": f"spark-agent@{__version__}",
                "name": "spark-agent",
                "version": __version__,
                "purl": _purl("spark-agent", __version__),
            },
        },
        "components": [components[key] for key in sorted(components)],
    }


def write_sbom(path: Path) -> dict[str, Any]:
    data = build_sbom()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data
