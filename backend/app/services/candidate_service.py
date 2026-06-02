from __future__ import annotations

import json
import logging
import math
import random
import re
from time import perf_counter
from collections import defaultdict
from statistics import mean, pstdev
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import (
    APP_ENV,
    ENABLE_HARD_FILTERING,
    ENABLE_FAKE_EMAILS,
    FEEDBACK_WEIGHTS,
    GROQ_API_KEY,
    EMBEDDING_VERSION,
    OPEN_ROUTER_API,
    SOURCE_PROVIDER,
    USE_INTERNAL_CANDIDATE_DB,
    MIN_SKILL_MATCH_THRESHOLD,
    PDL_SEARCH_SIZE,
    RLHF_FEEDBACK_HALF_LIFE_DAYS,
    RANKING_WEIGHTS,
    SCORING_DEFAULT_MODE,
    SERPAPI_ENABLED,
)
from app.db.repositories import (
    ATSExportRepository,
    CandidateFeedbackRepository,
    CandidateProfileRepository,
    CandidateSelectionSessionRepository,
    CompanyRepository,
    InterviewRepository,
    JobRepository,
    OutreachEventRepository,
    RankingExplanationRepository,
    RankingRunRepository,
    ScoringProfileRepository,
    _candidate_email_value,
    CandidateSelectionSessionEntity,
)
from app.schemas.candidate import CandidateExplanation, CandidateRankingDebug, CandidateResult
from app.services.candidate_text import build_candidate_text
from app.services.ats_lifecycle_service import get_candidate_ats_state, transition_candidate_ats_state
from app.services.ats.service import export_candidate_to_ats
from app.services.embedding_service import embed, preload_sample_candidate_embeddings
from app.services.prompt_sanitizer import sanitize_prompt_block, sanitize_prompt_text
from app.services.evaluation_service import record_candidate_fetch, record_shortlist_event
from app.services.llm_service import generate
from app.services.metrics_service import log_metric
from app.services.lifecycle_service import record_job_lifecycle_event
from app.services.notification_service import build_slot_booking_payload, generate_workflow_token, upsert_notification_workflow_token
from app.services.pdl_service import fetch_candidates_with_filters, is_pdl_disabled
from app.services.ranking_service import build_match_explanation, compute_final_score, compute_match_score
from app.services.ranking.semantic_reranking_service import rerank_xray_candidates
from app.services.retrieval_quality_service import rerank_candidates, retrieval_explanation
from app.services.recruiter_preference_service import (
    compute_recruiter_score_details,
    map_experience_to_bucket,
    load_recruiter_preference_profile,
    update_recruiter_preferences,
)
from app.services.ranking.models import coerce_candidate_explanation, ranked_candidate_final_score, ranked_candidate_sort_key
from app.services.sourcing.xray_service import build_xray_candidate_results, discover_xray_candidates
from app.services.serpapi_sourcing_service import discover_linkedin_xray_candidates
from app.services.retrieval_quality_service import hybrid_retrieval_score
from app.services.skill_normalizer import normalize_skills, parse_experience
from app.services.qdrant_service import (
    delete_candidate_vectors,
    ensure_all_collections,
    is_qdrant_search_error_active,
    last_qdrant_search_error,
    search_candidate_chunks,
    search_internal_candidate_chunks,
    upsert_candidate_chunks,
)
from app.services.slack_service import notify_slack
from app.services.state_machine import assert_valid_transition, is_swipe_locked, swipe_to_status
from app.utils.exceptions import APIError
from app.utils.observability import emit_trace
from app.utils.text import average_vectors, chunk_text, cosine_similarity

logger = logging.getLogger(__name__)
LOCAL_SEARCH_LIMIT = 120
RESULT_LIMIT = 12
ADAPTIVE_THRESHOLD_FLOOR = 0.45
ADAPTIVE_THRESHOLD_CEILING = 0.86
PDL_RETRY_BACKOFF_ON_QDRANT_ERROR_SECONDS = 180
EXPLORATION_RATE_FLOOR = 0.10
EXPLORATION_RATE_CEILING = 0.20
_last_pdl_attempt_when_qdrant_error: datetime | None = None

_CANDIDATE_REFRESH_VOLATILE_KEYS = {
    "created_at",
    "last_updated",
    "lastupdated",
    "source_timestamp",
    "sourcetimestamp",
    "updated_at",
    "updatedat",
}


