from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.repositories import InterviewRepository, JobRepository
from app.schemas.candidate import InterviewItem
from app.utils.exceptions import APIError


def list_interviews(*, db: Session, job_id: str) -> list[InterviewItem]:
    jobs = JobRepository(db)
    if not jobs.get(job_id):
        raise APIError("Job not found", status_code=404)

    interviews = InterviewRepository(db).list_for_job(job_id)
    return [InterviewItem(candidateId=row.candidate_id, status=row.status) for row in interviews]

