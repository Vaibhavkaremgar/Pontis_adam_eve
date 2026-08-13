from __future__ import annotations

import logging
import re
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import (
    EMBEDDING_VERSION,
    INTERNAL_CANDIDATE_MATCH_LIMIT,
    INTERNAL_CANDIDATE_MATCH_THRESHOLD,
    INTERNAL_CANDIDATE_MATCH_WEIGHTS,
    INTERNAL_CANDIDATE_MIN_MATCHES,
    INTERNAL_CANDIDATE_RETRIEVAL_TOP_K,
)
from app.db.repositories import JobRepository
from app.models.entities import CandidateProfileEntity
from app.schemas.candidate import CandidateExplanation, CandidateResult
from app.services.embedding_service import get_embedding
from app.services.job_text_service import build_job_text
from app.services.qdrant_service import QdrantUnavailableError, count_collection_points, search_internal_candidate_chunks
from app.services.qdrant_service import INTERNAL_CANDIDATE_COLLECTION_NAME
from app.services.skill_normalizer import normalize_skills, parse_experience
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _tokens(values: Any) -> set[str]:
    if isinstance(values, dict):
        values = list(values.keys()) + list(values.values())
    if isinstance(values, str):
        values = re.split(r"[,;/|]", values)
    if not isinstance(values, (list, tuple, set)):
        values = []
    return normalize_skills([_text(value) for value in values if _text(value)])


def _job_skills(job: Any) -> list[str]:
    structured = getattr(job, "structured_data", {}) if isinstance(getattr(job, "structured_data", {}), dict) else {}
    values = structured.get("skills_required") or structured.get("required_skills") or structured.get("skills") or getattr(job, "skills_required", [])
    return [_text(value) for value in (values if isinstance(values, list) else [values]) if _text(value)]


def _job_experience(job: Any) -> str:
    structured = getattr(job, "structured_data", {}) if isinstance(getattr(job, "structured_data", {}), dict) else {}
    for key in ("experience_required", "experienceRequired", "experience", "experience_level"):
        value = structured.get(key) or getattr(job, key, "")
        if value not in (None, ""):
            return _text(value)
    return ""


def _job_role(job: Any) -> str:
    structured = getattr(job, "structured_data", {}) if isinstance(getattr(job, "structured_data", {}), dict) else {}
    return _text(structured.get("role") or structured.get("title") or getattr(job, "title", ""))


def _candidate_skills(row: CandidateProfileEntity) -> list[str]:
    raw = row.skills if isinstance(row.skills, (list, dict, str)) else []
    raw_data = row.raw_data if isinstance(row.raw_data, dict) else {}
    parsed = row.parsed_resume_json if isinstance(row.parsed_resume_json, dict) else {}
    values = [raw, raw_data.get("skills"), parsed.get("skills")]
    result = set()
    for value in values:
        result.update(_tokens(value))
    return sorted(result)


def _candidate_skill_tokens(row: CandidateProfileEntity, job_tokens: set[str]) -> set[str]:
    structured = set(_candidate_skills(row))
    resume_words = {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{1,40}", _text(row.resume_text))
    }
    # Resume text can confirm a required skill, but it does not turn every word
    # in a resume into an exposed candidate skill.
    return structured.union(resume_words.intersection(job_tokens))


def _parse_years(value: Any) -> float:
    """Safely extract a float year count from numeric or string values.

    Examples: 14 -> 14.0 | "14 years" -> 14.0 | "6+ years" -> 6.0 | None -> 0.0
    """
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = re.sub(r"[^0-9.]", " ", str(value))
    for token in text.split():
        try:
            return max(0.0, float(token))
        except ValueError:
            continue
    return 0.0


