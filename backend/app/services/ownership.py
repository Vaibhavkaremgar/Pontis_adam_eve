from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.repositories import JobRepository
from app.utils.exceptions import APIError


def assert_job_ownership(*, db: Session, job_id: str, user_id: str) -> None:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    recruiter_id = JobRepository(db).get_recruiter_id(job_id)
    if recruiter_id and recruiter_id != user_id:
        raise APIError("Forbidden", status_code=403)