def _candidate_refresh_fingerprint(value: Any) -> str:
    def _prune(item: Any) -> Any:
        if isinstance(item, dict):
            pruned: dict[str, Any] = {}
            for key, nested in item.items():
                key_text = str(key or "").strip()
                if key_text.lower() in _CANDIDATE_REFRESH_VOLATILE_KEYS:
                    continue
                pruned[key_text] = _prune(nested)
            return pruned
        if isinstance(item, list):
            return [_prune(entry) for entry in item]
        return item

    try:
        return json.dumps(_prune(value), sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    except Exception:
        return str(value)

SKILL_SYNONYMS = {
    "js": "javascript",
    "nodejs": "node",
    "node.js": "node",
    "py": "python",
    "postgresql": "postgres",
    "aws": "amazon web services",
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "k8s": "kubernetes",
}


@dataclass
class ScoringWeights:
    pdl: float
    semantic: float
    skill: float
    recency: float
    feedback_bias: float
    elite_reasoning_bonus: float


@dataclass
class RankingWeights:
    similarity: float
    skill_overlap: float
    experience: float


@dataclass
class ModeConfig:
    mode: str
    top_k: int
    min_skill_match_threshold: int
    use_hard_filtering: bool
    ranking_weights: RankingWeights
    strategy: str


@dataclass
class FeedbackLearningContext:
    candidate_feedback: dict[str, float]
    candidate_accept_counts: dict[str, int]
    candidate_reject_counts: dict[str, int]
    global_skill_bias: dict[str, float]
    global_role_bias: dict[str, float]
    preferred_tokens: list[str]
    preferred_roles: list[str]
    learned_query_tokens: list[str]
    job_success_rate: float
    global_success_rate: float


@dataclass
class ExplorationContext:
    rate: float
    system_confidence: float = 0.0
    used: int = 0
    total: int = 0


def _safe_commit(db: Session, *, context: str, job_id: str) -> bool:
    try:
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        logger.warning("%s_commit_failed job_id=%s error=%s", context, job_id, str(exc))
        log_metric(
            "db_commit_failed",
            context=context,
            job_id=job_id,
            error_type=type(exc).__name__,
        )
        return False


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _normalize_list(values: Any, *, max_items: int = 20) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _normalize_text(item)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
        if len(normalized) >= max_items:
            break
    return normalized


def _normalize_job_filters(
    job,
    *,
    preferred_tokens: list[str] | None = None,
    preferred_roles: list[str] | None = None,
) -> dict:
    structured = getattr(job, "structured_data", None)
    if not isinstance(structured, dict):
        structured = {}
    structured_skills = [
        str(skill).strip().lower()
        for skill in (structured.get("skills_required") or structured.get("skills") or getattr(job, "skills_required", None) or [])
        if str(skill).strip()
    ]
    fallback_skills = [skill.strip().lower() for skill in job.description.replace("\n", " ").split() if len(skill) > 3][:5]
    learned_skills = [token for token in (preferred_tokens or []) if token and token not in structured_skills][:3]
    return {
        "role": job.title,
        "location": job.location,
        "compensation": getattr(job, "compensation", "") or structured.get("compensation") or structured.get("salary_range") or "",
        "experience": getattr(job, "experience_required", "") or structured.get("experienceRequired") or structured.get("experience_required") or getattr(job, "experience_level", ""),
        "skills": (structured_skills[:8] + learned_skills)[:10] or fallback_skills,
        "learned_query_tokens": list(preferred_tokens or []),
        "preferred_roles": [role for role in (preferred_roles or []) if role][:3],
    }


def _candidate_text(candidate: dict) -> str:
    name = str(candidate.get("full_name") or candidate.get("name") or "").strip()
    role = str(candidate.get("job_title") or candidate.get("title") or "").strip()
    company = str(candidate.get("job_company_name") or candidate.get("company") or "").strip()
    skills = ", ".join(str(s) for s in (candidate.get("skills") or []))
    experience = _candidate_experience_value(candidate)
    summary = str(candidate.get("summary") or candidate.get("bio") or candidate.get("experience_summary") or "").strip()
    return (
        f"Name: {name}\n"
        f"Role: {role}\n"
        f"Company: {company}\n"
        f"Skills: {skills}\n"
        f"Experience: {experience}\n"
        f"Summary: {summary}"
    )


def _candidate_embedding_text(candidate: dict) -> str:
    return build_candidate_text(
        {
            "name": candidate.get("full_name") or candidate.get("name") or "",
            "role": candidate.get("job_title") or candidate.get("title") or candidate.get("role") or "",
            "headline": candidate.get("headline") or candidate.get("job_title") or candidate.get("title") or "",
            "company": candidate.get("job_company_name") or candidate.get("company") or candidate.get("current_company") or "",
            "location": candidate.get("location_name")
            or candidate.get("location_locality")
            or candidate.get("location_region")
            or candidate.get("location_country")
            or candidate.get("location")
            or "",
            "skills": candidate.get("skills") or candidate.get("skills_required") or [],
            "experience": candidate.get("experience")
            or candidate.get("years_experience")
            or candidate.get("yearsExperience")
            or candidate.get("experience_summary")
            or "",
            "summary": candidate.get("summary") or candidate.get("bio") or candidate.get("experience_summary") or "",
            "companies": candidate.get("companies") or candidate.get("company_history") or candidate.get("companiesHistory") or [],
            "projects": candidate.get("projects") or [],
            "education": candidate.get("education") or [],
            "certifications": candidate.get("certifications") or [],
            "domain_experience": candidate.get("domain_experience") or candidate.get("domainExperience") or [],
            "raw_resume_text": candidate.get("raw_resume_text") or candidate.get("parsed_resume_text") or "",
        }
    )


def _candidate_role(candidate: dict) -> str:
    return str(candidate.get("job_title") or candidate.get("title") or "").strip()


def _candidate_name(candidate: dict, candidate_id: str) -> str:
    full_name = str(candidate.get("full_name") or candidate.get("name") or "").strip()
    if full_name:
        return full_name

    first = str(candidate.get("first_name") or "").strip()
    last = str(candidate.get("last_name") or "").strip()
    combined = " ".join(part for part in [first, last] if part).strip()
    if combined:
        return combined

    return f"Candidate {candidate_id[:8]}"


def _candidate_company(candidate: dict) -> str:
    return str(candidate.get("job_company_name") or candidate.get("company") or "").strip()


def _candidate_location(candidate: dict) -> str:
    for key in ("location_name", "location_locality", "location_region", "location_country", "location"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _candidate_experience_value(candidate: dict) -> str:
    for key in ("experience", "years_experience", "yearsExperience", "experience_summary", "experienceLevel", "experience_level"):
        value = candidate.get(key)
        if isinstance(value, (int, float)):
            return f"{float(value):g} years"
        if isinstance(value, str) and value.strip():
            return value.strip()
    text = " ".join(
        part
        for part in [
            str(candidate.get("summary") or ""),
            str(candidate.get("bio") or ""),
            str(candidate.get("experience_summary") or ""),
        ]
        if part
    ).strip()
    match = re.search(r"\b\d+\s*[-–]\s*\d+\s+years\b", text, flags=re.IGNORECASE) or re.search(
        r"\b\d+\+?\s+years\b", text, flags=re.IGNORECASE
    )
    return match.group(0) if match else ""


def _candidate_salary(candidate: dict) -> str:
    for key in ("compensation", "salary_range", "salary", "target_salary", "desired_compensation"):
        value = candidate.get(key)
        if isinstance(value, (int, float)):
            return f"{float(value):g}"
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _job_location(job) -> str:
    structured = getattr(job, "structured_data", None)
    if isinstance(structured, dict):
        for key in ("location", "remotePolicy", "remote_policy"):
            value = structured.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    value = getattr(job, "location", "")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _job_compensation(job) -> str:
    structured = getattr(job, "structured_data", None)
    if isinstance(structured, dict):
        for key in ("compensation", "salary_range", "salary", "target_salary"):
            value = structured.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    value = getattr(job, "compensation", "")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _text_alignment(left: str, right: str) -> float:
    left_text = _normalize_text(left).lower()
    right_text = _normalize_text(right).lower()
    if not left_text or not right_text:
        return 0.5
    if left_text == right_text:
        return 1.0
    left_tokens = set(_tokenize(left_text))
    right_tokens = set(_tokenize(right_text))
    if not left_tokens or not right_tokens:
        return 0.5
    return len(left_tokens.intersection(right_tokens)) / max(1, len(left_tokens.union(right_tokens)))


def _location_match(candidate_location: str, job_location: str) -> float:
    return _text_alignment(candidate_location, job_location)


def _salary_match(candidate_salary: str, job_salary: str) -> float:
    if not candidate_salary or not job_salary:
        return 0.5 if candidate_salary or job_salary else 0.0
    return _text_alignment(candidate_salary, job_salary)


def _normalize_identity_value(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(part for part in value.strip().lower().split() if part)


def _extract_candidate_email(candidate: dict) -> str:
    return _candidate_email_value(candidate)


def _candidate_lookup_value(candidate: Any, key: str) -> str:
    if isinstance(candidate, dict):
        return str(candidate.get(key) or "").strip()
    return str(getattr(candidate, key, "") or "").strip()


def ensure_candidate_email(candidate: Any) -> str:
    if isinstance(candidate, dict):
        existing = _extract_candidate_email(candidate)
    else:
        existing = _candidate_lookup_value(candidate, "email")
        if not existing:
            raw_data = getattr(candidate, "raw_data", None)
            if isinstance(raw_data, dict):
                existing = _extract_candidate_email(raw_data)
    if existing:
        return existing
    if not ENABLE_FAKE_EMAILS:
        return ""

    name = _candidate_lookup_value(candidate, "name") or _candidate_lookup_value(candidate, "full_name") or "candidate"
    candidate_id = (
        _candidate_lookup_value(candidate, "id")
        or _candidate_lookup_value(candidate, "candidate_id")
        or _candidate_lookup_value(candidate, "candidateId")
        or "000000"
    )
    safe_name = re.sub(r"[^a-z0-9]+", "", name.lower()) or "candidate"
    safe_id = re.sub(r"[^a-z0-9]+", "", candidate_id.lower())[:6] or "000000"
    return f"{safe_name}_{safe_id}@test.local"


def _normalize_identity_url(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.strip().lower()
    if not normalized:
        return ""
    normalized = re.sub(r"^https?://", "", normalized)
    normalized = re.sub(r"^www\.", "", normalized)
    normalized = normalized.rstrip("/")
    return normalized


def _candidate_url_identity(candidate: dict, *keys: str) -> str:
    for key in keys:
        value = candidate.get(key)
        if isinstance(value, str):
            normalized = _normalize_identity_url(value)
            if normalized:
                return normalized
    return ""


def _candidate_profile_block(candidate: Any) -> dict[str, Any]:
    blocks: dict[str, Any] = {}
    if isinstance(candidate, dict):
        for key in ("profileData", "rawDiscovery", "profile_data", "raw_discovery"):
            value = candidate.get(key)
            if isinstance(value, dict):
                blocks.update(value)
        return blocks

    for key in ("profileData", "rawDiscovery", "profile_data", "raw_discovery"):
        value = getattr(candidate, key, None)
        if isinstance(value, dict):
            blocks.update(value)
    return blocks


def _candidate_profile_url(candidate: Any) -> str:
    profile = _candidate_profile_block(candidate)
    if isinstance(candidate, dict):
        direct_url = _candidate_url_identity(
            candidate,
            "linkedin_url",
            "linkedinUrl",
            "linkedin",
            "source_url",
            "sourceUrl",
        )
    else:
        direct_url = _candidate_url_identity(
            candidate.model_dump() if hasattr(candidate, "model_dump") else {},
            "linkedin_url",
            "linkedinUrl",
            "linkedin",
            "source_url",
            "sourceUrl",
        )
    nested_url = _candidate_url_identity(
        profile,
        "linkedin_url",
        "linkedinUrl",
        "linkedin",
        "profile_url",
        "profileUrl",
        "source_url",
        "sourceUrl",
    )
    url = direct_url or nested_url
    if not url:
        return ""
    if "/in/" not in url.lower() or "/jobs/" in url.lower():
        return ""
    return url


def _candidate_display_name(candidate: Any) -> str:
    if isinstance(candidate, dict):
        raw = candidate.get("name") or candidate.get("full_name") or candidate.get("fullName") or candidate.get("title") or candidate.get("headline") or ""
    else:
        raw = getattr(candidate, "name", "") or getattr(candidate, "full_name", "") or getattr(candidate, "fullName", "") or getattr(candidate, "title", "") or getattr(candidate, "headline", "") or ""
    if raw:
        return _normalize_text(raw)
    profile = _candidate_profile_block(candidate)
    for key in ("candidateHeadline", "candidate_headline", "title", "headline"):
        value = profile.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_text(value)
    return ""


def _is_reviewable_candidate(candidate: Any) -> bool:
    email = ""
    is_mock_email = False
    source_provider = ""
    source_type = ""
    linkedin_url = ""
    display_name = _candidate_display_name(candidate)
    if isinstance(candidate, dict):
        email = _extract_candidate_email(candidate)
        is_mock_email = bool(candidate.get("isMockEmail"))
        source_provider = _normalize_text(candidate.get("sourceProvider") or candidate.get("source_provider") or "").lower()
        source_type = _normalize_text(candidate.get("sourceType") or candidate.get("source_type") or "").lower()
        linkedin_url = _candidate_profile_url(candidate)
    else:
        email = str(getattr(candidate, "email", "") or "").strip().lower()
        is_mock_email = bool(getattr(candidate, "isMockEmail", False))
        source_provider = str(getattr(candidate, "sourceProvider", "") or getattr(candidate, "source_provider", "") or "").strip().lower()
        source_type = str(getattr(candidate, "sourceType", "") or getattr(candidate, "source_type", "") or "").strip().lower()
        linkedin_url = _candidate_profile_url(candidate)
    if is_mock_email:
        return False
    if email.endswith("@test.local"):
        return False
    if source_provider == "xray_apollo" or source_type == "linkedin_xray":
        # X-Ray candidates are reviewable as long as we have a valid LinkedIn profile URL.
        # Some valid search hits do not carry a stable display name in the upstream payload,
        # and we do not want to drop the full deck because of that.
        return bool(linkedin_url)
    if not email:
        return False
    return True


def _extract_candidate_external_id(candidate: dict) -> str:
    for key in ("id", "external_id", "profile_id", "linkedin_id", "linkedin_url", "github_url"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _candidate_identity_key(candidate: dict) -> str:
    email = _extract_candidate_email(candidate)
    if email:
        return f"email:{email}"

    linkedin_url = _candidate_url_identity(candidate, "linkedin_url", "linkedinUrl", "linkedin")
    if linkedin_url:
        return f"linkedin:{linkedin_url}"

    github_url = _candidate_url_identity(candidate, "github_url", "githubUrl", "github")
    if github_url:
        return f"github:{github_url}"

    external_id = _extract_candidate_external_id(candidate)
    if external_id:
        return f"external:{external_id}"

    key = "|".join(
        part.lower()
        for part in [
            str(candidate.get("full_name") or candidate.get("name") or "").strip(),
            str(candidate.get("job_title") or candidate.get("title") or "").strip(),
            str(candidate.get("job_company_name") or candidate.get("company") or "").strip(),
        ]
        if part
    )
    return f"profile:{key or _candidate_text(candidate).lower()}"


def _candidate_skills(candidate: dict) -> list[str]:
    raw = candidate.get("skills") or []
    if not isinstance(raw, list):
        return []
    return [str(skill).strip() for skill in raw if str(skill).strip()]


def _candidate_summary(candidate: dict) -> str:
    summary = str(candidate.get("summary") or candidate.get("bio") or candidate.get("experience_summary") or "").strip()
    if summary:
        return summary

    company = _candidate_company(candidate)
    skills = _candidate_skills(candidate)
    experience = _candidate_experience_value(candidate)
    if company and skills:
        prefix = f"Currently at {company}"
        if experience:
            prefix += f" with {experience}"
        return f"{prefix}. Skills: {', '.join(skills[:6])}"
    if experience and skills:
        return f"Experience: {experience}. Skills: {', '.join(skills[:6])}"
    if skills:
        return f"Skills: {', '.join(skills[:6])}"
    if experience:
        return f"Experience: {experience}"
    return "Candidate profile sourced from People Data Labs."


def _candidate_profile_details(*, profile: Any | None = None, raw_data: Any | None = None) -> dict[str, Any]:
    source: dict[str, Any] = {}

    profile_raw_data = getattr(profile, "raw_data", None) if profile is not None else None
    if isinstance(profile_raw_data, dict):
        source.update(profile_raw_data)
    if isinstance(raw_data, dict):
        source.update(raw_data)

    def _find_node(node: Any, *, wanted: set[str]) -> Any:
        if isinstance(node, dict):
            for key, value in node.items():
                key_name = str(key).strip().lower()
                if key_name in wanted:
                    return value
                found = _find_node(value, wanted=wanted)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = _find_node(item, wanted=wanted)
                if found is not None:
                    return found
        return None

    def _first_text(node: Any) -> str:
        if isinstance(node, str):
            return _normalize_text(node)
        if isinstance(node, dict):
            for value in node.values():
                text = _first_text(value)
                if text:
                    return text
        elif isinstance(node, list):
            for item in node:
                text = _first_text(item)
                if text:
                    return text
        return ""

    def _collect_list_values(node: Any) -> list[str]:
        collected: list[str] = []
        seen: set[str] = set()

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)
            else:
                text = _normalize_text(value)
                if not text:
                    return
                key = text.lower()
                if key in seen:
                    return
                seen.add(key)
                collected.append(text)

        visit(node)
        return collected

    def _string_value(*keys: str) -> str:
        wanted = {key.lower() for key in keys}
        node = _find_node(source, wanted=wanted)
        if node is None:
            return ""
        if "email" in wanted:
            return _candidate_email_value(node)
        return _first_text(node)

    def _list_value(*keys: str) -> list[str]:
        wanted = {key.lower() for key in keys}
        node = _find_node(source, wanted=wanted)
        if node is None:
            return []
        collected = _collect_list_values(node)
        if collected:
            return collected
        return []

    years_experience_node = _find_node(source, wanted={"years_experience", "yearsexperience", "experience"})
    years_experience = years_experience_node if years_experience_node is not None else source.get("years_experience")
    if years_experience is None:
        years_experience = source.get("yearsExperience")
    if isinstance(years_experience, (dict, list)):
        years_experience = _first_text(years_experience)
    try:
        years_experience_value = float(years_experience or 0.0)
    except (TypeError, ValueError):
        match = re.search(r"\d+(?:\.\d+)?", _normalize_text(years_experience))
        years_experience_value = float(match.group(0)) if match else 0.0

    parsed_data = source.get("parsed_data") or source.get("parsedData") or {}
    if not isinstance(parsed_data, dict):
        parsed_data = {}

    email = _candidate_email_value(source)
    is_mock_email = email.endswith("@test.local") if email else False

    return {
        "email": email,
        "isMockEmail": is_mock_email,
        "headline": _string_value("headline", "title", "role"),
        "location": _string_value("location"),
        "yearsExperience": years_experience_value,
        "education": _list_value("education"),
        "projects": _list_value("projects"),
        "certifications": _list_value("certifications"),
        "companiesHistory": _list_value("companies", "company_history", "companyHistory"),
        "domainExperience": _list_value("domain_experience", "domainExperience"),
        "resumeText": _string_value("raw_resume_text", "rawResumeText"),
        "profileData": parsed_data,
    }


def _candidate_profile_display_name(profile: Any | None) -> str:
    if profile is None:
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
    return str(getattr(profile, "candidate_id", "") or "").strip()


def _candidate_experience(candidate: dict) -> str:
    for key in ("experience", "years_experience", "experience_summary", "experienceLevel", "experience_level"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    text = " ".join(
        part
        for part in [
            str(candidate.get("summary") or ""),
            str(candidate.get("bio") or ""),
            str(candidate.get("experience_summary") or ""),
        ]
        if part
    ).strip()
    match = re.search(r"\b\d+\s*[-–]\s*\d+\s+years\b", text, flags=re.IGNORECASE) or re.search(
        r"\b\d+\+?\s+years\b", text, flags=re.IGNORECASE
    )
    return match.group(0) if match else ""


def _candidate_freshness_score(candidate: Any) -> float:
    if candidate is None:
        return 0.0

    raw_values: list[Any]
    if isinstance(candidate, dict):
        raw_values = [
            candidate.get("last_refreshed_at"),
            candidate.get("lastRefreshedAt"),
            candidate.get("updated_at"),
            candidate.get("updatedAt"),
            candidate.get("last_updated"),
            candidate.get("lastUpdated"),
        ]
    else:
        raw_values = [
            getattr(candidate, "last_refreshed_at", None),
            getattr(candidate, "updated_at", None),
            getattr(candidate, "last_updated", None),
        ]

    timestamps: list[datetime] = []
    for value in raw_values:
        if isinstance(value, datetime):
            timestamps.append(value if value.tzinfo else value.replace(tzinfo=timezone.utc))
        elif isinstance(value, str) and value.strip():
            try:
                parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError:
                continue
            timestamps.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))

    if not timestamps:
        return 0.35

    latest = max(timestamps)
    age_days = max(0.0, (datetime.now(timezone.utc) - latest.astimezone(timezone.utc)).total_seconds() / 86400.0)
    freshness = math.exp(-age_days / 21.0)
    return max(0.0, min(1.0, freshness))


def _job_experience(job) -> str:
    structured = getattr(job, "structured_data", None)
    if isinstance(structured, dict):
        for key in ("experience_required", "experienceRequired", "experience", "experience_level"):
            value = structured.get(key)
            if isinstance(value, (int, float)):
                return f"{float(value):g} years"
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("experience_required", "experience_level", "experienceRequired", "experience"):
        value = getattr(job, key, "")
        if isinstance(value, (int, float)):
            return f"{float(value):g} years"
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _parse_year_span(text: str) -> tuple[float | None, float | None]:
    normalized = (text or "").strip()
    if not normalized:
        return (None, None)

    range_match = re.search(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s+years?", normalized, flags=re.IGNORECASE)
    if range_match:
        return (float(range_match.group(1)), float(range_match.group(2)))

    plus_match = re.search(r"(\d+(?:\.\d+)?)\+?\s+years?", normalized, flags=re.IGNORECASE)
    if plus_match:
        value = float(plus_match.group(1))
        return (value, value)

    single_match = re.search(r"(\d+(?:\.\d+)?)", normalized)
    if single_match:
        value = float(single_match.group(1))
        return (value, value)

    return (None, None)


def _experience_match(candidate_experience: str, job_experience: str) -> float:
    candidate_range = _parse_year_span(candidate_experience)
    job_range = _parse_year_span(job_experience)

    candidate_min, candidate_max = candidate_range
    job_min, job_max = job_range

    if job_min is None and candidate_min is None:
        return 0.5 if candidate_experience or job_experience else 0.0

    if candidate_min is None:
        return 0.35 if job_experience else 0.0

    if job_min is None:
        return 0.55 if candidate_experience else 0.0

    if job_max is None:
        job_max = job_min
    if candidate_max is None:
        candidate_max = candidate_min

    if candidate_max < job_min:
        gap = job_min - candidate_max
        return max(0.0, 1.0 - min(1.0, gap / max(job_min, 1.0)))
    if candidate_min > job_max:
        gap = candidate_min - job_max
        return max(0.0, 1.0 - min(1.0, gap / max(candidate_min, 1.0)))

    return 1.0


def _experience_match_summary(candidate_experience: str, job_experience: str) -> str:
    candidate_text = candidate_experience.strip()
    job_text = job_experience.strip()
    if candidate_text and job_text:
        return f"{candidate_text} vs {job_text}"
    if candidate_text:
        return candidate_text
    if job_text:
        return f"Matches {job_text} requirement"
    return "Experience not explicitly stated"


def _matched_skills(job_skills: list[str] | set[str], candidate_skills: list[str]) -> list[str]:
    job_tokens = normalize_skills(list(job_skills) if isinstance(job_skills, set) else job_skills)
    candidate_tokens = normalize_skills(candidate_skills)
    matches = sorted(job_tokens.intersection(candidate_tokens))
    return matches[:8]


def _candidate_id(candidate: dict) -> str:
    return str(uuid5(NAMESPACE_URL, _candidate_identity_key(candidate)))


def _strategy_from_score(score_0_to_5: float) -> str:
    if score_0_to_5 >= 4:
        return "HIGH"
    if score_0_to_5 >= 2.5:
        return "MEDIUM"
    return "LOW"


def _decision_from_score(final_score_0_to_1: float) -> str:
    if final_score_0_to_1 >= 0.75:
        return "strong_match"
    if final_score_0_to_1 >= 0.45:
        return "potential"
    return "weak"


def _normalize_similarity(cosine_value: float) -> float:
    normalized = (cosine_value + 1.0) / 2.0
    return max(0.0, min(1.0, normalized))


def _pdl_relevance(candidate: dict, index: int, total: int) -> float:
    for key in ("score", "_score", "relevance", "match_score"):
        value = candidate.get(key)
        if isinstance(value, (int, float)):
            numeric = float(value)
            if numeric > 1 and numeric <= 100:
                numeric /= 100.0
            elif numeric > 100:
                numeric /= 1000.0
            return max(0.0, min(1.0, numeric))

    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, (total - index) / total))


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    return [token for token in re.findall(r"[a-zA-Z0-9\.\+#]+", text.lower()) if len(token) > 1]


def _canonical_token(token: str) -> str:
    normalized = token.strip().lower()
    return SKILL_SYNONYMS.get(normalized, normalized)


def _normalized_skill_tokens(values: list[str]) -> set[str]:
    result: set[str] = set()
    for value in values:
        for token in _tokenize(value):
            result.add(_canonical_token(token))
    return result


def _job_skill_set(job) -> set[str]:
    structured = getattr(job, "structured_data", None)
    structured_skills = []
    if isinstance(structured, dict):
        structured_skills = [str(skill) for skill in (structured.get("skills") or structured.get("skills_required") or [])]
    structured_skill_tokens = normalize_skills(structured_skills or list(getattr(job, "skills_required", None) or []))
    if structured_skill_tokens:
        return structured_skill_tokens

    responsibilities = [str(item) for item in (getattr(job, "responsibilities", None) or []) if str(item).strip()]
    experience_level = _job_experience(job)
    return _normalized_skill_tokens([job.title, job.description, experience_level, *structured_skills, *responsibilities])


def _job_requirement_skills(job) -> list[str]:
    structured = getattr(job, "structured_data", None)
    raw_skills: list[str] = []
    if isinstance(structured, dict):
        raw_skills.extend(str(skill) for skill in (structured.get("skills") or structured.get("skills_required") or []) if str(skill).strip())
    raw_skills.extend(str(skill) for skill in (getattr(job, "skills_required", None) or []) if str(skill).strip())
    normalized = sorted(normalize_skills(raw_skills))
    return normalized or _normalize_list(raw_skills)


def _job_min_experience_years(job) -> int:
    structured = getattr(job, "structured_data", None)
    experience_text = ""
    if isinstance(structured, dict):
        experience_text = str(structured.get("experience") or structured.get("experience_level") or "").strip()
    if not experience_text:
        experience_text = _job_experience(job)
    return parse_experience(experience_text)


def _candidate_skill_values(candidate: dict, *, fallback_profile=None) -> list[str]:
    if fallback_profile is not None:
        profile_skills = getattr(fallback_profile, "skills", None) or []
        if isinstance(profile_skills, list) and profile_skills:
            return [str(skill).strip() for skill in profile_skills if str(skill).strip()]
    skills = candidate.get("skills") or []
    if isinstance(skills, list):
        return [str(skill).strip() for skill in skills if str(skill).strip()]
    return []


def _candidate_experience_years(candidate: dict, *, fallback_profile=None) -> int:
    experience_text = ""
    if fallback_profile is not None:
        raw_data = getattr(fallback_profile, "raw_data", None)
        if isinstance(raw_data, dict):
            years_value = raw_data.get("years_experience") or raw_data.get("yearsExperience")
            if isinstance(years_value, (int, float)):
                return int(max(0.0, float(years_value)))
            experience_text = _candidate_experience_value(raw_data)
        if not experience_text:
            experience_text = str(getattr(fallback_profile, "summary", "") or "").strip()
    if not experience_text:
        years_value = candidate.get("years_experience") or candidate.get("yearsExperience")
        if isinstance(years_value, (int, float)):
            return int(max(0.0, float(years_value)))
        experience_text = _candidate_experience_value(candidate)
    return parse_experience(experience_text)


def passes_hard_filters(
    candidate,
    job_skills: list[str],
    min_experience: int,
    *,
    min_skill_matches: int = MIN_SKILL_MATCH_THRESHOLD,
) -> bool:
    candidate_skills = normalize_skills(candidate.get("candidate_skills") or [])
    job_skill_set = normalize_skills(job_skills)

    if job_skill_set:
        required_matches = max(1, min(min_skill_matches, len(job_skill_set)))
        skill_match = len(candidate_skills & job_skill_set) >= required_matches
    else:
        skill_match = True

    candidate_experience_years = int(candidate.get("candidate_experience_years") or 0)
    experience_ok = candidate_experience_years >= min_experience if min_experience > 0 else True
    return skill_match and experience_ok


def _skill_overlap(job_skills: list[str] | set[str], candidate_skills: list[str]) -> float:
    job_skill_tokens = normalize_skills(list(job_skills) if isinstance(job_skills, set) else job_skills)
    if not job_skill_tokens:
        return 0.0
    candidate_skill_tokens = normalize_skills(candidate_skills)
    if not candidate_skill_tokens:
        return 0.0

    return max(0.0, min(1.0, len(job_skill_tokens.intersection(candidate_skill_tokens)) / len(job_skill_tokens)))


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _candidate_recency_score(candidate: dict) -> float:
    for key in (
        "last_updated",
        "updated",
        "updated_at",
        "data_source_updated_at",
        "last_seen",
    ):
        raw = candidate.get(key)
        if isinstance(raw, str):
            parsed = _parse_datetime(raw)
            if parsed:
                age_days = (datetime.now(timezone.utc) - parsed).days
                if age_days <= 30:
                    return 1.0
                if age_days <= 90:
                    return 0.8
                if age_days <= 180:
                    return 0.6
                if age_days <= 365:
                    return 0.4
                return 0.2
    return 0.5


def _embed_text(text: str) -> list[float]:
    safe = text.strip() or " "
    return list(embed(safe))


def _normalize_structured_items(values: Any) -> list[str]:
    if isinstance(values, list):
        return [str(item).strip() for item in values if str(item).strip()]
    if isinstance(values, str) and values.strip():
        return [values.strip()]
    return []


def _extract_voice_transcript(structured_data: Any) -> str:
    if not isinstance(structured_data, dict):
        return ""

    for key in ("voiceTranscript", "transcript", "voice_input", "voiceInput", "transcriptText"):
        value = structured_data.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_text(value)

    voice_extraction = structured_data.get("voiceExtraction")
    if isinstance(voice_extraction, dict):
        for key in ("transcript", "voiceTranscript", "voice_input", "voiceInput"):
            value = voice_extraction.get(key)
            if isinstance(value, str) and value.strip():
                return _normalize_text(value)
    return ""


def structured_data_to_text(structured_data: Any) -> str:
    if not isinstance(structured_data, dict):
        return ""

    voice_extraction = structured_data.get("voiceExtraction")
    if not isinstance(voice_extraction, dict):
        voice_extraction = {}

    job_block = structured_data.get("job") if isinstance(structured_data.get("job"), dict) else {}
    company_block = structured_data.get("company") if isinstance(structured_data.get("company"), dict) else {}
    if not job_block and isinstance(voice_extraction.get("job"), dict):
        job_block = voice_extraction.get("job") or {}
    if not company_block and isinstance(voice_extraction.get("company"), dict):
        company_block = voice_extraction.get("company") or {}

    role = _normalize_text(
        job_block.get("title")
        or structured_data.get("role")
        or structured_data.get("title")
        or voice_extraction.get("role")
    )
    skills = _normalize_list(
        job_block.get("skills_required")
        or structured_data.get("skills")
        or structured_data.get("skills_required")
        or voice_extraction.get("skills")
        or voice_extraction.get("skills_required")
    )
    experience = _normalize_text(
        job_block.get("experience_level")
        or structured_data.get("experience")
        or structured_data.get("experience_level")
        or voice_extraction.get("experience")
    )
    transcript = _extract_voice_transcript(structured_data)
    company_name = _normalize_text(
        company_block.get("name")
        or structured_data.get("companyName")
        or voice_extraction.get("companyName")
    )
    company_industry = _normalize_text(
        company_block.get("industry")
        or structured_data.get("industry")
        or voice_extraction.get("industry")
    )

    lines: list[str] = []
    if role:
        lines.append(f"Role: {role}")
    if skills:
        lines.append(f"Skills: {', '.join(skills)}")
    if experience:
        lines.append(f"Experience: {experience}")
    if company_name:
        lines.append(f"Company: {company_name}")
    if company_industry:
        lines.append(f"Industry: {company_industry}")
    if transcript:
        lines.append(f"Transcript: {transcript}")
    return "\n".join(lines).strip()


def build_job_text(job, structured_data: Any | None = None, transcript: str = "") -> str:
    resolved_structured_data = structured_data if isinstance(structured_data, dict) else getattr(job, "structured_data", None)
    if not isinstance(resolved_structured_data, dict):
        resolved_structured_data = {}

    transcript_text = _normalize_text(transcript) or _extract_voice_transcript(resolved_structured_data)
    role = _normalize_text(
        resolved_structured_data.get("role")
        or resolved_structured_data.get("title")
        or getattr(job, "title", "")
    )
    skills = _normalize_list(
        resolved_structured_data.get("skills")
        or resolved_structured_data.get("skills_required")
        or getattr(job, "skills_required", None)
    )
    experience = _normalize_text(
        resolved_structured_data.get("experience")
        or resolved_structured_data.get("experience_level")
        or resolved_structured_data.get("experienceRequired")
        or getattr(job, "experience_level", "")
        or getattr(job, "experience_required", "")
    )
    location = _normalize_text(
        resolved_structured_data.get("location")
        or getattr(job, "location", "")
    )
    compensation = _normalize_text(
        resolved_structured_data.get("compensation")
        or resolved_structured_data.get("salary_range")
        or getattr(job, "compensation", "")
    )
    work_authorization = _normalize_text(
        resolved_structured_data.get("workAuthorization")
        or resolved_structured_data.get("work_authorization")
        or getattr(job, "work_authorization", "")
    )
    remote_policy = _normalize_text(
        resolved_structured_data.get("remotePolicy")
        or resolved_structured_data.get("remote_policy")
        or getattr(job, "remote_policy", "")
    )
    responsibilities = _normalize_list(
        resolved_structured_data.get("responsibilities")
        or getattr(job, "responsibilities", None)
    )
    company_name = _normalize_text(
        resolved_structured_data.get("companyName")
        or resolved_structured_data.get("company")
        or getattr(getattr(job, "company", None), "name", "")
    )
    company_industry = _normalize_text(
        resolved_structured_data.get("industry")
        or getattr(getattr(job, "company", None), "industry", "")
    )
    company_description = _normalize_text(
        resolved_structured_data.get("companyDescription")
        or getattr(getattr(job, "company", None), "description", "")
    )
    original_jd = _normalize_text(getattr(job, "description", ""))
    if not original_jd:
        original_jd = _normalize_text(resolved_structured_data.get("description") or "")

    role_line = role or _normalize_text(getattr(job, "title", ""))
    skill_line = ", ".join(skills)
    job_text = (
        f"Title: {role_line}\n"
        f"Role: {role_line}\n"
        f"Experience: {experience}\n"
        f"Skills: {skill_line}\n\n"
        f"Responsibilities:\n" + ("\n".join(f"- {item}" for item in responsibilities) if responsibilities else "- Not specified") + "\n\n"
        f"Job Description:\n{original_jd}\n\n"
        f"Location: {location}\n"
        f"Compensation: {compensation}\n"
        f"Work Authorization: {work_authorization}\n"
        f"Remote Policy: {remote_policy}\n"
        f"Company: {company_name}\n"
        f"Industry: {company_industry}\n"
        f"Company Description: {company_description}\n\n"
        f"Voice Input:\n{transcript_text}"
    ).strip()
    if not job_text:
        job_text = original_jd or transcript_text or " "

    source = "structured_data" if role or skills or experience or location or compensation else "transcript" if transcript_text else "description"
    logger.info(
        "job_text_built job_id=%s source=%s has_structured_data=%s transcript_present=%s length=%s",
        getattr(job, "id", "unknown"),
        source,
        bool(role or skills or experience or location or compensation),
        bool(transcript_text),
        len(job_text),
    )
    return job_text


def _job_vector(job, feedback_learning: FeedbackLearningContext | None = None) -> list[float]:
    del feedback_learning
    job_text = build_job_text(
        job,
        structured_data=getattr(job, "structured_data", None),
        transcript=_extract_voice_transcript(getattr(job, "structured_data", None)),
    )
    vector = embed(job_text)
    logger.info("job_vector_created job_id=%s vector_length=%s", getattr(job, "id", "unknown"), len(vector))
    return vector


def _normalize_weights(pdl: float, semantic: float, skill: float, recency: float) -> tuple[float, float, float, float]:
    total = max(pdl + semantic + skill + recency, 1e-6)
    return (pdl / total, semantic / total, skill / total, recency / total)


def _load_scoring_weights(db: Session, *, job_id: str) -> ScoringWeights:
    profile = ScoringProfileRepository(db).get_or_create(job_id=job_id)
    pdl, semantic, skill, recency = _normalize_weights(
        profile.weight_pdl,
        profile.weight_semantic,
        profile.weight_skill,
        profile.weight_recency,
    )
    return ScoringWeights(
        pdl=pdl,
        semantic=semantic,
        skill=skill,
        recency=recency,
        feedback_bias=max(0.0, min(0.40, profile.feedback_bias)),
        elite_reasoning_bonus=max(0.0, min(0.25, profile.elite_reasoning_bonus)),
    )


def _normalize_weight_triplet(weights: dict[str, float]) -> RankingWeights:
    similarity = max(0.0, float(weights.get("similarity", RANKING_WEIGHTS["similarity"])))
    skill_overlap = max(0.0, float(weights.get("skill_overlap", RANKING_WEIGHTS["skill_overlap"])))
    experience = max(0.0, float(weights.get("experience", RANKING_WEIGHTS["experience"])))
    total = max(similarity + skill_overlap + experience, 1e-6)
    return RankingWeights(
        similarity=similarity / total,
        skill_overlap=skill_overlap / total,
        experience=experience / total,
    )


def _resolve_ranking_weights(job, *, default_weights: RankingWeights | None = None) -> RankingWeights:
    structured = getattr(job, "structured_data", None)
    override: dict[str, float] = {}
    if isinstance(structured, dict):
        raw_override = structured.get("rankingWeights") or structured.get("ranking_weights") or {}
        if isinstance(raw_override, dict):
            override = {
                key: value
                for key, value in raw_override.items()
                if key in {"similarity", "skill_overlap", "experience"} and isinstance(value, (int, float, str))
            }
    if override:
        return _normalize_weight_triplet(override)
    if default_weights is not None:
        return default_weights
    return _normalize_weight_triplet({})


def get_mode_config(mode: str | None) -> ModeConfig:
    normalized = (mode or "volume").strip().lower()
    if normalized == "elite":
        return ModeConfig(
            mode="elite",
            top_k=20,
            min_skill_match_threshold=2,
            use_hard_filtering=True,
            ranking_weights=_normalize_weight_triplet(
                {
                    "similarity": 0.55,
                    "skill_overlap": 0.30,
                    "experience": 0.15,
                }
            ),
            strategy="high_precision",
        )

    return ModeConfig(
        mode="volume",
        top_k=50,
        min_skill_match_threshold=1,
        use_hard_filtering=False,
        ranking_weights=_normalize_weight_triplet(
            {
                "similarity": 0.70,
                "skill_overlap": 0.20,
                "experience": 0.10,
            }
        ),
        strategy="high_volume",
    )


def _feedback_adjustment(feedback_signal: float | None, *, bias: float) -> float:
    return (feedback_signal or 0.0) * bias


def _feedback_outcome_multiplier(status: str | None) -> float:
    normalized = (status or "").strip().lower()
    if normalized in {"hired", "offer_accepted"}:
        return 1.8
    if normalized in {"interview_scheduled", "interviewed", "onsite", "final_round"}:
        return 1.4
    if normalized in {"outreach_sent", "selected"}:
        return 1.15
    if normalized in {"rejected", "declined"}:
        return 0.7
    return 1.0


def _feedback_success_value(feedback: str, status: str | None) -> float:
    action = feedback.strip().lower()
    if action == "accept":
        return _feedback_outcome_multiplier(status)
    if action == "reject":
        return 0.0
    return 0.0


def _feedback_signal_value(feedback: str, status: str | None) -> float:
    action = feedback.strip().lower()
    direction = FEEDBACK_WEIGHTS["accept"] if action == "accept" else FEEDBACK_WEIGHTS["reject"]
    return direction * _feedback_outcome_multiplier(status)


def _score_feedback_skills(skills: list[str], bias_map: dict[str, float]) -> float:
    tokens = normalize_skills(skills)
    if not tokens:
        tokens = _normalized_skill_tokens(skills)
    if not tokens:
        return 0.0
    values = [bias_map.get(token, 0.0) for token in tokens]
    if not values:
        return 0.0
    return sum(values) / max(1, len(values))


def _score_feedback_role(role: str, role_bias_map: dict[str, float]) -> float:
    role_tokens = _normalized_skill_tokens([role])
    if not role_tokens:
        return 0.0
    values = [role_bias_map.get(token, 0.0) for token in role_tokens]
    if not values:
        return 0.0
    return sum(values) / max(1, len(values))


def _candidate_rejection_penalty(candidate_id: str, feedback_learning: FeedbackLearningContext) -> float:
    accepts = feedback_learning.candidate_accept_counts.get(candidate_id, 0)
    rejects = feedback_learning.candidate_reject_counts.get(candidate_id, 0)
    total = accepts + rejects
    if total <= 0:
        return 0.0
    rejection_ratio = rejects / total
    confidence = min(1.0, total / 4.0)
    return max(0.0, min(0.25, rejection_ratio * confidence * 0.25))


def _selection_session_signal(session, candidate_id: str) -> float:
    if not session:
        return 0.0

    selected_ids = {str(value).strip() for value in (session.selected_candidate_ids or []) if str(value).strip()}
    rejected_ids = {str(value).strip() for value in (session.rejected_candidate_ids or []) if str(value).strip()}
    if candidate_id in selected_ids:
        return 1.0
    if candidate_id in rejected_ids:
        return -0.5

    for entry in reversed(list(session.batch_history or [])):
        if str(entry.get("selectedCandidateId") or "").strip() == candidate_id:
            return 1.0
        rejected_batch = {str(value).strip() for value in (entry.get("rejectedCandidateIds") or []) if str(value).strip()}
        if candidate_id in rejected_batch:
            return -0.5
    return 0.0


def _explanation_source_breakdown(
    *,
    vector_score: float = 0.0,
    lexical_score: float = 0.0,
    structured_score: float = 0.0,
    recruiter_score: float = 0.0,
    recency_score: float = 0.0,
    session_signal: float = 0.0,
    voice_score: float = 0.0,
    location_score: float = 0.0,
    salary_score: float = 0.0,
) -> dict[str, float]:
    return {
        "vector": round(max(0.0, min(1.0, vector_score)), 4),
        "lexical": round(max(0.0, min(1.0, lexical_score)), 4),
        "structured": round(max(0.0, min(1.0, structured_score)), 4),
        "recruiterPreference": round(max(0.0, min(1.0, recruiter_score)), 4),
        "freshness": round(max(0.0, min(1.0, recency_score)), 4),
        "selectionRound": round(max(-1.0, min(1.0, session_signal)), 4),
        "voiceInterview": round(max(0.0, min(1.0, voice_score)), 4),
        "location": round(max(0.0, min(1.0, location_score)), 4),
        "salary": round(max(0.0, min(1.0, salary_score)), 4),
    }


def _recruiter_feedback_count(db: Session, recruiter_id: str | None) -> int:
    recruiter_id = (recruiter_id or "").strip()
    if not recruiter_id:
        return 0
    return CandidateFeedbackRepository(db).count_for_recruiter(recruiter_id)


def _dynamic_ranking_weights(*, recruiter_feedback_count: int) -> tuple[float, float, float, float]:
    session_weight = 0.08
    raw_recruiter_weight = min(0.4, 0.07 * max(0, recruiter_feedback_count))
    recruiter_signal_strength = min(1.0, max(0, recruiter_feedback_count) / 4.0)
    effective_recruiter_weight = raw_recruiter_weight * recruiter_signal_strength
    existing_weight = max(0.0, 1.0 - (effective_recruiter_weight + session_weight))
    total = existing_weight + effective_recruiter_weight + session_weight
    if total <= 0:
        return 0.9, 0.0, 0.1, 0.0
    return (
        existing_weight / total,
        effective_recruiter_weight / total,
        session_weight / total,
        recruiter_signal_strength,
    )


def _apply_recruiter_safety_caps(*, existing_score: float, recruiter_score: float, recruiter_weight: float) -> tuple[float, bool]:
    """
    Safety cap for recruiter influence.
    This does not change how recruiter_score is originally computed.
    """
    capped = False
    if existing_score <= 0:
        return recruiter_score, capped

    capped_recruiter_score = recruiter_score
    primary_cap = existing_score * 1.2
    if capped_recruiter_score > primary_cap:
        capped_recruiter_score = primary_cap
        capped = True

    if recruiter_weight > 0:
        max_recruiter_contribution = existing_score * 0.5
        actual_recruiter_contribution = capped_recruiter_score * recruiter_weight
        if actual_recruiter_contribution > max_recruiter_contribution:
            capped_recruiter_score = max_recruiter_contribution / recruiter_weight
            capped = True

    return capped_recruiter_score, capped


def _candidate_debug_payload(
    *,
    existing_score: float,
    recruiter_score_raw: float,
    recruiter_score_adjusted: float,
    session_signal: float,
    existing_weight: float,
    recruiter_weight: float,
    session_weight: float,
    final_score: float,
    recruiter_capped: bool,
    experience_bucket: str = "",
    experience_score: float = 0.0,
) -> CandidateRankingDebug:
    return CandidateRankingDebug(
        existing_score=round(existing_score, 4),
        recruiter_score_raw=round(recruiter_score_raw, 4),
        recruiter_score_adjusted=round(recruiter_score_adjusted, 4),
        session_signal=round(session_signal, 4),
        weights={
            "existing": round(existing_weight, 4),
            "recruiter": round(recruiter_weight, 4),
            "session": round(session_weight, 4),
        },
        final_score=round(final_score, 4),
        recruiter_capped=bool(recruiter_capped),
        experience_bucket=experience_bucket,
        experience_score=round(experience_score, 4),
    )


def _blend_final_score(*, existing_score: float, recruiter_score: float, session_signal: float, recruiter_feedback_count: int) -> tuple[float, dict[str, float | bool], float]:
    existing_weight, recruiter_weight, session_weight, recruiter_signal_strength = _dynamic_ranking_weights(
        recruiter_feedback_count=recruiter_feedback_count
    )
    adjusted_recruiter_score, recruiter_capped = _apply_recruiter_safety_caps(
        existing_score=existing_score,
        recruiter_score=recruiter_score,
        recruiter_weight=recruiter_weight,
    )
    if recruiter_capped:
        logger.debug(
            "recruiter_score_capped existing_score=%s original_recruiter_score=%s adjusted_recruiter_score=%s recruiter_weight=%s",
            round(existing_score, 4),
            round(recruiter_score, 4),
            round(adjusted_recruiter_score, 4),
            round(recruiter_weight, 4),
        )
    final_score = max(
        0.0,
        min(
            1.0,
            (existing_score * existing_weight) + (adjusted_recruiter_score * recruiter_weight) + (session_signal * session_weight),
        ),
    )
    return final_score, {
        "existingWeight": round(existing_weight, 4),
        "recruiterWeight": round(recruiter_weight, 4),
        "sessionWeight": round(session_weight, 4),
        "recruiterSignalStrength": round(recruiter_signal_strength, 4),
        "recruiterCapped": recruiter_capped,
    }, adjusted_recruiter_score


def _record_ranking_run(
    *,
    db: Session,
    job_id: str,
    recruiter_id: str | None,
    run_type: str,
    metrics: list[dict[str, float | bool]],
) -> None:
    candidate_count = len(metrics)
    if candidate_count:
        avg_existing_score = sum(float(item.get("existing_score") or 0.0) for item in metrics) / candidate_count
        avg_final_score = sum(float(item.get("final_score") or 0.0) for item in metrics) / candidate_count
        avg_recruiter_score = sum(float(item.get("recruiter_score") or 0.0) for item in metrics) / candidate_count
        percent_recruiter_capped = (
            sum(1 for item in metrics if bool(item.get("recruiter_capped"))) / candidate_count
        ) * 100.0
    else:
        avg_existing_score = 0.0
        avg_final_score = 0.0
        avg_recruiter_score = 0.0
        percent_recruiter_capped = 0.0

    drift_delta = avg_final_score - avg_existing_score
    RankingRunRepository(db).create(
        job_id=job_id,
        recruiter_id=recruiter_id,
        run_type=run_type,
        avg_existing_score=avg_existing_score,
        avg_final_score=avg_final_score,
        avg_recruiter_score=avg_recruiter_score,
        percent_recruiter_capped=percent_recruiter_capped,
        candidate_count=candidate_count,
        drift_delta=drift_delta,
    )
    if drift_delta < -0.05:
        logger.warning("Negative drift detected for recruiter %s", recruiter_id or "")


def _infer_ranking_run_type(*, refresh: bool, selection_session: CandidateSelectionSessionEntity | None) -> str:
    if refresh:
        return "refresh"
    if selection_session and (
        (selection_session.selected_candidate_ids or [])
        or (selection_session.rejected_candidate_ids or [])
        or (selection_session.completed_at is not None)
    ):
        return "post_selection"
    return "initial"


def _ranking_run_metrics_for_candidates(
    candidates: list[CandidateResult],
    metrics_by_candidate_id: dict[str, dict[str, float | bool]],
) -> list[dict[str, float | bool]]:
    metrics: list[dict[str, float | bool]] = []
    for candidate in candidates:
        candidate_metrics = metrics_by_candidate_id.get(candidate.id)
        if candidate_metrics is None:
            final_score = ranked_candidate_final_score(candidate)
            candidate_metrics = {
                "existing_score": final_score,
                "final_score": final_score,
                "recruiter_score": 0.0,
                "recruiter_capped": False,
            }
        metrics.append(candidate_metrics)
    return metrics


def store_ranking_explanation(
    db: Session,
    *,
    rows: list[dict[str, float | str]],
) -> None:
    try:
        RankingExplanationRepository(db).store_bulk(rows)
    except Exception as exc:
        logger.info("ranking_explanations_store_skipped error=%s", str(exc))


def _build_embedding_boost_suffix(
    *,
    feedback_learning: FeedbackLearningContext,
    role: str = "",
    skills: list[str] | None = None,
) -> str:
    skill_tokens = _normalized_skill_tokens(skills or [])
    matched_skills = [token for token in feedback_learning.preferred_tokens[:6] if token in skill_tokens]
    role_signal = _score_feedback_role(role, feedback_learning.global_role_bias)
    parts: list[str] = []
    if matched_skills:
        parts.append(f"High-Performing Skill Signals: {', '.join(matched_skills)}")
    if role and role_signal > 0:
        parts.append(f"Successful Role Pattern: {role}")
    return ("\n" + "\n".join(parts)) if parts else ""


def _build_feedback_learning_context(db: Session, *, job_id: str) -> FeedbackLearningContext:
    feedback_repo = CandidateFeedbackRepository(db)
    interview_repo = InterviewRepository(db)
    profile_repo = CandidateProfileRepository(db)

    job_rows = feedback_repo.list_by_job(job_id)
    global_sample = feedback_repo.list_recent_global(limit=100)
    # Merge: job-specific rows are primary; global sample fills in cross-job signal.
    seen_ids: set[str] = {row.id for row in job_rows}
    rows = job_rows + [row for row in global_sample if row.id not in seen_ids]
    logger.info(
        "rlhf_feedback_loaded job_id=%s job_rows=%s global_sample=%s total=%s",
        job_id, len(job_rows), len(global_sample), len(rows),
    )
    if not rows:
        return FeedbackLearningContext(
            candidate_feedback={},
            candidate_accept_counts={},
            candidate_reject_counts={},
            global_skill_bias={},
            global_role_bias={},
            preferred_tokens=[],
            preferred_roles=[],
            learned_query_tokens=[],
            job_success_rate=0.0,
            global_success_rate=0.0,
        )

    now = datetime.now(timezone.utc)
    half_life_days = max(1, RLHF_FEEDBACK_HALF_LIFE_DAYS)
    lambda_decay = math.log(2) / half_life_days
    normalized_denominator = max(1.0, math.sqrt(len(rows)))

    rows_by_job: dict[str, list] = defaultdict(list)
    for row in rows:
        rows_by_job[str(row.job_id)].append(row)

    candidate_feedback: dict[str, float] = {}
    candidate_accept_counts: dict[str, int] = defaultdict(int)
    candidate_reject_counts: dict[str, int] = defaultdict(int)
    global_skill_accum: dict[str, float] = defaultdict(float)
    global_skill_counts: dict[str, int] = defaultdict(int)
    global_role_accum: dict[str, float] = defaultdict(float)
    global_role_counts: dict[str, int] = defaultdict(int)
    preferred_token_scores: dict[str, float] = defaultdict(float)
    preferred_role_scores: dict[str, float] = defaultdict(float)

    global_success = 0.0
    global_attempts = 0.0
    job_success = 0.0
    job_attempts = 0.0

    for feedback_job_id, job_rows in rows_by_job.items():
        status_map = {
            row.candidate_id: row.status
            for row in interview_repo.list_for_job(feedback_job_id)
        }
        profiles = {row.candidate_id: row for row in profile_repo.list_for_job(feedback_job_id)}
        for row in job_rows:
            updated_at = row.updated_at
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (now - updated_at.astimezone(timezone.utc)).total_seconds() / 86400.0)
            decay_factor = math.exp(-lambda_decay * age_days)

            status = status_map.get(row.candidate_id)
            signal = (_feedback_signal_value(row.feedback, status) * decay_factor) / normalized_denominator
            success_signal = _feedback_success_value(row.feedback, status) * decay_factor
            if row.feedback == "accept":
                candidate_accept_counts[row.candidate_id] += 1
            elif row.feedback == "reject":
                candidate_reject_counts[row.candidate_id] += 1

            global_success += success_signal
            global_attempts += 1.0
            if feedback_job_id == job_id:
                candidate_feedback[row.candidate_id] = candidate_feedback.get(row.candidate_id, 0.0) + signal
                job_success += success_signal
                job_attempts += 1.0

            profile = profiles.get(row.candidate_id)
            skills = profile.skills if profile else []
            role = profile.role if profile else ""
            for token in _normalized_skill_tokens(skills):
                global_skill_accum[token] += signal
                global_skill_counts[token] += 1
                if signal > 0:
                    preferred_token_scores[token] += signal
            for token in _normalized_skill_tokens([role]):
                global_role_accum[token] += signal
                global_role_counts[token] += 1
                if signal > 0:
                    preferred_role_scores[token] += signal

    global_skill_bias = {
        token: global_skill_accum[token] / max(1, global_skill_counts[token])
        for token in global_skill_accum
    }
    global_role_bias = {
        token: global_role_accum[token] / max(1, global_role_counts[token])
        for token in global_role_accum
    }
    sorted_preferred_tokens = sorted(preferred_token_scores.items(), key=lambda item: item[1], reverse=True)
    preferred_tokens = [token for token, _ in sorted_preferred_tokens[:6]]
    sorted_preferred_roles = sorted(preferred_role_scores.items(), key=lambda item: item[1], reverse=True)
    preferred_roles = [token for token, _ in sorted_preferred_roles[:4]]
    learned_query_tokens = preferred_tokens[:4] + [role for role in preferred_roles[:2] if role not in preferred_tokens]

    job_success_rate = max(0.0, min(1.0, job_success / max(1.0, job_attempts)))
    global_success_rate = max(0.0, min(1.0, global_success / max(1.0, global_attempts)))

    return FeedbackLearningContext(
        candidate_feedback=candidate_feedback,
        candidate_accept_counts=dict(candidate_accept_counts),
        candidate_reject_counts=dict(candidate_reject_counts),
        global_skill_bias=global_skill_bias,
        global_role_bias=global_role_bias,
        preferred_tokens=preferred_tokens,
        preferred_roles=preferred_roles,
        learned_query_tokens=learned_query_tokens,
        job_success_rate=job_success_rate,
        global_success_rate=global_success_rate,
    )


def _elite_reasoning(job, candidate: CandidateResult) -> tuple[str, float]:
    if not (GROQ_API_KEY or OPEN_ROUTER_API):
        heuristic = (
            "Strong semantic and skill alignment." if candidate.explanation.semanticScore >= 0.7 else "Moderate alignment."
        )
        return heuristic, 0.03 if candidate.explanation.semanticScore >= 0.7 else 0.0

    try:
        prompt = (
            "Rate this candidate for the job on a 0-100 scale and explain in one short sentence. "
            "Return exactly: SCORE=<number>; REASON=<text>.\n\n"
            f"{sanitize_prompt_block('JOB TITLE', job.title, max_length=120)}\n"
            f"{sanitize_prompt_block('JOB DESCRIPTION', job.description, max_length=4000)}\n"
            f"{sanitize_prompt_block('CANDIDATE ROLE', candidate.role, max_length=120)}\n"
            f"{sanitize_prompt_block('CANDIDATE SUMMARY', candidate.summary, max_length=2000)}\n"
            f"{sanitize_prompt_block('CANDIDATE SKILLS', ', '.join(candidate.skills), max_length=1000)}"
        )
        text = str(generate(prompt)).strip()

        score_match = re.search(r"SCORE\s*=\s*(\d{1,3})", text, re.IGNORECASE)
        reason_match = re.search(r"REASON\s*=\s*(.+)", text, re.IGNORECASE)
        score = float(score_match.group(1)) if score_match else 50.0
        score = max(0.0, min(100.0, score))
        reason = (reason_match.group(1).strip() if reason_match else text)[:240]
        bonus = (score / 100.0) * 0.10
        return reason or "Elite review completed.", bonus
    except Exception as exc:
        logger.warning("Elite reasoning failed; falling back to heuristic error=%s", str(exc))
        return "Elite reasoning unavailable; fallback scoring used.", 0.0


def _resolve_mode(mode: str | None) -> str:
    value = (mode or SCORING_DEFAULT_MODE or "volume").strip().lower()
    if value not in {"volume", "elite"}:
        return "volume"
    return value


def _normalize_vector_score(score: float) -> float:
    if score < 0:
        return max(0.0, min(1.0, (score + 1.0) / 2.0))
    if score > 1.0:
        return 1.0
    return max(0.0, score)


def _local_metadata_filters(job, feedback_learning: FeedbackLearningContext) -> dict[str, str | list[str]]:
    # NOTE: Do NOT pass company here — that field stores the *candidate's* employer,
    # not the hiring company. Passing the hiring company name causes zero Qdrant hits.
    # Only pass soft signals (preferred skills/roles) to widen recall.
    return {
        "embeddingVersion": EMBEDDING_VERSION,
        "preferredSkills": feedback_learning.preferred_tokens[:4],
        "preferredRoles": feedback_learning.preferred_roles[:2],
    }


def _adaptive_local_threshold(local_results: list[CandidateResult]) -> float:
    if not local_results:
        return ADAPTIVE_THRESHOLD_FLOOR

    scores = [max(0.0, min(1.0, row.explanation.semanticScore)) for row in local_results]
    score_mean = mean(scores)
    score_std = pstdev(scores) if len(scores) > 1 else 0.0
    threshold = score_mean + (0.45 * score_std)
    return max(ADAPTIVE_THRESHOLD_FLOOR, min(ADAPTIVE_THRESHOLD_CEILING, threshold))


def _candidate_diversity_score(candidates: list[CandidateResult]) -> float:
    if not candidates:
        return 0.0
    companies = {(row.company or "").strip().lower() for row in candidates if (row.company or "").strip()}
    roles = {(row.role or "").strip().lower() for row in candidates if (row.role or "").strip()}
    company_ratio = len(companies) / max(1, len(candidates))
    role_ratio = len(roles) / max(1, len(candidates))
    return max(0.0, min(1.0, (0.5 * company_ratio) + (0.5 * role_ratio)))


def _resolve_exploration_rate(*, diversity: float, feedback_success: float, system_confidence: float) -> float:
    # Higher exploration for low confidence/performance; reduce exploration as performance stabilizes.
    base = 0.20 - (0.05 * feedback_success) - (0.05 * diversity) - (0.06 * system_confidence)
    return max(EXPLORATION_RATE_FLOOR, min(EXPLORATION_RATE_CEILING, base))


def _compute_system_confidence(*, similarity: float, diversity: float, feedback_success: float) -> float:
    return max(0.0, min(1.0, (0.55 * similarity) + (0.20 * diversity) + (0.25 * feedback_success)))


def _exploration_bonus(exploration: ExplorationContext) -> float:
    exploration.total += 1
    if random.random() < exploration.rate:
        exploration.used += 1
        return random.uniform(0.015, 0.045)
    return 0.0


def _diversity_bonus(
    *,
    company: str,
    role: str,
    company_counts: dict[str, int],
    role_counts: dict[str, int],
) -> float:
    company_key = company.strip().lower()
    role_key = role.strip().lower()
    company_bonus = 0.025 if company_key and company_counts.get(company_key, 0) == 0 else 0.0
    role_bonus = 0.02 if role_key and role_counts.get(role_key, 0) == 0 else 0.0
    return company_bonus + role_bonus


def _update_diversity_counts(*, company: str, role: str, company_counts: dict[str, int], role_counts: dict[str, int]) -> None:
    company_key = company.strip().lower()
    role_key = role.strip().lower()
    if company_key:
        company_counts[company_key] = company_counts.get(company_key, 0) + 1
    if role_key:
        role_counts[role_key] = role_counts.get(role_key, 0) + 1


# fitScore is 0-5; threshold of 3/5 = 0.60 on the 0-1 similarity scale.
LOW_SIMILARITY_PDL_THRESHOLD = 0.60


def _decide_switching_mode(
    *,
    refresh: bool,
    local_count: int,
    similarity_score: float,
    feedback_success_rate: float,
    candidate_diversity: float,
) -> tuple[str, str]:
    if local_count == 0:
        return "pdl", "local_candidates_empty"

    # If avg similarity < 0.60 (equivalent to fitScore < 3/5), ping PDL for better matches.
    # PDL results will be merged; if PDL returns nothing, local results are used as fallback.
    if similarity_score < LOW_SIMILARITY_PDL_THRESHOLD:
        return "pdl_with_local_fallback", "low_similarity_score_below_threshold"

    return "local", "similarity_above_threshold"


def _allow_pdl_when_qdrant_is_unhealthy() -> bool:
    global _last_pdl_attempt_when_qdrant_error

    if not is_qdrant_search_error_active():
        return True

    now = datetime.now(timezone.utc)
    if _last_pdl_attempt_when_qdrant_error is None:
        _last_pdl_attempt_when_qdrant_error = now
        return True

    elapsed = now - _last_pdl_attempt_when_qdrant_error
    if elapsed >= timedelta(seconds=PDL_RETRY_BACKOFF_ON_QDRANT_ERROR_SECONDS):
        _last_pdl_attempt_when_qdrant_error = now
        return True

    return False


def _build_local_candidates(
    *,
    db: Session,
    job,
    mode: str,
    mode_config: ModeConfig,
    feedback_learning: FeedbackLearningContext,
    recruiter_preferences: dict,
    exploration: ExplorationContext,
    debug: bool = False,
    run_metrics_by_candidate_id: dict[str, dict[str, float | bool]] | None = None,
) -> list[CandidateResult]:
    raise APIError("Legacy sourcing paths are disabled; X-Ray retrieval is the only supported sourcing path", status_code=503)
    ensure_all_collections()
    recruiter_id = JobRepository(db).get_recruiter_id(job.id)
    job_vec = _job_vector(job, feedback_learning)
    search_fn = search_internal_candidate_chunks if USE_INTERNAL_CANDIDATE_DB else search_candidate_chunks
    hits = search_fn(
        query_vector=job_vec,
        limit=LOCAL_SEARCH_LIMIT,
        metadata_filters=_local_metadata_filters(job, feedback_learning),
    )
    if not hits:
        return []

    best_by_candidate: dict[str, dict] = {}
    identity_to_candidate_id: dict[str, str] = {}
    for hit in hits:
        candidate_id = str(hit.get("candidateId") or "").strip()
        if not candidate_id:
            continue
        payload = hit.get("payload") or {}
        identity_key = str(payload.get("dedupeKey") or candidate_id).strip() or candidate_id
        current = best_by_candidate.get(candidate_id)
        score = _normalize_vector_score(float(hit.get("score") or 0.0))

        existing_candidate_id = identity_to_candidate_id.get(identity_key)
        if existing_candidate_id and existing_candidate_id != candidate_id:
            existing = best_by_candidate.get(existing_candidate_id)
            if existing and existing["score"] >= score:
                continue
            best_by_candidate.pop(existing_candidate_id, None)

        if not current or score > current["score"]:
            best_by_candidate[candidate_id] = {"score": score, "payload": payload, "identityKey": identity_key}
            identity_to_candidate_id[identity_key] = candidate_id

    if not best_by_candidate:
        return []

    profile_repo = CandidateProfileRepository(db)
    profiles = profile_repo.latest_by_candidate_ids(job_id=job.id, candidate_ids=list(best_by_candidate.keys()))
    ordered = sorted(best_by_candidate.items(), key=lambda row: row[1]["score"], reverse=True)

    weights = _load_scoring_weights(db, job_id=job.id)
    ranking_weights = _resolve_ranking_weights(job, default_weights=mode_config.ranking_weights)
    company_counts: dict[str, int] = {}
    role_counts: dict[str, int] = {}
    upserted_ids: set[str] = set()
    job_skills = _job_requirement_skills(job)
    job_experience = _job_experience(job)
    min_experience_years = _job_min_experience_years(job)
    candidate_rows: list[dict[str, Any]] = []
    candidate_limit = max(1, mode_config.top_k)
    for candidate_id, item in ordered[:candidate_limit]:
        payload = item["payload"]
        semantic = _normalize_vector_score(item["score"])
        profile = profiles.get(candidate_id)

        historical = 0.0
        if profile:
            historical = max(0.0, min(1.0, profile.fit_score / 5.0))
        semantic_similarity = (0.70 * semantic) + (0.30 * historical)
        feedback_direct = _feedback_adjustment(
            feedback_learning.candidate_feedback.get(candidate_id),
            bias=weights.feedback_bias,
        )

        company = (profile.company if profile else str(payload.get("company") or "")).strip()
        role = (profile.role if profile else str(payload.get("role") or "")).strip() or "Unknown Role"
        skills = _candidate_skill_values(payload, fallback_profile=profile)
        candidate_source = profile.raw_data if profile and isinstance(profile.raw_data, dict) else payload
        candidate_experience = _candidate_experience_value(candidate_source)
        candidate_location = _candidate_location(candidate_source)
        candidate_salary = _candidate_salary(candidate_source)
        candidate_rows.append(
            {
                "candidate_id": candidate_id,
                "payload": payload,
                "semantic": semantic,
                "historical": historical,
                "company": company,
                "role": role,
                "skills": skills,
                "candidate_experience": candidate_experience,
                "candidate_location": candidate_location,
                "candidate_salary": candidate_salary,
                "candidate_experience_years": _candidate_experience_years(payload, fallback_profile=profile),
                "profile": profile,
                "feedback_direct": feedback_direct,
            }
        )

    recruiter_score_lookup: dict[str, float] = {}
    if recruiter_preferences:
        for row in candidate_rows:
            candidate_id = row["candidate_id"]
            candidate_profile = row["profile"] or row["payload"]
            recruiter_score_lookup[candidate_id] = compute_recruiter_score_details(
                candidate_profile,
                recruiter_preferences,
            ).get("score", 0.0)

    candidate_rows = rerank_candidates(
        job=job,
        rows=candidate_rows,
        recruiter_score_lookup=recruiter_score_lookup,
        learned_tokens=feedback_learning.learned_query_tokens,
        preferred_roles=feedback_learning.preferred_roles,
    )

    filtered_candidate_rows = candidate_rows
    if mode_config.use_hard_filtering:
        filtered_candidate_rows = [
            row
            for row in candidate_rows
            if passes_hard_filters(
                row,
                job_skills,
                min_experience_years,
                min_skill_matches=mode_config.min_skill_match_threshold,
            )
        ]
    filtered_out = len(candidate_rows) - len(filtered_candidate_rows)
    logger.info(
        "hard_filter_applied job_id=%s source=local enabled=%s total=%s kept=%s filtered_out=%s min_experience=%s min_skill_matches=%s",
        job.id,
        mode_config.use_hard_filtering,
        len(candidate_rows),
        len(filtered_candidate_rows),
        filtered_out,
        min_experience_years,
        mode_config.min_skill_match_threshold,
    )
    log_metric(
        "hard_filter_applied",
        job_id=job.id,
        source="local",
        enabled=mode_config.use_hard_filtering,
        total=len(candidate_rows),
        kept=len(filtered_candidate_rows),
        filtered_out=filtered_out,
        min_experience=min_experience_years,
        min_skill_matches=mode_config.min_skill_match_threshold,
    )
    logger.info(
        "candidates_filtered_count job_id=%s source=local total=%s filtered_out=%s",
        job.id,
        len(candidate_rows),
        filtered_out,
    )
    log_metric(
        "candidates_filtered_count",
        job_id=job.id,
        source="local",
        total=len(candidate_rows),
        filtered_out=filtered_out,
    )
    if mode_config.use_hard_filtering and candidate_rows and not filtered_candidate_rows:
        logger.info(
            "fallback_to_unfiltered job_id=%s source=local reason=no_candidates_after_hard_filter",
            job.id,
        )
        log_metric(
            "fallback_to_unfiltered",
            job_id=job.id,
            source="local",
            reason="no_candidates_after_hard_filter",
            total=len(candidate_rows),
        )
        filtered_candidate_rows = candidate_rows

    local_results: list[CandidateResult] = []
    for row in filtered_candidate_rows:
        candidate_id = row["candidate_id"]
        payload = row["payload"]
        semantic = row["semantic"]
        historical = row["historical"]
        company = row["company"]
        role = row["role"]
        skills = row["skills"]
        candidate_experience = row["candidate_experience"]
        profile = row["profile"]
        candidate_experience_years = row["candidate_experience_years"]
        retrieval = row.get("retrieval")
        semantic_similarity = float(row.get("hybrid_score") or semantic)

        skill_overlap = _skill_overlap(job_skills, skills or [])
        experience_match = _experience_match(candidate_experience, job_experience)
        global_skill_feedback = _score_feedback_skills(skills or [], feedback_learning.global_skill_bias) * 0.05
        role_feedback = _score_feedback_role(role, feedback_learning.global_role_bias) * 0.03
        diversity_bonus = _diversity_bonus(
            company=company,
            role=role,
            company_counts=company_counts,
            role_counts=role_counts,
        )
        exploration_bonus = _exploration_bonus(exploration)
        feedback_direct = row["feedback_direct"]
        feedback_bias = feedback_direct + global_skill_feedback + role_feedback
        rejection_penalty = _candidate_rejection_penalty(candidate_id, feedback_learning)
        freshness_score = _candidate_freshness_score(profile or payload)
        location_match = _location_match(str(row.get("candidate_location") or ""), _job_location(job))
        salary_match = _salary_match(str(row.get("candidate_salary") or ""), _job_compensation(job))
        log_metric(
            "candidate_penalty",
            job_id=job.id,
            candidate_id=candidate_id,
            penalty=round(rejection_penalty, 4),
        )
        final = compute_final_score(
            semantic_similarity=(0.70 * semantic_similarity) + (0.30 * historical),
            skill_overlap=skill_overlap,
            experience_match=experience_match,
            ranking_weights=ranking_weights,
            recency_score=freshness_score,
            pdl_component=0.0,
            feedback_bias=feedback_bias,
            diversity_bonus=diversity_bonus,
            exploration_bonus=exploration_bonus,
            rejection_penalty=rejection_penalty,
            semantic_penalty=1.0,
            missing_skills_penalty=1.0,
            location_match=location_match,
            salary_match=salary_match,
        )

        fit_score = round(final * 5, 2)
        decision = _decision_from_score(final)
        name = (profile.name if profile else str(payload.get("name") or "")).strip() or f"Candidate {candidate_id[:8]}"
        summary = (profile.summary if profile else str(payload.get("summary") or "")).strip() or "Local profile match."
        candidate_email = ensure_candidate_email(profile or payload)
        raw_data_source = getattr(profile, "raw_data", None) or payload
        stored_raw_data = dict(raw_data_source) if isinstance(raw_data_source, dict) else {}
        if candidate_email and not _extract_candidate_email(stored_raw_data):
            stored_raw_data["email"] = candidate_email
            stored_raw_data["work_email"] = candidate_email
            stored_raw_data["personal_email"] = candidate_email
        if candidate_email.endswith("@test.local"):
            stored_raw_data["is_mock_email"] = True
            stored_raw_data["email_source"] = "generated"
        profile_details = _candidate_profile_details(profile=profile, raw_data=stored_raw_data)

        strategy = _strategy_from_score(fit_score)
        debug_payload = None
        if debug:
            experience_bucket = map_experience_to_bucket(candidate_experience_years) if candidate_experience else ""
            debug_payload = _candidate_debug_payload(
                existing_score=final,
                recruiter_score_raw=0.0,
                recruiter_score_adjusted=0.0,
                session_signal=0.0,
                existing_weight=1.0,
                recruiter_weight=0.0,
                session_weight=0.0,
                final_score=final,
                recruiter_capped=False,
                experience_bucket=experience_bucket,
                experience_score=0.0,
            )
        result = CandidateResult(
            id=candidate_id,
            name=name,
            role=role,
            company=company,
            email=profile_details["email"] or candidate_email,
            isMockEmail=bool(profile_details["isMockEmail"]) or candidate_email.endswith("@test.local"),
            headline=profile_details["headline"],
            location=profile_details["location"],
            yearsExperience=float(profile_details["yearsExperience"] or 0.0),
            skills=skills or [],
            summary=summary,
            education=list(profile_details["education"] or []),
            projects=list(profile_details["projects"] or []),
            certifications=list(profile_details["certifications"] or []),
            companiesHistory=list(profile_details["companiesHistory"] or []),
            domainExperience=list(profile_details["domainExperience"] or []),
            resumeText=profile_details["resumeText"],
            profileData=dict(profile_details["profileData"] or {}),
            fitScore=fit_score,
            decision=decision,
            explanation=CandidateExplanation(
                semanticScore=round((0.70 * semantic_similarity) + (0.30 * historical), 4),
                skillOverlap=round(skill_overlap, 4),
                finalScore=round(final, 4),
                pdlRelevance=0.0,
                recencyScore=round(freshness_score, 4),
                engineeringScore=round(min(1.0, (0.65 * skill_overlap) + (0.35 * semantic_similarity)), 4),
                skillsMatched=_matched_skills(job_skills, skills or []),
                experienceMatch=_experience_match_summary(candidate_experience, job_experience),
                candidateExperience=candidate_experience,
                jobExperience=job_experience,
                penalties={
                    "source": 1.0,
                    "feedbackBias": round(feedback_bias, 4),
                    "rejectionPenalty": round(rejection_penalty, 4),
                    "explorationBonus": round(exploration_bonus, 4),
                },
                retrievalAttribution=retrieval_explanation(retrieval) if retrieval else {},
                sourceBreakdown=_explanation_source_breakdown(
                    vector_score=semantic_similarity,
                    lexical_score=historical,
                    structured_score=skill_overlap,
                    recruiter_score=0.0,
                    recency_score=freshness_score,
                    session_signal=0.0,
                    voice_score=1.0 if getattr(job, "structured_data", None) else 0.0,
                ),
                recruiterPreferenceInfluence=0.0,
                voiceInterviewInfluence=1.0 if getattr(job, "structured_data", None) else 0.0,
                lexicalRetrievalInfluence=round(historical, 4),
                vectorRetrievalInfluence=round(semantic_similarity, 4),
                freshnessInfluence=round(freshness_score, 4),
                selectionRoundInfluence=0.0,
                aiReasoning="Local source blend combining semantic and historical candidate signals.",
            ),
            strategy=strategy,
            status="new",
            debug=debug_payload,
        )
        # Persist profile so swipe/feedback can find this candidate by job_id + candidate_id.
        if candidate_id not in upserted_ids:
            profile_repo.upsert(
                job_id=job.id,
                candidate_id=candidate_id,
                name=name,
                role=role,
                company=company,
                summary=summary,
                skills=skills or [],
                raw_data=stored_raw_data,
                fit_score=fit_score,
                decision=decision,
                strategy=strategy,
            )
            upserted_ids.add(candidate_id)
        local_results.append(result)
        if run_metrics_by_candidate_id is not None:
            run_metrics_by_candidate_id[candidate_id] = {
                "existing_score": final,
                "final_score": final,
                "recruiter_score": 0.0,
                "recruiter_capped": False,
            }
        _update_diversity_counts(company=company, role=role, company_counts=company_counts, role_counts=role_counts)

    if mode == "elite":
        enriched: list[tuple[CandidateResult, float]] = []
        for index, candidate in enumerate(local_results):
            reason = ""
            bonus = 0.0
            if index < 6:
                reason, bonus = _elite_reasoning(job, candidate)
            explanation = coerce_candidate_explanation(candidate.explanation)
            current_score = ranked_candidate_final_score(candidate)
            explanation.aiReasoning = reason
            new_score = round(max(0.0, min(1.0, current_score + bonus)), 4)
            setattr(explanation, "finalScore", new_score)
            candidate.explanation = explanation
            candidate.fitScore = round(new_score * 5, 2)
            candidate.decision = _decision_from_score(new_score)
            enriched.append((candidate, new_score))
        local_results = [candidate for candidate, _ in sorted(enriched, key=lambda row: row[1], reverse=True)]

    ranked_local = sorted([(candidate, ranked_candidate_final_score(candidate)) for candidate in local_results], key=lambda row: row[1], reverse=True)
    diverse_local = diversify_candidates(ranked_local, limit=mode_config.top_k)
    return [candidate for candidate, _ in diverse_local]


def _build_ranked_candidates_from_pdl(
    *,
    db: Session,
    job,
    mode: str,
    size: int,
    mode_config: ModeConfig,
    feedback_learning: FeedbackLearningContext,
    exploration: ExplorationContext,
    recruiter_preferences: dict,
    recruiter_feedback_count: int,
    selection_session: Any,
    debug: bool = False,
    run_metrics_by_candidate_id: dict[str, dict[str, float | bool]] | None = None,
    source_candidates: list[dict[str, Any]] | None = None,
    source_label: str = "pdl",
) -> list[CandidateResult]:
    raise APIError("Legacy sourcing paths are disabled; X-Ray retrieval is the only supported sourcing path", status_code=503)
    filters = _normalize_job_filters(
        job,
        preferred_tokens=feedback_learning.preferred_tokens,
        preferred_roles=feedback_learning.preferred_roles,
    )
    if source_candidates is None:
        response = fetch_candidates_with_filters(filters=filters, size=size)
        candidates = response.get("data", []) if isinstance(response, dict) else []
    else:
        candidates = list(source_candidates)
    if not isinstance(candidates, list):
        candidates = []
    deduped_candidates: list[dict] = []
    seen_identity_keys: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        identity_key = _candidate_identity_key(candidate)
        if identity_key in seen_identity_keys:
            continue
        seen_identity_keys.add(identity_key)
        deduped_candidates.append(candidate)
    candidates = deduped_candidates

    if len(candidates) > size:
        candidates = candidates[:size]
    if candidates:
        logger.info("%s_top_k_applied count=%s job_id=%s", source_label, len(candidates), job.id)

    if not candidates:
        logger.warning(
            "%s fetch failed — preserving existing vectors job_id=%s",
            source_label.upper(),
            job.id,
        )
        return []

    job_vec = _job_vector(job, feedback_learning)
    job_skills = _job_requirement_skills(job)
    min_experience_years = _job_min_experience_years(job)
    ensure_all_collections()
    # Safe refresh: do NOT delete vectors here.
    # Deletion happens AFTER all new vectors are upserted below.

    weights = _load_scoring_weights(db, job_id=job.id)
    ranking_weights = _resolve_ranking_weights(job, default_weights=mode_config.ranking_weights)
    profile_repo = CandidateProfileRepository(db)
    recruiter_id = JobRepository(db).get_recruiter_id(job.id)
    company_counts: dict[str, int] = {}
    role_counts: dict[str, int] = {}
    job_experience = _job_experience(job)

    filtered_candidates = candidates
    if mode_config.use_hard_filtering:
        filtered_candidates = [
            item
            for item in candidates
            if passes_hard_filters(
                {
                    "candidate_skills": _candidate_skills(item),
                    "candidate_experience_years": _candidate_experience_years(item),
                },
                job_skills,
                min_experience_years,
                min_skill_matches=mode_config.min_skill_match_threshold,
            )
        ]
    filtered_out = len(candidates) - len(filtered_candidates)
    logger.info(
        "hard_filter_applied job_id=%s source=%s enabled=%s total=%s kept=%s filtered_out=%s min_experience=%s min_skill_matches=%s",
        job.id,
        source_label,
        mode_config.use_hard_filtering,
        len(candidates),
        len(filtered_candidates),
        filtered_out,
        min_experience_years,
        mode_config.min_skill_match_threshold,
    )
    log_metric(
        "hard_filter_applied",
        job_id=job.id,
        source=source_label,
        enabled=mode_config.use_hard_filtering,
        total=len(candidates),
        kept=len(filtered_candidates),
        filtered_out=filtered_out,
        min_experience=min_experience_years,
        min_skill_matches=mode_config.min_skill_match_threshold,
    )
    logger.info(
        "candidates_filtered_count job_id=%s source=%s total=%s filtered_out=%s",
        job.id,
        source_label,
        len(candidates),
        filtered_out,
    )
    log_metric(
        "candidates_filtered_count",
        job_id=job.id,
        source=source_label,
        total=len(candidates),
        filtered_out=filtered_out,
    )
    if mode_config.use_hard_filtering and candidates and not filtered_candidates:
        logger.info(
            "fallback_to_unfiltered job_id=%s source=%s reason=no_candidates_after_hard_filter",
            job.id,
            source_label,
        )
        log_metric(
            "fallback_to_unfiltered",
            job_id=job.id,
            source=source_label,
            reason="no_candidates_after_hard_filter",
            total=len(candidates),
        )
        filtered_candidates = candidates

    scored: list[tuple[CandidateResult, float]] = []
    ranking_explanation_rows: list[dict[str, float | str]] = []
    total_candidates = len(filtered_candidates)
    for index, item in enumerate(filtered_candidates):
        if not isinstance(item, dict):
            continue

        candidate_id = _candidate_id(item)
        candidate_name = _candidate_name(item, candidate_id)
        candidate_role = _candidate_role(item)
        candidate_company = _candidate_company(item)
        candidate_location = _candidate_location(item)
        candidate_skills = _candidate_skills(item)
        candidate_summary = _candidate_summary(item)
        candidate_experience = _candidate_experience_value(item)
        freshness_score = _candidate_freshness_score(item)
        candidate_email = ensure_candidate_email(item)
        candidate_external_id = _extract_candidate_external_id(item)
        candidate_identity_key = _candidate_identity_key(item)
        stored_raw_data = dict(item)
        if candidate_email and not _extract_candidate_email(stored_raw_data):
            stored_raw_data["email"] = candidate_email
            stored_raw_data["work_email"] = candidate_email
            stored_raw_data["personal_email"] = candidate_email
        if candidate_email.endswith("@test.local"):
            stored_raw_data["is_mock_email"] = True
            stored_raw_data["email_source"] = "generated"

        if not candidate_name.strip() or not candidate_role.strip():
            continue

        candidate_embed_text = _candidate_embedding_text(item)
        candidate_chunks = chunk_text(candidate_embed_text)
        candidate_vectors = [_embed_text(chunk) for chunk in candidate_chunks]
        candidate_vec = average_vectors(candidate_vectors)

        cosine_score = cosine_similarity(job_vec, candidate_vec)
        retrieval = hybrid_retrieval_score(
            job=job,
            candidate=item,
            vector_score=_normalize_similarity(cosine_score),
            recruiter_score=compute_recruiter_score_details(item, recruiter_preferences, candidate_vector=candidate_vec).get("score", 0.0),
            learned_tokens=feedback_learning.learned_query_tokens,
            preferred_roles=feedback_learning.preferred_roles,
        )
        semantic_similarity = retrieval.hybrid_score
        pdl_relevance = _pdl_relevance(item, index=index, total=total_candidates)
        skill_overlap = _skill_overlap(job_skills, candidate_skills)
        experience_match = _experience_match(candidate_experience or _candidate_experience_value(item), job_experience)
        location_match = _location_match(candidate_location, _job_location(job))
        salary_match = _salary_match(_candidate_salary(item), _job_compensation(job))
        recency_score = _candidate_recency_score(item)
        pdl_component = weights.pdl * pdl_relevance

        semantic_penalty = 0.45 if semantic_similarity < 0.30 else 1.0
        missing_skills_penalty = 0.55 if skill_overlap < 0.10 else 1.0
        feedback_direct = _feedback_adjustment(
            feedback_learning.candidate_feedback.get(candidate_id),
            bias=weights.feedback_bias,
        )
        global_skill_feedback = _score_feedback_skills(candidate_skills, feedback_learning.global_skill_bias) * 0.07
        role_feedback = _score_feedback_role(candidate_role, feedback_learning.global_role_bias) * 0.04
        feedback_bias = feedback_direct + global_skill_feedback + role_feedback
        diversity_bonus = _diversity_bonus(
            company=candidate_company,
            role=candidate_role,
            company_counts=company_counts,
            role_counts=role_counts,
        )
        exploration_bonus = _exploration_bonus(exploration)
        rejection_penalty = _candidate_rejection_penalty(candidate_id, feedback_learning)
        recruiter_score_details = compute_recruiter_score_details(candidate, recruiter_preferences, candidate_vector=candidate_vec)
        recruiter_score = float(recruiter_score_details["score"])
        session_signal = _selection_session_signal(selection_session, candidate_id)
        log_metric(
            "candidate_penalty",
            job_id=job.id,
            candidate_id=candidate_id,
            penalty=round(rejection_penalty, 4),
        )
        existing_score = compute_final_score(
            semantic_similarity=semantic_similarity,
            skill_overlap=skill_overlap,
            experience_match=experience_match,
            ranking_weights=ranking_weights,
            recency_score=freshness_score,
            pdl_component=pdl_component,
            feedback_bias=feedback_bias,
            diversity_bonus=diversity_bonus,
            exploration_bonus=exploration_bonus,
            rejection_penalty=rejection_penalty,
            semantic_penalty=semantic_penalty,
            missing_skills_penalty=missing_skills_penalty,
            location_match=location_match,
            salary_match=salary_match,
        )
        final_score, weight_snapshot, adjusted_recruiter_score = _blend_final_score(
            existing_score=existing_score,
            recruiter_score=recruiter_score,
            session_signal=session_signal,
            recruiter_feedback_count=recruiter_feedback_count,
        )

        fit_score = round(final_score * 5, 2)
        decision = _decision_from_score(final_score)
        debug_payload = None
        if debug:
            debug_payload = _candidate_debug_payload(
                existing_score=existing_score,
                recruiter_score_raw=recruiter_score,
                recruiter_score_adjusted=adjusted_recruiter_score,
                session_signal=session_signal,
                existing_weight=float(weight_snapshot["existingWeight"]),
                recruiter_weight=float(weight_snapshot["recruiterWeight"]),
                session_weight=float(weight_snapshot["sessionWeight"]),
                final_score=final_score,
                recruiter_capped=bool(weight_snapshot["recruiterCapped"]),
                experience_bucket=str(recruiter_score_details.get("experience_bucket") or ""),
                experience_score=float(recruiter_score_details.get("experience_score") or 0.0),
            )

        profile_details = _candidate_profile_details(raw_data=stored_raw_data)
        result = CandidateResult(
            id=candidate_id,
            name=candidate_name,
            role=candidate_role,
            company=candidate_company,
            email=profile_details["email"] or candidate_email,
            isMockEmail=bool(profile_details["isMockEmail"]) or candidate_email.endswith("@test.local"),
            headline=profile_details["headline"],
            location=profile_details["location"],
            yearsExperience=float(profile_details["yearsExperience"] or 0.0),
            skills=candidate_skills,
            summary=candidate_summary,
            education=list(profile_details["education"] or []),
            projects=list(profile_details["projects"] or []),
            certifications=list(profile_details["certifications"] or []),
            companiesHistory=list(profile_details["companiesHistory"] or []),
            domainExperience=list(profile_details["domainExperience"] or []),
            resumeText=profile_details["resumeText"],
            profileData=dict(profile_details["profileData"] or {}),
            fitScore=fit_score,
            decision=decision,
            explanation=CandidateExplanation(
                semanticScore=round(semantic_similarity, 4),
                skillOverlap=round(skill_overlap, 4),
                finalScore=round(final_score, 4),
                pdlRelevance=round(pdl_relevance, 4),
                recencyScore=round(freshness_score, 4),
                engineeringScore=round(min(1.0, (0.55 * skill_overlap) + (0.45 * semantic_similarity)), 4),
                skillsMatched=_matched_skills(job_skills, candidate_skills),
                experienceMatch=_experience_match_summary(candidate_experience, job_experience),
                candidateExperience=candidate_experience,
                jobExperience=job_experience,
                penalties={
                    "semanticPenalty": round(semantic_penalty, 4),
                    "missingSkillsPenalty": round(missing_skills_penalty, 4),
                    "feedbackBias": round(feedback_bias, 4),
                    "diversityBonus": round(diversity_bonus, 4),
                    "explorationBonus": round(exploration_bonus, 4),
                    "rejectionPenalty": round(rejection_penalty, 4),
                },
                retrievalAttribution=retrieval_explanation(retrieval),
                sourceBreakdown=_explanation_source_breakdown(
                    vector_score=float(retrieval.vector_score if retrieval else semantic_similarity),
                    lexical_score=float(retrieval.lexical_score if retrieval else 0.0),
                    structured_score=float(retrieval.structured_score if retrieval else skill_overlap),
                    recruiter_score=float(recruiter_score_details.get("score") or 0.0),
                    recency_score=freshness_score,
                    session_signal=session_signal,
                    voice_score=1.0 if getattr(job, "structured_data", None) else 0.0,
                    location_score=location_match,
                    salary_score=salary_match,
                ),
                recruiterPreferenceInfluence=round(float(recruiter_score_details.get("score") or 0.0), 4),
                voiceInterviewInfluence=1.0 if getattr(job, "structured_data", None) else 0.0,
                lexicalRetrievalInfluence=round(float(retrieval.lexical_score if retrieval else 0.0), 4),
                vectorRetrievalInfluence=round(float(retrieval.vector_score if retrieval else semantic_similarity), 4),
                freshnessInfluence=round(freshness_score, 4),
                selectionRoundInfluence=round(session_signal, 4),
                aiReasoning=(
                    "Hybrid ranking blends recruiter preference signals, retrieval evidence, and selection round feedback."
                ),
            ),
            strategy=_strategy_from_score(fit_score),
            status="new",
            debug=debug_payload,
        )
        _update_diversity_counts(
            company=candidate_company,
            role=candidate_role,
            company_counts=company_counts,
            role_counts=role_counts,
        )

        upsert_candidate_chunks(
            job_id=job.id,
            candidate_id=candidate_id,
            vectors=candidate_vectors,
            chunks=candidate_chunks,
            payload={
                **({"recruiterId": recruiter_id} if recruiter_id else {}),
                "role": candidate_role,
                "summary": candidate_summary,
                "name": candidate_name,
                "company": candidate_company,
                "location": candidate_location,
                "skills": candidate_skills,
                "decision": decision,
                "finalScore": final_score,
                "email": candidate_email,
                "externalId": candidate_external_id,
                "dedupeKey": candidate_identity_key,
                "roleNorm": _normalize_identity_value(candidate_role),
                "companyNorm": _normalize_identity_value(candidate_company),
                "locationNorm": _normalize_identity_value(candidate_location),
                "skillTokens": sorted(_normalized_skill_tokens(candidate_skills)),
                "rolePattern": _normalize_identity_value(candidate_role),
                "embeddingVersion": EMBEDDING_VERSION,
            },
        )

        profile_repo.upsert(
            job_id=job.id,
            candidate_id=candidate_id,
            name=candidate_name,
            role=candidate_role,
            company=candidate_company,
            summary=candidate_summary,
            skills=candidate_skills,
            raw_data=item,
            fit_score=fit_score,
            decision=decision,
            strategy=result.strategy,
        )

        scored.append((result, final_score))
        if run_metrics_by_candidate_id is not None:
            run_metrics_by_candidate_id[candidate_id] = {
                "existing_score": existing_score,
                "final_score": final_score,
                "recruiter_score": recruiter_score,
                "recruiter_capped": bool(weight_snapshot["recruiterCapped"]),
            }
        ranking_explanation_rows.append(
            {
                "job_id": job.id,
                "candidate_id": candidate_id,
                "existing_score": existing_score,
                "recruiter_score": recruiter_score,
                "session_signal": session_signal,
                "final_score": final_score,
                "recruiter_capped": bool(weight_snapshot["recruiterCapped"]),
            }
        )

    store_ranking_explanation(db, rows=ranking_explanation_rows)

    # Safe vector refresh: only delete stale vectors AFTER all new ones are upserted.
    if scored:
        try:
            delete_candidate_vectors(job.id)
            logger.info('pdl_stale_vectors_deleted job_id=%s new_count=%s', job.id, len(scored))
        except Exception as _del_exc:
            logger.warning('pdl_stale_vector_deletion_failed job_id=%s error=%s', job.id, str(_del_exc))

    ranked = sorted(scored, key=lambda row: row[1], reverse=True)
    diverse = diversify_candidates(ranked, limit=mode_config.top_k)

    if mode == "elite":
        enriched: list[tuple[CandidateResult, float]] = []
        for index, (candidate, score) in enumerate(diverse):
            reason = ""
            bonus = 0.0
            if index < 6:
                reason, bonus = _elite_reasoning(job, candidate)
                bonus = min(weights.elite_reasoning_bonus, bonus)
            explanation = coerce_candidate_explanation(candidate.explanation)
            current_score = ranked_candidate_final_score(candidate)
            explanation.aiReasoning = reason
            new_score = round(max(0.0, min(1.0, current_score + bonus)), 4)
            setattr(explanation, "finalScore", new_score)
            candidate.explanation = explanation
            candidate.fitScore = round(new_score * 5, 2)
            candidate.decision = _decision_from_score(new_score)
            enriched.append((candidate, new_score))
        diverse = sorted(enriched, key=lambda row: row[1], reverse=True)

    return [candidate for candidate, _ in diverse]


def _dedupe_key_from_result(candidate: CandidateResult, profiles: dict[str, object]) -> str:
    profile = profiles.get(candidate.id)
    raw_data = getattr(profile, "raw_data", None) if profile else None
    if isinstance(raw_data, dict):
        key = _candidate_identity_key(raw_data)
        if key:
            return key
    return candidate.id


def _merge_candidates(
    *,
    db: Session,
    job_id: str,
    local_results: list[CandidateResult],
    pdl_results: list[CandidateResult],
    limit: int = RESULT_LIMIT,
) -> list[CandidateResult]:
    all_candidates = local_results + pdl_results
    if not all_candidates:
        return []

    candidate_ids = [candidate.id for candidate in all_candidates if candidate.id]
    profiles = CandidateProfileRepository(db).latest_by_candidate_ids(job_id=job_id, candidate_ids=candidate_ids)

    merged: dict[str, CandidateResult] = {}
    for candidate in all_candidates:
        dedupe_key = _dedupe_key_from_result(candidate, profiles)
        existing = merged.get(dedupe_key)
        if not existing or candidate.fitScore > existing.fitScore:
            merged[dedupe_key] = candidate
    return sorted(merged.values(), key=lambda row: row.fitScore, reverse=True)[:limit]


def _build_candidate_state_maps(
    db: Session,
    *,
    job_id: str,
) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str], dict[str, str], dict[str, dict[str, Any]]]:
    interview_status_map: dict[str, str] = {}
    outreach_status_map: dict[str, str] = {}
    export_status_map: dict[str, str] = {}
    ats_export_status_map: dict[str, str] = {}
    ats_state_map: dict[str, str] = {}
    enrichment_state_map: dict[str, dict[str, Any]] = {}

    for row in CandidateProfileRepository(db).list_for_job(job_id):
        ats_state_map[row.candidate_id] = (getattr(row, "ats_status", "") or "").strip().lower() or "reviewed"
        enrichment_state = dict((getattr(row, "raw_data", {}) or {}).get("enrichment") or {})
        enrichment_state_map[row.candidate_id] = {
            "status": str(enrichment_state.get("status") or "").strip().lower() or "pending",
            "source": str(enrichment_state.get("source") or "").strip().lower(),
            "confidence": float(enrichment_state.get("confidence") or 0.0),
            "contactEmail": str(
                (getattr(row, "raw_data", {}) or {}).get("email")
                or (getattr(row, "raw_data", {}) or {}).get("work_email")
                or (getattr(row, "raw_data", {}) or {}).get("personal_email")
                or ""
            ).strip(),
            "contactPhone": str((getattr(row, "raw_data", {}) or {}).get("phone") or getattr(row, "phone", "") or "").strip(),
        }

    for row in InterviewRepository(db).list_for_job(job_id):
        interview_status_map[row.candidate_id] = (row.status or "").strip().lower() or "new"

    for row in OutreachEventRepository(db).list_for_job(job_id):
        outreach_status_map[row.candidate_id] = (row.status or "").strip().lower() or "pending"

    for row in ATSExportRepository(db).list_for_job(job_id):
        export_state = (row.status or "").strip().lower() or "pending"
        export_status = "exported" if export_state == "sent" else "failed" if export_state == "failed" else "pending"
        ats_state = "sent" if export_state == "sent" else "failed" if export_state == "failed" else "not_sent"
        candidate_ids = [str(candidate_id).strip() for candidate_id in (row.candidate_ids or []) if str(candidate_id).strip()]
        if row.candidate_id and row.candidate_id not in candidate_ids:
            candidate_ids.insert(0, row.candidate_id)
        for candidate_id in candidate_ids:
            export_status_map[candidate_id] = export_status
            ats_export_status_map[candidate_id] = ats_state

    return interview_status_map, outreach_status_map, export_status_map, ats_export_status_map, ats_state_map, enrichment_state_map


