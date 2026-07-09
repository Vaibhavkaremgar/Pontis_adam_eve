from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.repositories import CompanyRepository, JobRepository
from app.utils.exceptions import APIError


def resolve_company_id_for_user(*, db: Session, user_id: str) -> str:
    company = CompanyRepository(db).get_latest_for_user(user_id=user_id)
    if not company:
        raise APIError("Company not found", status_code=404)
    return str(company.id or "").strip()


def assert_job_ownership(*, db: Session, job_id: str, user_id: str) -> None:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    recruiter_id = JobRepository(db).get_recruiter_id(job_id)
    if not recruiter_id or recruiter_id != user_id:
        raise APIError("Forbidden", status_code=403)


def assert_job_company_ownership(*, db: Session, job_id: str, user_id: str) -> None:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    company_id = resolve_company_id_for_user(db=db, user_id=user_id)
    if str(getattr(job, "company_id", "") or "").strip() != company_id:
        raise APIError("Forbidden", status_code=403)
