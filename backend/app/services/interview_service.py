from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.repositories import CandidateProfileRepository, InterviewRepository, JobRepository
from app.schemas.candidate import InterviewItem
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)


def _candidate_display_name(profile) -> str:
    if not profile:
        return ""
    name = str(getattr(profile, "name", "") or "").strip()
    if name:
        return name
    raw_data = getattr(profile, "raw_data", None)
    if isinstance(raw_data, dict):
        for key in ("full_name", "fullName", "name", "candidate_name", "candidateName"):
            value = str(raw_data.get(key) or "").strip()
            if value:
                return value
    candidate_id = str(getattr(profile, "candidate_id", "") or "").strip()
    return candidate_id or ""


def list_interviews(*, db: Session, job_id: str, company_id: str) -> list[InterviewItem]:
    jobs = JobRepository(db)
    if not jobs.get(job_id):
        raise APIError("Job not found", status_code=404)
    job = jobs.get(job_id)
    if str(getattr(job, "company_id", "") or "").strip() != str(company_id or "").strip():
        raise APIError("Forbidden", status_code=403)

    interviews = InterviewRepository(db).list_for_job(job_id)
    profiles = {str(row.candidate_id): row for row in CandidateProfileRepository(db).list_for_job(job_id)}
    items: list[InterviewItem] = []
    for row in interviews:
        candidate_id = str(row.candidate_id) if row.candidate_id else ""
        profile = profiles.get(candidate_id)
        if not profile:
            logger.warning(
                "invalid_candidate_reference_detected table=interviews job_id=%s candidate_id=%s",
                job_id,
                candidate_id,
            )
        items.append(
            InterviewItem(
                candidateId=candidate_id,
                name=_candidate_display_name(profile),
                status=row.status,
            )
        )
    return items