def _candidate_reviewability_debug(row: Any) -> dict[str, Any]:
    raw_data = dict(getattr(row, "raw_data", {}) or {})
    email = str(
        raw_data.get("email")
        or raw_data.get("work_email")
        or raw_data.get("personal_email")
        or raw_data.get("contactEmail")
        or getattr(row, "phone", "")
        or ""
    ).strip().lower()
    source_provider = str(
        raw_data.get("sourceProvider")
        or raw_data.get("source_provider")
        or raw_data.get("source")
        or getattr(row, "sourceProvider", "")
        or getattr(row, "source_provider", "")
        or ""
    ).strip().lower()
    source_type = str(
        raw_data.get("sourceType")
        or raw_data.get("source_type")
        or getattr(row, "sourceType", "")
        or getattr(row, "source_type", "")
        or ""
    ).strip().lower()
    linkedin_url = str(_candidate_profile_url(raw_data) or getattr(row, "linkedin_url", "") or "").strip()
    is_mock_email = bool(raw_data.get("isMockEmail") or email.endswith("@test.local"))

    if is_mock_email:
        reason = "mock_email"
    elif source_provider == "xray_apollo" or source_type == "linkedin_xray":
        reason = "reviewable_xray" if linkedin_url else "missing_linkedin_url"
    elif not email:
        reason = "missing_email"
    else:
        reason = "reviewable"

    return {
        "candidateId": str(getattr(row, "candidate_id", "") or "").strip(),
        "sourceProvider": source_provider,
        "sourceType": source_type,
        "hasEmail": bool(email),
        "hasLinkedInUrl": bool(linkedin_url),
        "isMockEmail": is_mock_email,
        "reason": reason,
    }


