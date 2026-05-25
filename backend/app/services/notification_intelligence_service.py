from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.repositories import JobRepository, NotificationEventRepository, OrchestrationSessionRepository
from app.services.slack_integration import send_slack_dm_message

logger = logging.getLogger(__name__)


def _normalize(value: str | None) -> str:
    return (value or "").strip()


def resolve_recruiter_slack_user_id(*, db: Session, job_id: str) -> str:
    session = OrchestrationSessionRepository(db).get_by_job(job_id)
    if session and _normalize(session.slack_user_id):
        return _normalize(session.slack_user_id)
    job = JobRepository(db).get(job_id)
    if not job:
        return ""
    company = job.company
    if company and _normalize(getattr(company, "user_id", "")):
        return _normalize(str(company.user_id))
    return ""


def route_recruiter_notification(
    *,
    db: Session,
    job_id: str,
    candidate_id: str | None,
    notification_key: str,
    notification_type: str,
    title: str,
    body: str,
    metadata: dict[str, Any] | None = None,
    prefer_slack_dm: bool = True,
) -> dict[str, Any]:
    recruiter_user_id = resolve_recruiter_slack_user_id(db=db, job_id=job_id)
    notification_repo = NotificationEventRepository(db)
    row = notification_repo.upsert(
        notification_key=notification_key,
        job_id=job_id,
        candidate_id=candidate_id,
        recipient_type="recruiter",
        recipient=recruiter_user_id,
        channel="slack" if recruiter_user_id and prefer_slack_dm else "dashboard",
        title=title,
        body=body,
        status="delivered" if recruiter_user_id and prefer_slack_dm else "queued",
        notification_type=notification_type,
        notification_metadata=dict(metadata or {}),
        delivery_reference=notification_key,
    )

    delivered = False
    if recruiter_user_id and prefer_slack_dm:
        try:
            delivered = bool(
                asyncio.run(
                    send_slack_dm_message(
                        user_id=recruiter_user_id,
                        text=f"{title}\n{body}",
                    )
                )
            )
        except RuntimeError:
            delivered = False
        except Exception as exc:  # pragma: no cover - best effort delivery
            logger.warning("slack_dm_route_failed job_id=%s recruiter_user_id=%s error=%s", job_id, recruiter_user_id, str(exc))
            delivered = False

        if delivered:
            row.channel = "slack"
            row.status = "delivered"
        else:
            row.channel = "dashboard"
            row.status = "queued"
        db.flush()

    return {
        "notificationId": row.id,
        "notificationKey": row.notification_key,
        "channel": row.channel,
        "delivered": delivered,
        "recruiterSlackUserId": recruiter_user_id,
    }
