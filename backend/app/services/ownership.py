from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.security import is_super_admin_role
from app.db.repositories import JobRepository
from app.models.entities import UserEntity
from app.utils.exceptions import APIError


def _is_super_admin(*, db: Session, user_id: str) -> bool:
    user = db.get(UserEntity, user_id)
    return bool(user and is_super_admin_role(getattr(user, "role", "")))


def resolve_company_id_for_user(*, db: Session, user_id: str) -> str:
    if _is_super_admin(db=db, user_id=user_id):
        return ""
    user = db.get(UserEntity, user_id)
    if not user:
        raise APIError("User not found", status_code=404)
    agency_id = str(getattr(user, "agency_id", "") or "").strip()
    if not agency_id:
        raise APIError("Company not found", status_code=404)
    return agency_id


def assert_job_ownership(*, db: Session, job_id: str, user_id: str) -> None:
    if _is_super_admin(db=db, user_id=user_id):
        return
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    recruiter_id = JobRepository(db).get_recruiter_id(job_id)
    if not recruiter_id or recruiter_id != user_id:
        raise APIError("Forbidden", status_code=403)


def assert_job_company_ownership(*, db: Session, job_id: str, user_id: str) -> None:
    if _is_super_admin(db=db, user_id=user_id):
        return
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    company_id = resolve_company_id_for_user(db=db, user_id=user_id)
    if str(getattr(job, "company_id", "") or "").strip() != company_id:
        raise APIError("Forbidden", status_code=403)
