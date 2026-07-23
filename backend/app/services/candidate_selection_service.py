from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.repositories import (
    CandidateFeedbackRepository,
    CandidateSelectionSessionRepository,
    CandidateProfileRepository,
    InterviewRepository,
    JobRepository,
    ScoringProfileRepository,
)
from app.schemas.candidate import CandidateExplanation, CandidateResult
from app.services.candidate_service import fetch_ranked_candidates
from app.services.ats_lifecycle_service import transition_candidate_ats_state
from app.services.reuse_interview_service import check_and_reuse_interview, _REUSABLE_STATUSES
from app.services.job_queue_service import enqueue_job
from app.services.lifecycle_service import record_job_lifecycle_event
from app.services.recruiter_preference_service import update_recruiter_preferences
from app.services.recruiter_preference_round_service import (
    finalize_preference_session,
    get_preference_session,
)
from app.services.ranking.models import coerce_candidate_explanation, ranked_candidate_sort_key
from app.services.skill_normalizer import normalize_skills, parse_experience
from app.services.state_machine import assert_valid_transition, is_swipe_locked
from app.services.identity.candidate_identity_service import normalize_linkedin_url
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)
DEFAULT_BATCH_SIZE = 20
DEFAULT_TOTAL_BATCHES = 1
DEFAULT_SELECTION_LIMIT = DEFAULT_BATCH_SIZE * DEFAULT_TOTAL_BATCHES
DEFAULT_FINAL_LIMITS = {
    "volume": 20,
    "elite": 15,
}


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _normalize_linkedin_profile_url(*values: Any) -> str:
    for value in values:
        normalized = normalize_linkedin_url(value)
        if normalized:
            return normalized
    return ""


def _job_mode(job: Any) -> str:
    value = _normalize_text(getattr(job, "vetting_mode", "") or getattr(job, "vettingMode", "") or "volume").lower()
    return value if value in {"volume", "elite"} else "volume"


def _final_shortlist_limit(job: Any) -> int:
    return DEFAULT_FINAL_LIMITS.get(_job_mode(job), DEFAULT_FINAL_LIMITS["volume"])


def _selection_limit(job: Any) -> int:
    return _final_shortlist_limit(job)


def _candidate_display_name(candidate: Any | None) -> str:
    if candidate is None:
        return ""
    for key in ("name", "full_name", "fullName", "candidate_name", "candidateName"):
        value = getattr(candidate, key, None)
        if isinstance(value, str) and _normalize_text(value):
            return _normalize_text(value)
        if isinstance(candidate, dict):
            value = candidate.get(key)
            if isinstance(value, str) and _normalize_text(value):
                return _normalize_text(value)
    profile_data = getattr(candidate, "profileData", None)
    if isinstance(profile_data, dict):
        for key in ("full_name", "fullName", "name", "candidate_name", "candidateName"):
            value = profile_data.get(key)
            if isinstance(value, str) and _normalize_text(value):
                return _normalize_text(value)
    raw_data = getattr(candidate, "raw_data", None)
    if isinstance(raw_data, dict):
        for key in ("full_name", "fullName", "name", "candidate_name", "candidateName"):
            value = raw_data.get(key)
            if isinstance(value, str) and _normalize_text(value):
                return _normalize_text(value)
    candidate_id = _normalize_text(getattr(candidate, "id", "") or getattr(candidate, "candidate_id", "") or "")
    return candidate_id or "Unnamed candidate"


