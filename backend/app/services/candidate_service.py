from __future__ import annotations

import logging
import math
import random
import re
from collections import defaultdict
from statistics import mean, pstdev
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import (
    ENABLE_HARD_FILTERING,
    ENABLE_FAKE_EMAILS,
    FEEDBACK_WEIGHTS,
    GROQ_API_KEY,
    EMBEDDING_VERSION,
    MIN_SKILL_MATCH_THRESHOLD,
    PDL_SEARCH_SIZE,
    RLHF_FEEDBACK_HALF_LIFE_DAYS,
    RANKING_WEIGHTS,
    SCORING_DEFAULT_MODE,
)
from app.db.repositories import (
    ATSExportRepository,
    CandidateFeedbackRepository,
    CandidateProfileRepository,
    CandidateSelectionSessionRepository,
    InterviewRepository,
    JobRepository,
    OutreachEventRepository,
    RankingExplanationRepository,
    RankingRunRepository,
    ScoringProfileRepository,
)
from app.schemas.candidate import CandidateExplanation, CandidateRankingDebug, CandidateResult
from app.services.candidate_text import build_candidate_text
from app.services.ats.service import export_candidate_to_ats
from app.services.embedding_service import embed, preload_sample_candidate_embeddings
from app.services.evaluation_service import record_candidate_fetch, record_shortlist_event
from app.services.llm_service import generate
from app.services.metrics_service import log_metric
from app.services.pdl_service import fetch_candidates_with_filters, is_pdl_disabled
from app.services.recruiter_preference_service import (
    compute_recruiter_score_details,
    map_experience_to_bucket,
    load_recruiter_preference_profile,
    update_recruiter_preferences,
)
from app.services.skill_normalizer import normalize_skills, parse_experience
from app.services.qdrant_service import (
    delete_candidate_vectors,
    ensure_all_collections,
    is_qdrant_search_error_active,
    last_qdrant_search_error,
    search_candidate_chunks,
    upsert_candidate_chunks,
)
from app.services.slack_service import notify_slack
from app.services.state_machine import assert_valid_transition, is_swipe_locked, swipe_to_status
from app.utils.exceptions import APIError
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
    structured_skills = [str(skill).strip().lower() for skill in (getattr(job, "skills_required", None) or []) if str(skill).strip()]
    fallback_skills = [skill.strip().lower() for skill in job.description.replace("\n", " ").split() if len(skill) > 3][:5]
    learned_skills = [token for token in (preferred_tokens or []) if token and token not in structured_skills][:3]
    return {
        "role": job.title,
        "location": job.location,
        "skills": (structured_skills[:8] + learned_skills)[:10] or fallback_skills,
        "learned_query_tokens": list(preferred_tokens or []),
        "preferred_roles": [role for role in (preferred_roles or []) if role][:3],
    }


def _candidate_text(candidate: dict) -> str:
    name = str(candidate.get("full_name") or candidate.get("name") or "").strip()
    role = str(candidate.get("job_title") or candidate.get("title") or "").strip()
    company = str(candidate.get("job_company_name") or candidate.get("company") or "").strip()
    skills = ", ".join(str(s) for s in (candidate.get("skills") or []))
    experience = _candidate_experience(candidate)
    summary = str(candidate.get("summary") or candidate.get("bio") or candidate.get("experience_summary") or "").strip()
    return (
        f"Name: {name}\n"
        f"Role: {role}\n"
        f"Company: {company}\n"
        f"Skills: {skills}\n"
        f"Experience: {experience}\n"
        f"Summary: {summary}"
    )


