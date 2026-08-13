from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import requests
from requests import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import EVE_BASE_URL, EVE_INTERNAL_TOKEN, HTTP_TIMEOUT_SECONDS
from app.db.session import SessionLocal
from app.models.entities import AdamEveOutboundEventEntity

logger = logging.getLogger(__name__)

_RETRY_DELAYS_SECONDS = (10, 30, 120, 600, 1800)
_PROCESSING_LOCK_SECONDS = 60
_WORKER_SLEEP_SECONDS = 10
_WORKER_BATCH_SIZE = 10

_worker_thread: threading.Thread | None = None
_worker_stop = threading.Event()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _next_retry_for_attempt(attempt_count: int) -> datetime:
    index = max(0, min(len(_RETRY_DELAYS_SECONDS) - 1, int(attempt_count) - 1))
    return _utcnow() + timedelta(seconds=_RETRY_DELAYS_SECONDS[index])


def _claim_visibility_deadline() -> datetime:
    return _utcnow() + timedelta(seconds=_PROCESSING_LOCK_SECONDS)


def _build_recruiter_interest_payload(
    *,
    adam_event_id: str,
    candidate_id: str,
    candidate_email: str | None,
    job_id: str,
    agency_id: str,
    recruiter_user_id: str | None,
    recruiter_message: str | None,
) -> dict[str, Any]:
    return {
        "adam_event_id": adam_event_id,
        "candidate_id": candidate_id,
        "candidate_email": candidate_email,
        "job_id": job_id,
        "agency_id": agency_id,
        "recruiter_user_id": recruiter_user_id,
        "recruiter_message": recruiter_message,
    }


