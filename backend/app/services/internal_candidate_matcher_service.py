from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import (
    INTERNAL_MATCH_DEFAULT_LIMIT,
    INTERNAL_MATCH_MAX_LIMIT,
    INTERNAL_MATCH_MIN_INTERVIEW_SCORE,
    INTERNAL_MATCH_MIN_RESUME_SCORE,
    INTERNAL_MATCH_WEIGHTS,
)
from app.db.repositories import JobRepository
from app.services.skill_normalizer import normalize_skills, parse_experience


@dataclass
class InternalCandidateMatchFilters:
    skills: list[str]
    experience: str
    current_role: str
    current_company: str
    location: str
    min_resume_score: float | None
    min_interview_score: float | None
    recommendation: str
    interview_date_from: str
    interview_date_to: str
    talent_pool_ready_only: bool


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _freshness_score(value: datetime | str | None) -> float:
    if isinstance(value, str) and value.strip():
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return 0.35
    if not isinstance(value, datetime):
        return 0.35
    dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 86400.0)
    return max(0.0, min(1.0, math.exp(-age_days / 21.0)))


def _parse_date(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _experience_match(candidate_years: float, job_experience: str) -> float:
    job_years = float(parse_experience(job_experience))
    if job_years <= 0:
        return 0.5 if candidate_years >= 0 else 0.0
    ratio = min(candidate_years, job_years) / max(candidate_years, job_years, 1.0)
    if candidate_years >= job_years:
        return max(0.6, ratio)
    return max(0.0, ratio)


def _skill_coverage(job_skills: list[str], candidate_skills: list[str]) -> tuple[float, list[str]]:
    job_tokens = normalize_skills(job_skills)
    candidate_tokens = normalize_skills(candidate_skills)
    if not job_tokens:
        return 0.0, []
    matched = sorted(job_tokens.intersection(candidate_tokens))
    return min(1.0, len(matched) / max(1, len(job_tokens))), matched


class InternalCandidateMatcher:
    def __init__(self, db: Session) -> None:
        self.db = db

    def match(self, *, job_id: str, filters: InternalCandidateMatchFilters | dict[str, Any] | None = None, limit: int | None = None) -> dict[str, Any]:
        job = JobRepository(self.db).get(job_id)
        if not job:
            raise ValueError("Job not found")

        normalized_filters = self._normalize_filters(filters)
        max_limit = max(1, min(int(limit or INTERNAL_MATCH_DEFAULT_LIMIT), INTERNAL_MATCH_MAX_LIMIT))
        job_skills = self._job_skills(job)
        job_experience = self._job_experience(job)

        rows = self.db.execute(
            text(
                """
                WITH latest_application AS (
                    SELECT DISTINCT ON (job_id, candidate_id)
                        job_id, candidate_id, resume_score, evaluation_json, evaluation_timestamp,
                        shortlist_email_sent_at, shortlist_email_status, shortlisted_at, rejected_at
                    FROM candidate_applications
                    WHERE job_id = :job_id
                    ORDER BY job_id, candidate_id, created_at DESC
                ),
                latest_interview AS (
                    SELECT DISTINCT ON (job_id, candidate_id)
                        job_id, candidate_id, interview_score, completed_at, status
                    FROM interviews
                    WHERE job_id = :job_id
                    ORDER BY job_id, candidate_id, completed_at DESC NULLS LAST, created_at DESC
                ),
                latest_interview_eval AS (
                    SELECT DISTINCT ON (job_id, candidate_id)
                        job_id, candidate_id, recommendation, competency_scores, metadata, created_at
                    FROM interview_evaluations
                    WHERE job_id = :job_id
                    ORDER BY job_id, candidate_id, created_at DESC
                )
                SELECT
                    cp.candidate_id,
                    cp.name,
                    cp.current_role AS current_title,
                    cp.current_company,
                    cp.total_experience_years,
                    cp.skills,
                    cp.raw_data,
                    cp.parsed_resume_json,
                    cp.last_refreshed_at AS talent_pool_ready_at,
                    la.resume_score,
                    la.evaluation_json,
                    la.evaluation_timestamp,
                    li.interview_score,
                    li.completed_at AS interview_completed_at,
                    lie.recommendation AS interview_recommendation,
                    lie.competency_scores,
                    lie.metadata AS interview_metadata
                FROM candidates cp
                LEFT JOIN latest_application la
                    ON la.job_id = cp.job_id AND la.candidate_id = cp.candidate_id
                LEFT JOIN latest_interview li
                    ON li.job_id = cp.job_id AND li.candidate_id = cp.candidate_id
                LEFT JOIN latest_interview_eval lie
                    ON lie.job_id = cp.job_id AND lie.candidate_id = cp.candidate_id
                WHERE cp.job_id = :job_id
                  AND cp.agency_id = :company_id
                  AND cp.last_refreshed_at IS NOT NULL
                ORDER BY cp.last_refreshed_at DESC, cp.total_experience_years DESC, cp.name ASC
                """
            ),
            {"job_id": job_id, "company_id": job.company_id},
        ).mappings().all()

        candidates: list[dict[str, Any]] = []
        for row in rows:
            candidate = dict(row)
            if not self._passes_filters(candidate, normalized_filters):
                continue
            candidates.append(self._score_candidate(candidate, job_skills=job_skills, job_experience=job_experience))

        candidates.sort(key=lambda item: item["overallMatch"], reverse=True)
        return {
            "jobId": job_id,
            "total": len(candidates),
            "items": candidates[:max_limit],
        }

    def _normalize_filters(self, filters: InternalCandidateMatchFilters | dict[str, Any] | None) -> InternalCandidateMatchFilters:
        if isinstance(filters, InternalCandidateMatchFilters):
            return filters
        data = dict(filters or {})
        return InternalCandidateMatchFilters(
            skills=list(data.get("skills") or []),
            experience=str(data.get("experience") or ""),
            current_role=str(data.get("currentRole") or data.get("current_role") or ""),
            current_company=str(data.get("currentCompany") or data.get("current_company") or ""),
            location=str(data.get("location") or ""),
            min_resume_score=data.get("minResumeScore", data.get("min_resume_score")),
            min_interview_score=data.get("minInterviewScore", data.get("min_interview_score")),
            recommendation=str(data.get("recommendation") or ""),
            interview_date_from=str(data.get("interviewDateFrom") or data.get("interview_date_from") or ""),
            interview_date_to=str(data.get("interviewDateTo") or data.get("interview_date_to") or ""),
            talent_pool_ready_only=bool(data.get("talentPoolReadyOnly", data.get("talent_pool_ready_only", True))),
        )

    def _job_skills(self, job: Any) -> list[str]:
        structured = getattr(job, "structured_data", None)
        raw: list[str] = []
        if isinstance(structured, dict):
            for key in ("skills_required", "required_skills", "skills"):
                value = structured.get(key)
                if isinstance(value, list):
                    raw.extend([str(item) for item in value if str(item).strip()])
        raw.extend([str(item) for item in getattr(job, "skills_required", []) or [] if str(item).strip()])
        return sorted(normalize_skills(raw)) or raw

    def _job_experience(self, job: Any) -> str:
        structured = getattr(job, "structured_data", None)
        if isinstance(structured, dict):
            for key in ("experience_required", "experienceRequired", "experience", "experience_level"):
                value = structured.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, (int, float)):
                    return f"{float(value):g} years"
        for key in ("experience_required", "experience_level", "experienceRequired", "experience"):
            value = getattr(job, key, "")
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (int, float)):
                return f"{float(value):g} years"
        return ""

    def _passes_filters(self, candidate: dict[str, Any], filters: InternalCandidateMatchFilters) -> bool:
        if filters.talent_pool_ready_only and not candidate.get("talent_pool_ready_at"):
            return False

        if filters.current_role and filters.current_role.lower() not in _normalize_text(candidate.get("current_title")):
            return False
        if filters.current_company and filters.current_company.lower() not in _normalize_text(candidate.get("current_company")):
            return False

        if filters.min_resume_score is not None:
            if _as_float(candidate.get("resume_score")) < float(filters.min_resume_score):
                return False
        elif _as_float(candidate.get("resume_score")) < INTERNAL_MATCH_MIN_RESUME_SCORE:
            return False

        if filters.min_interview_score is not None:
            if _as_float(candidate.get("interview_score")) < float(filters.min_interview_score):
                return False
        elif _as_float(candidate.get("interview_score")) < INTERNAL_MATCH_MIN_INTERVIEW_SCORE:
            return False

        if filters.recommendation:
            normalized = _normalize_text(filters.recommendation)
            candidate_recommendation = _normalize_text(
                candidate.get("interview_recommendation") or _as_dict(candidate.get("evaluation_json")).get("recommendation")
            )
            if normalized not in candidate_recommendation:
                return False

        interview_date = candidate.get("interview_completed_at")
        if filters.interview_date_from:
            after = _parse_date(filters.interview_date_from)
            if after and isinstance(interview_date, datetime) and interview_date < after:
                return False
        if filters.interview_date_to:
            before = _parse_date(filters.interview_date_to)
            if before and isinstance(interview_date, datetime) and interview_date > before:
                return False

        if filters.location:
            location_text = _normalize_text(candidate.get("raw_data", {}).get("location"))
            parsed_location = _normalize_text(candidate.get("parsed_resume_json", {}).get("location"))
            if filters.location.lower() not in location_text and filters.location.lower() not in parsed_location:
                return False

        if filters.skills:
            _, matched = _skill_coverage(filters.skills, candidate.get("skills") or [])
            if not matched:
                return False

        return True

    def _score_candidate(self, candidate: dict[str, Any], *, job_skills: list[str], job_experience: str) -> dict[str, Any]:
        resume_score = _as_float(candidate.get("resume_score"))
        interview_score = _as_float(candidate.get("interview_score"))
        skills = list(candidate.get("skills") or [])
        skill_coverage, matched_skills = _skill_coverage(job_skills, skills)
        experience_match = _experience_match(_as_float(candidate.get("total_experience_years")), job_experience)
        freshness = _freshness_score(candidate.get("interview_completed_at") or candidate.get("talent_pool_ready_at"))

        weights = INTERNAL_MATCH_WEIGHTS
        weighted_sum = (
            resume_score * weights["resume_score"]
            + interview_score * weights["interview_score"]
            + skill_coverage * weights["skill_coverage"]
            + experience_match * weights["experience_match"]
            + freshness * weights["freshness"]
        )
        weight_total = sum(weights.values()) or 1.0
        overall = max(0.0, min(100.0, weighted_sum / weight_total * 100.0))
        explanation = (
            f"Matched {len(matched_skills)} of {max(1, len(job_skills))} target skills; "
            f"resume score {resume_score:.1f}, interview score {interview_score:.1f}, "
            f"experience fit {experience_match:.2f}, freshness {freshness:.2f}."
        )

        return {
            "candidateId": candidate.get("candidate_id", ""),
            "name": candidate.get("name", ""),
            "currentRole": candidate.get("current_title", "") or "",
            "currentCompany": candidate.get("current_company", "") or "",
            "experience": _as_float(candidate.get("total_experience_years")),
            "resumeMatch": round(resume_score, 4),
            "interviewScore": round(interview_score, 4),
            "overallMatch": round(overall, 4),
            "recommendation": _normalize_text(
                candidate.get("interview_recommendation") or _as_dict(candidate.get("evaluation_json")).get("recommendation") or ""
            ),
            "interviewDate": candidate.get("interview_completed_at").isoformat() if isinstance(candidate.get("interview_completed_at"), datetime) else None,
            "freshness": round(freshness, 4),
            "matchingExplanation": explanation,
            "skills": skills,
        }
