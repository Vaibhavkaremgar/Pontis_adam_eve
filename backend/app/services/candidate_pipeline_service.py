from __future__ import annotations

from sqlalchemy.orm import Session


def refresh_candidates_for_job(*, db: Session, job_id: str, mode: str | None = None, refresh: bool = False) -> int:
    from app.services.candidate_service import refresh_candidates_for_job as _refresh_candidates_for_job

    return _refresh_candidates_for_job(db=db, job_id=job_id, mode=mode, refresh=refresh)

