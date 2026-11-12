"""Pydantic request/response models for /api/v1."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=80)


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    slug: str
    name: str
    status: str
    created_at: datetime


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    org_id: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    org_id: str | None
    name: str
    description: str
    status: str
    created_at: datetime
    updated_at: datetime


class TaskCreate(BaseModel):
    project_id: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=300)
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    project_id: str
    title: str
    status: str
    payload: dict[str, Any]
    created_by: str
    created_at: datetime
    updated_at: datetime


class ArtifactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    kind: str = "file"
    uri: str = ""
    sha256: str = ""
    size: int = 0


class ArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tenant_id: str
    task_id: str
    name: str
    kind: str
    uri: str
    sha256: str
    size: int
    created_at: datetime


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tenant_id: str
    actor: str
    action: str
    resource: str
    decision: str
    meta: dict[str, Any]
    created_at: datetime


class MeetingCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    transcript: str = ""
    project_id: str | None = None
    include_docx: bool = True
    #: Optional filename under SPARK_CONTROL_DOCUMENT_ROOT; its text is appended
    #: to the transcript before parsing.
    document_filename: str | None = None


class DocumentExtractRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=300)


class ImapFetchRequest(BaseModel):
    limit: int = Field(default=5, ge=1, le=20)
    mailbox: str = Field(default="", max_length=200)
    search: str = Field(default="ALL", max_length=100)


class DocumentOut(BaseModel):
    filename: str
    kind: str
    text: str
    parts: int
    chars: int
    warnings: list[str] = Field(default_factory=list)


class FetchedEmailOut(BaseModel):
    filename: str
    subject: str
    kind: str
    text: str
    chars: int
    warnings: list[str] = Field(default_factory=list)


class ImapFetchOut(BaseModel):
    fetched: int
    documents: list[FetchedEmailOut]


class MeetingListItem(BaseModel):
    task_id: str
    title: str
    status: str
    created_at: datetime
    action_count: int = 0
    decision_count: int = 0


class MeetingOut(BaseModel):
    task_id: str
    title: str
    status: str
    minutes: dict[str, Any]
    artifacts: list[ArtifactOut] = Field(default_factory=list)


class ReportCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = ""
    metrics: str = Field(min_length=1, description="one metric per line: name,value[,target[,unit]]")
    sections: str = ""
    project_id: str | None = None


class ReportListItem(BaseModel):
    task_id: str
    title: str
    status: str
    created_at: datetime
    metric_count: int = 0


class ReportOut(BaseModel):
    task_id: str
    title: str
    status: str
    report: dict[str, Any]
    artifacts: list[ArtifactOut] = Field(default_factory=list)


class MeOut(BaseModel):
    subject: str
    email: str | None
    roles: list[str]
    tenant_id: str
    auth_mode: str


class HealthOut(BaseModel):
    status: str
    database: str
    auth_mode: str
    gateway_configured: bool


class GatewayStatusOut(BaseModel):
    configured: bool
    provider: str
    model: str
    base_url: str
    fallbacks: list[str]
