from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.repositories import NotificationWorkflowTokenRepository


def create_notification_workflow_token(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    workflow_name: str,
    token: str,
    payload: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    row = NotificationWorkflowTokenRepository(db).create(
        job_id=job_id,
        candidate_id=candidate_id,
        workflow_name=workflow_name,
        token=token,
        payload=payload,
        expires_at=expires_at,
    )
    return {
        "id": row.id,
        "jobId": row.job_id,
        "candidateId": row.candidate_id,
        "workflowName": row.workflow_name,
        "token": row.token,
        "status": row.status,
        "sourceApp": row.source_app,
        "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
        "consumedAt": row.consumed_at.isoformat() if row.consumed_at else None,
    }


def consume_notification_workflow_token(*, db: Session, token: str) -> dict[str, Any] | None:
    row = NotificationWorkflowTokenRepository(db).mark_consumed(token)
    if not row:
        return None
    return {
        "id": row.id,
        "jobId": row.job_id,
        "candidateId": row.candidate_id,
        "workflowName": row.workflow_name,
        "token": row.token,
        "status": row.status,
        "sourceApp": row.source_app,
        "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
        "consumedAt": row.consumed_at.isoformat() if row.consumed_at else None,
    }

