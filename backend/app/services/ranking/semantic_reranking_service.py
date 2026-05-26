from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.repositories import CandidateProfileRepository, JobRepository
from app.schemas.candidate import CandidateResult
from app.services.candidate_text import build_candidate_text
from app.services.embedding_service import embed
from app.services.metrics_service import log_metric
from app.services.qdrant_service import load_recruiter_memory, load_recruiter_preferences, upsert_recruiter_memory
from app.services.retrieval_quality_service import candidate_document_text, job_query_text

logger = logging.getLogger(__name__)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    limit = min(len(left), len(right))
    dot = sum(float(left[i]) * float(right[i]) for i in range(limit))
    left_norm = math.sqrt(sum(float(value) * float(value) for value in left[:limit]))
    right_norm = math.sqrt(sum(float(value) * float(value) for value in right[:limit]))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def _normalize_text(value: Any) -> str:
    return " ".join(str(value).split()).strip() if value is not None else ""


def _job_skill_tokens(job: Any) -> list[str]:
    structured = getattr(job, "structured_data", None)
    skills: list[str] = []
    if isinstance(structured, dict):
        raw_skills = structured.get("skills") or structured.get("skills_required") or []
        if isinstance(raw_skills, list):
            skills.extend(_normalize_text(item) for item in raw_skills if _normalize_text(item))
    raw = getattr(job, "skills_required", None) or []
    if isinstance(raw, list):
        skills.extend(_normalize_text(item) for item in raw if _normalize_text(item))
    seen: set[str] = set()
    ordered: list[str] = []
    for skill in skills:
        key = skill.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(skill)
    return ordered


def _matched_skills(job: Any, candidate: CandidateResult) -> list[str]:
    job_skills = {skill.lower() for skill in _job_skill_tokens(job)}
    candidate_skills = {str(skill).strip().lower() for skill in (candidate.skills or []) if str(skill).strip()}
    return sorted({skill for skill in candidate_skills.intersection(job_skills)})


def _historical_memory_score(*, recruiter_memory: list[dict[str, Any]], candidate_vector: list[float]) -> float:
    scored: list[float] = []
    for item in recruiter_memory:
        payload = item.get("payload") or {}
        if str(payload.get("memoryType") or "").strip().lower() not in {"successful_hire", "offer_sent", "advanced", "final_round"}:
            continue
        vector = [float(value) for value in (item.get("vector") or [])]
        if vector:
            scored.append(_cosine_similarity(candidate_vector, vector))
    if not scored:
        return 0.0
    return sum(scored) / len(scored)


def _explain_candidate(
    *,
    candidate: CandidateResult,
    xray_relevance_score: float,
    semantic_similarity: float,
    recruiter_preference_score: float,
    historical_success_score: float,
    matched_skills: list[str],
) -> CandidateResult:
    final_score = (
        max(0.0, min(1.0, xray_relevance_score)) * 0.45
        + max(0.0, min(1.0, semantic_similarity)) * 0.35
        + max(0.0, min(1.0, recruiter_preference_score)) * 0.15
        + max(0.0, min(1.0, historical_success_score)) * 0.05
    )
    fit_score = round(final_score * 5.0, 2)
    evidence: list[str] = []
    if matched_skills:
        evidence.extend([f"{skill}" for skill in matched_skills[:4]])
    if semantic_similarity >= 0.65:
        evidence.append("Strong semantic fit")
    if recruiter_preference_score >= 0.55:
        evidence.append("Matches recruiter preferences")
    if historical_success_score >= 0.45:
        evidence.append("Similar to previous successful hires")
    if not evidence:
        evidence.append("Relevant X-Ray and semantic signals")

    explanation = candidate.explanation.model_dump() if candidate.explanation else {}
    explanation.update(
        {
            "semanticScore": round(semantic_similarity, 4),
            "skillOverlap": round(explanation.get("skillOverlap", 0.0) if isinstance(explanation.get("skillOverlap"), (int, float)) else 0.0, 4),
            "finalScore": round(final_score, 4),
            "recruiterPreferenceInfluence": round(recruiter_preference_score, 4),
            "freshnessInfluence": round(historical_success_score, 4),
            "sourceBreakdown": {
                **dict(explanation.get("sourceBreakdown") or {}),
                "xrayRelevance": round(xray_relevance_score, 4),
                "semanticSimilarity": round(semantic_similarity, 4),
                "recruiterPreference": round(recruiter_preference_score, 4),
                "historicalSuccess": round(historical_success_score, 4),
                "skillSimilarity": round(len(matched_skills) / max(1, len(candidate.skills or [])), 4) if candidate.skills else 0.0,
            },
            "skillsMatched": matched_skills or explanation.get("skillsMatched") or [],
            "aiReasoning": "Why this candidate matched: " + "; ".join(evidence),
        }
    )

    update = {
        "fitScore": fit_score,
        "decision": "strong_match" if fit_score >= 4 else "potential" if fit_score >= 2.5 else "weak",
        "strategy": "HIGH" if fit_score >= 4 else "MEDIUM" if fit_score >= 2.5 else "LOW",
        "explanation": explanation,
        "profileData": {
            **dict(candidate.profileData or {}),
            "semanticRanking": {
                "xrayRelevance": round(xray_relevance_score, 4),
                "semanticSimilarity": round(semantic_similarity, 4),
                "recruiterPreference": round(recruiter_preference_score, 4),
                "historicalSuccess": round(historical_success_score, 4),
                "matchedSkills": matched_skills,
                "rankedAt": datetime.now(timezone.utc).isoformat(),
            },
        },
    }
    return candidate.model_copy(update=update)


