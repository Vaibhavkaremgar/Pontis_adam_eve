from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from app.core.config import (
    APP_ENV,
    JOB_QUEUE_BACKOFF_BASE_SECONDS,
    JOB_QUEUE_JOB_TTL_SECONDS,
    JOB_QUEUE_VISIBILITY_TIMEOUT_SECONDS,
    JOB_QUEUE_WORKERS_PER_TYPE,
)
from app.core.config import REDIS_URL
from app.utils.exceptions import QueueError
from app.services.metrics_service import log_metric
from app.services.redis_service import get_redis

logger = logging.getLogger(__name__)

QUEUE_TYPES = (
    "outreach_send",
    "outreach_followup",
    "candidate_enrichment",
    "embedding_generation",
    "candidate_refresh",
    "reply_processing",
)

_STOP_EVENT = threading.Event()
_WORKERS: list[threading.Thread] = []
_HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {}
_MAINTENANCE_INTERVAL_SECONDS = 5
_QUEUE_ALERT_STUCK_SECONDS = max(30, JOB_QUEUE_VISIBILITY_TIMEOUT_SECONDS * 2)
_QUEUE_CLEANUP_RAN = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _key(*parts: str) -> str:
    return "pontis:queue:" + ":".join(parts)


def _ready_key(queue_type: str) -> str:
    return _key(queue_type, "ready")


def _processing_key(queue_type: str) -> str:
    return _key(queue_type, "processing")


def _processing_meta_key(queue_type: str) -> str:
    return _key(queue_type, "processing_meta")


def _delayed_key(queue_type: str) -> str:
    return _key(queue_type, "delayed")


def _job_key(queue_type: str, job_id: str) -> str:
    return _key(queue_type, "job", job_id)


def _dedupe_key(queue_type: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return _key(queue_type, "dedupe", digest)


def _dead_key(queue_type: str) -> str:
    return _key(queue_type, "dead")


def _dead_meta_key(queue_type: str) -> str:
    return _key(queue_type, "dead_meta")


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)


def _json_loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def classify_queue_failure(exc: Exception) -> dict[str, Any]:
    message = str(exc).strip().lower()
    retryable = True
    code = "queue_retryable"
    category = "queue"
    if any(token in message for token in ("validation", "invalid payload", "not found", "forbidden")):
        retryable = False
        code = "queue_invalid_payload"
    elif any(token in message for token in ("auth", "permission", "unauthorized")):
        retryable = False
        code = "queue_unauthorized"
    elif any(token in message for token in ("timeout", "temporarily unavailable", "rate limit", "429", "503")):
        retryable = True
        code = "queue_provider_throttle"
    return {"code": code, "category": category, "retryable": retryable, "message": str(exc)}


def register_job_handler(queue_type: str, handler: Callable[[dict[str, Any]], Any]) -> None:
    if queue_type not in QUEUE_TYPES:
        raise ValueError(f"Unsupported queue type: {queue_type}")
    _HANDLERS[queue_type] = handler


