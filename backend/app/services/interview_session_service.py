from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
import secrets

from sqlalchemy.orm import Session

from app.core.config import INTERVIEW_SESSION_TTL_MINUTES, PUBLIC_APP_URL
from app.db.repositories import CandidateProfileRepository, CompanyRepository, InterviewRepository, InterviewSessionRepository, JobRepository, NotificationWorkflowTokenRepository
from app.services.candidate_service import ensure_candidate_email
from app.services.notification_service import build_slot_booking_payload, create_notification_workflow_token
from app.services.interview_link_providers import get_interview_link
from app.services.metrics_service import log_metric
from app.services.recruiter_preference_service import update_recruiter_preferences
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)


def _legacy_booking_url(token: str) -> str:
    return f"https://interview.pontis.one/booking.html?token={token}"


def _build_token_payload(*, profile: Any, job: Any, company_name: str = "") -> dict[str, Any]:
    return build_slot_booking_payload(
        candidate=profile,
        job={"title": getattr(job, "title", "") or "", "company_name": company_name or ""},
    )


def _session_payload(*, row, booking_link: str) -> dict[str, str | None]:
    return {
        "id": row.id,
        "jobId": row.job_id,
        "candidateId": row.candidate_id,
        "companyId": getattr(row, "company_id", None),
        "outreachEventId": getattr(row, "outreach_event_id", None),
        "email": row.email,
        "token": row.token,
        "status": row.status,
        "expiresAt": row.expires_at.isoformat(),
        "bookedAt": row.booked_at.isoformat() if row.booked_at else None,
        "bookingLink": booking_link,
        "bookingUrl": booking_link,
        "slotLink": booking_link,
        "slot_link": booking_link,
    }


def create_interview_session(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    outreach_event_id: str | None = None,
    source_app: str = "adam",
) -> dict[str, str | None]:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)

    session_repo = InterviewSessionRepository(db)
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    existing_session = session_repo.get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
    if existing_session and (existing_session.expires_at is None or existing_session.expires_at > datetime.now(timezone.utc)):
        company = CompanyRepository(db).get_by_id(job.company_id)
        if profile:
            token_repo = NotificationWorkflowTokenRepository(db)
            existing_workflow_token = token_repo.get_by_token(existing_session.token, source_app=source_app)
            token_payload = _build_token_payload(profile=profile, job=job, company_name=company.name if company else "")
            if existing_workflow_token:
                existing_workflow_token.payload = token_payload
                existing_workflow_token.expires_at = existing_session.expires_at
                existing_workflow_token.is_active = True
                existing_workflow_token.status = "active"
                existing_workflow_token.job_id = job_id
                existing_workflow_token.candidate_id = candidate_id
                db.flush()
            else:
                create_notification_workflow_token(
                    db=db,
                    job_id=job_id,
                    candidate_id=candidate_id,
                    workflow_name="slot_booking",
                    token=existing_session.token,
                    payload=token_payload,
                    expires_at=existing_session.expires_at,
                    token_type="slot_booking",
                    is_active=True,
                    source_app=source_app,
                )
        booking_link = existing_session.booking_url or _legacy_booking_url(existing_session.token)
        if not existing_session.booking_url:
            existing_session.booking_url = booking_link
        if outreach_event_id is not None:
            existing_session.outreach_event_id = outreach_event_id
        logger.info("interview_session_duplicate_skipped job_id=%s candidate_id=%s token=%s", job_id, candidate_id, existing_session.token)
        db.commit()
        return _session_payload(row=existing_session, booking_link=booking_link)

    if not profile:
        raise APIError("Candidate not found", status_code=404)

    email = ensure_candidate_email(profile)
    if not email:
        raise APIError("Candidate email is required", status_code=400)

    company = CompanyRepository(db).get_by_id(job.company_id)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=INTERVIEW_SESSION_TTL_MINUTES)
    token = secrets.token_urlsafe(32)
    booking_link = _legacy_booking_url(token)

    token_repo = NotificationWorkflowTokenRepository(db)
    existing_workflow_token = token_repo.get_active_by_candidate(
        job_id=job_id,
        candidate_id=candidate_id,
        source_app=source_app,
        token_type="slot_booking",
    )
    token_payload = _build_token_payload(profile=profile, job=job, company_name=company.name if company else "")
    if existing_workflow_token:
        existing_workflow_token.payload = token_payload
        existing_workflow_token.expires_at = expires_at
        existing_workflow_token.is_active = True
        existing_workflow_token.status = "active"
        token = existing_workflow_token.token
        booking_link = _legacy_booking_url(token)
        db.flush()
    else:
        create_notification_workflow_token(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            workflow_name="slot_booking",
            token=token,
            payload=token_payload,
            expires_at=expires_at,
            token_type="slot_booking",
            is_active=True,
            source_app=source_app,
        )

    row = session_repo.create(
        job_id=job_id,
        candidate_id=candidate_id,
        email=email,
        token=token,
        expires_at=expires_at,
        booking_url=booking_link,
        outreach_event_id=outreach_event_id,
    )
    booking_link = row.booking_url or _legacy_booking_url(token)
    db.commit()
    logger.info("interview_session_created job_id=%s candidate_id=%s token=%s", job_id, candidate_id, token)
    return _session_payload(row=row, booking_link=booking_link)


