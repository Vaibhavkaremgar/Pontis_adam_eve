from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import OUTREACH_FOLLOWUP_MAX_ATTEMPTS
from app.db.repositories import CandidateProfileRepository, InterviewRepository, JobRepository, OutreachEventRepository
from app.services.recruiter_preference_service import update_recruiter_preferences

logger = logging.getLogger(__name__)

_MAX_BATCH_SIZE = 100
_ALREADY_SHORTLISTED_STATUSES = {
    "shortlisted",
    "contacted",
    "interview_scheduled",
    "booked",
    "interviewed",
    "onsite",
    "final_round",
    "exported",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_already_progressed(*, db: Session, job_id: str, candidate_id: str) -> bool:
    interview = InterviewRepository(db).get_by_job_and_candidate(job_id, candidate_id)
    if not interview:
        return False
    return (interview.status or "").strip().lower() in _ALREADY_SHORTLISTED_STATUSES


def _learning_snapshot(*, db: Session, job_id: str, candidate_id: str) -> tuple[Any | None, Any | None, str | None]:
    job = JobRepository(db).get(job_id)
    if not job:
        return None, None, None
    recruiter_id = JobRepository(db).get_recruiter_id(job_id)
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    return job, profile, recruiter_id


def run_outreach_learning_cycle(
    db: Session,
    *,
    batch_limit: int = _MAX_BATCH_SIZE,
    max_follow_up_count: int = OUTREACH_FOLLOWUP_MAX_ATTEMPTS,
) -> dict[str, int]:
    """
    Apply weak negative learning for silent candidates after follow-up exhaustion.
    This is idempotent because rows are selected with learning_applied = false and
    then marked once handled.
    """
    now = _utcnow()
    outreach_repo = OutreachEventRepository(db)

    processed = applied = skipped = 0
    with db.begin():
        rows = outreach_repo.list_stale_for_learning_locked(
            now=now,
            max_follow_up_count=max_follow_up_count,
            limit=max(1, min(int(batch_limit), _MAX_BATCH_SIZE)),
        )
        if not rows:
            logger.info("outreach_learning_cycle_empty")
            return {"processed": 0, "applied": 0, "skipped": 0}

        logger.info(
            "outreach_learning_cycle_started batch=%s max_follow_up_count=%s",
            len(rows),
            max_follow_up_count,
        )

        for event in rows:
            processed += 1
            try:
                with db.begin_nested():
                    if (event.responded_at is not None) or (event.status or "").strip().lower() not in {"sent", "delivered"}:
                        skipped += 1
                        event.learning_applied = True
                        continue

                    if _is_already_progressed(db=db, job_id=event.job_id, candidate_id=event.candidate_id):
                        skipped += 1
                        event.learning_applied = True
                        logger.info(
                            "outreach_learning_skipped job_id=%s candidate_id=%s reason=already_progressed",
                            event.job_id,
                            event.candidate_id,
                        )
                        continue

                    job, profile, recruiter_id = _learning_snapshot(db=db, job_id=event.job_id, candidate_id=event.candidate_id)
                    if not job or not profile or not recruiter_id:
                        skipped += 1
                        event.learning_applied = True
                        logger.info(
                            "outreach_learning_skipped job_id=%s candidate_id=%s reason=missing_context",
                            event.job_id,
                            event.candidate_id,
                        )
                        continue

                    update_recruiter_preferences(
                        db,
                        recruiter_id,
                        selected_candidate=None,
                        rejected_candidates=[profile],
                        signal_multiplier=0.2,
                    )
                    event.learning_applied = True
                    applied += 1
                    logger.info(
                        "outreach_learning_applied job_id=%s candidate_id=%s recruiter_id=%s follow_up_count=%s",
                        event.job_id,
                        event.candidate_id,
                        recruiter_id,
                        event.follow_up_count,
                    )
            except Exception as exc:
                skipped += 1
                logger.warning(
                    "outreach_learning_failed job_id=%s candidate_id=%s error=%s",
                    event.job_id,
                    event.candidate_id,
                    str(exc),
                    exc_info=exc,
                )

    logger.info(
        "outreach_learning_cycle_complete processed=%s applied=%s skipped=%s",
        processed,
        applied,
        skipped,
    )
    return {"processed": processed, "applied": applied, "skipped": skipped}