def _default_idempotency_key(queue_type: str, payload: dict[str, Any]) -> str:
    material = _json_dumps({"queue_type": queue_type, "payload": payload})
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def enqueue_job(
    queue_type: str,
    payload: dict[str, Any],
    *,
    idempotency_key: str | None = None,
    delay_seconds: int = 0,
    max_attempts: int = 5,
    job_id: str | None = None,
) -> dict[str, Any]:
    if queue_type not in QUEUE_TYPES:
        raise ValueError(f"Unsupported queue type: {queue_type}")

    redis = get_redis()
    normalized_payload = dict(payload or {})
    normalized_payload.setdefault("queue_type", queue_type)
    normalized_payload.setdefault("attempts", 0)
    normalized_payload.setdefault("max_attempts", max(1, int(max_attempts or 1)))
    normalized_payload.setdefault("created_at", _utcnow().isoformat())
    normalized_payload.setdefault("updated_at", _utcnow().isoformat())
    normalized_payload.setdefault("status", "queued")
    normalized_payload.setdefault("job_id", job_id or "")

    if not idempotency_key:
        idempotency_key = _default_idempotency_key(queue_type, normalized_payload)

    if redis is None:
        if APP_ENV in {"production", "prod"}:
            logger.critical("queue_unavailable_production queue_type=%s", queue_type)
            raise QueueError(f"Redis is required for queue '{queue_type}' in production")
        background_job_id = job_id or uuid4().hex
        normalized_payload["job_id"] = background_job_id
        normalized_payload["status"] = "queued_ephemeral"
        logger.warning("queue_unavailable running_ephemeral queue_type=%s job_id=%s", queue_type, background_job_id)
        thread = threading.Thread(
            target=_run_inline_fallback,
            args=(queue_type, background_job_id, normalized_payload),
            daemon=True,
        )
        thread.start()
        return {
            "queued": False,
            "mode": "ephemeral",
            "queue_type": queue_type,
            "job_id": background_job_id,
            "idempotency_key": idempotency_key,
        }

    existing_job_id = redis.get(_dedupe_key(queue_type, idempotency_key))
    if existing_job_id:
        logger.info(
            "job_queue_deduplicated queue_type=%s job_id=%s idempotency_key=%s",
            queue_type,
            existing_job_id,
            idempotency_key,
        )
        return {
            "queued": True,
            "deduplicated": True,
            "queue_type": queue_type,
            "job_id": existing_job_id,
            "idempotency_key": idempotency_key,
        }

    resolved_job_id = job_id or uuid4().hex
    normalized_payload["job_id"] = resolved_job_id
    normalized_payload["status"] = "queued"
    normalized_payload["updated_at"] = _utcnow().isoformat()
    normalized_payload["attempts"] = int(normalized_payload.get("attempts") or 0)
    normalized_payload["max_attempts"] = max(1, int(normalized_payload.get("max_attempts") or max_attempts or 1))
    normalized_payload["idempotency_key"] = idempotency_key
    normalized_payload["available_at"] = (
        _utcnow().timestamp() + max(0, int(delay_seconds or 0))
    )

    claimed = redis.set(
        _dedupe_key(queue_type, idempotency_key),
        resolved_job_id,
        ex=JOB_QUEUE_JOB_TTL_SECONDS,
        nx=True,
    )
    if not claimed:
        existing_job_id = redis.get(_dedupe_key(queue_type, idempotency_key))
        return {
            "queued": True,
            "deduplicated": True,
            "queue_type": queue_type,
            "job_id": existing_job_id or resolved_job_id,
            "idempotency_key": idempotency_key,
        }

    payload_json = _json_dumps(normalized_payload)
    pipeline = redis.pipeline()
    pipeline.set(_job_key(queue_type, resolved_job_id), payload_json, ex=JOB_QUEUE_JOB_TTL_SECONDS)
    if delay_seconds > 0:
        pipeline.zadd(_delayed_key(queue_type), {resolved_job_id: normalized_payload["available_at"]})
    else:
        pipeline.lpush(_ready_key(queue_type), resolved_job_id)
    pipeline.execute()

    logger.info(
        "job_queue_enqueued queue_type=%s job_id=%s delay_seconds=%s max_attempts=%s",
        queue_type,
        resolved_job_id,
        delay_seconds,
        normalized_payload["max_attempts"],
    )
    log_metric(
        "queue_job_enqueued",
        queue_type=queue_type,
        job_id=resolved_job_id,
        delay_seconds=delay_seconds,
        max_attempts=normalized_payload["max_attempts"],
    )
    return {
        "queued": True,
        "queue_type": queue_type,
        "job_id": resolved_job_id,
        "idempotency_key": idempotency_key,
        "delay_seconds": delay_seconds,
    }


def _run_inline_fallback(queue_type: str, job_id: str, payload: dict[str, Any]) -> None:
    handler = _HANDLERS.get(queue_type)
    if handler is None:
        _resolve_default_handler(queue_type)(payload)
        return
    try:
        handler(payload)
    except Exception as exc:
        logger.error(
            "job_queue_ephemeral_failed queue_type=%s job_id=%s error=%s",
            queue_type,
            job_id,
            str(exc),
            exc_info=exc,
        )


