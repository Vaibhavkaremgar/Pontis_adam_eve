from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from sqlalchemy import and_, case, or_, select
from sqlalchemy.orm import Session

from app.core.config import EMBEDDING_VERSION, INTERNAL_CANDIDATE_EMBEDDING_BATCH_SIZE, INTERNAL_CANDIDATE_EMBEDDING_RETRIES
from app.models.entities import CandidateProfileEntity
from app.services.embedding_service import embed_many
from app.services.qdrant_service import (
    QdrantUnavailableError,
    delete_internal_candidate_vectors,
    internal_candidate_vector_exists,
    upsert_internal_candidate_embeddings,
)
from app.services.candidate_text import build_structured_candidate_text

logger = logging.getLogger(__name__)


def _resume_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _usable_resume_text(row: CandidateProfileEntity) -> str:
    return build_structured_candidate_text(row)


def _embed_with_retries(text: str) -> list[float]:
    attempts = max(1, int(INTERNAL_CANDIDATE_EMBEDDING_RETRIES or 1))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return embed_many([text])[0]
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                sleep_seconds = min(2.0 * attempt, 6.0)
                logger.warning(
                    "internal_candidate_embedding_retry attempt=%s/%s sleep_seconds=%.2f error=%s",
                    attempt,
                    attempts,
                    sleep_seconds,
                    str(exc),
                )
                time.sleep(sleep_seconds)
    assert last_error is not None
    raise last_error


def index_candidate_embedding(*, db: Session, candidate_record_id: str) -> dict[str, Any]:
    """Index one candidates-table row; safe to repeat for the same text/version."""
    started = perf_counter()
    row = db.get(CandidateProfileEntity, str(candidate_record_id))
    if not row:
        return {"status": "not_found", "candidateRecordId": str(candidate_record_id)}
    resume_text = _usable_resume_text(row)
    if not resume_text:
        return {"status": "skipped", "reason": "missing_searchable_profile", "candidateRecordId": str(row.id)}

    text_hash = _resume_hash(resume_text)
    if (
        getattr(row, "embedding_status", None) == "EMBEDDED"
        and getattr(row, "embedding_version", None) == EMBEDDING_VERSION
        and getattr(row, "embedding_text_hash", None) == text_hash
        and internal_candidate_vector_exists(candidate_record_id=str(row.id)) is not False
    ):
        logger.info("candidate_embedding_index candidate_record_id=%s agency_id=%s embedding_version=%s text_hash=%s status=skipped duration_ms=%.1f", row.id, row.agency_id or "", EMBEDDING_VERSION, text_hash, (perf_counter() - started) * 1000)
        return {"status": "already_indexed", "candidateRecordId": str(row.id), "candidateId": str(row.candidate_id or "")}

    row.embedding_status = "PROCESSING"
    db.flush()
    try:
        vector = _embed_with_retries(resume_text)
        delete_internal_candidate_vectors(candidate_record_id=str(row.id))
        indexed_at = datetime.now(timezone.utc)
        raw_data = dict(row.raw_data or {}) if isinstance(row.raw_data, dict) else {}
        upsert_internal_candidate_embeddings([{
            "candidateRecordId": str(row.id),
            "candidateId": str(row.candidate_id or row.id),
            "agencyId": str(row.agency_id or ""),
            "embeddingVersion": EMBEDDING_VERSION,
            "textHash": text_hash,
            "indexedAt": indexed_at.isoformat(),
            "resumeFingerprint": raw_data.get("resumeFingerprint") or raw_data.get("resume_fingerprint") or "",
            "vector": vector,
        }])
    except Exception as exc:
        row.embedding_status = "FAILED"
        db.flush()
        logger.error("candidate_embedding_index_failed candidate_record_id=%s agency_id=%s error=%s", row.id, row.agency_id or "", str(exc), exc_info=exc)
        raise

    row.embedding_status = "EMBEDDED"
    row.embedding_version = EMBEDDING_VERSION
    row.embedding_text_hash = text_hash
    row.embedding_indexed_at = indexed_at
    # Remove the legacy shadow state when encountered; explicit columns are authoritative.
    raw_data = dict(row.raw_data or {}) if isinstance(row.raw_data, dict) else {}
    if "semanticEmbedding" in raw_data:
        raw_data.pop("semanticEmbedding", None)
        row.raw_data = raw_data
    db.flush()
    logger.info("candidate_embedding_index candidate_record_id=%s agency_id=%s embedding_version=%s text_hash=%s status=indexed duration_ms=%.1f", row.id, row.agency_id or "", EMBEDDING_VERSION, text_hash, (perf_counter() - started) * 1000)
    return {"status": "indexed", "candidateRecordId": str(row.id), "candidateId": str(row.candidate_id or "")}


