from __future__ import annotations

import logging
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import EMBEDDING_VERSION  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.entities import CandidateProfileEntity  # noqa: E402
from app.services.candidate_text import build_candidate_text  # noqa: E402
from app.services.embedding_service import embed  # noqa: E402
from app.services.qdrant_service import ensure_all_collections, upsert_candidate_chunks  # noqa: E402
from app.utils.text import chunk_text  # noqa: E402

logger = logging.getLogger(__name__)
BATCH_SIZE = 250


def _candidate_experience(raw_data: dict | None) -> str:
    if not isinstance(raw_data, dict):
        return ""
    for key in ("experience", "experience_level", "years_experience", "experience_summary"):
        value = raw_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _build_candidate_payload(row: CandidateProfileEntity) -> dict:
    raw_data = row.raw_data if isinstance(row.raw_data, dict) else {}
    skills = row.skills or raw_data.get("skills") or raw_data.get("skills_required") or []
    candidate_input = {
        "role": row.role or raw_data.get("role") or raw_data.get("title") or "",
        "skills": skills,
        "experience": _candidate_experience(raw_data),
        "summary": row.summary or raw_data.get("summary") or raw_data.get("bio") or "",
    }
    text = build_candidate_text(candidate_input)
    chunks = chunk_text(text)
    vectors = [embed(chunk) for chunk in chunks]

    payload = {
        "role": row.role,
        "summary": row.summary,
        "name": row.name,
        "company": row.company,
        "skills": row.skills or [],
        "decision": row.decision,
        "finalScore": row.fit_score / 5.0,
        "embeddingVersion": EMBEDDING_VERSION,
        "dedupeKey": row.candidate_id,
    }
    return {
        "text": text,
        "chunks": chunks,
        "vectors": vectors,
        "payload": payload,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("backfill_started version=%s batch_size=%s", EMBEDDING_VERSION, BATCH_SIZE)

    ensure_all_collections()

    session = SessionLocal()
    processed = 0
    failed = 0
    try:
        job_ids = [
            row[0]
            for row in session.execute(
                select(CandidateProfileEntity.job_id).distinct().order_by(CandidateProfileEntity.job_id)
            ).all()
        ]
        total_jobs = len(job_ids)
        logger.info("backfill_scope total_jobs=%s", total_jobs)

        for job_index, job_id in enumerate(job_ids, start=1):
            rows = session.scalars(
                select(CandidateProfileEntity)
                .where(CandidateProfileEntity.job_id == job_id)
                .order_by(CandidateProfileEntity.last_scored_at.desc(), CandidateProfileEntity.candidate_id.asc())
            ).all()
            if not rows:
                continue

            logger.info(
                "backfill_job_started job_id=%s job_index=%s total_jobs=%s candidates=%s",
                job_id,
                job_index,
                total_jobs,
                len(rows),
            )

            for start in range(0, len(rows), BATCH_SIZE):
                batch = rows[start : start + BATCH_SIZE]
                logger.info(
                    "backfill_batch_started job_id=%s batch_start=%s batch_size=%s",
                    job_id,
                    start,
                    len(batch),
                )
                for row in batch:
                    try:
                        candidate_data = _build_candidate_payload(row)
                        upsert_candidate_chunks(
                            job_id=row.job_id,
                            candidate_id=row.candidate_id,
                            vectors=candidate_data["vectors"],
                            chunks=candidate_data["chunks"],
                            payload={
                                **candidate_data["payload"],
                                "embeddingVersion": EMBEDDING_VERSION,
                            },
                        )
                        processed += 1
                        logger.info(
                            "candidate_embedded job_id=%s candidate_id=%s version=%s chunks=%s",
                            row.job_id,
                            row.candidate_id,
                            EMBEDDING_VERSION,
                            len(candidate_data["chunks"]),
                        )
                    except Exception as exc:
                        failed += 1
                        logger.warning(
                            "candidate_backfill_failed job_id=%s candidate_id=%s error=%s",
                            row.job_id,
                            row.candidate_id,
                            exc,
                            exc_info=True,
                        )

        logger.info(
            "backfill_completed processed=%s failed=%s version=%s",
            processed,
            failed,
            EMBEDDING_VERSION,
        )
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
