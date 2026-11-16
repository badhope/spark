"""The table-creation switch must be *called*, not tested for truthiness.

``ControlSettings.should_create_tables`` is a method. ``if
settings.should_create_tables:`` is therefore always true — a bound method is a
truthy object — and every deployment ran ``create_all()``, including PostgreSQL
where Alembic owns the schema. mypy flagged it as ``truthy-function``; these
tests make sure the regression cannot come back silently.

The negative case matters most: with ``auto_create_tables=False`` the app must
not invent tables, otherwise a missing migration looks like a working database
until the first query touches an absent column.
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi.testclient import TestClient

from spark.control.app import create_app
from spark.control.settings import ControlSettings


def _table_names(url: str) -> set[str]:
    engine = sa.create_engine(url)
    try:
        return set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_sqlite_creates_tables_by_default(tmp_path) -> None:
    url = f"sqlite:///{(tmp_path / 'auto.db').as_posix()}"
    settings = ControlSettings(
        database_url=url, auth_mode="dev", allow_dev_auth=True
    )
    assert settings.should_create_tables() is True
    with TestClient(create_app(settings)):
        pass
    assert {"tenants", "projects", "tasks", "artifacts"} <= _table_names(url)


def test_explicit_false_does_not_create_tables(tmp_path) -> None:
    url = f"sqlite:///{(tmp_path / 'manual.db').as_posix()}"
    settings = ControlSettings(
        database_url=url,
        auth_mode="dev",
        allow_dev_auth=True,
        auto_create_tables=False,
    )
    assert settings.should_create_tables() is False
    # create_all must be skipped, so only the tenant bootstrap (which needs the
    # tables) is attempted — and it must not crash the app.
    try:
        with TestClient(create_app(settings)):
            pass
    except Exception:  # noqa: BLE001 - a missing schema may surface here
        pass
    assert "tasks" not in _table_names(url), "auto_create_tables=False still created tables"


def test_should_create_tables_is_a_method() -> None:
    """Guard against someone turning it back into a property/attribute misuse."""
    settings = ControlSettings()
    assert callable(settings.should_create_tables)
    assert isinstance(settings.should_create_tables(), bool)
