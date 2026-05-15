from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import config_diagnostics
from app.db.repositories import CandidateProfileRepository, OutreachEventRepository, RankingRunRepository
from app.models.entities import AuditEventEntity
from app.services.candidate_refresh_service import refresh_candidate
from app.services.embedding_registry_service import promote_embedding_version
from app.services.job_queue_service import (
    list_dead_letter_jobs,
    queue_depth_snapshot,
    queue_health_snapshot,
    replay_dead_letter_job,
)
from app.services.metrics_service import get_metrics_snapshot
from app.services.platform_event_stream import list_recent_platform_events, record_platform_event
from app.services.recruiter_preference_service import get_recruiter_learning_metrics, load_recruiter_preference_profile
from app.services.refresh_scheduler import scheduler_status
from app.services.qdrant_service import qdrant_health_snapshot
from app.services.llm_service import llm_health
from app.services.pdl_service import pdl_health_snapshot

logger = logging.getLogger(__name__)


def get_platform_diagnostics(db: Session) -> dict[str, Any]:
    return {
        "config": config_diagnostics(),
        "db": _db_health_snapshot(db),
        "queue": queue_health_snapshot(),
        "queueDepth": queue_depth_snapshot(),
        "llm": llm_health(),
        "pdl": pdl_health_snapshot(),
        "qdrant": qdrant_health_snapshot(),
        "scheduler": scheduler_status(),
        "metrics": get_metrics_snapshot(),
        "events": list_recent_platform_events(limit=25),
        "outreach": get_outreach_analytics(db),
    }


def _db_health_snapshot(db: Session) -> dict[str, Any]:
    try:
        db.execute(select(1))
        return {"status": "ok", "checked_at": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        return {"status": "down", "error": str(exc), "checked_at": datetime.now(timezone.utc).isoformat()}


def get_outreach_analytics(db: Session, job_id: str | None = None) -> dict[str, Any]:
    repo = OutreachEventRepository(db)
    if job_id:
        rows = repo.list_for_job(job_id)
    else:
        rows = repo.list_recent(limit=500)

    totals = {"queued": 0, "sending": 0, "sent": 0, "failed": 0, "replied": 0, "bounced": 0, "unsubscribed": 0, "opened": 0}
    for row in rows:
        status = (row.status or "").strip().lower()
        if status in totals:
            totals[status] += 1
        if row.responded_at:
            totals["replied"] += 1
        if "bounce" in (row.last_error or "").lower():
            totals["bounced"] += 1
        if "unsubscribe" in (row.last_error or "").lower():
            totals["unsubscribed"] += 1
        if status == "opened":
            totals["opened"] += 1

    sent_total = totals["sent"] or (totals["opened"] + totals["replied"])
    bounce_rate = totals["bounced"] / sent_total if sent_total else 0.0
    reply_rate = totals["replied"] / sent_total if sent_total else 0.0

    return {
        "counts": totals,
        "replyRate": round(reply_rate, 4),
        "bounceRate": round(bounce_rate, 4),
        "total": len(rows),
    }


def get_recruiter_learning_state(db: Session, recruiter_id: str) -> dict[str, Any]:
    return {
        "profile": load_recruiter_preference_profile(db, recruiter_id),
        "metrics": get_recruiter_learning_metrics(db, recruiter_id),
    }


def inspect_audit_logs(db: Session, limit: int = 100) -> list[dict[str, Any]]:
    rows = db.scalars(select(AuditEventEntity).order_by(AuditEventEntity.created_at.desc()).limit(max(1, limit))).all()
    return [
        {
            "id": row.id,
            "actorId": row.actor_id,
            "actorType": row.actor_type,
            "action": row.action,
            "entityType": row.entity_type,
            "entityId": row.entity_id,
            "metadata": row.event_metadata,
            "requestId": row.request_id,
            "createdAt": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


def force_embedding_migration(db: Session, embedding_version: str, vector_size: int, details: dict[str, Any] | None = None) -> dict[str, Any]:
    row = promote_embedding_version(
        db,
        embedding_version=embedding_version,
        vector_size=vector_size,
        details=details or {"source": "admin"},
    )
    record_platform_event(
        event_type="embedding_migrated",
        source="admin",
        db=db,
        entity_type="embedding_version",
        entity_id=embedding_version,
        payload={"vectorSize": vector_size, "status": row.status},
    )
    return {
        "embeddingVersion": row.embedding_version,
        "status": row.status,
        "vectorSize": row.vector_size,
        "details": row.details,
    }


def refresh_candidate_manually(db: Session, job_id: str, candidate_id: str) -> dict[str, Any]:
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if not profile:
        raise ValueError("Candidate not found")
    refreshed = refresh_candidate(db, profile)
    db.commit()
    return {"jobId": job_id, "candidateId": candidate_id, "refreshed": bool(refreshed)}


def replay_dead_letter(queue_type: str, job_id: str) -> dict[str, Any]:
    return replay_dead_letter_job(queue_type, job_id)


def inspect_dead_letters(queue_type: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    return list_dead_letter_jobs(queue_type=queue_type, limit=limit)
