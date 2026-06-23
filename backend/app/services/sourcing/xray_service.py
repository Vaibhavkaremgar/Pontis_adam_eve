from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.core import config as settings
from app.core.config import APIFY_TOKEN, SERPAPI_ENABLED, SOURCE_PROVIDER, XRAY_ENABLED
from app.schemas.candidate import CandidateExplanation, CandidateResult
from app.services.identity.candidate_identity_service import build_candidate_id, build_candidate_identity, normalize_linkedin_url
from app.services.metrics_service import log_metric
from app.services.serpapi_sourcing_service import build_linkedin_xray_queries, discover_linkedin_xray_candidates
from app.services.ranking.models import ranked_candidate_sort_key

logger = logging.getLogger(__name__)


def _normalize_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _tokens(value: Any) -> set[str]:
    import re

    return {token for token in re.findall(r"[a-z0-9]+", _normalize_text(value).lower()) if len(token) > 1}


def _similarity(left: Any, right: Any) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens and not right_tokens:
        return 0.0
    union = left_tokens.union(right_tokens)
    if not union:
        return 0.0
    return len(left_tokens.intersection(right_tokens)) / len(union)


def _job_skills(job: Any) -> list[str]:
    structured = getattr(job, "structured_data", None)
    if isinstance(structured, dict):
        skills = structured.get("skills") or structured.get("skills_required") or []
        if isinstance(skills, list):
            return [str(skill).strip() for skill in skills if str(skill).strip()]
    skills = getattr(job, "skills_required", None) or []
    if isinstance(skills, list):
        return [str(skill).strip() for skill in skills if str(skill).strip()]
    return []


def _candidate_identity_key(candidate: dict[str, Any]) -> str:
    identity = build_candidate_identity(
        candidate=candidate,
        source_provider="xray_apollo",
        source_query=_normalize_text(candidate.get("source_query") or candidate.get("search_query") or ""),
    )
    return (build_candidate_id(candidate=candidate, source_provider="xray_apollo", source_query=_normalize_text(candidate.get("source_query") or candidate.get("search_query") or "")) or identity.identity_fingerprint or identity.canonical_linkedin_url or identity.normalized_name or _normalize_text(candidate.get("id") or candidate.get("name") or "")).strip().lower()


def _broaden_intake(intake: dict[str, Any] | None, *, variant: int) -> dict[str, Any]:
    payload = dict(intake or {})
    if variant <= 0:
        return payload
    if variant == 1:
        payload.pop("company_stage", None)
        payload.pop("hiring_preferences", None)
        payload.pop("industry", None)
        payload.pop("leadership_expectations", None)
        return payload
    payload["skills"] = []
    payload.pop("company_stage", None)
    payload.pop("hiring_preferences", None)
    payload.pop("industry", None)
    payload.pop("leadership_expectations", None)
    return payload


