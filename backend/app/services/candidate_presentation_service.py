"""
candidate_presentation_service.py

Shared recruiter-facing candidate view-model.

Both the UI candidate list and Slack candidate cards consume this module.
No LLM usage — all summary text is built deterministically from ranked
candidate fields already present in CandidateResult / CandidateExplanation.
"""
from __future__ import annotations

import re
from typing import Any


# ── helpers ───────────────────────────────────────────────────────────────────

def _t(value: Any) -> str:
    """Normalize any value to a stripped string."""
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _list_clean(values: Any, *, limit: int = 5) -> list[str]:
    if not isinstance(values, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for item in values:
        cleaned = _t(item)
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
        if len(result) >= limit:
            break
    return result


def _resolve(source: Any, *keys: str) -> str:
    """Pull the first non-empty string value from a dict or object."""
    if isinstance(source, dict):
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return _t(value)
    else:
        for key in keys:
            value = getattr(source, key, None)
            if isinstance(value, str) and value.strip():
                return _t(value)
    return ""


def _resolve_float(source: Any, *keys: str) -> float | None:
    if isinstance(source, dict):
        for key in keys:
            value = source.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass
    else:
        for key in keys:
            value = getattr(source, key, None)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass
    return None


def _education_text(value: Any) -> str:
    if isinstance(value, str):
        return _t(value)
    if not isinstance(value, dict):
        return ""

    school = _t(value.get("school") or value.get("institution") or value.get("university") or value.get("name"))
    degree = _t(value.get("degree") or value.get("qualification") or value.get("fieldOfStudy") or value.get("field"))
    summary = _t(value.get("summary") or value.get("description"))

    parts: list[str] = []
    if degree and school:
        parts.append(f"{degree} at {school}")
    elif degree:
        parts.append(degree)
    elif school:
        parts.append(school)
    if summary and summary.lower() not in " ".join(parts).lower():
        parts.append(summary)
    return ", ".join(parts).strip(" ,")


def _education_summary(candidate: Any) -> str:
    raw_education = None
    if isinstance(candidate, dict):
        raw_education = candidate.get("education") or candidate.get("educations")
    else:
        raw_education = getattr(candidate, "education", None) or getattr(candidate, "educations", None)

    entries: list[str] = []
    if isinstance(raw_education, list):
        for item in raw_education:
            cleaned = _education_text(item)
            if cleaned and cleaned.lower() not in {entry.lower() for entry in entries}:
                entries.append(cleaned)
            if len(entries) >= 2:
                break
    else:
        cleaned = _education_text(raw_education)
        if cleaned:
            entries.append(cleaned)

    if not entries:
        return ""
    if len(entries) == 1:
        return f"Education highlights include {entries[0]}."
    return f"Education highlights include {entries[0]} and {entries[1]}."


def _split_headline_dump(text: str) -> list[str]:
    clean = _t(text)
    if not clean:
        return []
    parts = [part.strip(" -–—:") for part in re.split(r"\s*\|\s*|\s*•\s*|\s*[\n\r]+\s*", clean) if part.strip(" -–—:")]
    if len(parts) >= 3:
        return parts
    return [clean]


def _humanize_snippet(snippet: str, *, name: str = "", role: str = "", company: str = "", location: str = "") -> str:
    """
    Convert terse LinkedIn-style snippets into a more human-sounding recap.
    This stays deterministic so the UI and Slack remain aligned.
    """
    clean = _t(snippet)
    if not clean:
        return ""

    lower = clean.lower()
    if "|" in clean or clean.count("•") >= 2:
        parts = _split_headline_dump(clean)
        if len(parts) >= 3:
            headline = parts[0]
            skills_text = ", ".join(parts[1:4])
            intro_name = name.split()[0] if name and name != "Unknown Candidate" else "This candidate"
            pieces = [f"{intro_name} appears to be a {headline.lower()}"]
            if company and location:
                pieces.append(f"based at {company} in {location}")
            elif company:
                pieces.append(f"based at {company}")
            elif location:
                pieces.append(f"based in {location}")
            if skills_text:
                pieces.append(f"with a profile centered on {skills_text.lower()}")
            return ", ".join(pieces).strip(" ,") + "."

    normalized = clean[:-1] if clean.endswith(".") else clean
    normalized = re.sub(r"\s*\|\s*", ", ", normalized)

    if role and role.lower() not in lower:
        intro_name = name.split()[0] if name and name != "Unknown Candidate" else "This candidate"
        if company and company.lower() not in lower:
            return f"{intro_name} is a {role} at {company}. {normalized[:220].rstrip(' .,;')}."
        return f"{intro_name} is a {role}. {normalized[:220].rstrip(' .,;')}."

    if location and location.lower() not in lower and company and company.lower() not in lower:
        intro_name = name.split()[0] if name and name != "Unknown Candidate" else "This candidate"
        return f"{intro_name} is based in {location}. {normalized[:220].rstrip(' .,;')}."

    return normalized[:260].rstrip(" .,;") + "."


def _linkedin_url(candidate: Any) -> str:
    """Extract LinkedIn URL from CandidateResult or dict, normalizing fallbacks."""
    direct = _resolve(
        candidate,
        "linkedinUrl", "linkedin_url", "linkedin",
    )
    if direct and "linkedin.com/" in direct.lower():
        return direct.rstrip("/")

    profile_data = None
    if isinstance(candidate, dict):
        profile_data = candidate.get("profileData") or candidate.get("rawDiscovery") or {}
    else:
        profile_data = getattr(candidate, "profileData", None) or getattr(candidate, "rawDiscovery", None) or {}

    if isinstance(profile_data, dict):
        for key in ("linkedin_url", "linkedinUrl", "linkedin", "source_url"):
            value = _t(profile_data.get(key) or "")
            if value and "linkedin.com/" in value.lower():
                return value.rstrip("/")
    return ""


# ── deterministic recruiter summary ──────────────────────────────────────────

def build_recruiter_summary(
    *,
    name: str,
    role: str,
    company: str,
    location: str,
    years_experience: float | None,
    experience_label: str,
    matched_skills: list[str],
    all_skills: list[str],
    fit_score: float,
    explanation_reasoning: str,
    job_location: str = "",
    job_experience: str = "",
    raw_snippet: str = "",
    education_summary: str = "",
) -> list[str]:
    """
    Build a human-sounding recruiter summary as 2–3 natural sentences.
    Reads like a recruiter describing the candidate in a team standup —
    no bullet labels, no robotic field dumps.
    """
    first_name = name.split()[0] if name and name != "Unknown Candidate" else ""
    pronoun = first_name or "They"
    verb_have = "has" if pronoun != "They" else "have"
    verb_is = "is" if pronoun != "They" else "are"

    sentences: list[str] = []

    # — Sentence 1: who they are, where they work, location —
    s1_parts: list[str] = []

    if years_experience is not None and years_experience > 0:
        yr_int = int(years_experience)
        s1_parts.append(f"{pronoun} {verb_have} {yr_int} year{'s' if yr_int != 1 else ''} of experience")
    elif experience_label:
        s1_parts.append(f"{pronoun} {verb_have} {experience_label} of experience")

    if role and company:
        if s1_parts:
            s1_parts.append(f"and {verb_is} currently working as a {role} at {company}")
        else:
            s1_parts.append(f"{pronoun} {verb_is} currently a {role} at {company}")
    elif role:
        if s1_parts:
            s1_parts.append(f"as a {role}")
        else:
            s1_parts.append(f"{pronoun} {verb_is} a {role}")
    elif company:
        if s1_parts:
            s1_parts.append(f"at {company}")
        else:
            s1_parts.append(f"{pronoun} works at {company}")

    if location:
        s1_parts.append(f"based out of {location}")

    if s1_parts:
        sentence = ", ".join(s1_parts)
        sentences.append(sentence[0].upper() + sentence[1:] + ".")

    # — Sentence 2: skills in natural language —
    # Filter out skills that look like locations or noise (single chars, numbers)
    display_skills = [
        s for s in (matched_skills[:4] or all_skills[:4])
        if len(s) > 1 and not s.replace(" ", "").isdigit()
    ]
    if display_skills:
        if len(display_skills) == 1:
            skill_phrase = display_skills[0]
        elif len(display_skills) == 2:
            skill_phrase = f"{display_skills[0]} and {display_skills[1]}"
        else:
            skill_phrase = ", ".join(display_skills[:-1]) + f", and {display_skills[-1]}"
        skill_verb = "brings hands-on experience in" if matched_skills else "has worked with"
        sentences.append(f"{pronoun} {skill_verb} {skill_phrase}.")

    # — Sentence 3: location fit, or a clean signal from the raw snippet —
    if location and job_location and _t(location).lower() not in _t(job_location).lower():
        sentences.append(f"Currently located in {location}, while the role is based in {job_location}.")
    elif location and job_location and _t(location).lower() in _t(job_location).lower():
        sentences.append(f"{pronoun} {verb_is} locally based in {location}, which lines up well for this role.")
    elif len(sentences) < 2 and raw_snippet:
        humanized = _humanize_snippet(raw_snippet, name=name, role=role, company=company, location=location)
        if humanized:
            sentences.append(humanized)
    elif len(sentences) < 2 and explanation_reasoning:
        truncated = _t(explanation_reasoning)[:200].rstrip(" .,;")
        if truncated:
            sentences.append(truncated + ".")

    if education_summary:
        sentences.append(education_summary)

    # Fallback if we couldn’t build anything meaningful
    if not sentences:
        if first_name:
            return [f"{first_name} was sourced via LinkedIn and looks worth a closer look."]
        return ["This candidate was sourced via LinkedIn and looks worth a closer look."]

    return sentences[:5]


# ── shared view-model builder ─────────────────────────────────────────────────

def build_candidate_view_model(
    candidate: Any,
    *,
    job_location: str = "",
    job_experience: str = "",
) -> dict[str, Any]:
    """
    Convert a CandidateResult (or dict) into a standardised recruiter-facing
    presentation payload consumed by both the UI and Slack card builder.

    Does NOT change scores or ranking — presentation only.
    """
    candidate_id = _resolve(candidate, "id", "candidate_id")
    name = _resolve(candidate, "name", "full_name") or "Unknown Candidate"
    role = _resolve(candidate, "role", "headline", "job_title", "title") or ""
    company = _resolve(candidate, "company", "currentCompany", "job_company_name") or ""
    location = _resolve(candidate, "location") or ""
    fit_score = _resolve_float(candidate, "fitScore", "fit_score") or 0.0
    years_experience = _resolve_float(candidate, "yearsExperience", "years_experience")
    summary = _resolve(candidate, "summary") or ""
    linkedin_url = _linkedin_url(candidate)
    education_summary = _education_summary(candidate)

    # Skills
    raw_skills: list[str] = []
    if isinstance(candidate, dict):
        raw_skills = candidate.get("skills") or []
    else:
        raw_skills = getattr(candidate, "skills", None) or []
    all_skills = _list_clean(raw_skills)

    # Explanation fields
    explanation = None
    if isinstance(candidate, dict):
        explanation = candidate.get("explanation")
    else:
        explanation = getattr(candidate, "explanation", None)

    matched_skills: list[str] = []
    experience_label = ""
    job_exp = job_experience
    ai_reasoning = ""
    final_score = fit_score / 5.0 if fit_score > 0 else 0.0
    semantic_score = 0.0
    skill_overlap = 0.0
    source_breakdown: dict[str, Any] = {}

    if explanation is not None:
        matched_skills = _list_clean(
            explanation.get("skillsMatched") if isinstance(explanation, dict)
            else getattr(explanation, "skillsMatched", None)
        )
        experience_label = _resolve(explanation, "candidateExperience", "experienceMatch") or ""
        if not job_exp:
            job_exp = _resolve(explanation, "jobExperience") or ""
        ai_reasoning = _resolve(explanation, "aiReasoning") or ""
        final_score_raw = _resolve_float(explanation, "finalScore")
        if final_score_raw is not None:
            final_score = final_score_raw
        semantic_score = _resolve_float(explanation, "semanticScore") or 0.0
        skill_overlap = _resolve_float(explanation, "skillOverlap") or 0.0
        if isinstance(explanation, dict):
            source_breakdown = explanation.get("sourceBreakdown") or {}
        else:
            source_breakdown = getattr(explanation, "sourceBreakdown", None) or {}

    # Inferred experience label from years if not set
    if not experience_label and years_experience is not None:
        yr_int = int(years_experience)
        experience_label = f"{yr_int} year{'s' if yr_int != 1 else ''}"

    # inferredExperience fallback
    if not experience_label:
        experience_label = _resolve(candidate, "inferredExperience") or ""

    # Recruiter summary (deterministic, no LLM)
    summary_lines = build_recruiter_summary(
        name=name,
        role=role,
        company=company,
        location=location,
        years_experience=years_experience,
        experience_label=experience_label,
        matched_skills=matched_skills,
        all_skills=all_skills,
        fit_score=fit_score,
        explanation_reasoning=ai_reasoning,
        job_location=job_location,
        job_experience=job_exp,
        raw_snippet=summary,
        education_summary=education_summary,
    )

    # Source metadata
    source_provider = _resolve(candidate, "sourceProvider", "source_provider") or "xray"
    source_query = _resolve(candidate, "sourceQuery", "source_query") or ""
    source_type = _resolve(candidate, "sourceType", "source_type", "source") or "xray"
    snippet_quality = _resolve(candidate, "snippetQuality", "snippet_quality") or "partial"

    return {
        "candidate_id": candidate_id,
        "name": name,
        "linkedin_url": linkedin_url,
        "headline": role or None,
        "role": role or None,
        "company": company or None,
        "location": location or None,
        "fit_score": round(fit_score, 2),
        "fit_score_display": f"{fit_score:.1f}/5",
        "final_score": round(final_score, 4),
        "semantic_score": round(semantic_score, 4),
        "skill_overlap": round(skill_overlap, 4),
        "matched_skills": matched_skills[:5],
        "all_skills": all_skills[:8],
        "years_experience": years_experience,
        "experience_label": experience_label,
        "summary_lines": summary_lines,
        "recruiter_summary": " ".join(summary_lines),
        "source_provider": source_provider,
        "source_query": source_query,
        "source_type": source_type,
        "snippet_quality": snippet_quality,
        "source_breakdown": source_breakdown,
        "raw_summary": summary,
        "ai_reasoning": ai_reasoning,
    }


def assert_parity_builder(candidate: Any, *, context: str = "") -> dict:
    """
    Lightweight verification point: log that a given candidate was processed
    through the shared presentation builder.  Call this in debug/test paths to
    prove that both Slack and UI derive their payload from the same function.
    """
    import logging
    vm = build_candidate_view_model(candidate)
    logging.getLogger(__name__).debug(
        "candidate_presentation_parity builder=build_candidate_view_model "
        "context=%s candidate_id=%s fit_score_display=%s matched_skills_count=%s",
        context or "unspecified",
        vm.get("candidate_id", ""),
        vm.get("fit_score_display", ""),
        len(vm.get("matched_skills") or []),
    )
    return vm
