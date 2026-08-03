from __future__ import annotations

import asyncio
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
    OrchestrationSessionRepository,
)
from app.services.slack_integration import post_slack_message
from app.services.slack_tenant_service import SlackCompanyResolver
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
    "warm",
    "disqualified",
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
    "offer_accepted": "placed",
    "do_not_contact": "archived",
}

_TRANSITION_ORDER: dict[str, set[str]] = {
    "reviewed": {"sourced", "enriched", "selected", "rejected", "disqualified", "warm", "archived"},
    "sourced": {"enriched", "reviewed", "selected", "rejected", "disqualified", "warm", "archived"},
    "selected": {"enriching", "rejected", "disqualified", "warm", "archived"},
    "enriching": {"enriched", "enrichment_failed", "selected", "rejected", "disqualified", "warm", "archived"},
    "enrichment_failed": {"selected", "reviewed", "archived"},
    "enriched": {"reviewed", "outreach_pending", "outreach_sent", "rejected", "disqualified", "warm", "archived"},
    "outreach_pending": {"outreach_sent", "rejected", "disqualified", "warm", "archived"},
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
    "warm": {"reviewed", "archived"},
    "disqualified": {"archived"},
    "placed": {"search_closed", "archived"},
    "search_closed": {"archived"},
    "hired": {"archived"},
    "rejected": {"selected", "archived"},
    "archived": set(),
}

_ACQUISITION_STATES = {
    "discovered",
    "queued",
    "connection_sent",
    "pending_acceptance",
    "accepted",
    "message_queued",
    "message_sent",
    "waiting_for_eve",
    "handoff",
    "failed",
    "blocked",
    "retrying",
}


def normalize_ats_status(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace("-", "_")
    if normalized in _CANONICAL_SET:
        return normalized
    return _LEGACY_TO_CANONICAL.get(normalized, normalized or "reviewed")


async def _post_search_closed_slack_message(*, db: Session, job_id: str, candidate_id: str) -> None:
    job = JobRepository(db).get(job_id)
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    session = OrchestrationSessionRepository(db).get_by_job(job_id)
    slack_context = dict(getattr(session, "slack_context", {}) or {}) if session else {}
    channel_id = str(slack_context.get("channelId") or slack_context.get("channel_id") or "").strip()
    company_id = str(slack_context.get("companyId") or slack_context.get("company_id") or getattr(session, "company_id", "") or getattr(job, "company_id", "") or "").strip()
    if not job or not profile or not channel_id or not company_id:
        return

    try:
        bot_token = SlackCompanyResolver(db).resolve_bot_token(company_id=company_id)
    except Exception as exc:
        logger.warning("search_closed_slack_token_failed job_id=%s error=%s", job_id, str(exc), exc_info=exc)
        return
    if not bot_token:
        return

    candidate_name = str(getattr(profile, "name", "") or candidate_id).strip()
    job_title = str(getattr(job, "title", "") or "").strip()
    try:
        await post_slack_message(
            channel_id=channel_id,
            text=f"Search complete. 1 placement confirmed - {candidate_name} for {job_title}",
            bot_token=bot_token,
        )
    except Exception as exc:
        logger.warning("search_closed_slack_post_failed job_id=%s error=%s", job_id, str(exc), exc_info=exc)
        return

    logger.info("search_closed_slack_posted job_id=%s", job_id)


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
    if target_status == "placed":
        try:
            asyncio.run(_post_search_closed_slack_message(db=db, job_id=job_id, candidate_id=candidate_id))
        except RuntimeError:
            pass
        except Exception as exc:
            logger.warning("search_closed_slack_post_error job_id=%s candidate_id=%s error=%s", job_id, candidate_id, str(exc), exc_info=exc)
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
        from_status = (row.from_status or "").strip().lower()
        to_status = (row.to_status or "").strip().lower()
        event_type = "ats_transition"
        if from_status in _ACQUISITION_STATES or to_status in _ACQUISITION_STATES:
            event_type = "candidate_engagement"
        entries.append(
            {
                "type": event_type,
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
