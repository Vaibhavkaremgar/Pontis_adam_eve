from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import REFRESH_CANDIDATE_LIMIT, STALE_DAYS
from app.db.repositories import CandidateProfileRepository, JobRepository
from app.db.session import SessionLocal
from app.services.embedding_registry_service import get_active_embedding_version
from app.services.enrichment_service import enrich_candidate as run_candidate_enrichment
from app.services.candidate_text import build_candidate_text
from app.services.embedding_service import embed
from app.services.metrics_service import log_metric
from app.services.qdrant_service import ensure_all_collections, upsert_candidate_chunks
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
            "skills": list(getattr(candidate, "skills", None) or payload.get("skills") or payload.get("skills_required") or []),
            "experience": payload.get("experience") or payload.get("experience_level") or payload.get("years_experience") or "",
            "summary": getattr(candidate, "summary", "") or payload.get("summary") or payload.get("bio") or "",
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


def refresh_candidate(db: Session, candidate) -> bool:
    now = _utcnow()
    try:
        with db.begin_nested():
            run_candidate_enrichment(candidate)
            recruiter_id = JobRepository(db).get_recruiter_id(candidate.job_id)
            candidate_payload = _candidate_text_payload(candidate)
            normalized_text = build_candidate_text(candidate_payload)
            chunks = chunk_text(normalized_text)
            vectors = [embed(chunk) for chunk in chunks]
            active_embedding_version = get_active_embedding_version(db)
            if not vectors:
                logger.info(
                    "candidate_refresh_skipped job_id=%s candidate_id=%s reason=empty_embedding",
                    candidate.job_id,
                    candidate.candidate_id,
                )
                return False

            ensure_all_collections()
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
            return True
    except Exception as exc:
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