def _tokenize(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-zA-Z0-9\.\+#]+", value.lower()) if len(token) > 1]


def _candidate_skill_tokens(candidate: CandidateResult) -> set[str]:
    return normalize_skills(candidate.skills or [])


def _candidate_role_tokens(candidate: CandidateResult) -> set[str]:
    return {token for token in _tokenize(candidate.role or "") if token}


def _candidate_company_tokens(candidate: CandidateResult) -> set[str]:
    return {token for token in _tokenize(candidate.company or "") if token}


def _candidate_experience_years(candidate: CandidateResult) -> int:
    explanation = candidate.explanation
    years_text = _normalize_text(getattr(explanation, "candidateExperience", "") or "")
    if years_text:
        return parse_experience(years_text)
    return parse_experience(candidate.summary or "")


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    union = left.union(right)
    if not union:
        return 0.0
    return len(left.intersection(right)) / len(union)


def _subset_similarity(candidate: CandidateResult, selected: list[CandidateResult]) -> float:
    if not selected:
        return 0.0

    candidate_skills = _candidate_skill_tokens(candidate)
    candidate_roles = _candidate_role_tokens(candidate)
    candidate_company = _candidate_company_tokens(candidate)
    candidate_exp = _candidate_experience_years(candidate)

    total = 0.0
    for other in selected:
        skill_similarity = _jaccard(candidate_skills, _candidate_skill_tokens(other))
        role_similarity = _jaccard(candidate_roles, _candidate_role_tokens(other))
        company_similarity = _jaccard(candidate_company, _candidate_company_tokens(other))
        experience_distance = abs(candidate_exp - _candidate_experience_years(other))
        experience_similarity = max(0.0, 1.0 - min(experience_distance, 10) / 10.0)
        total += (skill_similarity * 0.45) + (role_similarity * 0.25) + (company_similarity * 0.15) + (experience_similarity * 0.15)

    return total / len(selected)


def _select_diverse_subset(candidates: list[CandidateResult], *, limit: int) -> list[CandidateResult]:
    ordered = sorted(candidates, key=lambda candidate: (-float(candidate.fitScore or 0.0), candidate.name or candidate.id))
    if not ordered:
        return []

    selected: list[CandidateResult] = [ordered.pop(0)]
    while ordered and len(selected) < limit:
        best_index = 0
        best_score = None
        for index, candidate in enumerate(ordered):
            diversity = 1.0 - _subset_similarity(candidate, selected)
            quality = max(0.0, min(1.0, float(candidate.fitScore or 0.0) / 5.0))
            score = (quality * 0.7) + (diversity * 0.3)
            if best_score is None or score > best_score:
                best_score = score
                best_index = index
        selected.append(ordered.pop(best_index))
    return selected[:limit]


def _build_batch_plan(candidates: list[CandidateResult]) -> list[list[str]]:
    if not candidates:
        raise APIError("No X-Ray candidates available for review", status_code=404)

    highest_matches = sorted(candidates, key=lambda candidate: (-float(candidate.fitScore or 0.0), candidate.name or candidate.id))
    limit = min(DEFAULT_SELECTION_LIMIT, len(highest_matches))
    return [[candidate.id for candidate in highest_matches[:limit]]]


def _candidate_lookup_snapshot(snapshot: list[dict[str, Any]]) -> dict[str, CandidateResult]:
    lookup: dict[str, CandidateResult] = {}
    for row in snapshot:
        try:
            candidate = CandidateResult.model_validate(row)
        except Exception:
            continue
        lookup[candidate.id] = candidate
    return lookup


def _candidate_without_resume(candidate: CandidateResult) -> CandidateResult:
    profile_data = dict(candidate.profileData or {})
    for key in ("resumeText", "resume_text", "rawResumeText", "raw_resume_text"):
        profile_data.pop(key, None)
    return candidate.model_copy(update={"resumeText": "", "profileData": profile_data})


def _is_external_candidate(candidate: CandidateResult) -> bool:
    reasoning = _normalize_text(getattr(candidate.explanation, "aiReasoning", "")).lower()
    if bool(candidate.isMockEmail) or _normalize_text(candidate.resumeText):
        return False
    if "local source" in reasoning or "local profile" in reasoning:
        return False
    return True


def _selection_candidate_pool(*, db: Session, job: Any) -> list[CandidateResult]:
    candidates = fetch_ranked_candidates(db=db, job_id=job.id, mode=_job_mode(job), refresh=False)
    external_candidates = [
        _candidate_without_resume(candidate)
        for candidate in candidates
        if _is_external_candidate(candidate)
    ]
    if external_candidates:
        limit = _selection_limit(job)
        highest_matches = sorted(external_candidates, key=lambda candidate: (-float(candidate.fitScore or 0.0), candidate.name or candidate.id))
        return highest_matches[:limit]
    if candidates:
        logger.warning(
            "selection_external_candidates_missing job_id=%s falling_back_to_ranked_pool count=%s",
            job.id,
            len(candidates),
        )
        limit = _selection_limit(job)
        highest_matches = sorted(candidates, key=lambda candidate: (-float(candidate.fitScore or 0.0), candidate.name or candidate.id))
        return highest_matches[:limit]
    profile_repo = CandidateProfileRepository(db)
    stored_profiles: list[CandidateResult] = []
    for row in profile_repo.list_for_job(job.id):
        raw_data = row.raw_data if isinstance(row.raw_data, dict) else {}
        try:
            candidate = CandidateResult.model_validate(raw_data)
        except Exception:
            continue
        if _is_external_candidate(candidate):
            stored_profiles.append(_candidate_without_resume(candidate))
    if stored_profiles:
        limit = _selection_limit(job)
        highest_matches = sorted(stored_profiles, key=lambda candidate: (-float(candidate.fitScore or 0.0), candidate.name or candidate.id))
        logger.info(
            "selection_candidate_pool_recovered_from_profiles job_id=%s count=%s",
            job.id,
            len(highest_matches),
        )
        return highest_matches[:limit]
    raise APIError("No X-Ray candidates available for review", status_code=404)


def _sync_selection_session_to_candidate_set(session, candidates: list[CandidateResult]) -> None:
    candidate_ids = [candidate.id for candidate in candidates]
    session.candidate_pool_snapshot = [candidate.model_dump() for candidate in candidates]
    session.batch_plan = [candidate_ids]
    session.batch_size = len(candidate_ids)
    session.total_batches = DEFAULT_TOTAL_BATCHES
    session.current_batch_index = 0
    session.final_candidate_snapshot = []
    if (session.status or "").strip().lower() == "completed":
        session.status = "active"


def _current_batch_from_session(session, snapshot_lookup: dict[str, CandidateResult]) -> list[CandidateResult]:
    batch_plan = list(session.batch_plan or [])
    batch_index = max(0, int(session.current_batch_index or 0))
    if batch_index >= len(batch_plan):
        return []
    return [snapshot_lookup[candidate_id] for candidate_id in batch_plan[batch_index] if candidate_id in snapshot_lookup]


def _selection_session_payload(session, state: dict[str, Any], current_batch: list[CandidateResult], final_candidates: list[CandidateResult] | None = None) -> dict[str, Any]:
    payload = _session_payload(session=session, current_batch=current_batch, final_candidates=final_candidates)
    payload.update(
        {
            "stage": state.get("stage", "initial_job_understanding"),
            "recommendedQuestions": list(state.get("recommended_questions") or []),
            "gapAnalysis": state.get("gap_analysis") or {},
            "intentProfile": state.get("intent_profile") or {},
            "telemetry": state.get("telemetry") or {},
            "currentPair": {},
            "pairExplanation": {},
            "voiceSummary": state.get("voice_summary", ""),
        }
    )
    return payload


def _best_effort_next_batch(session, snapshot_lookup: dict[str, CandidateResult]) -> list[CandidateResult]:
    next_batch = _current_batch_from_session(session, snapshot_lookup)
    if next_batch:
        return next_batch

    batch_plan = list(session.batch_plan or [])
    batch_index = max(0, int(session.current_batch_index or 0))
    if batch_index < len(batch_plan):
        return [snapshot_lookup[candidate_id] for candidate_id in batch_plan[batch_index] if candidate_id in snapshot_lookup]
    return []


def _best_effort_final_candidates(
    *,
    db: Session,
    job,
    updated_session,
    selected_rows: list[CandidateResult],
    analysis: dict[str, Any],
) -> list[CandidateResult]:
    final_limit = _final_shortlist_limit(job)
    try:
        real_candidates = _selection_candidate_pool(db=db, job=job)
    except Exception as exc:
        logger.warning(
            "selection_final_rank_fetch_failed job_id=%s error=%s",
            updated_session.job_id,
            str(exc),
        )
        real_candidates = []

    pool = real_candidates or selected_rows or [CandidateResult.model_validate(row) for row in (updated_session.candidate_pool_snapshot or [])]
    try:
        return _rerank_with_selection_signals(
            pool_candidates=pool,
            selected_candidates=selected_rows,
            analysis=analysis,
        )
    except Exception as exc:
        logger.warning(
            "selection_final_rerank_failed job_id=%s error=%s",
            updated_session.job_id,
            str(exc),
        )
        return pool[:final_limit]


def _build_selection_analysis(selected_candidates: list[CandidateResult]) -> dict[str, Any]:
    skill_counter: Counter[str] = Counter()
    role_counter: Counter[str] = Counter()
    company_counter: Counter[str] = Counter()
    experience_years: list[int] = []

    for candidate in selected_candidates:
        skill_counter.update(token.lower() for token in candidate.skills if _normalize_text(token))
        role_counter.update(_tokenize(candidate.role or ""))
        company_counter.update(_tokenize(candidate.company or ""))
        experience_years.append(_candidate_experience_years(candidate))

    top_skills = [{"skill": skill, "count": count} for skill, count in skill_counter.most_common(8)]
    top_roles = [{"role": role, "count": count} for role, count in role_counter.most_common(8)]
    top_companies = [{"company": company, "count": count} for company, count in company_counter.most_common(8)]
    avg_experience = round(sum(experience_years) / len(experience_years), 2) if experience_years else 0.0
    min_experience = min(experience_years) if experience_years else 0
    max_experience = max(experience_years) if experience_years else 0
    shared_skills = [item["skill"] for item in top_skills[:5]]
    shared_roles = [item["role"] for item in top_roles[:5]]
    shared_companies = [item["company"] for item in top_companies[:5]]

    summary_parts = []
    if shared_skills:
        summary_parts.append(f"Skills recurring across selections: {', '.join(shared_skills)}")
    if shared_roles:
        summary_parts.append(f"Role/title signals: {', '.join(shared_roles)}")
    if shared_companies:
        summary_parts.append(f"Company background overlap: {', '.join(shared_companies)}")
    if experience_years:
        summary_parts.append(f"Experience trend: {avg_experience:.1f} years on average")

    return {
        "skillsOverlap": top_skills,
        "experienceTrends": {
            "averageYears": avg_experience,
            "minimumYears": min_experience,
            "maximumYears": max_experience,
            "sampleSize": len(experience_years),
        },
        "companySimilarities": {
            "topCompanies": top_companies,
        },
        "roleAlignment": {
            "topRoles": top_roles,
        },
        "preferenceSignals": {
            "sharedSkills": shared_skills,
            "sharedRoles": shared_roles,
            "sharedCompanies": shared_companies,
        },
        "summary": ". ".join(summary_parts) if summary_parts else "Selection preferences recorded from recruiter choices.",
    }


def _rerank_with_selection_signals(
    *,
    pool_candidates: list[CandidateResult],
    selected_candidates: list[CandidateResult],
    analysis: dict[str, Any],
) -> list[CandidateResult]:
    selected_skill_counter: Counter[str] = Counter()
    selected_role_tokens: Counter[str] = Counter()
    selected_company_tokens: Counter[str] = Counter()
    selected_experience_years: list[int] = []
    selected_ids = {candidate.id for candidate in selected_candidates}

    for candidate in selected_candidates:
        selected_skill_counter.update(token.lower() for token in candidate.skills if _normalize_text(token))
        selected_role_tokens.update(_tokenize(candidate.role or ""))
        selected_company_tokens.update(_tokenize(candidate.company or ""))
        selected_experience_years.append(_candidate_experience_years(candidate))

    max_skill_count = max(1, sum(selected_skill_counter.values()))
    average_selected_experience = sum(selected_experience_years) / len(selected_experience_years) if selected_experience_years else 0.0
    top_selected_roles = {token for token, _ in selected_role_tokens.most_common(10)}
    top_selected_companies = {token for token, _ in selected_company_tokens.most_common(10)}

    reranked: list[CandidateResult] = []
    for candidate in pool_candidates:
        base_score = max(0.0, min(1.0, float(candidate.fitScore or 0.0) / 5.0))
        candidate_skill_tokens = [token.lower() for token in candidate.skills if _normalize_text(token)]
        candidate_skill_score = sum(selected_skill_counter.get(token, 0) for token in candidate_skill_tokens) / max_skill_count
        candidate_skill_score = max(0.0, min(1.0, candidate_skill_score))

        candidate_role_tokens = _candidate_role_tokens(candidate)
        role_score = 0.0
        if candidate_role_tokens and top_selected_roles:
            role_score = len(candidate_role_tokens.intersection(top_selected_roles)) / max(1, len(candidate_role_tokens.union(top_selected_roles)))

        candidate_company_tokens = _candidate_company_tokens(candidate)
        company_score = 0.0
        if candidate_company_tokens and top_selected_companies:
            company_score = len(candidate_company_tokens.intersection(top_selected_companies)) / max(
                1, len(candidate_company_tokens.union(top_selected_companies))
            )

        candidate_experience = _candidate_experience_years(candidate)
        if average_selected_experience > 0:
            experience_score = max(0.0, 1.0 - min(abs(candidate_experience - average_selected_experience), 10.0) / 10.0)
        else:
            experience_score = 0.5

        preference_signal = (
            (candidate_skill_score * 0.5)
            + (role_score * 0.2)
            + (company_score * 0.1)
            + (experience_score * 0.2)
        )
        selected_boost = 0.06 if candidate.id in selected_ids else 0.0
        final_score = max(0.0, min(1.0, (base_score * 0.55) + (preference_signal * 0.4) + selected_boost))

        candidate_copy = candidate.model_copy(deep=True)
        explanation = coerce_candidate_explanation(candidate_copy.explanation) if candidate_copy.explanation else CandidateExplanation(
            semanticScore=0.0,
            skillOverlap=0.0,
            finalScore=final_score,
            pdlRelevance=0.0,
            recencyScore=0.0,
            penalties={},
        )
        setattr(explanation, "finalScore", round(final_score, 4))
        explanation.aiReasoning = analysis.get("summary", "")
        explanation.penalties = dict(explanation.penalties or {})
        explanation.penalties["selectionPreferenceBonus"] = round(selected_boost, 4)
        explanation.penalties["skillPreferenceSignal"] = round(candidate_skill_score, 4)
        explanation.penalties["roleAlignmentSignal"] = round(role_score, 4)
        explanation.penalties["companyAlignmentSignal"] = round(company_score, 4)
        explanation.penalties["experienceAlignmentSignal"] = round(experience_score, 4)
        candidate_copy.explanation = explanation
        candidate_copy.fitScore = round(final_score * 5.0, 2)
        candidate_copy.decision = "strong_match" if final_score >= 0.75 else "potential" if final_score >= 0.45 else "weak"
        candidate_copy.strategy = "HIGH" if candidate_copy.fitScore >= 4 else "MEDIUM" if candidate_copy.fitScore >= 2.5 else "LOW"
        reranked.append(candidate_copy)

    reranked.sort(key=ranked_candidate_sort_key)
    return reranked


def _store_selection_feedback(
    db: Session,
    *,
    job_id: str,
    session_id: str | None,
    selected_candidate_id: str,
    rejected_candidate_ids: list[str],
) -> None:
    interview_repo = InterviewRepository(db)
    feedback_repo = CandidateFeedbackRepository(db)
    scoring_repo = ScoringProfileRepository(db)
    recruiter_id = JobRepository(db).get_recruiter_id(job_id)

    selected_row = interview_repo.get_by_job_and_candidate(job_id, selected_candidate_id)
    selected_status = (selected_row.status if selected_row else "new") or "new"
    if selected_status not in {"selected", "rejected"} and is_swipe_locked(selected_status):
        raise APIError(f"Cannot select candidate in '{selected_status}' state.", status_code=409)

    if selected_status != "selected":
        assert_valid_transition(
            candidate_id=selected_candidate_id,
            job_id=job_id,
            from_status=selected_status,
            to_status="selected",
        )
    feedback_repo.upsert(
        job_id=job_id,
        candidate_id=selected_candidate_id,
        feedback="accept",
        recruiter_id=recruiter_id,
        session_id=session_id,
    )
    scoring_repo.apply_feedback_adjustment(job_id=job_id, feedback="accept")
    if selected_status not in _REUSABLE_STATUSES:
        interview_repo.upsert_status(job_id=job_id, candidate_id=selected_candidate_id, status="selected", create_default="selected")
    transition_candidate_ats_state(
        db=db,
        job_id=job_id,
        candidate_id=selected_candidate_id,
        to_status="selected",
        source="selection",
        actor_id=recruiter_id,
        reason="selection_choice",
        metadata={"selectionSessionId": session_id, "status": "selected"},
    )
    record_job_lifecycle_event(
        db=db,
        job_id=job_id,
        event_type="CANDIDATE_SAVED",
        payload={
            "jobId": job_id,
            "candidateId": selected_candidate_id,
            "selectionSessionId": session_id,
            "status": "selected",
        },
        source="selection",
    )


def _session_payload(
    *,
    session,
    current_batch: list[CandidateResult],
    final_candidates: list[CandidateResult] | None = None,
) -> dict[str, Any]:
    analysis = session.selection_analysis or None
    completed = (session.status or "").strip().lower() == "completed"
    payload: dict[str, Any] = {
        "sessionId": session.id,
        "jobId": session.job_id,
        "status": session.status,
        "currentBatchIndex": int(session.current_batch_index or 0),
        "totalBatches": int(session.total_batches or DEFAULT_TOTAL_BATCHES),
        "batchSize": int(session.batch_size or DEFAULT_BATCH_SIZE),
        "selectedCandidateIds": list(session.selected_candidate_ids or []),
        "rejectedCandidateIds": list(session.rejected_candidate_ids or []),
        "currentBatch": [candidate.model_dump() for candidate in current_batch],
        "analysis": analysis,
        "completed": completed,
        "finalCandidates": [candidate.model_dump() for candidate in (final_candidates or [])],
    }
    return payload


def _get_or_create_selection_session(*, db: Session, job_id: str) -> tuple[Any, dict[str, Any]]:
    jobs = JobRepository(db)
    job = jobs.get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)

    repository = CandidateSelectionSessionRepository(db)
    existing = repository.get_by_job(job_id)
    recruiter_id = jobs.get_recruiter_id(job_id) or ""
    state = get_preference_session(recruiter_id=recruiter_id, job_id=job_id) or {}

    if existing:
        expected_limit = _selection_limit(job)
        is_legacy_comparison = (
            int(existing.total_batches or 0) != DEFAULT_TOTAL_BATCHES
            or int(existing.batch_size or 0) <= 2
            or int(existing.batch_size or 0) != expected_limit
        )
        if is_legacy_comparison:
            candidate_pool = _selection_candidate_pool(db=db, job=job)
            _sync_selection_session_to_candidate_set(existing, candidate_pool)
            db.flush()
        lookup = _candidate_lookup_snapshot(existing.candidate_pool_snapshot or [])
        current_batch = _current_batch_from_session(existing, lookup)
        final_limit = _final_shortlist_limit(job)
        final_candidates = [CandidateResult.model_validate(row) for row in (existing.final_candidate_snapshot or [])][:final_limit]
        return existing, _selection_session_payload(session=existing, state=state, current_batch=current_batch, final_candidates=final_candidates)

    candidate_pool = _selection_candidate_pool(db=db, job=job)
    candidate_pool_snapshot = [candidate.model_dump() for candidate in candidate_pool]
    batch_plan = [[candidate.id for candidate in candidate_pool]]
    session = repository.create(
        job_id=job_id,
        candidate_pool_snapshot=candidate_pool_snapshot,
        batch_plan=batch_plan,
        batch_size=len(candidate_pool),
        total_batches=DEFAULT_TOTAL_BATCHES,
    )
    lookup = _candidate_lookup_snapshot(candidate_pool_snapshot)
    current_batch = _current_batch_from_session(session, lookup)
    return session, _selection_session_payload(session=session, state=state, current_batch=current_batch)


