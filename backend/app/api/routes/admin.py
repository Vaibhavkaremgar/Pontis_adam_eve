from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query
from fastapi import HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import require_role
from app.core.config import config_diagnostics
from app.db.session import get_db
from app.models.entities import AllowedUserEntity
from app.services.audit_service import record_audit_event
from app.services.platform_ops_service import (
    force_embedding_migration,
    get_outreach_analytics,
    get_notification_center,
    get_platform_diagnostics,
    get_pipeline_analytics,
    get_pipeline_board,
    get_recruiter_learning_state,
    inspect_audit_logs,
    inspect_dead_letters,
    refresh_candidate_manually,
    replay_dead_letter,
    list_recent_platform_events,
)
from app.services.operational_intelligence_service import get_operational_intelligence_snapshot
from app.services.automation_service import list_automation_jobs
from app.db.repositories import NotificationEventRepository, RecruiterNoteRepository, RecruiterTaskRepository
from app.utils.responses import success_response

router = APIRouter(prefix="/admin", tags=["admin"])
ops_access = Depends(require_role("admin", "internal_ops"))
admin_access = Depends(require_role("admin"))


class AllowlistUpsertPayload(BaseModel):
    email: str
    note: str | None = None


def _require_admin_request(request: Request) -> dict:
    user = getattr(request.state, "user", None) or {}
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    return user


