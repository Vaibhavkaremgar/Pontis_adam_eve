"""
candidate_response_service.py
==============================
Adam-side service contract for the PENDING → ACCEPTED / DECLINED transition.

Eve integration contract:
    Eve will eventually call POST /candidates/{candidate_id}/respond with:
        { "token": "<request_id>", "action": "accept" | "decline" }

    This service validates the transition and writes the result.

State machine:
    PENDING  ──accept──►  ACCEPTED
    PENDING  ──decline──► DECLINED
    ACCEPTED ──*──►       rejected (idempotent if same action, error if different)
    DECLINED ──*──►       rejected (idempotent if same action, error if different)

Security contract:
    - Adam recruiters MUST NOT call this endpoint (no self-accept).
    - The route is gated by an internal API key, not a recruiter JWT.
    - agency_id and job_id are derived from the stored request row — never trusted from the caller.
    - candidate_id is validated against the stored row.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.repositories import CandidateProfileRepository, CompanyRepository, InterviewSessionRepository, JobRepository, NotificationEventRepository, NotificationWorkflowTokenRepository
from app.models.entities import CandidateProfileEntity, CandidateRequestEntity, RecruiterInterestRequestEntity
from app.services.email_service import send_email
from app.services.interview_session_service import create_interview_session
from app.services.notification_service import build_slot_selection_payload, upsert_notification_workflow_token
from app.utils.exceptions import APIError


def _enqueue_slot_booking_eve_event(
    db: Session,
    *,
    request_row,
    profile,
    booking_link: str,
    booking_token: str,
    expires_at,
) -> None:
    """Write a durable interview_slot_booking outbox event for Eve."""
    from app.services.eve_notification_service import upsert_outbound_event
    from uuid import uuid4 as _uuid4

    event_id = str(_uuid4())
    adam_event_id = f"slot-booking:{request_row.id}:{profile.id}"
    payload = {
        "event_id": event_id,
        "candidate_id": str(profile.id),
        "job_id": str(request_row.job_id),
        "agency_id": str(request_row.agency_id),
        "notification_type": "interview_slot_booking",
        "title": "Book your interview slot",
        "message": "You've been shortlisted. Please select a time that works for you.",
        "booking_url": booking_link,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }
    upsert_outbound_event(
        db,
        adam_event_id=adam_event_id,
        candidate_id=str(profile.id),
        job_id=str(request_row.job_id),
        agency_id=str(request_row.agency_id),
        notification_type="interview_slot_booking",
        payload=payload,
        event_id=event_id,
    )

logger = logging.getLogger(__name__)

_VALID_ACTIONS = frozenset({"accept", "decline"})
_ACTION_TO_STATUS = {"accept": "ACCEPTED", "decline": "DECLINED"}
_EVE_RESPONSE_TO_ACTION = {"interested": "accept", "not_interested": "decline"}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _notification_key(*, job_id: str, candidate_id: str, agency_id: str, recruiter_id: str, request_id: str) -> str:
    return f"slot-selection:{job_id}:{candidate_id}:{agency_id}:{recruiter_id}:{request_id}"


def _load_recruiter_interest_row(db: Session, *, request_row: CandidateRequestEntity) -> RecruiterInterestRequestEntity | None:
    return db.scalar(
        select(RecruiterInterestRequestEntity).where(
            RecruiterInterestRequestEntity.candidate_id == request_row.candidate_id,
            RecruiterInterestRequestEntity.job_id == request_row.job_id,
            RecruiterInterestRequestEntity.agency_id == request_row.agency_id,
            RecruiterInterestRequestEntity.recruiter_id == request_row.created_by,
        )
    )


def _ensure_slot_selection_artifacts(db: Session, *, request_row: CandidateRequestEntity) -> dict:
    profile = CandidateProfileRepository(db).get(job_id=str(request_row.job_id), candidate_id=str(request_row.candidate_id))
    if not profile:
        raise APIError("Candidate not found", status_code=404)

    job = JobRepository(db).get(str(request_row.job_id))
    if not job:
        raise APIError("Job not found", status_code=404)

    company = CompanyRepository(db).get_by_id(str(request_row.agency_id))
    recruiter_id = str(request_row.created_by or "")
    notification_key = _notification_key(
        job_id=str(request_row.job_id),
        candidate_id=str(profile.id),
        agency_id=str(request_row.agency_id),
        recruiter_id=recruiter_id,
        request_id=str(request_row.id),
    )
    existing_notification = NotificationEventRepository(db).get_by_key(notification_key)
    existing_session = InterviewSessionRepository(db).get_by_job_and_candidate(
        job_id=str(request_row.job_id),
        candidate_id=str(request_row.candidate_id),
    )
    if existing_notification or existing_session:
        if existing_notification:
            payload = dict(existing_notification.notification_metadata or {})
            booking_link = str(existing_notification.body or "").strip() or str(payload.get("bookingLink") or payload.get("bookingUrl") or "")
            return {
                "booking_token": str(existing_notification.delivery_reference or payload.get("workflowToken") or ""),
                "booking_link": booking_link,
                "notification_key": notification_key,
                "payload": payload,
            }
        return {
            "booking_token": "",
            "booking_link": "",
            "notification_key": notification_key,
            "payload": {},
        }

    token_repo = NotificationWorkflowTokenRepository(db)
    booking_token_row = token_repo.get_active_by_candidate(
        job_id=str(request_row.job_id),
        candidate_id=str(profile.id),
        source_app="ui",
        token_type="slot_selection",
    )
    if not booking_token_row:
        booking_token_row = token_repo.get_active_by_candidate(
            job_id=str(request_row.job_id),
            candidate_id=str(profile.id),
            source_app="ui",
            token_type="slot_booking",
        )
    if booking_token_row:
        booking_token = str(booking_token_row.token or "")
        booking_link = str(
            build_slot_selection_payload(
                candidate=profile,
                job=job,
                recruiter_id=recruiter_id,
                agency_id=str(request_row.agency_id),
                agency_name=str(company.name if company else ""),
                workflow_token=booking_token,
                booking_link="",
                source_type="eve",
                db=db,
            )["bookingLink"]
        )
    else:
        booking_token_row = upsert_notification_workflow_token(
            db=db,
            job_id=str(request_row.job_id),
            candidate_id=str(profile.id),
            workflow_name="slot_selection",
            token=None,
            payload={},
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            token_type="slot_selection",
            is_active=True,
            source_app="ui",
            agency_id=str(request_row.agency_id),
            user_id=recruiter_id,
            force_token=False,
        )
        booking_token = str(booking_token_row.get("token") or "")
        booking_link = str(booking_token_row.get("bookingLink") or booking_token_row.get("bookingUrl") or "")
        if not booking_link:
            booking_link = f"https://interview.pontis.one/booking.html?token={booking_token}"

    payload = build_slot_selection_payload(
        candidate=profile,
        job=job,
        recruiter_id=recruiter_id,
        agency_id=str(request_row.agency_id),
        agency_name=str(company.name if company else ""),
        workflow_token=booking_token,
        booking_link=booking_link,
        source_type="eve",
        db=db,
    )
    payload["request_id"] = str(request_row.id)
    payload["candidate"]["id"] = str(profile.id)
    payload["candidate"]["candidate_id"] = str(request_row.candidate_id)
    payload["agency"]["id"] = str(request_row.agency_id)
    payload["recruiter"]["id"] = recruiter_id
    payload["interview"]["status"] = "slot_selection_ready"
    payload["interview"]["booking_link"] = booking_link
    payload["interview"]["bookingLink"] = booking_link
    payload["interview"]["bookingUrl"] = booking_link

    # Create the interview session so the candidate appears in TO_BE_INTERVIEWED
    existing_session = InterviewSessionRepository(db).get_by_job_and_candidate(
        job_id=str(request_row.job_id),
        candidate_id=str(request_row.candidate_id),
    )
    if not existing_session:
        try:
            create_interview_session(
                db=db,
                job_id=str(request_row.job_id),
                candidate_id=str(request_row.candidate_id),
                workflow_token=booking_token or None,
                source_app="ui",
                stage_name="recruiter_screen",
                suppress_side_effects=True,
            )
        except Exception:
            logger.warning(
                "slot_booking_session_create_failed request_id=%s candidate_id=%s",
                request_row.id,
                profile.candidate_id,
                exc_info=True,
            )

    notification_key = _notification_key(
        job_id=str(request_row.job_id),
        candidate_id=str(profile.id),
        agency_id=str(request_row.agency_id),
        recruiter_id=recruiter_id,
        request_id=str(request_row.id),
    )
    existing_notification = NotificationEventRepository(db).get_by_key(notification_key)
    NotificationEventRepository(db).upsert(
        notification_key=notification_key,
        job_id=str(request_row.job_id),
        company_id=str(request_row.agency_id),
        candidate_id=str(profile.id),
        actor_id=recruiter_id or None,
        recipient_type="candidate",
        recipient=str(profile.email or ""),
        channel="eve",
        title="Interview slot selection ready",
        body=booking_link,
        status="delivered",
        notification_type="slot_selection_ready",
        notification_metadata=payload,
        delivery_reference=booking_token,
    )
    db.commit()

    candidate_email = str(profile.email or "").strip()
    if candidate_email:
        subject = f"Interview slot selection: {job.title or ''}".strip()
        body = (
            f"Hi {profile.name or 'there'},\n\n"
            f"{company.name if company else 'The team'} has shared your interview slot selection link.\n\n"
            f"{booking_link}\n\n"
            f"If you have questions, reply to this email.\n"
        )
        try:
            send_email(to_email=candidate_email, subject=subject, body=body)
        except Exception:
            logger.warning("slot_selection_email_failed request_id=%s candidate_id=%s", request_row.id, profile.candidate_id, exc_info=True)

    db.flush()

    # Resolve expires_at from the workflow token row if available
    _expires_at = getattr(booking_token_row, "expires_at", None) if not isinstance(booking_token_row, dict) else None
    _enqueue_slot_booking_eve_event(
        db,
        request_row=request_row,
        profile=profile,
        booking_link=booking_link,
        booking_token=booking_token,
        expires_at=_expires_at,
    )

    return {
        "booking_token": booking_token,
        "booking_link": booking_link,
        "notification_key": notification_key,
        "payload": payload,
    }


def respond_to_candidate_request(
    db: Session,
    *,
    request_id: str,
    candidate_id: str,
    action: str,
) -> dict:
    """
    Transition a PENDING candidate request to ACCEPTED or DECLINED.

    Parameters
    ----------
    request_id:
        The UUID of the CandidateRequestEntity row.
    candidate_id:
        The candidate_id stored on the row — used to verify the caller
        is acting on behalf of the correct candidate.
    action:
        "accept" or "decline"

    Returns
    -------
    dict with the updated request state.

    Raises
    ------
    APIError(400) — invalid action value
    APIError(404) — request not found
    APIError(403) — candidate_id mismatch (wrong candidate acting on this request)
    APIError(409) — request is not in PENDING state
                    (idempotent: returns current state if action matches existing status)
    """
    if action not in _VALID_ACTIONS:
        raise APIError(f"Invalid action '{action}'. Must be 'accept' or 'decline'.", status_code=400)

    row = db.scalar(
        select(CandidateRequestEntity).where(CandidateRequestEntity.id == request_id)
    )
    if not row:
        raise APIError("Request not found", status_code=404)

    # Verify the candidate acting on this request is the correct one
    if str(row.candidate_id) != str(candidate_id):
        raise APIError("Forbidden", status_code=403)

    target_status = _ACTION_TO_STATUS[action]

    # Idempotency: if already in the target state, return current state without error
    if row.status == target_status:
        if target_status == "ACCEPTED":
            _ensure_slot_selection_artifacts(db, request_row=row)
        return _serialize(row)

    # Reject any transition from a non-PENDING state
    if row.status != "PENDING":
        raise APIError(
            f"Cannot transition from '{row.status}' to '{target_status}'. "
            "Only PENDING requests can be accepted or declined.",
            status_code=409,
        )

    now = datetime.now(timezone.utc)
    row.status = target_status
    row.responded_at = now
    row.updated_at = now
    db.flush()

    recruiter_interest = _load_recruiter_interest_row(db, request_row=row)
    if not recruiter_interest:
        recruiter_interest = RecruiterInterestRequestEntity(
            id=str(uuid4()),
            candidate_id=str(row.candidate_id),
            job_id=str(row.job_id),
            agency_id=str(row.agency_id),
            recruiter_id=str(row.created_by),
            request_status="interested",
            recruiter_requested_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(recruiter_interest)
    recruiter_interest.candidate_response = action
    recruiter_interest.candidate_response_at = now
    recruiter_interest.updated_at = now
    if target_status == "ACCEPTED":
        _ensure_slot_selection_artifacts(db, request_row=row)

    return _serialize(row)


def get_pending_requests_for_candidate(
    db: Session,
    *,
    candidate_id: str,
) -> list[dict]:
    """
    Return all PENDING requests for a given candidate_id.
    Used by Eve to show the candidate their outstanding interest requests.
    """
    rows = db.scalars(
        select(CandidateRequestEntity).where(
            CandidateRequestEntity.candidate_id == candidate_id,
            CandidateRequestEntity.status == "PENDING",
        )
    ).all()
    return [_serialize(row) for row in rows]


def _serialize(row: CandidateRequestEntity) -> dict:
    return {
        "request_id": str(row.id),
        "candidate_id": row.candidate_id,
        "job_id": str(row.job_id),
        "agency_id": str(row.agency_id),
        "status": row.status,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "responded_at": _iso(row.responded_at),
    }


def respond_to_eve_candidate_response(
    db: Session,
    *,
    eve_event_id: str,
    adam_event_id: str,
    candidate_id: str,
    job_id: str,
    agency_id: str,
    response: str,
) -> dict:
    normalized_response = str(response or "").strip().lower()
    if normalized_response not in _EVE_RESPONSE_TO_ACTION:
        raise APIError("Invalid response. Must be 'interested' or 'not_interested'.", status_code=400)

    eve_event_id = str(eve_event_id or "").strip()
    adam_event_id = str(adam_event_id or "").strip()
    candidate_id = str(candidate_id or "").strip()
    job_id = str(job_id or "").strip()
    agency_id = str(agency_id or "").strip()

    if not eve_event_id:
        raise APIError("eve_event_id is required", status_code=400)
    if not adam_event_id:
        raise APIError("adam_event_id is required", status_code=400)
    if not candidate_id:
        raise APIError("candidate_id is required", status_code=400)
    if not job_id:
        raise APIError("job_id is required", status_code=400)
    if not agency_id:
        raise APIError("agency_id is required", status_code=400)

    profile = db.get(CandidateProfileEntity, candidate_id)
    if not profile:
        raise APIError("Candidate not found", status_code=404)
    if str(profile.agency_id or "") != agency_id:
        raise APIError("Forbidden", status_code=403)
    if str(profile.job_id or "") != job_id:
        raise APIError("Candidate/job mismatch", status_code=403)

    row = db.scalar(
        select(CandidateRequestEntity)
        .where(CandidateRequestEntity.id == adam_event_id)
        .with_for_update()
    )
    if not row:
        raise APIError("adam_event_id not found", status_code=404)
    if str(row.candidate_id or "") != str(profile.candidate_id or "") or str(row.job_id or "") != job_id or str(row.agency_id or "") != agency_id:
        raise APIError("Candidate/job/agency mismatch", status_code=403)

    target_action = _EVE_RESPONSE_TO_ACTION[normalized_response]
    target_status = _ACTION_TO_STATUS[target_action]
    now = datetime.now(timezone.utc)

    if row.eve_event_id == eve_event_id:
        if target_status == "ACCEPTED" and row.status == "ACCEPTED":
            _ensure_slot_selection_artifacts(db, request_row=row)
        return _serialize(row)

    if row.status == target_status:
        if target_status == "ACCEPTED":
            _ensure_slot_selection_artifacts(db, request_row=row)
        row.eve_event_id = eve_event_id
        row.updated_at = now
        db.flush()
        db.commit()
        return _serialize(row)

    if row.status != "PENDING":
        raise APIError(
            f"Cannot transition from '{row.status}' to '{target_status}'. Only PENDING requests can be accepted or declined.",
            status_code=409,
        )

    result = respond_to_candidate_request(
        db=db,
        request_id=adam_event_id,
        candidate_id=str(profile.candidate_id or ""),
        action=target_action,
    )
    row.eve_event_id = eve_event_id
    row.updated_at = now
    db.flush()
    db.commit()
    return result
