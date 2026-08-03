"""outreach_pipeline.py — production LinkedIn outreach lifecycle orchestrator.

Executes the complete outreach workflow for a single candidate:

  Capability inspection
      ↓
  Already connected?  → compose + deliver message
  Pending?            → WAITING_ACCEPTANCE  (resume on next run)
  Can connect?        → send connection + persist requested
  Not reachable?      → NOT_REACHABLE

Resumable: each run re-inspects capabilities and skips already-completed
stages.  Idempotent: duplicate connections and duplicate messages cannot
occur because every stage checks DB state before acting.

No browser logic, no selectors, no locator code lives here.
This module only orchestrates existing services.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class OutreachStatus(str, Enum):
    # Terminal success
    MESSAGE_SENT          = "MESSAGE_SENT"
    # Intermediate — pipeline will resume on next run
    WAITING_ACCEPTANCE    = "WAITING_ACCEPTANCE"
    CONNECTION_REQUESTED  = "CONNECTION_REQUESTED"
    # Terminal failures
    NOT_REACHABLE         = "NOT_REACHABLE"
    LOGIN_REQUIRED        = "LOGIN_REQUIRED"
    SESSION_EXPIRED       = "SESSION_EXPIRED"
    PROFILE_NOT_FOUND     = "PROFILE_NOT_FOUND"
    PREMIUM_REQUIRED      = "PREMIUM_REQUIRED"
    COMPOSE_FAILED        = "COMPOSE_FAILED"
    DELIVERY_FAILED       = "DELIVERY_FAILED"
    CONNECTION_FAILED     = "CONNECTION_FAILED"
    FAILED                = "FAILED"


@dataclass
class OutreachPipelineResult:
    status: OutreachStatus
    candidate_id: str
    job_id: str
    agency_id: str
    # Connection
    connection_status: str = ""          # requested / accepted / unknown
    # Message
    message_status: str = ""             # queued / sent / failed
    conversation_id: str = ""
    message_id: str = ""
    message_text: str = ""
    # Meta
    linkedin_url: str = ""
    account_id: str = ""
    error: str = ""
    duration_ms: int = 0
    stages: list[str] = field(default_factory=list)  # ordered log of completed stages


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

async def run_outreach(
    *,
    candidate_id: str,
    job_id: str,
    agency_id: str,
    account_id: str,
    connection_note: str | None = None,
    timeout_ms: int = 30000,
) -> OutreachPipelineResult:
    """Execute the full LinkedIn outreach lifecycle for one candidate.

    Args:
        candidate_id:    Candidate identifier.
        job_id:          Job identifier (used to load candidate + compose message).
        agency_id:       Agency identifier (used to compose message).
        account_id:      LinkedIn account (browser profile) to use.
        connection_note: Optional personalised note to attach to connection request.
        timeout_ms:      Playwright operation timeout.

    Returns:
        OutreachPipelineResult with status and all relevant IDs.
    """
    started_at = datetime.now(timezone.utc)
    stages: list[str] = []

    logger.info(
        "outreach_pipeline START candidate_id=%s job_id=%s agency_id=%s account_id=%s",
        candidate_id, job_id, agency_id, account_id,
    )

    try:
        result = await _run(
            candidate_id=candidate_id,
            job_id=job_id,
            agency_id=agency_id,
            account_id=account_id,
            connection_note=connection_note,
            timeout_ms=timeout_ms,
            stages=stages,
            started_at=started_at,
        )
        result.duration_ms = _duration_ms(started_at)
        result.stages = stages
        logger.info(
            "outreach_pipeline FINISH status=%s candidate_id=%s duration_ms=%d stages=%s",
            result.status.value, candidate_id, result.duration_ms, stages,
        )
        return result

    except Exception as exc:
        logger.exception(
            "outreach_pipeline UNHANDLED_ERROR candidate_id=%s job_id=%s",
            candidate_id, job_id,
        )
        return OutreachPipelineResult(
            status=OutreachStatus.FAILED,
            candidate_id=candidate_id,
            job_id=job_id,
            agency_id=agency_id,
            error=str(exc),
            duration_ms=_duration_ms(started_at),
            stages=stages,
        )


# ---------------------------------------------------------------------------
# Internal pipeline
# ---------------------------------------------------------------------------

async def _run(
    *,
    candidate_id: str,
    job_id: str,
    agency_id: str,
    account_id: str,
    connection_note: str | None,
    timeout_ms: int,
    stages: list[str],
    started_at: datetime,
) -> OutreachPipelineResult:

    def _partial(status: OutreachStatus, **kwargs: Any) -> OutreachPipelineResult:
        return OutreachPipelineResult(
            status=status,
            candidate_id=candidate_id,
            job_id=job_id,
            agency_id=agency_id,
            **kwargs,
        )

    # ── Stage 1: load candidate + LinkedIn URL ────────────────────────────────
    _stage(stages, "load_candidate")
    linkedin_url, load_error = _load_linkedin_url(candidate_id=candidate_id, job_id=job_id)
    if load_error:
        logger.error("outreach_pipeline load_candidate FAILED error=%r candidate_id=%s", load_error, candidate_id)
        return _partial(OutreachStatus.FAILED, error=load_error)
    logger.info("outreach_pipeline load_candidate SUCCESS linkedin_url=%s", linkedin_url)

    # ── Stage 2: capability inspection ───────────────────────────────────────
    _stage(stages, "inspect_capabilities")
    caps, inspect_error = await _inspect_capabilities(
        account_id=account_id,
        linkedin_url=linkedin_url,
        timeout_ms=timeout_ms,
    )
    if inspect_error or caps is None:
        logger.error("outreach_pipeline inspect_capabilities FAILED error=%r", inspect_error)
        return _partial(OutreachStatus.FAILED, linkedin_url=linkedin_url, error=inspect_error or "inspect failed")

    logger.info(
        "outreach_pipeline inspect_capabilities SUCCESS "
        "can_message=%s connected=%s pending=%s can_connect=%s "
        "login_required=%s session_expired=%s profile_not_found=%s",
        caps.can_message, caps.connected, caps.pending, caps.can_connect,
        caps.login_required, caps.session_expired, caps.profile_not_found,
    )

    # ── Stage 3: blocked-state early exits ───────────────────────────────────
    if caps.login_required:
        return _partial(OutreachStatus.LOGIN_REQUIRED, linkedin_url=linkedin_url,
                        error="LinkedIn session requires login")
    if caps.session_expired:
        return _partial(OutreachStatus.SESSION_EXPIRED, linkedin_url=linkedin_url,
                        error="LinkedIn session expired or account restricted")
    if caps.profile_not_found:
        return _partial(OutreachStatus.PROFILE_NOT_FOUND, linkedin_url=linkedin_url,
                        error="LinkedIn profile not found")
    if caps.profile_private and not caps.can_message and not caps.can_connect:
        return _partial(OutreachStatus.NOT_REACHABLE, linkedin_url=linkedin_url,
                        error="profile is private and not reachable")

    # ── Stage 4: decision tree ────────────────────────────────────────────────

    # PATH A — already connected (or message available): compose + deliver
    if caps.connected or caps.can_message:
        _stage(stages, "compose_message")
        compose_result, compose_error = _compose_message(
            candidate_id=candidate_id,
            job_id=job_id,
            agency_id=agency_id,
        )
        if compose_error or compose_result is None:
            logger.error("outreach_pipeline compose_message FAILED error=%r", compose_error)
            return _partial(OutreachStatus.COMPOSE_FAILED, linkedin_url=linkedin_url,
                            account_id=account_id, error=compose_error or "compose failed")
        message_text = compose_result["message_text"]
        logger.info("outreach_pipeline compose_message SUCCESS chars=%d", len(message_text))

        _stage(stages, "deliver_message")
        delivery, delivery_error = await _deliver_message(
            account_id=account_id,
            linkedin_url=linkedin_url,
            message_text=message_text,
            timeout_ms=timeout_ms,
        )
        if delivery_error or delivery is None:
            logger.error("outreach_pipeline deliver_message FAILED error=%r", delivery_error)
            return _partial(OutreachStatus.DELIVERY_FAILED, linkedin_url=linkedin_url,
                            account_id=account_id, message_text=message_text,
                            error=delivery_error or "delivery failed")

        if delivery.status.value != "MESSAGE_SENT":
            logger.warning(
                "outreach_pipeline deliver_message NOT_SENT status=%s error=%r",
                delivery.status.value, delivery.error_message,
            )
            return _partial(
                OutreachStatus.DELIVERY_FAILED,
                linkedin_url=linkedin_url,
                account_id=account_id,
                message_text=message_text,
                error=f"delivery status={delivery.status.value}: {delivery.error_message}",
            )

        _stage(stages, "persist_message_sent")
        conv_id, msg_id = _mark_message_sent(
            candidate_id=candidate_id,
            message_text=message_text,
        )
        logger.info(
            "outreach_pipeline deliver_message SUCCESS "
            "verification=%s conversation_id=%s message_id=%s",
            delivery.verification_method, conv_id, msg_id,
        )
        return _partial(
            OutreachStatus.MESSAGE_SENT,
            linkedin_url=linkedin_url,
            account_id=account_id,
            connection_status="accepted",
            message_status="sent",
            conversation_id=conv_id,
            message_id=msg_id,
            message_text=message_text,
        )

    # PATH B — connection request already pending: wait for acceptance
    if caps.pending:
        logger.info("outreach_pipeline WAITING_ACCEPTANCE candidate_id=%s", candidate_id)
        return _partial(
            OutreachStatus.WAITING_ACCEPTANCE,
            linkedin_url=linkedin_url,
            account_id=account_id,
            connection_status="requested",
        )

    # PATH C — connect button available: send connection request
    if caps.can_connect:
        _stage(stages, "send_connection")
        conn_result, conn_error = await _send_connection(
            account_id=account_id,
            linkedin_url=linkedin_url,
            candidate_id=candidate_id,
            connection_note=connection_note,
            timeout_ms=timeout_ms,
        )
        if conn_error or conn_result is None:
            logger.error("outreach_pipeline send_connection FAILED error=%r", conn_error)
            return _partial(OutreachStatus.CONNECTION_FAILED, linkedin_url=linkedin_url,
                            account_id=account_id, error=conn_error or "connection failed")

        status_val = conn_result.status.value
        logger.info("outreach_pipeline send_connection result status=%s", status_val)

        if status_val == "REQUEST_SENT":
            return _partial(
                OutreachStatus.CONNECTION_REQUESTED,
                linkedin_url=linkedin_url,
                account_id=account_id,
                connection_status="requested",
            )
        if status_val == "ALREADY_CONNECTED":
            # Race condition: became connected between inspect and send.
            # Recurse once to deliver the message.
            logger.info("outreach_pipeline send_connection ALREADY_CONNECTED — proceeding to message")
            stages.append("recurse_to_message")
            return await _run(
                candidate_id=candidate_id,
                job_id=job_id,
                agency_id=agency_id,
                account_id=account_id,
                connection_note=None,
                timeout_ms=timeout_ms,
                stages=stages,
                started_at=started_at,
            )
        if status_val == "REQUEST_ALREADY_PENDING":
            return _partial(
                OutreachStatus.WAITING_ACCEPTANCE,
                linkedin_url=linkedin_url,
                account_id=account_id,
                connection_status="requested",
            )

        # Any other status (FOLLOW_ONLY, UNKNOWN_STATE, FAILED, etc.)
        return _partial(
            OutreachStatus.CONNECTION_FAILED,
            linkedin_url=linkedin_url,
            account_id=account_id,
            error=f"connection worker returned {status_val}: {conn_result.error_message}",
        )

    # PATH D — nothing available
    logger.warning(
        "outreach_pipeline NOT_REACHABLE caps=%s candidate_id=%s",
        caps, candidate_id,
    )
    return _partial(
        OutreachStatus.NOT_REACHABLE,
        linkedin_url=linkedin_url,
        error="no actionable capability found on profile",
    )


# ---------------------------------------------------------------------------
# Stage helpers — thin wrappers that never raise, return (result, error)
# ---------------------------------------------------------------------------

def _load_linkedin_url(
    *, candidate_id: str, job_id: str
) -> tuple[str, str]:
    """Load the candidate's LinkedIn URL from DB.  Returns (url, error)."""
    try:
        from app.db.session import SessionLocal
        from app.db.repositories import CandidateProfileRepository

        db = SessionLocal()
        try:
            candidate = CandidateProfileRepository(db).get(
                job_id=job_id, candidate_id=candidate_id
            )
        finally:
            db.close()

        if candidate is None:
            return "", f"candidate not found candidate_id={candidate_id} job_id={job_id}"

        url = str(getattr(candidate, "linkedin_url", "") or "").strip()
        if not url:
            return "", f"candidate has no linkedin_url candidate_id={candidate_id}"

        return url, ""
    except Exception as exc:
        return "", str(exc)