def _fallback_broadening_layers(
    *,
    role: str,
    seniority: str,
    skills: list[str],
    location: str,
    industry: str = "",
    company_stage: str = "",
    raw_result_count: int,
    strict_layer_count: int,
    fallback_threshold: int = 3,
) -> list["XRayQueryLayer"]:
    """
    Sprint 3 — deterministic fallback broadening.

    Trigger conditions:
      - raw_result_count < fallback_threshold after strict search, OR
      - strict_layer_count == 0 (nothing to search with)

    Rules (capped at 2 additional queries):
      FB-1: same title, no location constraint
      FB-2: adjacent titles, no location, light skills
    """
    from app.services.serpapi_sourcing_service import (
        XRayQueryLayer,
        _strict_xray_title_variants,
        _role_family_for_query,
        _xray_single_keyword_terms,
        _strict_xray_query_for_variant,
        _normalize_text as _nt,
        _dedupe_preserve_order,
        _ADJACENT_TITLE_MAP,
    )

    if raw_result_count >= fallback_threshold and strict_layer_count > 0:
        return []

    role = _nt(role)
    seniority = _nt(seniority)
    location = _nt(location)
    skill_list = _dedupe_preserve_order(skills)
    title_variants = _strict_xray_title_variants(role=role, seniority=seniority, skills=skill_list)
    family = _role_family_for_query(role=role or seniority, skills=skill_list)
    adjacent = _xray_single_keyword_terms(
        [t for t in _ADJACENT_TITLE_MAP.get(family, _ADJACENT_TITLE_MAP["generic"])],
        limit=3,
    )
    skill_terms = _xray_single_keyword_terms(skill_list, limit=4)

    fallback_layers: list[XRayQueryLayer] = []
    reason = "raw_count_below_threshold" if raw_result_count < fallback_threshold else "no_strict_layers"

    # FB-1: same primary title + skills, no location
    fb1_query = _strict_xray_query_for_variant(
        variant=1,
        title_terms=title_variants[:2],
        skill_terms=skill_list[:3],
        signal_terms=[],
        location="",
        include_location=False,
    )
    if fb1_query:
        fallback_layers.append(XRayQueryLayer(
            layer_type="fallback_no_location",
            query=fb1_query,
            signals={
                "family": "fallback",
                "family_purpose": "fallback: exact title, no location constraint",
                "fallback_rule": "FB-1",
                "fallback_trigger": reason,
                "is_fallback": True,
            },
        ))

    if len(fallback_layers) >= 2:
        return fallback_layers

    # FB-2: adjacent titles + light skills, no location
    if adjacent:
        fb2_query = _strict_xray_query_for_variant(
            variant=1,
            title_terms=adjacent[:3],
            skill_terms=skill_terms[:2],
            signal_terms=[],
            location="",
            include_location=False,
        )
        if fb2_query and fb2_query != fb1_query:
            fallback_layers.append(XRayQueryLayer(
                layer_type="fallback_adjacent_title",
                query=fb2_query,
                signals={
                    "family": "fallback",
                    "family_purpose": "fallback: adjacent titles, no location",
                    "fallback_rule": "FB-2",
                    "fallback_trigger": reason,
                    "is_fallback": True,
                },
            ))

    return fallback_layers[:2]


def _trim_recruiter_preferences(recruiter_preferences: dict[str, Any] | None, *, variant: int) -> dict[str, Any]:
    prefs = dict(recruiter_preferences or {})
    if variant <= 0:
        return prefs
    preferred_keys = {
        "top_roles",
        "role_tokens",
        "top_skills",
        "skill_tokens",
        "top_experience",
        "experience_tokens",
        "preferred_technical_strengths",
        "preferredTechnicalStrengths",
    }
    if variant == 1:
        return {key: value for key, value in prefs.items() if key in preferred_keys}
    return {}


