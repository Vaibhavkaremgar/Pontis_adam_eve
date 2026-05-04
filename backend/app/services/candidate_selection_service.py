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
    InterviewRepository,
    JobRepository,
    ScoringProfileRepository,
)
from app.schemas.candidate import CandidateExplanation, CandidateResult
from app.services.candidate_service import fetch_ranked_candidates
from app.services.recruiter_preference_service import update_recruiter_preferences
from app.services.skill_normalizer import normalize_skills, parse_experience
from app.services.state_machine import assert_valid_transition, is_swipe_locked
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)
DEFAULT_BATCH_SIZE = 2
DEFAULT_TOTAL_BATCHES = 3
DEFAULT_SELECTION_LIMIT = DEFAULT_BATCH_SIZE * DEFAULT_TOTAL_BATCHES
DEFAULT_FINAL_LIMIT = 12


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


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
    if len(candidates) < DEFAULT_SELECTION_LIMIT:
        raise APIError("Not enough candidates to build a 3x2 selection flow", status_code=409)

    diverse_subset = _select_diverse_subset(candidates, limit=DEFAULT_SELECTION_LIMIT)
    if len(diverse_subset) < DEFAULT_SELECTION_LIMIT:
        diverse_subset = sorted(candidates, key=lambda candidate: (-float(candidate.fitScore or 0.0), candidate.name or candidate.id))[:DEFAULT_SELECTION_LIMIT]

    batches: list[list[CandidateResult]] = [[], [], []]
    for index, candidate in enumerate(diverse_subset[:DEFAULT_SELECTION_LIMIT]):
        batches[index % DEFAULT_TOTAL_BATCHES].append(candidate)

    for batch in batches:
        batch.sort(key=lambda candidate: (-float(candidate.fitScore or 0.0), candidate.name or candidate.id))

    return [[candidate.id for candidate in batch] for batch in batches]


def _candidate_lookup_snapshot(snapshot: list[dict[str, Any]]) -> dict[str, CandidateResult]:
    lookup: dict[str, CandidateResult] = {}
    for row in snapshot:
        try:
            candidate = CandidateResult.model_validate(row)
        except Exception:
            continue
        lookup[candidate.id] = candidate
    return lookup


