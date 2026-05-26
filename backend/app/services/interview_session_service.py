from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import INTERVIEW_SESSION_TTL_MINUTES, PUBLIC_APP_URL
from app.db.repositories import CandidateProfileRepository, CompanyRepository, InterviewRepository, InterviewSessionRepository, JobRepository, NotificationWorkflowTokenRepository
from app.services.ats_lifecycle_service import transition_candidate_ats_state
from app.services.audit_service import record_audit_event
from app.services.candidate_service import ensure_candidate_email
from app.services.lifecycle_service import record_job_lifecycle_event
from app.services.notification_intelligence_service import route_recruiter_notification
from app.services.outreach_service import _record_notification
from app.services.notification_service import build_slot_booking_payload, generate_workflow_token, upsert_notification_workflow_token
from app.services.interview_link_providers import get_interview_link
from app.services.metrics_service import log_metric
from app.services.recruiter_preference_service import update_recruiter_preferences
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)

INTERVIEW_STAGE_SEQUENCE: tuple[str, ...] = (
    "recruiter_screen",
    "technical_round",
    "hiring_manager_round",
    "final_round",
    "offer_stage",
    "placed",
)

_SLOT_BOOKING_BASE_URL = "https://interview1.pontis.one/booking.html"


def _slot_booking_url(workflow_token: str) -> str:
    token = (workflow_token or "").strip()
    return f"{_SLOT_BOOKING_BASE_URL}?token={token}" if token else _SLOT_BOOKING_BASE_URL


def _legacy_booking_url(token: str, *, source_type: str = "adam") -> str:
    return _slot_booking_url(token)


def _interview_url(token: str, *, source_type: str = "adam") -> str:
    base_url = (PUBLIC_APP_URL or "").rstrip("/")
    path = f"/interview?token={token}&source_type={source_type}"
    return f"{base_url}{path}" if base_url else path


def _utc_isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat()


