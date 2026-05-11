from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.repositories import CandidateSelectionSessionRepository, JobRepository
from app.services.job_gap_analysis_service import analyze_job_gap
from app.services.preference_pair_service import generate_preference_pair, generate_three_round_plan
from app.schemas.candidate import CandidateExplanation, CandidateResult
from app.services.recruiter_intent_service import (
    build_recruiter_intent_profile,
    persist_recruiter_intent_profile,
    save_cached_intent_profile,
    summarize_intent_profile,
)
from app.services.recruiter_preference_service import update_recruiter_preferences
from app.services.metrics_service import log_metric
from app.services.redis_service import get_redis
from app.services.recruiter_question_service import generate_recruiter_questions
from app.services.embedding_service import embed
from app.services.candidate_text import build_candidate_text

_STATE_PREFIX = "pontis:recruiter-preference-round:"
_STATE_TTL_SECONDS = 24 * 60 * 60


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = _normalize_text(value)
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(normalized)
    return ordered


def _job_mode(job: Any) -> str:
    value = _normalize_text(getattr(job, "vetting_mode", "") or getattr(job, "vettingMode", "") or "volume").lower()
    return value if value in {"volume", "elite"} else "volume"


def _extract_job_skills(job: Any, intent_profile: dict[str, Any]) -> list[str]:
    if isinstance(intent_profile, dict):
        preferred = list(intent_profile.get("preferred_skills") or [])
        required = list(intent_profile.get("required_skills") or [])
    else:
        preferred = []
        required = []
    job_skills = getattr(job, "skills_required", None) if not isinstance(job, dict) else job.get("skills_required")
    if isinstance(job_skills, list):
        required.extend(str(item) for item in job_skills)
    description = _normalize_text(getattr(job, "description", "") or (job.get("description") if isinstance(job, dict) else ""))
    for token in ("python", "typescript", "javascript", "react", "fastapi", "postgres", "aws", "gcp", "kubernetes", "terraform", "design systems", "system design", "leadership"):
        if token in description.lower():
            required.append(token)
    return _ordered_unique([*required, *preferred, "communication", "ownership", "execution"])


def _synthetic_candidate_blueprint(index: int, *, mode: str) -> dict[str, Any]:
    elite = mode == "elite"
    blueprints = [
        {
            "name": "Alex Rivera",
            "role": "Platform Engineer",
            "company": "Northstar Labs",
            "location": "Remote",
            "years": 9.5 if elite else 6.5,
            "summary": "Builds reliable systems, ships quickly, and owns ambiguous work without needing heavy supervision.",
            "archetype": "systems_owner",
        },
        {
            "name": "Jordan Chen",
            "role": "Product Engineer",
            "company": "Axiom Works",
            "location": "Bengaluru, India",
            "years": 8.8 if elite else 5.9,
            "summary": "Pairs product thinking with execution depth and keeps stakeholder communication crisp.",
            "archetype": "product_operator",
        },
        {
            "name": "Priya Shah",
            "role": "Staff Backend Engineer",
            "company": "Signal Forge",
            "location": "Remote",
            "years": 11.0 if elite else 7.1,
            "summary": "Strong at service design, performance tuning, and mentoring others through messy production issues.",
            "archetype": "technical_lead",
        },
        {
            "name": "Miguel Santos",
            "role": "Applied Systems Engineer",
            "company": "HarborStack",
            "location": "Singapore",
            "years": 7.8 if elite else 5.2,
            "summary": "Turns unclear product requirements into scoped deliverables and keeps momentum high.",
            "archetype": "startup_operator",
        },
        {
            "name": "Nina Patel",
            "role": "Infrastructure Engineer",
            "company": "Cinder Cloud",
            "location": "Remote",
            "years": 10.2 if elite else 6.8,
            "summary": "Deep cloud and infrastructure background with careful operational habits and practical judgment.",
            "archetype": "infra_specialist",
        },
        {
            "name": "Ethan Brooks",
            "role": "Full Stack Engineer",
            "company": "Evergreen Studio",
            "location": "Austin, TX",
            "years": 6.9 if elite else 4.8,
            "summary": "A balanced generalist who moves fast, learns quickly, and works well across product and engineering.",
            "archetype": "balanced_generalist",
        },
    ]
    return blueprints[index % len(blueprints)]


