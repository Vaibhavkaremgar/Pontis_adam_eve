from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.repositories import CandidateProfileRepository, InterviewRepository, JobRepository
from app.schemas.candidate import InterviewItem
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)


def list_interviews(*, db: Session, job_id: str) -> list[InterviewItem]:
    jobs = JobRepository(db)
    if not jobs.get(job_id):
        raise APIError("Job not found", status_code=404)

    interviews = InterviewRepository(db).list_for_job(job_id)
    profiles = {row.candidate_id: row for row in CandidateProfileRepository(db).list_for_job(job_id)}
    items: list[InterviewItem] = []
    for row in interviews:
        profile = profiles.get(row.candidate_id)
        if not profile:
            logger.warning(
                "invalid_candidate_reference_detected table=interviews job_id=%s candidate_id=%s",
                job_id,
                row.candidate_id,
            )
        items.append(
            InterviewItem(
                candidateId=row.candidate_id,
                name=profile.name if profile else "",
                status=row.status,
            )
        )
    return items
