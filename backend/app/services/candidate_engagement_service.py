from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.db.repositories import (
    CandidateLifecycleEventRepository,
    CandidateProfileRepository,
    JobRepository,
)
from app.linkedin.models import LinkedInConnectionEntity, LinkedInConversationEntity, LinkedInMessageEntity
from app.linkedin.playwright.browser_manager import BrowserManager
from app.linkedin.playwright.profile_inspector import LinkedInProfileInspector
from app.linkedin.playwright.profile_types import LinkedInProfileConnectionState
from app.linkedin.profile_resolver import has_linkedin_configuration
from app.linkedin.repository import LinkedInConnectionRepository
from app.linkedin.workers.messaging_worker import LinkedInMessagingWorker
from app.linkedin.workers.messaging_types import LinkedInMessagingWorkerStatus
from app.services.communication_service import CommunicationService
from app.services.interview_link_providers import get_booking_link
from app.services.job_queue_service import enqueue_job
from app.services.redis_service import rate_limit_check
from app.utils.exceptions import QueueError

logger = logging.getLogger(__name__)

ACQUISITION_DISCOVERED = "DISCOVERED"
ACQUISITION_QUEUED = "QUEUED"
ACQUISITION_CONNECTION_SENT = "CONNECTION_SENT"
ACQUISITION_PENDING_ACCEPTANCE = "PENDING_ACCEPTANCE"
ACQUISITION_ACCEPTED = "ACCEPTED"
ACQUISITION_MESSAGE_QUEUED = "MESSAGE_QUEUED"
ACQUISITION_MESSAGE_SENT = "MESSAGE_SENT"
ACQUISITION_WAITING_FOR_EVE = "WAITING_FOR_EVE"
ACQUISITION_HANDOFF = "HANDOFF"
ACQUISITION_FAILED = "FAILED"
ACQUISITION_BLOCKED = "BLOCKED"
ACQUISITION_RETRYING = "RETRYING"

_ACQUISITION_EVENT_LABELS = {
    ACQUISITION_DISCOVERED: "Candidate Discovered",
    ACQUISITION_CONNECTION_SENT: "Connection Sent",
    ACQUISITION_ACCEPTED: "Connection Accepted",
    ACQUISITION_MESSAGE_QUEUED: "Message Queued",
    ACQUISITION_MESSAGE_SENT: "Message Sent",
    ACQUISITION_WAITING_FOR_EVE: "Waiting For Eve",
    ACQUISITION_HANDOFF: "Handoff",
}

