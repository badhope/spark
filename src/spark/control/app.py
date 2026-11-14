"""FastAPI enterprise control plane (/api/v1).

This is a new surface: it does not replace the existing local Web UI. It
provides the tenancy/project/task/artifact/audit API that later office and
business workbenches build on. Local/dev uses SQLite + dev auth; enterprise
uses PostgreSQL + Keycloak by configuration.
"""

from __future__ import annotations

import hashlib
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import HTMLResponse
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from spark.adapters.legacy import LocalArtifactStore
from spark.config import SparkConfig, load_config
from spark.control.ai import OfficeAssistant
from spark.control.auth import AuthContext, get_auth
from spark.control.authz import CasbinAuthorizer
from spark.control.db import Database
from spark.control.documents import (
    MAX_FILE_BYTES,
    SUPPORTED_SUFFIXES,
    DocumentIngestor,
)
from spark.control.email_imap import ImapConnector
from spark.control.gateway import ModelGateway
from spark.control.models import (
    Artifact,
    AuditEvent,
    Organization,
    Project,
    Task,
    Tenant,
)
from spark.control.office import MeetingMinutesService
from spark.control.office_ui import OFFICE_HTML
from spark.control.report import ReportService
from spark.control.schemas import (
    ArtifactCreate,
    ArtifactOut,
    AuditOut,
    DocumentExtractRequest,
    DocumentOut,
    FetchedEmailOut,
    GatewayStatusOut,
    HealthOut,
    ImapFetchOut,
    ImapFetchRequest,
    MeetingCreate,
    MeetingListItem,
    MeetingOut,
    MeOut,
    ProjectCreate,
    ProjectOut,
    ReportCreate,
    ReportListItem,
    ReportOut,
    TaskCreate,
    TaskOut,
    TenantCreate,
    TenantOut,
)
from spark.control.settings import ControlSettings


def _get_session(request: Request):
    db: Database = request.app.state.db
    with db.session() as session:
        yield session


def _require(obj: str, act: str):
    def dependency(
        request: Request,
        auth: Annotated[AuthContext, Depends(get_auth)],
    ) -> AuthContext:
        authorizer: CasbinAuthorizer = request.app.state.authorizer
        if not authorizer.authorize(auth, obj, act):
            raise HTTPException(
                status_code=403,
                detail=f"missing permission: {obj}:{act}",
            )
        return auth

    return dependency


def _audit(
    session: Session,
    auth: AuthContext,
    action: str,
    resource: str,
    *,
    decision: str = "allow",
    **meta: Any,
) -> None:
    session.add(
        AuditEvent(
            tenant_id=auth.tenant_id,
            actor=auth.subject,
            action=action,
            resource=resource,
            decision=decision,
            meta=meta,
        )
    )


def _ensure_default_tenant(db: Database) -> None:
    """Create the tenant whose *id* matches what dev/oidc auth puts on requests.

    Auth resolves the tenant as a slug (``default``) and every table FKs
    ``tenants.id``. Letting ``id`` default to a random value made every write
    fail the FK on PostgreSQL; SQLite does not enforce FKs by default, which is
    why the bug only appeared against a real database.
    """
    with db.session() as session:
        existing = session.get(Tenant, "default")
        if existing is None:
            session.add(Tenant(id="default", slug="default", name="Default"))
            session.commit()


def _tenant_project(session: Session, tenant_id: str, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None or project.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="project not found")
    return project


def _tenant_task(session: Session, tenant_id: str, task_id: str) -> Task:
    task = session.get(Task, task_id)
    if task is None or task.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="task not found")
    return task


def _meeting_task(session: Session, tenant_id: str, task_id: str) -> Task:
    task = _tenant_task(session, tenant_id, task_id)
    if (task.payload or {}).get("kind") != "meeting_minutes":
        raise HTTPException(status_code=404, detail="meeting not found")
    return task


def _meeting_artifacts(session: Session, tenant_id: str, task_id: str) -> list[Artifact]:
    stmt = (
        select(Artifact)
        .where(Artifact.tenant_id == tenant_id, Artifact.task_id == task_id)
        .order_by(Artifact.created_at)
    )
    return list(session.scalars(stmt).all())


