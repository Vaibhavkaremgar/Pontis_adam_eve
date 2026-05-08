from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.security import require_role
from app.core.config import config_diagnostics
from app.db.session import get_db
from app.services.audit_service import record_audit_event
from app.services.platform_ops_service import (
    force_embedding_migration,
    get_outreach_analytics,
    get_platform_diagnostics,
    get_recruiter_learning_state,
    inspect_audit_logs,
    inspect_dead_letters,
    refresh_candidate_manually,
    replay_dead_letter,
)
from app.utils.responses import success_response

router = APIRouter(prefix="/admin", tags=["admin"])
ops_access = Depends(require_role("admin", "internal_ops"))
admin_access = Depends(require_role("admin"))


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