def build_candidate_fetch_debug(
    *,
    db: Session,
    job_id: str,
    mode: str | None = None,
    refresh: bool = False,
    request_source: str = "api",
    returned_count: int = 0,
) -> dict[str, Any]:
    job = JobRepository(db).get(job_id)
    profiles = CandidateProfileRepository(db).list_for_job(job_id) if job else []
    interviews = InterviewRepository(db).list_for_job(job_id) if job else []
    outreach_rows = OutreachEventRepository(db).list_for_job(job_id) if job else []
    swiped_ids = _get_swiped_candidate_ids(db, job_id=job_id) if job else frozenset()

    reviewability_rows = [_candidate_reviewability_debug(row) for row in profiles]
    reason_counts: dict[str, int] = {}
    for row in reviewability_rows:
        reason = str(row.get("reason") or "unknown")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    reviewable_count = sum(1 for row in reviewability_rows if row.get("reason") in {"reviewable", "reviewable_xray"})
    swiped_profile_count = sum(1 for row in profiles if getattr(row, "candidate_id", "") in swiped_ids)
    removed_count = max(0, len(reviewability_rows) - reviewable_count)
    current_job_status = str(getattr(job, "job_status", "") or "").strip().lower() if job else ""
    likely_reason = "candidates_returned"
    if returned_count <= 0:
        if not profiles:
            likely_reason = "no_candidate_profiles_persisted"
        elif reviewable_count <= 0:
            if reason_counts.get("missing_linkedin_url", 0) > 0:
                likely_reason = "all_xray_hits_missing_linkedin_url"
            elif reason_counts.get("mock_email", 0) == len(reviewability_rows) and reviewability_rows:
                likely_reason = "all_candidates_are_mock_emails"
            elif reason_counts.get("missing_email", 0) == len(reviewability_rows) and reviewability_rows:
                likely_reason = "all_candidates_missing_email"
            else:
                likely_reason = "all_candidates_filtered_by_reviewability"
        elif swiped_profile_count >= len(profiles) and profiles:
            likely_reason = "all_candidates_already_swiped"
        else:
            likely_reason = "no_candidates_after_ranking_or_filtering"

    logger.info(
        "deck_rebuild_diagnostics job_id=%s profile_count=%s reviewable_count=%s removed_count=%s removal_reasons=%s",
        job_id,
        len(profiles),
        reviewable_count,
        removed_count,
        reason_counts,
    )
    log_metric(
        "deck_rebuild_diagnostics",
        job_id=job_id,
        profile_count=len(profiles),
        reviewable_count=reviewable_count,
        removed_count=removed_count,
    )

    return {
        "jobId": job_id,
        "jobStatus": current_job_status,
        "mode": (mode or "").strip().lower(),
        "refresh": bool(refresh),
        "requestSource": (request_source or "api").strip().lower() or "api",
        "sourceProvider": SOURCE_PROVIDER,
        "candidateProfileCount": len(profiles),
        "returnedCount": int(returned_count),
        "reviewableCount": int(reviewable_count),
        "swipedCount": int(len(swiped_ids)),
        "swipedProfileCount": int(swiped_profile_count),
        "interviewCount": len(interviews),
        "outreachCount": len(outreach_rows),
        "reviewabilityReasons": reason_counts,
        "reviewability": reviewability_rows[:50],
        "likelyReason": likely_reason,
    }


