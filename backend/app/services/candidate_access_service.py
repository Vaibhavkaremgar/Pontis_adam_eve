"""
candidate_access_service.py
============================
Server-side profile access control for Phase 5.

Access rule:
    candidate_requests.status == "ACCEPTED"
    AND agency_id + job_id + candidate_id all match the authenticated recruiter's scope

Only ACCEPTED requests unlock the full candidate profile.
PENDING and DECLINED keep the profile locked.

Eve integration contract (NOT implemented here):
    Eve will eventually:
      1. Read PENDING requests for a candidate_id
      2. Display: "[Company] is interested in your profile"
      3. Allow: Accept / Decline
      4. Update: candidate_requests.status + responded_at
    Adam then consumes the resulting ACCEPTED/DECLINED state via this service.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import (
    CandidateProfileEntity,
    CandidateRequestEntity,
    CompanyEntity,
    JobEntity,
)
from app.utils.exceptions import APIError


# ── Private field names that must never appear in limited profiles ─────────────
_PRIVATE_FIELDS = frozenset(
    {
        "email",
        "phone",
        "resume_text",
        "resumeText",
        "raw_resume_text",
        "rawResumeText",
        "parsed_resume_text",
        "parsedResumeText",
        "parsed_resume_json",
        "parsedResumeJson",
        "parsed_data",
        "parsedData",
        "raw_data",
        "rawData",
        "contactEmail",
        "contactPhone",
        "linkedin_url",
        "linkedinUrl",
        "github_url",
        "githubUrl",
    }
)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# ── Core access check ──────────────────────────────────────────────────────────

def _get_accepted_request(
    db: Session,
    *,
    candidate_id: str,
    job_id: str,
    agency_id: str,
) -> CandidateRequestEntity | None:
    """Return the ACCEPTED request for this (candidate, job, agency) triple, or None."""
    return db.scalar(
        select(CandidateRequestEntity).where(
            CandidateRequestEntity.candidate_id == candidate_id,
            CandidateRequestEntity.job_id == job_id,
            CandidateRequestEntity.agency_id == agency_id,
            CandidateRequestEntity.status == "ACCEPTED",
        )
    )


def can_view_full_profile(
    db: Session,
    *,
    candidate_id: str,
    job_id: str,
    agency_id: str,
) -> bool:
    """
    Returns True only when candidate_requests.status == ACCEPTED
    for the exact (candidate_id, job_id, agency_id) triple.

    Acceptance is job-scoped: ACCEPTED for Job A does NOT unlock Job B.
    """
    return _get_accepted_request(db, candidate_id=candidate_id, job_id=job_id, agency_id=agency_id) is not None


# ── Scope validation (reused from candidate_request_service pattern) ───────────

def _validate_scope(
    db: Session,
    *,
    job_id: str,
    candidate_id: str,
    agency_id: str,
) -> tuple[JobEntity, CandidateProfileEntity]:
    job = db.get(JobEntity, job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    if _text(job.agency_id or "") != _text(agency_id):
        raise APIError("Forbidden", status_code=403)
    candidate = db.scalar(
        select(CandidateProfileEntity).where(
            CandidateProfileEntity.candidate_id == candidate_id,
            CandidateProfileEntity.agency_id == agency_id,
        ).limit(1)
    )
    if not candidate:
        raise APIError("Candidate not found", status_code=404)
    return job, candidate


# ── Profile serializers ────────────────────────────────────────────────────────

def _limited_profile(row: CandidateProfileEntity, request: CandidateRequestEntity | None) -> dict:
    """Recruiter-safe profile — no private contact/resume data."""
    raw = row.raw_data if isinstance(row.raw_data, dict) else {}
    return {
        "candidate_id": _text(row.candidate_id or row.id),
        "name": _text(row.name),
        "role": _text(row.current_role),
        "company": _text(row.current_company),
        "location": _text(row.location),
        "years_experience": float(row.total_experience_years or 0.0),
        "skills": list(row.skills) if isinstance(row.skills, list) else [],
        "summary": _text(row.summary),
        "profile_access": "LIMITED",
        "request_status": request.status if request else None,
        "recruiter_action": "INTERESTED" if request else "NONE",
        "request_id": str(request.id) if request else None,
        "responded_at": _iso(request.responded_at) if request else None,
    }


def _full_profile(
    row: CandidateProfileEntity,
    request: CandidateRequestEntity,
    agency: CompanyEntity | None,
) -> dict:
    """
    Full authorized profile — returned only when request.status == ACCEPTED.
    Exposes contact information, resume, and all available profile fields.
    """
    raw = row.raw_data if isinstance(row.raw_data, dict) else {}
    parsed = row.parsed_resume_json if isinstance(row.parsed_resume_json, dict) else {}

    # Resolve contact fields from multiple possible locations
    email = (
        _text(row.email)
        or _text(raw.get("work_email"))
        or _text(raw.get("email"))
        or _text(raw.get("personal_email"))
        or ""
    )
    phone = _text(row.phone) or _text(raw.get("phone")) or _text(raw.get("mobile")) or ""
    linkedin_url = _text(row.linkedin_url) or _text(raw.get("linkedin_url")) or ""
    github_url = _text(row.github_url) or _text(raw.get("github_url")) or ""

    # Work experience from parsed resume or raw_data
    work_experience = (
        parsed.get("work_experience")
        or raw.get("work_experience")
        or raw.get("experience")
        or []
    )
    education = (
        list(row.education) if isinstance(row.education, list)
        else parsed.get("education") or raw.get("education") or []
    )

    return {
        "candidate_id": _text(row.candidate_id or row.id),
        "name": _text(row.name),
        "role": _text(row.current_role),
        "company": _text(row.current_company),
        "location": _text(row.location),
        "years_experience": float(row.total_experience_years or 0.0),
        "skills": list(row.skills) if isinstance(row.skills, list) else [],
        "summary": _text(row.summary),
        # Contact — unlocked after acceptance
        "email": email,
        "phone": phone,
        "linkedin_url": linkedin_url,
        "github_url": github_url,
        # Resume
        "resume_text": _text(row.resume_text or row.parsed_resume_text),
        # Structured profile
        "work_experience": work_experience,
        "education": education,
        "certifications": list(parsed.get("certifications") or raw.get("certifications") or []),
        "projects": list(parsed.get("projects") or raw.get("projects") or []),
        # Access metadata
        "profile_access": "FULL",
        "request_status": request.status,
        "recruiter_action": "INTERESTED",
        "request_id": str(request.id),
        "responded_at": _iso(request.responded_at),
        # Company context for future Eve notification
        "agency_name": _text(agency.name) if agency else "",
    }


# ── Public API ─────────────────────────────────────────────────────────────────

def get_candidate_profile(
    db: Session,
    *,
    candidate_id: str,
    job_id: str,
    agency_id: str,
) -> dict:
    """
    Returns the appropriate profile based on request status:
      ACCEPTED  → full profile (contact, resume, work experience, education)
      PENDING   → limited profile (role, company, location, skills, summary)
      DECLINED  → limited profile
      NONE      → limited profile

    Raises APIError(403) if agency scope is violated.
    Raises APIError(404) if job or candidate not found.
    """
    job, candidate = _validate_scope(db, job_id=job_id, candidate_id=candidate_id, agency_id=agency_id)

    request = db.scalar(
        select(CandidateRequestEntity).where(
            CandidateRequestEntity.candidate_id == candidate_id,
            CandidateRequestEntity.job_id == job_id,
            CandidateRequestEntity.agency_id == agency_id,
        )
    )

    if request and request.status == "ACCEPTED":
        agency = db.get(CompanyEntity, agency_id)
        return _full_profile(candidate, request, agency)

    return _limited_profile(candidate, request)


def get_accepted_candidates(
    db: Session,
    *,
    job_id: str,
    agency_id: str,
) -> list[dict]:
    """
    Returns all candidates with ACCEPTED requests for a job.
    Used to populate the Accepted section in the Results/Review UI.
    """
    job = db.get(JobEntity, job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    if _text(job.agency_id or "") != _text(agency_id):
        raise APIError("Forbidden", status_code=403)

    accepted_requests = db.scalars(
        select(CandidateRequestEntity).where(
            CandidateRequestEntity.job_id == job_id,
            CandidateRequestEntity.agency_id == agency_id,
            CandidateRequestEntity.status == "ACCEPTED",
        )
    ).all()

    if not accepted_requests:
        return []

    agency = db.get(CompanyEntity, agency_id)
    results = []
    for req in accepted_requests:
        candidate = db.scalar(
            select(CandidateProfileEntity).where(
                CandidateProfileEntity.candidate_id == req.candidate_id,
                CandidateProfileEntity.agency_id == agency_id,
            ).limit(1)
        )
        if candidate:
            results.append(_full_profile(candidate, req, agency))
    return results


def get_pending_candidates(
    db: Session,
    *,
    job_id: str,
    agency_id: str,
) -> list[dict]:
    """
    Returns all candidates with PENDING requests for a job.
    Used to populate the 'To Be Accepted' section.
    """
    job = db.get(JobEntity, job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    if _text(job.agency_id or "") != _text(agency_id):
        raise APIError("Forbidden", status_code=403)

    pending_requests = db.scalars(
        select(CandidateRequestEntity).where(
            CandidateRequestEntity.job_id == job_id,
            CandidateRequestEntity.agency_id == agency_id,
            CandidateRequestEntity.status == "PENDING",
        )
    ).all()

    results = []
    for req in pending_requests:
        candidate = db.scalar(
            select(CandidateProfileEntity).where(
                CandidateProfileEntity.candidate_id == req.candidate_id,
                CandidateProfileEntity.agency_id == agency_id,
            ).limit(1)
        )
        if candidate:
            results.append(_limited_profile(candidate, req))
    return results
