from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import INTERVIEW_APP_URL, INTERVIEW_SESSION_TTL_MINUTES
from app.db.repositories import AutomationJobRepository, CandidateProfileRepository, CompanyRepository, InterviewRepository, InterviewSessionRepository, JobRepository, NotificationWorkflowTokenRepository
from app.services.ats_lifecycle_service import transition_candidate_ats_state
from app.services.audit_service import record_audit_event
from app.services.candidate_service import ensure_candidate_email
from app.services.lifecycle_service import record_job_lifecycle_event
from app.services.notification_intelligence_service import route_recruiter_notification
from app.services.outreach_service import _record_notification
from app.services.notification_service import build_slot_booking_payload, generate_workflow_token, upsert_notification_workflow_token
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

def _slot_booking_url(workflow_token: str) -> str:
    token = (workflow_token or "").strip()
    base_url = (INTERVIEW_APP_URL or "").rstrip("/")
    booking_path = "/booking.html"
    if not base_url:
        return booking_path
    if token:
        return f"{base_url}{booking_path}?token={token}"
    return f"{base_url}{booking_path}"


def _legacy_booking_url(token: str, *, source_type: str = "ui") -> str:
    return _slot_booking_url(token)


def _interview_url(session_token: str) -> str:
    base_url = (INTERVIEW_APP_URL or "").rstrip("/")
    path = "/interview"
    token = (session_token or "").strip()
    if not base_url:
        return path
    if token:
        return f"{base_url}{path}?token={token}"
    return f"{base_url}{path}"


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


def _booking_candidate_id_from_payload(payload: dict[str, Any]) -> str:
    candidate = payload.get("candidate")
    if isinstance(candidate, dict):
        candidate_id = str(candidate.get("candidate_id") or candidate.get("id") or "").strip()
        if candidate_id:
            return candidate_id
    return str(payload.get("candidateId") or payload.get("candidate_id") or payload.get("candidate") or "").strip()


def _stage_history(row) -> list[dict[str, Any]]:
    scheduling_metadata = _metadata_map(getattr(row, "scheduling_metadata", {}))
    history = scheduling_metadata.get("stageHistory") or scheduling_metadata.get("stage_history") or []
    return [entry for entry in history if isinstance(entry, dict)]


def _normalize_timezone_name(value: str | None) -> str:
    normalized = (value or "").strip()
    return normalized or "UTC"


def _normalize_available_slots(values: Any, *, timezone_name: str = "UTC") -> list[str]:
    if not isinstance(values, list):
        return []

    try:
        target_zone = ZoneInfo(_normalize_timezone_name(timezone_name))
    except Exception:
        target_zone = timezone.utc

    normalized_slots: list[str] = []
    seen: set[str] = set()
    for value in values:
        raw_text = str(value or "").strip()
        if not raw_text:
            continue
        candidate_text = raw_text[:-1] + "+00:00" if raw_text.endswith("Z") else raw_text
        try:
            slot_dt = datetime.fromisoformat(candidate_text)
        except ValueError:
            continue
        if slot_dt.tzinfo is None:
            slot_dt = slot_dt.replace(tzinfo=target_zone)
        slot_iso = slot_dt.astimezone(timezone.utc).isoformat()
        if slot_iso in seen:
            continue
        seen.add(slot_iso)
        normalized_slots.append(slot_iso)
    return normalized_slots


def _booking_resolution_failed(*, token: str, reason: str, status_code: int, message: str) -> None:
    logger.warning("token_resolution_failed reason=%s token=%s", reason, token)
    raise APIError(message, status_code=status_code)


def _build_token_payload(
    *,
    db: Session,
    profile: Any,
    job: Any,
    recruiter_id: str = "",
    company_name: str = "",
    resume_text: str | None = None,
    available_slots: list[str] | None = None,
    timezone_name: str = "UTC",
) -> dict[str, Any]:
    return build_slot_booking_payload(
        db=db,
        candidate=profile,
        job={"title": getattr(job, "title", "") or "", "company_name": company_name or ""},
        resume_text=resume_text,
        recruiter_id=recruiter_id,
        available_slots=available_slots or [],
        timezone_name=timezone_name,
    )