def _attach_candidate_workflow_state(db: Session, *, job_id: str, candidates: list[CandidateResult]) -> list[CandidateResult]:
    if not candidates:
        return candidates

    (
        interview_status_map,
        outreach_status_map,
        export_status_map,
        ats_export_status_map,
        ats_state_map,
        enrichment_state_map,
    ) = _build_candidate_state_maps(db, job_id=job_id)
    for candidate in candidates:
        export_status = export_status_map.get(candidate.id, "pending")
        ats_export_status = ats_export_status_map.get(candidate.id, "not_sent")
        outreach_status = outreach_status_map.get(candidate.id, "pending")
        status = ats_state_map.get(candidate.id) or interview_status_map.get(candidate.id, candidate.status or "new")
        enrichment_state = enrichment_state_map.get(candidate.id, {})

        if export_status == "exported":
            status = "offer_sent"
        elif outreach_status in {"sent", "dry_run", "simulated"}:
            status = ats_state_map.get(candidate.id) or "outreach_sent"

        candidate.status = status
        candidate.outreachStatus = outreach_status
        candidate.enrichmentStatus = str(enrichment_state.get("status") or "pending")
        candidate.enrichmentSource = str(enrichment_state.get("source") or "")
        candidate.enrichmentConfidence = float(enrichment_state.get("confidence") or 0.0)
        candidate.contactEmail = str(enrichment_state.get("contactEmail") or "")
        candidate.contactPhone = str(enrichment_state.get("contactPhone") or "")
        candidate.exportStatus = export_status
        candidate.ats_export_status = ats_export_status
    return candidates