def _candidate_years(row: CandidateProfileEntity) -> tuple[float, str]:
    """Return (years, source_label) using the full key priority order."""
    col = getattr(row, "total_experience_years", None)
    if col is not None and float(col) > 0:
        return max(0.0, float(col)), "candidate.total_experience_years"
    raw = row.raw_data if isinstance(row.raw_data, dict) else {}
    parsed = row.parsed_resume_json if isinstance(row.parsed_resume_json, dict) else {}
    profile_data = raw.get("profileData") if isinstance(raw.get("profileData"), dict) else {}
    explanation = raw.get("explanation") if isinstance(raw.get("explanation"), dict) else {}
    checks: list[tuple[Any, str]] = [
        (raw.get("yearsExperience"),                          "raw_data.yearsExperience"),
        (raw.get("years_experience"),                         "raw_data.years_experience"),
        (raw.get("experience_years"),                         "raw_data.experience_years"),
        (profile_data.get("inferred_experience"),             "raw_data.profileData.inferred_experience"),
        (raw.get("inferredExperience"),                       "raw_data.inferredExperience"),
        (explanation.get("candidateExperience"),              "raw_data.explanation.candidateExperience"),
        (parsed.get("years_experience"),                      "parsed_resume_json.years_experience"),
        (parsed.get("experience_years"),                      "parsed_resume_json.experience_years"),
    ]
    for value, source in checks:
        years = _parse_years(value)
        if years > 0:
            return years, source
    return 0.0, "none"


def _experience_match(candidate_years: float, required: str) -> float:
    try:
        required_years = float(parse_experience(required or ""))
    except (TypeError, ValueError):
        required_years = 0.0
    if required_years <= 0:
        return 0.5
    if candidate_years >= required_years:
        return 1.0
    return max(0.0, candidate_years / required_years)


def _location_match(job: Any, row: CandidateProfileEntity) -> float:
    structured = getattr(job, "structured_data", {}) if isinstance(getattr(job, "structured_data", {}), dict) else {}
    job_location = _text(structured.get("location") or getattr(job, "location", "")).lower()
    remote_policy = _text(structured.get("remotePolicy") or getattr(job, "remote_policy", "")).lower()
    candidate_location = _text(getattr(row, "location", "")).lower()
    if not job_location or "remote" in remote_policy or "remote" in job_location:
        return 1.0
    if not candidate_location:
        return 0.5
    return 1.0 if job_location in candidate_location or candidate_location in job_location else 0.0


def _role_match(job: Any, row: CandidateProfileEntity) -> float:
    job_tokens = _tokens(_job_role(job))
    candidate_tokens = _tokens(getattr(row, "current_role", ""))
    if not job_tokens or not candidate_tokens:
        return 0.5
    return len(job_tokens.intersection(candidate_tokens)) / len(job_tokens)


def _candidate_result(row: CandidateProfileEntity, item: dict[str, Any]) -> CandidateResult:
    semantic = float(item["semantic_similarity"])
    score = float(item["match_score"])
    explanation = CandidateExplanation(
        semanticScore=semantic,
        skillOverlap=float(item["skill_match"]),
        finalScore=score,
        pdlRelevance=0.0,
        recencyScore=0.0,
        engineeringScore=score,
        penalties={},
        skillsMatched=list(item["matched_requirements"]),
        missingSkills=list(item["missing_requirements"]),
        matchedRequirements=list(item["matched_requirements"]),
        missingRequirements=list(item["missing_requirements"]),
        experienceMatch=f"{float(item['experience_match']):.2f}",
        locationMatch=float(item["location_match"]),
        roleMatch=float(item["role_match"]),
        candidateExperience=f"{item['candidate_years']:g} years",
        jobExperience=item["job_experience"],
        retrievalAttribution={"source": "internal", "embeddingVersion": item["embedding_version"]},
        sourceBreakdown={"semanticSimilarity": semantic, "skillMatch": item["skill_match"], "experienceMatch": item["experience_match"], "locationMatch": item["location_match"]},
    )
    return CandidateResult(
        id=_text(row.candidate_id or row.id),
        name=_text(row.name or row.candidate_id or row.id),
        role=_text(row.current_role),
        company=_text(row.current_company),
        # Internal review is recruiter-safe; contact data is unlocked later.
        email=None,
        headline=_text(row.current_role),
        location=_text(row.location),
        yearsExperience=item["candidate_years"],
        skills=list(item["candidate_skills"]),
        summary=_text(row.summary),
        education=row.education if isinstance(row.education, list) else [],
        projects=[],
        certifications=[],
        companiesHistory=[_text(row.current_company)] if _text(row.current_company) else [],
        domainExperience=[],
        resumeText=None,
        profileData={"candidateRecordId": str(row.id), "agencyId": str(row.agency_id or "")},
        fitScore=round(score * 5.0, 4),
        decision="review",
        explanation=explanation,
        strategy="internal_semantic_match",
        status="reviewed",
        sourceProvider="internal",
        sourceType="internal",
        source="internal",
        currentCompany=_text(row.current_company),
        snippetQuality="rich" if row.resume_text else "partial",
        rawDiscovery={"semanticSimilarity": semantic, "matchScore": score, "matchedRequirements": item["matched_requirements"], "missingRequirements": item["missing_requirements"]},
    )


