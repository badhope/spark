"""Optional live-provider smoke test.

This is skipped in normal CI: it needs a running Spark Web instance configured
with a real (non-mock) provider. Run it explicitly after starting a local or
remote model endpoint:

    SPARK_REAL_BASE=http://127.0.0.1:8767 \
      .venv/bin/python -m pytest -q tests/test_real_provider_smoke.py

It proves two separate things:
1. Spark Web itself is on a real provider (not demo mode, has credentials).
2. The configured OpenAI-compatible endpoint actually performs inference and
   returns token usage in the response.
"""

from __future__ import annotations

import json
import os
import urllib.request

import pytest


BASE = os.environ.get("SPARK_REAL_BASE", "").rstrip("/")
pytestmark = pytest.mark.skipif(
    not BASE, reason="set SPARK_REAL_BASE to a live Spark Web URL to run this test"
)


def _get_json(url: str, timeout: float = 10.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def test_real_provider_status_and_inference() -> None:
    status = _get_json(f"{BASE}/api/status")
    assert status.get("provider") != "mock", status.get("provider")
    assert status.get("demo_mode") is False, status.get("demo_mode")
    assert status.get("has_api_key") is True, "real provider has no credential"
    assert status.get("model"), "real provider has no model"

    completion = _post_json(
        f"{status['base_url'].rstrip('/')}/chat/completions",
        {
            "model": status["model"],
            "messages": [{"role": "user", "content": "Reply with one short sentence."}],
            "max_tokens": 48,
            "temperature": 0,
        },
        timeout=180,
    )
    choice = completion["choices"][0]["message"]["content"]
    usage = completion.get("usage") or {}
    assert isinstance(choice, str) and choice.strip(), "provider returned no text"
    assert usage.get("total_tokens", 0) > 0, usage