def _resolve_default_handler(queue_type: str) -> Callable[[dict[str, Any]], Any]:
    if queue_type == "outreach_send":
        from app.db.session import SessionLocal
        from app.services.outreach_service import process_outreach

        def _handler(payload: dict[str, Any]) -> Any:
            with SessionLocal() as db:
                return process_outreach(
                    db=db,
                    job_id=str(payload.get("job_id") or ""),
                    selected_candidates=list(payload.get("selected_candidates") or []),
                    custom_body=str(payload.get("custom_body") or ""),
                )

        return _handler

    if queue_type == "outreach_followup":
        from app.db.session import SessionLocal
        from app.services.outreach_service import run_followup_cycle

        def _handler(_: dict[str, Any]) -> Any:
            with SessionLocal() as db:
                return run_followup_cycle(db)

        return _handler

    if queue_type == "candidate_enrichment":
        from app.db.session import SessionLocal
        from app.services.sourcing.apify_enrichment_service import enrich_selected_candidate
        from app.services.sourcing.outreach_trigger_service import trigger_outreach_after_enrichment

        def _handler(payload: dict[str, Any]) -> Any:
            job_id = str(payload.get("job_id") or "")
            candidate_id = str(payload.get("candidate_id") or "")
            if not job_id or not candidate_id:
                return {"status": "skipped", "reason": "missing_job_or_candidate"}
            candidate_snapshot = payload.get("candidateSnapshot")
            candidate_snapshot_map = candidate_snapshot if isinstance(candidate_snapshot, dict) else {}
            with SessionLocal() as db:
                enrichment = enrich_selected_candidate(
                    db=db,
                    job_id=job_id,
                    candidate_id=candidate_id,
                    source_type=str(payload.get("source_type") or payload.get("sourceType") or "linkedin_xray"),
                    linkedin_url=str(
                        payload.get("linkedin_url")
                        or payload.get("linkedinUrl")
                        or candidate_snapshot_map.get("linkedinUrl")
                        or candidate_snapshot_map.get("linkedin_url")
                        or ""
                    ),
                    workflow_token=str(payload.get("workflow_token") or payload.get("workflowToken") or ""),
                    selection_session_id=str(payload.get("selection_session_id") or payload.get("selectionSessionId") or ""),
                    automation_job_id=str(payload.get("automation_job_id") or payload.get("automationJobId") or payload.get("job_id") or ""),
                )
                outreach = trigger_outreach_after_enrichment(
                    db=db,
                    job_id=job_id,
                    candidate_id=candidate_id,
                    enrichment_result=enrichment,
                    selection_session_id=str(payload.get("selection_session_id") or payload.get("selectionSessionId") or ""),
                    automation_job_id=str(payload.get("automation_job_id") or payload.get("automationJobId") or payload.get("job_id") or ""),
                    source_type=str(payload.get("source_type") or payload.get("sourceType") or "linkedin_xray"),
                )
                db.commit()
                return {"status": enrichment.get("status") or "completed", "enrichment": enrichment, "outreach": outreach}

        return _handler

    if queue_type == "embedding_generation":
        from app.db.session import SessionLocal
        from app.services.candidate_refresh_service import refresh_candidate
        from app.db.repositories import CandidateProfileRepository

        def _handler(payload: dict[str, Any]) -> Any:
            job_id = str(payload.get("job_id") or "")
            candidate_id = str(payload.get("candidate_id") or "")
            if not job_id or not candidate_id:
                return {"processed": 0, "skipped": 1}
            with SessionLocal() as db:
                candidate = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
                if not candidate:
                    return {"processed": 0, "skipped": 1}
                refreshed = refresh_candidate(db, candidate)
                db.commit()
                return {"processed": 1, "refreshed": int(bool(refreshed)), "skipped": int(not refreshed)}

        return _handler

    if queue_type == "candidate_refresh":
        from app.db.repositories import CandidateProfileRepository
        from app.services.candidate_refresh_service import refresh_candidate, refresh_candidates

        def _handler(payload: dict[str, Any]) -> Any:
            job_id = str(payload.get("job_id") or "").strip()
            candidate_id = str(payload.get("candidate_id") or "").strip()
            if job_id and candidate_id:
                from app.db.session import SessionLocal

                with SessionLocal() as db:
                    candidate = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
                    if not candidate:
                        return {"processed": 0, "skipped": 1}
                    refreshed = refresh_candidate(db, candidate)
                    db.commit()
                    return {
                        "processed": 1,
                        "refreshed": int(bool(refreshed)),
                        "skipped": int(not refreshed),
                        "job_id": job_id,
                        "candidate_id": candidate_id,
                    }
            batch_size = int(payload.get("batch_size") or 100)
            stale_days = int(payload.get("stale_days") or 7)
            return refresh_candidates(batch_size=batch_size, stale_days=stale_days)

        return _handler

    if queue_type == "reply_processing":
        def _handler(_: dict[str, Any]) -> Any:
            return {"status": "disabled", "reason": "resend_webhook_only"}

        return _handler

    raise ValueError(f"Unsupported queue type: {queue_type}")


