"""Serve the enterprise control plane."""

from __future__ import annotations

from spark.control.settings import ControlSettings

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def serve(*, host: str = "127.0.0.1", port: int = 18800, reload: bool = False) -> None:
    settings = ControlSettings()
    settings.validate_runtime()
    if settings.auth_mode == "dev" and host not in _LOOPBACK:
        raise SystemExit(
            "refusing to bind dev auth beyond loopback; "
            "set SPARK_CONTROL_AUTH_MODE=oidc for a network bind"
        )
    try:
        import uvicorn
    except Exception as exc:  # pragma: no cover - enterprise extra missing
        raise SystemExit(
            "企业控制面依赖未安装：pip install 'spark-agent[enterprise]'"
        ) from exc
    uvicorn.run(
        "spark.control.app:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )
