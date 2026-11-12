"""Enterprise control plane (FastAPI /api/v1)."""

__all__ = ["create_app"]


def create_app(*args, **kwargs):
    from spark.control.app import create_app as _create_app

    return _create_app(*args, **kwargs)