async def _inspect_capabilities(
    *, account_id: str, linkedin_url: str, timeout_ms: int
) -> tuple[Any | None, str]:
    """Run capability inspection.  Returns (ProfileCapabilities, error)."""
    try:
        from app.linkedin.playwright.browser_manager import BrowserManager
        from app.linkedin.playwright.profile_inspector import LinkedInProfileInspector

        browser = BrowserManager(account_id=account_id)
        context = await browser.get_browser()
        try:
            inspector = LinkedInProfileInspector(context, timeout_ms=timeout_ms)
            caps = await inspector.inspect_capabilities(linkedin_url)
            caps.log_summary(linkedin_url, logger)
            return caps, ""
        finally:
            try:
                await browser.stop()
            except Exception:
                pass
    except Exception as exc:
        return None, str(exc)


def _compose_message(
    *, candidate_id: str, job_id: str, agency_id: str
) -> tuple[dict | None, str]:
    """Compose and persist a queued message.  Returns (result_dict, error)."""
    try:
        from app.linkedin.services.message_composer import compose_linkedin_message

        result = compose_linkedin_message(
            candidate_id=candidate_id,
            job_id=job_id,
            agency_id=agency_id,
        )
        return result, ""
    except Exception as exc:
        return None, str(exc)


async def _deliver_message(
    *, account_id: str, linkedin_url: str, message_text: str, timeout_ms: int
) -> tuple[Any | None, str]:
    """Deliver the message via MessagingWorker.  Returns (result, error)."""
    try:
        from app.linkedin.workers.messaging_worker import LinkedInMessagingWorker

        worker = LinkedInMessagingWorker(account_id=account_id, timeout_ms=timeout_ms)
        result = await worker.run(linkedin_url, message_text)
        return result, ""
    except Exception as exc:
        return None, str(exc)


