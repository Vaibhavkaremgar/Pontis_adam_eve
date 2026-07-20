from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.repositories import CandidateProfileRepository, JobRepository
from app.schemas.candidate import CandidateExplanation, CandidateResult
from app.services.candidate_text import build_candidate_text
from app.services.embedding_service import embed
from app.services.metrics_service import log_metric
from app.services.qdrant_service import load_recruiter_memory, load_recruiter_preferences, upsert_recruiter_memory
from app.services.retrieval_quality_service import candidate_document_text, job_query_text
from app.services.ranking.models import coerce_candidate_explanation, ranked_candidate_sort_key

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


def _job_domain_focus(job: Any) -> str:
    text = " ".join(
        [
            _normalize_text(getattr(job, "title", "") or getattr(job, "role", "") or ""),
            _normalize_text(getattr(job, "description", "") or ""),
            " ".join(_job_skill_tokens(job)),
        ]
    ).lower()
    if any(token in text for token in ("sales", "account executive", "ae", "bdr", "sdr", "revops", "revenue", "pipeline")):
        return "sales"
    if any(token in text for token in ("engineer", "developer", "architect", "platform", "infra", "backend", "frontend", "data", "security", "devops", "cloud")):
        return "tech"
    return "general"


def _snippet_quality_bonus(candidate: CandidateResult) -> float:
    profile_data = candidate.profileData if isinstance(candidate.profileData, dict) else {}
    value = _normalize_text(getattr(candidate, "snippetQuality", "") or profile_data.get("snippet_quality", ""))
    if value == "rich":
        return 0.06
    if value == "partial":
        return 0.03
    if value == "thin":
        return 0.01
    return 0.02


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


def _source_relevance_signal(candidate: CandidateResult) -> float:
    explanation = candidate.explanation
    if isinstance(explanation, dict):
        value = explanation.get("finalScore")
        if value is not None:
            try:
                return max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                pass
    elif explanation is not None:
        value = getattr(explanation, "finalScore", None)
        if value is not None:
            try:
                return max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                pass
    try:
        return max(0.0, min(1.0, float(candidate.fitScore or 0.0) / 5.0))
    except (TypeError, ValueError):
        return 0.0


def _xray_fallback_sort_key(candidate: CandidateResult, index: int) -> tuple[float, int, int, str]:
    profile_data = candidate.profileData if isinstance(candidate.profileData, dict) else {}
    try:
        search_page = int(profile_data.get("search_page") or profile_data.get("searchPage") or 0)
    except (TypeError, ValueError):
        search_page = 0
    try:
        search_position = int(profile_data.get("search_position") or profile_data.get("searchPosition") or 0)
    except (TypeError, ValueError):
        search_position = 0
    return (-_source_relevance_signal(candidate), search_page, search_position, f"{index:06d}-{candidate.id}")


