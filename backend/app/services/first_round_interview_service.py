from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import CandidateRequestEntity, JobEntity, UserEntity
from app.services.interview_session_service import create_interview_session
from app.services.email_service import send_email
from app.services.outreach_service import _notification_key, _record_notification
from app.db.repositories import CandidateProfileRepository, CompanyRepository, JobRepository, NotificationEventRepository
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)

_INTERVIEW_ROUND = "first_round"
_STAGE_NAME = "recruiter_screen"


def _get_accepted_request(
    db: Session,
    *,
    candidate_id: str,
    job_id: str,
    agency_id: str,
) -> CandidateRequestEntity:
    """Return the ACCEPTED candidate_request or raise an appropriate error."""
    row = db.scalar(
        select(CandidateRequestEntity).where(
            CandidateRequestEntity.candidate_id == candidate_id,
            CandidateRequestEntity.job_id == job_id,
            CandidateRequestEntity.agency_id == agency_id,
        )
    )
    if not row:
        raise APIError("Candidate request not found", status_code=404)
    if row.status == "PENDING":
        raise APIError("Candidate has not yet accepted the interest request", status_code=409)
    if row.status == "DECLINED":
        raise APIError("Candidate declined the interest request", status_code=409)
    if row.status != "ACCEPTED":
        raise APIError("Candidate request is not in ACCEPTED state", status_code=409)
    return row


def _resolve_agency_id(db: Session, *, user_id: str) -> str:
    user = db.get(UserEntity, user_id)
    if not user or not user.agency_id:
        raise APIError("Recruiter agency not found", status_code=403)
    return str(user.agency_id)


def _assert_job_belongs_to_agency(job: JobEntity, agency_id: str) -> None:
    if str(job.agency_id or "") != str(agency_id):
        raise APIError("Forbidden", status_code=403)


def _send_booking_email(
    *,
    to_email: str,
    candidate_name: str,
    job_title: str,
    company_name: str,
    booking_link: str,
    expires_info: str,
) -> None:
    subject = f"Interview invitation: {job_title} at {company_name}"
    body = (
        f"Hi {candidate_name or 'there'},\n\n"
        f"{company_name} would like to conduct a first-round interview with you for the {job_title} role.\n\n"
        f"Please choose a convenient interview slot using the link below:\n\n"
        f"{booking_link}\n\n"
        f"{expires_info}"
        f"If you have any questions, please reply to this email.\n\n"
        f"Best regards,\n{company_name}"
    )
    html = (
        f"<p>Hi {candidate_name or 'there'},</p>"
        f"<p><strong>{company_name}</strong> would like to conduct a first-round interview with you "
        f"for the <strong>{job_title}</strong> role.</p>"
        f"<p>Please choose a convenient interview slot:</p>"
        f"<p><a href=\"{booking_link}\" style=\"background:#4f46e5;color:#fff;padding:10px 20px;"
        f"border-radius:6px;text-decoration:none;display:inline-block;\">Book Interview Slot</a></p>"
        f"{f'<p><em>{expires_info}</em></p>' if expires_info else ''}"
        f"<p>Best regards,<br/>{company_name}</p>"
    )
    try:
        send_email(to_email=to_email, subject=subject, body=body, html=html)
    except Exception as exc:
        logger.warning("first_round_booking_email_failed to=%s error=%s", to_email, exc)


def request_first_round_interview(
    db: Session,
    *,
    candidate_id: str,
    job_id: str,
    recruiter_id: str,
    available_slots: list[str] | None = None,
    timezone_name: str = "UTC",
) -> dict[str, Any]:
    """
    Validate ACCEPTED status, then create (or return existing) first-round interview session.

    Idempotent: if a session already exists for this candidate+job, returns it without
    creating duplicates, sending duplicate emails, or creating duplicate notifications.
    """
    agency_id = _resolve_agency_id(db, user_id=recruiter_id)

    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    _assert_job_belongs_to_agency(job, agency_id)

    _get_accepted_request(db, candidate_id=candidate_id, job_id=job_id, agency_id=agency_id)

    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if not profile:
        raise APIError("Candidate not found", status_code=404)

    company = CompanyRepository(db).get_by_id(agency_id)
    company_name = company.name if company else ""
    job_title = job.title or ""

    # Delegate entirely to existing session infrastructure (handles idempotency internally)
    session_data = create_interview_session(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        source_app="ui",
        stage_name=_STAGE_NAME,
        available_slots=available_slots or [],
        timezone_name=timezone_name,
    )

    booking_link = str(session_data.get("bookingLink") or session_data.get("bookingUrl") or "")
    candidate_email = str(session_data.get("email") or "")
    candidate_name = str(session_data.get("candidateName") or profile.name or "")
    workflow_token = str(session_data.get("workflowToken") or "")

    # Idempotency: only send email/notification if this is a fresh session (no booked_at)
    already_booked = bool(session_data.get("bookedAt"))
    notification_key = _notification_key(
        job_id=job_id,
        candidate_id=candidate_id,
        notification_type="first_round_interview_requested",
        suffix=workflow_token or "",
    )
    existing_notification = NotificationEventRepository(db).get_by_key(notification_key)

    if not already_booked and not existing_notification:
        # Candidate-facing notification event (future Eve consumption)
        _record_notification(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            notification_type="first_round_interview_requested",
            recipient_type="candidate",
            recipient=candidate_email,
            channel="email",
            title="First-round interview invitation",
            body=booking_link,
            status="delivered",
            delivery_reference=workflow_token,
            metadata={
                "bookingLink": booking_link,
                "jobTitle": job_title,
                "companyName": company_name,
                "interviewRound": _INTERVIEW_ROUND,
                "candidateId": candidate_id,
                "jobId": job_id,
                "agencyId": agency_id,
            },
        )

        if candidate_email:
            _send_booking_email(
                to_email=candidate_email,
                candidate_name=candidate_name,
                job_title=job_title,
                company_name=company_name,
                booking_link=booking_link,
                expires_info="",
            )

    db.commit()

    logger.info(
        "first_round_interview_requested job_id=%s candidate_id=%s recruiter_id=%s token=%s",
        job_id,
        candidate_id,
        recruiter_id,
        workflow_token,
    )

    return {
        **session_data,
        "interviewRound": _INTERVIEW_ROUND,
        "jobTitle": job_title,
        "companyName": company_name,
    }