@router.post("/allowlist")
def add_allowlisted_user(
    payload: AllowlistUpsertPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    admin_user = _require_admin_request(request)
    email = (payload.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="email is required")
    admin_id = UUID(str(admin_user.get("id"))) if admin_user.get("id") else None

    row = db.scalar(select(AllowedUserEntity).where(AllowedUserEntity.email == email))
    now_note = (payload.note or "").strip() or None
    if not row:
        row = AllowedUserEntity(
            id=uuid4(),
            email=email,
            added_by=admin_id,
            note=now_note,
            is_active=True,
        )
        db.add(row)
    else:
        row.added_by = admin_id
        row.note = now_note if now_note is not None else row.note
        row.is_active = True

    db.flush()
    db.refresh(row)
    record_audit_event(
        db=db,
        actor_id=admin_user.get("id"),
        action="admin_allowlist_add",
        entity_type="allowed_user",
        entity_id=email,
        metadata={"email": email, "note": row.note, "isActive": row.is_active},
    )
    db.commit()
    return success_response(
        {
            "id": str(row.id),
            "email": row.email,
            "addedBy": str(row.added_by or ""),
            "note": row.note,
            "isActive": row.is_active,
            "createdAt": row.created_at.isoformat() if row.created_at else None,
        }
    )


@router.delete("/allowlist/{email}")
def deactivate_allowlisted_user(
    email: str,
    request: Request,
    db: Session = Depends(get_db),
):
    admin_user = _require_admin_request(request)
    normalized = (email or "").strip().lower()
    if not normalized:
        raise HTTPException(status_code=400, detail="email is required")

    row = db.scalar(select(AllowedUserEntity).where(AllowedUserEntity.email == normalized))
    if row:
        row.is_active = False
        db.flush()
        record_audit_event(
            db=db,
            actor_id=admin_user.get("id"),
            action="admin_allowlist_deactivate",
            entity_type="allowed_user",
            entity_id=normalized,
            metadata={"email": normalized, "isActive": row.is_active},
        )
        db.commit()
    return success_response(
        {
            "updated": bool(row),
            "email": normalized,
            "isActive": bool(row.is_active) if row else False,
        }
    )


@router.get("/allowlist")
def list_allowlisted_users(
    request: Request,
    db: Session = Depends(get_db),
):
    _require_admin_request(request)
    rows = db.scalars(select(AllowedUserEntity).order_by(AllowedUserEntity.created_at.desc())).all()
    return success_response(
        [
            {
                "id": str(row.id),
                "email": row.email,
                "addedBy": str(row.added_by or ""),
                "note": row.note,
                "isActive": row.is_active,
                "createdAt": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    )


@router.get("/diagnostics")
def diagnostics(_: dict = ops_access, db: Session = Depends(get_db)):
    return success_response(get_platform_diagnostics(db))


@router.get("/config/diagnostics")
def config_only(_: dict = ops_access):
    return success_response(config_diagnostics())


@router.get("/queue/deadletters")
def deadletters(
    queueType: str | None = Query(None, alias="queueType"),
    limit: int = Query(100, ge=1, le=500),
    _: dict = ops_access,
):
    return success_response(inspect_dead_letters(queue_type=queueType, limit=limit))


@router.post("/queue/deadletters/replay")
def replay_deadletter(
    queueType: str = Query(..., alias="queueType"),
    jobId: str = Query(..., alias="jobId"),
    user: dict = ops_access,
    db: Session = Depends(get_db),
):
    result = replay_dead_letter(queueType, jobId)
    record_audit_event(
        db=db,
        actor_id=user.get("id"),
        action="admin_replay_dead_letter",
        entity_type="queue",
        entity_id=f"{queueType}:{jobId}",
        metadata={"queueType": queueType, "jobId": jobId, **result},
    )
    db.commit()
    return success_response(result)


@router.post("/candidates/refresh")
def refresh_candidate(
    jobId: str = Query(..., alias="jobId"),
    candidateId: str = Query(..., alias="candidateId"),
    user: dict = ops_access,
    db: Session = Depends(get_db),
):
    result = refresh_candidate_manually(db, jobId, candidateId)
    record_audit_event(
        db=db,
        actor_id=user.get("id"),
        action="admin_refresh_candidate",
        entity_type="candidate_profile",
        entity_id=f"{jobId}:{candidateId}",
        metadata={"jobId": jobId, "candidateId": candidateId, **result},
    )
    db.commit()
    return success_response(result)


@router.get("/recruiters/{recruiter_id}/learning")
def recruiter_learning(
    recruiter_id: str,
    _: dict = ops_access,
    db: Session = Depends(get_db),
):
    return success_response(get_recruiter_learning_state(db, recruiter_id))


@router.get("/outreach/analytics")
def outreach_analytics(
    jobId: str | None = Query(None, alias="jobId"),
    _: dict = ops_access,
    db: Session = Depends(get_db),
):
    return success_response(get_outreach_analytics(db, job_id=jobId))


@router.get("/audit")
def audit_logs(
    limit: int = Query(100, ge=1, le=500),
    _: dict = admin_access,
    db: Session = Depends(get_db),
):
    return success_response(inspect_audit_logs(db, limit=limit))


@router.get("/notifications")
def notifications(
    jobId: str | None = Query(None, alias="jobId"),
    unreadOnly: bool = Query(False, alias="unreadOnly"),
    limit: int = Query(100, ge=1, le=500),
    _: dict = ops_access,
    db: Session = Depends(get_db),
):
    return success_response(get_notification_center(db, job_id=jobId, unread_only=unreadOnly, limit=limit))


@router.post("/notifications/read")
def mark_notification_read(
    notificationKey: str = Query(..., alias="notificationKey"),
    _: dict = ops_access,
    db: Session = Depends(get_db),
):
    row = NotificationEventRepository(db).mark_read(notificationKey)
    if row:
        record_audit_event(
            db=db,
            actor_id=None,
            action="notification_read",
            entity_type="notification",
            entity_id=row.id,
            metadata={"notificationKey": notificationKey},
        )
        db.commit()
    return success_response({"read": bool(row), "notificationKey": notificationKey})


@router.get("/pipeline/board")
def pipeline_board(
    jobId: str | None = Query(None, alias="jobId"),
    _: dict = ops_access,
    db: Session = Depends(get_db),
):
    return success_response(get_pipeline_board(db, job_id=jobId))


@router.get("/pipeline/analytics")
def pipeline_analytics(
    jobId: str | None = Query(None, alias="jobId"),
    _: dict = ops_access,
    db: Session = Depends(get_db),
):
    return success_response(get_pipeline_analytics(db, job_id=jobId))


@router.get("/tasks")
def recruiter_tasks(
    jobId: str | None = Query(None, alias="jobId"),
    _: dict = ops_access,
    db: Session = Depends(get_db),
):
    if not jobId:
        return success_response([])
    rows = RecruiterTaskRepository(db).list_for_job(jobId, status="open", limit=100)
    return success_response(
        [
            {
                "id": row.id,
                "jobId": row.job_id,
                "candidateId": row.candidate_id,
                "title": row.title,
                "body": row.body,
                "status": row.status,
                "priority": row.priority,
                "dueAt": row.due_at.isoformat() if row.due_at else None,
                "metadata": row.metadata_json,
                "createdAt": row.created_at.isoformat(),
                "updatedAt": row.updated_at.isoformat(),
            }
            for row in rows
        ]
    )


@router.get("/notes")
def recruiter_notes(
    jobId: str = Query(..., alias="jobId"),
    candidateId: str | None = Query(None, alias="candidateId"),
    _: dict = ops_access,
    db: Session = Depends(get_db),
):
    rows = RecruiterNoteRepository(db).list_for_job(jobId, candidate_id=candidateId, limit=100)
    return success_response(
        [
            {
                "id": row.id,
                "jobId": row.job_id,
                "candidateId": row.candidate_id,
                "recruiterId": row.recruiter_id,
                "noteType": row.note_type,
                "body": row.body,
                "status": row.status,
                "metadata": row.metadata_json,
                "createdAt": row.created_at.isoformat(),
                "updatedAt": row.updated_at.isoformat(),
            }
            for row in rows
        ]
    )


@router.post("/notes")
def create_recruiter_note(
    payload: dict,
    user: dict = ops_access,
    db: Session = Depends(get_db),
):
    job_id = str(payload.get("jobId") or "").strip()
    body = str(payload.get("body") or "").strip()
    if not job_id or not body:
        return success_response({"created": False, "error": "jobId and body are required"})
    row = RecruiterNoteRepository(db).create(
        job_id=job_id,
        candidate_id=str(payload.get("candidateId") or "").strip() or None,
        recruiter_id=str(user.get("id") or "").strip() or None,
        note_type=str(payload.get("noteType") or "note").strip(),
        body=body,
        metadata=dict(payload.get("metadata") or {}),
    )
    db.commit()
    return success_response(
        {
            "id": row.id,
            "jobId": row.job_id,
            "candidateId": row.candidate_id,
            "body": row.body,
            "noteType": row.note_type,
        }
    )


@router.get("/automation/jobs")
def automation_jobs(
    _: dict = ops_access,
    db: Session = Depends(get_db),
):
    return success_response(list_automation_jobs(db=db, limit=100))


@router.post("/tasks")
def create_recruiter_task(
    payload: dict,
    user: dict = ops_access,
    db: Session = Depends(get_db),
):
    job_id = str(payload.get("jobId") or "").strip()
    title = str(payload.get("title") or "").strip()
    if not job_id or not title:
        return success_response({"created": False, "error": "jobId and title are required"})
    row = RecruiterTaskRepository(db).create(
        job_id=job_id,
        candidate_id=str(payload.get("candidateId") or "").strip() or None,
        recruiter_id=str(user.get("id") or "").strip() or None,
        title=title,
        body=str(payload.get("body") or "").strip(),
        status=str(payload.get("status") or "open").strip(),
        priority=str(payload.get("priority") or "normal").strip(),
        metadata=dict(payload.get("metadata") or {}),
    )
    db.commit()
    return success_response(
        {
            "id": row.id,
            "jobId": row.job_id,
            "candidateId": row.candidate_id,
            "title": row.title,
            "status": row.status,
        }
    )


@router.patch("/tasks/{task_id}/done")
def complete_recruiter_task(
    task_id: str,
    _: dict = ops_access,
    db: Session = Depends(get_db),
):
    row = RecruiterTaskRepository(db).mark_done(task_id)
    if not row:
        return success_response({"updated": False})
    db.commit()
    return success_response({"updated": True, "taskId": row.id, "status": row.status})


@router.get("/events")
def platform_events(
    limit: int = Query(50, ge=1, le=200),
    _: dict = ops_access,
):
    return success_response(list_recent_platform_events(limit=limit))


@router.get("/intelligence")
def operational_intelligence(
    jobId: str | None = Query(None, alias="jobId"),
    candidateId: str | None = Query(None, alias="candidateId"),
    _: dict = ops_access,
    db: Session = Depends(get_db),
):
    return success_response(get_operational_intelligence_snapshot(db=db, job_id=jobId, candidate_id=candidateId))


@router.post("/embedding/migrate")
def migrate_embedding(
    embeddingVersion: str = Query(..., alias="embeddingVersion"),
    vectorSize: int = Query(..., alias="vectorSize", ge=1, le=4096),
    user: dict = admin_access,
    db: Session = Depends(get_db),
):
    result = force_embedding_migration(db, embeddingVersion, vectorSize)
    record_audit_event(
        db=db,
        actor_id=user.get("id"),
        action="admin_embedding_migrate",
        entity_type="embedding_version",
        entity_id=embeddingVersion,
        metadata={"vectorSize": vectorSize, **result},
    )
    db.commit()
    return success_response(result)
