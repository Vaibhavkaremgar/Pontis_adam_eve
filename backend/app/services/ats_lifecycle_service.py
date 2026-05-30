from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.repositories import (
    CandidateLifecycleEventRepository,
    CandidateProfileRepository,
    CompanyRepository,
    InterviewRepository,
    InterviewSessionRepository,
    JobRepository,
    InboundEmailRepository,
    NotificationEventRepository,
    OutreachEventRepository,
)
from app.utils.observability import emit_trace

logger = logging.getLogger(__name__)

CANONICAL_ATS_STATES: tuple[str, ...] = (
    "sourced",
    "reviewed",
    "selected",
    "enriching",
    "enriched",
    "enrichment_failed",
    "outreach_pending",
    "outreach_sent",
    "replied_interested",
    "replied_not_interested",
    "interview_requested",
    "interview_scheduled",
    "interview_in_progress",
    "interview_no_show",
    "interview_completed",
    "evaluation_processing",
    "results_ready",
    "advanced",
    "second_round_requested",
    "second_round_scheduled",
    "final_round",
    "offer_stage",
    "offer_sent",
    "placed",
    "hired",
    "rejected",
    "search_closed",
    "archived",
)

_CANONICAL_SET = set(CANONICAL_ATS_STATES)

_LEGACY_TO_CANONICAL: dict[str, str] = {
    "new": "reviewed",
    "review_pending": "reviewed",
    "shortlisted": "selected",
    "enriching": "enriching",
    "enriched": "enriched",
    "enrichment_failed": "enrichment_failed",
    "outreach_pending": "outreach_pending",
    "outreach_queued": "outreach_pending",
    "contacted": "outreach_sent",
    "interview_scheduled": "interview_scheduled",
    "exported": "offer_sent",
    "rejected": "rejected",
    "booked": "interview_scheduled",
    "sent": "outreach_sent",
    "follow_up_sent": "outreach_sent",
    "followup_sent": "outreach_sent",
    "responded": "replied_interested",
    "qualified": "replied_interested",
    "awaiting_resume": "replied_interested",
    "declined": "replied_not_interested",
    "do_not_contact": "archived",
}

_TRANSITION_ORDER: dict[str, set[str]] = {
    "reviewed": {"sourced", "enriched", "selected", "rejected", "archived"},
    "sourced": {"enriched", "reviewed", "selected", "rejected", "archived"},
    "selected": {"enriching", "rejected", "archived"},
    "enriching": {"enriched", "enrichment_failed", "selected", "rejected", "archived"},
    "enrichment_failed": {"selected", "reviewed", "archived"},
    "enriched": {"reviewed", "outreach_pending", "outreach_sent", "rejected", "archived"},
    "outreach_pending": {"outreach_sent", "rejected", "archived"},
    "outreach_sent": {"replied_interested", "replied_not_interested", "archived", "interview_requested"},
    "replied_interested": {"interview_requested", "interview_scheduled", "advanced", "final_round", "offer_sent", "rejected"},
    "replied_not_interested": {"archived", "rejected"},
    "interview_requested": {"interview_scheduled", "archived", "rejected"},
    "interview_scheduled": {"interview_completed", "advanced", "final_round", "rejected", "archived"},
    "interview_in_progress": {"interview_completed", "evaluation_processing", "interview_no_show", "rejected", "archived"},
    "interview_no_show": {"interview_scheduled", "archived", "rejected"},
    "interview_completed": {"advanced", "final_round", "offer_sent", "rejected", "archived"},
    "evaluation_processing": {"results_ready", "interview_completed", "rejected", "archived"},
    "results_ready": {"advanced", "second_round_requested", "final_round", "offer_stage", "offer_sent", "hired", "rejected", "search_closed", "archived"},
    "advanced": {"second_round_requested", "second_round_scheduled", "final_round", "offer_stage", "placed", "offer_sent", "hired", "rejected", "search_closed", "archived"},
    "second_round_requested": {"second_round_scheduled", "rejected", "search_closed", "archived"},
    "second_round_scheduled": {"offer_stage", "final_round", "placed", "rejected", "search_closed", "archived"},
    "final_round": {"offer_sent", "hired", "rejected", "archived"},
    "offer_stage": {"placed", "search_closed", "offer_sent", "hired", "rejected", "archived"},
    "offer_sent": {"placed", "hired", "rejected", "search_closed", "archived"},
    "placed": {"search_closed", "archived"},
    "search_closed": {"archived"},
    "hired": {"archived"},
    "rejected": {"selected", "archived"},
    "archived": set(),
}


