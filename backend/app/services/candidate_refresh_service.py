from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import EMBEDDING_VERSION, REFRESH_CANDIDATE_LIMIT, STALE_DAYS
from app.db.repositories import CandidateProfileRepository, JobRepository
from app.db.session import SessionLocal
from app.services.enrichment_service import enrich_candidate
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


def enrich_candidate(candidate) -> None:
    """Future enrichment hook for LinkedIn/GitHub/other external refreshes."""
    return None


def refresh_candidate(db: Session, candidate) -> bool:
    now = _utcnow()
    try:
        with db.begin_nested():
            enrich_candidate(candidate)
            recruiter_id = JobRepository(db).get_recruiter_id(candidate.job_id)
            candidate_payload = _candidate_text_payload(candidate)
            normalized_text = build_candidate_text(candidate_payload)
            chunks = chunk_text(normalized_text)
            vectors = [embed(chunk) for chunk in chunks]
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
                    "embeddingVersion": EMBEDDING_VERSION,
                    "lastUpdated": now.isoformat(),
                },
            )
            candidate.last_refreshed_at = now
            db.flush()
            logger.info(
                "candidate_refreshed job_id=%s candidate_id=%s embedding_version=%s",
                candidate.job_id,
                candidate.candidate_id,
                EMBEDDING_VERSION,
            )
            log_metric(
                "candidate_refreshed",
                job_id=candidate.job_id,
                candidate_id=candidate.candidate_id,
                embedding_version=EMBEDDING_VERSION,
            )
            enrich_candidate(candidate)
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
    return CandidateProfileRepository(db).list_stale(limit=max(1, limit), stale_before=stale_before)


def refresh_candidates(*, batch_size: int = 100, stale_days: int = STALE_DAYS) -> dict[str, int]:
    logger.info("candidate_refresh_started batch_size=%s stale_days=%s", batch_size, stale_days)
    log_metric("candidate_refresh_started", batch_size=batch_size, stale_days=stale_days)

    processed = 0
    refreshed = 0
    skipped = 0

    with SessionLocal() as db:
        try:
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
    )
    return {"processed": processed, "refreshed": refreshed, "skipped": skipped}