def _build_synthetic_candidate(
    *,
    job: Any,
    intent_profile: dict[str, Any],
    voice_summary: str,
    gap_analysis: dict[str, Any],
    mode: str,
    index: int,
) -> CandidateResult:
    job_title = _normalize_text(getattr(job, "title", "") or (job.get("title") if isinstance(job, dict) else "") or "Candidate")
    job_location = _normalize_text(getattr(job, "location", "") or (job.get("location") if isinstance(job, dict) else "") or "Remote")
    job_description = _normalize_text(getattr(job, "description", "") or (job.get("description") if isinstance(job, dict) else ""))
    skills = _extract_job_skills(job, intent_profile)
    blueprint = _synthetic_candidate_blueprint(index, mode=mode)
    fit_base = 4.65 if mode == "elite" else 4.35
    fit_score = max(3.6, round(fit_base - (index * (0.08 if mode == "elite" else 0.12)), 2))
    semantic = round(min(0.99, fit_score / 5.0), 3)
    shared_skills = skills[: min(5, len(skills))]
    summary = (
        f"{blueprint['name']} is a synthetic {blueprint['role'].lower()} profile created from the job brief and recruiter voice intake. "
        f"The profile emphasizes {', '.join(shared_skills[:4]) or 'execution'} and reflects a {mode} hiring mood."
    )
    resume_lines = [
        f"Target role: {job_title} ({mode} mood)",
        f"Location: {job_location}",
        f"Years of experience: {blueprint['years']:.1f}",
        f"Core strengths: {', '.join(shared_skills[:6]) or 'execution, ownership, communication'}",
        "",
        "Selected background",
        f"- {blueprint['summary']}",
        f"- Built around the recruiter signals captured from voice intake: {_normalize_text(voice_summary) or 'job requirements only'}.",
        f"- Focus areas: {job_description[:220] or 'N/A'}",
    ]
    explanation = CandidateExplanation(
        semanticScore=semantic,
        skillOverlap=min(1.0, len(shared_skills) / max(1, len(skills))),
        finalScore=semantic,
        pdlRelevance=semantic,
        recencyScore=0.72 if mode == "elite" else 0.63,
        penalties={
            "semanticPenalty": round(max(0.0, 0.15 - (index * 0.02)), 4),
            "missingSkillsPenalty": 0.0,
            "selectionPreferenceBonus": 0.0,
        },
        skillsMatched=shared_skills[:4],
        experienceMatch=f"{int(round(blueprint['years']))}+ years on adjacent work",
        candidateExperience=f"{blueprint['years']:.1f} years",
        jobExperience=_normalize_text(getattr(job, "experience_required", "") or getattr(job, "experienceRequired", "") or ""),
        aiReasoning=f"Synthetic profile tuned for {mode} selection. The intent is to reveal recruiter taste before using the real candidate pool.",
        sourceBreakdown={
            "vector": round(0.24 + index * 0.03, 4),
            "lexical": round(0.28 + index * 0.02, 4),
            "structured": round(0.22 + index * 0.02, 4),
            "recruiterPreference": 0.0,
            "freshness": round(0.18 + index * 0.01, 4),
            "selectionRound": 0.0,
            "voiceInterview": round(0.26 + index * 0.02, 4),
        },
    )
    fit_label = "HIGH" if fit_score >= 4 else "MEDIUM" if fit_score >= 2.5 else "LOW"
    decision = "strong_match" if fit_score >= 3.8 else "potential" if fit_score >= 2.5 else "weak"

    return CandidateResult(
        id=f"synthetic-{mode}-{_normalize_text(getattr(job, 'id', '') or (job.get('id') if isinstance(job, dict) else 'job'))}-{index + 1}",
        name=blueprint["name"],
        role=blueprint["role"],
        company=blueprint["company"],
        email=f"{blueprint['name'].lower().replace(' ', '.')}@synthetic.pontis.test",
        isMockEmail=True,
        headline=f"{mode.title()} candidate for {job_title}",
        location=blueprint["location"],
        yearsExperience=float(blueprint["years"]),
        skills=shared_skills,
        summary=summary,
        education=[
            "B.S. Computer Science, synthetic profile",
            "Professional development in systems design and product delivery",
        ],
        projects=[
            "Internal platform modernization",
            "Cross-functional workflow automation",
            "Operational dashboard rollout",
        ],
        certifications=[
            "AWS Fundamentals",
            "System Design Foundations",
        ],
        companiesHistory=[
            blueprint["company"],
            "Synthetic Growth Labs",
        ],
        domainExperience=[
            "Hiring intake analysis",
            "Platform execution",
            "High-velocity product delivery",
        ],
        resumeText="\n".join(resume_lines).strip(),
        profileData={
            "source": "synthetic",
            "mood": mode,
            "archetype": blueprint["archetype"],
            "generated_from": "job_details_and_voice_intake",
            "job_title": job_title,
            "job_location": job_location,
            "voice_summary": _normalize_text(voice_summary),
            "gap_analysis": gap_analysis,
        },
        fitScore=fit_score,
        decision=decision,
        explanation=explanation,
        strategy=fit_label,
        status="new",
        outreachStatus="pending",
        exportStatus="pending",
        ats_export_status="not_sent",
    )


