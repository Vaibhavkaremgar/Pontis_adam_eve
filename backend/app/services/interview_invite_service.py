from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.repositories import CandidateProfileRepository, InterviewRepository, JobRepository
from app.db.session import SessionLocal
from app.services.email_service import send_email
from app.services.interview_link_providers import get_booking_link
from app.services.slack_integration import post_slack_message
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)


def _normalize_email(value: Any) -> str:
    email = str(value or "").strip().lower()
    if not email or "@" not in email or email.endswith("@test.local"):
        return ""
    if ".." in email:
        return ""
    return email


def _extract_candidate_email(profile) -> str:
    raw_data = getattr(profile, "raw_data", None)
    if not isinstance(raw_data, dict):
        return ""

    for key in ("work_email", "email", "personal_email"):
        email = _normalize_email(raw_data.get(key))
        if email:
            return email

    for key in ("emails", "work_emails", "personal_emails"):
        values = raw_data.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, str):
                email = _normalize_email(item)
            elif isinstance(item, dict):
                email = _normalize_email(item.get("address") or item.get("email"))
            else:
                email = ""
            if email:
                return email
    return ""


def _build_invite_template(*, candidate_name: str, role: str, booking_link: str) -> tuple[str, str]:
    subject = f"Interview Invitation for {role}"
    body = (
        f"Hi {candidate_name},\n\n"
        "We'd like to move forward with your application.\n\n"
        "Please select a time slot for your interview:\n\n"
        f"👉 {booking_link}\n\n"
        "Best,\n"
        "Adam"
    )
    return subject, body


async def _post_slack_warning(channel_id: str | None, text: str) -> None:
    target = (channel_id or "").strip()
    if not target:
        return
    try:
        await post_slack_message(channel_id=target, text=text)
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.error("interview_invite_slack_warning_failed channel_id=%s error=%s", target, str(exc), exc_info=exc)


def send_interview_invite(candidate_id: str, job_id: str, *, channel_id: str | None = None) -> dict[str, Any]:
    with SessionLocal() as db:
        return _send_interview_invite(db=db, candidate_id=candidate_id, job_id=job_id, channel_id=channel_id)


def _send_interview_invite(*, db: Session, candidate_id: str, job_id: str, channel_id: str | None = None) -> dict[str, Any]:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)

    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if not profile:
        raise APIError("Candidate not found", status_code=404)

    candidate_name = (profile.name or "").strip() or "there"
    role = (job.title or "").strip() or "the role"
    candidate_email = _extract_candidate_email(profile)
    if not candidate_email:
        raise APIError("Candidate email is required", status_code=400)

    booking_link = get_booking_link(profile, job)
    subject, body = _build_invite_template(candidate_name=candidate_name, role=role, booking_link=booking_link)

    try:
        send_email(to_email=candidate_email, subject=subject, body=body)
        InterviewRepository(db).upsert_status(
            job_id=job_id,
            candidate_id=candidate_id,
            status="interview_invited",
            create_default="interview_invited",
        )
        db.commit()
        logger.info(
            "interview_invite_sent job_id=%s candidate_id=%s to_email=%s",
            job_id,
            candidate_id,
            candidate_email,
        )
        return {
            "success": True,
            "jobId": job_id,
            "candidateId": candidate_id,
            "candidateEmail": candidate_email,
            "subject": subject,
            "body": body,
            "bookingLink": booking_link,
            "status": "interview_invited",
        }
    except Exception as exc:
        db.rollback()
        logger.error(
            "interview_invite_failed job_id=%s candidate_id=%s error=%s",
            job_id,
            candidate_id,
            str(exc),
            exc_info=exc,
        )
        asyncio.run(_post_slack_warning(channel_id, "\u26a0\ufe0f Failed to send interview invite"))
        raise