def _load_job(redis, queue_type: str, job_id: str) -> dict[str, Any]:
    raw = redis.get(_job_key(queue_type, job_id))
    return _json_loads(raw)


def _remove_from_processing(redis, queue_type: str, job_id: str) -> None:
    redis.lrem(_processing_key(queue_type), 1, job_id)
    redis.hdel(_processing_meta_key(queue_type), job_id)


def _promote_due_jobs(redis, queue_type: str) -> int:
    now_ts = _utcnow().timestamp()
    job_ids = redis.zrangebyscore(_delayed_key(queue_type), 0, now_ts)
    if not job_ids:
        return 0

    pipeline = redis.pipeline()
    for job_id in job_ids:
        pipeline.zrem(_delayed_key(queue_type), job_id)
        pipeline.lpush(_ready_key(queue_type), job_id)
        current = _load_job(redis, queue_type, job_id)
        if current:
            current["status"] = "queued"
            current["updated_at"] = _utcnow().isoformat()
            pipeline.set(_job_key(queue_type, job_id), _json_dumps(current), ex=JOB_QUEUE_JOB_TTL_SECONDS)
    pipeline.execute()
    logger.info("job_queue_promoted queue_type=%s promoted=%s", queue_type, len(job_ids))
    return len(job_ids)


def _requeue_stale_processing(redis, queue_type: str) -> int:
    stale_before = _utcnow().timestamp() - max(1, int(JOB_QUEUE_VISIBILITY_TIMEOUT_SECONDS))
    stale_count = 0
    meta = redis.hgetall(_processing_meta_key(queue_type)) or {}
    for job_id, raw_meta in meta.items():
        payload = _json_loads(raw_meta)
        claimed_at = float(payload.get("claimed_at") or 0.0)
        if claimed_at <= 0 or claimed_at > stale_before:
            continue
        _remove_from_processing(redis, queue_type, job_id)
        redis.lpush(_ready_key(queue_type), job_id)
        current = _load_job(redis, queue_type, job_id)
        if current:
            current["status"] = "queued"
            current["updated_at"] = _utcnow().isoformat()
            redis.set(_job_key(queue_type, job_id), _json_dumps(current), ex=JOB_QUEUE_JOB_TTL_SECONDS)
        stale_count += 1
    if stale_count:
        logger.warning("job_queue_requeued_stale queue_type=%s stale_count=%s", queue_type, stale_count)
    return stale_count


def _mark_dead(redis, queue_type: str, job_id: str, payload: dict[str, Any], error: str) -> None:
    payload = dict(payload)
    payload["status"] = "dead_letter"
    payload["last_error"] = error
    payload["updated_at"] = _utcnow().isoformat()
    payload_json = _json_dumps(payload)
    pipe = redis.pipeline()
    pipe.set(_job_key(queue_type, job_id), payload_json, ex=JOB_QUEUE_JOB_TTL_SECONDS)
    pipe.hset(_dead_key(queue_type), job_id, payload_json)
    pipe.hset(
        _dead_meta_key(queue_type),
        job_id,
        _json_dumps(
            {
                "error": error,
                "status": "dead_letter",
                "updated_at": payload["updated_at"],
                "attempts": int(payload.get("attempts") or 0),
                "last_error": error,
            }
        ),
    )
    pipe.execute()
    logger.error("job_queue_dead_letter queue_type=%s job_id=%s error=%s", queue_type, job_id, error)
    log_metric("queue_job_deadlettered", queue_type=queue_type, job_id=job_id, error=error)


def _discard_terminal_job(redis, queue_type: str, job_id: str, payload: dict[str, Any], error: str) -> None:
    _mark_dead(redis, queue_type, job_id, payload, error)


