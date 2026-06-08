from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import APP_ENV, APIFY_TOKEN, REFRESH_CANDIDATE_LIMIT, STALE_DAYS
from app.db.repositories import CandidateProfileRepository, JobRepository
from app.db.session import SessionLocal
from app.services.apify_enrichment_service import enrich_candidate_with_apify
from app.services.embedding_registry_service import get_active_embedding_version
from app.services.candidate_text import build_candidate_text
from app.services.embedding_service import embed
from app.services.metrics_service import log_metric
from app.services.job_queue_service import enqueue_job
from app.services.outreach_service import APIFY_ENRICHMENT_TEST_FALLBACK_EMAIL
from app.services.qdrant_service import QdrantUnavailableError, ensure_all_collections, upsert_candidate_chunks
from app.utils.text import average_vectors, chunk_text

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _candidate_text_payload(candidate) -> dict[str, Any]:
    raw_data = getattr(candidate, "raw_data", None) if candidate is not None else None
    payload: dict[str, Any] = {}
    if isinstance(raw_data, dict):
        payload.update(raw_data)
    payload.update(
        {
            "role": getattr(candidate, "role", "") or payload.get("role") or payload.get("title") or "",
            "headline": payload.get("headline") or getattr(candidate, "role", "") or payload.get("title") or "",
            "location": getattr(candidate, "location", "") or payload.get("location") or payload.get("location_name") or payload.get("location_region") or "",
            "skills": list(getattr(candidate, "skills", None) or payload.get("skills") or payload.get("skills_required") or []),
            "experience": payload.get("experience") or payload.get("experience_level") or payload.get("experience_required") or payload.get("years_experience") or "",
            "years_experience": payload.get("years_experience") or payload.get("yearsExperience") or getattr(candidate, "total_experience_years", 0.0) or 0.0,
            "summary": getattr(candidate, "summary", "") or payload.get("summary") or payload.get("bio") or "",
            "companies": list(payload.get("companies") or []),
            "projects": list(payload.get("projects") or []),
            "education": list(payload.get("education") or []),
            "certifications": list(payload.get("certifications") or []),
            "domain_experience": list(payload.get("domain_experience") or payload.get("domainExperience") or []),
            "raw_resume_text": payload.get("raw_resume_text") or payload.get("parsed_resume_text") or "",
        }
    )
    return payload


def _candidate_embedding_version(candidate) -> str:
    raw_data = getattr(candidate, "raw_data", None) if candidate is not None else None
    if isinstance(raw_data, dict):
        version = raw_data.get("embedding_version") or raw_data.get("embeddingVersion")
        if isinstance(version, str):
            return version.strip()
    return ""


def _candidate_is_sparse(candidate_payload: dict[str, Any]) -> bool:
    skills = candidate_payload.get("skills") or []
    experience = candidate_payload.get("experience") or candidate_payload.get("experience_level") or candidate_payload.get("experience_required") or candidate_payload.get("years_experience") or ""
    headline = candidate_payload.get("headline") or candidate_payload.get("role") or candidate_payload.get("title") or ""
    return not bool(skills) or not bool(str(experience or "").strip()) or not bool(str(headline or "").strip())


def _extract_candidate_linkedin_url(candidate) -> str:
    raw_data = dict(getattr(candidate, "raw_data", {}) or {})
    for key in (
        "linkedin_url",
        "linkedinUrl",
        "linkedin",
        "profile_url",
        "profileUrl",
        "source_url",
        "sourceUrl",
    ):
        value = raw_data.get(key)
        if isinstance(value, str) and "linkedin.com/in/" in value.lower():
            return value.strip()
    value = getattr(candidate, "linkedin_url", "")
    if isinstance(value, str) and "linkedin.com/in/" in value.lower():
        return value.strip()
    return ""


