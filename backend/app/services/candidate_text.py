from __future__ import annotations

import re
from typing import Any


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _normalize_list(values: Any) -> list[str]:
    if isinstance(values, list):
        items = values
    elif isinstance(values, str) and values.strip():
        items = [values]
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _normalize_text(item)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized


def _get_value(candidate: Any, *keys: str) -> Any:
    if isinstance(candidate, dict):
        for key in keys:
            value = candidate.get(key)
            if value not in (None, ""):
                return value
        return None

    for key in keys:
        value = getattr(candidate, key, None)
        if value not in (None, ""):
            return value
    return None


def build_candidate_text(candidate: Any) -> str:
    role = _normalize_text(_get_value(candidate, "role", "title", "job_title", "headline") or "")
    name = _normalize_text(_get_value(candidate, "name", "full_name") or "")
    company = _normalize_text(_get_value(candidate, "company", "job_company_name", "current_company") or "")
    location = _normalize_text(_get_value(candidate, "location", "location_name", "location_region", "location_country") or "")
    skills = _normalize_list(_get_value(candidate, "skills", "skills_required") or [])
    experience_value = _get_value(candidate, "experience", "experience_level", "years_experience", "yearsExperience", "experience_summary")
    if isinstance(experience_value, (int, float)):
        experience = f"{float(experience_value):g} years"
    else:
        experience = _normalize_text(experience_value or "")
    headline = _normalize_text(_get_value(candidate, "headline", "job_title", "title") or "")
    summary = _normalize_text(_get_value(candidate, "summary", "bio", "experience_summary") or "")
    companies = _normalize_list(_get_value(candidate, "companies", "company_history", "companiesHistory") or [])
    projects = _normalize_list(_get_value(candidate, "projects") or [])
    education = _normalize_list(_get_value(candidate, "education") or [])
    certifications = _normalize_list(_get_value(candidate, "certifications") or [])
    domain_experience = _normalize_list(_get_value(candidate, "domain_experience", "domainExperience") or [])
    resume_text = _normalize_text(_get_value(candidate, "raw_resume_text", "resume_text", "parsed_resume_text") or "")

    parts = [
        f"Name: {name}".strip() if name else "",
        f"Role: {role}".strip() if role else "",
        f"Headline: {headline}".strip() if headline else "",
        f"Company: {company}".strip() if company else "",
        f"Location: {location}".strip() if location else "",
        f"Skills: {', '.join(skills)}".strip() if skills else "",
        f"Experience: {experience}".strip() if experience else "",
        f"Companies: {', '.join(companies)}".strip() if companies else "",
        f"Projects: {', '.join(projects)}".strip() if projects else "",
        f"Education: {', '.join(education)}".strip() if education else "",
        f"Certifications: {', '.join(certifications)}".strip() if certifications else "",
        f"Domain experience: {', '.join(domain_experience)}".strip() if domain_experience else "",
        f"Summary:\n{summary}".strip() if summary else "",
        f"Resume text:\n{resume_text[:8000]}".strip() if resume_text else "",
    ]

    return "\n".join(part for part in parts if part).strip()
