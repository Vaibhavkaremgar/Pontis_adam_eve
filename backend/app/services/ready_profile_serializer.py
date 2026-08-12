"""
ready_profile_serializer.py
============================
Recruiter-safe serialization layer for Ready candidates.

STRICT PRIVACY CONTRACT:
  - email, phone, mobile, raw_data, parsed_resume_json, resume_text are NEVER returned.
  - Only structured, non-contact profile fields are exposed.
  - This serializer is the ONLY path for Ready candidate data to reach the frontend.

Ready states covered: TO_BE_ACCEPTED, ACCEPTED, TO_BE_INTERVIEWED
"""
from __future__ import annotations

import re
from typing import Any

from app.models.entities import CandidateProfileEntity, CandidateRequestEntity

# Fields that must NEVER appear in any Ready profile response
_BLOCKED_FIELDS = frozenset({
    "email", "phone", "mobile",
    "raw_data", "rawData",
    "parsed_resume_json", "parsedResumeJson",
    "resume_text", "resumeText",
    "parsed_resume_text", "parsedResumeText",
    "raw_resume_text", "rawResumeText",
    "linkedin_url", "linkedinUrl",
    "github_url", "githubUrl",
    "contactEmail", "contactPhone",
})


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _safe_list(value: Any) -> list:
    if isinstance(value, list):
        return list(value)
    return []


def _safe_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _sanitize_work_experience(work_exp: Any) -> list[dict]:
    """Return work experience entries stripped of any contact fields."""
    entries = work_exp if isinstance(work_exp, list) else []
    safe = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cleaned = {k: v for k, v in entry.items() if k not in _BLOCKED_FIELDS}
        safe.append(cleaned)
    return safe


def _sanitize_education(education: Any) -> list:
    """Return education entries. If list of strings, pass through. If dicts, strip blocked fields."""
    if not isinstance(education, list):
        return []
    result = []
    for item in education:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            result.append({k: v for k, v in item.items() if k not in _BLOCKED_FIELDS})
    return result


def _sanitize_certifications(certifications: Any) -> list:
    """Return certifications as a safe list of strings or dicts without blocked fields."""
    if not isinstance(certifications, list):
        return []
    result = []
    for item in certifications:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            # Only expose name/title/issuer/date — never file paths or private URLs
            safe_cert = {}
            for key in ("name", "title", "issuer", "issued_date", "expiry_date", "credential_id"):
                if key in item:
                    safe_cert[key] = item[key]
            if safe_cert:
                result.append(safe_cert)
    return result


def build_ready_card(
    profile: CandidateProfileEntity,
    request: CandidateRequestEntity | None,
    *,
    match_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build the concise recruiter-safe summary card for the Ready section.

    Returned fields:
      candidate_id, name, role, company, location, years_experience,
      skills, summary, match_score, semantic_match, matched_requirements,
      missing_requirements, lifecycle_state (set by caller), profile_access.

    NEVER returns: email, phone, raw_data, parsed_resume_json, resume_text.
    """
    md = _safe_dict(match_data)
    explanation = _safe_dict(md.get("explanation"))

    card: dict[str, Any] = {
        "candidate_id": _text(profile.candidate_id or profile.id),
        "name": _text(profile.name),
        "role": _text(profile.current_role),
        "company": _text(profile.current_company),
        "location": _text(profile.location),
        "years_experience": float(profile.total_experience_years or 0.0),
        "skills": _safe_list(profile.skills),
        "summary": _text(profile.summary),
        # Match / scoring
        "match_score": float(profile.fit_score or md.get("fit_score") or 0.0),
        "semantic_match": float(
            explanation.get("semanticScore")
            or explanation.get("semantic")
            or 0.0
        ),
        "matched_requirements": _safe_list(
            explanation.get("matchedRequirements") or explanation.get("skills_match")
        ),
        "missing_requirements": _safe_list(
            explanation.get("missingRequirements") or explanation.get("missingSkills")
        ),
        # Access metadata
        "profile_access": "FULL" if (request and request.status == "ACCEPTED") else "LIMITED",
        "request_status": request.status if request else None,
        "request_id": str(request.id) if request else None,
    }
    return card


def build_ready_profile(
    profile: CandidateProfileEntity,
    request: CandidateRequestEntity | None,
    *,
    match_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build the full recruiter-safe expanded profile for a Ready candidate.

    Includes: summary, role, company, years_experience, location, skills,
              work_experience, education, certifications, match data.

    NEVER returns: email, phone, raw_data, parsed_resume_json, resume_text,
                   linkedin_url, github_url, or any contact field.
    """
    md = _safe_dict(match_data)
    explanation = _safe_dict(md.get("explanation"))

    parsed = _safe_dict(profile.parsed_resume_json)
    # Derive structured data from parsed_resume_json only — never from raw_data
    work_experience = _sanitize_work_experience(
        profile.work_experience
        or parsed.get("work_experience")
        or parsed.get("experience")
        or []
    )
    education = _sanitize_education(
        profile.education
        if isinstance(profile.education, list)
        else parsed.get("education") or []
    )
    certifications = _sanitize_certifications(
        parsed.get("certifications") or []
    )
    projects = _safe_list(parsed.get("projects") or [])

    expanded: dict[str, Any] = {
        "candidate_id": _text(profile.candidate_id or profile.id),
        "name": _text(profile.name),
        "role": _text(profile.current_role),
        "company": _text(profile.current_company),
        "location": _text(profile.location),
        "years_experience": float(profile.total_experience_years or 0.0),
        "skills": _safe_list(profile.skills),
        "summary": _text(profile.summary),
        # Structured profile — recruiter-safe
        "work_experience": work_experience,
        "education": education,
        "certifications": certifications,
        "projects": projects,
        # Match / scoring
        "match_score": float(profile.fit_score or md.get("fit_score") or 0.0),
        "semantic_match": float(
            explanation.get("semanticScore")
            or explanation.get("semantic")
            or 0.0
        ),
        "matched_requirements": _safe_list(
            explanation.get("matchedRequirements") or explanation.get("skills_match")
        ),
        "missing_requirements": _safe_list(
            explanation.get("missingRequirements") or explanation.get("missingSkills")
        ),
        "ai_reasoning": _text(explanation.get("aiReasoning") or ""),
        # Access metadata
        "profile_access": "FULL" if (request and request.status == "ACCEPTED") else "LIMITED",
        "request_status": request.status if request else None,
        "request_id": str(request.id) if request else None,
        "responded_at": request.responded_at.isoformat() if (request and request.responded_at) else None,
    }

    # Final safety check — assert no blocked fields leaked in
    for blocked in _BLOCKED_FIELDS:
        expanded.pop(blocked, None)

    return expanded
