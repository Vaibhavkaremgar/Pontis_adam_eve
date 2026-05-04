from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import INTERVIEW_SESSION_TTL_MINUTES, PUBLIC_APP_URL
from app.db.repositories import CandidateProfileRepository, InterviewRepository, InterviewSessionRepository, JobRepository
from app.services.candidate_service import ensure_candidate_email
from app.services.interview_link_providers import get_booking_link, get_interview_link
from app.services.metrics_service import log_metric
from app.services.recruiter_preference_service import update_recruiter_preferences
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)


def _legacy_booking_url(token: str) -> str:
    base_url = (PUBLIC_APP_URL or "").rstrip("/")
    return f"{base_url}/interview/book?token={token}" if base_url else f"/interview/book?token={token}"


def _session_payload(*, row, booking_link: str) -> dict[str, str | None]:
    return {
        "id": row.id,
        "jobId": row.job_id,
        "candidateId": row.candidate_id,
        "email": row.email,
        "token": row.token,
        "status": row.status,
        "expiresAt": row.expires_at.isoformat(),
        "bookedAt": row.booked_at.isoformat() if row.booked_at else None,
        "bookingLink": booking_link,
        "bookingUrl": booking_link,
    }


def create_interview_session(*, db: Session, job_id: str, candidate_id: str) -> dict[str, str | None]:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)

    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if not profile:
        raise APIError("Candidate not found", status_code=404)

    email = ensure_candidate_email(profile)
    if not email:
        raise APIError("Candidate email is required", status_code=400)

    token = str(uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=INTERVIEW_SESSION_TTL_MINUTES)
    row = InterviewSessionRepository(db).create(
        job_id=job_id,
        candidate_id=candidate_id,
        email=email,
        token=token,
        expires_at=expires_at,
    )
    booking_link = get_booking_link(profile, job)
    db.commit()
    logger.info("interview_session_created job_id=%s candidate_id=%s token=%s", job_id, candidate_id, token)
    return _session_payload(row=row, booking_link=booking_link)


def get_interview_session(*, db: Session, token: str) -> dict[str, str | None]:
    row = InterviewSessionRepository(db).get_by_token(token)
    if not row:
        raise APIError("Interview session not found", status_code=404)
    if row.expires_at <= datetime.now(timezone.utc):
        raise APIError("Interview session expired", status_code=410)

    job = JobRepository(db).get(row.job_id)
    profile = CandidateProfileRepository(db).get(job_id=row.job_id, candidate_id=row.candidate_id)
    booking_link = get_booking_link(profile, job) if job and profile else _legacy_booking_url(row.token)
    return _session_payload(row=row, booking_link=booking_link)


def book_interview_session(*, db: Session, token: str, scheduled_at: str | None = None) -> dict[str, str]:
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
