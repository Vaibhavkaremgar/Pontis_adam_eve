"""
candidate_semantic_document.py

Builds a deterministic semantic text document from a ranked CandidateResult
(or dict) for embedding into Qdrant as persistent candidate memory.

No LLM, no external calls — only fields already present after sourcing/ranking.
Called once per ranked candidate, after rerank, before Qdrant upsert.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any


# ── helpers ───────────────────────────────────────────────────────────────────

def _t(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _resolve(source: Any, *keys: str) -> str:
    if isinstance(source, dict):
        for key in keys:
            v = source.get(key)
            if isinstance(v, str) and v.strip():
                return _t(v)
    else:
        for key in keys:
            v = getattr(source, key, None)
            if isinstance(v, str) and v.strip():
                return _t(v)
    return ""


def _resolve_float(source: Any, *keys: str) -> float | None:
    if isinstance(source, dict):
        for key in keys:
            v = source.get(key)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
    else:
        for key in keys:
            v = getattr(source, key, None)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
    return None


def _list_clean(values: Any, *, limit: int = 8) -> list[str]:
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


def _linkedin_url(candidate: Any) -> str:
    direct = _resolve(candidate, "linkedinUrl", "linkedin_url", "linkedin")
    if direct and "linkedin.com/" in direct.lower():
        return direct.rstrip("/")
    profile_data = None
    if isinstance(candidate, dict):
        profile_data = candidate.get("profileData") or candidate.get("rawDiscovery") or {}
    else:
        profile_data = getattr(candidate, "profileData", None) or getattr(candidate, "rawDiscovery", None) or {}
    if isinstance(profile_data, dict):
        for key in ("linkedin_url", "linkedinUrl", "linkedin", "source_url"):
            v = _t(profile_data.get(key) or "")
            if v and "linkedin.com/" in v.lower():
                return v.rstrip("/")
    return ""


# ── stable point identity ─────────────────────────────────────────────────────

def sourced_candidate_point_id(candidate: Any) -> int:
    """
    Derive a stable uint64 Qdrant point ID for a sourced X-Ray candidate.

    Identity priority:
      1. Normalized LinkedIn URL   — canonical across jobs
      2. Internal candidate_id     — stable UUID
      3. Deterministic hash of name+company fallback

    This ensures the same LinkedIn profile never gets a duplicate vector
    even if sourced for multiple different jobs.
    """
    linkedin = _linkedin_url(candidate)
    if linkedin:
        seed = f"xray:linkedin:{linkedin.lower().strip('/')}"
    else:
        candidate_id = _resolve(candidate, "id", "candidate_id")
        if candidate_id:
            seed = f"xray:id:{candidate_id}"
        else:
            name = _resolve(candidate, "name", "full_name").lower()
            company = _resolve(candidate, "company", "currentCompany", "job_company_name").lower()
            seed = f"xray:profile:{name}|{company}"
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


# ── semantic document builder ─────────────────────────────────────────────────

def build_candidate_semantic_document(
    candidate: Any,
    *,
    job_id: str = "",
    company_id: str = "",
    request_source: str = "",
    role_query: str = "",
) -> dict[str, Any]:
    """
    Build a semantic text document + Qdrant payload dict from a ranked candidate.

    Returns:
        {
            "semantic_text": str,       -- embed this
            "payload": dict,            -- store as Qdrant payload
        }

    Does NOT embed — caller embeds and upserts.
    """
    # Core identity fields
    candidate_id = _resolve(candidate, "id", "candidate_id")
    name = _resolve(candidate, "name", "full_name") or "Unknown Candidate"
    role = _resolve(candidate, "role", "headline", "job_title", "title") or ""
    company = _resolve(candidate, "company", "currentCompany", "job_company_name") or ""
    location = _resolve(candidate, "location") or ""
    linkedin_url = _linkedin_url(candidate)
    summary = _resolve(candidate, "summary") or ""

    # Scores
    fit_score = _resolve_float(candidate, "fitScore", "fit_score") or 0.0
    years_experience = _resolve_float(candidate, "yearsExperience", "years_experience")

    # Skills
    raw_skills: list[str] = []
    if isinstance(candidate, dict):
        raw_skills = candidate.get("skills") or []
    else:
        raw_skills = getattr(candidate, "skills", None) or []
    skills = _list_clean(raw_skills, limit=10)

    # Explanation fields
    explanation = None
    if isinstance(candidate, dict):
        explanation = candidate.get("explanation")
    else:
        explanation = getattr(candidate, "explanation", None)

    matched_skills: list[str] = []
    final_score: float = fit_score / 5.0 if fit_score > 0 else 0.0
    semantic_score: float = 0.0
    ai_reasoning: str = ""
    experience_label: str = ""
    source_breakdown: dict[str, Any] = {}

    if explanation is not None:
        matched_skills = _list_clean(
            explanation.get("skillsMatched") if isinstance(explanation, dict)
            else getattr(explanation, "skillsMatched", None)
        )
        experience_label = _resolve(explanation, "candidateExperience", "experienceMatch") or ""
        ai_reasoning = _resolve(explanation, "aiReasoning") or ""
        final_score_raw = _resolve_float(explanation, "finalScore")
        if final_score_raw is not None:
            final_score = final_score_raw
        semantic_score = _resolve_float(explanation, "semanticScore") or 0.0
        if isinstance(explanation, dict):
            source_breakdown = explanation.get("sourceBreakdown") or {}
        else:
            source_breakdown = getattr(explanation, "sourceBreakdown", None) or {}

    if not experience_label and years_experience is not None:
        yr_int = int(years_experience)
        experience_label = f"{yr_int} year{'s' if yr_int != 1 else ''}"

    if not experience_label:
        experience_label = _resolve(candidate, "inferredExperience") or ""

    # Source metadata
    source_provider = _resolve(candidate, "sourceProvider", "source_provider") or "xray"
    source_query = _resolve(candidate, "sourceQuery", "source_query") or role_query
    source_type = _resolve(candidate, "sourceType", "source_type", "source") or "xray"
    snippet_quality = _resolve(candidate, "snippetQuality", "snippet_quality") or "partial"

    # ── Build semantic text ───────────────────────────────────────────────────
    # Deterministic prose optimized for cosine similarity against job JD text.
    parts: list[str] = []
    if name and name != "Unknown Candidate":
        parts.append(f"Candidate: {name}.")
    if role:
        parts.append(f"Current role: {role}.")
    if company:
        parts.append(f"Current company: {company}.")
    if location:
        parts.append(f"Location: {location}.")
    if experience_label:
        parts.append(f"Experience: {experience_label}.")
    if matched_skills:
        parts.append(f"Matched skills: {', '.join(matched_skills[:6])}.")
    elif skills:
        parts.append(f"Skills: {', '.join(skills[:6])}.")
    if summary:
        # Truncate to 400 chars to keep embedding focused
        truncated_summary = summary[:400].rstrip(" .,;")
        parts.append(f"Profile summary: {truncated_summary}.")
    if ai_reasoning:
        truncated_reasoning = ai_reasoning[:280].rstrip(" .,;")
        parts.append(f"Ranking signal: {truncated_reasoning}.")

    semantic_text = " ".join(parts).strip() or f"Candidate profile: {name}. Role: {role}. Company: {company}."

    # ── Build Qdrant payload ──────────────────────────────────────────────────
    now_iso = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        # Identity
        "candidate_id": candidate_id,
        "candidate_name": name,
        "linkedin_url": linkedin_url or None,
        # Profile
        "headline": role or None,
        "role": role or None,
        "company": company or None,
        "location": location or None,
        # Scores
        "fit_score": round(fit_score, 2),
        "final_score": round(final_score, 4),
        "semantic_score": round(semantic_score, 4),
        # Skills
        "matched_skills": matched_skills[:6],
        "all_skills": skills[:10],
        # Experience
        "years_experience": years_experience,
        "experience_label": experience_label or None,
        # Source context
        "job_id": job_id or None,
        "company_id": company_id or None,
        "source_provider": source_provider,
        "source_type": source_type,
        "source_query": source_query or None,
        "request_source": request_source or None,
        "snippet_quality": snippet_quality,
        # Ranking metadata
        "source_breakdown": source_breakdown or None,
        # Timestamps
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    return {
        "semantic_text": semantic_text,
        "payload": payload,
    }


# ── example output (for documentation / tests) ───────────────────────────────

EXAMPLE_OUTPUT = {
    "semantic_text": (
        "Candidate: Jane Smith. Current role: Senior Backend Engineer. "
        "Current company: Stripe. Location: San Francisco, CA. "
        "Experience: 6 years. Matched skills: Python, FastAPI, PostgreSQL, AWS. "
        "Profile summary: Backend engineer with 6 years building payment infrastructure at scale. "
        "Ranking signal: Strong semantic match on Python, distributed systems, and payment domain."
    ),
    "payload": {
        "candidate_id": "3f2e1d...",
        "candidate_name": "Jane Smith",
        "linkedin_url": "https://www.linkedin.com/in/janesmith",
        "headline": "Senior Backend Engineer",
        "role": "Senior Backend Engineer",
        "company": "Stripe",
        "location": "San Francisco, CA",
        "fit_score": 4.2,
        "final_score": 0.8412,
        "semantic_score": 0.7810,
        "matched_skills": ["Python", "FastAPI", "PostgreSQL", "AWS"],
        "all_skills": ["Python", "FastAPI", "PostgreSQL", "AWS", "Redis", "Kafka"],
        "years_experience": 6.0,
        "experience_label": "6 years",
        "job_id": "job-uuid",
        "company_id": "company-uuid",
        "source_provider": "serpapi",
        "source_type": "xray",
        "source_query": "site:linkedin.com/in backend engineer python",
        "request_source": "ui",
        "snippet_quality": "rich",
        "source_breakdown": {"vector": 0.78, "lexical": 0.62},
        "created_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-01T00:00:00+00:00",
    },
}