def _build_synthetic_candidate_pool(
    *,
    job: Any,
    voice_summary: str,
    gap_analysis: dict[str, Any],
    intent_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    mode = _job_mode(job)
    synthetic_candidates = [
        _build_synthetic_candidate(
            job=job,
            intent_profile=intent_profile,
            voice_summary=voice_summary,
            gap_analysis=gap_analysis,
            mode=mode,
            index=index,
        )
        for index in range(6)
    ]
    return [candidate.model_dump(exclude_none=True) for candidate in synthetic_candidates]


def _state_key(*, recruiter_id: str, job_id: str) -> str:
    return f"{_STATE_PREFIX}{_normalize_text(recruiter_id)}:{_normalize_text(job_id)}"


def _serialize_candidate(candidate: Any) -> dict[str, Any]:
    if isinstance(candidate, dict):
        candidate_id = _normalize_text(candidate.get("id"))
        return {
            "id": candidate_id,
            "name": _normalize_text(candidate.get("name")),
            "role": _normalize_text(candidate.get("role")),
            "company": _normalize_text(candidate.get("company")),
            "skills": list(candidate.get("skills") or []),
            "summary": _normalize_text(candidate.get("summary")),
            "fitScore": float(candidate.get("fitScore") or candidate.get("fit_score") or 0.0),
            "status": _normalize_text(candidate.get("status") or "new") or "new",
        }
    return {
        "id": _normalize_text(getattr(candidate, "id", "")),
        "name": _normalize_text(getattr(candidate, "name", "")),
        "role": _normalize_text(getattr(candidate, "role", "")),
        "company": _normalize_text(getattr(candidate, "company", "")),
        "skills": list(getattr(candidate, "skills", []) or []),
        "summary": _normalize_text(getattr(candidate, "summary", "")),
        "fitScore": float(getattr(candidate, "fitScore", 0.0) or 0.0),
        "status": _normalize_text(getattr(candidate, "status", "new") or "new") or "new",
    }


def _load_state(*, recruiter_id: str, job_id: str) -> dict[str, Any] | None:
    redis = get_redis()
    if redis is None:
        return None
    raw = redis.get(_state_key(recruiter_id=recruiter_id, job_id=job_id))
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _save_state(*, recruiter_id: str, job_id: str, state: dict[str, Any]) -> dict[str, Any]:
    redis = get_redis()
    state = dict(state)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    if redis is not None:
        try:
            redis.set(_state_key(recruiter_id=recruiter_id, job_id=job_id), json.dumps(state), ex=_STATE_TTL_SECONDS)
        except Exception:
            pass
    return state


def _candidate_lookup(pool: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("id") or "").strip(): item for item in pool if str(item.get("id") or "").strip()}


def _pair_by_ids(pool: list[dict[str, Any]], pair_ids: list[str]) -> list[dict[str, Any]]:
    lookup = _candidate_lookup(pool)
    return [lookup[candidate_id] for candidate_id in pair_ids if candidate_id in lookup]