def rerank_xray_candidates(
    *,
    db: Session,
    job: Any,
    candidates: list[CandidateResult],
    recruiter_id: str = "",
    source_query: str = "",
) -> list[CandidateResult]:
    if not candidates:
        return []

    recruiter_id = _normalize_text(recruiter_id)
    job_query = job_query_text(job)
    job_vector = embed(job_query)
    recruiter_preferences = load_recruiter_preferences(recruiter_id) or {}
    recruiter_pref_vector = [float(value) for value in (recruiter_preferences.get("vector") or [])]
    recruiter_memory = load_recruiter_memory(recruiter_id, limit=20) if recruiter_id else []

    reranked: list[CandidateResult] = []
    for candidate in candidates:
        candidate_text = candidate_document_text(
            candidate={
                "name": candidate.name,
                "role": candidate.role,
                "company": candidate.company,
                "summary": candidate.summary,
                "skills": candidate.skills,
                "location": candidate.location,
            }
        )
        candidate_vector = embed(candidate_text or candidate.name or candidate.id or " ")
        xray_relevance_score = float(candidate.explanation.finalScore if candidate.explanation else candidate.fitScore / 5.0)
        semantic_similarity = _cosine_similarity(job_vector, candidate_vector)
        recruiter_preference_score = _cosine_similarity(recruiter_pref_vector, candidate_vector) if recruiter_pref_vector else 0.0
        historical_success_score = _historical_memory_score(recruiter_memory=recruiter_memory, candidate_vector=candidate_vector)
        matched_skills = _matched_skills(job, candidate)

        reranked_candidate = _explain_candidate(
            candidate=candidate,
            xray_relevance_score=xray_relevance_score,
            semantic_similarity=semantic_similarity,
            recruiter_preference_score=recruiter_preference_score,
            historical_success_score=historical_success_score,
            matched_skills=matched_skills,
        )
        reranked.append(reranked_candidate)

        log_metric(
            "semantic_rerank_candidate",
            job_id=getattr(job, "id", ""),
            candidate_id=candidate.id,
            xray_relevance=round(xray_relevance_score, 4),
            semantic_similarity=round(semantic_similarity, 4),
            recruiter_preference=round(recruiter_preference_score, 4),
            historical_success=round(historical_success_score, 4),
        )

    reranked.sort(key=lambda item: (-float(item.explanation.finalScore if item.explanation else 0.0), -float(item.fitScore or 0.0), item.name or item.id))
    logger.info(
        "semantic_rerank_complete job_id=%s recruiter_id=%s candidate_count=%s source_query=%s",
        getattr(job, "id", ""),
        recruiter_id or "",
        len(reranked),
        source_query,
    )
    return reranked


def record_successful_candidate_memory(*, db: Session, job_id: str, candidate_id: str, memory_type: str = "successful_hire") -> None:
    job = JobRepository(db).get(job_id)
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if not job or not profile:
        return
    recruiter_id = JobRepository(db).get_recruiter_id(job_id)
    if not recruiter_id:
        return
    text = build_candidate_text(
        {
            "name": profile.name,
            "role": profile.role,
            "company": profile.company,
            "summary": profile.summary,
            "skills": list(profile.skills or []),
            "experience": getattr(profile, "total_experience_years", 0.0),
        }
    )
    vector = embed(text or profile.name or candidate_id or " ")
    upsert_recruiter_memory(
        recruiter_id=recruiter_id,
        job_id=job_id,
        candidate_id=candidate_id,
        vector=vector,
        payload={
            "memoryType": memory_type,
            "embeddingVersion": getattr(profile, "raw_data", {}).get("embedding_version") if isinstance(getattr(profile, "raw_data", {}), dict) else "",
            "candidateName": profile.name,
            "candidateRole": profile.role,
            "candidateCompany": profile.company,
        },
    )