def _meeting_out(task: Task, artifacts: list[Artifact]) -> MeetingOut:
    minutes = dict((task.payload or {}).get("minutes") or {})
    return MeetingOut(
        task_id=task.id,
        title=task.title,
        status=task.status,
        minutes=minutes,
        artifacts=[ArtifactOut.model_validate(item) for item in artifacts],
    )


def _report_task(session: Session, tenant_id: str, task_id: str) -> Task:
    task = _tenant_task(session, tenant_id, task_id)
    if (task.payload or {}).get("kind") != "report":
        raise HTTPException(status_code=404, detail="report not found")
    return task


def _report_out(task: Task, artifacts: list[Artifact]) -> ReportOut:
    report = dict((task.payload or {}).get("report") or {})
    return ReportOut(
        task_id=task.id,
        title=task.title,
        status=task.status,
        report=report,
        artifacts=[ArtifactOut.model_validate(item) for item in artifacts],
    )


def _document_path(settings: ControlSettings, filename: str) -> Path:
    root = Path(settings.document_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    candidate = (root / filename).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="document path escapes root") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="document not found")
    return candidate


def _build_router() -> Any:
    from fastapi import APIRouter

    router = APIRouter(prefix="/api/v1")

    @router.get("/health", response_model=HealthOut)
    def health(request: Request) -> HealthOut:
        settings: ControlSettings = request.app.state.settings
        gateway: ModelGateway = request.app.state.gateway
        database = "ok"
        try:
            with request.app.state.db.session() as session:
                session.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 - health reports, never raises
            database = "degraded"
        return HealthOut(
            status="ok" if database == "ok" else "degraded",
            database=database,
            auth_mode=settings.auth_mode,
            gateway_configured=gateway.configured,
        )

    @router.get("/me", response_model=MeOut)
    def me(auth: Annotated[AuthContext, Depends(get_auth)]) -> MeOut:
        return MeOut(
            subject=auth.subject,
            email=auth.email,
            roles=list(auth.roles),
            tenant_id=auth.tenant_id,
            auth_mode=auth.mode,
        )

    @router.get("/models", response_model=GatewayStatusOut)
    def models(
        request: Request,
        _auth: Annotated[AuthContext, Depends(_require("models", "read"))],
    ) -> GatewayStatusOut:
        return GatewayStatusOut(**request.app.state.gateway.status())

    @router.get("/tenants", response_model=list[TenantOut])
    def list_tenants(
        session: Annotated[Session, Depends(_get_session)],
        _auth: Annotated[AuthContext, Depends(_require("tenants", "read"))],
    ) -> list[Tenant]:
        return list(session.scalars(select(Tenant).order_by(Tenant.created_at)).all())

    @router.post("/tenants", response_model=TenantOut, status_code=201)
    def create_tenant(
        payload: TenantCreate,
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("tenants", "write"))],
    ) -> Tenant:
        tenant = Tenant(name=payload.name.strip(), slug=payload.slug.strip().lower())
        session.add(tenant)
        _audit(session, auth, "tenant.create", tenant.slug)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="tenant slug already exists") from exc
        return tenant

    @router.get("/projects", response_model=list[ProjectOut])
    def list_projects(
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("projects", "read"))],
    ) -> list[Project]:
        stmt = (
            select(Project)
            .where(Project.tenant_id == auth.tenant_id)
            .order_by(Project.created_at.desc())
        )
        return list(session.scalars(stmt).all())

    @router.post("/projects", response_model=ProjectOut, status_code=201)
    def create_project(
        payload: ProjectCreate,
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("projects", "write"))],
    ) -> Project:
        if payload.org_id:
            org = session.get(Organization, payload.org_id)
            if org is None or org.tenant_id != auth.tenant_id:
                raise HTTPException(status_code=404, detail="organization not found")
        project = Project(
            tenant_id=auth.tenant_id,
            org_id=payload.org_id,
            name=payload.name.strip(),
            description=payload.description,
        )
        session.add(project)
        _audit(session, auth, "project.create", project.name)
        session.commit()
        return project

    @router.get("/tasks", response_model=list[TaskOut])
    def list_tasks(
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("tasks", "read"))],
        project_id: str | None = Query(default=None),
    ) -> list[Task]:
        stmt = select(Task).where(Task.tenant_id == auth.tenant_id)
        if project_id:
            stmt = stmt.where(Task.project_id == project_id)
        stmt = stmt.order_by(Task.created_at.desc())
        return list(session.scalars(stmt).all())

    @router.post("/tasks", response_model=TaskOut, status_code=201)
    def create_task(
        payload: TaskCreate,
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("tasks", "write"))],
    ) -> Task:
        _tenant_project(session, auth.tenant_id, payload.project_id)
        task = Task(
            tenant_id=auth.tenant_id,
            project_id=payload.project_id,
            title=payload.title.strip(),
            payload=payload.payload,
            created_by=auth.subject,
        )
        session.add(task)
        _audit(session, auth, "task.create", task.title)
        session.commit()
        return task

    @router.get("/tasks/{task_id}", response_model=TaskOut)
    def get_task(
        task_id: str,
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("tasks", "read"))],
    ) -> Task:
        return _tenant_task(session, auth.tenant_id, task_id)

    @router.post("/tasks/{task_id}/artifacts", response_model=ArtifactOut, status_code=201)
    def create_artifact(
        task_id: str,
        payload: ArtifactCreate,
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("artifacts", "write"))],
    ) -> Artifact:
        _tenant_task(session, auth.tenant_id, task_id)
        artifact = Artifact(
            tenant_id=auth.tenant_id,
            task_id=task_id,
            name=payload.name.strip(),
            kind=payload.kind,
            uri=payload.uri,
            sha256=payload.sha256,
            size=payload.size,
        )
        session.add(artifact)
        _audit(session, auth, "artifact.create", artifact.name, task_id=task_id)
        session.commit()
        return artifact

    @router.get("/tasks/{task_id}/artifacts", response_model=list[ArtifactOut])
    def list_artifacts(
        task_id: str,
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("artifacts", "read"))],
    ) -> list[Artifact]:
        _tenant_task(session, auth.tenant_id, task_id)
        stmt = (
            select(Artifact)
            .where(Artifact.tenant_id == auth.tenant_id, Artifact.task_id == task_id)
            .order_by(Artifact.created_at.desc())
        )
        return list(session.scalars(stmt).all())

    @router.get("/audit", response_model=list[AuditOut])
    def list_audit(
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("audit", "read"))],
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[AuditEvent]:
        stmt = (
            select(AuditEvent)
            .where(AuditEvent.tenant_id == auth.tenant_id)
            .order_by(AuditEvent.id.desc())
            .limit(limit)
        )
        return list(session.scalars(stmt).all())

    @router.get("/office/meetings", response_model=list[MeetingListItem])
    def list_meetings(
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("office", "read"))],
    ) -> list[MeetingListItem]:
        stmt = (
            select(Task)
            .where(Task.tenant_id == auth.tenant_id)
            .order_by(Task.created_at.desc())
            .limit(100)
        )
        out: list[MeetingListItem] = []
        for task in session.scalars(stmt).all():
            payload = task.payload or {}
            if payload.get("kind") != "meeting_minutes":
                continue
            minutes = payload.get("minutes") or {}
            out.append(
                MeetingListItem(
                    task_id=task.id,
                    title=task.title,
                    status=task.status,
                    created_at=task.created_at,
                    action_count=len(minutes.get("actions") or []),
                    decision_count=len(minutes.get("decisions") or []),
                )
            )
            if len(out) >= 50:
                break
        return out

    @router.post("/office/meetings", response_model=MeetingOut, status_code=201)
    def create_meeting(
        payload: MeetingCreate,
        request: Request,
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("office", "write"))],
    ) -> MeetingOut:
        project: Project | None = None
        if payload.project_id:
            project = _tenant_project(session, auth.tenant_id, payload.project_id)
        else:
            project = session.scalar(
                select(Project)
                .where(Project.tenant_id == auth.tenant_id)
                .order_by(Project.created_at)
                .limit(1)
            )
            if project is None:
                project = Project(
                    tenant_id=auth.tenant_id,
                    name="Office",
                    description="Auto-created for meeting minutes",
                )
                session.add(project)
                session.flush()

        transcript = payload.transcript.strip()
        if payload.document_filename:
            path = _document_path(request.app.state.settings, payload.document_filename)
            try:
                document = request.app.state.documents.extract(path)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            transcript = (
                f"{transcript}\n\n{document.text}".strip() if transcript else document.text
            )
        if not transcript.strip():
            raise HTTPException(status_code=422, detail="transcript or document is required")

        service: MeetingMinutesService = request.app.state.meetings
        minutes = service.build(payload.title, transcript)
        assistant: OfficeAssistant = request.app.state.assistant
        ai_summary = assistant.minutes_summary(minutes, transcript)
        if ai_summary:
            # 模型只追加摘要层；结构化决议/行动项仍来自确定性解析与来源行号。
            minutes.ai_summary = ai_summary
        minutes_data = minutes.to_dict()
        task = Task(
            tenant_id=auth.tenant_id,
            project_id=project.id,
            title=payload.title.strip(),
            status="draft",
            payload={"kind": "meeting_minutes", "minutes": minutes_data},
            created_by=auth.subject,
        )
        session.add(task)
        session.flush()

        store: LocalArtifactStore = request.app.state.artifacts
        artifacts: list[Artifact] = []

        markdown = minutes.markdown().encode("utf-8")
        md_key = f"{auth.tenant_id}/{task.id}/minutes.md"
        store.put(md_key, markdown)
        md_artifact = Artifact(
            tenant_id=auth.tenant_id,
            task_id=task.id,
            name="minutes.md",
            kind="meeting_minutes_md",
            uri=md_key,
            sha256=hashlib.sha256(markdown).hexdigest(),
            size=len(markdown),
        )
        session.add(md_artifact)
        artifacts.append(md_artifact)

        if payload.include_docx:
            try:
                docx = minutes.docx_bytes()
                docx_key = f"{auth.tenant_id}/{task.id}/minutes.docx"
                store.put(docx_key, docx)
                docx_artifact = Artifact(
                    tenant_id=auth.tenant_id,
                    task_id=task.id,
                    name="minutes.docx",
                    kind="meeting_minutes_docx",
                    uri=docx_key,
                    sha256=hashlib.sha256(docx).hexdigest(),
                    size=len(docx),
                )
                session.add(docx_artifact)
                artifacts.append(docx_artifact)
            except Exception:  # noqa: BLE001 - markdown remains usable
                # python-docx missing or generation failed; the meeting is still
                # created and the markdown artifact is always available.
                pass
            try:
                pptx = minutes.pptx_bytes()
                pptx_key = f"{auth.tenant_id}/{task.id}/minutes.pptx"
                store.put(pptx_key, pptx)
                pptx_artifact = Artifact(
                    tenant_id=auth.tenant_id,
                    task_id=task.id,
                    name="minutes.pptx",
                    kind="meeting_minutes_pptx",
                    uri=pptx_key,
                    sha256=hashlib.sha256(pptx).hexdigest(),
                    size=len(pptx),
                )
                session.add(pptx_artifact)
                artifacts.append(pptx_artifact)
            except Exception:  # noqa: BLE001 - markdown/docx remain usable
                pass
            try:
                pdf = minutes.pdf_bytes()
                pdf_key = f"{auth.tenant_id}/{task.id}/minutes.pdf"
                store.put(pdf_key, pdf)
                pdf_artifact = Artifact(
                    tenant_id=auth.tenant_id,
                    task_id=task.id,
                    name="minutes.pdf",
                    kind="meeting_minutes_pdf",
                    uri=pdf_key,
                    sha256=hashlib.sha256(pdf).hexdigest(),
                    size=len(pdf),
                )
                session.add(pdf_artifact)
                artifacts.append(pdf_artifact)
            except Exception:  # noqa: BLE001 - markdown/docx/pptx remain usable
                pass

        _audit(
            session,
            auth,
            "meeting.minutes.create",
            task.title,
            task_id=task.id,
            actions=len(minutes.actions),
            decisions=len(minutes.decisions),
            ai_summary=bool(ai_summary),
        )
        session.commit()
        return _meeting_out(task, artifacts)

    @router.get("/office/meetings/{task_id}", response_model=MeetingOut)
    def get_meeting(
        task_id: str,
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("office", "read"))],
    ) -> MeetingOut:
        task = _meeting_task(session, auth.tenant_id, task_id)
        return _meeting_out(task, _meeting_artifacts(session, auth.tenant_id, task_id))

    @router.post("/office/meetings/{task_id}/approve", response_model=MeetingOut)
    def approve_meeting(
        task_id: str,
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("office", "write"))],
    ) -> MeetingOut:
        task = _meeting_task(session, auth.tenant_id, task_id)
        task.status = "approved"
        _audit(session, auth, "meeting.minutes.approve", task.title, task_id=task.id)
        session.commit()
        return _meeting_out(task, _meeting_artifacts(session, auth.tenant_id, task_id))

    @router.get("/office/meetings/{task_id}/download")
    def download_meeting(
        task_id: str,
        request: Request,
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("office", "read"))],
        format: Literal["md", "docx", "pptx", "pdf"] = "md",
    ) -> Response:
        _meeting_task(session, auth.tenant_id, task_id)
        kind = f"meeting_minutes_{format}"
        artifact = session.scalar(
            select(Artifact).where(
                Artifact.tenant_id == auth.tenant_id,
                Artifact.task_id == task_id,
                Artifact.kind == kind,
            )
        )
        if artifact is None:
            raise HTTPException(status_code=404, detail=f"{format} artifact not found")
        store: LocalArtifactStore = request.app.state.artifacts
        try:
            data = store.get(artifact.uri)
        except Exception as exc:  # noqa: BLE001 - missing artifact file
            raise HTTPException(status_code=404, detail="artifact file missing") from exc
        media = {
            "md": "text/markdown; charset=utf-8",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "pdf": "application/pdf",
        }[format]
        return Response(
            content=data,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="minutes.{format}"'},
        )

    @router.get("/office/reports", response_model=list[ReportListItem])
    def list_reports(
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("office", "read"))],
    ) -> list[ReportListItem]:
        stmt = (
            select(Task)
            .where(Task.tenant_id == auth.tenant_id)
            .order_by(Task.created_at.desc())
            .limit(100)
        )
        out: list[ReportListItem] = []
        for task in session.scalars(stmt).all():
            payload = task.payload or {}
            if payload.get("kind") != "report":
                continue
            report = payload.get("report") or {}
            out.append(
                ReportListItem(
                    task_id=task.id,
                    title=task.title,
                    status=task.status,
                    created_at=task.created_at,
                    metric_count=len(report.get("metrics") or []),
                )
            )
            if len(out) >= 50:
                break
        return out

    @router.post("/office/reports", response_model=ReportOut, status_code=201)
    def create_report(
        payload: ReportCreate,
        request: Request,
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("office", "write"))],
    ) -> ReportOut:
        project: Project | None = None
        if payload.project_id:
            project = _tenant_project(session, auth.tenant_id, payload.project_id)
        else:
            project = session.scalar(
                select(Project)
                .where(Project.tenant_id == auth.tenant_id)
                .order_by(Project.created_at)
                .limit(1)
            )
            if project is None:
                project = Project(
                    tenant_id=auth.tenant_id,
                    name="Office",
                    description="Auto-created for office reports",
                )
                session.add(project)
                session.flush()

        service: ReportService = request.app.state.reports
        try:
            report = service.build(
                payload.title, payload.summary, payload.metrics, payload.sections
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        assistant: OfficeAssistant = request.app.state.assistant
        ai_sections = assistant.report_sections(report)
        if ai_sections:
            # 模型章节追加在人工章节之后；指标与达成率仍由确定性计算给出。
            report.sections.extend(ai_sections)
        task = Task(
            tenant_id=auth.tenant_id,
            project_id=project.id,
            title=payload.title.strip(),
            status="draft",
            payload={"kind": "report", "report": report.to_dict()},
            created_by=auth.subject,
        )
        session.add(task)
        session.flush()

        store: LocalArtifactStore = request.app.state.artifacts
        artifacts: list[Artifact] = []
        outputs = {
            "report_md": ("report.md", report.markdown().encode("utf-8")),
            "report_xlsx": ("report.xlsx", report.xlsx_bytes()),
            "report_docx": ("report.docx", report.docx_bytes()),
            "report_pptx": ("report.pptx", report.pptx_bytes()),
            "report_pdf": ("report.pdf", report.pdf_bytes()),
        }
        for kind, (name, data) in outputs.items():
            key = f"{auth.tenant_id}/{task.id}/{name}"
            store.put(key, data)
            artifact = Artifact(
                tenant_id=auth.tenant_id,
                task_id=task.id,
                name=name,
                kind=kind,
                uri=key,
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
            )
            session.add(artifact)
            artifacts.append(artifact)

        _audit(
            session,
            auth,
            "report.create",
            task.title,
            task_id=task.id,
            metrics=len(report.metrics),
            ai_sections=len(ai_sections),
        )
        session.commit()
        return _report_out(task, artifacts)

    @router.get("/office/reports/{task_id}", response_model=ReportOut)
    def get_report(
        task_id: str,
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("office", "read"))],
    ) -> ReportOut:
        task = _report_task(session, auth.tenant_id, task_id)
        return _report_out(task, _meeting_artifacts(session, auth.tenant_id, task_id))

    @router.post("/office/reports/{task_id}/approve", response_model=ReportOut)
    def approve_report(
        task_id: str,
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("office", "write"))],
    ) -> ReportOut:
        task = _report_task(session, auth.tenant_id, task_id)
        task.status = "approved"
        _audit(session, auth, "report.approve", task.title, task_id=task.id)
        session.commit()
        return _report_out(task, _meeting_artifacts(session, auth.tenant_id, task_id))

    @router.get("/office/reports/{task_id}/download")
    def download_report(
        task_id: str,
        request: Request,
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("office", "read"))],
        format: Literal["md", "xlsx", "docx", "pptx", "pdf"] = "md",
    ) -> Response:
        _report_task(session, auth.tenant_id, task_id)
        kind = f"report_{format}"
        artifact = session.scalar(
            select(Artifact).where(
                Artifact.tenant_id == auth.tenant_id,
                Artifact.task_id == task_id,
                Artifact.kind == kind,
            )
        )
        if artifact is None:
            raise HTTPException(status_code=404, detail=f"{format} artifact not found")
        store: LocalArtifactStore = request.app.state.artifacts
        try:
            data = store.get(artifact.uri)
        except Exception as exc:  # noqa: BLE001 - missing artifact file
            raise HTTPException(status_code=404, detail="artifact file missing") from exc
        media = {
            "md": "text/markdown; charset=utf-8",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "pdf": "application/pdf",
        }[format]
        return Response(
            content=data,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="report.{format}"'},
        )

    @router.post("/office/documents/upload", response_model=DocumentOut, status_code=201)
    def upload_document(
        request: Request,
        file: Annotated[UploadFile, File(...)],
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("office", "write"))],
    ) -> DocumentOut:
        settings: ControlSettings = request.app.state.settings
        original = Path(file.filename or "").name
        suffix = Path(original).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise HTTPException(
                status_code=422, detail=f"unsupported document type: {suffix or 'unknown'}"
            )
        data = file.file.read(MAX_FILE_BYTES + 1)
        if not data:
            raise HTTPException(status_code=422, detail="uploaded file is empty")
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"document is too large (limit {MAX_FILE_BYTES} bytes)",
            )

        stem = re.sub(
            r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", Path(original).stem
        ).strip("._-")
        safe_name = f"{uuid4().hex[:8]}-{(stem or 'document')[:60]}{suffix}"
        upload_root = Path(settings.document_root).expanduser().resolve() / "uploads"
        upload_root.mkdir(parents=True, exist_ok=True)
        path = upload_root / safe_name
        path.write_bytes(data)

        try:
            document = request.app.state.documents.extract(path)
        except Exception as exc:
            path.unlink(missing_ok=True)
            if isinstance(exc, ValueError):
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if isinstance(exc, RuntimeError):
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            raise

        relative = f"uploads/{safe_name}"
        _audit(
            session,
            auth,
            "document.upload",
            relative,
            chars=len(document.text),
            parts=document.parts,
            bytes=len(data),
        )
        session.commit()
        payload = document.to_dict()
        payload["filename"] = relative
        return DocumentOut(**payload)

    @router.post("/office/documents/imap", response_model=ImapFetchOut)
    def fetch_imap(
        payload: ImapFetchRequest,
        request: Request,
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("office", "write"))],
    ) -> ImapFetchOut:
        connector: ImapConnector = request.app.state.imap
        if not connector.configured:
            raise HTTPException(
                status_code=503,
                detail=(
                    "IMAP is not configured: set SPARK_CONTROL_IMAP_HOST, "
                    "SPARK_CONTROL_IMAP_USER and SPARK_CONTROL_IMAP_PASSWORD"
                ),
            )
        try:
            fetched = connector.fetch_latest(
                limit=payload.limit,
                mailbox=payload.mailbox,
                search=payload.search,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - network/mailbox errors
            raise HTTPException(status_code=502, detail=f"IMAP fetch failed: {exc}") from exc
        _audit(
            session,
            auth,
            "document.imap",
            payload.mailbox or request.app.state.settings.imap_mailbox,
            fetched=len(fetched),
            limit=payload.limit,
            search=payload.search,
        )
        session.commit()
        return ImapFetchOut(
            fetched=len(fetched),
            documents=[
                FetchedEmailOut(
                    filename=item.filename,
                    subject=item.subject,
                    kind=item.document.kind,
                    text=item.document.text,
                    chars=len(item.document.text),
                    warnings=list(item.document.warnings),
                )
                for item in fetched
            ],
        )

    @router.post("/office/documents/extract", response_model=DocumentOut)
    def extract_document(
        payload: DocumentExtractRequest,
        request: Request,
        session: Annotated[Session, Depends(_get_session)],
        auth: Annotated[AuthContext, Depends(_require("office", "read"))],
    ) -> DocumentOut:
        path = _document_path(request.app.state.settings, payload.filename)
        try:
            document = request.app.state.documents.extract(path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        _audit(
            session,
            auth,
            "document.extract",
            payload.filename,
            chars=len(document.text),
            parts=document.parts,
        )
        session.commit()
        return DocumentOut(**document.to_dict())

    return router


def create_app(
    settings: ControlSettings | None = None,
    cfg: SparkConfig | None = None,
    gateway: ModelGateway | None = None,
) -> FastAPI:
    settings = settings or ControlSettings()
    # load_config applies SPARK_BASE_URL / SPARK_MODEL and the user's saved
    # provider profile; SparkConfig() alone would silently keep the defaults.
    cfg = cfg or load_config(workdir=Path.cwd())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.validate_runtime()
        db = Database(settings.database_url)
        app.state.db = db
        if settings.should_create_tables:
            db.create_all()
        # The default tenant must exist even when tables came from Alembic:
        # with it missing every write fails the tenant_id foreign key on
        # PostgreSQL (SQLite never enforced it, so the gap went unnoticed).
        _ensure_default_tenant(db)
        app.state.authorizer = CasbinAuthorizer(settings)
        app.state.gateway = gateway or ModelGateway(cfg)
        app.state.assistant = OfficeAssistant(app.state.gateway, settings)
        app.state.artifacts = LocalArtifactStore(settings.artifact_root)
        app.state.meetings = MeetingMinutesService()
        app.state.reports = ReportService()
        app.state.documents = DocumentIngestor()
        app.state.imap = ImapConnector(settings, app.state.documents)
        try:
            yield
        finally:
            db.close()

    app = FastAPI(
        title="Spark Enterprise Control Plane",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.cfg = cfg
    app.include_router(_build_router())

    @app.get("/office", response_class=HTMLResponse, include_in_schema=False)
    def office_page() -> HTMLResponse:
        return HTMLResponse(OFFICE_HTML)

    return app


app = create_app()