def _candidate_ats_export_status(db: Session, *, job_id: str, candidate_id: str) -> str:
    rows = ATSExportRepository(db).list_for_job(job_id)
    for row in rows:
        candidate_ids = [str(candidate).strip() for candidate in (row.candidate_ids or []) if str(candidate).strip()]
        if row.candidate_id and row.candidate_id not in candidate_ids:
            candidate_ids.insert(0, row.candidate_id)
        if candidate_id not in candidate_ids:
            continue
        status = (row.status or "").strip().lower()
        if status == "sent":
            return "sent"
        if status == "failed":
            return "failed"
        return "not_sent"
    return "not_sent"


def _get_swiped_candidate_ids(db: Session, *, job_id: str) -> frozenset[str]:
    """Return IDs of candidates already swiped (shortlisted or rejected) for this job."""
    rows = CandidateFeedbackRepository(db).list_for_job(job_id)
    return frozenset(row.candidate_id for row in rows)


def _filter_unswiped_candidates(
    candidates: list[CandidateResult],
    swiped_ids: frozenset[str],
    *,
    job_id: str,
) -> list[CandidateResult]:
    """Remove already-swiped candidates from the recommendation list."""
    filtered = [c for c in candidates if c.id not in swiped_ids]
    excluded = len(candidates) - len(filtered)
    if excluded:
        logger.info(
            "recommendation_filter job_id=%s total=%s excluded_swiped=%s remaining=%s",
            job_id, len(candidates), excluded, len(filtered),
        )
        log_metric(
            "recommendation_filter",
            job_id=job_id,
            total=len(candidates),
            excluded_swiped=excluded,
            remaining=len(filtered),
        )
    return filtered


def _fallback_stored_candidates(
    *,
    db: Session,
    job_id: str,
    swiped_ids: frozenset[str],
    source: str,
    reason: str,
) -> list[CandidateResult]:
    raise APIError("Legacy sourcing paths are disabled; X-Ray retrieval is the only supported sourcing path", status_code=503)
    stored_candidates = list_stored_candidates(db=db, job_id=job_id)
    if not stored_candidates:
        return []

    filtered_stored = _filter_unswiped_candidates(stored_candidates, swiped_ids, job_id=job_id)
    fallback_candidates = filtered_stored or stored_candidates
    logger.warning(
        "fallback_to_stored_candidates job_id=%s source=%s reason=%s stored_count=%s filtered_count=%s returned_count=%s",
        job_id,
        source,
        reason,
        len(stored_candidates),
        len(filtered_stored),
        len(fallback_candidates),
    )
    log_metric(
        "fallback_to_stored_candidates",
        job_id=job_id,
        source=source,
        reason=reason,
        stored_count=len(stored_candidates),
        filtered_count=len(filtered_stored),
        returned_count=len(fallback_candidates),
    )
    return fallback_candidates


def _finalize_candidate_sourcing_state(
    *,
    db: Session,
    jobs: JobRepository,
    job,
    previous_status: str,
    source: str,
    reason: str,
    local_count: int,
    pdl_count: int,
    swiped_ids: frozenset[str],
    run_type: str,
    recruiter_id: str | None,
    combined_run_metrics: dict[str, dict[str, float | bool]],
) -> list[CandidateResult]:
    candidate_count = CandidateProfileRepository(db).count_for_job(job.id)
    outreach_triggered = candidate_count > 0
    new_status = "active" if outreach_triggered else "no_candidates"
    now = datetime.now(timezone.utc)

    jobs.update_candidate_sourcing_state(
        job_id=job.id,
        job_status=new_status,
        last_candidate_attempt_at=now,
    )
    logger.info(
        "job_sourcing_finalized job_id=%s candidate_count=%s previous_status=%s new_status=%s outreach_triggered=%s",
        job.id,
        candidate_count,
        previous_status,
        new_status,
        outreach_triggered,
    )
    log_metric(
        "job_sourcing_finalized",
        job_id=job.id,
        candidate_count=candidate_count,
        previous_status=previous_status,
        new_status=new_status,
        outreach_triggered=outreach_triggered,
        local_count=local_count,
        pdl_count=pdl_count,
        source=source,
        reason=reason,
    )

    if not outreach_triggered:
        logger.info(
            "no_candidates_detected job_id=%s local_count=%s pdl_count=%s candidate_count=%s",
            job.id,
            local_count,
            pdl_count,
            candidate_count,
        )
        log_metric(
            "no_candidates_detected",
            job_id=job.id,
            local_count=local_count,
            pdl_count=pdl_count,
            candidate_count=candidate_count,
        )
        _safe_commit(db, context="candidate_fetch_finalized_no_candidates", job_id=job.id)
        return []

    stored_candidates = _fallback_stored_candidates(
        db=db,
        job_id=job.id,
        swiped_ids=swiped_ids,
        source=source,
        reason=reason,
    )
    if not stored_candidates:
        logger.warning(
            "candidate_profiles_present_but_no_candidates_returned job_id=%s candidate_count=%s",
            job.id,
            candidate_count,
        )
        return []

    record_candidate_fetch(job_id=job.id, candidates=stored_candidates)
    _record_ranking_run(
        db=db,
        job_id=job.id,
        recruiter_id=recruiter_id,
        run_type=run_type,
        metrics=_ranking_run_metrics_for_candidates(stored_candidates, combined_run_metrics),
    )
    logger.info(
        "outreach_triggered job_id=%s candidate_count=%s returned_count=%s",
        job.id,
        candidate_count,
        len(stored_candidates),
    )
    log_metric(
        "outreach_triggered",
        job_id=job.id,
        candidate_count=candidate_count,
        returned_count=len(stored_candidates),
        source=source,
        reason=reason,
    )
    _safe_commit(db, context="candidate_fetch_finalized_with_candidates", job_id=job.id)
    return stored_candidates


