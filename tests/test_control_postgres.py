"""Regression: the control plane must work against a real PostgreSQL.

SQLite does not enforce foreign keys by default, so every write silently
succeeded even when ``tenants`` had no row matching the tenant id auth puts on
requests. Against PostgreSQL the same calls failed with
``projects_tenant_id_fkey`` — the deployment target was broken while the test
suite was green.

These tests run only when ``SPARK_TEST_POSTGRES_URL`` is set, so CI without a
database keeps passing; the value must point at a disposable database.
"""

from __future__ import annotations

import os

import pytest

PG_URL = os.environ.get("SPARK_TEST_POSTGRES_URL", "")

pytestmark = pytest.mark.skipif(
    not PG_URL, reason="set SPARK_TEST_POSTGRES_URL to run PostgreSQL contract tests"
)


@pytest.fixture()
def pg_app():
    from spark.control.app import create_app
    from spark.control.settings import ControlSettings

    settings = ControlSettings(
        database_url=PG_URL,
        artifact_root=os.environ.get("SPARK_TEST_ARTIFACT_ROOT", "D:/spark-workspace/.tmp/pg-artifacts"),
        auth_mode="dev",
        allow_dev_auth=True,
        auto_create_tables=False,
    )
    return create_app(settings)


def test_default_tenant_id_matches_auth_tenant(pg_app) -> None:
    """The seeded tenant's primary key must equal the id requests carry."""
    from fastapi.testclient import TestClient

    from spark.control.models import Tenant

    with TestClient(pg_app) as client:
        db = pg_app.state.db
        with db.session() as session:
            row = session.get(Tenant, "default")
            assert row is not None, "default tenant missing after startup"
            assert row.slug == "default"
        # auth in dev mode reports tenant "default"
        me = client.get("/api/v1/me")
        assert me.status_code == 200
        assert me.json().get("tenant_id") == "default"


def test_writes_succeed_under_foreign_keys(pg_app) -> None:
    """Project / task / meeting writes must not violate the tenant FK."""
    from fastapi.testclient import TestClient

    with TestClient(pg_app) as client:
        created = client.post("/api/v1/projects", json={"name": "PG contract"})
        assert created.status_code == 201, created.text
        project_id = created.json()["id"]

        task = client.post(
            "/api/v1/tasks",
            json={"project_id": project_id, "title": "PG task"},
        )
        assert task.status_code in (200, 201), task.text

        meeting = client.post(
            "/api/v1/office/meetings",
            json={
                "title": "PG 会议",
                "transcript": "参会：甲\n决定：通过\n行动项：验证，负责人：甲，截止：2026-12-01",
            },
        )
        assert meeting.status_code == 201, meeting.text
        assert meeting.json()["minutes"]["decisions"]


def test_office_downloads_work_against_postgres(pg_app) -> None:
    """Artifacts are looked up through the same FK chain; verify one download."""
    from fastapi.testclient import TestClient

    with TestClient(pg_app) as client:
        created = client.post(
            "/api/v1/office/meetings",
            json={
                "title": "PG 下载",
                "transcript": "参会：乙\n决定：交付\n行动项：补文档，负责人：乙，截止：2026-12-02",
            },
        )
        assert created.status_code == 201, created.text
        task_id = created.json()["task_id"]
        for fmt, magic in (("md", b"#"), ("docx", b"PK"), ("pdf", b"%PDF")):
            resp = client.get(f"/api/v1/office/meetings/{task_id}/download?format={fmt}")
            assert resp.status_code == 200, f"{fmt}: {resp.text[:120]}"
            assert resp.content.startswith(magic), fmt