def _retry_job(redis, queue_type: str, job_id: str, payload: dict[str, Any], error: str) -> None:
    attempts = int(payload.get("attempts") or 0) + 1
    max_attempts = max(1, int(payload.get("max_attempts") or 1))
    payload = dict(payload)
    payload["attempts"] = attempts
    payload["last_error"] = error
    payload["status"] = "retrying" if attempts < max_attempts else "dead_letter"
    payload["updated_at"] = _utcnow().isoformat()
    payload_json = _json_dumps(payload)

    if attempts >= max_attempts:
        _mark_dead(redis, queue_type, job_id, payload, error)
        return

    backoff_seconds = min(
        900,
        max(1, JOB_QUEUE_BACKOFF_BASE_SECONDS) * (2 ** max(0, attempts - 1)),
    )
    available_at = _utcnow().timestamp() + backoff_seconds
    payload["available_at"] = available_at
    payload_json = _json_dumps(payload)
    pipe = redis.pipeline()
    pipe.set(_job_key(queue_type, job_id), payload_json, ex=JOB_QUEUE_JOB_TTL_SECONDS)
    pipe.hdel(_processing_meta_key(queue_type), job_id)
    pipe.lrem(_processing_key(queue_type), 1, job_id)
    pipe.zadd(_delayed_key(queue_type), {job_id: available_at})
    pipe.execute()
    logger.warning(
        "job_queue_retry_scheduled queue_type=%s job_id=%s attempts=%s max_attempts=%s backoff_seconds=%s error=%s",
        queue_type,
        job_id,
        attempts,
        max_attempts,
        backoff_seconds,
        error,
    )
    log_metric(
        "queue_job_retry",
        queue_type=queue_type,
        job_id=job_id,
        attempts=attempts,
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
    )


def _complete_job(redis, queue_type: str, job_id: str, payload: dict[str, Any], result: Any) -> None:
    payload = dict(payload)
    payload["status"] = "completed"
    payload["last_result"] = result if isinstance(result, (str, int, float, dict, list, bool)) else str(result)
    payload["completed_at"] = _utcnow().isoformat()
    payload["updated_at"] = payload["completed_at"]
    pipe = redis.pipeline()
    pipe.set(_job_key(queue_type, job_id), _json_dumps(payload), ex=JOB_QUEUE_JOB_TTL_SECONDS)
    pipe.hdel(_processing_meta_key(queue_type), job_id)
    pipe.lrem(_processing_key(queue_type), 1, job_id)
    pipe.execute()
    logger.info("job_queue_completed queue_type=%s job_id=%s", queue_type, job_id)
    log_metric("queue_job_completed", queue_type=queue_type, job_id=job_id)


def _process_single_job(redis, queue_type: str, job_id: str, worker_name: str) -> None:
    payload = _load_job(redis, queue_type, job_id)
    if not payload:
        _remove_from_processing(redis, queue_type, job_id)
        logger.warning("job_queue_missing_payload queue_type=%s job_id=%s", queue_type, job_id)
        return

    payload.setdefault("attempts", 0)
    payload.setdefault("max_attempts", 5)
    payload["status"] = "processing"
    payload["worker"] = worker_name
    payload["claimed_at"] = _utcnow().timestamp()
    payload["updated_at"] = _utcnow().isoformat()
    redis.hset(
        _processing_meta_key(queue_type),
        job_id,
        _json_dumps({"claimed_at": payload["claimed_at"], "worker": worker_name}),
    )
    redis.set(_job_key(queue_type, job_id), _json_dumps(payload), ex=JOB_QUEUE_JOB_TTL_SECONDS)

    handler = _HANDLERS.get(queue_type) or _resolve_default_handler(queue_type)
    logger.info(
        "job_queue_processing queue_type=%s job_id=%s attempts=%s worker=%s",
        queue_type,
        job_id,
        payload.get("attempts"),
        worker_name,
    )
    try:
        result = handler(payload)
        _complete_job(redis, queue_type, job_id, payload, result)
    except Exception as exc:
        failure = classify_queue_failure(exc)
        error = failure["message"]
        logger.warning(
            "job_queue_failed queue_type=%s job_id=%s attempts=%s error=%s",
            queue_type,
            job_id,
            int(payload.get("attempts") or 0) + 1,
            error,
            exc_info=exc,
        )
        payload["last_error"] = error
        payload["failure_code"] = failure["code"]
        payload["failure_category"] = failure["category"]
        if not failure["retryable"]:
            _discard_terminal_job(redis, queue_type, job_id, payload, error)
            return
        _retry_job(redis, queue_type, job_id, payload, error)