def _session_payload(*, row, booking_link: str, token_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    scheduling_metadata = _metadata_map(getattr(row, "scheduling_metadata", {}))
    token_payload = _metadata_map(token_payload or {})
    stage_name = _session_stage_name(row)
    booked_at = getattr(row, "booked_at", None)
    available_slots = list(getattr(row, "available_slots", None) or token_payload.get("available_slots") or [])
    timezone_name = str(getattr(row, "timezone", "") or token_payload.get("timezone") or "UTC").strip() or "UTC"
    interviewer = token_payload.get("interviewer") if isinstance(token_payload.get("interviewer"), dict) else {}
    return {
        "id": row.id,
        "jobId": row.job_id,
        "candidateId": row.candidate_id,
        "companyId": getattr(row, "company_id", None),
        "outreachEventId": getattr(row, "outreach_event_id", None),
        "sourceType": str(scheduling_metadata.get("sourceType") or "ui"),
        "workflowToken": _workflow_token(row),
        "stageName": stage_name,
        "stageIndex": _stage_index(stage_name),
        "bookingStatus": str(getattr(row, "booking_status", "") or "pending"),
        "email": row.email,
        "token": row.token,
        "status": row.status,
        "expiresAt": None,
        "bookedAt": _utc_isoformat(booked_at),
        "scheduledAt": _utc_isoformat(getattr(row, "scheduled_at", None)),
        "timezone": timezone_name,
        "availableSlots": available_slots,
        "interviewer": interviewer,
        "candidateName": str(token_payload.get("name") or ""),
        "jobTitle": str(token_payload.get("job_title") or ""),
        "companyName": str(token_payload.get("company_name") or ""),
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


def _resolve_booking_context(*, db: Session, token: str) -> tuple[Any, str, Any, dict[str, Any]]:
    normalized_token = (token or "").strip()
    if not normalized_token:
        _booking_resolution_failed(
            token=token,
            reason="missing_token",
            status_code=404,
            message="Booking link is invalid or has expired",
        )

    session_row = InterviewSessionRepository(db).get_by_token(normalized_token)
    token_row = NotificationWorkflowTokenRepository(db).get_by_token(normalized_token, source_app="ui")
    now = datetime.now(timezone.utc)

    if token_row:
        if token_row.expires_at and _ensure_utc_datetime(token_row.expires_at) and _ensure_utc_datetime(token_row.expires_at) <= now:
            _booking_resolution_failed(
                token=normalized_token,
                reason="expired",
                status_code=410,
                message="This booking link has expired",
            )
        if token_row.used_at or token_row.consumed_at or not token_row.is_active:
            _booking_resolution_failed(
                token=normalized_token,
                reason="used",
                status_code=410,
                message="This booking link has already been used",
            )

    if session_row:
        if (str(getattr(session_row, "booking_status", "") or "").strip().lower() == "confirmed") or getattr(session_row, "booked_at", None) or str(getattr(session_row, "status", "") or "").strip().lower() in {"booked", "interview_scheduled"}:
            _booking_resolution_failed(
                token=normalized_token,
                reason="used",
                status_code=410,
                message="This booking link has already been used",
            )
        if not token_row:
            _booking_resolution_failed(
                token=normalized_token,
                reason="not_found",
                status_code=404,
                message="Booking link is invalid or has expired",
            )
        payload = _metadata_map(token_row.payload)
        return session_row, _workflow_token(session_row) or normalized_token, token_row, payload

    if not token_row:
        _booking_resolution_failed(
            token=normalized_token,
            reason="not_found",
            status_code=404,
            message="Booking link is invalid or has expired",
        )

    payload = _metadata_map(token_row.payload)
    if str(getattr(token_row, "token_type", "") or "").strip().lower() == "slot_selection" and not session_row:
        candidate_id = _booking_candidate_id_from_payload(payload)
        if not candidate_id:
            _booking_resolution_failed(
                token=normalized_token,
                reason="not_found",
                status_code=404,
                message="Booking link is invalid or has expired",
            )
        create_interview_session(
            db=db,
            job_id=str(token_row.job_id or ""),
            candidate_id=candidate_id,
            workflow_token=str(token_row.token or normalized_token),
            source_app=str(token_row.source_app or "ui"),
            stage_name=str(payload.get("interview", {}).get("round") or payload.get("stageName") or "recruiter_screen"),
            available_slots=list(payload.get("interview", {}).get("availableSlots") or payload.get("availableSlots") or payload.get("available_slots") or []),
            timezone_name=str(payload.get("interview", {}).get("timezone") or payload.get("timezone") or "UTC"),
            suppress_side_effects=True,
        )
        session_row = InterviewSessionRepository(db).get_by_token(str(token_row.token or normalized_token))
        token_row = NotificationWorkflowTokenRepository(db).get_by_token(normalized_token, source_app="ui") or token_row
        payload = _metadata_map(token_row.payload)
        if not session_row:
            _booking_resolution_failed(
                token=normalized_token,
                reason="not_found",
                status_code=404,
                message="Booking link is invalid or has expired",
            )
    session_token = str(payload.get("currentInterviewToken") or "").strip()
    if not session_token and session_row:
        session_token = str(getattr(session_row, "token", "") or "").strip()
    if not session_token:
        _booking_resolution_failed(
            token=normalized_token,
            reason="not_found",
            status_code=404,
            message="Booking link is invalid or has expired",
        )

    session_row = InterviewSessionRepository(db).get_by_token(session_token)
    if not session_row:
        _booking_resolution_failed(
            token=normalized_token,
            reason="not_found",
            status_code=404,
            message="Booking link is invalid or has expired",
        )
    if (str(getattr(session_row, "booking_status", "") or "").strip().lower() == "confirmed") or getattr(session_row, "booked_at", None) or str(getattr(session_row, "status", "") or "").strip().lower() in {"booked", "interview_scheduled"}:
        _booking_resolution_failed(
            token=normalized_token,
            reason="used",
            status_code=410,
            message="This booking link has already been used",
        )
    return session_row, str(token_row.token or normalized_token).strip(), token_row, payload


def create_interview_session(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    outreach_event_id: str | None = None,
    source_app: str = "",
    resume_text: str | None = None,
    workflow_token: str | None = None,
    stage_name: str = "recruiter_screen",
    interviewer_metadata: dict[str, Any] | None = None,
    scheduling_metadata: dict[str, Any] | None = None,
    available_slots: list[str] | None = None,
    timezone_name: str | None = None,
    candidate_email_override: str = "",
    suppress_side_effects: bool = False,
) -> dict[str, str | None]:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    normalized_source_app = source_app.strip().lower() if isinstance(source_app, str) else ""
    if normalized_source_app not in {"slack", "ui"}:
        normalized_source_app = getattr(job, "source_app", "ui") or "ui"

    session_repo = InterviewSessionRepository(db)
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    recruiter_id = JobRepository(db).get_recruiter_id(job.id)
    existing_session = session_repo.get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
    normalized_stage_name = _normalize_stage_name(stage_name) or "recruiter_screen"
    normalized_timezone = _normalize_timezone_name(timezone_name or getattr(existing_session, "timezone", None) or "UTC")
    normalized_slots = _normalize_available_slots(available_slots or [], timezone_name=normalized_timezone)
    existing_slots = _normalize_available_slots(list(getattr(existing_session, "available_slots", None) or []), timezone_name=normalized_timezone) if existing_session else []
    existing_stage_name = _session_stage_name(existing_session) if existing_session else ""
    if existing_session and existing_stage_name == normalized_stage_name:
        company = CompanyRepository(db).get_by_id(job.company_id)
        source_type = str((_metadata_map(getattr(existing_session, "scheduling_metadata", {})).get("sourceType") or normalized_source_app or getattr(job, "source_app", "ui") or "ui"))
        workflow_token_value = _workflow_token(existing_session) or existing_session.token
        if profile:
            token_payload = _build_token_payload(
                db=db,
                profile=profile,
                job=job,
                recruiter_id=recruiter_id,
                company_name=company.name if company else "",
                resume_text=resume_text,
                available_slots=normalized_slots or existing_slots,
                timezone_name=normalized_timezone,
            )
            token_payload.update(
                {
                    "workflowToken": workflow_token_value,
                    "stageName": normalized_stage_name,
                    "stageIndex": _stage_index(normalized_stage_name),
                    "currentInterviewToken": existing_session.token,
                    "currentStage": normalized_stage_name,
                    "jobId": job.id,
                    "companyId": job.company_id,
                    "recruiterId": recruiter_id,
                    "availableSlots": normalized_slots or existing_slots,
                    "timezone": normalized_timezone,
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
                    expires_at=None,
                    token_type="slot_booking",
                    is_active=True,
                    source_app=normalized_source_app,
                    force_token=True,
                )
        booking_link = _slot_booking_url(workflow_token_value)
        existing_session.booking_url = booking_link
        existing_session.available_slots = normalized_slots or existing_slots
        existing_session.timezone = normalized_timezone
        if outreach_event_id is not None:
            existing_session.outreach_event_id = outreach_event_id
        existing_session.scheduling_metadata = {
            **_metadata_map(existing_session.scheduling_metadata),
            "sourceType": source_type,
            "bookingLink": booking_link,
            "stageName": normalized_stage_name,
            "stageIndex": _stage_index(normalized_stage_name),
            "workflowToken": workflow_token_value,
            "availableSlots": normalized_slots or existing_slots,
            "timezone": normalized_timezone,
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

    email = ensure_candidate_email(profile) or str(candidate_email_override or "").strip()
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
        db=db,
        profile=profile,
        job=job,
        recruiter_id=recruiter_id,
        company_name=company.name if company else "",
        resume_text=resume_text,
        available_slots=normalized_slots,
        timezone_name=normalized_timezone,
    )
    token_payload.update(
        {
            "jobId": job.id,
            "companyId": job.company_id,
            "recruiterId": recruiter_id,
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
            "availableSlots": normalized_slots,
            "timezone": normalized_timezone,
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
                    source_app=normalized_source_app,
        force_token=False,
    )
    workflow_token_value = str(token_data.get("token") or canonical_workflow_token)
    booking_link = _slot_booking_url(workflow_token_value)

    row = session_repo.create(
        job_id=job_id,
        candidate_id=candidate_id,
        email=email,
        token=session_token,
        booking_url=booking_link,
        outreach_event_id=outreach_event_id,
        stage_name=normalized_stage_name,
        booking_status="pending",
        available_slots=normalized_slots,
        timezone_name=normalized_timezone,
    )
    row.booking_url = booking_link
    row.stage = "requested"
    row.booking_status = "pending"
    row.available_slots = normalized_slots
    row.timezone = normalized_timezone
    row.interviewer_metadata = dict(interviewer_metadata or {})
    row.scheduling_metadata = {
        **_metadata_map(scheduling_metadata),
        "sourceApp": normalized_source_app,
        "sourceType": normalized_source_app or getattr(job, "source_app", "ui") or "ui",
        "bookingLink": booking_link,
        "outreachEventId": outreach_event_id,
        "stageName": normalized_stage_name,
        "stageIndex": _stage_index(normalized_stage_name),
        "workflowToken": workflow_token_value,
        "currentInterviewToken": session_token,
        "availableSlots": normalized_slots,
        "timezone": normalized_timezone,
        "stageHistory": stage_history,
    }
    booking_link = row.booking_url or _slot_booking_url(workflow_token_value)
    db.commit()
    if suppress_side_effects:
        return _session_payload(row=row, booking_link=booking_link)
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
        metadata={"bookingLink": booking_link, "source": normalized_source_app},
    )
    return _session_payload(row=row, booking_link=booking_link)


def get_interview_session(*, db: Session, token: str) -> dict[str, str | None]:
    row, workflow_token_value, token_row, token_payload = _resolve_booking_context(db=db, token=token)
    booking_link = row.booking_url or _slot_booking_url(workflow_token_value or _workflow_token(row) or row.token)
    return _session_payload(row=row, booking_link=booking_link, token_payload=token_payload)


def book_interview_session(*, db: Session, token: str, scheduled_at: str | None = None) -> dict[str, str]:
    scheduled_at_value = _parse_scheduled_at(scheduled_at)
    selected_slot = _utc_isoformat(scheduled_at_value)

    repo = InterviewSessionRepository(db)
    row, workflow_token_value, token_row, token_payload = _resolve_booking_context(db=db, token=token)
    available_slots = _normalize_available_slots(getattr(row, "available_slots", None) or token_payload.get("available_slots") or token_payload.get("availableSlots") or [], timezone_name=str(getattr(row, "timezone", "") or token_payload.get("timezone") or "UTC"))
    timezone_name = _normalize_timezone_name(getattr(row, "timezone", "") or token_payload.get("timezone") or "UTC")
    if not scheduled_at_value:
        raise APIError("scheduledAt is required", status_code=400)
    if available_slots:
        if not selected_slot or selected_slot not in available_slots:
            raise APIError("Selected slot is no longer available", status_code=409)
    elif (row.status or "").strip().lower() == "interview_scheduled":
        raise APIError("Interview session already booked", status_code=409)

    row = repo.mark_booked(row.token)
    if not row:
        raise APIError("Interview session not found", status_code=404)
    source_type = str((_metadata_map(row.scheduling_metadata).get("sourceType") or "ui"))
    row.scheduled_at = scheduled_at_value
    row.stage = "scheduled"
    row.evaluation_status = "pending"
    remaining_slots = [slot for slot in available_slots if slot != selected_slot]
    row.available_slots = remaining_slots
    row.timezone = timezone_name
    row.scheduling_metadata = {
        **_metadata_map(row.scheduling_metadata),
        "scheduledAt": _utc_isoformat(row.scheduled_at),
        "bookingConfirmedAt": _utc_isoformat(row.booked_at),
        "sourceType": source_type,
        "workflowToken": workflow_token_value or str((_metadata_map(row.scheduling_metadata).get("workflowToken") or row.token)),
        "stageName": _session_stage_name(row),
        "availableSlots": remaining_slots,
        "timezone": timezone_name,
    }
    workflow_token_value = workflow_token_value or str((_metadata_map(row.scheduling_metadata).get("workflowToken") or row.token))
    workflow_token_row = NotificationWorkflowTokenRepository(db).get_by_token(workflow_token_value, source_app="ui")
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
            "availableSlots": remaining_slots,
            "timezone": timezone_name,
        }
    )
    upsert_notification_workflow_token(
        db=db,
        job_id=row.job_id,
        candidate_id=row.candidate_id,
        workflow_name="slot_booking",
        token=workflow_token_value,
        payload=workflow_payload,
        expires_at=None,
        token_type="slot_booking",
        is_active=True,
        source_app=source_type,
        force_token=True,
    )
    NotificationWorkflowTokenRepository(db).mark_consumed(workflow_token_value, source_app="ui")

    job = JobRepository(db).get(row.job_id)
    profile = CandidateProfileRepository(db).get(job_id=row.job_id, candidate_id=row.candidate_id)
    scheduled_time = _utc_isoformat(row.scheduled_at) or _utc_isoformat(row.booked_at)
    meeting_link = _interview_url(row.token)

    InterviewRepository(db).upsert_status(
        job_id=row.job_id,
        candidate_id=row.candidate_id,
        status="interview_scheduled",
        async_token=workflow_token_value,
    )
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
    AutomationJobRepository(db).upsert(
        automation_key=f"interview-execution:{row.token}",
        automation_type="interview_execution",
        job_id=row.job_id,
        candidate_id=row.candidate_id,
        scheduled_at=row.scheduled_at or datetime.now(timezone.utc),
        payload={
            "sessionToken": row.token,
            "workflowToken": workflow_token_value or str((_metadata_map(row.scheduling_metadata).get("workflowToken") or row.token)),
            "jobId": row.job_id,
            "candidateId": row.candidate_id,
        },
    )
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
        "bookingStatus": row.booking_status,
        "timezone": timezone_name,
        "availableSlots": remaining_slots,
        "interviewer": token_payload.get("interviewer") if isinstance(token_payload.get("interviewer"), dict) else {},
    }