def match_internal_candidates_for_job(*, db: Session, job_id: str, agency_id: str, limit: int | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    if not _text(agency_id):
        raise APIError("agency_id is required for candidate matching", status_code=400, code="missing_agency_id", retryable=False)
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    if _text(getattr(job, "agency_id", "")) != _text(agency_id):
        raise APIError("Forbidden", status_code=403)
    job_text = build_job_text(job)
    logger.error(
        "[MATCH_DEBUG] job_text_length=%s",
        len(job_text),
    )
    logger.error(
        "[MATCH_DEBUG] job_text_preview=%s",
        job_text[:1500],
    )
    if not job_text.strip():
        raise APIError("Job requirements are incomplete", status_code=409)

    # DIAG-1: job context
    logger.info(
        "[DIAG] match_start job_id=%s agency_id=%s job_text_length=%s embedding_version=%s collection=%s top_k=%s threshold=%.4f",
        job_id, agency_id, len(job_text), EMBEDDING_VERSION,
        INTERNAL_CANDIDATE_COLLECTION_NAME, INTERNAL_CANDIDATE_RETRIEVAL_TOP_K, INTERNAL_CANDIDATE_MATCH_THRESHOLD,
    )

    try:
        query_vector = get_embedding(job_text)
        logger.info("[DIAG] embedding_ok job_id=%s vector_length=%s", job_id, len(query_vector))
        hits = search_internal_candidate_chunks(
            query_vector=query_vector,
            limit=max(1, INTERNAL_CANDIDATE_RETRIEVAL_TOP_K),
            metadata_filters={"embeddingVersion": EMBEDDING_VERSION},
            raise_on_unavailable=True,
            allow_unfiltered_fallback=False,
        )
    except QdrantUnavailableError as exc:
        raise APIError("Internal candidate search is unavailable", status_code=503, code="internal_search_unavailable", retryable=True) from exc

    logger.error(
        "[MATCH_DEBUG] job_id=%s qdrant_hits=%s",
        job_id,
        len(hits),
    )
    if hits:
        logger.error(
            "[MATCH_DEBUG] first_hit_payload=%s score=%s",
            hits[0].get("payload"),
            hits[0].get("score"),
        )

    collection_points = count_collection_points(INTERNAL_CANDIDATE_COLLECTION_NAME)
    # DIAG-2: Qdrant results
    logger.info(
        "[DIAG] qdrant_results job_id=%s collection_total_points=%s hits_returned=%s metadata_filter={embeddingVersion: %s}",
        job_id, collection_points, len(hits), EMBEDDING_VERSION,
    )
    if hits:
        top_scores = [round(float(h.get("score") or 0.0), 4) for h in hits[:5]]
        sample_payloads = [{k: v for k, v in (h.get("payload") or {}).items() if k in ("candidateRecordId", "embeddingVersion", "agencyId", "sourceType")} for h in hits[:3]]
        logger.info("[DIAG] qdrant_top_scores job_id=%s scores=%s", job_id, top_scores)
        logger.info("[DIAG] qdrant_sample_payloads job_id=%s payloads=%s", job_id, sample_payloads)
    else:
        logger.info("[DIAG] qdrant_zero_hits job_id=%s collection_points=%s", job_id, collection_points)

    if not hits and collection_points == 0:
        return {
            "status": "index_not_ready", "source": "internal", "candidates": [],
            "qualified_count": 0, "retrieval_count": 0,
            "semantic_top_k": INTERNAL_CANDIDATE_RETRIEVAL_TOP_K,
            "threshold": INTERNAL_CANDIDATE_MATCH_THRESHOLD,
            "minimum_internal_matches": INTERNAL_CANDIDATE_MIN_MATCHES,
            "fallback_eligible": False, "fallback_reason": "internal_index_not_ready",
            "matching_duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    record_ids = list(dict.fromkeys(_text((hit.get("payload") or {}).get("candidateRecordId")) for hit in hits if _text((hit.get("payload") or {}).get("candidateRecordId"))))
    # DIAG-3: candidateRecordId extraction
    missing_record_id_count = sum(1 for hit in hits if not _text((hit.get("payload") or {}).get("candidateRecordId")))
    logger.info(
        "[DIAG] record_id_extraction job_id=%s hits=%s record_ids_extracted=%s missing_record_id=%s",
        job_id, len(hits), len(record_ids), missing_record_id_count,
    )
    logger.error(
        "[MATCH_DEBUG] record_ids=%s extracted=%s",
        len(record_ids),
        record_ids[:5],
    )

    rows = db.scalars(select(CandidateProfileEntity).where(CandidateProfileEntity.id.in_(record_ids))).all() if record_ids else []
    # DIAG-4: PostgreSQL lookup
    logger.info(
        "[DIAG] pg_lookup job_id=%s record_ids_queried=%s pg_rows_found=%s",
        job_id, len(record_ids), len(rows),
    )
    if record_ids and not rows:
        logger.info("[DIAG] pg_lookup_zero_rows job_id=%s sample_record_ids=%s", job_id, record_ids[:3])
    logger.error(
        "[MATCH_DEBUG] postgres_rows_found=%s",
        len(rows),
    )

    row_by_id = {str(row.id): row for row in rows}
    job_skills = _job_skills(job)
    job_tokens = _tokens(job_skills)
    job_experience = _job_experience(job)
    weights = INTERNAL_CANDIDATE_MATCH_WEIGHTS
    scored: list[tuple[CandidateProfileEntity, dict[str, Any]]] = []
    dropped_no_row = dropped_status = dropped_version = 0
    logger.error(
        "[MATCH_DEBUG] starting_candidate_filtering hits=%s",
        len(hits),
    )
    for hit in hits:
        payload = hit.get("payload") or {}
        row = row_by_id.get(_text(payload.get("candidateRecordId")))
        if not row:
            dropped_no_row += 1
            continue
        if getattr(row, "embedding_status", None) != "EMBEDDED":
            dropped_status += 1
            logger.info("[DIAG] dropped_status job_id=%s record_id=%s embedding_status=%s", job_id, row.id, row.embedding_status)
            continue
        if getattr(row, "embedding_version", None) != EMBEDDING_VERSION:
            dropped_version += 1
            logger.info("[DIAG] dropped_version job_id=%s record_id=%s row_version=%s expected=%s", job_id, row.id, row.embedding_version, EMBEDDING_VERSION)
            continue
        candidate_skills = _candidate_skills(row)
        candidate_skill_tokens = _candidate_skill_tokens(row, job_tokens)
        matched = sorted(job_tokens.intersection(candidate_skill_tokens))
        skill_match = len(matched) / len(job_tokens) if job_tokens else 0.5
        candidate_years, experience_source = _candidate_years(row)
        experience_match = _experience_match(candidate_years, job_experience)
        location_match = _location_match(job, row)
        role_match = _role_match(job, row)
        semantic = max(0.0, min(1.0, float(hit.get("score") or 0.0)))
        weight_sum = sum(max(0.0, float(weights.get(key, 0.0))) for key in ("semantic_similarity", "skill_match", "experience_match")) or 1.0
        base_score = (
            max(0.0, float(weights.get("semantic_similarity", 0.7))) * semantic
            + max(0.0, float(weights.get("skill_match", 0.2))) * skill_match
            + max(0.0, float(weights.get("experience_match", 0.1))) * experience_match
        ) / weight_sum
        final_score = max(0.0, min(1.0, base_score * (0.85 + (0.15 * location_match)) * (0.90 + (0.10 * role_match))))
        item = {
            "candidate_id": _text(row.candidate_id or row.id), "candidate_record_id": str(row.id),
            "semantic_similarity": round(semantic, 4), "skill_match": round(skill_match, 4),
            "experience_match": round(experience_match, 4), "location_match": round(location_match, 4),
            "role_match": round(role_match, 4),
            "match_score": round(final_score, 4), "candidate_years": candidate_years,
            "candidate_skills": candidate_skills, "matched_requirements": matched,
            "missing_requirements": sorted(job_tokens.difference(candidate_skill_tokens)),
            "job_experience": job_experience, "embedding_version": _text(payload.get("embeddingVersion")) or EMBEDDING_VERSION,
            "experience_source": experience_source,
        }
        scored.append((row, item))

    logger.error(
        "[MATCH_DEBUG] scored_candidates=%s",
        len(scored),
    )
    scored.sort(key=lambda pair: pair[1]["match_score"], reverse=True)
    score_values = [float(item["match_score"]) for _, item in scored]
    logger.error(
        "[MATCH_SCORE_DEBUG] highest_score=%s lowest_score=%s average_score=%s",
        max(score_values) if score_values else 0.0,
        min(score_values) if score_values else 0.0,
        (sum(score_values) / len(score_values)) if score_values else 0.0,
    )
    for row, item in scored[:20]:
        logger.error(
            "[MATCH_SCORE_DEBUG]\n"
            "candidate_id=%s\n"
            "candidate_name=%s\n"
            "semantic_similarity=%s\n"
            "skill_match=%s\n"
            "experience_match=%s\n"
            "location_match=%s\n"
            "role_match=%s\n"
            "final_match_score=%s",
            item["candidate_id"],
            _text(row.name or row.candidate_id or row.id),
            item["semantic_similarity"],
            item["skill_match"],
            item["experience_match"],
            item["location_match"],
            item["role_match"],
            item["match_score"],
        )
        logger.error(
            "[EXPERIENCE_FIXED_DEBUG]\n"
            "candidate_id=%s\n"
            "candidate_name=%s\n"
            "experience_source=%s\n"
            "candidate_years=%s\n"
            "job_required_years=%s\n"
            "experience_match=%s",
            item["candidate_id"],
            _text(row.name or row.candidate_id or row.id),
            item["experience_source"],
            item["candidate_years"],
            item["job_experience"],
            item["experience_match"],
        )
    # ── DIAG: Pre-qualification summary ─────────────────────────────────────
    top20_scores = [round(float(item["match_score"]), 4) for _, item in scored[:20]]
    logger.error(
        "[DIAG_QUALIFY] PRE-QUALIFICATION job_id=%s total_scored=%s threshold=%.4f top20_scores=%s",
        job_id,
        len(scored),
        INTERNAL_CANDIDATE_MATCH_THRESHOLD,
        top20_scores,
    )
    qualified = [(row, item) for row, item in scored if item["match_score"] >= INTERNAL_CANDIDATE_MATCH_THRESHOLD]
    resolved_limit = max(1, min(int(limit or INTERNAL_CANDIDATE_MATCH_LIMIT), INTERNAL_CANDIDATE_MATCH_LIMIT))
    results = [_candidate_result(row, item) for row, item in qualified[:resolved_limit]]
    qualified_count = len(qualified)
    # ── DIAG: Post-qualification summary ─────────────────────────────────────
    logger.error(
        "[DIAG_QUALIFY] POST-QUALIFICATION job_id=%s qualified_count=%s threshold=%.4f results_to_return=%s",
        job_id,
        qualified_count,
        INTERNAL_CANDIDATE_MATCH_THRESHOLD,
        len(results),
    )
    logger.error(
        "[MATCH_DEBUG] qualified_candidates=%s threshold=%s",
        qualified_count,
        INTERNAL_CANDIDATE_MATCH_THRESHOLD,
    )
    fallback_eligible = qualified_count == 0
    fallback_reason = "insufficient_internal_candidates" if fallback_eligible else "internal_candidates_sufficient"
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    # ── DIAG: Persistence stage ───────────────────────────────────────────────────
    # NOTE: internal_candidate_semantic_service does NOT write to the candidates
    # table — it reads from it. Persistence happens upstream in candidate_service.py
    # (profile_repo.upsert) during X-Ray sourcing. The candidates table is the
    # SOURCE for this service, not the destination.
    # What we CAN log here is how many CandidateResult objects are being returned
    # to the API layer, and their candidate_ids.
    persisted_candidate_ids = [_text(row.candidate_id or row.id) for row, _ in qualified[:resolved_limit]]
    logger.error(
        "[DIAG_PERSIST] job_id=%s candidates_being_returned=%s candidate_ids=%s",
        job_id,
        len(results),
        persisted_candidate_ids,
    )
    # DIAG-5: final funnel summary
    logger.info(
        "[DIAG] funnel_summary job_id=%s qdrant_hits=%s dropped_no_pg_row=%s dropped_bad_status=%s dropped_bad_version=%s scored=%s below_threshold=%s qualified=%s final=%s threshold=%.4f duration_ms=%.2f",
        job_id, len(hits), dropped_no_row, dropped_status, dropped_version,
        len(scored), len(scored) - qualified_count, qualified_count, len(results),
        INTERNAL_CANDIDATE_MATCH_THRESHOLD, duration_ms,
    )
    logger.info(
        "internal_candidate_matching job_id=%s retrieval_count=%s qualified_count=%s top_k=%s threshold=%.4f fallback_eligible=%s reason=%s duration_ms=%.2f",
        job_id, len(hits), qualified_count, INTERNAL_CANDIDATE_RETRIEVAL_TOP_K, INTERNAL_CANDIDATE_MATCH_THRESHOLD, fallback_eligible, fallback_reason, duration_ms,
    )
    logger.error(
        "[MATCH_DEBUG] summary qdrant_hits=%s rows=%s scored=%s qualified=%s final=%s",
        len(hits),
        len(rows),
        len(scored),
        qualified_count,
        len(results),
    )
    return {
        "status": "ok",
        "source": "internal",
        "candidates": results,
        "qualified_count": qualified_count,
        "retrieval_count": len(hits),
        "semantic_top_k": INTERNAL_CANDIDATE_RETRIEVAL_TOP_K,
        "threshold": INTERNAL_CANDIDATE_MATCH_THRESHOLD,
        "minimum_internal_matches": INTERNAL_CANDIDATE_MIN_MATCHES,
        "fallback_eligible": fallback_eligible,
        "fallback_reason": fallback_reason,
        "matching_duration_ms": duration_ms,
    }