def _ensure_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _metadata_map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize_stage_name(value: str | None) -> str:
    return (value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _stage_index(stage_name: str | None) -> int:
    normalized = _normalize_stage_name(stage_name)
    try:
        return INTERVIEW_STAGE_SEQUENCE.index(normalized)
    except ValueError:
        return -1


def _session_stage_name(row) -> str:
    scheduling_metadata = _metadata_map(getattr(row, "scheduling_metadata", {}))
    return _normalize_stage_name(
        scheduling_metadata.get("stageName")
        or scheduling_metadata.get("stage_name")
        or getattr(row, "stage", "")
        or "recruiter_screen"
    )


def _workflow_token(row) -> str:
    scheduling_metadata = _metadata_map(getattr(row, "scheduling_metadata", {}))
    return str(
        scheduling_metadata.get("workflowToken")
        or scheduling_metadata.get("workflow_token")
        or scheduling_metadata.get("workflowTokenValue")
        or getattr(row, "token", "")
    ).strip()


def _stage_history(row) -> list[dict[str, Any]]:
    scheduling_metadata = _metadata_map(getattr(row, "scheduling_metadata", {}))
    history = scheduling_metadata.get("stageHistory") or scheduling_metadata.get("stage_history") or []
    return [entry for entry in history if isinstance(entry, dict)]


def _build_token_payload(*, profile: Any, job: Any, company_name: str = "", resume_text: str | None = None) -> dict[str, Any]:
    return build_slot_booking_payload(
        candidate=profile,
        job={"title": getattr(job, "title", "") or "", "company_name": company_name or ""},
        resume_text=resume_text,
    )


def _session_payload(*, row, booking_link: str) -> dict[str, str | None]:
    scheduling_metadata = _metadata_map(getattr(row, "scheduling_metadata", {}))
    stage_name = _session_stage_name(row)
    booked_at = getattr(row, "booked_at", None)
    return {
        "id": row.id,
        "jobId": row.job_id,
        "candidateId": row.candidate_id,
        "companyId": getattr(row, "company_id", None),
        "outreachEventId": getattr(row, "outreach_event_id", None),
        "sourceType": str(scheduling_metadata.get("sourceType") or "adam"),
        "workflowToken": _workflow_token(row),
        "stageName": stage_name,
        "stageIndex": _stage_index(stage_name),
        "email": row.email,
        "token": row.token,
        "status": row.status,
        "expiresAt": _utc_isoformat(row.expires_at) or row.expires_at.isoformat(),
        "bookedAt": _utc_isoformat(booked_at),
        "scheduledAt": _utc_isoformat(getattr(row, "scheduled_at", None)),
        "stageHistory": _stage_history(row),
        "bookingLink": booking_link,
        "bookingUrl": booking_link,
        "slotLink": booking_link,
        "slot_link": booking_link,
    }


def _parse_scheduled_at(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        scheduled_at = datetime.fromisoformat(text)
    except ValueError as exc:
        raise APIError("scheduledAt must be a valid ISO-8601 datetime", status_code=400) from exc
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    return scheduled_at.astimezone(timezone.utc)


def _validate_booking_token(*, db: Session, token: str) -> Any:
    session_row, _ = _resolve_booking_context(db=db, token=token)
    if not session_row:
        raise APIError("Interview session not found", status_code=404)
    now = datetime.now(timezone.utc)
    expires_at = _ensure_utc_datetime(session_row.expires_at)
    if expires_at and expires_at <= now:
        raise APIError("Interview session expired", status_code=410)
    workflow_token = _workflow_token(session_row)
    if workflow_token:
        token_row = NotificationWorkflowTokenRepository(db).get_by_token(workflow_token, source_app="adam")
        if not token_row:
            raise APIError("Interview session not found", status_code=404)
        if not token_row.is_active or token_row.consumed_at is not None:
            raise APIError("Interview session already used", status_code=410)
    return session_row


def _resolve_booking_context(*, db: Session, token: str) -> tuple[Any | None, str]:
    normalized_token = (token or "").strip()
    if not normalized_token:
        return None, ""

    session_row = InterviewSessionRepository(db).get_by_token(normalized_token)
    if session_row:
        return session_row, _workflow_token(session_row) or normalized_token

    token_row = NotificationWorkflowTokenRepository(db).get_by_token(normalized_token, source_app="adam")
    if not token_row:
        return None, ""

    workflow_token = str(token_row.token or normalized_token).strip()
    payload = _metadata_map(token_row.payload)
    session_token = str(payload.get("currentInterviewToken") or "").strip()
    if not session_token:
        return None, workflow_token

    session_row = InterviewSessionRepository(db).get_by_token(session_token)
    return session_row, workflow_token


def create_interview_session(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    outreach_event_id: str | None = None,
    source_app: str = "adam",
    resume_text: str | None = None,
    workflow_token: str | None = None,
    stage_name: str = "recruiter_screen",
    interviewer_metadata: dict[str, Any] | None = None,
    scheduling_metadata: dict[str, Any] | None = None,
) -> dict[str, str | None]:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)

    session_repo = InterviewSessionRepository(db)
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    existing_session = session_repo.get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
    existing_expires_at = _ensure_utc_datetime(getattr(existing_session, "expires_at", None))
    normalized_stage_name = _normalize_stage_name(stage_name) or "recruiter_screen"
    existing_stage_name = _session_stage_name(existing_session) if existing_session else ""
    if existing_session and existing_stage_name == normalized_stage_name and (existing_expires_at is None or existing_expires_at > datetime.now(timezone.utc)):
        company = CompanyRepository(db).get_by_id(job.company_id)
        source_type = str((_metadata_map(getattr(existing_session, "scheduling_metadata", {})).get("sourceType") or source_app or "adam"))
        workflow_token_value = _workflow_token(existing_session) or existing_session.token
        if profile:
            token_payload = _build_token_payload(
                profile=profile,
                job=job,
                company_name=company.name if company else "",
                resume_text=resume_text,
            )
            token_payload.update(
                {
                    "workflowToken": workflow_token_value,
                    "stageName": normalized_stage_name,
                    "stageIndex": _stage_index(normalized_stage_name),
                    "currentInterviewToken": existing_session.token,
                    "currentStage": normalized_stage_name,
                }
                )
            if (existing_session.status or "").strip().lower() != "interview_scheduled":
                upsert_notification_workflow_token(
                    db=db,
                    job_id=job_id,
                    candidate_id=candidate_id,
                    workflow_name="slot_booking",
                    token=workflow_token_value,
                    payload=token_payload,
                    expires_at=existing_session.expires_at,
                    token_type="slot_booking",
                    is_active=True,
                    source_app=source_app,
                    force_token=True,
                )
        booking_link = _slot_booking_url(workflow_token_value)
        existing_session.booking_url = booking_link
        if outreach_event_id is not None:
            existing_session.outreach_event_id = outreach_event_id
        existing_session.scheduling_metadata = {
            **_metadata_map(existing_session.scheduling_metadata),
            "sourceType": source_type,
            "bookingLink": booking_link,
            "stageName": normalized_stage_name,
            "stageIndex": _stage_index(normalized_stage_name),
            "workflowToken": workflow_token_value,
            "stageHistory": _stage_history(existing_session) or [
                {
                    "stageName": normalized_stage_name,
                    "status": existing_session.status,
                    "createdAt": _utc_isoformat(existing_session.created_at),
                }
            ],
        }
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
    canonical_workflow_token = (workflow_token or "").strip() or generate_workflow_token()
    session_token = canonical_workflow_token
    stage_history = [
        {
            "stageName": normalized_stage_name,
            "status": "requested",
            "createdAt": _utc_isoformat(datetime.now(timezone.utc)),
        }
    ]

    token_payload = _build_token_payload(
        profile=profile,
        job=job,
        company_name=company.name if company else "",
        resume_text=resume_text,
    )
    token_payload.update(
        {
            "jobId": job.id,
            "companyId": job.company_id,
            "recruiterId": JobRepository(db).get_recruiter_id(job.id),
        }
    )
    token_payload.update(
        {
            "workflowToken": canonical_workflow_token,
            "stageName": normalized_stage_name,
            "stageIndex": _stage_index(normalized_stage_name),
            "currentInterviewToken": session_token,
            "currentStage": normalized_stage_name,
            "stageHistory": stage_history,
        }
    )
    token_data = upsert_notification_workflow_token(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        workflow_name="slot_booking",
        token=canonical_workflow_token,
        payload=token_payload,
        expires_at=expires_at,
        token_type="slot_booking",
        is_active=True,
        source_app=source_app,
        force_token=False,
    )
    workflow_token_value = str(token_data.get("token") or canonical_workflow_token)
    booking_link = _slot_booking_url(workflow_token_value)

    row = session_repo.create(
        job_id=job_id,
        candidate_id=candidate_id,
        email=email,
        token=session_token,
        expires_at=expires_at,
        booking_url=booking_link,
        outreach_event_id=outreach_event_id,
        stage_name=normalized_stage_name,
    )
    row.booking_url = booking_link
    row.stage = "requested"
    row.interviewer_metadata = dict(interviewer_metadata or {})
    row.scheduling_metadata = {
        **_metadata_map(scheduling_metadata),
        "sourceApp": source_app,
        "sourceType": source_app or "adam",
        "bookingLink": booking_link,
        "outreachEventId": outreach_event_id,
        "stageName": normalized_stage_name,
        "stageIndex": _stage_index(normalized_stage_name),
        "workflowToken": workflow_token_value,
        "currentInterviewToken": session_token,
        "stageHistory": stage_history,
    }
    booking_link = row.booking_url or _slot_booking_url(workflow_token_value)
    db.commit()
    recruiter_id = JobRepository(db).get_recruiter_id(job_id)
    logger.info(
        "interview_session_created job_id=%s candidate_id=%s recruiter_id=%s session_token=%s workflow_token=%s",
        job_id,
        candidate_id,
        recruiter_id or "",
        session_token,
        workflow_token_value,
    )
    record_job_lifecycle_event(
        db=db,
        job_id=job_id,
        event_type="INTERVIEW_CREATED",
        payload={
            "jobId": job_id,
            "candidateId": candidate_id,
            "recruiterId": recruiter_id,
            "sessionToken": session_token,
            "workflowToken": workflow_token_value,
            "stageName": normalized_stage_name,
            "outreachEventId": outreach_event_id,
            "scheduledAt": None,
        },
        source="interview",
    )
    transition_candidate_ats_state(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        to_status="interview_requested",
        source="interview",
        reason="interview_session_created",
        metadata={"token": session_token, "workflowToken": workflow_token_value, "outreachEventId": outreach_event_id, "stageName": normalized_stage_name},
    )
    _record_notification(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        notification_type="interview_requested",
        recipient_type="candidate",
        recipient=email,
        channel="email",
        title="Interview request",
        body=booking_link,
        status="delivered",
        delivery_reference=workflow_token_value or session_token,
        metadata={"bookingLink": booking_link, "source": source_app},
    )
    return _session_payload(row=row, booking_link=booking_link)


def get_interview_session(*, db: Session, token: str) -> dict[str, str | None]:
    _validate_booking_token(db=db, token=token)

    row, workflow_token_value = _resolve_booking_context(db=db, token=token)
    if not row:
        raise APIError("Interview session not found", status_code=404)
    row_expires_at = _ensure_utc_datetime(row.expires_at)
    if row_expires_at and row_expires_at <= datetime.now(timezone.utc):
        raise APIError("Interview session expired", status_code=410)

    job = JobRepository(db).get(row.job_id)
    profile = CandidateProfileRepository(db).get(job_id=row.job_id, candidate_id=row.candidate_id)
    source_type = str((_metadata_map(row.scheduling_metadata).get("sourceType") or "adam"))
    booking_link = row.booking_url or _slot_booking_url(workflow_token_value or _workflow_token(row) or row.token)
    return _session_payload(row=row, booking_link=booking_link)


def book_interview_session(*, db: Session, token: str, scheduled_at: str | None = None) -> dict[str, str]:
    _validate_booking_token(db=db, token=token)
    scheduled_at_value = _parse_scheduled_at(scheduled_at)

    repo = InterviewSessionRepository(db)
    row, workflow_token_value = _resolve_booking_context(db=db, token=token)
    if not row:
        raise APIError("Interview session not found", status_code=404)
    row_expires_at = _ensure_utc_datetime(row.expires_at)
    if row_expires_at and row_expires_at <= datetime.now(timezone.utc):
        raise APIError("Interview session expired", status_code=410)
    if (row.status or "").strip().lower() == "interview_scheduled" and row.scheduled_at and scheduled_at_value:
        existing_scheduled_at = _utc_isoformat(row.scheduled_at)
        requested_scheduled_at = _utc_isoformat(scheduled_at_value)
        if existing_scheduled_at == requested_scheduled_at:
            source_type = str((_metadata_map(row.scheduling_metadata).get("sourceType") or "adam"))
            return {
                "token": row.token,
                "status": row.status,
                "jobId": row.job_id,
                "candidateId": row.candidate_id,
                "scheduledAt": existing_scheduled_at,
                "meetingLink": _interview_url(workflow_token_value or _workflow_token(row) or row.token, source_type=source_type),
                "sourceType": source_type,
                "workflowToken": workflow_token_value or str((_metadata_map(row.scheduling_metadata).get("workflowToken") or row.token)),
                "stageName": _session_stage_name(row),
            }
    elif (row.status or "").strip().lower() == "interview_scheduled":
        raise APIError("Interview session already booked", status_code=409)

    row = repo.mark_booked(row.token)
    if not row:
        raise APIError("Interview session not found", status_code=404)
    source_type = str((_metadata_map(row.scheduling_metadata).get("sourceType") or "adam"))
    row.scheduled_at = scheduled_at_value
    row.stage = "scheduled"
    row.evaluation_status = "pending"
    row.scheduling_metadata = {
        **_metadata_map(row.scheduling_metadata),
        "scheduledAt": _utc_isoformat(row.scheduled_at),
        "bookingConfirmedAt": _utc_isoformat(row.booked_at),
        "sourceType": source_type,
        "workflowToken": workflow_token_value or str((_metadata_map(row.scheduling_metadata).get("workflowToken") or row.token)),
        "stageName": _session_stage_name(row),
    }
    workflow_token_value = workflow_token_value or str((_metadata_map(row.scheduling_metadata).get("workflowToken") or row.token))
    workflow_token_row = NotificationWorkflowTokenRepository(db).get_by_token(workflow_token_value, source_app="adam")
    workflow_payload = _metadata_map(workflow_token_row.payload if workflow_token_row else {})
    workflow_payload.update(
        {
            "workflowToken": workflow_token_value,
            "currentInterviewToken": row.token,
            "currentStage": _session_stage_name(row),
            "scheduledAt": _utc_isoformat(row.scheduled_at),
            "bookingConfirmedAt": _utc_isoformat(row.booked_at),
            "bookingStatus": row.status,
            "sourceType": source_type,
        }
    )
    upsert_notification_workflow_token(
        db=db,
        job_id=row.job_id,
        candidate_id=row.candidate_id,
        workflow_name="slot_booking",
        token=workflow_token_value,
        payload=workflow_payload,
        expires_at=row.expires_at,
        token_type="slot_booking",
        is_active=True,
        source_app="adam",
        force_token=True,
    )

    job = JobRepository(db).get(row.job_id)
    profile = CandidateProfileRepository(db).get(job_id=row.job_id, candidate_id=row.candidate_id)
    scheduled_time = _utc_isoformat(row.scheduled_at) or _utc_isoformat(row.booked_at)
    meeting_link = get_interview_link(profile, job, scheduled_time) if job and profile else ""
    if not meeting_link:
        meeting_link = _interview_url(workflow_token_value or _workflow_token(row) or row.token, source_type=source_type)

    InterviewRepository(db).upsert_status(job_id=row.job_id, candidate_id=row.candidate_id, status="interview_scheduled")
    recruiter_id = JobRepository(db).get_recruiter_id(row.job_id)
    if recruiter_id and profile:
        update_recruiter_preferences(
            db,
            recruiter_id,
            profile,
            [],
            signal_multiplier=3.0,
        )
    transition_candidate_ats_state(
        db=db,
        job_id=row.job_id,
        candidate_id=row.candidate_id,
        to_status="interview_scheduled",
        source="interview",
        reason="booking_confirmed",
        metadata={"scheduledAt": _utc_isoformat(row.scheduled_at), "meetingLink": meeting_link},
    )
    recruiter_id = JobRepository(db).get_recruiter_id(row.job_id)
    _record_notification(
        db=db,
        job_id=row.job_id,
        candidate_id=row.candidate_id,
        notification_type="interview_scheduled",
        recipient_type="recruiter",
        recipient=str(recruiter_id or ""),
        channel="slack",
        title="Interview scheduled",
        body=meeting_link,
        status="delivered",
        delivery_reference=workflow_token_value or token,
        metadata={"scheduledAt": _utc_isoformat(row.scheduled_at), "meetingLink": meeting_link},
    )
    db.commit()
    logger.info(
        "interview_session_scheduled job_id=%s candidate_id=%s recruiter_id=%s session_token=%s workflow_token=%s",
        row.job_id,
        row.candidate_id,
        recruiter_id or "",
        row.token,
        workflow_token_value,
    )
    log_metric("interview_scheduled", job_id=row.job_id, candidate_id=row.candidate_id)
    record_job_lifecycle_event(
        db=db,
        job_id=row.job_id,
        event_type="INTERVIEW_BOOKED",
        payload={
            "jobId": row.job_id,
            "candidateId": row.candidate_id,
            "recruiterId": recruiter_id,
            "sessionToken": row.token,
            "scheduledAt": _utc_isoformat(row.scheduled_at),
            "meetingLink": meeting_link,
            "workflowToken": workflow_token_value,
        },
        source="interview",
    )
    row.scheduling_metadata = {
        **_metadata_map(row.scheduling_metadata),
        "sourceType": source_type,
        "meetingLink": meeting_link,
    }
    record_audit_event(
        db=db,
        actor_id=None,
        action="interview_scheduled",
        entity_type="interview_session",
        entity_id=row.id,
        metadata={"jobId": row.job_id, "candidateId": row.candidate_id, "scheduledAt": _utc_isoformat(row.scheduled_at), "sourceType": source_type},
    )
    return {
        "token": row.token,
        "status": row.status,
        "jobId": row.job_id,
        "candidateId": row.candidate_id,
        "scheduledAt": _utc_isoformat(row.scheduled_at),
        "meetingLink": meeting_link,
        "sourceType": source_type,
        "workflowToken": workflow_token_value,
        "stageName": _session_stage_name(row),
    }


def reschedule_interview_session(*, db: Session, token: str, scheduled_at: str, reason: str = "") -> dict[str, str]:
    _validate_booking_token(db=db, token=token)
    rescheduled_at = _parse_scheduled_at(scheduled_at)
    if not rescheduled_at:
        raise APIError("scheduledAt is required", status_code=400)

    row, workflow_token_value = _resolve_booking_context(db=db, token=token)
    if not row:
        raise APIError("Interview session not found", status_code=404)

    source_type = str((_metadata_map(row.scheduling_metadata).get("sourceType") or "adam"))
    recruiter_id = JobRepository(db).get_recruiter_id(row.job_id)
    requested_scheduled_at = _utc_isoformat(rescheduled_at)
    existing_scheduled_at = _utc_isoformat(row.scheduled_at)
    if existing_scheduled_at == requested_scheduled_at:
        return {
            "token": row.token,
            "status": row.status,
            "jobId": row.job_id,
            "candidateId": row.candidate_id,
            "scheduledAt": existing_scheduled_at,
            "meetingLink": _interview_url(workflow_token_value or _workflow_token(row) or row.token, source_type=source_type),
            "sourceType": source_type,
        }

    row.scheduled_at = rescheduled_at
    row.stage = "scheduled"
    row.status = "interview_scheduled"
    row.evaluation_status = "pending"
    row.scheduling_metadata = {
        **_metadata_map(row.scheduling_metadata),
        "rescheduledAt": requested_scheduled_at,
        "rescheduleReason": reason.strip(),
        "sourceType": source_type,
        "bookingConfirmedAt": _utc_isoformat(datetime.now(timezone.utc)),
        "workflowToken": str((_metadata_map(row.scheduling_metadata).get("workflowToken") or row.token)),
        "stageName": _session_stage_name(row),
    }
    workflow_token_value = workflow_token_value or str((_metadata_map(row.scheduling_metadata).get("workflowToken") or row.token))
    workflow_token_row = NotificationWorkflowTokenRepository(db).get_by_token(workflow_token_value, source_app="adam")
    workflow_payload = _metadata_map(workflow_token_row.payload if workflow_token_row else {})
    workflow_payload.update(
        {
            "workflowToken": workflow_token_value,
            "currentInterviewToken": row.token,
            "currentStage": _session_stage_name(row),
            "scheduledAt": requested_scheduled_at,
            "rescheduledAt": requested_scheduled_at,
            "bookingStatus": row.status,
            "sourceType": source_type,
        }
    )
    upsert_notification_workflow_token(
        db=db,
        job_id=row.job_id,
        candidate_id=row.candidate_id,
        workflow_name="slot_booking",
        token=workflow_token_value,
        payload=workflow_payload,
        expires_at=row.expires_at,
        token_type="slot_booking",
        is_active=True,
        source_app="adam",
        force_token=True,
    )

    meeting_link = _interview_url(workflow_token_value or _workflow_token(row) or row.token, source_type=source_type)
    InterviewRepository(db).upsert_status(job_id=row.job_id, candidate_id=row.candidate_id, status="interview_scheduled")
    transition_candidate_ats_state(
        db=db,
        job_id=row.job_id,
        candidate_id=row.candidate_id,
        to_status="interview_scheduled",
        source="interview",
        reason="interview_rescheduled",
        metadata={"scheduledAt": requested_scheduled_at, "meetingLink": meeting_link, "reason": reason},
    )
    route_recruiter_notification(
        db=db,
        job_id=row.job_id,
        candidate_id=row.candidate_id,
        notification_key=f"interview-rescheduled:{row.job_id}:{row.candidate_id}:{row.token}:{requested_scheduled_at}",
        notification_type="interview_rescheduled",
        title="Interview rescheduled",
        body=f"Interview for {row.candidate_id} moved to {requested_scheduled_at}.",
        metadata={"scheduledAt": requested_scheduled_at, "reason": reason, "sourceType": source_type},
    )
    db.commit()
    logger.info(
        "interview_session_rescheduled job_id=%s candidate_id=%s recruiter_id=%s session_token=%s workflow_token=%s",
        row.job_id,
        row.candidate_id,
        recruiter_id or "",
        row.token,
        workflow_token_value,
    )
    record_job_lifecycle_event(
        db=db,
        job_id=row.job_id,
        event_type="INTERVIEW_RESCHEDULED",
        payload={
            "jobId": row.job_id,
            "candidateId": row.candidate_id,
            "recruiterId": recruiter_id,
            "sessionToken": row.token,
            "scheduledAt": requested_scheduled_at,
            "meetingLink": meeting_link,
            "reason": reason,
            "workflowToken": workflow_token_value,
        },
        source="interview",
    )
    record_audit_event(
        db=db,
        actor_id=None,
        action="interview_rescheduled",
        entity_type="interview_session",
        entity_id=row.id,
        metadata={"jobId": row.job_id, "candidateId": row.candidate_id, "scheduledAt": requested_scheduled_at, "reason": reason},
    )
    return {
        "token": row.token,
        "status": row.status,
        "jobId": row.job_id,
        "candidateId": row.candidate_id,
        "scheduledAt": requested_scheduled_at,
        "meetingLink": meeting_link,
        "sourceType": source_type,
        "workflowToken": workflow_token_value,
        "stageName": _session_stage_name(row),
    }


def mark_interview_no_show(*, db: Session, token: str, reason: str = "no_show_detected") -> dict[str, str]:
    row, workflow_token_value = _resolve_booking_context(db=db, token=token)
    if not row:
        raise APIError("Interview session not found", status_code=404)

    source_type = str((_metadata_map(row.scheduling_metadata).get("sourceType") or "adam"))
    recruiter_id = JobRepository(db).get_recruiter_id(row.job_id)
    if (row.status or "").strip().lower() == "no_show":
        return {
            "token": row.token,
            "status": row.status,
            "jobId": row.job_id,
            "candidateId": row.candidate_id,
            "scheduledAt": _utc_isoformat(row.scheduled_at),
            "sourceType": source_type,
            "workflowToken": workflow_token_value or str((_metadata_map(row.scheduling_metadata).get("workflowToken") or row.token)),
        }

    row.status = "no_show"
    row.stage = "no_show"
    row.evaluation_status = "pending"
    row.scheduling_metadata = {
        **_metadata_map(row.scheduling_metadata),
        "noShowDetectedAt": datetime.now(timezone.utc).isoformat(),
        "noShowReason": reason,
        "sourceType": source_type,
        "workflowToken": workflow_token_value or str((_metadata_map(row.scheduling_metadata).get("workflowToken") or row.token)),
        "stageName": _session_stage_name(row),
    }
    workflow_token_value = workflow_token_value or str((_metadata_map(row.scheduling_metadata).get("workflowToken") or row.token))
    workflow_token_row = NotificationWorkflowTokenRepository(db).get_by_token(workflow_token_value, source_app="adam")
    workflow_payload = _metadata_map(workflow_token_row.payload if workflow_token_row else {})
    workflow_payload.update(
        {
            "workflowToken": workflow_token_value,
            "currentInterviewToken": row.token,
            "currentStage": _session_stage_name(row),
            "noShowDetectedAt": row.scheduling_metadata.get("noShowDetectedAt"),
            "noShowReason": reason,
            "bookingStatus": row.status,
            "sourceType": source_type,
        }
    )
    upsert_notification_workflow_token(
        db=db,
        job_id=row.job_id,
        candidate_id=row.candidate_id,
        workflow_name="slot_booking",
        token=workflow_token_value,
        payload=workflow_payload,
        expires_at=row.expires_at,
        token_type="slot_booking",
        is_active=True,
        source_app="adam",
        force_token=True,
    )

    InterviewRepository(db).upsert_status(job_id=row.job_id, candidate_id=row.candidate_id, status="no_show")
    transition_candidate_ats_state(
        db=db,
        job_id=row.job_id,
        candidate_id=row.candidate_id,
        to_status="interview_no_show",
        source="interview",
        reason=reason,
        metadata={"token": row.token, "scheduledAt": _utc_isoformat(row.scheduled_at)},
    )
    route_recruiter_notification(
        db=db,
        job_id=row.job_id,
        candidate_id=row.candidate_id,
        notification_key=f"interview-no-show:{row.job_id}:{row.candidate_id}:{row.token}",
        notification_type="interview_no_show",
        title="Interview no-show detected",
        body=f"No-show recorded for {row.candidate_id}.",
        metadata={"token": row.token, "scheduledAt": _utc_isoformat(row.scheduled_at), "reason": reason, "sourceType": source_type},
    )
    db.commit()
    logger.info(
        "interview_session_no_show job_id=%s candidate_id=%s recruiter_id=%s session_token=%s workflow_token=%s",
        row.job_id,
        row.candidate_id,
        recruiter_id or "",
        row.token,
        workflow_token_value,
    )
    record_job_lifecycle_event(
        db=db,
        job_id=row.job_id,
        event_type="INTERVIEW_NO_SHOW",
        payload={
            "jobId": row.job_id,
            "candidateId": row.candidate_id,
            "recruiterId": recruiter_id,
            "sessionToken": row.token,
            "scheduledAt": _utc_isoformat(row.scheduled_at),
            "reason": reason,
            "workflowToken": workflow_token_value,
        },
        source="interview",
    )
    record_audit_event(
        db=db,
        actor_id=None,
        action="interview_no_show",
        entity_type="interview_session",
        entity_id=row.id,
        metadata={"jobId": row.job_id, "candidateId": row.candidate_id, "scheduledAt": _utc_isoformat(row.scheduled_at), "reason": reason},
    )
    return {
        "token": row.token,
        "status": row.status,
        "jobId": row.job_id,
        "candidateId": row.candidate_id,
        "scheduledAt": _utc_isoformat(row.scheduled_at),
        "sourceType": source_type,
        "workflowToken": workflow_token_value,
        "stageName": _session_stage_name(row),
    }