def _extract_candidate_email(candidate) -> str:
    raw_data = dict(getattr(candidate, "raw_data", {}) or {})
    enrichment = raw_data.get("enrichment") if isinstance(raw_data.get("enrichment"), dict) else {}
    for value in (
        enrichment.get("contactEmail"),
        raw_data.get("contactEmail"),
        raw_data.get("email"),
        raw_data.get("work_email"),
        raw_data.get("personal_email"),
        raw_data.get("candidateEmail"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _queue_outreach_after_enrichment(*, db: Session, candidate) -> dict[str, Any] | None:
    decision = str(getattr(candidate, "decision", "") or "").strip().lower()
    if decision != "selected":
        logger.info(
            "candidate_refresh_outreach_skipped job_id=%s candidate_id=%s reason=not_selected decision=%s",
            getattr(candidate, "job_id", ""),
            getattr(candidate, "candidate_id", ""),
            decision or "missing",
        )
        return None

    raw_data = dict(getattr(candidate, "raw_data", {}) or {})
    enrichment = dict(raw_data.get("enrichment") or {})
    enrichment_status = str(enrichment.get("status") or "").strip().lower()
    real_email = _extract_candidate_email(candidate)
    email_used = real_email or APIFY_ENRICHMENT_TEST_FALLBACK_EMAIL
    email_source = "real" if real_email else "fallback"

    if real_email:
        logger.info(
            "candidate_refresh_enrichment_email_found job_id=%s candidate_id=%s email=%s",
            getattr(candidate, "job_id", ""),
            getattr(candidate, "candidate_id", ""),
            real_email,
        )
    else:
        logger.warning(
            "enrichment_no_email_using_fallback candidate_id=%s email=%s",
            getattr(candidate, "candidate_id", ""),
            email_used,
        )
        enrichment["status"] = "enrichment_no_email"
        enrichment["emailStatus"] = "missing"
        enrichment["shouldOutreach"] = True
        enrichment["contactEmail"] = email_used

    raw_data["enrichment"] = enrichment
    candidate.raw_data = raw_data
    db.flush()

    queue_result = enqueue_job(
        "outreach_send_after_enrichment",
        {
            "job_id": getattr(candidate, "job_id", ""),
            "candidate_id": getattr(candidate, "candidate_id", ""),
            "email": email_used,
            "email_source": email_source,
            "selection_session_id": str(enrichment.get("selectionSessionId") or enrichment.get("selection_session_id") or ""),
            "source_type": str(enrichment.get("sourceType") or "candidate_refresh"),
        },
        idempotency_key=f"outreach-after-enrichment:{getattr(candidate, 'job_id', '')}:{getattr(candidate, 'candidate_id', '')}:{email_used}",
        delay_seconds=1,
    )
    logger.info(
        "candidate_refresh_outreach_queued job_id=%s candidate_id=%s enrichment_status=%s email_source=%s email=%s queue_job_id=%s",
        getattr(candidate, "job_id", ""),
        getattr(candidate, "candidate_id", ""),
        enrichment_status or "missing",
        email_source,
        email_used,
        queue_result.get("job_id") or "",
    )
    return queue_result


def _refresh_candidate_with_apify_timeout(db: Session, candidate, *, timeout_seconds: float = 30.0) -> bool:
    linkedin_url = _extract_candidate_linkedin_url(candidate)
    if not linkedin_url:
        logger.info(
            "candidate_refresh_apify_skipped job_id=%s candidate_id=%s reason=missing_linkedin_url",
            candidate.job_id,
            candidate.candidate_id,
        )
        return False

    result_box: dict[str, Any] = {}
    done = threading.Event()

    def _worker() -> None:
        try:
            with SessionLocal() as enrichment_db:
                result_box["result"] = enrich_candidate_with_apify(
                    db=enrichment_db,
                    job_id=candidate.job_id,
                    candidate_id=candidate.candidate_id,
                    source_type="candidate_refresh",
                    linkedin_url=linkedin_url,
                )
                enrichment_db.commit()
        except Exception as exc:
            result_box["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    finished = done.wait(timeout=max(0.0, float(timeout_seconds)))
    if not finished:
        logger.warning(
            "candidate_refresh_apify_timeout job_id=%s candidate_id=%s timeout_seconds=%s",
            candidate.job_id,
            candidate.candidate_id,
            timeout_seconds,
        )
        return False

    if result_box.get("error"):
        logger.warning(
            "candidate_refresh_apify_failed job_id=%s candidate_id=%s error=%s",
            candidate.job_id,
            candidate.candidate_id,
            str(result_box["error"]),
            exc_info=result_box["error"],
        )
        return False

    try:
        db.refresh(candidate)
    except Exception as exc:
        logger.warning(
            "candidate_refresh_apify_refresh_failed job_id=%s candidate_id=%s error=%s",
            candidate.job_id,
            candidate.candidate_id,
            str(exc),
            exc_info=exc,
        )
        return False

    logger.info(
        "candidate_refresh_apify_completed job_id=%s candidate_id=%s status=%s",
        candidate.job_id,
        candidate.candidate_id,
        (result_box.get("result") or {}).get("status", ""),
    )
    return True


def refresh_candidate(db: Session, candidate) -> bool:
    now = _utcnow()
    embedding_failed = False
    embedding_error = ""
    try:
        with db.begin_nested():
            candidate_payload = _candidate_text_payload(candidate)
            decision = str(getattr(candidate, "decision", "") or candidate_payload.get("decision") or "").strip().lower()
            if decision == "selected":
                if APIFY_TOKEN:
                    _refresh_candidate_with_apify_timeout(db, candidate, timeout_seconds=30.0)
                else:
                    logger.info(
                        "candidate_refresh_enrichment_skipped job_id=%s candidate_id=%s reason=no_apify_token",
                        getattr(candidate, "job_id", ""),
                        getattr(candidate, "candidate_id", ""),
                    )
                candidate_payload = _candidate_text_payload(candidate)
            else:
                logger.info(
                    "candidate_refresh_enrichment_skipped job_id=%s candidate_id=%s reason=not_selected decision=%s",
                    getattr(candidate, "job_id", ""),
                    getattr(candidate, "candidate_id", ""),
                    decision or "missing",
                )
            recruiter_id = JobRepository(db).get_recruiter_id(candidate.job_id)
            normalized_text = build_candidate_text(candidate_payload)
            chunks = chunk_text(normalized_text)
            try:
                vectors = [embed(chunk) for chunk in chunks]
            except Exception as exc:
                embedding_failed = True
                embedding_error = str(exc)
                raise
            active_embedding_version = get_active_embedding_version(db)
            if not vectors:
                logger.info(
                    "candidate_refresh_skipped job_id=%s candidate_id=%s reason=empty_embedding",
                    candidate.job_id,
                    candidate.candidate_id,
                )
                return False

            ensure_all_collections()
            try:
                upsert_candidate_chunks(
                    job_id=candidate.job_id,
                    candidate_id=candidate.candidate_id,
                    vectors=vectors,
                    chunks=chunks,
                    payload={
                        **({"recruiterId": recruiter_id} if recruiter_id else {}),
                        "role": getattr(candidate, "role", "") or "",
                        "summary": getattr(candidate, "summary", "") or "",
                        "name": getattr(candidate, "name", "") or "",
                        "company": getattr(candidate, "company", "") or "",
                        "skills": list(getattr(candidate, "skills", None) or []),
                        "decision": getattr(candidate, "decision", "") or "",
                        "finalScore": float(getattr(candidate, "fit_score", 0.0) or 0.0) / 5.0,
                        "embeddingVersion": active_embedding_version,
                        "lastUpdated": now.isoformat(),
                    },
                )
            except QdrantUnavailableError:
                logger.error(
                    "candidate_refresh_failed vector_store_unavailable candidate_id=%s job_id=%s",
                    candidate.candidate_id,
                    candidate.job_id,
                )
                return False
            candidate.last_refreshed_at = now
            raw_data = dict(getattr(candidate, "raw_data", {}) or {})
            raw_data["embedding_version"] = active_embedding_version
            candidate.raw_data = raw_data
            db.flush()
            logger.info(
                "candidate_refreshed job_id=%s candidate_id=%s embedding_version=%s",
                candidate.job_id,
                candidate.candidate_id,
                active_embedding_version,
            )
            log_metric(
                "candidate_refreshed",
                job_id=candidate.job_id,
                candidate_id=candidate.candidate_id,
                embedding_version=active_embedding_version,
            )
            if decision == "selected":
                _queue_outreach_after_enrichment(db=db, candidate=candidate)
            return True
    except Exception as exc:
        if embedding_failed:
            raw_data = dict(getattr(candidate, "raw_data", {}) or {})
            raw_data["embedding_status"] = "failed"
            raw_data["embedding_error"] = embedding_error or str(exc)
            raw_data["embedding_failed_at"] = now.isoformat()
            candidate.raw_data = raw_data
            candidate.last_refreshed_at = now
            try:
                db.commit()
            except Exception:
                db.rollback()
                raise
            logger.error(
                "candidate_refresh_embedding_failed job_id=%s candidate_id=%s error=%s",
                getattr(candidate, "job_id", ""),
                getattr(candidate, "candidate_id", ""),
                embedding_error or str(exc),
                exc_info=exc,
            )
            raise RuntimeError(embedding_error or "candidate embedding refresh failed") from exc
        else:
            logger.warning(
                "candidate_refresh_failed job_id=%s candidate_id=%s error=%s",
                getattr(candidate, "job_id", ""),
                getattr(candidate, "candidate_id", ""),
                str(exc),
                exc_info=exc,
            )
        return False


def get_stale_candidates(*, db: Session, limit: int = REFRESH_CANDIDATE_LIMIT, stale_days: int = STALE_DAYS):
    stale_before = _utcnow() - timedelta(days=max(1, stale_days))
    active_embedding_version = get_active_embedding_version(db)
    rows = CandidateProfileRepository(db).list_for_migration(limit=max(1, limit) * 5)
    filtered = []
    for row in rows:
        version_mismatch = _candidate_embedding_version(row) != active_embedding_version
        is_stale = (row.last_refreshed_at and row.last_refreshed_at < stale_before) or version_mismatch
        if is_stale:
            if version_mismatch:
                log_metric(
                    "embedding_drift",
                    job_id=getattr(row, "job_id", ""),
                    candidate_id=getattr(row, "candidate_id", ""),
                    active_version=active_embedding_version,
                    candidate_version=_candidate_embedding_version(row),
                )
            filtered.append(row)
    def _priority(row) -> tuple[float, float]:
        refreshed_at = getattr(row, "last_refreshed_at", None)
        if isinstance(refreshed_at, datetime):
            age_days = max(0.0, (_utcnow() - refreshed_at.astimezone(timezone.utc)).total_seconds() / 86400.0)
        else:
            age_days = float(stale_days)
        quality = float(getattr(row, "fit_score", 0.0) or 0.0)
        return (age_days, -quality)

    filtered.sort(key=_priority, reverse=True)
    return filtered[: max(1, limit)]


def refresh_candidates(*, batch_size: int = 100, stale_days: int = STALE_DAYS) -> dict[str, int]:
    logger.info("candidate_refresh_started batch_size=%s stale_days=%s", batch_size, stale_days)
    log_metric("candidate_refresh_started", batch_size=batch_size, stale_days=stale_days)

    processed = 0
    refreshed = 0
    skipped = 0
    active_embedding_version = get_active_embedding_version()

    with SessionLocal() as db:
        try:
            active_embedding_version = get_active_embedding_version(db)
            stale_candidates = get_stale_candidates(db=db, limit=batch_size, stale_days=stale_days)
            for candidate in stale_candidates:
                processed += 1
                try:
                    job = JobRepository(db).get(candidate.job_id)
                    if not job:
                        skipped += 1
                        continue
                    if refresh_candidate(db, candidate):
                        refreshed += 1
                    else:
                        skipped += 1
                except Exception as exc:
                    skipped += 1
                    logger.warning(
                        "candidate_refresh_item_failed job_id=%s candidate_id=%s error=%s",
                        candidate.job_id,
                        candidate.candidate_id,
                        str(exc),
                        exc_info=exc,
                    )
                    db.rollback()
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("candidate_refresh_batch_failed error=%s", str(exc), exc_info=exc)
            raise

    logger.info(
        "candidate_refresh_completed processed=%s refreshed=%s skipped=%s",
        processed,
        refreshed,
        skipped,
    )
    log_metric(
        "candidate_refresh_completed",
        processed=processed,
        refreshed=refreshed,
        skipped=skipped,
        embedding_version=active_embedding_version,
    )
    return {"processed": processed, "refreshed": refreshed, "skipped": skipped}
