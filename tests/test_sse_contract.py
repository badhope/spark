"""SSE contract drift gate: every backend TurnEvent type must exist in the
frontend ChatEvent union. Extra frontend-only types (done, etc.) are allowed."""
from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from spark.models import TurnEventType

ROOT = Path(__file__).resolve().parents[1]
TYPES = ROOT / "web" / "src" / "types.ts"


def test_backend_turn_events_are_declared_in_frontend() -> None:
    source = TYPES.read_text(encoding="utf-8")
    start = source.index("export type ChatEvent")
    end = source.index("/** Goal", start)
    block = source[start:end]
    frontend = set(re.findall(r'type:\s*"([a-z_]+)"', block))
    backend = set(get_args(TurnEventType))
    missing = sorted(backend - frontend)
    assert not missing, f"frontend ChatEvent missing backend event types: {missing}"