def reschedule_interview_session(*, db: Session, token: str, scheduled_at: str, reason: str = "") -> dict[str, str]:
    rescheduled_at = _parse_scheduled_at(scheduled_at)
    if not rescheduled_at:
        raise APIError("scheduledAt is required", status_code=400)

    row, workflow_token_value, token_row, token_payload = _resolve_booking_context(db=db, token=token)

    source_type = str((_metadata_map(row.scheduling_metadata).get("sourceType") or "ui"))
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
            "meetingLink": _interview_url(workflow_token_value or _workflow_token(row) or row.token),
            "sourceType": source_type,
            "bookingStatus": row.booking_status,
            "timezone": str(getattr(row, "timezone", "") or token_payload.get("timezone") or "UTC"),
            "availableSlots": list(getattr(row, "available_slots", None) or token_payload.get("available_slots") or []),
            "interviewer": token_payload.get("interviewer") if isinstance(token_payload.get("interviewer"), dict) else {},
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
    workflow_token_row = NotificationWorkflowTokenRepository(db).get_by_token(workflow_token_value, source_app="ui")
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
        expires_at=None,
        token_type="slot_booking",
        is_active=True,
        source_app=source_type,
        force_token=True,
    )

    meeting_link = _interview_url(workflow_token_value or _workflow_token(row) or row.token)
    InterviewRepository(db).upsert_status(
        job_id=row.job_id,
        candidate_id=row.candidate_id,
        status="interview_scheduled",
        async_token=workflow_token_value,
    )
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
        "bookingStatus": row.booking_status,
        "timezone": str(getattr(row, "timezone", "") or token_payload.get("timezone") or "UTC"),
        "availableSlots": list(getattr(row, "available_slots", None) or token_payload.get("available_slots") or []),
        "interviewer": token_payload.get("interviewer") if isinstance(token_payload.get("interviewer"), dict) else {},
    }