def normalize_ats_status(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace("-", "_")
    if normalized in _CANONICAL_SET:
        return normalized
    return _LEGACY_TO_CANONICAL.get(normalized, normalized or "reviewed")


def _transition_key(*, job_id: str, candidate_id: str, to_status: str, source: str, actor_id: str | None, metadata: dict[str, Any]) -> str:
    payload = {
        "jobId": job_id,
        "candidateId": candidate_id,
        "toStatus": to_status,
        "source": source,
        "actorId": actor_id or "",
        "metadata": metadata,
    }
    digest = hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()
    return digest


def get_candidate_ats_state(*, db: Session, job_id: str, candidate_id: str) -> str:
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if not profile:
        return "reviewed"
    return normalize_ats_status(getattr(profile, "ats_status", "") or getattr(profile, "candidate_status", ""))


def transition_candidate_ats_state(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    to_status: str,
    source: str = "system",
    actor_id: str | None = None,
    slack_team_id: str = "",
    slack_user_id: str = "",
    slack_installation_id: str | None = None,
    reason: str = "",
    metadata: dict[str, Any] | None = None,
    allow_idempotent: bool = True,
) -> dict[str, Any]:
    job = JobRepository(db).get(job_id)
    if not job:
        raise ValueError("Job not found")

    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if not profile:
        profile = CandidateProfileRepository(db).ensure_candidate_profile(job_id=job_id, candidate_id=candidate_id)

    current_status = normalize_ats_status(getattr(profile, "ats_status", "") or getattr(profile, "candidate_status", ""))
    target_status = normalize_ats_status(to_status)
    if current_status == target_status and allow_idempotent:
        return {
            "jobId": job_id,
            "candidateId": candidate_id,
            "fromStatus": current_status,
            "toStatus": target_status,
            "duplicate": True,
            "status": target_status,
        }

    allowed_targets = _TRANSITION_ORDER.get(current_status, set())
    if target_status not in allowed_targets and current_status != target_status:
        logger.info(
            "ats_transition_adjusted job_id=%s candidate_id=%s from=%s requested=%s source=%s",
            job_id,
            candidate_id,
            current_status,
            target_status,
            source,
        )

    now = datetime.now(timezone.utc)
    payload = dict(metadata or {})
    payload.setdefault("reason", reason)
    payload.setdefault("source", source)
    payload.setdefault("jobId", job_id)
    payload.setdefault("candidateId", candidate_id)

    transition_key = _transition_key(
        job_id=job_id,
        candidate_id=candidate_id,
        to_status=target_status,
        source=source,
        actor_id=actor_id,
        metadata=payload,
    )
    event_repo = CandidateLifecycleEventRepository(db)
    try:
        event = event_repo.create(
            job_id=job_id,
            company_id=str(job.company_id),
            candidate_id=candidate_id,
            from_status=current_status,
            to_status=target_status,
            source=source,
            actor_id=actor_id,
            transition_key=transition_key,
            event_metadata=payload,
        )
        event.company_id = str(job.company_id)
        event.slack_team_id = (slack_team_id or getattr(event, "slack_team_id", "") or "").strip()
        event.slack_user_id = (slack_user_id or getattr(event, "slack_user_id", "") or "").strip()
        event.slack_installation_id = (slack_installation_id or getattr(event, "slack_installation_id", None) or "").strip() or None
        db.flush()
    except IntegrityError:
        # If the transition already exists, treat the request as idempotent.
        db.rollback()
        return {
            "jobId": job_id,
            "candidateId": candidate_id,
            "fromStatus": current_status,
            "toStatus": target_status,
            "duplicate": True,
            "status": target_status,
        }

    profile.ats_status = target_status
    profile.candidate_status = target_status
    profile.ats_status_source = (source or "system").strip().lower() or "system"
    profile.ats_status_reason = reason.strip()
    profile.ats_status_updated_at = now
    profile.raw_data = {
        **dict(profile.raw_data or {}),
        "candidate_status": target_status,
        "ats_status": target_status,
    }
    profile.ats_metadata = {
        **dict(profile.ats_metadata or {}),
        **payload,
        "transitionKey": transition_key,
        "updatedAt": now.isoformat(),
    }
    db.flush()

    emit_trace(
        logger,
        "ats_transition_recorded",
        job_id=job_id,
        candidate_id=candidate_id,
        from_status=current_status,
        to_status=target_status,
        source=source,
    )
    return {
        "jobId": job_id,
        "candidateId": candidate_id,
        "fromStatus": current_status,
        "toStatus": target_status,
        "status": target_status,
        "eventId": event.id,
    }


def candidate_timeline(*, db: Session, job_id: str, candidate_id: str, limit: int = 100) -> list[dict[str, Any]]:
    job = JobRepository(db).get(job_id)
    if not job:
        return []

    entries: list[dict[str, Any]] = []
    for row in CandidateLifecycleEventRepository(db).list_for_candidate(job_id=job_id, candidate_id=candidate_id, limit=limit):
        entries.append(
            {
                "type": "ats_transition",
                "jobId": row.job_id,
                "candidateId": row.candidate_id,
                "fromStatus": row.from_status,
                "toStatus": row.to_status,
                "source": row.source,
                "metadata": dict(row.event_metadata or {}),
                "createdAt": row.created_at.isoformat(),
            }
        )

    for row in OutreachEventRepository(db).list_for_job(job_id):
        if row.candidate_id != candidate_id:
            continue
        entries.append(
            {
                "type": "outreach",
                "jobId": row.job_id,
                "candidateId": row.candidate_id,
                "status": row.status,
                "provider": row.provider,
                "providerMessageId": row.provider_message_id,
                "replyState": getattr(row, "reply_state", "") or "",
                "lastError": row.last_error,
                "createdAt": row.created_at.isoformat(),
            }
        )

    for row in InboundEmailRepository(db).list_for_candidate(job_id=job_id, candidate_id=candidate_id, limit=50):
        entries.append(
            {
                "type": "inbound_reply",
                "jobId": row.job_id,
                "candidateId": row.candidate_id,
                "status": row.processing_status,
                "matchStatus": row.match_status,
                "intent": row.intent,
                "senderEmail": row.sender_email,
                "subject": row.subject,
                "attachments": int(row.attachment_count or 0),
                "createdAt": row.received_at.isoformat(),
            }
        )

    interview = InterviewRepository(db).get_by_job_and_candidate(job_id, candidate_id)
    if interview:
        entries.append(
            {
                "type": "interview",
                "jobId": job_id,
                "candidateId": candidate_id,
                "status": interview.status,
                "createdAt": interview.created_at.isoformat(),
            }
        )

    session = InterviewSessionRepository(db).get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
    if session:
        entries.append(
            {
                "type": "interview_session",
                "jobId": job_id,
                "candidateId": candidate_id,
                "status": session.status,
                "token": session.token,
                "scheduledAt": session.scheduled_at.isoformat() if session.scheduled_at else None,
                "bookingUrl": session.booking_url,
                "createdAt": session.created_at.isoformat(),
            }
        )

    for row in NotificationEventRepository(db).list_for_job(job_id):
        if row.candidate_id and row.candidate_id != candidate_id:
            continue
        entries.append(
            {
                "type": "notification",
                "jobId": row.job_id,
                "candidateId": row.candidate_id,
                "channel": row.channel,
                "recipientType": row.recipient_type,
                "recipient": row.recipient,
                "status": row.status,
                "notificationType": row.notification_type,
                "title": row.title,
                "createdAt": row.created_at.isoformat(),
            }
        )

    entries.sort(key=lambda item: item.get("createdAt", ""), reverse=True)
    return entries[: max(1, limit)]
