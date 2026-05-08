from __future__ import annotations

from sqlalchemy.orm import Session


def apply_feedback(*, db: Session, job_id: str, candidate_id: str, action: str) -> dict:
    from app.services.candidate_service import apply_feedback as _apply_feedback

    return _apply_feedback(db=db, job_id=job_id, candidate_id=candidate_id, action=action)