def cleanup_orphaned_queue_entries() -> dict[str, int]:
    redis = get_redis()
    if redis is None:
        logger.info("job_queue_cleanup_skipped reason=redis_unavailable")
        return {"removed": 0, "dead_removed": 0, "processing_removed": 0, "ready_removed": 0, "delayed_removed": 0}

    from app.db.session import SessionLocal
    from app.models.entities import JobEntity
    from sqlalchemy import select

    with SessionLocal() as db:
        valid_job_ids = {
            str(job_id or "").strip()
            for (job_id,) in db.execute(select(JobEntity.id)).all()
            if str(job_id or "").strip()
        }

    removed = 0
    dead_removed = 0
    processing_removed = 0
    ready_removed = 0
    delayed_removed = 0

    for queue_type in QUEUE_TYPES:
        ready_ids = list(redis.lrange(_ready_key(queue_type), 0, -1) or [])
        processing_ids = list(redis.lrange(_processing_key(queue_type), 0, -1) or [])
        delayed_ids = list(redis.zrange(_delayed_key(queue_type), 0, -1) or [])
        dead_items = dict(redis.hgetall(_dead_key(queue_type)) or {})
        dead_meta_items = dict(redis.hgetall(_dead_meta_key(queue_type)) or {})

        for job_id in ready_ids:
            payload = _load_job(redis, queue_type, str(job_id))
            if payload and str(payload.get("job_id") or job_id).strip() in valid_job_ids:
                continue
            idempotency_key = str((payload or {}).get("idempotency_key") or "").strip()
            if idempotency_key:
                redis.delete(_dedupe_key(queue_type, idempotency_key))
            redis.lrem(_ready_key(queue_type), 0, job_id)
            redis.delete(_job_key(queue_type, str(job_id)))
            ready_removed += 1
            removed += 1

        for job_id in processing_ids:
            payload = _load_job(redis, queue_type, str(job_id))
            if payload and str(payload.get("job_id") or job_id).strip() in valid_job_ids:
                continue
            idempotency_key = str((payload or {}).get("idempotency_key") or "").strip()
            if idempotency_key:
                redis.delete(_dedupe_key(queue_type, idempotency_key))
            redis.lrem(_processing_key(queue_type), 0, job_id)
            redis.hdel(_processing_meta_key(queue_type), job_id)
            redis.delete(_job_key(queue_type, str(job_id)))
            processing_removed += 1
            removed += 1

        for job_id in delayed_ids:
            payload = _load_job(redis, queue_type, str(job_id))
            if payload and str(payload.get("job_id") or job_id).strip() in valid_job_ids:
                continue
            idempotency_key = str((payload or {}).get("idempotency_key") or "").strip()
            if idempotency_key:
                redis.delete(_dedupe_key(queue_type, idempotency_key))
            redis.zrem(_delayed_key(queue_type), job_id)
            redis.delete(_job_key(queue_type, str(job_id)))
            delayed_removed += 1
            removed += 1

        for job_id, raw_payload in dead_items.items():
            payload = _json_loads(raw_payload)
            if payload and str(payload.get("job_id") or job_id).strip() in valid_job_ids:
                continue
            idempotency_key = str((payload or {}).get("idempotency_key") or "").strip()
            if idempotency_key:
                redis.delete(_dedupe_key(queue_type, idempotency_key))
            redis.hdel(_dead_key(queue_type), job_id)
            redis.hdel(_dead_meta_key(queue_type), job_id)
            redis.delete(_job_key(queue_type, str(job_id)))
            dead_removed += 1
            removed += 1

        for job_id, raw_meta in dead_meta_items.items():
            if job_id in dead_items:
                continue
            meta = _json_loads(raw_meta)
            if meta and str(meta.get("job_id") or "").strip() in valid_job_ids:
                continue
            redis.hdel(_dead_meta_key(queue_type), job_id)

    logger.info(
        "job_queue_cleanup_completed removed=%s dead_removed=%s processing_removed=%s ready_removed=%s delayed_removed=%s",
        removed,
        dead_removed,
        processing_removed,
        ready_removed,
        delayed_removed,
    )
    return {
        "removed": removed,
        "dead_removed": dead_removed,
        "processing_removed": processing_removed,
        "ready_removed": ready_removed,
        "delayed_removed": delayed_removed,
    }


