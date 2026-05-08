from __future__ import annotations

from sqlalchemy.orm import Session


def fetch_ranked_candidates(*, db: Session, job_id: str, mode: str | None = None, refresh: bool = False, debug: bool = False):
    from app.services.candidate_service import fetch_ranked_candidates as _fetch_ranked_candidates

    return _fetch_ranked_candidates(db=db, job_id=job_id, mode=mode, refresh=refresh, debug=debug)


def warm_candidate_retrieval() -> int:
    from app.services.candidate_service import warm_candidate_retrieval as _warm_candidate_retrieval

    return _warm_candidate_retrieval()