def mark_interview_no_show(*, db: Session, token: str, reason: str = "no_show_detected") -> dict[str, str]:
    row, workflow_token_value, token_row, token_payload = _resolve_booking_context(db=db, token=token)

    source_type = str((_metadata_map(row.scheduling_metadata).get("sourceType") or "ui"))
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
            "bookingStatus": row.booking_status,
            "timezone": str(getattr(row, "timezone", "") or token_payload.get("timezone") or "UTC"),
            "availableSlots": list(getattr(row, "available_slots", None) or token_payload.get("available_slots") or []),
            "interviewer": token_payload.get("interviewer") if isinstance(token_payload.get("interviewer"), dict) else {},
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
    workflow_token_row = NotificationWorkflowTokenRepository(db).get_by_token(workflow_token_value, source_app="ui")
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
        expires_at=None,
        token_type="slot_booking",
        is_active=True,
        source_app=source_type,
        force_token=True,
    )

    InterviewRepository(db).upsert_status(
        job_id=row.job_id,
        candidate_id=row.candidate_id,
        status="no_show",
        async_token=workflow_token_value,
    )
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
        "bookingStatus": row.booking_status,
        "timezone": str(getattr(row, "timezone", "") or token_payload.get("timezone") or "UTC"),
        "availableSlots": list(getattr(row, "available_slots", None) or token_payload.get("available_slots") or []),
        "interviewer": token_payload.get("interviewer") if isinstance(token_payload.get("interviewer"), dict) else {},
    }
