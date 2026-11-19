"""Architecture guard for the CLI cold path.

PF-02 made ``spark --version`` independent of config/onboarding. This test
fails if a future top-level import drags pydantic-settings, the agent loop,
providers, Chroma or Playwright back into every CLI invocation.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_MODULES = (
    "pydantic_settings",
    "spark.config",
    "spark.onboard",
    "spark.core.loop",
    "spark.providers",
    "chromadb",
    "playwright",
    "litellm",
)


def test_cli_import_does_not_pull_heavy_runtime_modules() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.sep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", "import spark.cli"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    audit = result.stderr
    for module in FORBIDDEN_MODULES:
        assert f"| {module}" not in audit, f"{module} leaked into spark.cli import"
