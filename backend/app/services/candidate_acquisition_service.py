from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy import asc, or_
from sqlalchemy.orm import Session

from app.core.config import (
    LINKEDIN_CONNECTION_BACKOFF_BASE_SECONDS,
    LINKEDIN_CONNECTION_DAILY_LIMIT,
    LINKEDIN_CONNECTION_MAX_RETRIES,
    LINKEDIN_CONNECTION_QUEUE_NAME,
    LINKEDIN_CONNECTION_RANDOM_DELAY_MAX_SECONDS,
    LINKEDIN_CONNECTION_RANDOM_DELAY_MIN_SECONDS,
    LINKEDIN_CONNECTION_REQUESTS_PER_HOUR,
)
from app.db.repositories import CandidateLifecycleEventRepository, CandidateProfileRepository, JobRepository
from app.linkedin.models import LinkedInAccountEntity, LinkedInConnectionEntity
from app.linkedin.repository import LinkedInConnectionRepository
from app.linkedin.workers.connection_worker import LinkedInConnectionWorker
from app.services.redis_service import rate_limit_check
from app.utils.exceptions import QueueError

logger = logging.getLogger(__name__)

DISCOVERY_STATUS = "DISCOVERED"
QUEUED_STATUS = "QUEUED"
SENDING_STATUS = "SENDING"
SENT_STATUS = "CONNECTION_SENT"
PENDING_ACCEPTANCE_STATUS = "PENDING_ACCEPTANCE"
ACCEPTED_STATUS = "ACCEPTED"
DECLINED_STATUS = "DECLINED"
FAILED_STATUS = "FAILED"
BLOCKED_STATUS = "BLOCKED"
RETRYING_STATUS = "RETRYING"
TERMINAL_STATUSES = {ACCEPTED_STATUS, DECLINED_STATUS, FAILED_STATUS, BLOCKED_STATUS}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalized_text(value: object) -> str:
    return str(value or "").strip()


def normalize_linkedin_url(value: object) -> str:
    raw = _normalized_text(value)
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw.lstrip('/')}"
    parsed = urlsplit(raw)
    path = parsed.path.rstrip("/")
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() in {"trk", "locale", "mini"}
    ]
    normalized_query = urlencode(query_items, doseq=True)
    normalized = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, normalized_query, ""))
    return normalized.rstrip("/")


