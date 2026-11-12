"""pytest 公共配置：每个测试隔离配置目录，避免写坏真实 ~/.spark。"""
from __future__ import annotations

import os

import pytest


def _reset_checkpointer() -> None:
    try:
        from spark.core.turn_store import reset_persistent_checkpointer

        reset_persistent_checkpointer()
    except Exception:  # pragma: no cover - optional import path
        pass


@pytest.fixture(autouse=True, scope="session")
def _no_proxy_for_localhost():
    """Never route loopback traffic through an HTTP proxy.

    Tests start a real server on 127.0.0.1 and talk to it with urllib / httpx.
    Both honour ``HTTP_PROXY`` when it is set, which on a CI runner sends
    loopback requests to an unreachable proxy and blocks until a socket timeout —
    one plausible source of the Windows job stall.
    """
    keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
    saved = {k: os.environ.get(k) for k in keys}
    no_proxy_saved = os.environ.get("NO_PROXY")
    for k in keys:
        os.environ.pop(k, None)
    os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
    os.environ["no_proxy"] = "127.0.0.1,localhost,::1"
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    if no_proxy_saved is None:
        os.environ.pop("NO_PROXY", None)
        os.environ.pop("no_proxy", None)
    else:
        os.environ["NO_PROXY"] = no_proxy_saved
        os.environ["no_proxy"] = no_proxy_saved


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path):
    old = os.environ.get("SPARK_HOME")
    os.environ["SPARK_HOME"] = str(tmp_path / ".spark-home")
    # The durable turn checkpointer is process-wide: one opened against the
    # previous test's home would keep its SQLite connection (bound to the
    # shared runtime loop) alive into the next test.
    _reset_checkpointer()
    yield
    _reset_checkpointer()
    if old is None:
        os.environ.pop("SPARK_HOME", None)
    else:
        os.environ["SPARK_HOME"] = old


@pytest.fixture(autouse=True)
def _close_web_states():
    """Stop scheduler threads and release Chroma clients after every test.

    Many tests build a real ``SparkWebState`` but only some of their fixtures
    call ``automations.stop()``. The leaked scheduler threads and embedded
    Chroma clients accumulated until the full suite hit a native access
    violation; centralising teardown makes the leak impossible to reintroduce.
    """
    yield
    try:
        from spark.web.state import SparkWebState

        SparkWebState.close_all()
    except Exception:  # pragma: no cover - teardown must not mask test failures
        pass
