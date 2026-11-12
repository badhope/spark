"""Control-plane CLI/settings safety checks."""
from __future__ import annotations

import pytest

from spark.control.cli import serve
from spark.control.settings import ControlSettings


def test_dev_auth_refuses_non_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPARK_CONTROL_AUTH_MODE", "dev")
    monkeypatch.setenv("SPARK_CONTROL_ALLOW_DEV_AUTH", "true")
    with pytest.raises(SystemExit):
        serve(host="0.0.0.0", port=18800)


def test_oidc_mode_requires_issuer_and_audience() -> None:
    settings = ControlSettings(auth_mode="oidc", allow_dev_auth=False)
    with pytest.raises(ValueError):
        settings.validate_runtime()
