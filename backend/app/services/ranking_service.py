from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.metrics_service import log_metric


@dataclass(frozen=True)
class RankingWeights:
    similarity: float
    skill_overlap: float
    experience: float


def _normalize_skill_tokens(values: list[str]) -> set[str]:
    return {str(value).strip().lower() for value in values if str(value).strip()}


def _tokenize(text: str) -> list[str]:
    return [token for token in str(text or "").lower().replace("/", " ").replace("-", " ").split() if token]


def _job_experience(job_context: Any) -> str:
    keys = ("experience_level", "experienceRequired", "experience", "seniority")
    if isinstance(job_context, dict):
        for key in keys:
            value = job_context.get(key, "")
            if isinstance(value, str) and value.strip():
                return value.strip()
    else:
        for key in keys:
            value = getattr(job_context, key, "")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _candidate_experience(candidate: Any) -> str:
    if isinstance(candidate, dict):
        for key in ("experience", "summary", "bio", "experience_summary"):
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    return str(getattr(candidate, "experience", "") or getattr(candidate, "summary", "") or "").strip()


def _candidate_skills(candidate: Any) -> list[str]:
    if isinstance(candidate, dict):
        return [str(skill).strip() for skill in (candidate.get("skills") or []) if str(skill).strip()]
    return [str(skill).strip() for skill in (getattr(candidate, "skills", []) or []) if str(skill).strip()]


def _job_requirement_skills(job_context: Any) -> list[str]:
    skills = getattr(job_context, "skills_required", None) if not isinstance(job_context, dict) else job_context.get("skills_required")
    if not isinstance(skills, list):
        return []
    return [str(skill).strip() for skill in skills if str(skill).strip()]


def _matched_skills(job_skills: list[str] | set[str], candidate_skills: list[str]) -> list[str]:
    job_tokens = _normalize_skill_tokens(list(job_skills))
    return [skill for skill in candidate_skills if skill.strip().lower() in job_tokens]


def _parse_year_span(text: str) -> tuple[float | None, float | None]:
    years: list[float] = []
    for token in _tokenize(text):
        if token.isdigit():
            years.append(float(token))
    if not years:
        return None, None
    if len(years) == 1:
        return years[0], years[0]
    return min(years), max(years)


def _experience_match(candidate_experience: str, job_experience: str) -> float:
    if not candidate_experience or not job_experience:
        return 0.0
    c_min, c_max = _parse_year_span(candidate_experience)
    j_min, j_max = _parse_year_span(job_experience)
    if c_min is None or j_min is None:
        return 0.35 if candidate_experience.lower() == job_experience.lower() else 0.0
    candidate_mid = (c_min + c_max) / 2.0
    job_mid = (j_min + j_max) / 2.0
    delta = abs(candidate_mid - job_mid)
    return max(0.0, min(1.0, 1.0 - (delta / 8.0)))


def _experience_match_summary(candidate_experience: str, job_experience: str) -> str:
    score = _experience_match(candidate_experience, job_experience)
    if score >= 0.8:
        return "Strong experience alignment"
    if score >= 0.5:
        return "Moderate experience alignment"
    return "Limited experience alignment"


def compute_match_score(*, similarity: float, skill_overlap: float, experience_match: float, weights: RankingWeights | None = None) -> float:
    resolved = weights or RankingWeights(similarity=0.7, skill_overlap=0.2, experience=0.1)
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
    candidate_experience = _candidate_experience(candidate)
    candidate_skills = _candidate_skills(candidate)
    job_skills = _job_requirement_skills(job_context)
    matched_skills = _matched_skills(job_skills, candidate_skills)
    experience_match_value = _experience_match(candidate_experience, job_experience)
    explanation = {
        "skills_matched": matched_skills,
        "experience_match": _experience_match_summary(candidate_experience, job_experience),
        "similarity_score": round(max(0.0, min(1.0, semantic_similarity)), 4),
        "candidate_experience": candidate_experience,
        "job_experience": job_experience,
        "experience_match_value": round(experience_match_value, 4),
    }
    log_metric(
        "ranking_explanation_built",
        similarity=explanation["similarity_score"],
        experience=explanation["experience_match_value"],
    )
    return explanation


def compute_final_score(
    *,
    semantic_similarity: float,
    skill_overlap: float,
    experience_match: float,
    ranking_weights: RankingWeights,
    recency_score: float = 0.0,
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
    raw = (
        base_match
        + (float(getattr(ranking_weights, "experience", 0.1)) * max(0.0, min(1.0, recency_score)))
        + pdl_component
        + feedback_bias
        + diversity_bonus
        + exploration_bonus
        - rejection_penalty
    )
    penalized = raw * semantic_penalty * missing_skills_penalty
    return max(0.0, min(1.0, penalized))
