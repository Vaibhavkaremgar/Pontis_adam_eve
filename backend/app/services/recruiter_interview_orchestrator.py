from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.repositories import JobRepository
from app.services.job_gap_analysis_service import analyze_job_gap
from app.services.recruiter_intent_service import build_recruiter_intent_profile, persist_recruiter_intent_profile, summarize_intent_profile
from app.services.recruiter_preference_round_service import bootstrap_preference_session, build_state_response
from app.services.recruiter_question_service import generate_recruiter_questions
from app.services.redis_service import get_redis

_STATE_PREFIX = "pontis:recruiter-interview:"
_STATE_TTL_SECONDS = 24 * 60 * 60


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _state_key(*, recruiter_id: str, job_id: str) -> str:
    return f"{_STATE_PREFIX}{_normalize_text(recruiter_id)}:{_normalize_text(job_id)}"


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


def _persist_job_intelligence_snapshot(*, db: Session, job_id: str, state: dict[str, Any]) -> None:
    job_repo = JobRepository(db)
    job = job_repo.get(job_id)
    if not job:
        return

    structured = dict(job.structured_data or {})
    structured["recruiterIntelligence"] = {
        "stage": state.get("stage", "initial_job_understanding"),
        "status": state.get("status", "active"),
        "transcript": state.get("transcript", ""),
        "voiceSummary": state.get("voice_summary", ""),
        "gapAnalysis": state.get("gap_analysis") or {},
        "recommendedQuestions": list(state.get("recommended_questions") or []),
        "intentProfile": state.get("intent_profile") or {},
        "currentQuestion": state.get("current_question", ""),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    job_repo.update_structured_fields(job_id=job_id, structured_data=structured)


def start_recruiter_interview_session(
    *,
    db: Session,
    recruiter_id: str,
    job_id: str,
    transcript: str = "",
    entities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    job = JobRepository(db).get(job_id)
    if not job:
        raise ValueError("Job not found")

    recruiter_id = _normalize_text(recruiter_id)
    transcript = _normalize_text(transcript)
    existing = _load_state(recruiter_id=recruiter_id, job_id=job_id)
    if existing and transcript and not existing.get("transcript"):
        existing["transcript"] = transcript
        existing["voice_summary"] = existing.get("voice_summary") or transcript
        final_existing = _save_state(recruiter_id=recruiter_id, job_id=job_id, state=existing)
        _persist_job_intelligence_snapshot(db=db, job_id=job_id, state=final_existing)
        return final_existing
    if existing:
        return existing

    gap_analysis = analyze_job_gap(job=job, voice_summary=transcript, entities=entities or {})
    recommended_questions = generate_recruiter_questions(
        gap_analysis=gap_analysis,
        job=job,
        voice_summary=transcript,
        max_questions=7,
    )
    voice_summary = transcript or "Structured job intake captured. Proceeding with recruiter calibration."
    intent_profile = build_recruiter_intent_profile(
        db=db,
        recruiter_id=recruiter_id,
        job=job,
        voice_summary=voice_summary,
        gap_analysis=gap_analysis,
        selection_rounds=[],
        transcript=transcript,
    )
    persist_recruiter_intent_profile(db=db, recruiter_id=recruiter_id, profile=intent_profile)

    state = {
        "job_id": job_id,
        "recruiter_id": recruiter_id,
        "stage": "initial_job_understanding",
        "status": "active",
        "gap_analysis": gap_analysis,
        "recommended_questions": recommended_questions,
        "voice_summary": voice_summary,
        "transcript": transcript,
        "entities": entities or {},
        "intent_profile": summarize_intent_profile(intent_profile),
        "current_question_index": 0,
        "current_question": recommended_questions[0] if recommended_questions else "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_state(recruiter_id=recruiter_id, job_id=job_id, state=state)
    _persist_job_intelligence_snapshot(db=db, job_id=job_id, state=state)
    return state


def update_recruiter_interview_session(
    *,
    db: Session,
    recruiter_id: str,
    job_id: str,
    transcript: str,
    parsed_entities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = start_recruiter_interview_session(
        db=db,
        recruiter_id=recruiter_id,
        job_id=job_id,
        transcript=transcript,
        entities=parsed_entities or {},
    )
    job = JobRepository(db).get(job_id)
    if not job:
        raise ValueError("Job not found")

    gap_analysis = analyze_job_gap(job=job, voice_summary=transcript, entities=parsed_entities or state.get("entities") or {})
    questions = generate_recruiter_questions(
        gap_analysis=gap_analysis,
        job=job,
        voice_summary=transcript,
        max_questions=7,
    )
    intent_profile = build_recruiter_intent_profile(
        db=db,
        recruiter_id=recruiter_id,
        job=job,
        voice_summary=transcript,
        gap_analysis=gap_analysis,
        selection_rounds=[],
        transcript=transcript,
    )
    persist_recruiter_intent_profile(db=db, recruiter_id=recruiter_id, profile=intent_profile)

    state.update(
        {
            "stage": "dynamic_questioning" if questions else "intent_refinement",
            "gap_analysis": gap_analysis,
            "recommended_questions": questions,
            "transcript": transcript,
            "voice_summary": transcript or state.get("voice_summary", ""),
            "intent_profile": summarize_intent_profile(intent_profile),
            "current_question_index": min(int(state.get("current_question_index") or 0), max(0, len(questions) - 1)),
            "current_question": questions[min(int(state.get("current_question_index") or 0), max(0, len(questions) - 1))] if questions else "",
            "status": "active",
        }
    )
    final_state = _save_state(recruiter_id=recruiter_id, job_id=job_id, state=state)
    _persist_job_intelligence_snapshot(db=db, job_id=job_id, state=final_state)
    return final_state


def advance_recruiter_interview_stage(
    *,
    db: Session,
    recruiter_id: str,
    job_id: str,
) -> dict[str, Any]:
    state = _load_state(recruiter_id=recruiter_id, job_id=job_id)
    if not state:
        state = start_recruiter_interview_session(db=db, recruiter_id=recruiter_id, job_id=job_id)

    stage_order = [
        "initial_job_understanding",
        "gap_analysis",
        "dynamic_questioning",
        "intent_refinement",
        "final_profile_generation",
    ]
    current_stage = state.get("stage", "initial_job_understanding")
    try:
        stage_index = stage_order.index(current_stage)
    except ValueError:
        stage_index = 0

    next_stage = stage_order[min(stage_index + 1, len(stage_order) - 1)]
    state["stage"] = next_stage

    if next_stage == "final_profile_generation":
        job = JobRepository(db).get(job_id)
        if job:
            intent_profile = build_recruiter_intent_profile(
                db=db,
                recruiter_id=recruiter_id,
                job=job,
                voice_summary=state.get("voice_summary", ""),
                gap_analysis=state.get("gap_analysis") or {},
                selection_rounds=[],
                transcript=state.get("transcript", ""),
            )
            persist_recruiter_intent_profile(db=db, recruiter_id=recruiter_id, profile=intent_profile)
            state["intent_profile"] = summarize_intent_profile(intent_profile)
        state["status"] = "completed"

    final_state = _save_state(recruiter_id=recruiter_id, job_id=job_id, state=state)
    _persist_job_intelligence_snapshot(db=db, job_id=job_id, state=final_state)
    return final_state


def build_recruiter_interview_response(*, state: dict[str, Any] | None) -> dict[str, Any]:
    payload = build_state_response(state)
    payload["stage_summary"] = {
        "initial_job_understanding": "Understand the structured job request and context.",
        "gap_analysis": "Identify missing, ambiguous, and low-confidence inputs.",
        "dynamic_questioning": "Ask the recruiter only the highest-signal follow-ups.",
        "intent_refinement": "Merge job form, voice input, and history into a recruiter profile.",
        "final_profile_generation": "Persist the intent profile and prepare preference rounds.",
    }.get(payload.get("stage"), "")
    return payload