def build_connection_idempotency_key(*, job_id: str, candidate_id: str, linkedin_url: str) -> str:
    material = "|".join(
        [
            _normalized_text(job_id).lower(),
            _normalized_text(candidate_id).lower(),
            normalize_linkedin_url(linkedin_url).lower(),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _candidate_value(candidate: Any, *keys: str) -> str:
    if isinstance(candidate, dict):
        for key in keys:
            value = candidate.get(key)
            if value is not None and _normalized_text(value):
                return _normalized_text(value)
        return ""
    for key in keys:
        value = getattr(candidate, key, None)
        if value is not None and _normalized_text(value):
            return _normalized_text(value)
    return ""


def _candidate_raw(candidate: Any) -> dict[str, Any]:
    if isinstance(candidate, dict):
        return dict(candidate)
    if hasattr(candidate, "model_dump"):
        try:
            return dict(candidate.model_dump())
        except Exception:
            return {}
    if hasattr(candidate, "__dict__"):
        return dict(candidate.__dict__)
    return {}


def _effective_daily_limit(account: LinkedInAccountEntity) -> int:
    configured = int(getattr(account, "daily_connection_limit", 0) or 0)
    if configured > 0:
        return configured
    fallback = int(LINKEDIN_CONNECTION_DAILY_LIMIT or 0)
    return fallback


def _effective_hourly_limit(account: LinkedInAccountEntity) -> int:
    configured = int(LINKEDIN_CONNECTION_REQUESTS_PER_HOUR or 0)
    return max(0, configured)


def _choose_connection_account(db: Session, *, company_id: str) -> LinkedInAccountEntity | None:
    query = (
        db.query(LinkedInAccountEntity)
        .filter(LinkedInAccountEntity.status == "active")
        .filter(or_(LinkedInAccountEntity.health.is_(None), LinkedInAccountEntity.health != "unhealthy"))
    )
    if company_id:
        query = query.filter(LinkedInAccountEntity.company_id == company_id)
    accounts = list(query.order_by(asc(LinkedInAccountEntity.connections_sent_today), asc(LinkedInAccountEntity.created_at)).all())
    for account in accounts:
        daily_limit = _effective_daily_limit(account)
        if daily_limit > 0 and int(account.connections_sent_today or 0) >= daily_limit:
            continue
        return account
    if accounts:
        return accounts[0]
    if company_id:
        return (
            db.query(LinkedInAccountEntity)
            .filter(LinkedInAccountEntity.status == "active")
            .order_by(asc(LinkedInAccountEntity.connections_sent_today), asc(LinkedInAccountEntity.created_at))
            .first()
        )
    return None


def _queue_delay_seconds() -> int:
    minimum = max(0, int(LINKEDIN_CONNECTION_RANDOM_DELAY_MIN_SECONDS or 0))
    maximum = max(minimum, int(LINKEDIN_CONNECTION_RANDOM_DELAY_MAX_SECONDS or minimum))
    return random.randint(minimum, maximum) if maximum > 0 else 0


def _record_transition(
    db: Session,
    *,
    job_id: str,
    company_id: str,
    candidate_id: str,
    from_status: str,
    to_status: str,
    source: str,
    actor_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        CandidateLifecycleEventRepository(db).create(
            job_id=job_id,
            company_id=company_id,
            candidate_id=candidate_id,
            from_status=from_status,
            to_status=to_status,
            source=source,
            actor_id=actor_id or None,
            transition_key=hashlib.sha256(
                repr({
                    "job_id": job_id,
                    "company_id": company_id,
                    "candidate_id": candidate_id,
                    "from_status": from_status,
                    "to_status": to_status,
                    "source": source,
                    "metadata": dict(metadata or {}),
                }).encode("utf-8")
            ).hexdigest(),
            event_metadata=dict(metadata or {}),
        )
        db.flush()
    except Exception:
        db.rollback()


def persist_discovered_linkedin_candidates(
    db: Session,
    *,
    job_id: str,
    candidates: list[Any],
    recruiter_id: str = "",
    agency_id: str = "",
    source: str = "serpapi",
    source_type: str = "linkedin_xray",
    source_query: str = "",
    request_source: str = "api",
    priority: int = 0,
) -> dict[str, Any]:
    repo = CandidateProfileRepository(db)
    job = JobRepository(db).get(job_id)
    if not job:
        raise QueueError("Job not found", status_code=404, code="job_missing", retryable=False)

    seen_linkedin_urls: set[str] = set()
    seen_candidate_ids: set[str] = set()
    queued = 0
    updated = 0
    skipped = 0
    deduped = 0
    queue_jobs: list[dict[str, Any]] = []
    discovery_timestamp = _utcnow()

    for candidate in candidates:
        raw = _candidate_raw(candidate)
        linkedin_url = normalize_linkedin_url(
            _candidate_value(candidate, "linkedin_url", "linkedinUrl", "source_url", "sourceUrl", "profileUrl", "profile_url")
            or raw.get("linkedin_url")
            or raw.get("linkedinUrl")
            or raw.get("source_url")
            or raw.get("sourceUrl")
        )
        if not linkedin_url:
            skipped += 1
            continue

        candidate_id = _normalized_text(
            _candidate_value(candidate, "id", "candidate_id", "candidateId")
            or raw.get("id")
            or raw.get("candidate_id")
            or raw.get("candidateId")
            or linkedin_url
        )
        if linkedin_url in seen_linkedin_urls or candidate_id in seen_candidate_ids:
            deduped += 1
            continue
        seen_linkedin_urls.add(linkedin_url)
        if candidate_id:
            seen_candidate_ids.add(candidate_id)

        name = _candidate_value(candidate, "name", "full_name", "fullName")
        role = _candidate_value(candidate, "current_role", "currentRole", "headline", "role")
        company = _candidate_value(candidate, "current_company", "currentCompany", "company")
        source_timestamp = _candidate_value(candidate, "source_timestamp", "sourceTimestamp")
        raw_data = dict(raw)
        raw_data.setdefault("source", source)
        raw_data.setdefault("source_type", source_type)
        raw_data.setdefault("source_query", source_query)
        raw_data.setdefault("request_source", request_source)
        raw_data.setdefault("linkedin_url", linkedin_url)
        raw_data.setdefault("discovery_timestamp", discovery_timestamp.isoformat())

        row = repo.upsert_acquisition_candidate(
            job_id=job_id,
            candidate_id=candidate_id,
            name=name,
            current_company=company,
            current_role=role,
            linkedin_url=linkedin_url,
            source=source,
            source_provider=_candidate_value(candidate, "source_provider", "sourceProvider") or source,
            source_type=source_type,
            source_query=source_query,
            source_timestamp=source_timestamp,
            discovery_timestamp=discovery_timestamp,
            raw_data=raw_data,
            agency_id=agency_id or str(getattr(job, "company_id", "") or ""),
            recruiter_id=recruiter_id,
            status=DISCOVERY_STATUS,
            reason="discovered_from_serp",
            retry_count=0,
            priority=priority,
        )
        _record_transition(
            db,
            job_id=job_id,
            company_id=str(getattr(job, "company_id", "") or ""),
            candidate_id=str(row.candidate_id or candidate_id),
            from_status="",
            to_status=DISCOVERY_STATUS,
            source="linkedin_xray",
            metadata={
                "reason": "discovered_from_serp",
                "source_query": source_query,
                "source_type": source_type,
                "request_source": request_source,
            },
        )
        _update_candidate_state(
            job_id=job_id,
            candidate_id=str(row.candidate_id or candidate_id),
            status=QUEUED_STATUS,
            reason="queued_for_linkedin_connection",
            priority=priority,
        )
        queued_payload = {
            "candidate_id": str(row.candidate_id or candidate_id),
            "linkedin_url": linkedin_url,
            "job_id": job_id,
            "retry_count": 0,
            "priority": int(priority),
            "idempotency_key": build_connection_idempotency_key(
                job_id=job_id,
                candidate_id=str(row.candidate_id or candidate_id),
                linkedin_url=linkedin_url,
            ),
        }
        queue_jobs.append(queued_payload)
        updated += 1

    db.commit()
    from app.services.job_queue_service import enqueue_job

    for payload in queue_jobs:
        try:
            delay_seconds = _queue_delay_seconds()
            queue_result = enqueue_job(
                LINKEDIN_CONNECTION_QUEUE_NAME,
                payload,
                idempotency_key=str(payload["idempotency_key"]),
                delay_seconds=delay_seconds,
                max_attempts=LINKEDIN_CONNECTION_MAX_RETRIES,
            )
            if bool(queue_result.get("queued", False)):
                queued += 1
                candidate_row = _update_candidate_state(
                    job_id=job_id,
                    candidate_id=str(payload["candidate_id"]),
                    status=QUEUED_STATUS,
                    reason="queued_for_linkedin_connection",
                    queue_job_id=str(queue_result.get("job_id") or ""),
                    idempotency_key=str(payload["idempotency_key"]),
                    retry_count=0,
                    priority=int(payload.get("priority") or 0),
                )
                if candidate_row is not None:
                    candidate_row.acquisition_discovered_at = candidate_row.acquisition_discovered_at or discovery_timestamp
            else:
                logger.warning(
                    "linkedin_connection_queue_deferred job_id=%s candidate_id=%s reason=%s",
                    job_id,
                    payload.get("candidate_id", ""),
                    str(queue_result.get("reason") or queue_result.get("mode") or "redis_unavailable"),
                )
                _update_candidate_state(
                    job_id=job_id,
                    candidate_id=str(payload["candidate_id"]),
                    status=QUEUED_STATUS,
                    reason="queue_deferred_redis_unavailable",
                    last_error="Redis unavailable; LinkedIn connection deferred",
                    queue_job_id="",
                    idempotency_key=str(payload["idempotency_key"]),
                    retry_count=0,
                    priority=int(payload.get("priority") or 0),
                )
        except Exception as exc:
            logger.warning(
                "linkedin_connection_queue_enqueue_failed job_id=%s candidate_id=%s error=%s",
                job_id,
                payload.get("candidate_id", ""),
                str(exc),
                exc_info=exc,
            )
            _update_candidate_state(
                job_id=job_id,
                candidate_id=str(payload["candidate_id"]),
                status=RETRYING_STATUS,
                reason="queue_enqueue_failed",
                last_error=str(exc),
                idempotency_key=str(payload["idempotency_key"]),
                retry_count=int(payload.get("retry_count") or 0) + 1,
                priority=int(payload.get("priority") or 0),
            )

    db.commit()
    logger.info(
        "linkedin_connection_acquisition_persisted job_id=%s updated=%s queued=%s deduped=%s skipped=%s",
        job_id,
        updated,
        queued,
        deduped,
        skipped,
    )
    return {
        "job_id": job_id,
        "updated": updated,
        "queued": queued,
        "deduped": deduped,
        "skipped": skipped,
    }


def _update_candidate_state(
    db: Session,
    *,
    job_id: str,
    candidate_id: str,
    status: str,
    reason: str = "",
    last_error: str = "",
    account_id: str = "",
    queue_job_id: str = "",
    idempotency_key: str = "",
    retry_count: int = 0,
    priority: int = 0,
) -> Any | None:
    repo = CandidateProfileRepository(db)
    row = repo.get(job_id=job_id, candidate_id=candidate_id)
    previous_status = _normalized_text(getattr(row, "acquisition_status", "") or "") if row is not None else ""
    row = repo.update_acquisition_state(
        job_id=job_id,
        candidate_id=candidate_id,
        status=status,
        reason=reason,
        last_error=last_error,
        account_id=account_id,
        queue_job_id=queue_job_id,
        idempotency_key=idempotency_key,
        retry_count=retry_count,
        priority=priority,
    )
    if row is not None:
        _record_transition(
            db,
            job_id=job_id,
            company_id=str(getattr(row, "company_id", "") or ""),
            candidate_id=candidate_id,
            from_status=previous_status or "",
            to_status=status,
            source="linkedin_connection_queue",
            metadata={
                "reason": reason,
                "last_error": last_error,
                "queue_job_id": queue_job_id,
                "idempotency_key": idempotency_key,
                "retry_count": retry_count,
                "priority": priority,
            },
        )
        db.commit()
    return row


def _persist_linkedin_connection_result(
    db: Session,
    *,
    candidate_id: str,
    account_id: str,
    linkedin_url: str,
    connection_status: str,
    request_sent_at: datetime | None = None,
    accepted_at: datetime | None = None,
    profile_snapshot: dict[str, Any] | None = None,
) -> None:
    now = _utcnow()
    repo = LinkedInConnectionRepository(db)
    existing = (
        db.query(LinkedInConnectionEntity)
        .filter(
            LinkedInConnectionEntity.candidate_id == candidate_id,
            LinkedInConnectionEntity.account_id == account_id,
        )
        .first()
    )
    if existing is None:
        repo.create(
            LinkedInConnectionEntity(
                id=str(uuid4()),
                candidate_id=candidate_id,
                account_id=account_id,
                linkedin_url=linkedin_url,
                connection_status=connection_status,
                request_sent_at=request_sent_at,
                accepted_at=accepted_at,
                last_checked_at=now,
                profile_snapshot_json=dict(profile_snapshot or {}),
                created_at=now,
                updated_at=now,
            )
        )
        return

    existing.linkedin_url = linkedin_url or existing.linkedin_url
    existing.connection_status = connection_status or existing.connection_status
    existing.request_sent_at = request_sent_at or existing.request_sent_at
    existing.accepted_at = accepted_at or existing.accepted_at
    existing.last_checked_at = now
    existing.profile_snapshot_json = dict(profile_snapshot or existing.profile_snapshot_json or {})
    existing.updated_at = now
    db.flush()


def process_linkedin_connection_queue_job(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = _normalized_text(payload.get("job_id"))
    candidate_id = _normalized_text(payload.get("candidate_id"))
    linkedin_url = normalize_linkedin_url(payload.get("linkedin_url"))
    retry_count = int(payload.get("retry_count") or 0)
    priority = int(payload.get("priority") or 0)
    idempotency_key = _normalized_text(payload.get("idempotency_key"))
    if not job_id or not candidate_id or not linkedin_url:
        return {"status": "skipped", "reason": "missing_payload"}

    from app.db.session import SessionLocal

    with SessionLocal() as db:
        repo = CandidateProfileRepository(db)
        candidate = repo.get(job_id=job_id, candidate_id=candidate_id) or repo.find_by_linkedin_url(job_id=job_id, linkedin_url=linkedin_url)
        if candidate is None:
            return {"status": "skipped", "reason": "candidate_missing", "job_id": job_id, "candidate_id": candidate_id}

        resolved_candidate_id = str(candidate.candidate_id or candidate_id)
        job = JobRepository(db).get(job_id)
        company_id = str(getattr(candidate, "agency_id", "") or getattr(job, "company_id", "") or getattr(job, "agency_id", "") or "")
        if candidate.acquisition_status in TERMINAL_STATUSES:
            return {
                "status": "skipped",
                "reason": "terminal_state",
                "candidate_id": resolved_candidate_id,
                "acquisition_status": candidate.acquisition_status,
            }

        account = _choose_connection_account(db, company_id=company_id)
        if account is None or not account.browser_profile_name:
            _update_candidate_state(
                db,
                job_id=job_id,
                candidate_id=resolved_candidate_id,
                status=BLOCKED_STATUS,
                reason="no_active_linkedin_account",
                last_error="no active LinkedIn account available",
                priority=priority,
            )
            return {
                "status": "blocked",
                "reason": "no_active_linkedin_account",
                "candidate_id": resolved_candidate_id,
            }

        hourly_limit = _effective_hourly_limit(account)
        if hourly_limit > 0:
            rate_key = f"linkedin:connection:hourly:{account.id}"
            allowed = rate_limit_check(rate_key, hourly_limit, 3600)
            if not allowed:
                _update_candidate_state(
                    db,
                    job_id=job_id,
                    candidate_id=resolved_candidate_id,
                    status=RETRYING_STATUS,
                    reason="hourly_rate_limit_reached",
                    last_error="hourly rate limit reached",
                    account_id=str(account.id),
                    retry_count=retry_count + 1,
                    priority=priority,
                )
                raise QueueError("linkedin connection rate limit reached", status_code=429, code="linkedin_connection_rate_limited", retryable=True)

        daily_limit = _effective_daily_limit(account)
        if daily_limit > 0 and int(account.connections_sent_today or 0) >= daily_limit:
            _update_candidate_state(
                db,
                job_id=job_id,
                candidate_id=resolved_candidate_id,
                status=RETRYING_STATUS,
                reason="daily_rate_limit_reached",
                last_error="daily limit reached",
                account_id=str(account.id),
                retry_count=retry_count + 1,
                priority=priority,
            )
            raise QueueError("linkedin connection daily limit reached", status_code=429, code="linkedin_connection_daily_limit", retryable=True)

        _update_candidate_state(
            db,
            job_id=job_id,
            candidate_id=resolved_candidate_id,
            status=SENDING_STATUS,
            reason="sending_connection_request",
            account_id=str(account.id),
            retry_count=retry_count,
            priority=priority,
            idempotency_key=idempotency_key,
        )

        worker = LinkedInConnectionWorker(account_id=str(account.browser_profile_name), timeout_ms=30000)
        note = ""
        result = asyncio.run(worker.run(linkedin_url, note, candidate_id=resolved_candidate_id))
        status_value = str(getattr(getattr(result, "status", None), "value", getattr(result, "status", ""))).strip()
        structured_status = {
            "REQUEST_SENT": PENDING_ACCEPTANCE_STATUS,
            "REQUEST_ALREADY_PENDING": PENDING_ACCEPTANCE_STATUS,
            "ALREADY_CONNECTED": ACCEPTED_STATUS,
            "LOGIN_REQUIRED": BLOCKED_STATUS,
            "PROFILE_NOT_FOUND": FAILED_STATUS,
            "FOLLOW_ONLY": BLOCKED_STATUS,
            "UNKNOWN_STATE": RETRYING_STATUS,
            "UNKNOWN_RESULT": RETRYING_STATUS,
            "FAILED": RETRYING_STATUS,
        }.get(status_value, FAILED_STATUS)

        if status_value == "REQUEST_SENT":
            account.connections_sent_today = int(account.connections_sent_today or 0) + 1
            account.updated_at = _utcnow()
            db.flush()
            _update_candidate_state(
                db,
                job_id=job_id,
                candidate_id=resolved_candidate_id,
                status=SENT_STATUS,
                reason="connection_request_sent",
                account_id=str(account.id),
                retry_count=retry_count,
                priority=priority,
            )
            _persist_linkedin_connection_result(
                db,
                candidate_id=resolved_candidate_id,
                account_id=str(account.id),
                linkedin_url=linkedin_url,
                connection_status="requested",
                request_sent_at=_utcnow(),
                profile_snapshot={
                    "worker_status": status_value,
                    "note_sent": bool(getattr(result, "note_sent", False)),
                    "duration_ms": int(getattr(result, "duration_ms", 0) or 0),
                },
            )
        elif status_value == "REQUEST_ALREADY_PENDING":
            _persist_linkedin_connection_result(
                db,
                candidate_id=resolved_candidate_id,
                account_id=str(account.id),
                linkedin_url=linkedin_url,
                connection_status="requested",
                profile_snapshot={
                    "worker_status": status_value,
                    "note_sent": bool(getattr(result, "note_sent", False)),
                    "duration_ms": int(getattr(result, "duration_ms", 0) or 0),
                },
            )
        _update_candidate_state(
            db,
            job_id=job_id,
            candidate_id=resolved_candidate_id,
            status=structured_status,
            reason=status_value.lower() or "connection_worker_result",
            last_error=str(getattr(result, "error_message", "") or ""),
            account_id=str(account.id),
            retry_count=retry_count,
            priority=priority,
        )
        if structured_status == PENDING_ACCEPTANCE_STATUS:
            candidate.acquisition_sent_at = candidate.acquisition_sent_at or _utcnow()
            candidate.acquisition_pending_acceptance_at = _utcnow()
            candidate.acquisition_status = PENDING_ACCEPTANCE_STATUS
            candidate.acquisition_status_reason = "awaiting_acceptance"
            db.commit()
        elif structured_status == ACCEPTED_STATUS:
            candidate.acquisition_accepted_at = _utcnow()
            candidate.acquisition_status = ACCEPTED_STATUS
            candidate.acquisition_status_reason = "already_connected"
            _persist_linkedin_connection_result(
                db,
                candidate_id=resolved_candidate_id,
                account_id=str(account.id),
                linkedin_url=linkedin_url,
                connection_status="accepted",
                accepted_at=_utcnow(),
                profile_snapshot={
                    "worker_status": status_value,
                    "note_sent": bool(getattr(result, "note_sent", False)),
                    "duration_ms": int(getattr(result, "duration_ms", 0) or 0),
                },
            )
            db.commit()
        elif structured_status == BLOCKED_STATUS:
            candidate.acquisition_blocked_at = _utcnow()
            candidate.acquisition_status = BLOCKED_STATUS
            _persist_linkedin_connection_result(
                db,
                candidate_id=resolved_candidate_id,
                account_id=str(account.id),
                linkedin_url=linkedin_url,
                connection_status="blocked",
                profile_snapshot={
                    "worker_status": status_value,
                    "error_message": str(getattr(result, "error_message", "") or ""),
                },
            )
            db.commit()
        elif structured_status == RETRYING_STATUS:
            candidate.acquisition_retrying_at = _utcnow()
            candidate.acquisition_status = RETRYING_STATUS
            _persist_linkedin_connection_result(
                db,
                candidate_id=resolved_candidate_id,
                account_id=str(account.id),
                linkedin_url=linkedin_url,
                connection_status="retrying",
                profile_snapshot={
                    "worker_status": status_value,
                    "error_message": str(getattr(result, "error_message", "") or ""),
                },
            )
            db.commit()
        else:
            candidate.acquisition_failed_at = _utcnow()
            candidate.acquisition_status = FAILED_STATUS
            _persist_linkedin_connection_result(
                db,
                candidate_id=resolved_candidate_id,
                account_id=str(account.id),
                linkedin_url=linkedin_url,
                connection_status="failed",
                profile_snapshot={
                    "worker_status": status_value,
                    "error_message": str(getattr(result, "error_message", "") or ""),
                },
            )
            db.commit()

        if status_value == "REQUEST_SENT":
            return {
                "status": "requested",
                "candidate_id": resolved_candidate_id,
                "linkedin_url": linkedin_url,
                "account_id": str(account.id),
                "browser_profile_name": account.browser_profile_name,
                "worker_status": status_value,
                "note_sent": bool(getattr(result, "note_sent", False)),
                "duration_ms": int(getattr(result, "duration_ms", 0) or 0),
            }

        return {
            "status": structured_status.lower(),
            "candidate_id": resolved_candidate_id,
            "linkedin_url": linkedin_url,
            "account_id": str(account.id),
            "browser_profile_name": account.browser_profile_name,
            "worker_status": status_value,
            "error_message": str(getattr(result, "error_message", "") or ""),
            "duration_ms": int(getattr(result, "duration_ms", 0) or 0),
        }
