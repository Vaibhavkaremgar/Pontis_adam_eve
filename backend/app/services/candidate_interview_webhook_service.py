"""
Candidate interview Vapi webhook handler.

This is SEPARATE from vapi_webhook_service.py which handles the recruiter
job-intake flow (writes to job_intakes).  This handler is invoked when the
Interview Project's Vapi call carries `candidateId` in the call metadata,
meaning it is a candidate AI interview, not a recruiter intake call.

Integration pattern
-------------------
The Interview Project:
  1. Reads session context via GET /interview/session/context?token=<session_token>
  2. Starts a Vapi call with metadata: {candidateId, jobId, sessionToken, agencyId}
  3. On call end, Vapi fires POST /webhooks/vapi
  4. Adam routes to this handler (candidateId present in metadata)
  5. This handler writes transcript/scores/video to the `interviews` table
  6. ATS state transitions to interview_completed
  7. Results UI reads from interviews table automatically (no extra work needed)

Alternatively the Interview Project may call POST /interviews/results directly
with X-Internal-API-Key — that path already exists in interviews.py route and
is fully functional.  This webhook handler is the fallback/alternative path.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.repositories import (
    CandidateProfileRepository,
    InterviewRepository,
    InterviewSessionRepository,
    JobRepository,
    NotificationWorkflowTokenRepository,
)
from app.services.ats_lifecycle_service import transition_candidate_ats_state
from app.services.audit_service import record_audit_event
from app.services.lifecycle_service import record_job_lifecycle_event
from app.services.notification_intelligence_service import route_recruiter_notification
from app.services.redis_service import get_redis

logger = logging.getLogger(__name__)

_CANDIDATE_INTERVIEW_DEDUPE_PREFIX = "pontis:vapi:candidate_interview:"
_DEDUPE_TTL_SECONDS = 48 * 60 * 60
_TERMINAL_EVENT_TYPES = {"end-of-call-report", "call-ended", "call.ended", "call.completed"}


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _dedupe_key(call_id: str) -> str:
    return f"{_CANDIDATE_INTERVIEW_DEDUPE_PREFIX}{call_id}"


def _mark_processed(call_id: str) -> bool:
    """Returns True if this call_id was NOT already processed (i.e. we own it)."""
    redis = get_redis()
    if redis is None:
        return True  # no Redis → allow processing (idempotency via DB state)
    try:
        return bool(redis.set(_dedupe_key(call_id), "1", nx=True, ex=_DEDUPE_TTL_SECONDS))
    except Exception:
        return True


def _resolve_session(
    db: Session,
    *,
    session_token: str,
    job_id: str,
    candidate_id: str,
    agency_id: str,
) -> Any | None:
    """
    Resolve and validate the interview session.

    Security: verifies the session belongs to the correct candidate + job + agency.
    Returns None if the session is invalid or already completed.
    """
    session = InterviewSessionRepository(db).get_by_token(session_token) if session_token else None
    if not session:
        # Fall back to job+candidate lookup
        session = InterviewSessionRepository(db).get_by_job_and_candidate(
            job_id=job_id, candidate_id=candidate_id
        )
    if not session:
        logger.warning(
            "candidate_interview_webhook_session_not_found token=%s job_id=%s candidate_id=%s",
            session_token, job_id, candidate_id,
        )
        return None

    # Authorization: verify session belongs to the correct candidate + job + agency
    session_candidate = _normalize(session.candidate_id)
    session_job = _normalize(session.job_id)
    session_agency = _normalize(session.agency_id)

    if session_candidate != _normalize(candidate_id):
        logger.warning(
            "candidate_interview_webhook_candidate_mismatch session_candidate=%s request_candidate=%s",
            session_candidate, candidate_id,
        )
        return None
    if session_job != _normalize(job_id):
        logger.warning(
            "candidate_interview_webhook_job_mismatch session_job=%s request_job=%s",
            session_job, job_id,
        )
        return None
    if agency_id and session_agency and session_agency != _normalize(agency_id):
        logger.warning(
            "candidate_interview_webhook_agency_mismatch session_agency=%s request_agency=%s",
            session_agency, agency_id,
        )
        return None

    # Idempotency: if already completed, skip
    session_status = _normalize(session.status).lower()
    if session_status in {"completed", "interview_completed", "results_ready"}:
        logger.info(
            "candidate_interview_webhook_already_completed session_token=%s",
            session.session_token,
        )
        return None

    return session


def process_candidate_interview_webhook(
    *,
    db: Session,
    event_type: str,
    call_id: str,
    candidate_id: str,
    job_id: str,
    agency_id: str,
    session_token: str,
    transcript: str,
    recording_url: str,
    ended_at: str,
    assistant_id: str,
    webhook_id: str,
) -> dict[str, Any]:
    """
    Process a terminal Vapi event for a candidate AI interview.

    Called by process_vapi_webhook() when candidateId is present in call metadata.
    Writes transcript + recording to the interviews table and transitions ATS state.
    """
    if event_type not in _TERMINAL_EVENT_TYPES:
        return {"ignored": True, "reason": "non_terminal_event", "event_type": event_type}

    if not candidate_id or not job_id:
        return {"ignored": True, "reason": "missing_candidate_or_job_metadata"}

    # Redis deduplication — one processing per call_id
    if call_id and not _mark_processed(call_id):
        logger.info(
            "candidate_interview_webhook_deduped call_id=%s candidate_id=%s",
            call_id, candidate_id,
        )
        return {"ignored": True, "reason": "duplicate_call_id", "call_id": call_id}

    # Validate job exists
    job = JobRepository(db).get(job_id)
    if not job:
        logger.warning("candidate_interview_webhook_job_not_found job_id=%s", job_id)
        return {"ignored": True, "reason": "job_not_found", "job_id": job_id}

    # Validate candidate exists
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if not profile:
        logger.warning(
            "candidate_interview_webhook_candidate_not_found job_id=%s candidate_id=%s",
            job_id, candidate_id,
        )
        return {"ignored": True, "reason": "candidate_not_found", "candidate_id": candidate_id}

    # Resolve and authorize session
    resolved_agency_id = agency_id or _normalize(getattr(job, "company_id", "") or "")
    session = _resolve_session(
        db,
        session_token=session_token,
        job_id=job_id,
        candidate_id=candidate_id,
        agency_id=resolved_agency_id,
    )
    if not session:
        return {"ignored": True, "reason": "session_invalid_or_completed"}

    # Resolve workflow token for the interviews row
    workflow_token = _normalize(
        (dict(session.scheduling_metadata or {}).get("workflowToken") or "")
        or session.session_token
    )
    token_row = NotificationWorkflowTokenRepository(db).get_by_token(workflow_token, source_app="ui")

    now = datetime.now(timezone.utc)

    # Write transcript + recording to interviews table (existing infrastructure)
    interview_repo = InterviewRepository(db)
    interview_row = interview_repo.get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
    if not interview_row:
        # Ensure the interviews row exists (upsert_status creates it if missing)
        interview_repo.upsert_status(
            job_id=job_id,
            candidate_id=candidate_id,
            status="interview_scheduled",
            async_token=workflow_token or None,
        )

    # Write results using the existing upsert_interview_results path
    # (same path used by POST /interviews/results callback)
    interview_repo.upsert_interview_results(
        job_id=job_id,
        candidate_id=candidate_id,
        result_data={
            "transcript": transcript,
            "video_url": recording_url or None,
            "ai_summary": "",          # Interview Project fills this via POST /interviews/results
            "feedback": "",
            "interviewer_notes": "",
            "interview_score": None,
            "technical_score": None,
            "communication_score": None,
            "culture_fit_score": None,
            "completed_at": now,
        },
    )

    # Update session stage to completed
    session.stage = "completed"
    session.evaluation_status = "completed"
    session.evaluation_ready_at = session.evaluation_ready_at or now
    session.scheduling_metadata = {
        **dict(session.scheduling_metadata or {}),
        "interviewCompletedAt": now.isoformat(),
        "callId": call_id,
        "assistantId": assistant_id,
        "recordingUrl": recording_url,
        "endedAt": ended_at,
        "source": "vapi_candidate_webhook",
    }
    db.flush()

    # ATS state transition: interview_completed
    transition_candidate_ats_state(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        to_status="interview_completed",
        source="candidate_interview_webhook",
        reason="vapi_call_ended",
        metadata={
            "callId": call_id,
            "sessionToken": session.session_token,
            "workflowToken": workflow_token,
            "recordingUrl": recording_url,
            "endedAt": ended_at,
        },
    )

    # Recruiter notification
    recruiter_id = JobRepository(db).get_recruiter_id(job_id)
    candidate_name = _normalize(getattr(profile, "name", "") or candidate_id)
    route_recruiter_notification(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        notification_key=f"candidate-interview-completed:{job_id}:{candidate_id}:{call_id}",
        notification_type="interview_completed",
        title="Interview completed",
        body=f"AI interview completed for {candidate_name}. Results are ready.",
        metadata={
            "callId": call_id,
            "sessionToken": session.session_token,
            "workflowToken": workflow_token,
            "recordingUrl": recording_url,
        },
    )

    record_job_lifecycle_event(
        db=db,
        job_id=job_id,
        event_type="INTERVIEW_COMPLETED",
        payload={
            "jobId": job_id,
            "candidateId": candidate_id,
            "recruiterId": recruiter_id,
            "sessionToken": session.session_token,
            "workflowToken": workflow_token,
            "callId": call_id,
            "recordingUrl": recording_url,
            "endedAt": ended_at,
            "source": "vapi_candidate_webhook",
        },
        source="candidate_interview_webhook",
    )

    record_audit_event(
        db=db,
        actor_id=None,
        action="candidate_interview_completed",
        entity_type="interview_session",
        entity_id=session.id,
        metadata={
            "jobId": job_id,
            "candidateId": candidate_id,
            "callId": call_id,
            "sessionToken": session.session_token,
            "workflowToken": workflow_token,
        },
    )

    logger.info(
        "candidate_interview_webhook_processed job_id=%s candidate_id=%s call_id=%s transcript_length=%s",
        job_id, candidate_id, call_id, len(transcript),
    )

    return {
        "processed": True,
        "job_id": job_id,
        "candidate_id": candidate_id,
        "call_id": call_id,
        "session_token": session.session_token,
        "workflow_token": workflow_token,
        "transcript_length": len(transcript),
        "recording_url": recording_url,
    }
