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

from openai import OpenAI
from sqlalchemy.orm import Session

from app.core.config import (
    OPENAI_API_KEY,
    OPENAI_MODEL,
    PDL_SEARCH_SIZE,
    RLHF_FEEDBACK_HALF_LIFE_DAYS,
    SCORING_DEFAULT_MODE,
)
from app.db.repositories import (
    ATSExportRepository,
    CandidateFeedbackRepository,
    CandidateProfileRepository,
    InterviewRepository,
    JobRepository,
    OutreachEventRepository,
    ScoringProfileRepository,
)
from app.schemas.candidate import CandidateExplanation, CandidateResult
from app.services.embedding_service import get_embedding, preload_sample_candidate_embeddings
from app.services.metrics_service import log_metric
from app.services.pdl_service import fetch_candidates_with_filters
from app.services.qdrant_service import (
    delete_candidate_vectors,
    ensure_all_collections,
    is_qdrant_search_error_active,
    last_qdrant_search_error,
    search_candidate_chunks,
    upsert_candidate_chunks,
)
from app.services.slack_service import notify_slack
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
    summary = str(candidate.get("summary") or candidate.get("bio") or candidate.get("experience_summary") or "").strip()
    return (
        f"Name: {name}\n"
        f"Role: {role}\n"
        f"Company: {company}\n"
        f"Skills: {skills}\n"
        f"Summary: {summary}"
    )