def _worker_loop(queue_type: str, index: int) -> None:
    redis = None
    worker_name = f"{queue_type}-worker-{index}"
    last_maintenance = 0.0
    logger.info("job_queue_worker_started queue_type=%s worker=%s", queue_type, worker_name)
    while not _STOP_EVENT.is_set():
        try:
            redis = get_redis()
            if redis is None:
                time.sleep(1.0)
                continue

            now = time.monotonic()
            if now - last_maintenance >= _MAINTENANCE_INTERVAL_SECONDS:
                _promote_due_jobs(redis, queue_type)
                _requeue_stale_processing(redis, queue_type)
                last_maintenance = now

            job_id = redis.brpoplpush(_ready_key(queue_type), _processing_key(queue_type), timeout=1)
            if not job_id:
                continue
            _process_single_job(redis, queue_type, str(job_id), worker_name)
        except Exception as exc:
            logger.error(
                "job_queue_worker_error queue_type=%s worker=%s error=%s",
                queue_type,
                worker_name,
                str(exc),
                exc_info=exc,
            )
            time.sleep(1.0)
    logger.info("job_queue_worker_stopped queue_type=%s worker=%s", queue_type, worker_name)


def start_job_queue_workers() -> None:
    if _WORKERS:
        return

    global _QUEUE_CLEANUP_RAN
    if not _QUEUE_CLEANUP_RAN:
        try:
            cleanup_orphaned_queue_entries()
        except Exception as exc:
            logger.warning("job_queue_cleanup_failed error=%s", str(exc), exc_info=exc)
        finally:
            _QUEUE_CLEANUP_RAN = True

    _STOP_EVENT.clear()
    per_type = max(1, int(JOB_QUEUE_WORKERS_PER_TYPE))
    for queue_type in QUEUE_TYPES:
        for index in range(per_type):
            thread = threading.Thread(target=_worker_loop, args=(queue_type, index), daemon=True)
            thread.start()
            _WORKERS.append(thread)
    logger.info(
        "job_queue_workers_started queue_types=%s per_type=%s total_workers=%s",
        len(QUEUE_TYPES),
        per_type,
        len(_WORKERS),
    )


def stop_job_queue_workers(*, timeout_seconds: float = 5.0) -> None:
    if not _WORKERS:
        return
    _STOP_EVENT.set()
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    for thread in list(_WORKERS):
        remaining = max(0.0, deadline - time.monotonic())
        thread.join(timeout=remaining)
    _WORKERS.clear()
    logger.info("job_queue_workers_stopped")


def queue_depth_snapshot() -> dict[str, Any]:
    redis = get_redis()
    if redis is None:
        return {
            "status": "degraded",
            "redis": "unavailable",
            "queues": {},
            "workers": len(_WORKERS),
        }

    queues: dict[str, dict[str, int]] = {}
    for queue_type in QUEUE_TYPES:
        ready = int(redis.llen(_ready_key(queue_type)) or 0)
        processing = int(redis.llen(_processing_key(queue_type)) or 0)
        delayed = int(redis.zcard(_delayed_key(queue_type)) or 0)
        dead = int(redis.hlen(_dead_key(queue_type)) or 0)
        queues[queue_type] = {
            "ready": ready,
            "processing": processing,
            "delayed": delayed,
            "dead": dead,
            "stuck_processing": int(redis.hlen(_processing_meta_key(queue_type)) or 0),
            "oldest_ready_age_seconds": _oldest_ready_age_seconds(redis, queue_type),
        }
    return {
        "status": "ok",
        "queues": queues,
        "workers": len(_WORKERS),
        "redis": "connected",
    }