_ACCEPTED_STATES = {
    LinkedInProfileConnectionState.CONNECTED,
    LinkedInProfileConnectionState.MESSAGE_AVAILABLE,
    LinkedInProfileConnectionState.ALREADY_CONNECTED,
}
_PENDING_STATES = {LinkedInProfileConnectionState.REQUEST_PENDING}
_RESTRICTED_STATES = {
    LinkedInProfileConnectionState.LOGIN_REQUIRED,
    LinkedInProfileConnectionState.ACCOUNT_RESTRICTED,
    LinkedInProfileConnectionState.SESSION_EXPIRED,
}
_DECLINED_STATES = {
    LinkedInProfileConnectionState.FOLLOW_ONLY,
    LinkedInProfileConnectionState.PRIVATE_PROFILE,
    LinkedInProfileConnectionState.PROFILE_NOT_FOUND,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _linkedin_configuration_missing(account_id: str) -> bool:
    account_id = _normalize_text(account_id)
    if not account_id:
        return True
    return not has_linkedin_configuration(account_id)


def _transition_key(*, job_id: str, candidate_id: str, from_status: str, to_status: str, source: str, metadata: dict[str, Any]) -> str:
    payload = {
        "jobId": _normalize_text(job_id),
        "candidateId": _normalize_text(candidate_id),
        "fromStatus": _normalize_text(from_status).upper(),
        "toStatus": _normalize_text(to_status).upper(),
        "source": _normalize_text(source).lower(),
        "metadata": metadata,
    }
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()


def _current_connection_status(value: Any) -> str:
    status = _normalize_text(value).lower()
    return status or "unknown"


def _current_acquisition_status(profile: Any) -> str:
    status = _normalize_text(getattr(profile, "acquisition_status", "")).upper()
    return status or ACQUISITION_DISCOVERED


def _progress_for_status(status: str) -> str:
    normalized = _normalize_text(status).upper()
    return {
        ACQUISITION_DISCOVERED: "Candidate sourced and normalized",
        ACQUISITION_QUEUED: "Queued for connection work",
        ACQUISITION_CONNECTION_SENT: "Connection request sent",
        ACQUISITION_PENDING_ACCEPTANCE: "Waiting on LinkedIn acceptance",
        ACQUISITION_ACCEPTED: "Connection accepted",
        ACQUISITION_MESSAGE_QUEUED: "Message queued for delivery",
        ACQUISITION_MESSAGE_SENT: "Message delivered",
        ACQUISITION_WAITING_FOR_EVE: "Candidate is being handed off to Eve",
        ACQUISITION_HANDOFF: "Handoff completed",
        ACQUISITION_BLOCKED: "Blocked by account or profile restrictions",
        ACQUISITION_RETRYING: "Retrying after a transient failure",
        ACQUISITION_FAILED: "Failed",
    }.get(normalized, "In progress")


def _source_category(profile: Any) -> str:
    raw_data = getattr(profile, "raw_data", {})
    if not isinstance(raw_data, dict):
        raw_data = {}
    source_type = _normalize_text(raw_data.get("source_type") or raw_data.get("sourceType"))
    source_provider = _normalize_text(raw_data.get("source_provider") or raw_data.get("sourceProvider") or raw_data.get("source"))
    source_hint = (source_type or source_provider).lower()
    if any(token in source_hint for token in ("internal", "manual", "referral", "ats")):
        return "internal"
    return "serp"


def _ensure_engagement_metadata(profile: Any) -> dict[str, Any]:
    raw_data = getattr(profile, "raw_data", {})
    if not isinstance(raw_data, dict):
        raw_data = {}
    engagement = dict(raw_data.get("engagement") or {})
    return engagement


def _record_transition(
    db,
    *,
    job_id: str,
    company_id: str,
    candidate_id: str,
    from_status: str,
    to_status: str,
    source: str,
    actor_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    repo = CandidateLifecycleEventRepository(db)
    try:
        repo.create(
            job_id=job_id,
            company_id=company_id,
            candidate_id=candidate_id,
            from_status=from_status,
            to_status=to_status,
            source=source,
            actor_id=actor_id,
            transition_key=_transition_key(
                job_id=job_id,
                candidate_id=candidate_id,
                from_status=from_status,
                to_status=to_status,
                source=source,
                metadata=dict(metadata or {}),
            ),
            event_metadata=dict(metadata or {}),
        )
        db.commit()
    except IntegrityError:
        db.rollback()


def _transition_candidate(
    db,
    *,
    job_id: str,
    candidate_id: str,
    status: str,
    reason: str,
    source: str,
    actor_id: str | None = None,
    last_error: str = "",
    queue_job_id: str = "",
    idempotency_key: str = "",
    account_id: str = "",
    retry_count: int | None = None,
    priority: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any | None:
    repo = CandidateProfileRepository(db)
    profile = repo.get(job_id=job_id, candidate_id=candidate_id) or repo.find_by_linkedin_url(job_id=job_id, linkedin_url="")
    if not profile:
        return None
    from_status = _current_acquisition_status(profile)
    row = repo.update_acquisition_state(
        job_id=job_id,
        candidate_id=candidate_id,
        status=status,
        reason=reason,
        last_error=last_error,
        queue_job_id=queue_job_id,
        idempotency_key=idempotency_key,
        account_id=account_id,
        retry_count=retry_count,
        priority=priority,
    )
    if row is not None:
        row.raw_data = dict(row.raw_data or {})
        engagement = _ensure_engagement_metadata(row)
        engagement.update(
            {
                "currentStage": status,
                "connectionStatus": status,
                "invitationStatus": status,
                "currentProgress": _progress_for_status(status),
                "updatedAt": _utcnow().isoformat(),
                "reason": reason,
            }
        )
        row.raw_data["engagement"] = engagement
        row.raw_data["candidate_status"] = status.lower()
        row.raw_data["acquisition_status"] = status.upper()
        row.acquisition_updated_at = _utcnow()
        db.flush()
        _record_transition(
            db,
            job_id=job_id,
            company_id=str(getattr(profile, "company_id", "") or getattr(profile, "agency_id", "") or ""),
            candidate_id=candidate_id,
            from_status=from_status,
            to_status=status,
            source=source,
            actor_id=actor_id,
            metadata={
                "reason": reason,
                "lastError": last_error,
                "queueJobId": queue_job_id,
                "idempotencyKey": idempotency_key,
                "accountId": account_id,
                "retryCount": retry_count,
                "priority": priority,
                **dict(metadata or {}),
            },
        )
    return row


class CommunicationService:
    """Generic message renderer for recruiter-to-candidate LinkedIn copy."""

    DEFAULT_TEMPLATE = (
        "Hi {candidate_name},\n\n"
        "{recruiter_name} from {company} thought your background could be a strong fit for {job_title}.\n"
        "If you'd like to continue, you can book time here: {eve_link}\n\n"
        "Best,\n"
        "{recruiter_name}"
    )

    def render_message(
        self,
        *,
        recruiter_name: str,
        company: str,
        job_title: str,
        candidate_name: str,
        eve_link: str,
        template: str = "",
    ) -> str:
        values = {
            "recruiter_name": _normalize_text(recruiter_name) or "Recruiter",
            "company": _normalize_text(company) or "our company",
            "job_title": _normalize_text(job_title) or "the role",
            "candidate_name": _normalize_text(candidate_name) or "there",
            "eve_link": _normalize_text(eve_link),
        }
        raw_template = _normalize_text(template) or self.DEFAULT_TEMPLATE
        rendered = raw_template.format_map(_SafeFormatDict(values))
        return "\n".join(line.rstrip() for line in rendered.splitlines()).strip()


class _SafeFormatDict(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def _candidate_display_name(profile: Any, fallback: str = "") -> str:
    return _normalize_text(getattr(profile, "name", "") or fallback)


def _candidate_job_title(job: Any, profile: Any) -> str:
    return _normalize_text(getattr(job, "title", "") or getattr(profile, "current_title", "") or getattr(profile, "current_role", ""))


def _company_name(job: Any, profile: Any) -> str:
    return _normalize_text(
        getattr(job, "company_name", "")
        or getattr(profile, "current_company", "")
        or getattr(profile, "company", "")
        or getattr(profile, "company_name", "")
    )


def _recruiter_name(db, job: Any, recruiter_name: str) -> str:
    if _normalize_text(recruiter_name):
        return _normalize_text(recruiter_name)
    try:
        from app.models.entities import UserEntity

        if getattr(job, "created_by", None):
            user = db.get(UserEntity, str(job.created_by))
            if user and _normalize_text(getattr(user, "full_name", "")):
                return _normalize_text(user.full_name)
    except Exception:
        pass
    return "Recruiter"


def _persist_message_row(
    *,
    db,
    candidate_id: str,
    account_id: str,
    message_text: str,
) -> str:
    now = _utcnow()
    conversation = (
        db.query(LinkedInConversationEntity)
        .filter(LinkedInConversationEntity.candidate_id == candidate_id)
        .first()
    )
    if conversation is None:
        conversation = LinkedInConversationEntity(
            id=str(uuid4()),
            candidate_id=candidate_id,
            account_id=account_id,
            conversation_id="",
            conversation_status="unknown",
            last_message_at=None,
            last_synced_at=None,
            created_at=now,
            updated_at=now,
        )
        db.add(conversation)
        db.flush()

    existing = (
        db.query(LinkedInMessageEntity)
        .filter(
            LinkedInMessageEntity.candidate_id == candidate_id,
            LinkedInMessageEntity.message_type == "outreach",
            LinkedInMessageEntity.sent_at.is_(None),
        )
        .first()
    )
    if existing is None:
        existing = LinkedInMessageEntity(
            id=str(uuid4()),
            conversation_id=conversation.id,
            candidate_id=candidate_id,
            sender_type="system",
            message_type="outreach",
            message_text=message_text,
            linkedin_message_id="",
            attachment_count=0,
            sent_at=None,
            created_at=now,
        )
        db.add(existing)
    else:
        existing.conversation_id = conversation.id
        existing.message_text = message_text
        existing.created_at = now
    db.flush()
    return str(conversation.id)


def _mark_message_sent(*, db, candidate_id: str, message_text: str) -> None:
    now = _utcnow()
    message_row = (
        db.query(LinkedInMessageEntity)
        .filter(
            LinkedInMessageEntity.candidate_id == candidate_id,
            LinkedInMessageEntity.message_type == "outreach",
            LinkedInMessageEntity.message_text == message_text,
        )
        .order_by(LinkedInMessageEntity.created_at.desc())
        .first()
    )
    if message_row is None:
        return
    message_row.sent_at = now
    message_row.created_at = message_row.created_at or now
    db.flush()


def _map_acceptance_state(state: LinkedInProfileConnectionState) -> tuple[str, str, str]:
    if state in _ACCEPTED_STATES:
        return "accepted", ACQUISITION_ACCEPTED, "accepted_by_linkedin"
    if state in _PENDING_STATES:
        return "pending", ACQUISITION_PENDING_ACCEPTANCE, "awaiting_linkedin_acceptance"
    if state in _RESTRICTED_STATES:
        return "restricted", ACQUISITION_BLOCKED, "linkedin_account_or_profile_restricted"
    if state in _DECLINED_STATES:
        return "declined", ACQUISITION_DECLINED, "connection_request_declined_or_removed"
    return "unknown", ACQUISITION_RETRYING, "acceptance_state_unknown"


async def _inspect_connections_for_account(
    *,
    account_id: str,
    rows: list[Any],
    timeout_ms: int,
    message_template: str,
    event_actor_id: str | None = None,
) -> dict[str, int]:
    from app.db.session import SessionLocal
    from app.linkedin.repository import LinkedInAccountRepository

    summary = {"checked": 0, "accepted": 0, "pending": 0, "declined": 0, "restricted": 0, "unknown": 0, "queued_messages": 0}
    if _linkedin_configuration_missing(account_id):
        logger.info("linkedin_acceptance_skipped account_id=%s reason=linkedin_not_configured", account_id)
        return summary
    browser_manager = BrowserManager(account_id=account_id)
    context = None
    try:
        context = await browser_manager.get_browser()
        inspector = LinkedInProfileInspector(context, timeout_ms=timeout_ms)
        for row in rows:
            candidate_id = _normalize_text(row.candidate_id)
            profile_url = _normalize_text(row.linkedin_url)
            if not candidate_id or not profile_url:
                continue
            previous_status = ""
            try:
                inspection = await inspector.inspect(profile_url)
                state = inspection.connection_state
            except Exception as exc:
                logger.exception("linkedin_acceptance_inspect_failed account_id=%s candidate_id=%s", account_id, candidate_id)
                state = LinkedInProfileConnectionState.UNKNOWN
                inspection = None
                exc_message = str(exc)
            else:
                exc_message = ""

            connection_state_label, acquisition_state, reason = _map_acceptance_state(state)
            summary[connection_state_label] += 1
            summary["checked"] += 1

            db = SessionLocal()
            try:
                conn_repo = LinkedInConnectionRepository(db)
                conn_row = db.get(LinkedInConnectionEntity, row.id)
                if conn_row is not None:
                    conn_row.last_checked_at = _utcnow()
                    conn_row.updated_at = _utcnow()
                    snapshot = dict(getattr(conn_row, "profile_snapshot_json", {}) or {})
                    snapshot.update(
                        {
                            "inspectionState": state.value,
                            "inspectionTimestamp": getattr(inspection, "inspection_timestamp", "") if inspection else "",
                            "profileUrl": profile_url,
                            "accountId": account_id,
                            "reason": reason,
                            "error": exc_message,
                        }
                    )
                    conn_row.profile_snapshot_json = snapshot
                    if connection_state_label == "accepted":
                        conn_row.connection_status = "accepted"
                        conn_row.accepted_at = _utcnow()
                    elif connection_state_label == "pending":
                        conn_row.connection_status = "requested"
                    elif connection_state_label == "declined":
                        conn_row.connection_status = "declined"
                    elif connection_state_label == "restricted":
                        conn_row.connection_status = "restricted"
                    else:
                        conn_row.connection_status = "unknown"
                    db.flush()

                profile_repo = CandidateProfileRepository(db)
                profile = profile_repo.get(job_id="", candidate_id=candidate_id)
                if profile is None:
                    continue
                job_id = _normalize_text(getattr(profile, "job_id", ""))
                job = JobRepository(db).get(job_id) if job_id else None
                if job is None:
                    continue
                previous_status = _current_acquisition_status(profile)

                now = _utcnow()
                metadata = {
                    "inspectionState": state.value,
                    "inspectionTimestamp": getattr(inspection, "inspection_timestamp", ""),
                    "profileUrl": profile_url,
                    "accountId": account_id,
                }
                if connection_state_label == "accepted":
                    profile.acquisition_accepted_at = now
                    profile.acquisition_pending_acceptance_at = profile.acquisition_pending_acceptance_at or now
                    profile_repo.update_acquisition_state(
                        job_id=job_id,
                        candidate_id=candidate_id,
                        status=ACQUISITION_ACCEPTED,
                        reason=reason,
                        account_id=account_id,
                        retry_count=int(getattr(profile, "acquisition_retry_count", 0) or 0),
                        priority=int(getattr(profile, "acquisition_priority", 0) or 0),
                    )
                    _record_transition(
                        db,
                        job_id=job_id,
                        company_id=str(getattr(profile, "company_id", "") or getattr(profile, "agency_id", "") or ""),
                        candidate_id=candidate_id,
                        from_status=previous_status,
                        to_status=ACQUISITION_ACCEPTED,
                        source="linkedin_acceptance_queue",
                        actor_id=event_actor_id,
                        metadata={"reason": reason, **metadata},
                    )
                    previous_status = ACQUISITION_ACCEPTED
                    profile.raw_data = dict(profile.raw_data or {})
                    profile.raw_data["engagement"] = {
                        "currentStage": ACQUISITION_ACCEPTED,
                        "connectionStatus": connection_state_label,
                        "invitationStatus": "sent",
                        "currentProgress": _progress_for_status(ACQUISITION_ACCEPTED),
                        "updatedAt": now.isoformat(),
                    }
                    db.flush()
                    message_text = CommunicationService().render_message(
                        recruiter_name="",
                        company=_company_name(job, profile),
                        job_title=_candidate_job_title(job, profile),
                        candidate_name=_candidate_display_name(profile, candidate_id),
                        eve_link=get_booking_link(profile, job),
                        template=message_template,
                    )
                    message_payload = {
                        "job_id": job_id,
                        "candidate_id": candidate_id,
                        "account_id": account_id,
                        "linkedin_url": profile_url,
                        "candidate_name": _candidate_display_name(profile, candidate_id),
                        "recruiter_name": "",
                        "company": _company_name(job, profile),
                        "job_title": _candidate_job_title(job, profile),
                        "eve_link": get_booking_link(profile, job),
                        "template": message_template,
                        "idempotency_key": hashlib.sha256(f"message:{job_id}:{candidate_id}:{profile_url}".encode("utf-8")).hexdigest(),
                    }
                    enqueue_result = enqueue_job(
                        "linkedin_message_queue",
                        message_payload,
                        idempotency_key=message_payload["idempotency_key"],
                        max_attempts=5,
                    )
                    if bool(enqueue_result.get("queued", False)):
                        _persist_message_row(db=db, candidate_id=candidate_id, account_id=account_id, message_text=message_text)
                        profile_repo.update_acquisition_state(
                            job_id=job_id,
                            candidate_id=candidate_id,
                            status=ACQUISITION_MESSAGE_QUEUED,
                            reason="queued_after_acceptance",
                            account_id=account_id,
                            retry_count=int(getattr(profile, "acquisition_retry_count", 0) or 0),
                            priority=int(getattr(profile, "acquisition_priority", 0) or 0),
                        )
                        _record_transition(
                            db,
                            job_id=job_id,
                            company_id=str(getattr(job, "company_id", "") or ""),
                            candidate_id=candidate_id,
                            from_status=previous_status,
                            to_status=ACQUISITION_MESSAGE_QUEUED,
                            source="linkedin_acceptance_queue",
                            actor_id=event_actor_id,
                            metadata={"reason": "queued_message_after_acceptance", **metadata},
                        )
                        previous_status = ACQUISITION_MESSAGE_QUEUED
                        summary["queued_messages"] += 1
                    else:
                        profile.raw_data = dict(profile.raw_data or {})
                        engagement = _ensure_engagement_metadata(profile)
                        engagement.update(
                            {
                                "currentStage": ACQUISITION_ACCEPTED,
                                "connectionStatus": connection_state_label,
                                "invitationStatus": "sent",
                                "messageQueueState": "deferred",
                                "messageQueueReason": str(enqueue_result.get("reason") or enqueue_result.get("mode") or "redis_unavailable"),
                                "currentProgress": _progress_for_status(ACQUISITION_ACCEPTED),
                                "updatedAt": _utcnow().isoformat(),
                            }
                        )
                        profile.raw_data["engagement"] = engagement
                        db.flush()
                        logger.warning(
                            "linkedin_message_queue_deferred job_id=%s candidate_id=%s reason=%s",
                            job_id,
                            candidate_id,
                            str(enqueue_result.get("reason") or enqueue_result.get("mode") or "redis_unavailable"),
                        )
                    db.commit()
                elif connection_state_label == "pending":
                    profile_repo.update_acquisition_state(
                        job_id=job_id,
                        candidate_id=candidate_id,
                        status=ACQUISITION_PENDING_ACCEPTANCE,
                        reason=reason,
                        last_error=exc_message,
                        account_id=account_id,
                        retry_count=int(getattr(profile, "acquisition_retry_count", 0) or 0),
                        priority=int(getattr(profile, "acquisition_priority", 0) or 0),
                    )
                    _record_transition(
                        db,
                        job_id=job_id,
                        company_id=str(getattr(profile, "company_id", "") or getattr(profile, "agency_id", "") or ""),
                        candidate_id=candidate_id,
                        from_status=previous_status,
                        to_status=ACQUISITION_PENDING_ACCEPTANCE,
                        source="linkedin_acceptance_queue",
                        actor_id=event_actor_id,
                        metadata={"reason": reason, **metadata},
                    )
                elif connection_state_label == "declined":
                    profile_repo.update_acquisition_state(
                        job_id=job_id,
                        candidate_id=candidate_id,
                        status="DECLINED",
                        reason=reason,
                        last_error=exc_message,
                        account_id=account_id,
                        retry_count=int(getattr(profile, "acquisition_retry_count", 0) or 0),
                        priority=int(getattr(profile, "acquisition_priority", 0) or 0),
                    )
                    _record_transition(
                        db,
                        job_id=job_id,
                        company_id=str(getattr(profile, "company_id", "") or getattr(profile, "agency_id", "") or ""),
                        candidate_id=candidate_id,
                        from_status=previous_status,
                        to_status="DECLINED",
                        source="linkedin_acceptance_queue",
                        actor_id=event_actor_id,
                        metadata={"reason": reason, **metadata},
                    )
                elif connection_state_label == "restricted":
                    profile_repo.update_acquisition_state(
                        job_id=job_id,
                        candidate_id=candidate_id,
                        status=ACQUISITION_BLOCKED,
                        reason=reason,
                        last_error=exc_message,
                        account_id=account_id,
                        retry_count=int(getattr(profile, "acquisition_retry_count", 0) or 0) + 1,
                        priority=int(getattr(profile, "acquisition_priority", 0) or 0),
                    )
                    try:
                        LinkedInAccountRepository(db).mark_unhealthy(account_id)
                    except Exception:
                        logger.debug("linkedin_acceptance_mark_unhealthy_failed account_id=%s", account_id, exc_info=True)
                    _record_transition(
                        db,
                        job_id=job_id,
                        company_id=str(getattr(profile, "company_id", "") or getattr(profile, "agency_id", "") or ""),
                        candidate_id=candidate_id,
                        from_status=previous_status,
                        to_status=ACQUISITION_BLOCKED,
                        source="linkedin_acceptance_queue",
                        actor_id=event_actor_id,
                        metadata={"reason": reason, **metadata},
                    )
                else:
                    profile_repo.update_acquisition_state(
                        job_id=job_id,
                        candidate_id=candidate_id,
                        status=ACQUISITION_RETRYING,
                        reason=reason,
                        last_error=exc_message or "unknown acceptance state",
                        account_id=account_id,
                        retry_count=int(getattr(profile, "acquisition_retry_count", 0) or 0) + 1,
                        priority=int(getattr(profile, "acquisition_priority", 0) or 0),
                    )
                    _record_transition(
                        db,
                        job_id=job_id,
                        company_id=str(getattr(profile, "company_id", "") or getattr(profile, "agency_id", "") or ""),
                        candidate_id=candidate_id,
                        from_status=previous_status,
                        to_status=ACQUISITION_RETRYING,
                        source="linkedin_acceptance_queue",
                        actor_id=event_actor_id,
                        metadata={"reason": reason, **metadata},
                    )
                db.commit()
            except Exception as exc:
                db.rollback()
                logger.warning(
                    "linkedin_acceptance_candidate_update_failed account_id=%s candidate_id=%s error=%s",
                    account_id,
                    candidate_id,
                    str(exc),
                    exc_info=exc,
                )
                summary["unknown"] += 1
            finally:
                db.close()
    finally:
        try:
            await browser_manager.stop()
        except Exception:
            logger.debug("linkedin_acceptance_browser_stop_failed account_id=%s", account_id, exc_info=True)
    return summary


def process_linkedin_acceptance_check_queue_job(payload: dict[str, Any]) -> dict[str, Any]:
    timeout_ms = int(payload.get("timeout_ms") or 30000)
    message_template = _normalize_text(payload.get("template") or "")
    actor_id = _normalize_text(payload.get("actor_id") or "") or None
    from app.db.session import SessionLocal
    from app.linkedin.repository import LinkedInConnectionRepository

    db = SessionLocal()
    try:
        pending_rows = list(LinkedInConnectionRepository(db).list_pending())
    finally:
        db.close()

    if not pending_rows:
        return {"status": "skipped", "reason": "no_pending_connections", "checked": 0, "accepted": 0, "pending": 0, "declined": 0, "restricted": 0, "unknown": 0, "queued_messages": 0}

    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in pending_rows:
        grouped[str(row.account_id)].append(row)

    summary = {"checked": 0, "accepted": 0, "pending": 0, "declined": 0, "restricted": 0, "unknown": 0, "queued_messages": 0}
    skipped_accounts = 0
    for account_id, rows in grouped.items():
        if _linkedin_configuration_missing(account_id):
            skipped_accounts += 1
            logger.info("linkedin_acceptance_skipped account_id=%s reason=linkedin_not_configured", account_id)
            continue
        account_summary = asyncio.run(
            _inspect_connections_for_account(
                account_id=account_id,
                rows=rows,
                timeout_ms=timeout_ms,
                message_template=message_template,
                event_actor_id=actor_id,
            )
        )
        for key, value in account_summary.items():
            summary[key] = int(summary.get(key, 0)) + int(value or 0)

    if skipped_accounts and not summary["checked"]:
        return {
            "status": "skipped",
            "reason": "linkedin_not_configured",
            "checked": 0,
            "accepted": 0,
            "pending": 0,
            "declined": 0,
            "restricted": 0,
            "unknown": 0,
            "queued_messages": 0,
            "skipped_accounts": skipped_accounts,
        }

    logger.info(
        "linkedin_acceptance_check_completed checked=%s accepted=%s pending=%s declined=%s restricted=%s unknown=%s queued_messages=%s",
        summary["checked"],
        summary["accepted"],
        summary["pending"],
        summary["declined"],
        summary["restricted"],
        summary["unknown"],
        summary["queued_messages"],
    )
    if skipped_accounts:
        summary["skipped_accounts"] = skipped_accounts
    return summary


def process_linkedin_message_queue_job(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = _normalize_text(payload.get("job_id"))
    candidate_id = _normalize_text(payload.get("candidate_id"))
    account_id = _normalize_text(payload.get("account_id"))
    linkedin_url = _normalize_text(payload.get("linkedin_url"))
    recruiter_name = _normalize_text(payload.get("recruiter_name"))
    company = _normalize_text(payload.get("company"))
    job_title = _normalize_text(payload.get("job_title"))
    candidate_name = _normalize_text(payload.get("candidate_name"))
    eve_link = _normalize_text(payload.get("eve_link"))
    template = _normalize_text(payload.get("template"))
    if not job_id or not candidate_id or not linkedin_url:
        return {"status": "skipped", "reason": "missing_payload"}

    from app.db.session import SessionLocal
    from app.models.entities import UserEntity

    db = SessionLocal()
    try:
        job = JobRepository(db).get(job_id)
        profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
        if job is None or profile is None:
            return {"status": "skipped", "reason": "missing_job_or_candidate"}

        if not account_id:
            conn_row = (
                db.query(LinkedInConnectionEntity)
                .filter(LinkedInConnectionEntity.candidate_id == candidate_id)
                .order_by(LinkedInConnectionEntity.created_at.desc())
                .first()
            )
            account_id = _normalize_text(getattr(conn_row, "account_id", "") or "")
        if not account_id:
            return {"status": "skipped", "reason": "missing_account"}

        recruiter_name = recruiter_name or _recruiter_name(db, job, "")
        company = company or _company_name(job, profile)
        job_title = job_title or _candidate_job_title(job, profile)
        candidate_name = candidate_name or _candidate_display_name(profile, candidate_id)
        eve_link = eve_link or get_booking_link(profile, job)
        message = CommunicationService().render_message(
            recruiter_name=recruiter_name,
            company=company,
            job_title=job_title,
            candidate_name=candidate_name,
            eve_link=eve_link,
            template=template,
        )

        profile_repo = CandidateProfileRepository(db)
        profile_repo.update_acquisition_state(
            job_id=job_id,
            candidate_id=candidate_id,
            status=ACQUISITION_MESSAGE_QUEUED,
            reason="message_queue_started",
            account_id=account_id,
            retry_count=int(getattr(profile, "acquisition_retry_count", 0) or 0),
            priority=int(getattr(profile, "acquisition_priority", 0) or 0),
        )
        _record_transition(
            db,
            job_id=job_id,
            company_id=str(getattr(job, "company_id", "") or ""),
            candidate_id=candidate_id,
            from_status=_current_acquisition_status(profile),
            to_status=ACQUISITION_MESSAGE_QUEUED,
            source="linkedin_message_queue",
            metadata={"reason": "message_enqueued"},
        )
        profile.raw_data = dict(profile.raw_data or {})
        engagement = _ensure_engagement_metadata(profile)
        engagement.update(
            {
                "currentStage": ACQUISITION_MESSAGE_QUEUED,
                "connectionStatus": _current_connection_status(getattr(profile, "acquisition_status", "")),
                "invitationStatus": "queued",
                "currentProgress": _progress_for_status(ACQUISITION_MESSAGE_QUEUED),
                "updatedAt": _utcnow().isoformat(),
            }
        )
        profile.raw_data["engagement"] = engagement
        db.flush()

        _persist_message_row(db=db, candidate_id=candidate_id, account_id=account_id, message_text=message)
        db.commit()

        worker = LinkedInMessagingWorker(account_id=account_id, timeout_ms=int(payload.get("timeout_ms") or 30000))
        result = asyncio.run(worker.run(linkedin_url, message))
        status_value = getattr(getattr(result, "status", None), "value", getattr(result, "status", "FAILED"))
        normalized = _normalize_text(status_value).upper()

        if normalized == LinkedInMessagingWorkerStatus.MESSAGE_SENT.value:
            profile_repo.update_acquisition_state(
                job_id=job_id,
                candidate_id=candidate_id,
                status=ACQUISITION_MESSAGE_SENT,
                reason="message_sent",
                account_id=account_id,
                retry_count=int(getattr(profile, "acquisition_retry_count", 0) or 0),
                priority=int(getattr(profile, "acquisition_priority", 0) or 0),
            )
            _record_transition(
                db,
                job_id=job_id,
                company_id=str(getattr(job, "company_id", "") or ""),
                candidate_id=candidate_id,
                from_status=ACQUISITION_MESSAGE_QUEUED,
                to_status=ACQUISITION_MESSAGE_SENT,
                source="linkedin_message_queue",
                metadata={"workerStatus": normalized},
            )
            profile_repo.update_acquisition_state(
                job_id=job_id,
                candidate_id=candidate_id,
                status=ACQUISITION_WAITING_FOR_EVE,
                reason="waiting_for_candidate_response",
                account_id=account_id,
                retry_count=int(getattr(profile, "acquisition_retry_count", 0) or 0),
                priority=int(getattr(profile, "acquisition_priority", 0) or 0),
            )
            _record_transition(
                db,
                job_id=job_id,
                company_id=str(getattr(job, "company_id", "") or ""),
                candidate_id=candidate_id,
                from_status=ACQUISITION_MESSAGE_SENT,
                to_status=ACQUISITION_WAITING_FOR_EVE,
                source="linkedin_message_queue",
                metadata={"workerStatus": normalized},
            )
            profile_repo.update_acquisition_state(
                job_id=job_id,
                candidate_id=candidate_id,
                status=ACQUISITION_HANDOFF,
                reason="candidate_handed_off_to_eve",
                account_id=account_id,
                retry_count=int(getattr(profile, "acquisition_retry_count", 0) or 0),
                priority=int(getattr(profile, "acquisition_priority", 0) or 0),
            )
            _record_transition(
                db,
                job_id=job_id,
                company_id=str(getattr(job, "company_id", "") or ""),
                candidate_id=candidate_id,
                from_status=ACQUISITION_WAITING_FOR_EVE,
                to_status=ACQUISITION_HANDOFF,
                source="linkedin_message_queue",
                metadata={"workerStatus": normalized, "eveLink": eve_link},
            )
            _mark_message_sent(db=db, candidate_id=candidate_id, message_text=message)
            db.commit()
            return {
                "status": "sent",
                "candidate_id": candidate_id,
                "job_id": job_id,
                "message_text": message,
                "eve_link": eve_link,
                "worker_status": normalized,
                "result": {
                    "profileUrl": getattr(result, "profile_url", ""),
                    "durationMs": getattr(result, "duration_ms", 0),
                    "verificationMethod": getattr(result, "verification_method", ""),
                    "composeSelector": getattr(result, "compose_selector", ""),
                    "sendSelector": getattr(result, "send_selector", ""),
                },
            }

        if normalized in {
            LinkedInMessagingWorkerStatus.LOGIN_REQUIRED.value,
            LinkedInMessagingWorkerStatus.SESSION_EXPIRED.value,
            LinkedInMessagingWorkerStatus.PREMIUM_REQUIRED.value,
        }:
            next_status = ACQUISITION_BLOCKED
        elif normalized in {
            LinkedInMessagingWorkerStatus.MESSAGE_BUTTON_NOT_FOUND.value,
            LinkedInMessagingWorkerStatus.DIALOG_NOT_DETECTED.value,
            LinkedInMessagingWorkerStatus.NOT_MESSAGEABLE.value,
            LinkedInMessagingWorkerStatus.UNKNOWN_DIALOG.value,
            LinkedInMessagingWorkerStatus.SEND_FAILED.value,
        }:
            next_status = ACQUISITION_RETRYING
        else:
            next_status = ACQUISITION_FAILED

        profile_repo.update_acquisition_state(
            job_id=job_id,
            candidate_id=candidate_id,
            status=next_status,
            reason="message_delivery_failed",
            last_error=_normalize_text(getattr(result, "error_message", "") or normalized),
            account_id=account_id,
            retry_count=int(getattr(profile, "acquisition_retry_count", 0) or 0) + 1,
            priority=int(getattr(profile, "acquisition_priority", 0) or 0),
        )
        _record_transition(
            db,
            job_id=job_id,
            company_id=str(getattr(job, "company_id", "") or ""),
            candidate_id=candidate_id,
            from_status=ACQUISITION_MESSAGE_QUEUED,
            to_status=next_status,
            source="linkedin_message_queue",
            metadata={"workerStatus": normalized, "error": getattr(result, "error_message", "")},
        )
        db.commit()
        return {
            "status": next_status.lower(),
            "candidate_id": candidate_id,
            "job_id": job_id,
            "worker_status": normalized,
            "error_message": getattr(result, "error_message", ""),
        }
    except Exception as exc:
        db.rollback()
        logger.exception("linkedin_message_queue_failed job_id=%s candidate_id=%s", job_id, candidate_id)
        raise QueueError(str(exc), status_code=500, code="linkedin_message_queue_failed", retryable=True) from exc
    finally:
        db.close()