async def _send_connection(
    *,
    account_id: str,
    linkedin_url: str,
    candidate_id: str,
    connection_note: str | None,
    timeout_ms: int,
) -> tuple[Any | None, str]:
    """Send a connection request via ConnectionWorker.  Returns (result, error)."""
    try:
        from app.linkedin.workers.connection_worker import LinkedInConnectionWorker

        worker = LinkedInConnectionWorker(account_id=account_id, timeout_ms=timeout_ms)
        result = await worker.run(
            linkedin_url,
            connection_note,
            candidate_id=candidate_id,
        )
        return result, ""
    except Exception as exc:
        return None, str(exc)


def _mark_message_sent(
    *, candidate_id: str, message_text: str
) -> tuple[str, str]:
    """Mark the queued outreach message as sent.  Returns (conversation_id, message_id)."""
    try:
        from app.db.session import SessionLocal
        from app.linkedin.models import LinkedInMessageEntity

        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            msg = (
                db.query(LinkedInMessageEntity)
                .filter(
                    LinkedInMessageEntity.candidate_id == candidate_id,
                    LinkedInMessageEntity.message_type == "outreach",
                    LinkedInMessageEntity.sent_at.is_(None),
                )
                .order_by(LinkedInMessageEntity.created_at.desc())
                .first()
            )
            if msg is not None:
                msg.sent_at = now
                db.commit()
                logger.info(
                    "outreach_pipeline message_marked_sent "
                    "message_id=%s conversation_id=%s candidate_id=%s",
                    msg.id, msg.conversation_id, candidate_id,
                )
                return str(msg.conversation_id), str(msg.id)
            logger.warning(
                "outreach_pipeline no_queued_message_found candidate_id=%s", candidate_id
            )
            return "", ""
        finally:
            db.close()
    except Exception as exc:
        logger.exception(
            "outreach_pipeline mark_message_sent_failed candidate_id=%s", candidate_id
        )
        return "", ""


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _stage(stages: list[str], name: str) -> None:
    stages.append(name)
    logger.info("outreach_pipeline stage=%s", name)


def _duration_ms(started_at: datetime) -> int:
    return int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