def upsert_outbound_event(
    db: Session,
    *,
    adam_event_id: str,
    candidate_id: str,
    candidate_email: str | None = None,
    job_id: str,
    agency_id: str,
    recruiter_user_id: str | None = None,
    recruiter_message: str | None = None,
    notification_type: str = "recruiter_interest",
    payload: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> AdamEveOutboundEventEntity:
    normalized_adam_event_id = _normalize_text(adam_event_id)
    row = db.scalar(
        select(AdamEveOutboundEventEntity).where(AdamEveOutboundEventEntity.adam_event_id == normalized_adam_event_id)
    )
    if row:
        return row

    now = _utcnow()
    normalized_payload = dict(payload or {})
    if _normalize_text(notification_type) == "recruiter_interest":
        normalized_payload.setdefault("adam_event_id", normalized_adam_event_id)
        normalized_payload.setdefault("candidate_id", _normalize_text(candidate_id))
        normalized_payload.setdefault("candidate_email", _normalize_text(candidate_email) or None)
        normalized_payload.setdefault("job_id", _normalize_text(job_id))
        normalized_payload.setdefault("agency_id", _normalize_text(agency_id))
        normalized_payload.setdefault("recruiter_user_id", _normalize_text(recruiter_user_id) or None)
        normalized_payload.setdefault("recruiter_message", recruiter_message)
    row = AdamEveOutboundEventEntity(
        id=str(uuid4()),
        adam_event_id=normalized_adam_event_id,
        event_id=_normalize_text(event_id) or None,
        candidate_id=_normalize_text(candidate_id),
        job_id=_normalize_text(job_id),
        agency_id=_normalize_text(agency_id),
        recruiter_user_id=_normalize_text(recruiter_user_id) or None,
        recruiter_message=(recruiter_message if recruiter_message is None else str(recruiter_message).strip() or None),
        notification_type=_normalize_text(notification_type) or "recruiter_interest",
        payload=normalized_payload,
        status="pending",
        attempt_count=0,
        last_error=None,
        next_retry_at=now,
        delivered_at=None,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def _validate_http_response(response: Response) -> tuple[bool, bool, str]:
    status = int(response.status_code)
    if status in {200, 201}:
        return True, False, ""
    if status in {401, 403}:
        return False, False, f"eve_auth_error status_code={status} body={response.text[:1000]}"
    if status in {404, 422}:
        return False, False, f"eve_contract_error status_code={status} body={response.text[:1000]}"
    if status >= 500:
        return False, True, f"eve_server_error status_code={status} body={response.text[:1000]}"
    return False, False, f"eve_unexpected_status status_code={status} body={response.text[:1000]}"


def _deliver_event(row: AdamEveOutboundEventEntity) -> tuple[bool, bool, str]:
    if not EVE_BASE_URL or not EVE_INTERNAL_TOKEN:
        return False, True, "eve_delivery_not_configured"

    notification_type = _normalize_text(row.notification_type) or "recruiter_interest"
    headers = {
        "Authorization": f"Bearer {EVE_INTERNAL_TOKEN}",
        "Content-Type": "application/json",
    }

    if notification_type == "recruiter_interest":
        candidate_email_value = ""
        if isinstance(row.payload, dict):
            candidate_email_value = _normalize_text(row.payload.get("candidate_email"))
        if not candidate_email_value:
            return False, False, "eve_contract_error missing_candidate_email"
        payload = _build_recruiter_interest_payload(
            adam_event_id=str(row.adam_event_id),
            candidate_id=str(row.candidate_id),
            candidate_email=candidate_email_value,
            job_id=str(row.job_id),
            agency_id=str(row.agency_id),
            recruiter_user_id=str(row.recruiter_user_id).strip() if row.recruiter_user_id else None,
            recruiter_message=row.recruiter_message,
        )
        url = f"{EVE_BASE_URL.rstrip('/')}/api/internal/recruiter-interest"
    else:
        # interview_slot_booking and second_round_invite both use the candidate-notification endpoint
        stored_payload = dict(row.payload) if isinstance(row.payload, dict) else {}
        payload = stored_payload
        url = f"{EVE_BASE_URL.rstrip('/')}/api/internal/candidate-notification"

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        return False, True, f"eve_network_error: {exc}"

    # 409 from candidate-notification means duplicate — treat as success
    if notification_type != "recruiter_interest" and response.status_code == 409:
        return True, False, ""

    return _validate_http_response(response)


def _mark_delivered(db: Session, row: AdamEveOutboundEventEntity) -> None:
    now = _utcnow()
    row.status = "delivered"
    row.delivered_at = now
    row.updated_at = now
    row.last_error = None
    row.next_retry_at = now
    db.flush()


def _mark_failed(db: Session, row: AdamEveOutboundEventEntity, *, error: str) -> None:
    now = _utcnow()
    row.status = "failed"
    row.last_error = error[:4000]
    row.updated_at = now
    row.next_retry_at = now
    db.flush()


def _schedule_retry(db: Session, row: AdamEveOutboundEventEntity, *, error: str) -> None:
    now = _utcnow()
    row.status = "pending"
    row.last_error = error[:4000]
    row.updated_at = now
    row.next_retry_at = _next_retry_for_attempt(int(row.attempt_count or 0))
    db.flush()


def _claim_due_events(db: Session, *, limit: int) -> list[AdamEveOutboundEventEntity]:
    now = _utcnow()
    stmt = (
        select(AdamEveOutboundEventEntity)
        .where(
            AdamEveOutboundEventEntity.status == "pending",
            AdamEveOutboundEventEntity.delivered_at.is_(None),
            AdamEveOutboundEventEntity.next_retry_at <= now,
            AdamEveOutboundEventEntity.attempt_count < len(_RETRY_DELAYS_SECONDS),
        )
        .order_by(AdamEveOutboundEventEntity.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(max(1, int(limit)))
    )
    rows = list(db.scalars(stmt).all())
    if not rows:
        return []
    claim_deadline = _claim_visibility_deadline()
    for row in rows:
        row.attempt_count = int(row.attempt_count or 0) + 1
        row.next_retry_at = claim_deadline
        row.updated_at = now
    db.flush()
    return rows


def deliver_pending_events(db: Session | None = None, *, limit: int = _WORKER_BATCH_SIZE) -> dict[str, int]:
    if not EVE_BASE_URL or not EVE_INTERNAL_TOKEN:
        logger.info("eve_delivery_skipped reason=missing_configuration")
        return {"claimed": 0, "delivered": 0, "retryable_failed": 0, "failed": 0}

    owns_session = db is None or bool(getattr(db, "in_transaction", lambda: False)())
    session = SessionLocal() if owns_session else db
    try:
        claimed_rows = _claim_due_events(session, limit=limit)
        session.commit()

        delivered = retryable_failed = failed = 0
        for row in claimed_rows:
            try:
                success, retryable, error = _deliver_event(row)
            except Exception as exc:
                success, retryable, error = False, True, f"eve_delivery_exception: {exc}"
            with session.begin():
                managed_row = session.get(AdamEveOutboundEventEntity, row.id)
                if not managed_row:
                    continue
                if success:
                    _mark_delivered(session, managed_row)
                    delivered += 1
                    logger.info("eve_delivery_succeeded adam_event_id=%s", managed_row.adam_event_id)
                    continue
                if retryable and int(managed_row.attempt_count or 0) < len(_RETRY_DELAYS_SECONDS):
                    _schedule_retry(session, managed_row, error=error)
                    retryable_failed += 1
                    logger.warning(
                        "eve_delivery_retry_scheduled adam_event_id=%s attempt=%s next_retry_at=%s error=%s",
                        managed_row.adam_event_id,
                        managed_row.attempt_count,
                        managed_row.next_retry_at.isoformat(),
                        error,
                    )
                    continue
                _mark_failed(session, managed_row, error=error)
                failed += 1
                logger.error("eve_delivery_failed adam_event_id=%s error=%s", managed_row.adam_event_id, error)

        return {
            "claimed": len(claimed_rows),
            "delivered": delivered,
            "retryable_failed": retryable_failed,
            "failed": failed,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()
        if db is not None and hasattr(db, "expire_all"):
            db.expire_all()


def _worker_loop() -> None:
    logger.info("eve_delivery_worker_started")
    while not _worker_stop.is_set():
        try:
            with SessionLocal() as db:
                deliver_pending_events(db)
        except Exception as exc:
            logger.warning("eve_delivery_worker_cycle_failed error=%s", str(exc), exc_info=exc)
        _worker_stop.wait(_WORKER_SLEEP_SECONDS)
    logger.info("eve_delivery_worker_stopped")


def start_eve_notification_worker() -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_stop.clear()
    _worker_thread = threading.Thread(target=_worker_loop, name="pontis-eve-delivery", daemon=True)
    _worker_thread.start()


def stop_eve_notification_worker() -> None:
    _worker_stop.set()
    if _worker_thread and _worker_thread.is_alive():
        _worker_thread.join(timeout=5.0)


def eve_notification_worker_status() -> dict[str, Any]:
    return {
        "running": bool(_worker_thread and _worker_thread.is_alive() and not _worker_stop.is_set()),
        "base_url_configured": bool(EVE_BASE_URL),
        "token_configured": bool(EVE_INTERNAL_TOKEN),
    }
