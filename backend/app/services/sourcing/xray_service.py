from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.core.config import APOLLO_ENRICHMENT_ENABLED, SERPAPI_ENABLED, SOURCE_PROVIDER, XRAY_ENABLED
from app.schemas.candidate import CandidateExplanation, CandidateResult
from app.services.identity.candidate_identity_service import build_candidate_identity, normalize_linkedin_url
from app.services.metrics_service import log_metric
from app.services.serpapi_sourcing_service import build_linkedin_xray_queries, discover_linkedin_xray_candidates

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


def _score_candidate(job: Any, candidate: dict[str, Any]) -> tuple[float, float, float, float]:
    job_title = _normalize_text(getattr(job, "title", "") or getattr(job, "role", "") or "")
    job_location = _normalize_text(getattr(job, "location", "") or "")
    job_skills = _job_skills(job)

    candidate_title = _normalize_text(candidate.get("title") or candidate.get("headline") or candidate.get("job_title") or "")
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


def _build_preview_result(*, job: Any, candidate: dict[str, Any], index: int) -> CandidateResult:
    identity = build_candidate_identity(candidate=candidate, source_provider="xray_apollo", source_query=_normalize_text(candidate.get("source_query") or candidate.get("search_query") or ""))
    title_signal, skill_signal, location_signal, company_signal = _score_candidate(job, candidate)
    raw_score = float(candidate.get("score") or 0.0)
    final_score = max(0.0, min(1.0, (raw_score * 0.5) + (title_signal * 0.2) + (skill_signal * 0.2) + (location_signal * 0.05) + company_signal))
    fit_score = round(final_score * 5.0, 2)
    skills = list(candidate.get("skills") or [])
    company = _normalize_text(candidate.get("current_company") or candidate.get("company") or candidate.get("job_company_name") or "")
    title = _normalize_text(candidate.get("title") or candidate.get("headline") or candidate.get("job_title") or getattr(job, "title", "") or "")
    linkedin_url = normalize_linkedin_url(candidate.get("linkedin_url") or candidate.get("linkedinUrl") or "")
    source_query = _normalize_text(candidate.get("source_query") or candidate.get("sourceQuery") or candidate.get("search_query") or "")
    source_timestamp = _normalize_text(candidate.get("source_timestamp") or candidate.get("sourceTimestamp") or datetime.now(timezone.utc).isoformat())
    source_provider = _normalize_text(candidate.get("source_provider") or candidate.get("sourceProvider") or "xray_apollo") or "xray_apollo"
    location = _normalize_text(candidate.get("location") or "")
    summary = _normalize_text(candidate.get("snippet") or candidate.get("summary") or candidate.get("displayed_link") or candidate.get("search_query") or "")
    experience = _normalize_text(candidate.get("inferred_experience") or candidate.get("experience") or getattr(job, "experience_required", "") or "")

    explanation = CandidateExplanation(
        semanticScore=round(title_signal, 4),
        skillOverlap=round(skill_signal, 4),
        finalScore=round(final_score, 4),
        pdlRelevance=0.0,
        recencyScore=0.0,
        engineeringScore=round(min(1.0, (title_signal * 0.6) + (skill_signal * 0.4)), 4),
        penalties={},
        skillsMatched=[skill for skill in skills[:5] if skill],
        experienceMatch=experience,
        candidateExperience=experience,
        jobExperience=_normalize_text(getattr(job, "experience_required", "") or getattr(job, "experience_level", "") or ""),
        aiReasoning="LinkedIn X-Ray preview derived from title, skill, and location signals.",
        sourceBreakdown={
            "title": round(title_signal, 4),
            "skills": round(skill_signal, 4),
            "location": round(location_signal, 4),
            "company": round(company_signal, 4),
        },
    )

    return CandidateResult(
        id=linkedin_url or candidate.get("id") or f"xray-{index}",
        name=_normalize_text(candidate.get("name") or candidate.get("full_name") or title or "Unknown Candidate"),
        role=title or getattr(job, "title", "") or "Unknown Role",
        company=company or "",
        email="",
        isMockEmail=False,
        headline=title or company,
        location=location,
        yearsExperience=0.0,
        skills=skills,
        summary=summary,
        education=[],
        projects=[],
        certifications=[],
        companiesHistory=[],
        domainExperience=[],
        resumeText="",
        profileData={
            "linkedin_url": linkedin_url,
            "identity": identity.to_dict(),
            "source_provider": source_provider,
            "source_provider_label": "xray_apollo",
            "source_query": source_query,
            "source_timestamp": source_timestamp,
            "current_company": company,
            "inferred_experience": experience,
            "snippet": summary,
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
        source="linkedin_xray",
        sourceProvider=source_provider,
        sourceQuery=source_query,
        sourceTimestamp=source_timestamp,
        sourceType="linkedin_xray",
        linkedinUrl=linkedin_url,
        currentCompany=company,
        inferredExperience=experience,
    )


def discover_xray_candidates(
    *,
    job: Any,
    intake: dict[str, Any] | None = None,
    limit: int = 10,
    pages_per_query: int = 1,
    recruiter_preferences: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if SOURCE_PROVIDER != "xray_apollo":
        logger.info("[xray] skipped source_provider=%s", SOURCE_PROVIDER)
        return []
    if not XRAY_ENABLED or not SERPAPI_ENABLED:
        logger.info("[xray] skipped enabled=%s serpapi_enabled=%s", XRAY_ENABLED, SERPAPI_ENABLED)
        return []

    logger.info(
        "[xray] discovery_started job_id=%s role=%s limit=%s pages_per_query=%s apollo_enrichment_enabled=%s",
        getattr(job, "id", ""),
        _normalize_text(getattr(job, "title", "")),
        limit,
        pages_per_query,
        APOLLO_ENRICHMENT_ENABLED,
    )
    log_metric(
        "xray_discovery_started",
        job_id=getattr(job, "id", ""),
        limit=limit,
        pages_per_query=pages_per_query,
    )

    queries = build_linkedin_xray_queries(
        role=_normalize_text((intake or {}).get("role") or getattr(job, "title", "") or ""),
        seniority=_normalize_text((intake or {}).get("seniority") or getattr(job, "experience_level", "") or ""),
        skills=[str(skill).strip() for skill in ((intake or {}).get("skills") or []) if str(skill).strip()] if isinstance((intake or {}).get("skills"), list) else [token.strip() for token in _normalize_text((intake or {}).get("skills") or "").split(",") if token.strip()],
        location=_normalize_text((intake or {}).get("location") or getattr(job, "location", "") or ""),
        company_stage=_normalize_text((intake or {}).get("company_stage") or ""),
        hiring_preferences=_normalize_text((intake or {}).get("hiring_preferences") or ""),
        industry=_normalize_text((intake or {}).get("industry") or ""),
        leadership_expectations=_normalize_text((intake or {}).get("leadership_expectations") or ""),
        recruiter_preferences=recruiter_preferences,
    )
    for query in queries:
        logger.info("[xray_query] job_id=%s query=%s", getattr(job, "id", ""), query)

    candidates = discover_linkedin_xray_candidates(
        job=job,
        intake=intake,
        limit=limit,
        pages_per_query=pages_per_query,
        recruiter_preferences=recruiter_preferences,
    )

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for candidate in candidates:
        identity = build_candidate_identity(candidate=candidate, source_provider="xray_apollo", source_query=queries[0] if queries else "")
        linkedin_url = identity.canonical_linkedin_url
        key = identity.identity_fingerprint or linkedin_url.lower() or identity.normalized_name.lower()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        normalized.append(
            {
                **candidate,
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
            }
        )

    logger.info("[xray_candidate_count] job_id=%s raw=%s deduped=%s", getattr(job, "id", ""), len(candidates), len(normalized))
    logger.info("[xray_deduped] job_id=%s count=%s", getattr(job, "id", ""), len(normalized))
    log_metric("xray_candidates_found", job_id=getattr(job, "id", ""), count=len(normalized))
    return normalized


def build_xray_candidate_results(
    *,
    job: Any,
    candidates: list[dict[str, Any]],
    limit: int = 12,
) -> list[CandidateResult]:
    ranked = [_build_preview_result(job=job, candidate=candidate, index=index) for index, candidate in enumerate(candidates, start=1)]
    ranked.sort(key=lambda candidate: (-float(candidate.explanation.finalScore if candidate.explanation else 0.0), -float(candidate.fitScore or 0.0), candidate.name or candidate.id))
    return ranked[: max(1, limit)]
