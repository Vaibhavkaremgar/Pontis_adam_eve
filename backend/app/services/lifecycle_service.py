from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.repositories import OrchestrationEventRepository, OrchestrationSessionRepository
from app.utils.observability import emit_trace

logger = logging.getLogger(__name__)


def _normalize_event_type(event_type: str) -> str:
    return (event_type or "").strip().upper()


def record_job_lifecycle_event(
    *,
    db: Session,
    job_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    source: str = "orchestration",
    session_id: str | None = None,
) -> None:
    normalized_job_id = (job_id or "").strip()
    normalized_event_type = _normalize_event_type(event_type)
    if not normalized_job_id or not normalized_event_type:
        return

    session_repo = OrchestrationSessionRepository(db)
    session = session_repo.get(session_id) if session_id else None
    if session is None:
        session = session_repo.get_by_job(normalized_job_id)

    if session is None:
        emit_trace(
            logger,
            "lifecycle_event_skipped",
            job_id=normalized_job_id,
            event_type=normalized_event_type,
            reason="missing_orchestration_session",
            source=source,
        )
        return

    event_payload = dict(payload or {})
    event_payload.setdefault("jobId", normalized_job_id)
    event_payload.setdefault("sessionId", session.id)
    event_payload.setdefault("eventType", normalized_event_type)
    event_payload.setdefault("source", source)

    try:
        with db.begin_nested():
            OrchestrationEventRepository(db).create(
                session_id=session.id,
                event_type=normalized_event_type,
                event_payload=event_payload,
                source=source,
            )
        emit_trace(
            logger,
            "lifecycle_event_recorded",
            job_id=normalized_job_id,
            session_id=session.id,
            event_type=normalized_event_type,
            source=source,
        )
    except Exception as exc:  # best-effort observability only
        logger.warning(
            "lifecycle_event_write_failed job_id=%s session_id=%s event_type=%s error=%s",
            normalized_job_id,
            session.id,
            normalized_event_type,
            str(exc),
            exc_info=exc,
        )
        emit_trace(
            logger,
            "lifecycle_event_write_failed",
            job_id=normalized_job_id,
            session_id=session.id,
            event_type=normalized_event_type,
            source=source,
            error=type(exc).__name__,
        )
