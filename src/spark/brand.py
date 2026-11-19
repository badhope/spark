"""Lightweight brand strings shared by CLI fast paths and onboarding.

Keeping these here means ``spark --version`` does not need ``spark.config``
(and therefore ``pydantic-settings``) just to print two lines.
"""

from __future__ import annotations

BRAND = "◆ Spark"
TAGLINE = "本地编程智能体 · 代码在你手，密钥在你手"


def version_line(detail: str) -> str:
    return f"{BRAND} {detail}\n  {TAGLINE}"