def fetch_ranked_candidates(
    *,
    db: Session,
    job_id: str,
    mode: str | None = None,
    refresh: bool = False,
    debug: bool = False,
    recruiter_id: str | None = None,
    request_source: str = "api",
) -> list[CandidateResult]:
    jobs = JobRepository(db)
    job = jobs.get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)

    job_status = (job.job_status or "active").strip().lower()
    if refresh:
        logger.info("manual_refresh_triggered job_id=%s", job_id)
    elif job_status == "no_candidates":
        logger.info("no_candidates_skipped job_id=%s reason=terminal_state", job_id)
        return []

    job_mode = (getattr(job, "vetting_mode", None) or SCORING_DEFAULT_MODE or "volume").strip().lower()
    resolved_mode = _resolve_mode(mode or job_mode)
    mode_config = get_mode_config(resolved_mode)
    if APP_ENV in {"production", "prod"} and SOURCE_PROVIDER != "xray_apollo":
        raise APIError("Production discovery must use SOURCE_PROVIDER=xray_apollo", status_code=503)
    if APP_ENV in {"production", "prod"} and USE_INTERNAL_CANDIDATE_DB:
        raise APIError("Internal candidate DB sourcing is disabled in production", status_code=503)
    log_metric("retrieval_request", job_id=job.id, mode=resolved_mode, refresh=refresh)
    logger.info("dynamic_switch_applied job_id=%s mode=%s strategy=%s", job.id, mode_config.mode, mode_config.strategy)
    log_metric("dynamic_switch_applied", job_id=job.id, mode=mode_config.mode, strategy=mode_config.strategy)
    # Load swiped IDs once — used to exclude already-decided candidates server-side.
    swiped_ids = _get_swiped_candidate_ids(db, job_id=job.id)
    feedback_learning = _build_feedback_learning_context(db, job_id=job.id)
    recruiter_id = (recruiter_id or jobs.get_recruiter_id(job.id) or "").strip()
    recruiter_preferences = load_recruiter_preference_profile(db, recruiter_id) if recruiter_id else {}
    recruiter_feedback_count = _recruiter_feedback_count(db, recruiter_id)
    selection_session = CandidateSelectionSessionRepository(db).get_by_job(job.id)
    workflow_token = str(getattr(selection_session, "id", "") or "").strip()
    archetype_ids: list[str] = []
    if selection_session and isinstance(getattr(selection_session, "batch_plan", None), list):
        for batch in list(selection_session.batch_plan or []):
            if not isinstance(batch, dict):
                continue
            for candidate_id in batch.get("candidate_ids") or []:
                candidate_text = str(candidate_id or "").strip()
                if candidate_text:
                    archetype_ids.append(candidate_text)
    archetype_ids = list(dict.fromkeys(archetype_ids))
    run_type = _infer_ranking_run_type(refresh=refresh, selection_session=selection_session)
    local_run_metrics: dict[str, dict[str, float | bool]] = {}
    pdl_run_metrics: dict[str, dict[str, float | bool]] = {}
    request_source = (request_source or "api").strip().lower() or "api"

    if SOURCE_PROVIDER == "xray_apollo":
        if request_source not in {"api", "selection"}:
            logger.info(
                "xray_retrieval_skipped job_id=%s request_source=%s reason=non_interactive",
                job.id,
                request_source,
            )
            log_metric("candidate_retrieval_skipped", job_id=job.id, source="xray", request_source=request_source)
            return []
        try:
            pipeline_started = perf_counter()
            xray_target_limit = max(90, mode_config.top_k)
            xray_candidates = discover_xray_candidates(
                job=job,
                intake=getattr(job, "structured_data", None) or {},
                limit=xray_target_limit,
                pages_per_query=1,
                recruiter_preferences=recruiter_preferences,
                db=db,
                role_search_id=f"{job.id}:{recruiter_id or 'recruiter'}:{workflow_token or 'no_workflow'}",
                recruiter_id=recruiter_id,
                company_id=getattr(job, "company_id", ""),
                workflow_token=workflow_token,
                archetype_ids=archetype_ids,
            )
            xray_results = build_xray_candidate_results(
                job=job,
                candidates=xray_candidates,
                limit=xray_target_limit,
            )
            pre_rerank_count = len(xray_results)
            rerank_started = perf_counter()
            try:
                xray_results = rerank_xray_candidates(
                    db=db,
                    job=job,
                    candidates=xray_results,
                    recruiter_id=recruiter_id,
                    source_query=(getattr(job, "title", "") or getattr(job, "role", "") or "").strip(),
                )
                xray_results = xray_results[:xray_target_limit]
                logger.info(
                    "[semantic_rerank] job_id=%s recruiter_id=%s candidate_count=%s rerank_status=applied",
                    job.id,
                    recruiter_id or "",
                    len(xray_results),
                )
            except Exception as rerank_exc:
                logger.warning(
                    "[ranking_fallback] job_id=%s recruiter_id=%s candidate_count=%s rerank_status=failed error=%s",
                    job.id,
                    recruiter_id or "",
                    len(xray_results),
                    str(rerank_exc),
                )
            rerank_ms = round((perf_counter() - rerank_started) * 1000.0, 2)
            total_pipeline_ms = round((perf_counter() - pipeline_started) * 1000.0, 2)
            xray_results = sorted(xray_results, key=ranked_candidate_sort_key)
            logger.info(
                "[xray_timing] job_id=%s recruiter_id=%s query_generation_ms=%s serpapi_latency_ms=%s dedupe_ms=%s prefilter_ms=%s rerank_ms=%s total_pipeline_ms=%s",
                job.id,
                recruiter_id or "",
                0.0,
                0.0,
                0.0,
                0.0,
                rerank_ms,
                total_pipeline_ms,
            )
            log_metric(
                "xray_timing",
                job_id=job.id,
                recruiter_id=recruiter_id or "",
                query_generation_ms=0.0,
                serpapi_latency_ms=0.0,
                dedupe_ms=0.0,
                prefilter_ms=0.0,
                rerank_ms=rerank_ms,
                total_pipeline_ms=total_pipeline_ms,
                pre_rerank_count=pre_rerank_count,
                post_rerank_count=len(xray_results),
            )
            for rank, candidate in enumerate(xray_results[:5], start=1):
                explanation = coerce_candidate_explanation(getattr(candidate, "explanation", None))
                logger.info(
                    "[xray_top_candidate] job_id=%s recruiter_id=%s rank=%s candidate_id=%s name=%s role=%s company=%s fit_score=%.2f final_score=%.4f",
                    job.id,
                    recruiter_id or "",
                    rank,
                    candidate.id,
                    candidate.name,
                    candidate.role,
                    candidate.company,
                    float(candidate.fitScore or 0.0),
                    float(explanation.finalScore or 0.0),
            )
            profile_repo = CandidateProfileRepository(db)
            refresh_queue_jobs: list[dict[str, str]] = []
            for candidate in xray_results:
                existing_profile = profile_repo.get(job_id=job.id, candidate_id=candidate.id)
                existing_raw_data = dict(getattr(existing_profile, "raw_data", {}) or {}) if existing_profile else {}
                new_raw_data = candidate.model_dump()
                raw_data_changed = not existing_profile or _candidate_refresh_fingerprint(existing_raw_data) != _candidate_refresh_fingerprint(new_raw_data)
                profile_repo.upsert(
                    job_id=job.id,
                    candidate_id=candidate.id,
                    name=candidate.name,
                    role=candidate.role,
                    company=candidate.company,
                    summary=candidate.summary,
                    skills=list(candidate.skills or []),
                    raw_data=candidate.model_dump(),
                    fit_score=float(candidate.fitScore or 0.0),
                    decision=candidate.decision or "potential",
                    strategy=candidate.strategy or "MEDIUM",
                )
                if raw_data_changed:
                    refresh_queue_jobs.append({"job_id": job.id, "candidate_id": candidate.id})
            _safe_commit(db, context="candidate_fetch_xray_refresh_queue_commit", job_id=job.id)
            if refresh_queue_jobs:
                from app.services.job_queue_service import enqueue_job

                queued_count = 0
                for payload in refresh_queue_jobs:
                    try:
                        enqueue_job(
                            "candidate_refresh",
                            payload,
                            idempotency_key=f"candidate-refresh:{payload['job_id']}:{payload['candidate_id']}",
                        )
                        queued_count += 1
                    except Exception as exc:
                        logger.warning(
                            "xray_candidate_refresh_queue_failed job_id=%s candidate_id=%s error=%s",
                            job.id,
                            payload.get("candidate_id", ""),
                            str(exc),
                            exc_info=exc,
                        )
                logger.info(
                    "xray_candidate_refresh_queued job_id=%s count=%s",
                    job.id,
                    queued_count,
                )
            raw_xray_count = len(xray_results)
            reviewable_seed_candidates = list(xray_results)
            display_limit = 20  # Recruiter view always gets the top 20 from the ranked X-Ray pool.
            xray_results = _filter_unswiped_candidates(
                _attach_candidate_workflow_state(db, job_id=job.id, candidates=reviewable_seed_candidates[:display_limit]),
                swiped_ids,
                job_id=job.id,
            )
            unswiped_xray_count = len(xray_results)
            xray_results = [candidate for candidate in xray_results if _is_reviewable_candidate(candidate)]
            already_swiped_count = max(0, unswiped_xray_count - len(xray_results))
            removed_reviewability_count = max(0, unswiped_xray_count - len(xray_results))
            logger.info(
                "candidate_retrieval_diagnostics job_id=%s source=xray raw_candidate_count=%s normalized_count=%s duplicate_count=%s invalid_url_count=%s already_swiped_count=%s reviewable_count=%s",
                job.id,
                len(reviewable_seed_candidates),
                len(reviewable_seed_candidates),
                0,
                0,
                already_swiped_count,
                len(xray_results),
            )
            log_metric(
                "candidate_retrieval_diagnostics",
                job_id=job.id,
                source="xray",
                raw_candidate_count=len(reviewable_seed_candidates),
                normalized_count=len(reviewable_seed_candidates),
                duplicate_count=0,
                invalid_url_count=0,
                already_swiped_count=already_swiped_count,
                reviewable_count=len(xray_results),
            )
            if not xray_results:
                logger.warning(
                    "xray_reviewable_candidates_missing job_id=%s raw_count=%s unswiped_count=%s reason=using_raw_xray_pool likely_reason=all_candidates_filtered_by_reviewability",
                    job.id,
                    raw_xray_count,
                    unswiped_xray_count,
                )
                return reviewable_seed_candidates[:display_limit]
            logger.info(
                "candidate_filter_counts job_id=%s source=xray raw_count=%s unswiped_count=%s reviewable_count=%s",
                job.id,
                raw_xray_count,
                unswiped_xray_count,
                len(xray_results),
            )
            log_metric(
                "candidate_filter_counts",
                job_id=job.id,
                source="xray",
                raw_count=raw_xray_count,
                unswiped_count=unswiped_xray_count,
                reviewable_count=len(xray_results),
            )
            logger.info(
                "xray_only_candidates job_id=%s count=%s source_provider=%s",
                job.id,
                len(xray_results),
                SOURCE_PROVIDER,
            )
            log_metric("candidate_count", job_id=job.id, count=len(xray_results), mode=resolved_mode, source="xray")
            _safe_commit(db, context="candidate_fetch_xray", job_id=job.id)
            emit_trace(
                logger,
                "candidate_ranking_ready",
                job_id=job.id,
                recruiter_id=recruiter_id,
                source="xray",
                mode=resolved_mode,
                returned_count=len(xray_results),
                reviewable_count=len(xray_results),
            )
            record_candidate_fetch(job_id=job.id, candidates=xray_results)
            logger.info(
                "[xray_candidates] job_id=%s recruiter_id=%s candidate_count=%s rerank_status=%s",
                job.id,
                recruiter_id or "",
                len(xray_results),
                "applied" if xray_results else "empty",
            )
            return xray_results[:display_limit]
        except Exception as exc:
            logger.warning("xray_candidate_retrieval_failed job_id=%s error=%s", job.id, str(exc))
            log_metric("candidate_retrieval_error", job_id=job.id, mode=resolved_mode, source="xray", error_type=type(exc).__name__)
            return []

    raise APIError(
        "Legacy sourcing paths are disabled; X-Ray retrieval is the only supported sourcing path",
        status_code=503,
    )

    local_diversity_seed = 0.0
    seed_confidence = _compute_system_confidence(
        similarity=0.0,
        diversity=local_diversity_seed,
        feedback_success=max(
            feedback_learning.job_success_rate,
            feedback_learning.global_success_rate,
        ),
    )
    exploration_rate = _resolve_exploration_rate(
        diversity=local_diversity_seed,
        feedback_success=max(
            feedback_learning.job_success_rate,
            feedback_learning.global_success_rate,
        ),
        system_confidence=seed_confidence,
    )
    exploration = ExplorationContext(rate=exploration_rate, system_confidence=seed_confidence)
    logger.info(
        "learned_query_tokens job_id=%s tokens=%s preferred_roles=%s",
        job.id,
        ",".join(feedback_learning.learned_query_tokens),
        ",".join(feedback_learning.preferred_roles),
    )
    log_metric(
        "learned_query_tokens",
        job_id=job.id,
        tokens="|".join(feedback_learning.learned_query_tokens),
    )
    try:
        local_results = _build_local_candidates(
            db=db,
            job=job,
            mode=resolved_mode,
            mode_config=mode_config,
            feedback_learning=feedback_learning,
            recruiter_preferences=recruiter_preferences,
            exploration=exploration,
            debug=debug,
            run_metrics_by_candidate_id=local_run_metrics,
        )
    except Exception as exc:
        logger.warning("local_candidate_retrieval_failed job_id=%s mode=%s error=%s", job.id, resolved_mode, str(exc))
        log_metric(
            "candidate_retrieval_error",
            job_id=job.id,
            mode=resolved_mode,
            source="local",
            error_type=type(exc).__name__,
        )
        local_results = []
    avg_local_similarity = mean([row.explanation.semanticScore for row in local_results]) if local_results else 0.0
    local_top_score = local_results[0].explanation.semanticScore if local_results else 0.0
    qdrant_hit = len(local_results) > 0
    logger.info(
        "qdrant_retrieval_result job_id=%s local_candidate_count=%s top_score=%.4f qdrant_hit=%s",
        job.id,
        len(local_results),
        local_top_score,
        qdrant_hit,
    )
    log_metric(
        "qdrant_retrieval",
        job_id=job.id,
        local_candidate_count=len(local_results),
        top_score=round(local_top_score, 4),
        qdrant_hit=qdrant_hit,
        fallback_reason="none" if qdrant_hit else "local_candidates_empty",
    )
    log_metric("retrieval_similarity", job_id=job.id, mode=resolved_mode, value=round(avg_local_similarity, 4))

    local_top_semantic = local_results[0].explanation.semanticScore if local_results else 0.0
    adaptive_threshold = _adaptive_local_threshold(local_results)
    local_count = len(local_results)
    candidate_diversity = _candidate_diversity_score(local_results)
    feedback_success = max(feedback_learning.job_success_rate, feedback_learning.global_success_rate)
    system_confidence = _compute_system_confidence(
        similarity=avg_local_similarity,
        diversity=candidate_diversity,
        feedback_success=feedback_success,
    )
    exploration.system_confidence = system_confidence
    exploration.rate = _resolve_exploration_rate(
        diversity=candidate_diversity,
        feedback_success=feedback_success,
        system_confidence=system_confidence,
    )
    log_metric(
        "adaptive_exploration_rate",
        job_id=job.id,
        rate=round(exploration.rate, 4),
        confidence=round(system_confidence, 4),
        feedback_success=round(feedback_success, 4),
        diversity=round(candidate_diversity, 4),
    )
    logger.info(
        "adaptive_exploration_rate job_id=%s rate=%.4f confidence=%.4f feedback_success=%.4f diversity=%.4f",
        job.id,
        exploration.rate,
        system_confidence,
        feedback_success,
        candidate_diversity,
    )
    switching_mode, switch_reason = _decide_switching_mode(
        refresh=refresh,
        local_count=local_count,
        similarity_score=avg_local_similarity,
        feedback_success_rate=feedback_success,
        candidate_diversity=candidate_diversity,
    )
    if USE_INTERNAL_CANDIDATE_DB:
        switching_mode = "local"
        switch_reason = "internal_candidate_db_enabled"

    logger.info(
        "switch_decision job_id=%s mode=%s reason=%s local_count=%s avg_similarity=%.4f top_semantic=%.4f diversity=%.4f feedback_success=%.4f confidence=%.4f threshold=%.4f refresh=%s",
        job_id,
        switching_mode,
        switch_reason,
        local_count,
        avg_local_similarity,
        local_top_semantic,
        candidate_diversity,
        feedback_success,
        system_confidence,
        adaptive_threshold,
        refresh,
    )
    log_metric(
        "switching_mode",
        job_id=job.id,
        selected_mode=switching_mode,
        reason=switch_reason,
        similarity=round(avg_local_similarity, 4),
        feedback_success=round(feedback_success, 4),
        diversity=round(candidate_diversity, 4),
        confidence=round(system_confidence, 4),
    )
    pdl_disabled = is_pdl_disabled()
    if USE_INTERNAL_CANDIDATE_DB:
        pdl_disabled = True
    if pdl_disabled:
        reason = "internal_candidate_db_enabled" if USE_INTERNAL_CANDIDATE_DB else "service_disabled"
        logger.warning("pdl_disabled job_id=%s reason=%s", job_id, reason)
        log_metric("pdl_disabled", job_id=job.id, mode=resolved_mode, reason=reason)
    pdl_allowed = _allow_pdl_when_qdrant_is_unhealthy() and not pdl_disabled
    should_call_pdl = switching_mode in {"pdl", "pdl_with_local_fallback"} and not pdl_disabled

    if not should_call_pdl:
        # similarity >= 0.60 and local results exist — serve local directly.
        logger.info(
            "local_hit job_id=%s count=%s top_semantic=%.4f avg_similarity=%.4f",
            job_id,
            len(local_results),
            local_top_semantic,
            avg_local_similarity,
        )
        log_metric("candidate_count", job_id=job.id, count=len(local_results), mode=resolved_mode, source="local")
        log_metric("local_hit", job_id=job.id, mode=resolved_mode, top_semantic=round(local_top_semantic, 4))
        _safe_commit(db, context="candidate_fetch_local_hit", job_id=job.id)
        final_local = _filter_unswiped_candidates(
            _attach_candidate_workflow_state(db, job_id=job.id, candidates=local_results[: mode_config.top_k]),
            swiped_ids,
            job_id=job.id,
        )
        unswiped_local_count = len(final_local)
        final_local = [candidate for candidate in final_local if _is_reviewable_candidate(candidate)]
        logger.info(
            "candidate_filter_counts job_id=%s source=local raw_count=%s unswiped_count=%s reviewable_count=%s",
            job.id,
            len(local_results),
            unswiped_local_count,
            len(final_local),
        )
        log_metric(
            "candidate_filter_counts",
            job_id=job.id,
            source="local",
            raw_count=len(local_results),
            unswiped_count=unswiped_local_count,
            reviewable_count=len(final_local),
        )
        emit_trace(
            logger,
            "candidate_ranking_ready",
            job_id=job.id,
            recruiter_id=recruiter_id,
            source="local",
            mode=resolved_mode,
            returned_count=len(final_local),
            reviewable_count=len(final_local),
        )
        if not final_local:
            return _finalize_candidate_sourcing_state(
                db=db,
                jobs=jobs,
                job=job,
                previous_status=job_status,
                source="local",
                reason="no_candidates_after_filter",
                local_count=len(local_results),
                pdl_count=0,
                swiped_ids=swiped_ids,
                run_type=run_type,
                recruiter_id=recruiter_id,
                combined_run_metrics=local_run_metrics,
            )
        record_candidate_fetch(job_id=job.id, candidates=final_local)
        _record_ranking_run(
            db=db,
            job_id=job.id,
            recruiter_id=recruiter_id,
            run_type=run_type,
            metrics=_ranking_run_metrics_for_candidates(final_local, local_run_metrics),
        )
        logger.info("candidates_returned count=%s", len(final_local))
        return final_local

    # PDL is required (low similarity or empty local). Check if PDL is healthy.
    if not pdl_allowed and not SERPAPI_ENABLED:
        logger.warning(
            "pdl_suppressed_due_to_qdrant_error job_id=%s qdrant_error=%s — serving local fallback",
            job_id,
            last_qdrant_search_error() or "unknown",
        )
        log_metric("pdl_suppressed", job_id=job.id, mode=resolved_mode, reason="qdrant_error_backoff")
        _safe_commit(db, context="candidate_fetch_qdrant_suppressed", job_id=job.id)
        final_suppressed = _filter_unswiped_candidates(
            _attach_candidate_workflow_state(db, job_id=job.id, candidates=local_results[: mode_config.top_k]),
            swiped_ids,
            job_id=job.id,
        )
        unswiped_suppressed_count = len(final_suppressed)
        final_suppressed = [candidate for candidate in final_suppressed if _is_reviewable_candidate(candidate)]
        logger.info(
            "candidate_filter_counts job_id=%s source=local_fallback raw_count=%s unswiped_count=%s reviewable_count=%s",
            job.id,
            len(local_results),
            unswiped_suppressed_count,
            len(final_suppressed),
        )
        log_metric(
            "candidate_filter_counts",
            job_id=job.id,
            source="local_fallback",
            raw_count=len(local_results),
            unswiped_count=unswiped_suppressed_count,
            reviewable_count=len(final_suppressed),
        )
        emit_trace(
            logger,
            "candidate_ranking_ready",
            job_id=job.id,
            recruiter_id=recruiter_id,
            source="local_fallback",
            mode=resolved_mode,
            returned_count=len(final_suppressed),
            reviewable_count=len(final_suppressed),
        )
        if not final_suppressed:
            return _finalize_candidate_sourcing_state(
                db=db,
                jobs=jobs,
                job=job,
                previous_status=job_status,
                source="local_fallback",
                reason="no_candidates_after_filter",
                local_count=len(local_results),
                pdl_count=0,
                swiped_ids=swiped_ids,
                run_type=run_type,
                recruiter_id=recruiter_id,
                combined_run_metrics=local_run_metrics,
            )
        record_candidate_fetch(job_id=job.id, candidates=final_suppressed)
        _record_ranking_run(
            db=db,
            job_id=job.id,
            recruiter_id=recruiter_id,
            run_type=run_type,
            metrics=_ranking_run_metrics_for_candidates(final_suppressed, local_run_metrics),
        )
        logger.info("candidates_returned count=%s", len(final_suppressed))
        return final_suppressed

    logger.info(
        "pdl_call job_id=%s reason=%s local_count=%s avg_similarity=%.4f",
        job_id,
        switch_reason,
        local_count,
        avg_local_similarity,
    )
    log_metric("pdl_call", job_id=job.id, mode=resolved_mode, reason=switch_reason)

    size = max(PDL_SEARCH_SIZE, mode_config.top_k)
    pdl_results: list[CandidateResult] = []
    serpapi_results: list[CandidateResult] = []
    serpapi_run_metrics: dict[str, dict[str, float | bool]] = {}
    if should_call_pdl:
        try:
            serpapi_candidates = discover_linkedin_xray_candidates(
                job=job,
                intake=getattr(job, "structured_data", None) or {},
                limit=max(90, size),
                pages_per_query=1,
                recruiter_preferences=recruiter_preferences,
                db=db,
                role_search_id=f"{job.id}:{recruiter_id or 'recruiter'}:{workflow_token or 'no_workflow'}",
                recruiter_id=recruiter_id,
                company_id=getattr(job, "company_id", ""),
                workflow_token=workflow_token,
                archetype_ids=archetype_ids,
            )
            if serpapi_candidates:
                serpapi_results = _build_ranked_candidates_from_pdl(
                    db=db,
                    job=job,
                    mode=resolved_mode,
                    size=size,
                    mode_config=mode_config,
                    feedback_learning=feedback_learning,
                    exploration=exploration,
                    recruiter_preferences=recruiter_preferences,
                    recruiter_feedback_count=recruiter_feedback_count,
                    selection_session=selection_session,
                    debug=debug,
                    run_metrics_by_candidate_id=serpapi_run_metrics,
                    source_candidates=serpapi_candidates,
                    source_label="serpapi",
                )
        except Exception as exc:
            logger.warning("serpapi_candidate_retrieval_failed job_id=%s mode=%s error=%s", job.id, resolved_mode, str(exc))
            log_metric(
                "candidate_retrieval_error",
                job_id=job.id,
                mode=resolved_mode,
                source="serpapi",
                error_type=type(exc).__name__,
            )

    if serpapi_results:
        pdl_results = serpapi_results
        pdl_run_metrics = serpapi_run_metrics

    try:
        if not serpapi_results and pdl_allowed:
            pdl_results = _build_ranked_candidates_from_pdl(
                db=db,
                job=job,
                mode=resolved_mode,
                size=size,
                mode_config=mode_config,
                feedback_learning=feedback_learning,
                exploration=exploration,
                recruiter_preferences=recruiter_preferences,
                recruiter_feedback_count=recruiter_feedback_count,
                selection_session=selection_session,
                debug=debug,
                run_metrics_by_candidate_id=pdl_run_metrics,
            )
    except Exception as exc:
        logger.warning("pdl_candidate_retrieval_failed job_id=%s mode=%s error=%s", job.id, resolved_mode, str(exc))
        log_metric(
            "candidate_retrieval_error",
            job_id=job.id,
            mode=resolved_mode,
            source="pdl",
            error_type=type(exc).__name__,
        )
        pdl_results = []

    if pdl_results:
        # PDL responded with candidates — use them (merged with local if available).
        if local_results:
            candidates = _merge_candidates(
                db=db,
                job_id=job_id,
                local_results=local_results,
                pdl_results=pdl_results,
                limit=mode_config.top_k,
            )
            log_metric("candidate_count", job_id=job.id, count=len(candidates), mode=resolved_mode, source="pdl_merged_local")
            logger.info(
                "pdl_merged_with_local job_id=%s local_count=%s pdl_count=%s merged_count=%s",
                job_id, len(local_results), len(pdl_results), len(candidates),
            )
        else:
            candidates = pdl_results
            log_metric("candidate_count", job_id=job.id, count=len(candidates), mode=resolved_mode, source="pdl")
            logger.info(
                "pdl_only job_id=%s pdl_count=%s",
                job_id, len(pdl_results),
            )
    else:
        # PDL returned nothing — fall back to local Qdrant results with fitScore > 2.5.
        # Candidates below 2.5 are too weak to show when PDL couldn't supplement them.
        filtered_local = [c for c in local_results if c.fitScore > 2.5]
        logger.warning(
            "pdl_empty_fallback_to_local job_id=%s local_count=%s filtered_count=%s avg_similarity=%.4f",
            job_id,
            len(local_results),
            len(filtered_local),
            avg_local_similarity,
        )
        log_metric(
            "pdl_empty_fallback",
            job_id=job.id,
            mode=resolved_mode,
            reason="pdl_returned_no_candidates",
            local_total=len(local_results),
            filtered_above_2_5=len(filtered_local),
        )
        candidates = filtered_local
        log_metric("candidate_count", job_id=job.id, count=len(candidates), mode=resolved_mode, source="local_fallback")

    combined_run_metrics = {**local_run_metrics, **pdl_run_metrics}
    now = datetime.now(timezone.utc)
    if not candidates:
        logger.warning(
            "candidate_ranking_empty job_id=%s source=%s reason=pdl_empty_or_filtered local_count=%s pdl_count=%s swiped_count=%s",
            job.id,
            resolved_mode,
            local_count,
            len(pdl_results) if pdl_results else 0,
            len(swiped_ids),
        )
        return _finalize_candidate_sourcing_state(
            db=db,
            jobs=jobs,
            job=job,
            previous_status=job_status,
            source=resolved_mode,
            reason="pdl_empty_or_filtered",
            local_count=local_count,
            pdl_count=len(pdl_results) if pdl_results else 0,
            swiped_ids=swiped_ids,
            run_type=run_type,
            recruiter_id=recruiter_id,
            combined_run_metrics=combined_run_metrics,
        )

    jobs.update_candidate_sourcing_state(
        job_id=job.id,
        job_status="active",
        last_candidate_attempt_at=now,
    )
    log_metric(
        "exploration_usage",
        job_id=job.id,
        selected_mode=switching_mode,
        rate=round(exploration.rate, 4),
        used=exploration.used,
        total=exploration.total,
    )

    _safe_commit(db, context="candidate_fetch_final_state", job_id=job_id)
    notify_slack(
        title="Pontis Candidates Ready",
        lines=[
            f"job_id={job.id}",
            f"mode={resolved_mode}",
            f"count={len(candidates)}",
            f"switch={switching_mode}",
        ],
    )
    final_candidates = _filter_unswiped_candidates(
        _attach_candidate_workflow_state(db, job_id=job.id, candidates=candidates),
        swiped_ids,
        job_id=job.id,
    )
    unswiped_final_count = len(final_candidates)
    final_candidates = [candidate for candidate in final_candidates if _is_reviewable_candidate(candidate)]
    logger.info(
        "candidate_filter_counts job_id=%s source=%s raw_count=%s unswiped_count=%s reviewable_count=%s",
        job.id,
        resolved_mode,
        len(candidates),
        unswiped_final_count,
        len(final_candidates),
    )
    log_metric(
        "candidate_filter_counts",
        job_id=job.id,
        source=resolved_mode,
        raw_count=len(candidates),
        unswiped_count=unswiped_final_count,
        reviewable_count=len(final_candidates),
    )
    emit_trace(
        logger,
        "candidate_ranking_ready",
        job_id=job.id,
        recruiter_id=recruiter_id,
        source=resolved_mode,
        switch=switching_mode,
        mode=resolved_mode,
        returned_count=len(final_candidates),
        reviewable_count=len(final_candidates),
        recruiter_signal_strength=recruiter_preferences.get("signal_strength", 0.0) if recruiter_preferences else 0.0,
    )
    record_candidate_fetch(job_id=job.id, candidates=final_candidates)
    _record_ranking_run(
        db=db,
        job_id=job.id,
        recruiter_id=recruiter_id,
        run_type=run_type,
        metrics=_ranking_run_metrics_for_candidates(final_candidates, combined_run_metrics),
    )
    logger.info("candidates_returned count=%s", len(final_candidates))
    return final_candidates


def build_selection_candidate_snapshot(
    *,
    db: Session,
    job_id: str,
    mode: str | None = None,
    refresh: bool = False,
    limit: int = 12,
) -> list[CandidateResult]:
    """Return the real retrieved candidates used to seed the 3-round preference flow."""
    def _reachable(candidate: CandidateResult) -> bool:
        email = _normalize_text(candidate.email or "").lower()
        if not email or "@" not in email:
            source_provider = _normalize_text(
                getattr(candidate, "sourceProvider", "") or ((candidate.profileData or {}).get("source_provider") if candidate.profileData else "") or ""
            ).lower()
            source_type = _normalize_text(
                getattr(candidate, "sourceType", "") or ((candidate.profileData or {}).get("source_type") if candidate.profileData else "") or ""
            ).lower()
            if source_provider == "xray_apollo" or source_type == "linkedin_xray":
                return True
            return False
        if candidate.isMockEmail:
            return False
        if email.endswith("@test.local"):
            return False
        return True

    candidates = fetch_ranked_candidates(db=db, job_id=job_id, mode=mode, refresh=refresh)
    collected: list[CandidateResult] = []
    seen_ids: set[str] = set()
    for candidate in candidates:
        candidate_id = str(candidate.id or "").strip()
        if not candidate_id or candidate_id in seen_ids:
            continue
        if not _reachable(candidate):
            continue
        collected.append(candidate)
        seen_ids.add(candidate_id)
        if len(collected) >= max(2, limit):
            break

    return collected


def warm_candidate_retrieval() -> int:
    ensure_all_collections()
    preloaded = preload_sample_candidate_embeddings()
    logger.info("candidate_retrieval_warmup embeddings_preloaded=%s", preloaded)
    return preloaded