def get_first_selection_batch(*, db: Session, job_id: str) -> dict[str, Any]:
    _, payload = _get_or_create_selection_session(db=db, job_id=job_id)
    return payload


def get_next_selection_batch(*, db: Session, job_id: str) -> dict[str, Any]:
    session, payload = _get_or_create_selection_session(db=db, job_id=job_id)
    if (session.status or "").strip().lower() == "completed":
        return payload
    return payload


def submit_selection_choice(*, db: Session, job_id: str, candidate_id: str) -> dict[str, Any]:
    repository = CandidateSelectionSessionRepository(db)
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    try:
        session, payload = _get_or_create_selection_session(db=db, job_id=job_id)
        if (session.status or "").strip().lower() == "completed":
            return payload

        lookup = _candidate_lookup_snapshot(session.candidate_pool_snapshot or [])
        recruiter_id = JobRepository(db).get_recruiter_id(job_id) or ""
        state = get_preference_session(recruiter_id=recruiter_id, job_id=job_id) or {}
        current_batch = _current_batch_from_session(session, lookup)
        current_batch_ids = [candidate.id for candidate in current_batch]
        if candidate_id not in current_batch_ids:
            raise APIError("candidate is not part of the active candidate set", status_code=400)

        if candidate_id in (session.selected_candidate_ids or []):
            return payload

        # Keep the rest of the deck reviewable; selecting one candidate should not bulk-reject the others.
        rejected_candidate_ids: list[str] = []

        selected_candidate = lookup.get(candidate_id)
        rejected_candidates = [lookup[candidate] for candidate in rejected_candidate_ids if candidate in lookup]
        feedback_error = None
        enrichment_status = "enrichment_pending"
        outreach_status = "pending"
        reply_status = "outreach_pending"
        contact_email = ""
        contact_phone = ""
        candidate_status = "selected"
        previous_state = _normalize_text(getattr(selected_candidate, "status", "") or "new") if selected_candidate else "new"
        logger.info(
            "selection_choice_state job_id=%s candidate_id=%s feedback=%s previous_state=%s new_state=%s rejected_count=%s",
            job_id,
            candidate_id,
            "accept",
            previous_state,
            "selected",
            len(rejected_candidate_ids),
        )
        if selected_candidate:
            profile_repo = CandidateProfileRepository(db)
            profile = profile_repo.get(job_id=job_id, candidate_id=candidate_id) or profile_repo.ensure_candidate_profile(job_id=job_id, candidate_id=candidate_id)
            selected_name = _candidate_display_name(selected_candidate)
            selected_role = _normalize_text(getattr(selected_candidate, "role", "") or getattr(selected_candidate, "headline", "") or "")
            selected_company = _normalize_text(getattr(selected_candidate, "company", "") or getattr(selected_candidate, "currentCompany", "") or "")
            selected_summary = _normalize_text(getattr(selected_candidate, "summary", "") or "")
            selected_skills = list(getattr(selected_candidate, "skills", []) or [])
            selected_profile_data = dict(profile.raw_data or {}) if isinstance(profile.raw_data, dict) else {}
            if selected_name:
                profile.name = selected_name
                selected_profile_data["full_name"] = selected_name
                selected_profile_data["name"] = selected_name
            if selected_role:
                profile.current_role = selected_role
                selected_profile_data["role"] = selected_role
            if selected_company:
                profile.current_company = selected_company
                selected_profile_data["company"] = selected_company
                selected_profile_data["current_company"] = selected_company
            if selected_summary and not _normalize_text(getattr(profile, "summary", "") or ""):
                profile.summary = selected_summary
            if selected_skills and not list(getattr(profile, "skills", []) or []):
                profile.skills = selected_skills
            profile.raw_data = selected_profile_data
            db.flush()
        try:
            _store_selection_feedback(
                db,
                job_id=job_id,
                session_id=session.id,
                selected_candidate_id=candidate_id,
                rejected_candidate_ids=rejected_candidate_ids,
            )
        except Exception as exc:
            feedback_error = str(exc)
            logger.warning(
                "selection_feedback_write_failed job_id=%s candidate_id=%s error=%s",
                job_id,
                candidate_id,
                feedback_error,
            )

        history_entry = {
            "batchIndex": int(session.current_batch_index or 0),
            "selectedCandidateId": candidate_id,
            "rejectedCandidateIds": rejected_candidate_ids,
            "selectedAt": datetime.now(timezone.utc).isoformat(),
        }
        try:
            repository.mark_selection(
                session,
                selected_candidate_id=candidate_id,
                rejected_candidate_ids=rejected_candidate_ids,
                batch_index=int(session.current_batch_index or 0) + 1,
                history_entry=history_entry,
            )
        except Exception as exc:
            logger.warning(
                "selection_session_mark_failed job_id=%s candidate_id=%s error=%s",
                job_id,
                candidate_id,
                str(exc),
            )

        selected_linkedin_url = _normalize_linkedin_profile_url(
            getattr(selected_candidate, "linkedinUrl", ""),
            getattr(selected_candidate, "linkedin_url", ""),
            selected_profile_data.get("linkedin_url"),
            selected_profile_data.get("linkedinUrl"),
            selected_profile_data.get("source_url"),
            selected_profile_data.get("sourceUrl"),
            profile.linkedin_url,
        )
        enrichment_error = ""
        outreach_error = ""
        enrichment_result: dict[str, Any] | None = None
        outreach_result: dict[str, Any] | None = None
        reuse_result = check_and_reuse_interview(db=db, job_id=job_id, candidate_id=candidate_id)

        if reuse_result.reused:
            enrichment_status = "reused"
            outreach_status = "skipped"
            reply_status = "skipped"
        else:
            try:
                raw_profile_data = dict(selected_profile_data or {})
                contact_phone = _normalize_text(
                    raw_profile_data.get("phone")
                    or raw_profile_data.get("contactPhone")
                    or raw_profile_data.get("phoneNumber")
                    or ""
                )

                enrichment_result = {
                    "status": enrichment_status,
                    "enrichmentStatus": enrichment_status,
                    "shouldOutreach": False,
                    "contactEmail": "",
                    "contactPhone": contact_phone,
                }
                raw_profile_data["enrichment"] = {
                    **dict(raw_profile_data.get("enrichment") or {}),
                    "status": enrichment_status,
                    "enrichment_provider": "selection",
                    "sourceType": "review_selection",
                    "shouldOutreach": False,
                    "contactEmail": "",
                    "contactPhone": contact_phone,
                }
                profile.raw_data = raw_profile_data
                db.flush()

                from app.services.enrichment_orchestration_service import request_enrichment
                _sel_enrich = request_enrichment(
                    db=db,
                    job_id=job_id,
                    candidate_id=candidate_id,
                    action="selected",
                    source_type="review_selection",
                    selection_session_id=session.id,
                )
                queue_result = {
                    "job_id": _sel_enrich.queue_job_id,
                    "queue_type": "candidate_enrichment",
                    "triggered": _sel_enrich.triggered,
                    "skipped": _sel_enrich.skipped,
                    "skip_reason": _sel_enrich.skip_reason,
                }
                logger.info(
                    "selection_candidate_enrichment_queued job_id=%s candidate_id=%s queue_job_id=%s queue_type=%s",
                    job_id,
                    candidate_id,
                    queue_result.get("job_id") or "",
                    queue_result.get("queue_type") or "candidate_enrichment",
                )
                logger.info(
                    "selection_candidate_enrichment_started job_id=%s candidate_id=%s enrichment_status=%s outreach_status=%s linkedin_url=%s",
                    job_id,
                    candidate_id,
                    enrichment_status,
                    outreach_status,
                    selected_linkedin_url or "missing",
                )
            except Exception as exc:
                enrichment_error = str(exc)
                enrichment_status = "failed"
                outreach_status = "failed"
                reply_status = "outreach_not_sent"
                logger.error(
                    "selection_candidate_outreach_direct_failed job_id=%s candidate_id=%s error=%s linkedin_url=%s",
                    job_id,
                    candidate_id,
                    enrichment_error,
                    selected_linkedin_url or "missing",
                    exc_info=exc,
                )

        updated_session = repository.get_by_job(job_id)
        if not updated_session:
            raise APIError("Selection session not found", status_code=404)

        state = get_preference_session(recruiter_id=recruiter_id, job_id=job_id) or {}
        updated_session = repository.get_by_job(job_id)
        if not updated_session:
            raise APIError("Selection session not found", status_code=404)

        if (state.get("status") or "").strip().lower() == "completed" or int(updated_session.current_batch_index or 0) >= DEFAULT_TOTAL_BATCHES:
            final_limit = _final_shortlist_limit(job)
            selected_lookup = _candidate_lookup_snapshot(updated_session.candidate_pool_snapshot or [])
            selected_rows = [
                selected_lookup[candidate_id]
                for candidate_id in (updated_session.selected_candidate_ids or [])
                if candidate_id in selected_lookup
            ]
            analysis = _build_selection_analysis(selected_rows)
            final_candidates = _best_effort_final_candidates(
                db=db,
                job=job,
                updated_session=updated_session,
                selected_rows=selected_rows,
                analysis=analysis,
            )
            repository.complete(
                updated_session,
                selection_analysis=analysis,
                final_candidate_snapshot=[candidate.model_dump() for candidate in final_candidates[:final_limit]],
            )
            db.commit()
            completed_session = repository.get_by_job(job_id)
            if not completed_session:
                raise APIError("Selection session not found after completion", status_code=404)
            final_rows = [CandidateResult.model_validate(row) for row in (completed_session.final_candidate_snapshot or [])][:final_limit]
            try:
                state = finalize_preference_session(db=db, recruiter_id=recruiter_id, job_id=job_id)
            except Exception as exc:
                logger.warning("selection_finalize_state_failed job_id=%s error=%s", job_id, str(exc))
                state = get_preference_session(recruiter_id=recruiter_id, job_id=job_id) or {}
            return {
                **_session_payload(
                    session=completed_session,
                    current_batch=[],
                    final_candidates=final_rows,
                ),
                "analysis": completed_session.selection_analysis or analysis,
                "topCandidates": [candidate.model_dump() for candidate in final_rows],
                "stage": state.get("stage", "final_shortlist"),
                "intentProfile": state.get("intent_profile") or {},
                "telemetry": state.get("telemetry") or {},
                "warning": feedback_error or enrichment_error,
                "enrichmentStatus": enrichment_status,
                "outreachStatus": outreach_status,
                "replyStatus": reply_status,
                "candidateStatus": candidate_status,
                "contactEmail": contact_email,
                "contactPhone": contact_phone,
                "interviewReuse": {
                    "reused": reuse_result.reused,
                    "workflowToken": reuse_result.workflow_token,
                },
            }

        db.commit()
        refreshed_session = repository.get_by_job(job_id)
        if not refreshed_session:
            raise APIError("Selection session not found", status_code=404)
        refreshed_lookup = _candidate_lookup_snapshot(refreshed_session.candidate_pool_snapshot or [])
        next_batch = _best_effort_next_batch(refreshed_session, refreshed_lookup)
        payload = _selection_session_payload(session=refreshed_session, state=state, current_batch=next_batch)
        if feedback_error or enrichment_error:
            payload["warning"] = feedback_error or enrichment_error
        payload["enrichmentStatus"] = enrichment_status
        payload["outreachStatus"] = outreach_status
        payload["replyStatus"] = reply_status
        payload["candidateStatus"] = candidate_status
        payload["contactEmail"] = contact_email
        payload["contactPhone"] = contact_phone
        payload["interviewReuse"] = {
            "reused": reuse_result.reused,
            "workflowToken": reuse_result.workflow_token,
        }
        return payload
    except Exception as exc:
        logger.exception("selection_submit_unhandled job_id=%s candidate_id=%s error=%s", job_id, candidate_id, str(exc))
        session, payload = _get_or_create_selection_session(db=db, job_id=job_id)
        return payload


def get_final_selection_results(*, db: Session, job_id: str) -> dict[str, Any]:
    session, payload = _get_or_create_selection_session(db=db, job_id=job_id)
    if (session.status or "").strip().lower() != "completed":
        return payload

    job = JobRepository(db).get(job_id)
    final_limit = _final_shortlist_limit(job) if job else DEFAULT_FINAL_LIMITS["volume"]
    final_rows = [CandidateResult.model_validate(row) for row in (session.final_candidate_snapshot or [])][:final_limit]
    return {
        **payload,
        "analysis": session.selection_analysis or {},
        "topCandidates": [candidate.model_dump() for candidate in final_rows],
    }
