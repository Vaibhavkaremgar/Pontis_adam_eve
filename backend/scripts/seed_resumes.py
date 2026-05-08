from __future__ import annotations

import logging
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal
from app.services.metrics_service import log_metric
from app.services.resume_ingestion_service import ingest_resume_directory, validate_ingestion_state

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("pontis.seed_resumes")


def main() -> int:
    resumes_dir = ROOT / "resumes"
    started = perf_counter()
    logger.info("resume_seed_started directory=%s", resumes_dir)

    if not resumes_dir.exists():
        logger.error("resume_seed_directory_missing directory=%s", resumes_dir)
        return 1

    with SessionLocal() as db:
        result = ingest_resume_directory(db, resumes_dir)
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
        validation = validate_ingestion_state(db)

    duration = perf_counter() - started
    logger.info(
        "resume_seed_completed processed=%s failed=%s duplicates=%s postgres_count=%s qdrant_count=%s duration_seconds=%.2f",
        result["processed"],
        result["failed"],
        result["duplicates"],
        validation["postgres_count"],
        validation["qdrant_count"],
        duration,
    )
    log_metric(
        "resume_seed_completed",
        processed=result["processed"],
        failed=result["failed"],
        duplicates=result["duplicates"],
        postgres_count=validation["postgres_count"],
        qdrant_count=validation["qdrant_count"],
        duration_seconds=round(duration, 4),
    )
    return 0 if result["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