def _current_batch_from_session(session, snapshot_lookup: dict[str, CandidateResult]) -> list[CandidateResult]:
    batch_plan = list(session.batch_plan or [])
    batch_index = max(0, int(session.current_batch_index or 0))
    if batch_index >= len(batch_plan):
        return []
    return [snapshot_lookup[candidate_id] for candidate_id in batch_plan[batch_index] if candidate_id in snapshot_lookup]


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
        explanation = candidate_copy.explanation or CandidateExplanation(
            semanticScore=0.0,
            skillOverlap=0.0,
            finalScore=final_score,
            pdlRelevance=0.0,
            recencyScore=0.0,
            penalties={},
        )
        explanation.finalScore = round(final_score, 4)
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

    reranked.sort(key=lambda candidate: (-float(candidate.explanation.finalScore if candidate.explanation else 0.0), -float(candidate.fitScore or 0.0), candidate.name or candidate.id))
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
    if is_swipe_locked(selected_status):
        raise APIError(f"Cannot select candidate in '{selected_status}' state.", status_code=409)

    if selected_status != "shortlisted":
        assert_valid_transition(
            candidate_id=selected_candidate_id,
            job_id=job_id,
            from_status=selected_status,
            to_status="shortlisted",
        )
    feedback_repo.upsert(
        job_id=job_id,
        candidate_id=selected_candidate_id,
        feedback="accept",
        recruiter_id=recruiter_id,
        session_id=session_id,
    )
    scoring_repo.apply_feedback_adjustment(job_id=job_id, feedback="accept")
    interview_repo.upsert_status(job_id=job_id, candidate_id=selected_candidate_id, status="shortlisted", create_default="shortlisted")

    for candidate_id in rejected_candidate_ids:
        if not candidate_id or candidate_id == selected_candidate_id:
            continue
        rejected_row = interview_repo.get_by_job_and_candidate(job_id, candidate_id)
        rejected_status = (rejected_row.status if rejected_row else "new") or "new"
        if is_swipe_locked(rejected_status):
            raise APIError(f"Cannot reject candidate in '{rejected_status}' state.", status_code=409)
        if rejected_status != "rejected":
            assert_valid_transition(
                candidate_id=candidate_id,
                job_id=job_id,
                from_status=rejected_status,
                to_status="rejected",
            )
        feedback_repo.upsert(
            job_id=job_id,
            candidate_id=candidate_id,
            feedback="reject",
            recruiter_id=recruiter_id,
            session_id=session_id,
        )
        scoring_repo.apply_feedback_adjustment(job_id=job_id, feedback="reject")
        interview_repo.upsert_status(job_id=job_id, candidate_id=candidate_id, status="rejected", create_default="rejected")


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
    if not jobs.get(job_id):
        raise APIError("Job not found", status_code=404)

    repository = CandidateSelectionSessionRepository(db)
    existing = repository.get_by_job(job_id)
    if existing:
        lookup = _candidate_lookup_snapshot(existing.candidate_pool_snapshot or [])
        current_batch = _current_batch_from_session(existing, lookup)
        final_candidates = [CandidateResult.model_validate(row) for row in (existing.final_candidate_snapshot or [])]
        return existing, _session_payload(session=existing, current_batch=current_batch, final_candidates=final_candidates)

    ranked_candidates = fetch_ranked_candidates(db=db, job_id=job_id, mode=None, refresh=True)
    if len(ranked_candidates) < DEFAULT_SELECTION_LIMIT:
        raise APIError("Not enough candidates to start selection flow", status_code=409)

    candidate_pool_snapshot = [candidate.model_dump() for candidate in ranked_candidates[:DEFAULT_FINAL_LIMIT]]
    candidate_pool = [CandidateResult.model_validate(row) for row in candidate_pool_snapshot]
    batch_plan = _build_batch_plan(candidate_pool)
    session = repository.create(
        job_id=job_id,
        candidate_pool_snapshot=candidate_pool_snapshot,
        batch_plan=batch_plan,
        batch_size=DEFAULT_BATCH_SIZE,
        total_batches=DEFAULT_TOTAL_BATCHES,
    )
    lookup = _candidate_lookup_snapshot(candidate_pool_snapshot)
    current_batch = _current_batch_from_session(session, lookup)
    return session, _session_payload(session=session, current_batch=current_batch)


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
    session, payload = _get_or_create_selection_session(db=db, job_id=job_id)
    if (session.status or "").strip().lower() == "completed":
        return payload

    lookup = _candidate_lookup_snapshot(session.candidate_pool_snapshot or [])
    current_batch = _current_batch_from_session(session, lookup)
    current_batch_ids = [candidate.id for candidate in current_batch]
    if candidate_id not in current_batch_ids:
        raise APIError("candidate is not part of the active batch", status_code=400)

    if candidate_id in (session.selected_candidate_ids or []):
        return payload

    rejected_candidate_ids = [cid for cid in current_batch_ids if cid != candidate_id]

    _store_selection_feedback(
        db,
        job_id=job_id,
        session_id=session.id,
        selected_candidate_id=candidate_id,
        rejected_candidate_ids=rejected_candidate_ids,
    )

    selected_candidate = lookup.get(candidate_id)
    rejected_candidates = [lookup[candidate] for candidate in rejected_candidate_ids if candidate in lookup]
    recruiter_id = JobRepository(db).get_recruiter_id(job_id)
    if recruiter_id and selected_candidate:
        update_recruiter_preferences(
            db,
            recruiter_id,
            selected_candidate,
            rejected_candidates,
        )

    history_entry = {
        "batchIndex": int(session.current_batch_index or 0),
        "selectedCandidateId": candidate_id,
        "rejectedCandidateIds": rejected_candidate_ids,
        "selectedAt": datetime.now(timezone.utc).isoformat(),
    }
    repository.mark_selection(
        session,
        selected_candidate_id=candidate_id,
        rejected_candidate_ids=rejected_candidate_ids,
        batch_index=int(session.current_batch_index or 0) + 1,
        history_entry=history_entry,
    )

    updated_session = repository.get_by_job(job_id)
    if not updated_session:
        raise APIError("Selection session not found", status_code=404)

    if int(updated_session.current_batch_index or 0) >= DEFAULT_TOTAL_BATCHES:
        selected_lookup = _candidate_lookup_snapshot(updated_session.candidate_pool_snapshot or [])
        selected_rows = [
            selected_lookup[candidate_id]
            for candidate_id in (updated_session.selected_candidate_ids or [])
            if candidate_id in selected_lookup
        ]
        analysis = _build_selection_analysis(selected_rows)
        final_candidates = _rerank_with_selection_signals(
            pool_candidates=[CandidateResult.model_validate(row) for row in (updated_session.candidate_pool_snapshot or [])],
            selected_candidates=selected_rows,
            analysis=analysis,
        )
        repository.complete(
            updated_session,
            selection_analysis=analysis,
            final_candidate_snapshot=[candidate.model_dump() for candidate in final_candidates[:DEFAULT_FINAL_LIMIT]],
        )
        db.commit()
        completed_session = repository.get_by_job(job_id)
        if not completed_session:
            raise APIError("Selection session not found after completion", status_code=404)
        final_rows = [CandidateResult.model_validate(row) for row in (completed_session.final_candidate_snapshot or [])]
        return {
            **_session_payload(
                session=completed_session,
                current_batch=[],
                final_candidates=final_rows,
            ),
            "analysis": completed_session.selection_analysis or analysis,
            "topCandidates": [candidate.model_dump() for candidate in final_rows],
        }

    db.commit()
    refreshed_session = repository.get_by_job(job_id)
    if not refreshed_session:
        raise APIError("Selection session not found", status_code=404)
    refreshed_lookup = _candidate_lookup_snapshot(refreshed_session.candidate_pool_snapshot or [])
    next_batch = _current_batch_from_session(refreshed_session, refreshed_lookup)
    return _session_payload(session=refreshed_session, current_batch=next_batch)


def get_final_selection_results(*, db: Session, job_id: str) -> dict[str, Any]:
    session, payload = _get_or_create_selection_session(db=db, job_id=job_id)
    if (session.status or "").strip().lower() != "completed":
        return payload

    final_rows = [CandidateResult.model_validate(row) for row in (session.final_candidate_snapshot or [])]
    return {
        **payload,
        "analysis": session.selection_analysis or {},
        "topCandidates": [candidate.model_dump() for candidate in final_rows],
    }