def _explain_candidate(
    *,
    candidate: CandidateResult,
    semantic_similarity: float,
    domain_similarity: float,
    source_signal: float,
    recruiter_preference_score: float,
    historical_success_score: float,
    snippet_quality: str,
    domain_focus: str,
    matched_skills: list[str],
) -> CandidateResult:
    final_score = (
        max(0.0, min(1.0, semantic_similarity)) * 0.55
        + max(0.0, min(1.0, domain_similarity)) * 0.12
        + max(0.0, min(1.0, source_signal)) * 0.08
        + max(0.0, min(1.0, recruiter_preference_score)) * 0.15
        + max(0.0, min(1.0, historical_success_score)) * 0.05
        + _snippet_quality_bonus(candidate)
    )
    fit_score = round(final_score * 5.0, 2)
    evidence: list[str] = []
    if matched_skills:
        evidence.extend([f"{skill}" for skill in matched_skills[:4]])
    if domain_focus == "tech":
        evidence.append("Matches a technical hiring profile")
    elif domain_focus == "sales":
        evidence.append("Matches a revenue-oriented hiring profile")
    if semantic_similarity >= 0.65:
        evidence.append("Strong semantic alignment")
    if recruiter_preference_score >= 0.55:
        evidence.append("Matches recruiter preferences")
    if historical_success_score >= 0.45:
        evidence.append("Similar to previous successful hires")
    if snippet_quality in {"rich", "partial"}:
        evidence.append(f"{snippet_quality.capitalize()} source snippet")
    if not evidence:
        evidence.append("Relevant X-Ray and semantic signals")

    explanation = coerce_candidate_explanation(candidate.explanation) if candidate.explanation else CandidateExplanation(
        semanticScore=0.0,
        skillOverlap=0.0,
        finalScore=0.0,
        pdlRelevance=0.0,
        recencyScore=0.0,
        penalties={},
    )
    explanation.semanticScore = round(semantic_similarity, 4)
    explanation.skillOverlap = round(explanation.skillOverlap if isinstance(explanation.skillOverlap, (int, float)) else 0.0, 4)
    setattr(explanation, "finalScore", round(final_score, 4))
    explanation.recruiterPreferenceInfluence = round(recruiter_preference_score, 4)
    explanation.freshnessInfluence = round(historical_success_score, 4)
    explanation.sourceBreakdown = {
        **dict(explanation.sourceBreakdown or {}),
        "semanticSimilarity": round(semantic_similarity, 4),
        "domainSimilarity": round(domain_similarity, 4),
        "sourceSignal": round(source_signal, 4),
        "recruiterPreference": round(recruiter_preference_score, 4),
        "historicalSuccess": round(historical_success_score, 4),
        "snippetQuality": snippet_quality,
    }
    explanation.skillsMatched = matched_skills or list(explanation.skillsMatched or [])
    explanation.aiReasoning = "Why this candidate matched: " + "; ".join(evidence)

    update = {
        "fitScore": fit_score,
        "decision": "strong_match" if fit_score >= 4 else "potential" if fit_score >= 2.5 else "weak",
        "strategy": "HIGH" if fit_score >= 4 else "MEDIUM" if fit_score >= 2.5 else "LOW",
        "explanation": explanation,
        "profileData": {
            **dict(candidate.profileData or {}),
            "semanticRanking": {
                "semanticSimilarity": round(semantic_similarity, 4),
                "domainSimilarity": round(domain_similarity, 4),
                "sourceSignal": round(source_signal, 4),
                "recruiterPreference": round(recruiter_preference_score, 4),
                "historicalSuccess": round(historical_success_score, 4),
                "snippetQuality": snippet_quality,
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
    try:
        job_query = job_query_text(job)
        job_vector = embed(job_query)
        job_domain = _job_domain_focus(job)
        domain_context_text = " ".join(
            [
                job_query,
                _normalize_text(getattr(job, "description", "") or ""),
                f"domain focus: {job_domain}",
            ]
        ).strip()
        domain_vector = embed(domain_context_text or job_query or " ")
        try:
            recruiter_preferences = load_recruiter_preferences(recruiter_id) or {}
        except Exception as exc:
            logger.warning(
                "[recruiter_memory] recruiter_id=%s job_id=%s candidate_count=%s rerank_status=preference_load_failed error=%s",
                recruiter_id or "",
                getattr(job, "id", ""),
                len(candidates),
                str(exc),
            )
            recruiter_preferences = {}
        recruiter_pref_vector = [float(value) for value in (recruiter_preferences.get("vector") or [])]
        try:
            recruiter_memory = load_recruiter_memory(recruiter_id, limit=20) if recruiter_id else []
        except Exception as exc:
            logger.warning(
                "[recruiter_memory] recruiter_id=%s job_id=%s candidate_count=%s rerank_status=memory_load_failed error=%s",
                recruiter_id or "",
                getattr(job, "id", ""),
                len(candidates),
                str(exc),
            )
            recruiter_memory = []
        logger.info(
            "[recruiter_memory] recruiter_id=%s job_id=%s candidate_count=%s memory_count=%s rerank_status=%s",
            recruiter_id or "",
            getattr(job, "id", ""),
            len(candidates),
            len(recruiter_memory),
            "loaded" if recruiter_memory else "empty",
        )
    except Exception as exc:
        logger.warning(
            "[semantic_rerank_fallback] job_id=%s recruiter_id=%s candidate_count=%s rerank_status=embedding_unavailable error=%s",
            getattr(job, "id", ""),
            recruiter_id or "",
            len(candidates),
            str(exc),
        )
        log_metric(
            "semantic_rerank_fallback",
            job_id=getattr(job, "id", ""),
            recruiter_id=recruiter_id,
            candidate_count=len(candidates),
            fallback_reason="embedding_unavailable",
            error_type=type(exc).__name__,
        )
        ordered = sorted(list(enumerate(candidates)), key=lambda item: _xray_fallback_sort_key(item[1], item[0]))
        return [candidate for _, candidate in ordered]

    reranked: list[CandidateResult] = []
    try:
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
            semantic_similarity = _cosine_similarity(job_vector, candidate_vector)
            domain_similarity = _cosine_similarity(domain_vector, candidate_vector)
            recruiter_preference_score = _cosine_similarity(recruiter_pref_vector, candidate_vector) if recruiter_pref_vector else 0.0
            historical_success_score = _historical_memory_score(recruiter_memory=recruiter_memory, candidate_vector=candidate_vector)
            matched_skills = _matched_skills(job, candidate)
            source_signal = max(0.0, min(1.0, float(getattr(candidate.explanation, "finalScore", 0.0) or candidate.fitScore or 0.0) / 5.0))
            profile_data = candidate.profileData if isinstance(candidate.profileData, dict) else {}
            snippet_quality = _normalize_text(getattr(candidate, "snippetQuality", "") or profile_data.get("snippet_quality", ""))

            reranked_candidate = _explain_candidate(
                candidate=candidate,
                semantic_similarity=semantic_similarity,
                domain_similarity=domain_similarity,
                source_signal=source_signal,
                recruiter_preference_score=recruiter_preference_score,
                historical_success_score=historical_success_score,
                snippet_quality=snippet_quality,
                domain_focus=job_domain,
                matched_skills=matched_skills,
            )
            reranked.append(reranked_candidate)

            log_metric(
                "semantic_rerank_candidate",
                job_id=getattr(job, "id", ""),
                candidate_id=candidate.id,
                semantic_similarity=round(semantic_similarity, 4),
                domain_similarity=round(domain_similarity, 4),
                source_signal=round(source_signal, 4),
                recruiter_preference=round(recruiter_preference_score, 4),
                historical_success=round(historical_success_score, 4),
                snippet_quality=snippet_quality,
                recruiter_memory_influence=round(max(recruiter_preference_score, historical_success_score), 4),
            )
            logger.info(
                "[rerank_scores] job_id=%s recruiter_id=%s candidate_id=%s semantic=%.4f domain=%.4f source=%.4f recruiter=%.4f historical=%.4f snippet_quality=%s",
                getattr(job, "id", ""),
                recruiter_id or "",
                candidate.id,
                semantic_similarity,
                domain_similarity,
                source_signal,
                recruiter_preference_score,
                historical_success_score,
                snippet_quality,
            )
    except Exception as exc:
        logger.warning(
            "[semantic_rerank_fallback] job_id=%s recruiter_id=%s candidate_count=%s rerank_status=loop_failed error=%s",
            getattr(job, "id", ""),
            recruiter_id or "",
            len(candidates),
            str(exc),
        )
        log_metric(
            "semantic_rerank_fallback",
            job_id=getattr(job, "id", ""),
            recruiter_id=recruiter_id,
            candidate_count=len(candidates),
            fallback_reason="loop_failed",
            error_type=type(exc).__name__,
        )
        ordered = sorted(list(enumerate(candidates)), key=lambda item: _xray_fallback_sort_key(item[1], item[0]))
        return [candidate for _, candidate in ordered]

    reranked.sort(key=ranked_candidate_sort_key)
    logger.info(
        "[semantic_rerank] job_id=%s recruiter_id=%s candidate_count=%s source_query=%s rerank_status=complete",
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
            "role": profile.current_role,
            "company": profile.current_company,
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
            "candidateRole": profile.current_role,
            "candidateCompany": profile.current_company,
        },
    )