def _ensure_pair_for_round(
    *,
    state: dict[str, Any],
    job: Any,
    recruiter_id: str,
    round_index: int,
    previous_choice: dict[str, Any] | None,
) -> dict[str, Any]:
    rounds = list(state.get("rounds") or [])
    existing = next((item for item in rounds if int(item.get("round_index") or 0) == round_index), None)
    if existing and existing.get("candidate_ids"):
        return existing

    intent_profile = state.get("intent_profile") or {}
    pool = list(state.get("candidate_pool") or [])
    pair = generate_preference_pair(
        candidates=pool,
        intent_profile=intent_profile,
        round_index=round_index,
        previous_choice=previous_choice,
        excluded_ids=set(state.get("selected_candidate_ids") or []) | set(state.get("rejected_candidate_ids") or []),
    )
    rounds = [item for item in rounds if int(item.get("round_index") or 0) != round_index]
    rounds.append(pair)
    rounds.sort(key=lambda item: int(item.get("round_index") or 0))
    state["rounds"] = rounds
    state["current_round_index"] = round_index
    state["current_pair"] = pair
    return pair


def bootstrap_preference_session(
    *,
    db: Session,
    recruiter_id: str,
    job_id: str,
    voice_summary: str = "",
    gap_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    job = JobRepository(db).get(job_id)
    if not job:
        raise ValueError("Job not found")

    recruiter_id = _normalize_text(recruiter_id)
    existing_state = _load_state(recruiter_id=recruiter_id, job_id=job_id)
    if existing_state:
        return existing_state

    gap_analysis = gap_analysis or analyze_job_gap(job=job, voice_summary=voice_summary)
    intent_profile = build_recruiter_intent_profile(
        db=db,
        recruiter_id=recruiter_id,
        job=job,
        voice_summary=voice_summary,
        gap_analysis=gap_analysis,
        selection_rounds=[],
        transcript=voice_summary,
    )
    persist_recruiter_intent_profile(db=db, recruiter_id=recruiter_id, profile=intent_profile)
    candidate_pool = _build_synthetic_candidate_pool(
        job=job,
        voice_summary=voice_summary,
        gap_analysis=gap_analysis,
        intent_profile=intent_profile,
    )

    round_plan = generate_three_round_plan(candidates=candidate_pool, intent_profile=intent_profile)
    session_repo = CandidateSelectionSessionRepository(db)
    db_session, _created = session_repo.get_or_create(
        job_id=job_id,
        candidate_pool_snapshot=candidate_pool,
        batch_plan=[pair.get("candidate_ids", []) for pair in round_plan],
        batch_size=2,
        total_batches=3,
    )
    state = {
        "job_id": job_id,
        "recruiter_id": recruiter_id,
        "status": "active",
        "stage": "dynamic_questioning" if gap_analysis.get("recommended_questions") else "intent_refinement",
        "current_round_index": 1,
        "candidate_pool": candidate_pool,
        "rounds": round_plan,
        "current_pair": round_plan[0] if round_plan else {},
        "selected_candidate_ids": [],
        "rejected_candidate_ids": [],
        "history": [],
        "gap_analysis": gap_analysis,
        "recommended_questions": list(gap_analysis.get("recommended_questions") or []),
        "vetting_mode": _job_mode(job),
        "candidate_source": "synthetic",
        "intent_profile": summarize_intent_profile(intent_profile),
        "voice_summary": _normalize_text(voice_summary),
        "session_id": db_session.id,
        "telemetry": {
            "preference_learning_gain": 0.0,
            "rerank_precision_gain": 0.0,
            "pair_signal_quality": round(float(round_plan[0].get("signal_quality") or 0.0), 4) if round_plan else 0.0,
            "recruiter_preference_confidence": float(intent_profile.get("history_signal_strength") or 0.0),
        },
    }
    _save_state(recruiter_id=recruiter_id, job_id=job_id, state=state)
    if round_plan:
        log_metric("pair_signal_quality", value=float(round_plan[0].get("signal_quality") or 0.0))
    return state


def get_preference_session(*, recruiter_id: str, job_id: str) -> dict[str, Any] | None:
    return _load_state(recruiter_id=recruiter_id, job_id=job_id)


def _live_profile_update(
    *,
    db: Session,
    recruiter_id: str,
    job: Any,
    state: dict[str, Any],
    selected_candidate: dict[str, Any],
    rejected_candidates: list[dict[str, Any]],
    round_index: int,
) -> dict[str, Any]:
    previous_rounds = list(state.get("history") or [])
    updated_profile = build_recruiter_intent_profile(
        db=db,
        recruiter_id=recruiter_id,
        job=job,
        voice_summary=state.get("voice_summary", ""),
        gap_analysis=state.get("gap_analysis") or {},
        selection_rounds=previous_rounds,
        transcript=state.get("voice_summary", ""),
    )

    selected_embedding = embed(build_candidate_text(selected_candidate))
    rejected_embeddings = [embed(build_candidate_text(candidate)) for candidate in rejected_candidates]
    rejected_mean = [sum(values) / max(1, len(values)) for values in zip(*rejected_embeddings)] if rejected_embeddings else []
    delta_vector = []
    if selected_embedding and rejected_mean and len(selected_embedding) == len(rejected_mean):
        delta_vector = [round(sel - rej, 8) for sel, rej in zip(selected_embedding, rejected_mean)]

    state["intent_profile"] = summarize_intent_profile(updated_profile)
    state["live_embedding_delta"] = delta_vector
    state["telemetry"] = {
        **dict(state.get("telemetry") or {}),
        "preference_learning_gain": round(min(1.0, (round_index / 3.0) * 0.35 + len(previous_rounds) * 0.05), 4),
        "pair_signal_quality": round(float((state.get("current_pair") or {}).get("signal_quality") or 0.0), 4),
        "recruiter_preference_confidence": round(min(1.0, float(updated_profile.get("history_signal_strength") or 0.0) + round_index * 0.12), 4),
    }
    log_metric("preference_learning_gain", value=state["telemetry"]["preference_learning_gain"])
    log_metric("pair_signal_quality", value=state["telemetry"]["pair_signal_quality"])
    log_metric("recruiter_preference_confidence", value=state["telemetry"]["recruiter_preference_confidence"])
    return state


def record_preference_choice(
    *,
    db: Session,
    recruiter_id: str,
    job_id: str,
    selected_candidate_id: str,
    previous_round: int | None = None,
) -> dict[str, Any]:
    recruiter_id = _normalize_text(recruiter_id)
    job = JobRepository(db).get(job_id)
    if not job:
        raise ValueError("Job not found")

    state = _load_state(recruiter_id=recruiter_id, job_id=job_id)
    if not state:
        state = bootstrap_preference_session(db=db, recruiter_id=recruiter_id, job_id=job_id)

    pool = list(state.get("candidate_pool") or [])
    lookup = _candidate_lookup(pool)
    current_round_index = int(previous_round or state.get("current_round_index") or 1)
    current_pair = _ensure_pair_for_round(
        state=state,
        job=job,
        recruiter_id=recruiter_id,
        round_index=current_round_index,
        previous_choice=state.get("history", [])[-1] if state.get("history") else None,
    )
    current_ids = list(current_pair.get("candidate_ids") or [])
    if selected_candidate_id not in current_ids:
        raise ValueError("Candidate is not part of the active comparison pair")

    rejected_candidate_ids = [candidate_id for candidate_id in current_ids if candidate_id != selected_candidate_id]
    selected_candidate = lookup.get(selected_candidate_id, {})
    rejected_candidates = [lookup[candidate_id] for candidate_id in rejected_candidate_ids if candidate_id in lookup]

    update_recruiter_preferences(
        db,
        recruiter_id,
        selected_candidate,
        rejected_candidates,
        signal_multiplier=1.1 + (current_round_index * 0.25),
    )

    history_entry = {
        "round_index": current_round_index,
        "selected_candidate_id": selected_candidate_id,
        "selected_candidate_name": selected_candidate.get("name", ""),
        "selected_candidate_skills": list(selected_candidate.get("skills") or []),
        "rejected_candidate_ids": rejected_candidate_ids,
        "signal_summary": current_pair.get("rationale", ""),
        "selected_at": datetime.now(timezone.utc).isoformat(),
        "pair_explanation": current_pair.get("pair_explanation", {}),
    }
    state["selected_candidate_ids"] = list(dict.fromkeys([*state.get("selected_candidate_ids", []), selected_candidate_id]))
    state["rejected_candidate_ids"] = list(dict.fromkeys([*state.get("rejected_candidate_ids", []), *rejected_candidate_ids]))
    state["history"] = [*list(state.get("history") or []), history_entry]
    state = _live_profile_update(
        db=db,
        recruiter_id=recruiter_id,
        job=job,
        state=state,
        selected_candidate=selected_candidate,
        rejected_candidates=rejected_candidates,
        round_index=current_round_index,
    )

    next_round_index = current_round_index + 1
    if next_round_index <= 3:
        next_pair = _ensure_pair_for_round(
            state=state,
            job=job,
            recruiter_id=recruiter_id,
            round_index=next_round_index,
            previous_choice={
                "selected_candidate_id": selected_candidate_id,
                "selected_candidate_skills": selected_candidate.get("skills", []),
            },
        )
        state["stage"] = "preference_rounds" if next_round_index <= 3 else "final_shortlist"
        state["current_round_index"] = next_round_index
        state["current_pair"] = next_pair
        state["status"] = "active"
    else:
        state["status"] = "completed"
        state["stage"] = "final_shortlist"

    final_state = _save_state(recruiter_id=recruiter_id, job_id=job_id, state=state)
    log_metric("rerank_precision_gain", value=float(state.get("telemetry", {}).get("preference_learning_gain", 0.0)))
    save_cached_intent_profile(recruiter_id=recruiter_id, job_id=job_id, profile=final_state.get("intent_profile") or {})
    return final_state


def finalize_preference_session(*, db: Session, recruiter_id: str, job_id: str) -> dict[str, Any]:
    recruiter_id = _normalize_text(recruiter_id)
    job = JobRepository(db).get(job_id)
    if not job:
        raise ValueError("Job not found")

    state = _load_state(recruiter_id=recruiter_id, job_id=job_id)
    if not state:
        state = bootstrap_preference_session(db=db, recruiter_id=recruiter_id, job_id=job_id)

    selection_rounds = list(state.get("history") or [])
    intent_profile = build_recruiter_intent_profile(
        db=db,
        recruiter_id=recruiter_id,
        job=job,
        voice_summary=state.get("voice_summary", ""),
        gap_analysis=state.get("gap_analysis") or {},
        selection_rounds=selection_rounds,
        transcript=state.get("voice_summary", ""),
    )
    persist_recruiter_intent_profile(db=db, recruiter_id=recruiter_id, profile=intent_profile)
    state["intent_profile"] = summarize_intent_profile(intent_profile)
    state["status"] = "completed"
    state["stage"] = "final_shortlist"
    state["candidate_source"] = "real" if len(selection_rounds) >= 3 else state.get("candidate_source", "synthetic")
    state["vetting_mode"] = _job_mode(job)
    state["telemetry"] = {
        **dict(state.get("telemetry") or {}),
        "recruiter_preference_confidence": round(min(1.0, float(intent_profile.get("history_signal_strength") or 0.0) + len(selection_rounds) * 0.12), 4),
    }
    return _save_state(recruiter_id=recruiter_id, job_id=job_id, state=state)


def build_state_response(state: dict[str, Any] | None) -> dict[str, Any]:
    if not state:
        return {
            "status": "missing",
            "stage": "initial_job_understanding",
            "rounds": [],
            "current_pair": {},
            "history": [],
            "intent_profile": {},
            "recommended_questions": [],
            "telemetry": {},
        }

    return {
        "status": state.get("status", "active"),
        "stage": state.get("stage", "initial_job_understanding"),
        "vetting_mode": state.get("vetting_mode", "volume"),
        "candidate_source": state.get("candidate_source", "synthetic"),
        "rounds": list(state.get("rounds") or []),
        "current_round_index": int(state.get("current_round_index") or 1),
        "current_pair": state.get("current_pair") or {},
        "history": list(state.get("history") or []),
        "gap_analysis": state.get("gap_analysis") or {},
        "recommended_questions": list(state.get("recommended_questions") or []),
        "intent_profile": state.get("intent_profile") or {},
        "telemetry": state.get("telemetry") or {},
        "voice_summary": state.get("voice_summary", ""),
    }