def apply_feedback(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    action: str,
    actor_id: str | None = None,
    company_id: str | None = None,
    slack_team_id: str = "",
    slack_user_id: str = "",
    slack_installation_id: str | None = None,
) -> dict:
    jobs = JobRepository(db)
    job = jobs.get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)

    action = action.strip().lower()
    if action not in {"accept", "reject"}:
        raise APIError("action must be accept or reject", status_code=400)

    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if not profile:
        raise APIError("Candidate not found for this job", status_code=404)

    # ── State machine: resolve current status ──────────────────────────────────
    interview_repo = InterviewRepository(db)
    existing_interview = interview_repo.get_by_job_and_candidate(job_id, candidate_id)
    current_status = (existing_interview.status if existing_interview else None) or "new"
    target_status = swipe_to_status(action)

    # ── Idempotency: same action already applied → return success immediately ──
    if current_status == target_status:
        current_ats_status = _candidate_ats_export_status(db, job_id=job_id, candidate_id=candidate_id)
        logger.info(
            "swipe_idempotent job_id=%s candidate_id=%s action=%s status=%s",
            job_id, candidate_id, action, current_status,
        )
        return {
            "jobId": job_id,
            "candidateId": candidate_id,
            "action": action,
            "previousState": current_status,
            "newState": current_status,
            "exportStatus": "exported" if current_ats_status == "sent" else "failed" if current_ats_status == "failed" else "pending",
            "ats_export_status": current_ats_status,
            "message": "Already recorded — no change.",
        }

    # ── State machine: enforce allowed transitions ─────────────────────────────
    # selected candidates can still be explicitly rejected later.
    # assert_valid_transition covers the full transition table.
    if is_swipe_locked(current_status) and not (current_status == "selected" and action == "reject"):
        logger.warning(
            "swipe_blocked job_id=%s candidate_id=%s current_status=%s action=%s",
            job_id, candidate_id, current_status, action,
        )
        raise APIError(
            f"Cannot swipe candidate in '{current_status}' state. "
            "Only explicit reject is allowed after shortlist selection.",
            status_code=409,
        )

    assert_valid_transition(
        candidate_id=candidate_id,
        job_id=job_id,
        from_status=current_status,
        to_status=target_status,
    )

    # ── Persist feedback (idempotent upsert) ───────────────────────────────────
    existing_feedback = CandidateFeedbackRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    is_new_feedback = existing_feedback is None

    scoring_repo = ScoringProfileRepository(db)
    before_profile = scoring_repo.get_or_create(job_id=job_id)
    recruiter_id = jobs.get_recruiter_id(job_id)
    selection_session = CandidateSelectionSessionRepository(db).get_by_job(job_id)
    session_id = selection_session.id if selection_session else None
    before_weights = {
        "pdl": round(float(before_profile.weight_pdl), 6),
        "semantic": round(float(before_profile.weight_semantic), 6),
        "skill": round(float(before_profile.weight_skill), 6),
        "recency": round(float(before_profile.weight_recency), 6),
        "feedback_bias": round(float(before_profile.feedback_bias), 6),
    }

    CandidateFeedbackRepository(db).upsert(
        job_id=job_id,
        candidate_id=candidate_id,
        feedback=action,
        recruiter_id=recruiter_id,
        session_id=session_id,
    )
    feedback_row = CandidateFeedbackRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if feedback_row:
        feedback_row.company_id = (company_id or getattr(feedback_row, "company_id", None) or job.company_id or "").strip() or None
        feedback_row.recruiter_id = (actor_id or recruiter_id or feedback_row.recruiter_id or "").strip() or None
        feedback_row.slack_team_id = (slack_team_id or getattr(feedback_row, "slack_team_id", "") or "").strip()
        feedback_row.slack_user_id = (slack_user_id or getattr(feedback_row, "slack_user_id", "") or "").strip()
        feedback_row.slack_installation_id = (slack_installation_id or getattr(feedback_row, "slack_installation_id", None) or "").strip() or None
        db.flush()
    if is_new_feedback and recruiter_id:
        update_recruiter_preferences(
            db,
            recruiter_id,
            profile if action == "accept" else None,
            [] if action == "accept" else [profile],
            signal_multiplier=2.0 if action == "accept" else 0.5,
        )
    if is_new_feedback:
        lifecycle_event_type = "CANDIDATE_SAVED" if action == "accept" else "CANDIDATE_REJECTED"
        record_job_lifecycle_event(
            db=db,
            job_id=job_id,
            event_type=lifecycle_event_type,
            payload={
                "jobId": job_id,
                "candidateId": candidate_id,
                "action": action,
                "source": "candidate_feedback",
            },
            source="candidate_feedback",
        )
        transition_candidate_ats_state(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            to_status="selected" if action == "accept" else "rejected",
            source="candidate_feedback",
            actor_id=actor_id or recruiter_id,
            slack_team_id=slack_team_id,
            slack_user_id=slack_user_id,
            slack_installation_id=slack_installation_id,
            reason="recruiter_feedback",
            metadata={
                "action": action,
                "feedbackSource": "candidate_feedback",
                "slackTeamId": slack_team_id,
                "slackUserId": slack_user_id,
                "companyId": company_id or job.company_id,
            },
        )

    # Only run RLHF weight update for genuinely new feedback signals.
    # Re-submitting the same action is already handled by idempotency above.
    # A changed action (accept→reject) is blocked by state machine above.
    # So reaching here always means is_new_feedback=True in practice,
    # but we guard explicitly for safety.
    if is_new_feedback:
        after_profile = scoring_repo.apply_feedback_adjustment(job_id=job_id, feedback=action)
    else:
        after_profile = before_profile

    after_weights = {
        "pdl": round(float(after_profile.weight_pdl), 6),
        "semantic": round(float(after_profile.weight_semantic), 6),
        "skill": round(float(after_profile.weight_skill), 6),
        "recency": round(float(after_profile.weight_recency), 6),
        "feedback_bias": round(float(after_profile.feedback_bias), 6),
    }

    # ── Update interview status (state transition) ─────────────────────────────
    interview_repo.upsert_status(
        job_id=job_id,
        candidate_id=candidate_id,
        status=target_status,
        create_default=target_status,
    )

    ats_export_status = "not_sent"
    export_status = "pending"
    if target_status == "selected" and bool(getattr(job, "auto_export_to_ats", False)):
        try:
            export_result = export_candidate_to_ats(profile, job, db=db)
            export_status = "exported" if export_result.get("status") == "sent" else "failed"
            ats_export_status = "sent" if export_result.get("status") == "sent" else "failed"
        except Exception as exc:
            export_status = "failed"
            ats_export_status = "failed"
            logger.warning(
                "ats_auto_export_failed job_id=%s candidate_id=%s error=%s",
                job_id,
                candidate_id,
                str(exc),
            )

    _safe_commit(db, context="candidate_feedback_commit", job_id=job_id)

    enrichment_result: dict[str, Any] | None = None
    if action == "accept":
        try:
            from app.services.automation_service import schedule_automation_job

            enrichment_result = schedule_automation_job(
                db=db,
                automation_type="candidate_enrichment",
                job_id=job_id,
                candidate_id=candidate_id,
                run_at=datetime.now(timezone.utc),
                payload={
                    "feedbackAction": action,
                    "sourceType": "dashboard",
                },
                automation_key=f"candidate-enrichment:{job_id}:{candidate_id}",
            )
        except Exception as exc:
            logger.warning(
                "auto_enrichment_failed job_id=%s candidate_id=%s error=%s",
                job_id,
                candidate_id,
                str(exc),
                exc_info=exc,
            )

    # ── Observability ──────────────────────────────────────────────────────────
    feedback_count = CandidateFeedbackRepository(db).count_for_job(job_id)
    rlhf_direction = "positive" if action == "accept" else "negative"
    weight_deltas = {
        k: round(after_weights[k] - before_weights[k], 6)
        for k in before_weights
    }

    logger.info(
        "swipe_recorded job_id=%s candidate_id=%s action=%s "
        "previous_state=%s new_state=%s is_new_feedback=%s",
        job_id, candidate_id, action, current_status, target_status, is_new_feedback,
    )
    logger.info(
        "rlhf_update job_id=%s candidate_id=%s direction=%s "
        "feedback_bias_before=%.6f feedback_bias_after=%.6f "
        "semantic_delta=%.6f skill_delta=%.6f pdl_delta=%.6f feedback_bias_delta=%.6f",
        job_id, candidate_id, rlhf_direction,
        before_weights["feedback_bias"], after_weights["feedback_bias"],
        weight_deltas["semantic"], weight_deltas["skill"],
        weight_deltas["pdl"], weight_deltas["feedback_bias"],
    )

    log_metric("feedback_count", job_id=job_id, count=feedback_count)
    log_metric(
        "rlhf_weight_update",
        job_id=job_id,
        candidate_id=candidate_id,
        action=action,
        direction=rlhf_direction,
        is_new_feedback=is_new_feedback,
        **{f"before_{k}": v for k, v in before_weights.items()},
        **{f"after_{k}": v for k, v in after_weights.items()},
        **{f"delta_{k}": v for k, v in weight_deltas.items()},
    )
    notify_slack(
        title="Pontis Candidate Feedback",
        lines=[
            f"job_id={job_id}",
            f"candidate_id={candidate_id}",
            f"action={action}",
            f"state={current_status} → {target_status}",
            f"rlhf={rlhf_direction} bias_delta={weight_deltas['feedback_bias']:+.6f}",
        ],
    )

    return {
        "jobId": job_id,
        "candidateId": candidate_id,
        "action": action,
        "previousState": current_status,
        "newState": target_status,
        "exportStatus": export_status,
        "ats_export_status": ats_export_status,
        "enrichment": enrichment_result or {},
        "message": "Feedback recorded and ranking weights updated",
    }


def list_shortlisted_candidates(*, db: Session, job_id: str) -> list[CandidateResult]:
    """Return only shortlisted candidates for a job — used by the outreach page."""
    jobs = JobRepository(db)
    job = jobs.get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)

    selection_session = CandidateSelectionSessionRepository(db).get_by_job(job_id)
    final_snapshot_lookup: dict[str, CandidateResult] = {}
    if selection_session and (selection_session.final_candidate_snapshot or []):
        for row in selection_session.final_candidate_snapshot or []:
            try:
                candidate = CandidateResult.model_validate(row)
            except Exception:
                continue
            final_snapshot_lookup[candidate.id] = candidate

    if selection_session and (selection_session.status or "").strip().lower() == "completed" and final_snapshot_lookup:
        shortlisted_ids = [
            candidate_id
            for candidate_id in final_snapshot_lookup.keys()
        ]
    else:
        interview_rows = InterviewRepository(db).list_for_job(job_id)
        shortlisted_ids = [
            row.candidate_id
            for row in interview_rows
            if (row.status or "").strip().lower() == "selected"
        ]

    logger.info(
        "outreach_shortlisted_fetch job_id=%s shortlisted_count=%s",
        job_id, len(shortlisted_ids),
    )
    log_metric("outreach_shortlisted_fetch", job_id=job_id, count=len(shortlisted_ids))

    if not shortlisted_ids:
        return []

    profile_repo = CandidateProfileRepository(db)
    profiles = profile_repo.latest_by_candidate_ids(job_id=job_id, candidate_ids=shortlisted_ids)
    company = CompanyRepository(db).get_by_id(job.company_id)
    recruiter_id = jobs.get_recruiter_id(job.id)
    outreach_status_map = {
        row.candidate_id: (row.status or "").strip().lower()
        for row in OutreachEventRepository(db).list_for_job(job_id)
    }
    selection_session = CandidateSelectionSessionRepository(db).get_by_job(job_id)

    shortlisted_rows: list[dict[str, Any]] = []
    (
        interview_status_map,
        outreach_status_map,
        export_status_map,
        ats_export_status_map,
        _ats_state_map,
        enrichment_state_map,
    ) = _build_candidate_state_maps(db, job_id=job_id)
    updated_workflow_tokens = False
    for candidate_id in shortlisted_ids:
        profile = profiles.get(candidate_id)
        snapshot_candidate = final_snapshot_lookup.get(candidate_id)
        if not profile and not snapshot_candidate:
            logger.warning(
                "invalid_candidate_reference_detected table=interviews job_id=%s candidate_id=%s",
                job_id,
                candidate_id,
            )
            continue
        profile_details = _candidate_profile_details(
            profile=profile,
            raw_data=dict(snapshot_candidate.profileData or {}) if snapshot_candidate else None,
        )
        enrichment_state = dict(getattr(profile, "raw_data", {}) or {}).get("enrichment") or dict(getattr(snapshot_candidate, "profileData", {}) or {}).get("enrichment") or {}
        enrichment_status = str(enrichment_state.get("status") or "pending").strip().lower() or "pending"
        email = profile_details["email"] or (ensure_candidate_email(profile) if profile else "") or str((snapshot_candidate.email if snapshot_candidate else "") or "")
        if not email or email.endswith("@test.local"):
            logger.info("outreach_review_candidate_skipped_missing_email job_id=%s candidate_id=%s", job_id, candidate_id)
            continue
        slot_payload = build_slot_booking_payload(
            candidate=profile or snapshot_candidate or {"id": candidate_id, "name": candidate_id},
            job={"title": job.title, "company_name": company.name if company else ""},
            recruiter_id=recruiter_id,
            db=db,
        )
        slot_payload.update(
            {
                "jobId": job.id,
                "companyId": job.company_id,
                "recruiterId": recruiter_id,
            }
        )
        shortlisted_rows.append(
            {
                "candidate_id": candidate_id,
                "name": _candidate_profile_display_name(profile) or (snapshot_candidate.name if snapshot_candidate else "") or candidate_id,
                "role": (getattr(profile, "role", "") or (snapshot_candidate.role if snapshot_candidate else "") or "").strip(),
                "company": (getattr(profile, "company", "") or (snapshot_candidate.company if snapshot_candidate else "") or "").strip(),
                "email": email,
                "is_mock_email": bool(profile_details["isMockEmail"]) or email.endswith("@test.local"),
                "headline": profile_details["headline"],
                "location": profile_details["location"],
                "years_experience": float(profile_details["yearsExperience"] or 0.0),
                "skills": list(getattr(profile, "skills", []) or (snapshot_candidate.skills if snapshot_candidate else []) or []),
                "summary": getattr(profile, "summary", "") or (snapshot_candidate.summary if snapshot_candidate else ""),
                "education": list(profile_details["education"] or []),
                "projects": list(profile_details["projects"] or []),
                "certifications": list(profile_details["certifications"] or []),
                "companies_history": list(profile_details["companiesHistory"] or []),
                "domain_experience": list(profile_details["domainExperience"] or []),
                "resume_text": profile_details["resumeText"],
                "profile_data": dict(profile_details["profileData"] or {}),
                "fit_score": round(float(getattr(profile, "fit_score", 0.0) or getattr(snapshot_candidate, "fitScore", 0.0) or 0.0), 2),
                "decision": getattr(profile, "decision", "") or (snapshot_candidate.decision if snapshot_candidate else ""),
                "strategy": getattr(profile, "strategy", "") or (snapshot_candidate.strategy if snapshot_candidate else ""),
                "enrichment_status": enrichment_status,
                "enrichment_source": str(enrichment_state_map.get(candidate_id, {}).get("source") or ""),
                "enrichment_confidence": float(enrichment_state_map.get(candidate_id, {}).get("confidence") or 0.0),
                "contact_email": email,
                "contact_phone": str(getattr(profile, "phone", "") or (snapshot_candidate.contactPhone if snapshot_candidate else "") or ""),
                "selection_signal": _selection_session_signal(selection_session, candidate_id),
                "voice_score": 1.0 if getattr(job, "structured_data", None) else 0.0,
                "slot_payload": slot_payload,
            }
        )

    # Release the read transaction before we write workflow tokens.
    db.commit()

    results: list[CandidateResult] = []
    for row in shortlisted_rows:
        upsert_notification_workflow_token(
            db=db,
            job_id=job_id,
            candidate_id=row["candidate_id"],
            workflow_name="slot_booking",
            token=generate_workflow_token(),
            payload=row["slot_payload"],
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            token_type="slot_booking",
            is_active=True,
            source_app="adam",
            force_token=False,
        )
        updated_workflow_tokens = True
        final_score = max(0.0, min(1.0, float(row["fit_score"]) / 5.0))
        results.append(
            CandidateResult(
                id=row["candidate_id"],
                name=row["name"],
                role=row["role"],
                company=row["company"],
                email=row["email"],
                isMockEmail=row["is_mock_email"],
                headline=row["headline"],
                location=row["location"],
                yearsExperience=float(row["years_experience"] or 0.0),
                skills=row["skills"],
                summary=row["summary"],
                education=row["education"],
                projects=row["projects"],
                certifications=row["certifications"],
                companiesHistory=row["companies_history"],
                domainExperience=row["domain_experience"],
                resumeText=row["resume_text"],
                profileData=row["profile_data"],
                fitScore=float(row["fit_score"]),
                decision=row["decision"],
                explanation=CandidateExplanation(
                    semanticScore=0.0,
                    skillOverlap=0.0,
                    finalScore=round(final_score, 4),
                    pdlRelevance=0.0,
                    recencyScore=0.0,
                    engineeringScore=round(final_score, 4),
                    penalties={},
                    sourceBreakdown=_explanation_source_breakdown(
                        vector_score=0.0,
                        lexical_score=0.0,
                        structured_score=0.0,
                        recruiter_score=0.0,
                        recency_score=0.0,
                        session_signal=row["selection_signal"],
                        voice_score=row["voice_score"],
                    ),
                    recruiterPreferenceInfluence=0.0,
                    voiceInterviewInfluence=row["voice_score"],
                    lexicalRetrievalInfluence=0.0,
                    vectorRetrievalInfluence=0.0,
                    freshnessInfluence=0.0,
                    selectionRoundInfluence=row["selection_signal"],
                    aiReasoning="Final shortlist inherits recruiter preference and selection-round feedback.",
                ),
                strategy=row["strategy"],
                status=(profile.ats_status or "shortlisted"),
                enrichmentStatus=row["enrichment_status"],
                enrichmentSource=row["enrichment_source"],
                enrichmentConfidence=row["enrichment_confidence"],
                contactEmail=row["email"],
                contactPhone=str(getattr(profile, "phone", "") or ""),
                outreachStatus=outreach_status_map.get(row["candidate_id"], "pending"),
                exportStatus=export_status_map.get(row["candidate_id"], "pending"),
                ats_export_status=ats_export_status_map.get(row["candidate_id"], "not_sent"),
            )
        )
    if updated_workflow_tokens:
        db.commit()
    sorted_results = sorted(results, key=lambda r: r.fitScore, reverse=True)
    emit_trace(
        logger,
        "shortlist_ready",
        job_id=job_id,
        shortlisted_requested=len(shortlisted_ids),
        shortlisted_returned=len(sorted_results),
        skipped_missing_email=len(shortlisted_ids) - len(results),
    )
    record_shortlist_event(job_id=job_id, shortlisted_count=len(sorted_results))
    return sorted_results


def list_stored_candidates(*, db: Session, job_id: str) -> list[CandidateResult]:
    jobs = JobRepository(db)
    if not jobs.get(job_id):
        raise APIError("Job not found", status_code=404)

    profiles = CandidateProfileRepository(db).list_for_job(job_id)
    if not profiles:
        return []

    results: list[CandidateResult] = []
    for row in profiles:
        final_score = max(0.0, min(1.0, row.fit_score / 5.0))
        profile_details = _candidate_profile_details(profile=row)
        enrichment_state = dict(getattr(row, "raw_data", {}) or {}).get("enrichment") or {}
        enrichment_status = str(enrichment_state.get("status") or "pending").strip().lower() or "pending"
        results.append(
            CandidateResult(
                id=row.candidate_id,
                name=_candidate_profile_display_name(row),
                role=row.role,
                company=row.company,
                email=profile_details["email"] or ensure_candidate_email(row),
                isMockEmail=bool(profile_details["isMockEmail"]) or ensure_candidate_email(row).endswith("@test.local"),
                headline=profile_details["headline"],
                location=profile_details["location"],
                yearsExperience=float(profile_details["yearsExperience"] or 0.0),
                skills=row.skills or [],
                summary=row.summary,
                education=list(profile_details["education"] or []),
                projects=list(profile_details["projects"] or []),
                certifications=list(profile_details["certifications"] or []),
                companiesHistory=list(profile_details["companiesHistory"] or []),
                domainExperience=list(profile_details["domainExperience"] or []),
                resumeText=profile_details["resumeText"],
                profileData=dict(profile_details["profileData"] or {}),
                fitScore=round(row.fit_score, 2),
                decision=row.decision,
                enrichmentStatus=enrichment_status,
                enrichmentSource=str(enrichment_state.get("source") or ""),
                enrichmentConfidence=float(enrichment_state.get("confidence") or 0.0),
                contactEmail=profile_details["email"] or ensure_candidate_email(row),
                contactPhone=str(getattr(row, "phone", "") or ""),
                explanation=CandidateExplanation(
                    semanticScore=0.0,
                    skillOverlap=0.0,
                    finalScore=round(final_score, 4),
                    pdlRelevance=0.0,
                    recencyScore=0.0,
                    engineeringScore=round(final_score, 4),
                    penalties={},
                ),
                strategy=row.strategy,
                status="new",
            )
        )
    return _attach_candidate_workflow_state(db, job_id=job_id, candidates=results)


def refresh_candidates_for_job(*, db: Session, job_id: str, mode: str | None = None, refresh: bool = False, request_source: str = "api") -> int:
    refreshed = fetch_ranked_candidates(db=db, job_id=job_id, mode=mode, refresh=refresh, request_source=request_source)
    return len(refreshed)


def diversify_candidates(scored_rows: list[tuple[CandidateResult, float]], limit: int = RESULT_LIMIT) -> list[tuple[CandidateResult, float]]:
    selected: list[tuple[CandidateResult, float]] = []
    seen_signatures: set[tuple[str, tuple[str, ...]]] = set()

    for candidate, score in scored_rows:
        if len(selected) >= limit:
            break
        skill_signature = tuple(sorted(str(skill).strip().lower() for skill in (candidate.skills or [])[:3] if str(skill).strip()))
        signature = ((candidate.role or "").strip().lower(), skill_signature)
        if signature in seen_signatures:
            continue
        selected.append((candidate, score))
        seen_signatures.add(signature)

    if len(selected) >= limit:
        return selected[:limit]

    for candidate, score in scored_rows:
        if len(selected) >= limit:
            break
        if any(existing.id == candidate.id for existing, _ in selected):
            continue
        selected.append((candidate, score))

    return selected[:limit]