def _dedupe_candidates(*candidate_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in candidate_groups:
        for candidate in group:
            key = _candidate_identity_key(candidate)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(candidate)
    return merged


_ROLE_KEYWORDS = (
    "engineer",
    "developer",
    "architect",
    "platform",
    "backend",
    "frontend",
    "full stack",
    "fullstack",
    "infra",
    "infrastructure",
    "systems",
    "staff",
    "senior",
    "principal",
    "lead",
    "manager",
    "ml",
    "machine learning",
    "data",
    "product",
    "security",
    "devops",
    "cloud",
)


def _extract_candidate_role(*values: Any) -> str:
    cleaned_values = [_normalize_text(value) for value in values if _normalize_text(value)]
    if not cleaned_values:
        return ""

    for value in cleaned_values:
        parts = [part.strip() for part in re.split(r"\s*[|???-]\s*", value) if part.strip()]
        role_parts: list[str] = []
        for part in parts:
            lowered = part.lower()
            if "linkedin" in lowered or lowered.startswith("http"):
                continue
            if any(keyword in lowered for keyword in _ROLE_KEYWORDS):
                role_parts.append(part)
        if role_parts:
            role = " - ".join(role_parts[:2]).strip()
            if role:
                return role
    return ""


def _score_candidate(job: Any, candidate: dict[str, Any]) -> tuple[float, float, float, float]:
    job_title = _normalize_text(getattr(job, "title", "") or getattr(job, "role", "") or "")
    job_location = _normalize_text(getattr(job, "location", "") or "")
    job_skills = _job_skills(job)

    candidate_title = _normalize_text(candidate.get("title") or candidate.get("job_title") or candidate.get("role") or "")
    candidate_company = _normalize_text(candidate.get("current_company") or candidate.get("company") or candidate.get("job_company_name") or "")
    candidate_location = _normalize_text(candidate.get("location") or "")
    candidate_skills = [str(skill).strip() for skill in (candidate.get("skills") or []) if str(skill).strip()]

    title_signal = _similarity(job_title, candidate_title)
    skill_signal = _similarity(" ".join(job_skills), " ".join(candidate_skills))
    location_signal = 0.0
    if job_location and candidate_location:
        location_signal = 1.0 if job_location.lower() in candidate_location.lower() or candidate_location.lower() in job_location.lower() else _similarity(job_location, candidate_location)
    company_signal = 0.0
    if candidate_company:
        company_signal = 0.1 if len(candidate_company) > 1 else 0.0
    return title_signal, skill_signal, location_signal, company_signal


def _match_skills(job_skills: list[str], candidate_skills: list[str]) -> list[str]:
    matched: list[str] = []
    candidate_lookup = {skill.strip().lower(): skill.strip() for skill in candidate_skills if str(skill).strip()}
    normalized_candidate_skills = [skill.strip().lower() for skill in candidate_skills if str(skill).strip()]

    for skill in job_skills:
        normalized_skill = skill.strip().lower()
        if not normalized_skill:
            continue
        if normalized_skill in candidate_lookup:
            matched.append(candidate_lookup[normalized_skill])
            continue
        if any(normalized_skill in candidate_skill or candidate_skill in normalized_skill for candidate_skill in normalized_candidate_skills):
            matched.append(skill.strip())
    if matched:
        seen: set[str] = set()
        ordered: list[str] = []
        for skill in matched:
            key = skill.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(skill)
        return ordered
    return [skill for skill in candidate_skills if str(skill).strip()]


def _experience_display_text(*, experience: str, years_experience: float | None) -> str:
    if experience:
        return experience
    if years_experience is None:
        return ""
    if float(years_experience).is_integer():
        return f"{int(years_experience)} years"
    return f"{years_experience:g} years"


def _build_profile_summary(
    *,
    candidate_name: str,
    role: str,
    company: str,
    location: str,
    experience_label: str,
    skills: list[str],
    snippet: str,
) -> str:
    subject = candidate_name or role or "Candidate"
    intro_parts: list[str] = []
    if role:
        intro_parts.append(f"is a {role}")
    if company:
        intro_parts.append(f"currently at {company}")
    if location:
        intro_parts.append(f"based in {location}")
    intro = f"{subject} {' '.join(intro_parts)}".strip()
    if intro and not intro.endswith("."):
        intro += "."

    detail_parts: list[str] = []
    if experience_label:
        detail_parts.append(f"Experience signal: {experience_label}.")
    if skills:
        detail_parts.append(f"Core skills surfaced: {', '.join(skills[:4])}.")
    if snippet:
        detail_parts.append(f"Source snippet: {snippet}.")

    parts = [part.strip() for part in [intro, " ".join(detail_parts).strip()] if part.strip()]
    return " ".join(parts).strip()


def _build_match_reason(
    *,
    job: Any,
    role: str,
    company: str,
    location: str,
    experience_label: str,
    matched_skills: list[str],
    title_signal: float,
    skill_signal: float,
    location_signal: float,
    company_signal: float,
    snippet: str,
) -> str:
    job_title = _normalize_text(getattr(job, "title", "") or getattr(job, "role", "") or "")
    job_location = _normalize_text(getattr(job, "location", "") or "")
    reasons: list[str] = []

    if title_signal >= 0.15 or (job_title and role):
        if job_title and role:
            reasons.append(f"their title aligns with {job_title}")
        elif role:
            reasons.append(f"their current title is {role}")

    if matched_skills:
        reasons.append(f"skill overlap includes {', '.join(matched_skills[:4])}")
    elif skill_signal > 0:
        reasons.append("their skills overlap with the job requirements")

    if location_signal > 0.05:
        if job_location and location:
            reasons.append(f"location matches {location} against the job location {job_location}")
        elif location:
            reasons.append(f"location signal points to {location}")

    if experience_label:
        reasons.append(f"experience signal points to {experience_label}")

    if company and company_signal > 0:
        reasons.append(f"current company signal includes {company}")

    if snippet:
        reasons.append(f"the source snippet mentions {snippet}")

    if not reasons:
        return "Matched because the profile has enough title and skill overlap to stay in the XRay shortlist."

    return "Matched because " + "; ".join(reasons) + "."


def _build_preview_result(*, job: Any, candidate: dict[str, Any], index: int) -> CandidateResult:
    identity = build_candidate_identity(candidate=candidate, source_provider="xray_apollo", source_query=_normalize_text(candidate.get("source_query") or candidate.get("search_query") or ""))
    candidate_id = build_candidate_id(candidate=candidate, source_provider="xray_apollo", source_query=_normalize_text(candidate.get("source_query") or candidate.get("search_query") or ""))
    title_signal, skill_signal, location_signal, company_signal = _score_candidate(job, candidate)
    source_signal = float(candidate.get("score") or 0.0)
    snippet_quality = _normalize_text(candidate.get("snippet_quality") or candidate.get("snippetQuality") or "partial").lower()
    snippet_bonus = {"rich": 0.12, "partial": 0.07, "thin": 0.03}.get(snippet_quality, 0.05)
    semantic_base = max(0.0, min(1.0, (title_signal * 0.4) + (skill_signal * 0.35) + (location_signal * 0.1) + (company_signal * 0.15)))
    final_score = max(0.0, min(1.0, (semantic_base * 0.72) + (source_signal * 0.13) + snippet_bonus))
    fit_score = round(final_score * 5.0, 2)
    skills = list(candidate.get("skills") or [])
    company = _normalize_text(candidate.get("current_company") or candidate.get("company") or candidate.get("job_company_name") or "")
    role = _normalize_text(candidate.get("title") or candidate.get("job_title") or candidate.get("role") or "")
    candidate_name = _normalize_text(candidate.get("name") or candidate.get("full_name") or "")
    linkedin_url = normalize_linkedin_url(candidate.get("linkedin_url") or candidate.get("linkedinUrl") or "")
    source_query = _normalize_text(candidate.get("source_query") or candidate.get("sourceQuery") or candidate.get("search_query") or "")
    source_timestamp = _normalize_text(candidate.get("source_timestamp") or candidate.get("sourceTimestamp") or datetime.now(timezone.utc).isoformat())
    source_provider = _normalize_text(candidate.get("source_provider") or candidate.get("sourceProvider") or "serpapi") or "serpapi"
    source = _normalize_text(candidate.get("source") or candidate.get("sourceType") or "xray") or "xray"
    location = _normalize_text(candidate.get("location") or "")
    job_skills = _job_skills(job)
    experience = _normalize_text(
        candidate.get("experience")
        or candidate.get("inferred_experience")
        or candidate.get("years_experience")
        or candidate.get("yearsExperience")
        or ""
    )
    if experience and not re.search(r"\b\d{1,2}\s*[-–—]\s*(?:\d{1,2})\s*(?:years?|yrs?|yr)\b|\b\d{1,2}\+?\s*(?:years?|yrs?|yr)\b", experience, flags=re.IGNORECASE):
        experience = ""
    years_experience = None
    if experience:
        years_match = re.search(r"\d+(?:\.\d+)?", experience)
        if years_match:
            try:
                years_experience = float(years_match.group(0))
            except Exception as exc:
                logger.warning(
                    "xray_preview_years_parse_failed candidate_id=%s index=%s experience=%s error=%s",
                    candidate_id,
                    index,
                    experience,
                    str(exc),
                )
    if years_experience is None:
        candidate_years = candidate.get("years_experience") or candidate.get("yearsExperience")
        try:
            if candidate_years not in (None, ""):
                years_experience = float(candidate_years)
        except (TypeError, ValueError):
            years_experience = None
    experience_label = _experience_display_text(experience=experience, years_experience=years_experience)
    matched_skills = _match_skills(job_skills, skills)
    raw_snippet = _normalize_text(candidate.get("summary") or candidate.get("snippet") or "")
    trimmed_snippet = raw_snippet[:220].rstrip(" ,;:.-") if raw_snippet else ""
    summary = _build_profile_summary(
        candidate_name=candidate_name,
        role=role,
        company=company,
        location=location,
        experience_label=experience_label,
        skills=matched_skills or skills,
        snippet=trimmed_snippet,
    )
    query_family = _normalize_text(candidate.get("query_family") or candidate.get("queryFamily") or "")
    query_signals = candidate.get("query_signals") if isinstance(candidate.get("query_signals"), dict) else candidate.get("querySignals") if isinstance(candidate.get("querySignals"), dict) else {}

    explanation = CandidateExplanation(
        semanticScore=round(title_signal, 4),
        skillOverlap=round(skill_signal, 4),
        finalScore=round(final_score, 4),
        pdlRelevance=0.0,
        recencyScore=0.0,
        engineeringScore=round(min(1.0, (title_signal * 0.6) + (skill_signal * 0.4)), 4),
        penalties={},
        skillsMatched=matched_skills[:5] if matched_skills else [skill for skill in skills[:5] if skill],
        experienceMatch=experience,
        candidateExperience=experience,
        jobExperience=_normalize_text(getattr(job, "experience_required", "") or getattr(job, "experience_level", "") or ""),
        aiReasoning=_build_match_reason(
            job=job,
            role=role,
            company=company,
            location=location,
            experience_label=experience_label,
            matched_skills=matched_skills,
            title_signal=title_signal,
            skill_signal=skill_signal,
            location_signal=location_signal,
            company_signal=company_signal,
            snippet=trimmed_snippet,
        ),
        sourceBreakdown={
            "title": round(title_signal, 4),
            "skills": round(skill_signal, 4),
            "location": round(location_signal, 4),
            "company": round(company_signal, 4),
            "snippetQuality": snippet_quality,
        },
    )

    return CandidateResult(
        id=candidate_id,
        name=candidate_name or "Unknown Candidate",
        role=role or None,
        company=company or None,
        email=None,
        isMockEmail=False,
        headline=role or None,
        location=location or None,
        yearsExperience=years_experience,
        skills=skills,
        summary=summary or None,
        education=None,
        projects=None,
        certifications=None,
        companiesHistory=None,
        domainExperience=None,
        resumeText=None,
        profileData={
            "linkedin_url": linkedin_url,
            "linkedinUrl": linkedin_url,
            "source_url": _normalize_text(candidate.get("source_url") or candidate.get("link") or candidate.get("displayed_link") or ""),
            "identity": identity.to_dict(),
            "id": candidate_id,
            "source_provider": source_provider,
            "source_provider_label": source_provider,
            "source_query": source_query,
            "source_timestamp": source_timestamp,
            "current_company": company or None,
            "inferred_experience": experience or None,
            "snippet": summary or None,
            "snippet_quality": snippet_quality,
            "query_family": query_family,
            "query_signals": query_signals,
        },
        fitScore=fit_score,
        decision="strong_match" if fit_score >= 4 else "potential" if fit_score >= 2.5 else "weak",
        explanation=explanation,
        strategy="HIGH" if fit_score >= 4 else "MEDIUM" if fit_score >= 2.5 else "LOW",
        status="sourced",
        outreachStatus="pending",
        enrichmentStatus="pending",
        enrichmentSource="",
        enrichmentConfidence=0.0,
        contactEmail="",
        contactPhone="",
        exportStatus="pending",
        ats_export_status="not_sent",
        source=source,
        sourceProvider=source_provider,
        sourceQuery=source_query,
        sourceTimestamp=source_timestamp,
        sourceType=source,
        linkedinUrl=linkedin_url,
        githubUrl=None,
        portfolioUrl=None,
        source_url=_normalize_text(candidate.get("source_url") or candidate.get("link") or candidate.get("displayed_link") or ""),
        currentCompany=company or None,
        inferredExperience=experience or None,
        snippetQuality=snippet_quality if snippet_quality in {"rich", "partial", "thin"} else "partial",
        rawDiscovery={
            "query": source_query,
            "source_url": _normalize_text(candidate.get("source_url") or candidate.get("link") or candidate.get("displayed_link") or ""),
            "displayed_link": _normalize_text(candidate.get("displayed_link") or ""),
            "linkedin_url": linkedin_url,
            "snippet": summary or None,
            "title": _normalize_text(candidate.get("name") or candidate.get("full_name") or title or ""),
            "current_company": company or None,
            "location": location or None,
            "linkedin_url": linkedin_url,
            "source_provider": source_provider,
            "source_timestamp": source_timestamp,
            "snippet_quality": snippet_quality,
            "query_family": query_family,
            "query_signals": query_signals,
        },
    )


def discover_xray_candidates(
    *,
    job: Any,
    intake: dict[str, Any] | None = None,
    limit: int = 10,
    pages_per_query: int = 1,
    recruiter_preferences: dict[str, Any] | None = None,
    db: Any | None = None,
    role_search_id: str = "",
    recruiter_id: str = "",
    company_id: str = "",
    workflow_token: str = "",
    archetype_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    if SOURCE_PROVIDER != "xray_apollo":
        logger.info("[xray] skipped source_provider=%s", SOURCE_PROVIDER)
        return []
    if not XRAY_ENABLED or not SERPAPI_ENABLED:
        logger.info("[xray] skipped enabled=%s serpapi_enabled=%s", XRAY_ENABLED, SERPAPI_ENABLED)
        return []

    effective_limit = max(1, int(limit))
    max_pages = max(1, int(getattr(settings, "SERPAPI_MAX_PAGES", 3)))
    job_id = getattr(job, "id", "")
    role = _normalize_text((intake or {}).get("role") or getattr(job, "title", "") or "")
    logger.info(
        "[xray] discovery_started job_id=%s role=%s limit=%s pages_per_query=%s apify_token_configured=%s",
        job_id,
        role,
        effective_limit,
        max_pages,
        bool(APIFY_TOKEN),
    )
    log_metric(
        "xray_discovery_started",
        job_id=job_id,
        limit=effective_limit,
        pages_per_query=max_pages,
    )

    queries = build_linkedin_xray_queries(
        role=_normalize_text((intake or {}).get("role") or getattr(job, "title", "") or ""),
        seniority=_normalize_text((intake or {}).get("seniority") or getattr(job, "experience_level", "") or ""),
        skills=[str(skill).strip() for skill in ((intake or {}).get("skills") or []) if str(skill).strip()] if isinstance((intake or {}).get("skills"), list) else [token.strip() for token in _normalize_text((intake or {}).get("skills") or "").split(",") if token.strip()],
        education_level=_normalize_text((intake or {}).get("education_level") or getattr(job, "education_level", "") or ""),
        preferred_institutions=[
            str(item).strip()
            for item in (
                (intake or {}).get("preferred_institutions")
                or (getattr(job, "structured_data", {}) or {}).get("preferred_institutions")
                or (getattr(job, "structured_data", {}) or {}).get("preferredInstitutions")
                or []
            )
            if str(item).strip()
        ] if isinstance(
            (intake or {}).get("preferred_institutions")
            or (getattr(job, "structured_data", {}) or {}).get("preferred_institutions")
            or (getattr(job, "structured_data", {}) or {}).get("preferredInstitutions")
            or [],
            list,
        ) else [token.strip() for token in _normalize_text((intake or {}).get("preferred_institutions") or "").split(",") if token.strip()],
        certifications=[
            str(item).strip()
            for item in (
                (intake or {}).get("certifications")
                or (getattr(job, "structured_data", {}) or {}).get("certifications")
                or (getattr(job, "structured_data", {}) or {}).get("certification")
                or []
            )
            if str(item).strip()
        ] if isinstance(
            (intake or {}).get("certifications")
            or (getattr(job, "structured_data", {}) or {}).get("certifications")
            or (getattr(job, "structured_data", {}) or {}).get("certification")
            or [],
            list,
        ) else [token.strip() for token in _normalize_text((intake or {}).get("certifications") or "").split(",") if token.strip()],
        location=_normalize_text((intake or {}).get("location") or getattr(job, "location", "") or ""),
        company_stage=_normalize_text((intake or {}).get("company_stage") or ""),
        hiring_preferences=_normalize_text((intake or {}).get("hiring_preferences") or ""),
        industry=_normalize_text((intake or {}).get("industry") or ""),
        leadership_expectations=_normalize_text((intake or {}).get("leadership_expectations") or ""),
        remote_policy=_normalize_text((intake or {}).get("remote_policy") or getattr(job, "remote_policy", "") or ""),
        compensation=_normalize_text((intake or {}).get("compensation") or getattr(job, "compensation", "") or ""),
        work_authorization=_normalize_text((intake or {}).get("work_authorization") or getattr(job, "work_authorization", "") or ""),
        recruiter_preferences=recruiter_preferences,
        job_description=_normalize_text(getattr(job, "description", "") or ""),
        voice_summary=_normalize_text((getattr(job, "structured_data", {}) or {}).get("voiceTranscriptClean", "") if isinstance(getattr(job, "structured_data", {}), dict) else ""),
        voice_transcript=_normalize_text((getattr(job, "structured_data", {}) or {}).get("voiceTranscriptRaw", "") if isinstance(getattr(job, "structured_data", {}), dict) else ""),
        nice_to_have_skills=[str(skill).strip() for skill in ((getattr(job, "structured_data", {}) or {}).get("nice_to_have_skills") or []) if str(skill).strip()] if isinstance((getattr(job, "structured_data", {}) or {}).get("nice_to_have_skills"), list) else [],
    )
    primary_query = queries[0] if queries else ""
    logger.info("[xray_query] job_id=%s query=%s pages=%s", job_id, primary_query, max_pages)

    effective_limit = max(1, int(limit))
    raw_candidates = discover_linkedin_xray_candidates(
        job=job,
        intake=intake,
        limit=effective_limit,
        pages_per_query=max_pages,
        recruiter_preferences=recruiter_preferences,
        db=db,
        role_search_id=role_search_id or f"{job_id}:xray_preview",
        recruiter_id=recruiter_id,
        company_id=company_id,
        workflow_token=workflow_token,
        archetype_ids=archetype_ids or [],
    )

    if not raw_candidates:
        logger.info("[xray_candidate_count] job_id=%s raw=0 deduped=0", job_id)
        log_metric("xray_candidates_found", job_id=job_id, count=0)

        # Sprint 3 — fallback broadening when strict search returns nothing
        fallback_layers = _fallback_broadening_layers(
            role=_normalize_text((intake or {}).get("role") or getattr(job, "title", "") or ""),
            seniority=_normalize_text((intake or {}).get("seniority") or getattr(job, "experience_level", "") or ""),
            skills=[
                str(s).strip()
                for s in ((intake or {}).get("skills") or [])
                if str(s).strip()
            ] if isinstance((intake or {}).get("skills"), list) else [
                t.strip()
                for t in _normalize_text((intake or {}).get("skills") or "").split(",")
                if t.strip()
            ],
            location=_normalize_text((intake or {}).get("location") or getattr(job, "location", "") or ""),
            industry=_normalize_text((intake or {}).get("industry") or ""),
            company_stage=_normalize_text((intake or {}).get("company_stage") or ""),
            raw_result_count=0,
            strict_layer_count=0,
        )
        if fallback_layers:
            logger.info(
                "[xray_fallback] job_id=%s fallback_layer_count=%s reason=zero_raw_results",
                job_id,
                len(fallback_layers),
            )
            for fb_layer in fallback_layers:
                fb_raw = discover_linkedin_xray_candidates(
                    job=job,
                    intake=intake,
                    limit=effective_limit,
                    pages_per_query=max_pages,
                    recruiter_preferences=recruiter_preferences,
                    db=db,
                    role_search_id=role_search_id or f"{job_id}:xray_fallback",
                    recruiter_id=recruiter_id,
                    company_id=company_id,
                    workflow_token=workflow_token,
                    archetype_ids=archetype_ids or [],
                )
                if fb_raw:
                    raw_candidates = fb_raw
                    logger.info(
                        "[xray_fallback_hit] job_id=%s layer=%s count=%s",
                        job_id,
                        fb_layer.layer_type,
                        len(fb_raw),
                    )
                    break
        if not raw_candidates:
            return []

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicate_linkedin_urls = 0
    duplicate_candidate_ids = 0
    duplicate_name_company = 0
    for candidate in raw_candidates:
        identity = build_candidate_identity(candidate=candidate, source_provider="xray_apollo", source_query=_normalize_text(candidate.get("source_query") or candidate.get("search_query") or ""))
        candidate_id = build_candidate_id(candidate=candidate, source_provider="xray_apollo", source_query=_normalize_text(candidate.get("source_query") or candidate.get("search_query") or ""))
        linkedin_url = identity.canonical_linkedin_url
        name_company = f"{identity.normalized_name}|{identity.normalized_company}".strip("|").lower()
        key = candidate_id or identity.identity_fingerprint or linkedin_url.lower() or name_company
        if key and key in seen:
            if linkedin_url and linkedin_url.lower() in seen:
                duplicate_linkedin_urls += 1
            elif candidate_id and candidate_id.lower() in seen:
                duplicate_candidate_ids += 1
            else:
                duplicate_name_company += 1
            continue
        if key:
            seen.add(key)
        if linkedin_url:
            seen.add(linkedin_url.lower())
        if candidate_id:
            seen.add(candidate_id.lower())
        if name_company:
            seen.add(name_company)
        logger.info(
            "xray_candidate_id_normalized job_id=%s candidate_id=%s linkedin_url=%s source_url=%s",
            job_id,
            candidate_id,
            linkedin_url,
            _normalize_text(candidate.get("source_url") or candidate.get("link") or candidate.get("displayed_link") or ""),
        )
        normalized.append(
            {
                **candidate,
                "id": candidate_id,
                "source_provider": "xray_apollo",
                "sourceProvider": "xray_apollo",
                "source": "linkedin_xray",
                "source_type": "linkedin_xray",
                "sourceType": "linkedin_xray",
                "source_timestamp": candidate.get("source_timestamp") or datetime.now(timezone.utc).isoformat(),
                "sourceTimestamp": candidate.get("source_timestamp") or datetime.now(timezone.utc).isoformat(),
                "source_query": candidate.get("source_query") or candidate.get("search_query") or "",
                "sourceQuery": candidate.get("source_query") or candidate.get("search_query") or "",
                "current_company": candidate.get("current_company") or candidate.get("company") or candidate.get("job_company_name") or "",
                "currentCompany": candidate.get("current_company") or candidate.get("company") or candidate.get("job_company_name") or "",
                "inferred_experience": candidate.get("inferred_experience") or candidate.get("experience") or "",
                "inferredExperience": candidate.get("inferred_experience") or candidate.get("experience") or "",
                "identity": identity.to_dict(),
                "candidate_id": candidate_id,
            }
        )

    logger.info(
        "[xray_candidate_count] job_id=%s raw=%s deduped=%s requested_limit=%s",
        job_id,
        len(raw_candidates),
        len(normalized),
        effective_limit,
    )
    logger.info("[xray_deduped] job_id=%s count=%s", job_id, len(normalized))
    logger.info(
        "[xray_dedup] job_id=%s raw_candidates=%s duplicate_candidates=%s deduped_candidates=%s duplicate_rate=%.4f duplicate_linkedin_urls=%s duplicate_candidate_ids=%s duplicate_candidate_names=%s",
        job_id,
        len(raw_candidates),
        len(raw_candidates) - len(normalized),
        len(normalized),
        (len(raw_candidates) - len(normalized)) / len(raw_candidates) if raw_candidates else 0.0,
        duplicate_linkedin_urls,
        duplicate_candidate_ids,
        duplicate_name_company,
    )
    log_metric("xray_candidates_found", job_id=job_id, count=len(normalized))
    return normalized


def build_xray_candidate_results(
    *,
    job: Any,
    candidates: list[dict[str, Any]],
    limit: int = 12,
) -> list[CandidateResult]:
    ranked: list[CandidateResult] = []
    for index, candidate in enumerate(candidates, start=1):
        candidate_id = str(candidate.get("id") or candidate.get("candidate_id") or candidate.get("linkedin_url") or candidate.get("link") or "").strip()
        try:
            ranked.append(_build_preview_result(job=job, candidate=candidate, index=index))
        except Exception as exc:
            logger.exception(
                "xray_preview_build_failed job_id=%s candidate_id=%s index=%s error=%s",
                getattr(job, "id", ""),
                candidate_id,
                index,
                str(exc),
            )
            continue
    ranked.sort(key=ranked_candidate_sort_key)
    logger.info(
        "xray_preview_build_completed job_id=%s input_count=%s output_count=%s",
        getattr(job, "id", ""),
        len(candidates),
        len(ranked),
    )
    return ranked[: max(1, limit)]