def _candidate_embedding_text(*, role: str, skills: list[str], experience: str, summary: str) -> str:
    return build_candidate_text(
        {
            "role": role,
            "skills": skills,
            "experience": experience,
            "summary": summary,
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


def _normalize_identity_value(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(part for part in value.strip().lower().split() if part)


def _extract_candidate_email(candidate: dict) -> str:
    direct_keys = ("work_email", "personal_email", "email", "emails_primary")
    for key in direct_keys:
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()

    for key in ("emails", "personal_emails", "work_emails"):
        value = candidate.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    return item.strip().lower()
                if isinstance(item, dict):
                    address = str(item.get("address") or item.get("email") or "").strip()
                    if address:
                        return address.lower()
    return ""


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


def _extract_candidate_external_id(candidate: dict) -> str:
    for key in ("id", "external_id", "profile_id", "linkedin_id", "linkedin_url"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _candidate_identity_key(candidate: dict) -> str:
    email = _extract_candidate_email(candidate)
    if email:
        return f"email:{email}"

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
    experience = _candidate_experience(candidate)
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


def _job_experience(job) -> str:
    structured = getattr(job, "structured_data", None)
    structured_experience = ""
    if isinstance(structured, dict):
        structured_experience = str(structured.get("experience") or structured.get("experience_level") or "").strip()
    return str(getattr(job, "experience_level", "") or structured_experience or "").strip()


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
            experience_text = _candidate_experience(raw_data)
        if not experience_text:
            experience_text = str(getattr(fallback_profile, "summary", "") or "").strip()
    if not experience_text:
        experience_text = _candidate_experience(candidate)
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
        or getattr(job, "experience_level", "")
    )
    original_jd = _normalize_text(getattr(job, "description", ""))
    if not original_jd:
        original_jd = _normalize_text(resolved_structured_data.get("description") or "")

    role_line = role or _normalize_text(getattr(job, "title", ""))
    skill_line = ", ".join(skills)
    job_text = (
        f"Role: {role_line}\n"
        f"Experience: {experience}\n"
        f"Skills: {skill_line}\n\n"
        f"Job Description:\n{original_jd}\n\n"
        f"Voice Input:\n{transcript_text}"
    ).strip()
    if not job_text:
        job_text = original_jd or transcript_text or " "

    source = "structured_data" if role or skills or experience else "transcript" if transcript_text else "description"
    logger.info(
        "job_text_built job_id=%s source=%s has_structured_data=%s transcript_present=%s length=%s",
        getattr(job, "id", "unknown"),
        source,
        bool(role or skills or experience),
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
                    "similarity": 0.6,
                    "skill_overlap": 0.25,
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
                "similarity": 0.8,
                "skill_overlap": 0.15,
                "experience": 0.05,
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
    if normalized in {"contacted", "shortlisted"}:
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


def _recruiter_feedback_count(db: Session, recruiter_id: str | None) -> int:
    recruiter_id = (recruiter_id or "").strip()
    if not recruiter_id:
        return 0
    return CandidateFeedbackRepository(db).count_for_recruiter(recruiter_id)


def _dynamic_ranking_weights(*, recruiter_feedback_count: int) -> tuple[float, float, float, float]:
    session_weight = 0.1
    raw_recruiter_weight = min(0.3, 0.05 * max(0, recruiter_feedback_count))
    recruiter_signal_strength = min(1.0, max(0, recruiter_feedback_count) / 5.0)
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
            final_score = float(getattr(candidate.explanation, "finalScore", 0.0) or 0.0)
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
    if not GROQ_API_KEY:
        heuristic = (
            "Strong semantic and skill alignment." if candidate.explanation.semanticScore >= 0.7 else "Moderate alignment."
        )
        return heuristic, 0.03 if candidate.explanation.semanticScore >= 0.7 else 0.0

    try:
        prompt = (
            "Rate this candidate for the job on a 0-100 scale and explain in one short sentence. "
            "Return exactly: SCORE=<number>; REASON=<text>.\n\n"
            f"JOB TITLE: {job.title}\n"
            f"JOB DESCRIPTION: {job.description}\n"
            f"CANDIDATE ROLE: {candidate.role}\n"
            f"CANDIDATE SUMMARY: {candidate.summary}\n"
            f"CANDIDATE SKILLS: {', '.join(candidate.skills)}"
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


def compute_match_score(
    *,
    similarity: float,
    skill_overlap: float,
    experience_match: float,
    weights: RankingWeights | None = None,
) -> float:
    resolved = weights or _normalize_weight_triplet({})
    return max(
        0.0,
        min(
            1.0,
            (resolved.similarity * similarity)
            + (resolved.skill_overlap * skill_overlap)
            + (resolved.experience * experience_match),
        ),
    )


def build_match_explanation(*, candidate, job_context, semantic_similarity: float) -> dict[str, Any]:
    job_experience = _job_experience(job_context)
    candidate_experience = _candidate_experience(candidate) if isinstance(candidate, dict) else str(
        getattr(candidate, "experience", "") or getattr(candidate, "summary", "") or ""
    ).strip()
    candidate_skills = _candidate_skills(candidate) if isinstance(candidate, dict) else list(getattr(candidate, "skills", []) or [])
    job_skills = _job_requirement_skills(job_context)
    matched_skills = _matched_skills(job_skills, candidate_skills)
    experience_match_value = _experience_match(candidate_experience, job_experience)

    return {
        "skills_matched": matched_skills,
        "experience_match": _experience_match_summary(candidate_experience, job_experience),
        "similarity_score": round(max(0.0, min(1.0, semantic_similarity)), 4),
        "candidate_experience": candidate_experience,
        "job_experience": job_experience,
        "experience_match_value": round(experience_match_value, 4),
    }


def compute_final_score(
    *,
    semantic_similarity: float,
    skill_overlap: float,
    experience_match: float,
    ranking_weights: RankingWeights,
    pdl_component: float,
    feedback_bias: float,
    diversity_bonus: float,
    exploration_bonus: float,
    rejection_penalty: float,
    semantic_penalty: float,
    missing_skills_penalty: float,
) -> float:
    base_match = compute_match_score(
        similarity=semantic_similarity,
        skill_overlap=skill_overlap,
        experience_match=experience_match,
        weights=ranking_weights,
    )
    raw = base_match + pdl_component + feedback_bias + diversity_bonus + exploration_bonus - rejection_penalty
    penalized = raw * semantic_penalty * missing_skills_penalty
    return max(0.0, min(1.0, penalized))


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
    exploration: ExplorationContext,
    debug: bool = False,
    run_metrics_by_candidate_id: dict[str, dict[str, float | bool]] | None = None,
) -> list[CandidateResult]:
    ensure_all_collections()
    recruiter_id = JobRepository(db).get_recruiter_id(job.id)
    job_vec = _job_vector(job, feedback_learning)
    hits = search_candidate_chunks(
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
        candidate_experience = _candidate_experience(profile.raw_data if profile and isinstance(profile.raw_data, dict) else payload)
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
                "candidate_experience_years": _candidate_experience_years(payload, fallback_profile=profile),
                "profile": profile,
                "feedback_direct": feedback_direct,
            }
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
        log_metric(
            "candidate_penalty",
            job_id=job.id,
            candidate_id=candidate_id,
            penalty=round(rejection_penalty, 4),
        )
        final = compute_final_score(
            semantic_similarity=(0.70 * semantic) + (0.30 * historical),
            skill_overlap=skill_overlap,
            experience_match=experience_match,
            ranking_weights=ranking_weights,
            pdl_component=0.0,
            feedback_bias=feedback_bias,
            diversity_bonus=diversity_bonus,
            exploration_bonus=exploration_bonus,
            rejection_penalty=rejection_penalty,
            semantic_penalty=1.0,
            missing_skills_penalty=1.0,
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
            email=candidate_email,
            isMockEmail=candidate_email.endswith("@test.local"),
            skills=skills or [],
            summary=summary,
            fitScore=fit_score,
            decision=decision,
            explanation=CandidateExplanation(
                semanticScore=round((0.70 * semantic) + (0.30 * historical), 4),
                skillOverlap=round(skill_overlap, 4),
                finalScore=round(final, 4),
                pdlRelevance=0.0,
                recencyScore=0.0,
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
            candidate.explanation.aiReasoning = reason
            candidate.explanation.finalScore = round(max(0.0, min(1.0, candidate.explanation.finalScore + bonus)), 4)
            candidate.fitScore = round(candidate.explanation.finalScore * 5, 2)
            candidate.decision = _decision_from_score(candidate.explanation.finalScore)
            enriched.append((candidate, candidate.explanation.finalScore))
        local_results = [candidate for candidate, _ in sorted(enriched, key=lambda row: row[1], reverse=True)]

    ranked_local = sorted([(candidate, candidate.explanation.finalScore) for candidate in local_results], key=lambda row: row[1], reverse=True)
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
    run_metrics_by_candidate_id: dict[str, dict[str, float | bool]] | None = None,
) -> list[CandidateResult]:
    filters = _normalize_job_filters(
        job,
        preferred_tokens=feedback_learning.preferred_tokens,
        preferred_roles=feedback_learning.preferred_roles,
    )
    response = fetch_candidates_with_filters(filters=filters, size=size)
    candidates = response.get("data", []) if isinstance(response, dict) else []
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
        logger.info("pdl_top_k_applied count=%s job_id=%s", len(candidates), job.id)

    if not candidates:
        logger.warning(
            "PDL fetch failed — preserving existing vectors job_id=%s", job.id
        )
        return []

    job_vec = _job_vector(job, feedback_learning)
    job_skills = _job_requirement_skills(job)
    min_experience_years = _job_min_experience_years(job)
    ensure_all_collections()
    delete_candidate_vectors(job.id)

    weights = _load_scoring_weights(db, job_id=job.id)
    ranking_weights = _resolve_ranking_weights(job, default_weights=mode_config.ranking_weights)
    profile_repo = CandidateProfileRepository(db)
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
        "hard_filter_applied job_id=%s source=pdl enabled=%s total=%s kept=%s filtered_out=%s min_experience=%s min_skill_matches=%s",
        job.id,
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
        source="pdl",
        enabled=mode_config.use_hard_filtering,
        total=len(candidates),
        kept=len(filtered_candidates),
        filtered_out=filtered_out,
        min_experience=min_experience_years,
        min_skill_matches=mode_config.min_skill_match_threshold,
    )
    logger.info(
        "candidates_filtered_count job_id=%s source=pdl total=%s filtered_out=%s",
        job.id,
        len(candidates),
        filtered_out,
    )
    log_metric(
        "candidates_filtered_count",
        job_id=job.id,
        source="pdl",
        total=len(candidates),
        filtered_out=filtered_out,
    )
    if mode_config.use_hard_filtering and candidates and not filtered_candidates:
        logger.info(
            "fallback_to_unfiltered job_id=%s source=pdl reason=no_candidates_after_hard_filter",
            job.id,
        )
        log_metric(
            "fallback_to_unfiltered",
            job_id=job.id,
            source="pdl",
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
        candidate_experience = _candidate_experience(item)
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

        candidate_embed_text = _candidate_embedding_text(
            role=candidate_role,
            skills=candidate_skills,
            experience=candidate_experience,
            summary=candidate_summary,
        )
        candidate_chunks = chunk_text(candidate_embed_text)
        candidate_vectors = [_embed_text(chunk) for chunk in candidate_chunks]
        candidate_vec = average_vectors(candidate_vectors)

        cosine_score = cosine_similarity(job_vec, candidate_vec)
        semantic_similarity = _normalize_similarity(cosine_score)
        pdl_relevance = _pdl_relevance(item, index=index, total=total_candidates)
        skill_overlap = _skill_overlap(job_skills, candidate_skills)
        experience_match = _experience_match(candidate_experience, job_experience)
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
            pdl_component=pdl_component,
            feedback_bias=feedback_bias,
            diversity_bonus=diversity_bonus,
            exploration_bonus=exploration_bonus,
            rejection_penalty=rejection_penalty,
            semantic_penalty=semantic_penalty,
            missing_skills_penalty=missing_skills_penalty,
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

        result = CandidateResult(
            id=candidate_id,
            name=candidate_name,
            role=candidate_role,
            company=candidate_company,
            email=candidate_email,
            isMockEmail=candidate_email.endswith("@test.local"),
            skills=candidate_skills,
            summary=candidate_summary,
            fitScore=fit_score,
            decision=decision,
            explanation=CandidateExplanation(
                semanticScore=round(semantic_similarity, 4),
                skillOverlap=round(skill_overlap, 4),
                finalScore=round(final_score, 4),
                pdlRelevance=round(pdl_relevance, 4),
                recencyScore=round(recency_score, 4),
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
            candidate.explanation.aiReasoning = reason
            candidate.explanation.finalScore = round(max(0.0, min(1.0, candidate.explanation.finalScore + bonus)), 4)
            candidate.fitScore = round(candidate.explanation.finalScore * 5, 2)
            candidate.decision = _decision_from_score(candidate.explanation.finalScore)
            enriched.append((candidate, candidate.explanation.finalScore))
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
) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    interview_status_map: dict[str, str] = {}
    outreach_status_map: dict[str, str] = {}
    export_status_map: dict[str, str] = {}
    ats_export_status_map: dict[str, str] = {}

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

    return interview_status_map, outreach_status_map, export_status_map, ats_export_status_map


def _attach_candidate_workflow_state(db: Session, *, job_id: str, candidates: list[CandidateResult]) -> list[CandidateResult]:
    if not candidates:
        return candidates

    interview_status_map, outreach_status_map, export_status_map, ats_export_status_map = _build_candidate_state_maps(db, job_id=job_id)
    for candidate in candidates:
        export_status = export_status_map.get(candidate.id, "pending")
        ats_export_status = ats_export_status_map.get(candidate.id, "not_sent")
        outreach_status = outreach_status_map.get(candidate.id, "pending")
        status = interview_status_map.get(candidate.id, candidate.status or "new")

        if export_status == "exported":
            status = "exported"
        elif outreach_status in {"sent", "dry_run", "simulated"}:
            status = "contacted"

        candidate.status = status
        candidate.outreachStatus = outreach_status
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


def fetch_ranked_candidates(*, db: Session, job_id: str, mode: str | None = None, refresh: bool = False, debug: bool = False) -> list[CandidateResult]:
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
    log_metric("retrieval_request", job_id=job.id, mode=resolved_mode, refresh=refresh)
    logger.info("dynamic_switch_applied job_id=%s mode=%s strategy=%s", job.id, mode_config.mode, mode_config.strategy)
    log_metric("dynamic_switch_applied", job_id=job.id, mode=mode_config.mode, strategy=mode_config.strategy)
    # Load swiped IDs once — used to exclude already-decided candidates server-side.
    swiped_ids = _get_swiped_candidate_ids(db, job_id=job.id)
    feedback_learning = _build_feedback_learning_context(db, job_id=job.id)
    recruiter_id = jobs.get_recruiter_id(job.id)
    recruiter_preferences = load_recruiter_preference_profile(db, recruiter_id) if recruiter_id else {}
    recruiter_feedback_count = _recruiter_feedback_count(db, recruiter_id)
    selection_session = CandidateSelectionSessionRepository(db).get_by_job(job.id)
    run_type = _infer_ranking_run_type(refresh=refresh, selection_session=selection_session)
    local_run_metrics: dict[str, dict[str, float | bool]] = {}
    pdl_run_metrics: dict[str, dict[str, float | bool]] = {}
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
    if pdl_disabled:
        logger.warning("pdl_disabled job_id=%s reason=service_disabled", job_id)
        log_metric("pdl_disabled", job_id=job.id, mode=resolved_mode, reason="service_disabled")
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
        if not final_local:
            stored_candidates = _fallback_stored_candidates(
                db=db,
                job_id=job.id,
                swiped_ids=swiped_ids,
                source="local",
                reason="no_candidates_after_filter",
            )
            if stored_candidates:
                record_candidate_fetch(job_id=job.id, candidates=stored_candidates)
                _record_ranking_run(
                    db=db,
                    job_id=job.id,
                    recruiter_id=recruiter_id,
                    run_type=run_type,
                    metrics=_ranking_run_metrics_for_candidates(stored_candidates, local_run_metrics),
                )
                logger.info("candidates_returned count=%s", len(stored_candidates))
                return stored_candidates
            jobs.update_candidate_sourcing_state(
                job_id=job.id,
                job_status="no_candidates",
                last_candidate_attempt_at=datetime.now(timezone.utc),
            )
            logger.info(
                "no_candidates_detected job_id=%s local_count=%s pdl_count=0",
                job.id,
                len(local_results),
            )
            log_metric(
                "no_candidates_detected",
                job_id=job.id,
                local_count=len(local_results),
                pdl_count=0,
                )
            _safe_commit(db, context="candidate_fetch_no_candidates_local", job_id=job.id)
            _record_ranking_run(
                db=db,
                job_id=job.id,
                recruiter_id=recruiter_id,
                run_type=run_type,
                metrics=[],
            )
            return []
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
    if not pdl_allowed:
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
        if not final_suppressed:
            stored_candidates = _fallback_stored_candidates(
                db=db,
                job_id=job.id,
                swiped_ids=swiped_ids,
                source="local_fallback",
                reason="no_candidates_after_filter",
            )
            if stored_candidates:
                record_candidate_fetch(job_id=job.id, candidates=stored_candidates)
                _record_ranking_run(
                    db=db,
                    job_id=job.id,
                    recruiter_id=recruiter_id,
                    run_type=run_type,
                    metrics=_ranking_run_metrics_for_candidates(stored_candidates, local_run_metrics),
                )
                logger.info("candidates_returned count=%s", len(stored_candidates))
                return stored_candidates
            jobs.update_candidate_sourcing_state(
                job_id=job.id,
                job_status="no_candidates",
                last_candidate_attempt_at=now,
            )
            logger.info(
                "no_candidates_detected job_id=%s local_count=%s pdl_count=0",
                job.id,
                len(local_results),
            )
            log_metric(
                "no_candidates_detected",
                job_id=job.id,
                local_count=len(local_results),
                pdl_count=0,
            )
            _safe_commit(db, context="candidate_fetch_no_candidates_suppressed", job_id=job.id)
            _record_ranking_run(
                db=db,
                job_id=job.id,
                recruiter_id=recruiter_id,
                run_type=run_type,
                metrics=[],
            )
            return []
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
    try:
        pdl_results = _build_ranked_candidates_from_pdl(
            db=db,
            job=job,
            mode=resolved_mode,
            size=size,
            mode_config=mode_config,
            feedback_learning=feedback_learning,
            exploration=exploration,
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
        stored_candidates = _fallback_stored_candidates(
            db=db,
            job_id=job.id,
            swiped_ids=swiped_ids,
            source=resolved_mode,
            reason="pdl_empty_or_filtered",
        )
        if stored_candidates:
            jobs.update_candidate_sourcing_state(
                job_id=job.id,
                job_status="active",
                last_candidate_attempt_at=now,
            )
            _safe_commit(db, context="candidate_fetch_pdl_fallback_active", job_id=job.id)
            record_candidate_fetch(job_id=job.id, candidates=stored_candidates)
            _record_ranking_run(
                db=db,
                job_id=job.id,
                recruiter_id=recruiter_id,
                run_type=run_type,
                metrics=_ranking_run_metrics_for_candidates(stored_candidates, combined_run_metrics),
            )
            logger.info("candidates_returned count=%s", len(stored_candidates))
            return stored_candidates
        jobs.update_candidate_sourcing_state(
            job_id=job.id,
            job_status="no_candidates",
            last_candidate_attempt_at=now,
        )
        logger.info(
            "no_candidates_detected job_id=%s local_count=%s pdl_count=%s",
            job.id,
            local_count,
            len(pdl_results) if pdl_results else 0,
        )
        log_metric(
            "no_candidates_detected",
            job_id=job.id,
            local_count=local_count,
            pdl_count=len(pdl_results) if pdl_results else 0,
        )
        _safe_commit(db, context="candidate_fetch_pdl_no_candidates", job_id=job.id)
        _record_ranking_run(
            db=db,
            job_id=job.id,
            recruiter_id=recruiter_id,
            run_type=run_type,
            metrics=[],
        )
        return []

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


def warm_candidate_retrieval() -> int:
    ensure_all_collections()
    preloaded = preload_sample_candidate_embeddings()
    logger.info("candidate_retrieval_warmup embeddings_preloaded=%s", preloaded)
    return preloaded


def apply_feedback(*, db: Session, job_id: str, candidate_id: str, action: str) -> dict:
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
    # is_swipe_locked covers shortlisted/contacted/interview_scheduled/exported.
    # assert_valid_transition covers the full transition table.
    if is_swipe_locked(current_status):
        logger.warning(
            "swipe_blocked job_id=%s candidate_id=%s current_status=%s action=%s",
            job_id, candidate_id, current_status, action,
        )
        raise APIError(
            f"Cannot swipe candidate in '{current_status}' state. "
            "Only 'new' candidates can be accepted or rejected.",
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
    if is_new_feedback and recruiter_id:
        update_recruiter_preferences(
            db,
            recruiter_id,
            profile if action == "accept" else None,
            [] if action == "accept" else [profile],
            signal_multiplier=2.0 if action == "accept" else 0.5,
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
    if target_status == "shortlisted" and bool(getattr(job, "auto_export_to_ats", False)):
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
        "message": "Feedback recorded and ranking weights updated",
    }


def list_shortlisted_candidates(*, db: Session, job_id: str) -> list[CandidateResult]:
    """Return only shortlisted candidates for a job — used by the outreach page."""
    jobs = JobRepository(db)
    if not jobs.get(job_id):
        raise APIError("Job not found", status_code=404)

    interview_rows = InterviewRepository(db).list_for_job(job_id)
    shortlisted_ids = [
        row.candidate_id
        for row in interview_rows
        if (row.status or "").strip().lower() == "shortlisted"
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
    outreach_status_map = {
        row.candidate_id: (row.status or "").strip().lower()
        for row in OutreachEventRepository(db).list_for_job(job_id)
    }

    results: list[CandidateResult] = []
    interview_status_map, outreach_status_map, export_status_map, ats_export_status_map = _build_candidate_state_maps(db, job_id=job_id)
    for candidate_id in shortlisted_ids:
        profile = profiles.get(candidate_id)
        if not profile:
            logger.warning(
                "invalid_candidate_reference_detected table=interviews job_id=%s candidate_id=%s",
                job_id,
                candidate_id,
            )
            continue
        final_score = max(0.0, min(1.0, profile.fit_score / 5.0))
        results.append(
            CandidateResult(
                id=profile.candidate_id,
                name=profile.name,
                role=profile.role,
                company=profile.company,
                email=ensure_candidate_email(profile),
                isMockEmail=ensure_candidate_email(profile).endswith("@test.local"),
                skills=profile.skills or [],
                summary=profile.summary,
                fitScore=round(profile.fit_score, 2),
                decision=profile.decision,
                explanation=CandidateExplanation(
                    semanticScore=0.0,
                    skillOverlap=0.0,
                    finalScore=round(final_score, 4),
                    pdlRelevance=0.0,
                    recencyScore=0.0,
                    penalties={},
                ),
                strategy=profile.strategy,
                status="shortlisted",
                outreachStatus=outreach_status_map.get(candidate_id, "pending"),
                exportStatus=export_status_map.get(candidate_id, "pending"),
                ats_export_status=ats_export_status_map.get(candidate_id, "not_sent"),
            )
        )
    sorted_results = sorted(results, key=lambda r: r.fitScore, reverse=True)
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
        results.append(
            CandidateResult(
                id=row.candidate_id,
                name=row.name,
                role=row.role,
                company=row.company,
                email=ensure_candidate_email(row),
                isMockEmail=ensure_candidate_email(row).endswith("@test.local"),
                skills=row.skills or [],
                summary=row.summary,
                fitScore=round(row.fit_score, 2),
                decision=row.decision,
                explanation=CandidateExplanation(
                    semanticScore=0.0,
                    skillOverlap=0.0,
                    finalScore=round(final_score, 4),
                    pdlRelevance=0.0,
                    recencyScore=0.0,
                    penalties={},
                ),
                strategy=row.strategy,
                status="new",
            )
        )
    return _attach_candidate_workflow_state(db, job_id=job_id, candidates=results)


def refresh_candidates_for_job(*, db: Session, job_id: str, mode: str | None = None, refresh: bool = False) -> int:
    refreshed = fetch_ranked_candidates(db=db, job_id=job_id, mode=mode, refresh=refresh)
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