def bulk_index_candidate_embeddings(
    *, db: Session, agency_id: str | None = None, batch_size: int = INTERNAL_CANDIDATE_EMBEDDING_BATCH_SIZE
) -> dict[str, int]:
    """Backfill candidates in bounded batches and report resumable progress."""
    query = select(CandidateProfileEntity).where(or_(
        CandidateProfileEntity.resume_text.is_not(None),
        CandidateProfileEntity.parsed_resume_text.is_not(None),
        CandidateProfileEntity.current_role.is_not(None),
        CandidateProfileEntity.summary.is_not(None),
        CandidateProfileEntity.skills.is_not(None),
        CandidateProfileEntity.work_experience.is_not(None),
        CandidateProfileEntity.parsed_resume_json.is_not(None),
    )).order_by(CandidateProfileEntity.id)
    if agency_id:
        query = query.where(CandidateProfileEntity.agency_id == str(agency_id))

    processed = skipped = failed = 0
    offset = 0
    resolved_batch_size = max(1, int(batch_size or 1))
    while True:
        rows = list(db.scalars(query.offset(offset).limit(resolved_batch_size)).all())
        if not rows:
            break
        offset += len(rows)
        for row in rows:
            try:
                result = index_candidate_embedding(db=db, candidate_record_id=str(row.id))
                if result["status"] in {"indexed", "already_indexed"}:
                    processed += 1
                else:
                    skipped += 1
            except Exception as exc:
                db.rollback()
                failed += 1
                logger.warning("internal_candidate_embedding_failed candidate_record_id=%s error=%s", row.id, str(exc), exc_info=exc)
        db.commit()
        logger.info("internal_candidate_embedding_batch_progress offset=%s processed=%s skipped=%s failed=%s", offset, processed, skipped, failed)
    return {"processed": processed, "skipped": skipped, "failed": failed}


def enqueue_stale_candidate_embedding_jobs(
    *, db: Session, limit: int = INTERNAL_CANDIDATE_EMBEDDING_BATCH_SIZE,
) -> dict[str, int]:
    """Adam-owned poller for candidates written directly to shared PostgreSQL."""
    has_profile = or_(
        CandidateProfileEntity.resume_text.is_not(None),
        CandidateProfileEntity.parsed_resume_text.is_not(None),
        CandidateProfileEntity.current_role.is_not(None),
        CandidateProfileEntity.summary.is_not(None),
        CandidateProfileEntity.skills.is_not(None),
        CandidateProfileEntity.work_experience.is_not(None),
        CandidateProfileEntity.parsed_resume_json.is_not(None),
    )
    stale_state = or_(
        CandidateProfileEntity.embedding_status.is_(None),
        CandidateProfileEntity.embedding_status != "EMBEDDED",
        CandidateProfileEntity.embedding_version != EMBEDDING_VERSION,
        CandidateProfileEntity.embedding_indexed_at.is_(None),
        and_(
            CandidateProfileEntity.updated_at.is_not(None),
            CandidateProfileEntity.embedding_indexed_at.is_not(None),
            CandidateProfileEntity.updated_at > CandidateProfileEntity.embedding_indexed_at,
        ),
    )
    query = select(CandidateProfileEntity).where(has_profile).order_by(
        case((stale_state, 0), else_=1),
        CandidateProfileEntity.updated_at.desc().nullslast(),
        CandidateProfileEntity.id,
    ).limit(max(1, int(limit or 1)))
    queued = skipped = 0
    from app.services.job_queue_service import enqueue_job
    for row in db.scalars(query).all():
        text = _usable_resume_text(row)
        if not text:
            skipped += 1
            continue
        text_hash = _resume_hash(text)
        existing_is_current = (
            getattr(row, "embedding_status", None) == "EMBEDDED"
            and getattr(row, "embedding_version", None) == EMBEDDING_VERSION
            and getattr(row, "embedding_text_hash", None) == text_hash
        )
        vector_exists = None
        if existing_is_current:
            vector_exists = internal_candidate_vector_exists(candidate_record_id=str(row.id))
        if existing_is_current and vector_exists is not False:
            skipped += 1
            continue
        enqueue_job(
            "candidate_embedding_index",
            {"candidate_record_id": str(row.id)},
            idempotency_key=f"candidate-embedding:{row.id}:{EMBEDDING_VERSION}:{text_hash}",
        )
        queued += 1
    logger.info("candidate_embedding_detection_complete scanned=%s queued=%s skipped=%s", queued + skipped, queued, skipped)
    return {"queued": queued, "skipped": skipped}
