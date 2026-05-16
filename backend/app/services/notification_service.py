from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.db.repositories import NotificationWorkflowTokenRepository

BOOKING_BASE_URL = "https://interview.pontis.one/booking.html"


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _string_field(item: Any, *names: str) -> str:
    for name in names:
        if isinstance(item, dict):
            value = item.get(name)
        else:
            value = getattr(item, name, None)
        text = _normalize_text(value)
        if text:
            return text
    return ""


def _build_booking_link(token: str) -> str:
    query = urlencode({"token": token}) if token else ""
    return f"{BOOKING_BASE_URL}?{query}" if query else BOOKING_BASE_URL


def build_slot_booking_payload(*, candidate: Any, job: Any) -> dict[str, Any]:
    return {
        "name": _string_field(candidate, "name", "full_name", "fullName"),
        "email": _string_field(candidate, "email"),
        "phone": _string_field(candidate, "phone"),
        "linkedin_url": _string_field(candidate, "linkedin_url", "linkedinUrl"),
        "github_url": _string_field(candidate, "github_url", "githubUrl"),
        "current_company": _string_field(candidate, "current_company", "company"),
        "current_title": _string_field(candidate, "current_title", "role", "title"),
        "total_experience_years": float(
            getattr(candidate, "total_experience_years", None)
            if getattr(candidate, "total_experience_years", None) is not None
            else getattr(candidate, "years_experience", 0.0)
            if getattr(candidate, "years_experience", None) is not None
            else 0.0
        ),
        "skills": list(getattr(candidate, "skills", None) or []),
        "resume_text": _string_field(candidate, "parsed_resume_text", "resume_text", "resumeText"),
        "fit_score": float(getattr(candidate, "fit_score", 0.0) or 0.0),
        "job_title": _string_field(job, "title", "job_title", "jobTitle"),
        "company_name": _string_field(job, "company_name", "company", "companyName"),
    }


def create_notification_workflow_token(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    workflow_name: str,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
    token_type: str = "",
    is_active: bool = True,
    source_app: str = "dashboard",
) -> dict[str, Any]:
    token_value = token or secrets.token_urlsafe(32)
    row = NotificationWorkflowTokenRepository(db).create(
        job_id=job_id,
        candidate_id=candidate_id,
        workflow_name=workflow_name,
        token=token_value,
        payload=payload,
        expires_at=expires_at,
        token_type=token_type,
        is_active=is_active,
        source_app=source_app,
    )
    booking_link = _build_booking_link(row.token)
    return {
        "id": row.id,
        "jobId": row.job_id,
        "candidateId": row.candidate_id,
        "tokenType": row.token_type,
        "workflowName": row.workflow_name,
        "token": row.token,
        "status": row.status,
        "isActive": row.is_active,
        "sourceApp": row.source_app,
        "payload": row.payload,
        "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
        "consumedAt": row.consumed_at.isoformat() if row.consumed_at else None,
        "bookingLink": booking_link,
        "bookingUrl": booking_link,
        "slotLink": booking_link,
        "slot_link": booking_link,
    }


def consume_notification_workflow_token(*, db: Session, token: str, source_app: str = "dashboard") -> dict[str, Any] | None:
    row = NotificationWorkflowTokenRepository(db).mark_consumed(token, source_app=source_app)
    if not row:
        return None
    booking_link = _build_booking_link(row.token)
    return {
        "id": row.id,
        "jobId": row.job_id,
        "candidateId": row.candidate_id,
        "tokenType": row.token_type,
        "workflowName": row.workflow_name,
        "token": row.token,
        "status": row.status,
        "isActive": row.is_active,
        "sourceApp": row.source_app,
        "payload": row.payload,
        "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
        "consumedAt": row.consumed_at.isoformat() if row.consumed_at else None,
        "bookingLink": booking_link,
        "bookingUrl": booking_link,
        "slotLink": booking_link,
        "slot_link": booking_link,
    }