def get_interview_session(*, db: Session, token: str) -> dict[str, str | None]:
    token_row = NotificationWorkflowTokenRepository(db).get_by_token(token, source_app="adam")
    if not token_row:
        raise APIError("Interview session not found", status_code=404)
    if token_row.expires_at and token_row.expires_at <= datetime.now(timezone.utc):
        raise APIError("Interview session expired", status_code=410)
    if not token_row.is_active:
        raise APIError("Interview session not found", status_code=404)

    row = InterviewSessionRepository(db).get_by_token(token)
    if not row:
        raise APIError("Interview session not found", status_code=404)
    if row.expires_at <= datetime.now(timezone.utc):
        raise APIError("Interview session expired", status_code=410)

    job = JobRepository(db).get(row.job_id)
    profile = CandidateProfileRepository(db).get(job_id=row.job_id, candidate_id=row.candidate_id)
    booking_link = row.booking_url or _legacy_booking_url(row.token)
    return _session_payload(row=row, booking_link=booking_link)


def book_interview_session(*, db: Session, token: str, scheduled_at: str | None = None) -> dict[str, str]:
    token_row = NotificationWorkflowTokenRepository(db).get_by_token(token, source_app="adam")
    if not token_row:
        raise APIError("Interview session not found", status_code=404)
    if token_row.expires_at and token_row.expires_at <= datetime.now(timezone.utc):
        raise APIError("Interview session expired", status_code=410)
    if not token_row.is_active:
        raise APIError("Interview session not found", status_code=404)

    repo = InterviewSessionRepository(db)
    row = repo.get_by_token(token)
    if not row:
        raise APIError("Interview session not found", status_code=404)
    if row.expires_at <= datetime.now(timezone.utc):
        raise APIError("Interview session expired", status_code=410)
    if (row.status or "").strip().lower() == "booked":
        raise APIError("Interview session already booked", status_code=409)

    row = repo.mark_booked(token)
    if not row:
        raise APIError("Interview session not found", status_code=404)

    job = JobRepository(db).get(row.job_id)
    profile = CandidateProfileRepository(db).get(job_id=row.job_id, candidate_id=row.candidate_id)
    scheduled_time = scheduled_at or (row.booked_at.isoformat() if row.booked_at else None)
    meeting_link = get_interview_link(profile, job, scheduled_time) if job and profile else ""

    InterviewRepository(db).upsert_status(job_id=row.job_id, candidate_id=row.candidate_id, status="booked")
    recruiter_id = JobRepository(db).get_recruiter_id(row.job_id)
    if recruiter_id and profile:
        update_recruiter_preferences(
            db,
            recruiter_id,
            profile,
            [],
            signal_multiplier=3.0,
        )
    db.commit()
    logger.info("interview_session_booked job_id=%s candidate_id=%s token=%s", row.job_id, row.candidate_id, token)
    log_metric("interview_booked", job_id=row.job_id, candidate_id=row.candidate_id)
    return {
        "token": row.token,
        "status": row.status,
        "jobId": row.job_id,
        "candidateId": row.candidate_id,
        "meetingLink": meeting_link,
    }
