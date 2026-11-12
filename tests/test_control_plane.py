"""Control-plane API tests using SQLite + dev auth (no containers required)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spark.control.app import create_app
from spark.control.settings import ControlSettings


@pytest.fixture()
def client(tmp_path: Path):
    settings = ControlSettings(
        database_url=f"sqlite:///{(tmp_path / 'control.db').as_posix()}",
        auth_mode="dev",
        allow_dev_auth=True,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def _headers(**kwargs: str) -> dict[str, str]:
    mapping = {
        "user": "X-Spark-User",
        "roles": "X-Spark-Roles",
        "tenant": "X-Spark-Tenant",
    }
    return {mapping[key]: value for key, value in kwargs.items()}


def test_health_and_me(client: TestClient) -> None:
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["database"] == "ok"
    assert health.json()["auth_mode"] == "dev"

    me = client.get("/api/v1/me").json()
    assert me["subject"] == "dev"
    assert me["tenant_id"] == "default"
    assert "admin" in me["roles"]


def test_project_task_artifact_flow(client: TestClient) -> None:
    project = client.post("/api/v1/projects", json={"name": "Office"})
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]

    task = client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "title": "会议纪要", "payload": {"source": "meeting"}},
    )
    assert task.status_code == 201, task.text
    task_id = task.json()["id"]

    artifact = client.post(
        f"/api/v1/tasks/{task_id}/artifacts",
        json={"name": "minutes.docx", "kind": "docx", "uri": "artifacts/1"},
    )
    assert artifact.status_code == 201, artifact.text

    tasks = client.get("/api/v1/tasks", params={"project_id": project_id})
    assert tasks.status_code == 200
    assert tasks.json()[0]["title"] == "会议纪要"

    artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts")
    assert artifacts.status_code == 200
    assert artifacts.json()[0]["name"] == "minutes.docx"

    audit = client.get("/api/v1/audit")
    assert audit.status_code == 200
    actions = {row["action"] for row in audit.json()}
    assert {"project.create", "task.create", "artifact.create"} <= actions


def test_member_is_read_only(client: TestClient) -> None:
    member = _headers(roles="member")
    assert client.get("/api/v1/projects", headers=member).status_code == 200
    denied = client.post("/api/v1/projects", json={"name": "Nope"}, headers=member)
    assert denied.status_code == 403


def test_tenant_isolation_and_default_tenant(client: TestClient) -> None:
    assert (
        client.post(
            "/api/v1/tenants",
            json={"name": "Other", "slug": "other"},
        ).status_code
        == 201
    )
    default_project = client.post("/api/v1/projects", json={"name": "Default project"})
    assert default_project.status_code == 201

    other = _headers(tenant="other")
    assert client.get("/api/v1/projects", headers=other).json() == []
    other_project = client.post(
        "/api/v1/projects", json={"name": "Other project"}, headers=other
    )
    assert other_project.status_code == 201

    default_projects = client.get("/api/v1/projects").json()
    assert [p["name"] for p in default_projects] == ["Default project"]


def test_task_cannot_cross_tenant(client: TestClient) -> None:
    project_id = client.post("/api/v1/projects", json={"name": "P"}).json()["id"]
    client.post("/api/v1/tenants", json={"name": "Other", "slug": "other"})
    other = _headers(tenant="other")
    response = client.post(
        "/api/v1/tasks",
        json={"project_id": project_id, "title": "stolen"},
        headers=other,
    )
    assert response.status_code == 404