def _candidate_embedding_text(*, name: str, role: str, company: str, skills: list[str], summary: str) -> str:
    return (
        f"Name: {name}\n"
        f"Role: {role}\n"
        f"Company: {company}\n"
        f"Skills: {', '.join(skills)}\n"
        f"Summary: {summary}"
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
    if company and skills:
        return f"Currently at {company}. Skills: {', '.join(skills[:6])}"
    if skills:
        return f"Skills: {', '.join(skills[:6])}"
    return "Candidate profile sourced from People Data Labs."


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
    return _normalized_skill_tokens([job.title, job.description])


def _skill_overlap(job_skills: set[str], candidate_skills: list[str]) -> float:
    if not job_skills:
        return 0.0
    candidate_skill_tokens = _normalized_skill_tokens(candidate_skills)
    if not candidate_skill_tokens:
        return 0.0

    exact = len(job_skills.intersection(candidate_skill_tokens))
    exact_ratio = exact / len(job_skills)

    unmatched_job = [token for token in job_skills if token not in candidate_skill_tokens]
    unmatched_candidate = [token for token in candidate_skill_tokens if token not in job_skills]
    soft_hits = 0
    for job_token in unmatched_job:
        if any(
            candidate_token.startswith(job_token[:3]) or job_token.startswith(candidate_token[:3])
            for candidate_token in unmatched_candidate
            if len(job_token) >= 3 and len(candidate_token) >= 3
        ):
            soft_hits += 1
    soft_ratio = soft_hits / len(job_skills)

    return max(0.0, min(1.0, (0.75 * exact_ratio) + (0.25 * soft_ratio)))


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
    return list(get_embedding(safe))


def _job_vector(job, feedback_learning: FeedbackLearningContext | None = None) -> list[float]:
    skills_text = ", ".join(str(skill).strip() for skill in (getattr(job, "skills_required", None) or []) if str(skill).strip())
    responsibilities_text = "\n".join(
        f"- {item.strip()}" for item in (getattr(job, "responsibilities", None) or []) if str(item).strip()
    )
    experience_level = str(getattr(job, "experience_level", "") or "").strip()
    text = (
        f"Title: {job.title}\n"
        f"Skills Required: {skills_text}\n"
        f"Responsibilities:\n{responsibilities_text}\n"
        f"Description: {job.description}\n"
        f"Experience Level: {experience_level}\n"
        f"Location: {job.location}\n"
        f"Compensation: {job.compensation}\n"
        f"Work Authorization: {job.work_authorization}"
    )
    if feedback_learning:
        learned_skills = ", ".join(feedback_learning.preferred_tokens[:6])
        learned_roles = ", ".join(feedback_learning.preferred_roles[:3])
        if learned_skills:
            text += f"\nHistorically Successful Skills: {learned_skills}"
        if learned_roles:
            text += f"\nHistorically Successful Roles: {learned_roles}"
    chunks = chunk_text(text)
    vectors = [_embed_text(chunk) for chunk in chunks]
    return average_vectors(vectors)


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
    direction = 1.0 if action == "accept" else -1.0
    return direction * _feedback_outcome_multiplier(status)


def _score_feedback_skills(skills: list[str], bias_map: dict[str, float]) -> float:
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

    rows = feedback_repo.list_all()
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
    if not OPENAI_API_KEY:
        heuristic = (
            "Strong semantic and skill alignment." if candidate.explanation.semanticScore >= 0.7 else "Moderate alignment."
        )
        return heuristic, 0.03 if candidate.explanation.semanticScore >= 0.7 else 0.0

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        prompt = (
            "Rate this candidate for the job on a 0-100 scale and explain in one short sentence. "
            "Return exactly: SCORE=<number>; REASON=<text>.\n\n"
            f"JOB TITLE: {job.title}\n"
            f"JOB DESCRIPTION: {job.description}\n"
            f"CANDIDATE ROLE: {candidate.role}\n"
            f"CANDIDATE SUMMARY: {candidate.summary}\n"
            f"CANDIDATE SKILLS: {', '.join(candidate.skills)}"
        )
        response = client.responses.create(model=OPENAI_MODEL, input=prompt, temperature=0)
        text = (response.output_text or "").strip()

        score_match = re.search(r"SCORE\s*=\s*(\d{1,3})", text, re.IGNORECASE)
        reason_match = re.search(r"REASON\s*=\s*(.+)", text, re.IGNORECASE)
        score = float(score_match.group(1)) if score_match else 50.0
        score = max(0.0, min(100.0, score))
        reason = (reason_match.group(1).strip() if reason_match else text)[:240]
        bonus = (score / 100.0) * 0.10
        return reason or "Elite review completed.", bonus
    except Exception as exc:
        logger.warning("Elite reasoning failed; falling back to heuristic", exc_info=exc)
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
    company_name = ""
    company = getattr(job, "company", None)
    if company and getattr(company, "name", None):
        company_name = str(company.name)
    return {
        "role": _normalize_identity_value(job.title),
        "company": _normalize_identity_value(company_name),
        "location": _normalize_identity_value(job.location),
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


def _compute_final_score(
    *,
    semantic_component: float,
    pdl_component: float,
    feedback_bias: float,
    diversity_bonus: float,
    exploration_bonus: float,
    rejection_penalty: float,
    semantic_penalty: float,
    missing_skills_penalty: float,
) -> float:
    # final_score = semantic + pdl + feedback_bias + diversity_bonus + exploration_bonus
    raw = semantic_component + pdl_component + feedback_bias + diversity_bonus + exploration_bonus - rejection_penalty
    penalized = raw * semantic_penalty * missing_skills_penalty
    return max(0.0, min(1.0, penalized))


def _decide_switching_mode(
    *,
    refresh: bool,
    local_count: int,
    similarity_score: float,
    feedback_success_rate: float,
    candidate_diversity: float,
) -> tuple[str, str]:
    if refresh:
        if local_count > 0:
            return "hybrid", "refresh_requested_with_local_context"
        return "pdl", "refresh_requested_without_local_candidates"

    if local_count == 0:
        return "pdl", "local_candidates_empty"

    blended_quality = (
        (0.45 * similarity_score)
        + (0.35 * feedback_success_rate)
        + (0.20 * candidate_diversity)
    )
    if blended_quality >= 0.68 and similarity_score >= 0.62 and candidate_diversity >= 0.24:
        return "local", "strong_similarity_feedback_and_diversity"
    if similarity_score < 0.38 and candidate_diversity < 0.18:
        return "pdl", "low_similarity_and_low_diversity"
    return "hybrid", "mixed_quality_signals_prefer_hybrid"


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
    feedback_learning: FeedbackLearningContext,
    exploration: ExplorationContext,
) -> list[CandidateResult]:
    ensure_all_collections()
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
    profiles = profile_repo.latest_by_candidate_ids(list(best_by_candidate.keys()))
    ordered = sorted(best_by_candidate.items(), key=lambda row: row[1]["score"], reverse=True)

    weights = _load_scoring_weights(db, job_id=job.id)
    local_results: list[CandidateResult] = []
    company_counts: dict[str, int] = {}
    role_counts: dict[str, int] = {}
    for candidate_id, item in ordered[:RESULT_LIMIT]:
        payload = item["payload"]
        semantic = _normalize_vector_score(item["score"])
        profile = profiles.get(candidate_id)

        historical = 0.0
        if profile:
            historical = max(0.0, min(1.0, profile.fit_score / 5.0))
        semantic_component = (0.70 * semantic) + (0.30 * historical)
        feedback_direct = _feedback_adjustment(
            feedback_learning.candidate_feedback.get(candidate_id),
            bias=weights.feedback_bias,
        )

        company = (profile.company if profile else str(payload.get("company") or "")).strip()
        role = (profile.role if profile else str(payload.get("role") or "")).strip() or "Unknown Role"
        skills = profile.skills if profile else [str(skill) for skill in (payload.get("skills") or []) if str(skill).strip()]
        global_skill_feedback = _score_feedback_skills(skills or [], feedback_learning.global_skill_bias) * 0.05
        role_feedback = _score_feedback_role(role, feedback_learning.global_role_bias) * 0.03
        diversity_bonus = _diversity_bonus(
            company=company,
            role=role,
            company_counts=company_counts,
            role_counts=role_counts,
        )
        exploration_bonus = _exploration_bonus(exploration)
        feedback_bias = feedback_direct + global_skill_feedback + role_feedback
        rejection_penalty = _candidate_rejection_penalty(candidate_id, feedback_learning)
        log_metric(
            "candidate_penalty",
            job_id=job.id,
            candidate_id=candidate_id,
            penalty=round(rejection_penalty, 4),
        )
        final = _compute_final_score(
            semantic_component=semantic_component,
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

        result = CandidateResult(
            id=candidate_id,
            name=name,
            role=role,
            company=company,
            skills=skills or [],
            summary=summary,
            fitScore=fit_score,
            decision=decision,
            explanation=CandidateExplanation(
                semanticScore=round(semantic, 4),
                skillOverlap=0.0,
                finalScore=round(final, 4),
                pdlRelevance=0.0,
                recencyScore=0.0,
                penalties={
                    "source": 1.0,
                    "feedbackBias": round(feedback_bias, 4),
                    "rejectionPenalty": round(rejection_penalty, 4),
                    "explorationBonus": round(exploration_bonus, 4),
                },
            ),
            strategy=_strategy_from_score(fit_score),
            status="new",
        )
        local_results.append(result)
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

    return local_results


def _build_ranked_candidates_from_pdl(
    *,
    db: Session,
    job,
    mode: str,
    size: int,
    feedback_learning: FeedbackLearningContext,
    exploration: ExplorationContext,
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

    job_vec = _job_vector(job, feedback_learning)
    job_skills = _job_skill_set(job)
    ensure_all_collections()
    delete_candidate_vectors(job.id)

    weights = _load_scoring_weights(db, job_id=job.id)
    profile_repo = CandidateProfileRepository(db)
    company_counts: dict[str, int] = {}
    role_counts: dict[str, int] = {}

    scored: list[tuple[CandidateResult, float]] = []
    total_candidates = len(candidates)
    for index, item in enumerate(candidates):
        if not isinstance(item, dict):
            continue

        candidate_id = _candidate_id(item)
        candidate_name = _candidate_name(item, candidate_id)
        candidate_role = _candidate_role(item)
        candidate_company = _candidate_company(item)
        candidate_location = _candidate_location(item)
        candidate_skills = _candidate_skills(item)
        candidate_summary = _candidate_summary(item)
        candidate_email = _extract_candidate_email(item)
        candidate_external_id = _extract_candidate_external_id(item)
        candidate_identity_key = _candidate_identity_key(item)

        if not candidate_name.strip() or not candidate_role.strip():
            continue

        candidate_embed_text = _candidate_embedding_text(
            name=candidate_name,
            role=candidate_role,
            company=candidate_company,
            skills=candidate_skills,
            summary=candidate_summary,
        )
        candidate_embed_text += _build_embedding_boost_suffix(
            feedback_learning=feedback_learning,
            role=candidate_role,
            skills=candidate_skills,
        )
        candidate_chunks = chunk_text(candidate_embed_text)
        candidate_vectors = [_embed_text(chunk) for chunk in candidate_chunks]
        candidate_vec = average_vectors(candidate_vectors)

        cosine_score = cosine_similarity(job_vec, candidate_vec)
        semantic_similarity = _normalize_similarity(cosine_score)
        pdl_relevance = _pdl_relevance(item, index=index, total=total_candidates)
        skill_overlap = _skill_overlap(job_skills, candidate_skills)
        recency_score = _candidate_recency_score(item)

        semantic_component = (
            (weights.semantic * semantic_similarity)
            + (weights.skill * skill_overlap)
            + (weights.recency * recency_score)
        )
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
        log_metric(
            "candidate_penalty",
            job_id=job.id,
            candidate_id=candidate_id,
            penalty=round(rejection_penalty, 4),
        )
        final_score = _compute_final_score(
            semantic_component=semantic_component,
            pdl_component=pdl_component,
            feedback_bias=feedback_bias,
            diversity_bonus=diversity_bonus,
            exploration_bonus=exploration_bonus,
            rejection_penalty=rejection_penalty,
            semantic_penalty=semantic_penalty,
            missing_skills_penalty=missing_skills_penalty,
        )

        fit_score = round(final_score * 5, 2)
        decision = _decision_from_score(final_score)

        result = CandidateResult(
            id=candidate_id,
            name=candidate_name,
            role=candidate_role,
            company=candidate_company,
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

    ranked = sorted(scored, key=lambda row: row[1], reverse=True)
    diverse = _diverse_shortlist(ranked, limit=RESULT_LIMIT)

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


def _merge_candidates(*, db: Session, local_results: list[CandidateResult], pdl_results: list[CandidateResult]) -> list[CandidateResult]:
    all_candidates = local_results + pdl_results
    if not all_candidates:
        return []

    candidate_ids = [candidate.id for candidate in all_candidates if candidate.id]
    profiles = CandidateProfileRepository(db).latest_by_candidate_ids(candidate_ids)

    merged: dict[str, CandidateResult] = {}
    for candidate in all_candidates:
        dedupe_key = _dedupe_key_from_result(candidate, profiles)
        existing = merged.get(dedupe_key)
        if not existing or candidate.fitScore > existing.fitScore:
            merged[dedupe_key] = candidate
    return sorted(merged.values(), key=lambda row: row.fitScore, reverse=True)[:RESULT_LIMIT]


def _build_candidate_state_maps(db: Session, *, job_id: str) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    interview_status_map: dict[str, str] = {}
    outreach_status_map: dict[str, str] = {}
    export_status_map: dict[str, str] = {}

    for row in InterviewRepository(db).list_for_job(job_id):
        interview_status_map[row.candidate_id] = (row.status or "").strip().lower() or "new"

    for row in OutreachEventRepository(db).list_for_job(job_id):
        outreach_status_map[row.candidate_id] = (row.status or "").strip().lower() or "pending"

    for row in ATSExportRepository(db).list_for_job(job_id):
        export_state = (row.status or "").strip().lower() or "pending"
        for candidate_id in row.candidate_ids or []:
            export_status_map[str(candidate_id)] = export_state

    return interview_status_map, outreach_status_map, export_status_map


def _attach_candidate_workflow_state(db: Session, *, job_id: str, candidates: list[CandidateResult]) -> list[CandidateResult]:
    if not candidates:
        return candidates

    interview_status_map, outreach_status_map, export_status_map = _build_candidate_state_maps(db, job_id=job_id)
    for candidate in candidates:
        export_status = export_status_map.get(candidate.id, "pending")
        outreach_status = outreach_status_map.get(candidate.id, "pending")
        status = interview_status_map.get(candidate.id, candidate.status or "new")

        if export_status == "exported":
            status = "exported"
        elif outreach_status in {"sent", "dry_run"}:
            status = "contacted"

        candidate.status = status
        candidate.outreachStatus = outreach_status
        candidate.exportStatus = export_status
    return candidates


def fetch_ranked_candidates(*, db: Session, job_id: str, mode: str | None = None, refresh: bool = False) -> list[CandidateResult]:
    jobs = JobRepository(db)
    job = jobs.get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)

    resolved_mode = _resolve_mode(mode)
    log_metric("retrieval_request", job_id=job.id, mode=resolved_mode, refresh=refresh)
    feedback_learning = _build_feedback_learning_context(db, job_id=job.id)
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
    local_results = _build_local_candidates(
        db=db,
        job=job,
        mode=resolved_mode,
        feedback_learning=feedback_learning,
        exploration=exploration,
    )
    avg_local_similarity = mean([row.explanation.semanticScore for row in local_results]) if local_results else 0.0
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
    pdl_allowed = _allow_pdl_when_qdrant_is_unhealthy()
    should_call_pdl = switching_mode in {"hybrid", "pdl"}
    if should_call_pdl and not pdl_allowed:
        logger.warning(
            "pdl_suppressed_due_to_qdrant_error job_id=%s qdrant_error=%s",
            job_id,
            last_qdrant_search_error() or "unknown",
        )
        log_metric("fallback", source="candidate_retrieval", reason="qdrant_error_backoff")
        log_metric("pdl_suppressed", job_id=job.id, mode=resolved_mode, reason="qdrant_error_backoff")
        return _attach_candidate_workflow_state(db, job_id=job.id, candidates=local_results[:RESULT_LIMIT])

    if not should_call_pdl:
        logger.info(
            "local_hit job_id=%s count=%s top_semantic=%.4f adaptive_threshold=%.4f avg_similarity=%.4f exploration_rate=%.3f exploration_used=%s exploration_total=%s",
            job_id,
            len(local_results),
            local_top_semantic,
            adaptive_threshold,
            avg_local_similarity,
            exploration.rate,
            exploration.used,
            exploration.total,
        )
        log_metric("candidate_count", job_id=job.id, count=len(local_results), mode=resolved_mode, source="local")
        log_metric("local_hit", job_id=job.id, mode=resolved_mode, top_semantic=round(local_top_semantic, 4))
        log_metric(
            "exploration_usage",
            job_id=job.id,
            selected_mode="local",
            rate=round(exploration.rate, 4),
            used=exploration.used,
            total=exploration.total,
        )
        return _attach_candidate_workflow_state(db, job_id=job.id, candidates=local_results[:RESULT_LIMIT])

    fallback_reason = switch_reason
    logger.info(
        "pdl_switch_decision job_id=%s selected_mode=%s local_count=%s top_semantic=%.4f threshold=%.4f reason=%s",
        job_id,
        switching_mode,
        local_count,
        local_top_semantic,
        adaptive_threshold,
        fallback_reason,
    )
    log_metric("pdl_fallback", job_id=job.id, mode=resolved_mode, reason=fallback_reason)
    log_metric("fallback", source="candidate_retrieval", reason=fallback_reason)
    size = max(PDL_SEARCH_SIZE, 24) if resolved_mode == "elite" else PDL_SEARCH_SIZE
    pdl_results = _build_ranked_candidates_from_pdl(
        db=db,
        job=job,
        mode=resolved_mode,
        size=size,
        feedback_learning=feedback_learning,
        exploration=exploration,
    )

    if switching_mode == "hybrid" and local_results:
        logger.info(
            "hybrid_mode job_id=%s local_count=%s pdl_count=%s top_semantic=%.4f adaptive_threshold=%.4f avg_similarity=%.4f refresh=%s",
            job_id,
            len(local_results),
            len(pdl_results),
            local_top_semantic,
            adaptive_threshold,
            avg_local_similarity,
            refresh,
        )
        candidates = _merge_candidates(db=db, local_results=local_results, pdl_results=pdl_results)
        log_metric("candidate_count", job_id=job.id, count=len(candidates), mode=resolved_mode, source="hybrid")
    else:
        logger.info(
            "pdl_mode job_id=%s local_count=%s pdl_count=%s adaptive_threshold=%.4f refresh=%s",
            job_id,
            len(local_results),
            len(pdl_results),
            adaptive_threshold,
            refresh,
        )
        candidates = pdl_results
        log_metric("candidate_count", job_id=job.id, count=len(candidates), mode=resolved_mode, source="pdl")
    log_metric(
        "exploration_usage",
        job_id=job.id,
        selected_mode=switching_mode,
        rate=round(exploration.rate, 4),
        used=exploration.used,
        total=exploration.total,
    )
    logger.info(
        "exploration_usage job_id=%s mode=%s rate=%.3f used=%s total=%s",
        job_id,
        switching_mode,
        exploration.rate,
        exploration.used,
        exploration.total,
    )

    db.commit()
    notify_slack(
        title="Pontis Candidates Ready",
        lines=[
            f"job_id={job.id}",
            f"mode={resolved_mode}",
            f"count={len(candidates)}",
            f"switch={switching_mode}",
        ],
    )
    return _attach_candidate_workflow_state(db, job_id=job.id, candidates=candidates)


def warm_candidate_retrieval() -> int:
    ensure_all_collections()
    preloaded = preload_sample_candidate_embeddings()
    logger.info("candidate_retrieval_warmup embeddings_preloaded=%s", preloaded)
    return preloaded


def apply_feedback(*, db: Session, job_id: str, candidate_id: str, action: str) -> dict:
    jobs = JobRepository(db)
    if not jobs.get(job_id):
        raise APIError("Job not found", status_code=404)

    action = action.strip().lower()
    if action not in {"accept", "reject"}:
        raise APIError("action must be accept or reject", status_code=400)

    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if not profile:
        raise APIError("Candidate not found for this job", status_code=404)

    scoring_repo = ScoringProfileRepository(db)
    before_profile = scoring_repo.get_or_create(job_id=job_id)
    before_feedback_bias = float(before_profile.feedback_bias)

    CandidateFeedbackRepository(db).upsert(job_id=job_id, candidate_id=candidate_id, feedback=action)
    after_profile = scoring_repo.apply_feedback_adjustment(job_id=job_id, feedback=action)
    after_feedback_bias = float(after_profile.feedback_bias)
    feedback_bias_delta = round(after_feedback_bias - before_feedback_bias, 6)
    InterviewRepository(db).upsert_status(
        job_id=job_id,
        candidate_id=candidate_id,
        status="shortlisted" if action == "accept" else "rejected",
        create_default="shortlisted",
    )
    db.commit()
    feedback_count = len(CandidateFeedbackRepository(db).list_for_job(job_id))
    log_metric("feedback_count", job_id=job_id, count=feedback_count)
    log_metric(
        "feedback_impact",
        job_id=job_id,
        candidate_id=candidate_id,
        action=action,
        feedback_bias_before=round(before_feedback_bias, 6),
        feedback_bias_after=round(after_feedback_bias, 6),
        feedback_bias_delta=feedback_bias_delta,
    )
    notify_slack(
        title="Pontis Candidate Feedback",
        lines=[
            f"job_id={job_id}",
            f"candidate_id={candidate_id}",
            f"action={action}",
            f"feedback_bias_delta={feedback_bias_delta}",
        ],
    )

    return {
        "jobId": job_id,
        "candidateId": candidate_id,
        "action": action,
        "message": "Feedback recorded and ranking weights updated",
    }


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


def refresh_candidates_for_job(*, db: Session, job_id: str, mode: str = "volume") -> int:
    refreshed = fetch_ranked_candidates(db=db, job_id=job_id, mode=mode)
    return len(refreshed)


def _diverse_shortlist(scored_rows: list[tuple[CandidateResult, float]], limit: int) -> list[tuple[CandidateResult, float]]:
    selected: list[tuple[CandidateResult, float]] = []
    selected_ids: set[str] = set()
    selected_signatures: set[tuple[str, str, str]] = set()
    used_companies: set[str] = set()

    for candidate, score in scored_rows:
        if len(selected) >= limit:
            break
        company_key = (candidate.company or "").strip().lower()
        signature = (candidate.name.strip().lower(), candidate.role.strip().lower(), company_key)
        if candidate.id in selected_ids or signature in selected_signatures:
            continue
        if company_key and company_key in used_companies:
            continue

        selected.append((candidate, score))
        selected_ids.add(candidate.id)
        selected_signatures.add(signature)
        if company_key:
            used_companies.add(company_key)

    for candidate, score in scored_rows:
        if len(selected) >= limit:
            break
        company_key = (candidate.company or "").strip().lower()
        signature = (candidate.name.strip().lower(), candidate.role.strip().lower(), company_key)
        if candidate.id in selected_ids or signature in selected_signatures:
            continue

        selected.append((candidate, score))
        selected_ids.add(candidate.id)
        selected_signatures.add(signature)

    return selected
