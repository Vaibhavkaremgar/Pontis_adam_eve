from __future__ import annotations

import re
from typing import Any


# all-MiniLM-L6-v2 has a short useful context window.  Keep the indexed
# representation deliberately compact and put matching signals first.
STRUCTURED_CANDIDATE_TEXT_MAX_CHARS = 1800


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


def _flatten_profile_value(value: Any, *, limit: int = 8) -> list[str]:
    """Turn common JSON profile shapes into short, deterministic lines."""
    if isinstance(value, dict):
        items: list[str] = []
        for key, item in value.items():
            if key.lower() in {"id", "email", "phone", "token", "password"}:
                continue
            if isinstance(item, (str, int, float)) and str(item).strip():
                items.append(f"{key}: {item}")
            elif isinstance(item, list):
                items.extend(_flatten_profile_value(item, limit=limit))
            if len(items) >= limit:
                break
        return items[:limit]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, dict):
                result.append("; ".join(_flatten_profile_value(item, limit=4)))
            else:
                text = _normalize_text(item)
                if text:
                    result.append(text)
            if len(result) >= limit:
                break
        return result[:limit]
    text = _normalize_text(value)
    return [text] if text else []


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


def build_structured_candidate_text(candidate: Any) -> str:
    """Build the compact, deterministic text used by internal candidate embeddings.

    The order is intentional: role, skills, experience, location, summary, then
    recent work/education and a small resume excerpt.  This prevents a long raw
    resume from crowding out the fields recruiters search for most often.
    """
    role = _normalize_text(_get_value(candidate, "current_role", "role", "headline", "job_title", "title") or "")
    company = _normalize_text(_get_value(candidate, "current_company", "company", "job_company_name") or "")
    location = _normalize_text(_get_value(candidate, "location", "location_name", "location_region", "location_country") or "")
    skills = _normalize_list(_get_value(candidate, "skills") or [])
    experience = _get_value(candidate, "total_experience_years", "experience_years", "years_experience", "yearsExperience", "experience")
    if isinstance(experience, (int, float)):
        experience_text = f"{float(experience):g} years"
    else:
        experience_text = _normalize_text(experience or "")
    summary = _normalize_text(_get_value(candidate, "summary", "bio") or "")
    work = _flatten_profile_value(_get_value(candidate, "work_experience", "experience_history") or {}, limit=5)
    education = _flatten_profile_value(_get_value(candidate, "education") or {}, limit=4)
    parsed = _get_value(candidate, "parsed_resume_json", "parsedResumeJson")
    parsed_lines = _flatten_profile_value(parsed, limit=5)
    resume = _normalize_text(_get_value(candidate, "parsed_resume_text", "resume_text") or "")

    parts = []
    if role: parts.append(f"Role: {role}")
    if company: parts.append(f"Current company: {company}")
    if skills: parts.append(f"Skills: {', '.join(skills[:16])}")
    if experience_text: parts.append(f"Experience: {experience_text}")
    if location: parts.append(f"Location: {location}")
    if summary: parts.append(f"Summary: {summary[:420]}")
    if work: parts.append(f"Relevant experience: {' | '.join(work)}")
    if education: parts.append(f"Education: {' | '.join(education)}")
    if parsed_lines: parts.append(f"Parsed profile: {' | '.join(parsed_lines)}")
    if resume: parts.append(f"Resume context: {resume[:500]}")
    return "\n".join(parts)[:STRUCTURED_CANDIDATE_TEXT_MAX_CHARS].strip()


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