def _oldest_ready_age_seconds(redis, queue_type: str) -> int:
    try:
        job_id = redis.lindex(_ready_key(queue_type), -1)
        if not job_id:
            return 0
        payload = _load_job(redis, queue_type, str(job_id))
        created_at = str(payload.get("created_at") or "")
        if not created_at:
            return 0
        created = datetime.fromisoformat(created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return max(0, int((_utcnow() - created).total_seconds()))
    except Exception:
        return 0


def queue_health_snapshot() -> dict[str, Any]:
    redis = get_redis()
    if redis is None:
        return {
            "status": "degraded",
            "error": "redis_unavailable",
            "queue_depth": {},
            "redis_latency_ms": None,
            "workers": len(_WORKERS),
        }

    started = time.perf_counter()
    try:
        redis.ping()
        redis_latency_ms = round((time.perf_counter() - started) * 1000, 2)
    except Exception as exc:
        return {
            "status": "down",
            "error": str(exc),
            "queue_depth": queue_depth_snapshot(),
            "redis_latency_ms": None,
            "workers": len(_WORKERS),
        }

    snapshot = queue_depth_snapshot()
    status = "ok"
    if any(queue_stats.get("dead", 0) > 0 for queue_stats in snapshot.get("queues", {}).values()):
        status = "degraded"
    elif any(queue_stats.get("ready", 0) > 100 for queue_stats in snapshot.get("queues", {}).values()):
        status = "degraded"
    elif any(queue_stats.get("stuck_processing", 0) > 0 for queue_stats in snapshot.get("queues", {}).values()):
        status = "degraded"
    return {
        "status": status,
        "error": "",
        "queue_depth": snapshot,
        "redis_latency_ms": redis_latency_ms,
        "workers": len(_WORKERS),
    }


def list_dead_letter_jobs(*, queue_type: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    redis = get_redis()
    if redis is None:
        return []

    queue_types = (queue_type,) if queue_type else QUEUE_TYPES
    rows: list[dict[str, Any]] = []
    for current_queue in queue_types:
        if current_queue not in QUEUE_TYPES:
            continue
        items = redis.hgetall(_dead_key(current_queue)) or {}
        meta = redis.hgetall(_dead_meta_key(current_queue)) or {}
        for job_id, raw_payload in items.items():
            payload = _json_loads(raw_payload)
            payload_meta = _json_loads(meta.get(job_id))
            rows.append(
                {
                    "queueType": current_queue,
                    "jobId": job_id,
                    "status": payload.get("status") or payload_meta.get("status") or "dead_letter",
                    "attempts": int(payload.get("attempts") or payload_meta.get("attempts") or 0),
                    "lastError": payload.get("last_error") or payload_meta.get("error") or "",
                    "updatedAt": payload.get("updated_at") or payload_meta.get("updated_at") or "",
                    "payload": payload,
                }
            )
    rows.sort(key=lambda item: (item.get("updatedAt") or ""), reverse=True)
    return rows[: max(1, limit)]


def replay_dead_letter_job(queue_type: str, job_id: str) -> dict[str, Any]:
    if queue_type not in QUEUE_TYPES:
        raise QueueError(f"Unsupported queue type: {queue_type}", status_code=400, code="queue_invalid_type", retryable=False)
    redis = get_redis()
    if redis is None:
        raise QueueError("Redis is unavailable", code="queue_redis_unavailable", retryable=True)

    raw_payload = redis.hget(_dead_key(queue_type), job_id)
    if not raw_payload:
        raise QueueError("Dead-letter job not found", status_code=404, code="queue_dead_letter_missing", retryable=False)
    payload = _json_loads(raw_payload)
    if not payload:
        raise QueueError("Dead-letter payload is invalid", code="queue_dead_letter_invalid", retryable=False)

    payload["status"] = "queued"
    payload["updated_at"] = _utcnow().isoformat()
    payload["last_error"] = ""
    payload["attempts"] = 0
    pipe = redis.pipeline()
    pipe.hdel(_dead_key(queue_type), job_id)
    pipe.hdel(_dead_meta_key(queue_type), job_id)
    pipe.execute()
    enqueue_job(
        queue_type,
        payload,
        idempotency_key=payload.get("idempotency_key") or f"replay:{queue_type}:{job_id}",
        job_id=job_id,
    )
    log_metric("queue_dead_letter_replayed", queue_type=queue_type, job_id=job_id)
    return {"queueType": queue_type, "jobId": job_id, "replayed": True}
