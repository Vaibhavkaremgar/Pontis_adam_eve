from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.repositories import CandidateSelectionSessionRepository, JobRepository
from app.services.candidate_service import build_selection_candidate_snapshot
from app.services.job_gap_analysis_service import analyze_job_gap
from app.services.llm_service import generate
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
from app.services.prompt_sanitizer import sanitize_prompt_block

logger = logging.getLogger(__name__)

_STATE_PREFIX = "pontis:recruiter-preference-round:"
_STATE_TTL_SECONDS = 24 * 60 * 60


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _compat_get(data: Any, key: str, default: Any = None) -> Any:
    if isinstance(data, dict):
        return data.get(key, default)
    return getattr(data, key, default)


def _normalize_text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _normalize_text(value)
    if isinstance(value, (int, float, bool)):
        return _normalize_text(value)
    if isinstance(value, dict):
        for key in ("text", "label", "title", "name", "role", "value", "summary", "description"):
            normalized = _normalize_text_value(value.get(key))
            if normalized:
                return normalized
        flattened = [_normalize_text_value(item) for item in value.values()]
        return ", ".join(item for item in flattened if item)
    if isinstance(value, list):
        flattened = [_normalize_text_value(item) for item in value]
        return ", ".join(item for item in flattened if item)
    return _normalize_text(str(value))


def _normalize_text_list(value: Any) -> list[str]:
    collected: list[str] = []

    def visit(item: Any) -> None:
        if item is None:
            return
        if isinstance(item, list):
            for nested in item:
                visit(nested)
            return
        if isinstance(item, dict):
            for key in ("text", "label", "title", "name", "role", "value", "skill", "strength", "signal", "tradeoff"):
                normalized = _normalize_text_value(item.get(key))
                if normalized:
                    collected.append(normalized)
                    return
            for nested in item.values():
                visit(nested)
            return

        normalized = _normalize_text_value(item)
        if not normalized:
            return
        if isinstance(item, str):
            parts = [part.strip() for part in re.split(r"[;,|/]\s*", normalized) if part.strip()]
            if len(parts) > 1:
                for part in parts:
                    visit(part)
                return
        collected.append(normalized)

    visit(value)
    return _ordered_unique(collected)


def _text_field(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _compat_get(data, key)
        normalized = _normalize_text_value(value)
        if normalized:
            return normalized
    return ""


def _list_field(data: dict[str, Any], *keys: str) -> list[str]:
    collected: list[str] = []
    for key in keys:
        value = _compat_get(data, key)
        if value is None:
            continue
        collected.extend(_normalize_text_list(value))
    return _ordered_unique(collected)


def _compact_archetype_label(value: str, fallback: str) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        return fallback
    words = normalized.split()
    if len(words) <= 4:
        return normalized
    return " ".join(words[:4]).strip()


def _candidate_headline_from_option(option: dict[str, Any], *, fallback: str) -> str:
    return _compact_archetype_label(
        _text_field(
            option,
            "candidate_headline",
            "candidateHeadline",
            "headline",
            "title",
            "name",
            "role",
            "label",
        ),
        fallback,
    )


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


def _stable_calibration_set_id(round_index: int) -> str:
    return f"calibration-set-{max(1, int(round_index or 1))}"


def _stable_archetype_id(calibration_set_id: str, option_index: int) -> str:
    safe_set_id = _normalize_text(calibration_set_id) or _stable_calibration_set_id(1)
    return f"{safe_set_id}-archetype-{max(1, int(option_index or 0) + 1)}"


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, set):
        return [_json_safe_value(item) for item in sorted(value, key=lambda item: str(item))]
    if hasattr(value, "__dict__"):
        return _json_safe_value(vars(value))
    if hasattr(value, "dict") and callable(getattr(value, "dict")):
        try:
            return _json_safe_value(value.dict())  # type: ignore[call-arg]
        except Exception:
            pass
    if hasattr(value, "model_dump") and callable(getattr(value, "model_dump")):
        try:
            return _json_safe_value(value.model_dump())  # type: ignore[call-arg]
        except Exception:
            pass
    return _normalize_text(value)


def _calibration_state_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    snapshot = _json_safe_value(state)
    if not isinstance(snapshot, dict):
        snapshot = {}
    snapshot["rounds"] = _json_safe_value(list(state.get("rounds") or []))
    snapshot["archetype_sets"] = _json_safe_value(list(state.get("archetype_sets") or []))
    snapshot["archetype_pool"] = _json_safe_value(list(state.get("archetype_pool") or []))
    snapshot["history"] = _json_safe_value(list(state.get("history") or []))
    snapshot["selected_archetype_ids"] = list(state.get("selected_archetype_ids") or [])
    snapshot["selected_candidate_ids"] = list(state.get("selected_candidate_ids") or [])
    snapshot["rejected_candidate_ids"] = list(state.get("rejected_candidate_ids") or [])
    snapshot["recommended_questions"] = list(state.get("recommended_questions") or [])
    snapshot["current_pair"] = _json_safe_value(dict(state.get("current_pair") or {}))
    snapshot["telemetry"] = _json_safe_value(dict(state.get("telemetry") or {}))
    snapshot["intent_profile"] = _json_safe_value(dict(state.get("intent_profile") or {}))
    snapshot["gap_analysis"] = _json_safe_value(dict(state.get("gap_analysis") or {}))
    snapshot["calibration_set_ids"] = [str(item.get("calibration_set_id") or "").strip() for item in list(state.get("archetype_sets") or []) if str(item.get("calibration_set_id") or "").strip()]
    return snapshot


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
    job_skills = _compat_get(job, "skills_required")
    if isinstance(job_skills, list):
        required.extend(str(item) for item in job_skills)
    description = _normalize_text(_compat_get(job, "description", ""))
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
    job_title = _normalize_text(_compat_get(job, "title", "") or "Candidate")
    job_location = _normalize_text(_compat_get(job, "location", "") or "Remote")
    job_description = _normalize_text(_compat_get(job, "description", ""))
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
        engineeringScore=round(min(1.0, len(shared_skills) / max(1, len(skills))), 3),
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
        id=f"synthetic-{mode}-{_normalize_text(_compat_get(job, 'id', 'job'))}-{index + 1}",
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


def _real_candidate_pool_snapshot(
    *,
    db: Session,
    job: Any,
) -> list[dict[str, Any]]:
    mode = _job_mode(job)
    try:
        candidates = build_selection_candidate_snapshot(
            db=db,
            job_id=str(getattr(job, "id", "") or ""),
            mode=mode,
            refresh=True,
            limit=12,
        )
    except Exception as exc:
        logger.warning(
            "real_selection_snapshot_failed job_id=%s error=%s",
            getattr(job, "id", ""),
            str(exc),
        )
        return []

    snapshot = [candidate.model_dump(exclude_none=True) for candidate in candidates]
    if len(snapshot) < 6:
        return []

    logger.info(
        "real_selection_snapshot_captured job_id=%s count=%s mode=%s",
        getattr(job, "id", ""),
        len(snapshot),
        mode,
    )
    return snapshot


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


def _persist_selection_snapshot(*, db: Session, job_id: str, state: dict[str, Any]) -> None:
    job_repo = JobRepository(db)
    job = job_repo.get(job_id)
    if not job:
        return

    structured = dict(job.structured_data or {})
    candidate_pool = list(state.get("candidate_pool") or [])
    structured["selectionSnapshot"] = {
        "source": state.get("candidate_source", "synthetic"),
        "candidateCount": len(candidate_pool),
        "candidateIds": [str(candidate.get("id") or "").strip() for candidate in candidate_pool if str(candidate.get("id") or "").strip()],
        "batchPlan": [list(pair.get("candidate_ids") or []) for pair in list(state.get("rounds") or [])],
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    job_repo.update_structured_fields(job_id=job_id, structured_data=structured)


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
    candidate_pool = _real_candidate_pool_snapshot(
        db=db,
        job=job,
    )
    candidate_source = "real" if len(candidate_pool) >= 6 else "synthetic"
    if candidate_source == "synthetic":
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
        "candidate_source": candidate_source,
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
    _persist_selection_snapshot(db=db, job_id=job_id, state=state)
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

    try:
        update_recruiter_preferences(
            db,
            recruiter_id,
            selected_candidate,
            rejected_candidates,
            signal_multiplier=1.1 + (current_round_index * 0.25),
        )
    except Exception as exc:
        logger.warning(
            "recruiter_preference_update_failed recruiter_id=%s job_id=%s round_index=%s error=%s",
            recruiter_id,
            job_id,
            current_round_index,
            str(exc),
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
    try:
        state = _live_profile_update(
            db=db,
            recruiter_id=recruiter_id,
            job=job,
            state=state,
            selected_candidate=selected_candidate,
            rejected_candidates=rejected_candidates,
            round_index=current_round_index,
        )
    except Exception as exc:
        logger.warning(
            "recruiter_live_profile_update_failed recruiter_id=%s job_id=%s round_index=%s error=%s",
            recruiter_id,
            job_id,
            current_round_index,
            str(exc),
        )
        state["telemetry"] = {
            **dict(state.get("telemetry") or {}),
            "preference_learning_gain": round(min(1.0, (current_round_index / 3.0) * 0.25 + len(previous_rounds) * 0.03), 4),
        }

    next_round_index = current_round_index + 1
    if next_round_index <= 3:
        try:
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
        except Exception as exc:
            logger.warning(
                "recruiter_next_pair_generation_failed recruiter_id=%s job_id=%s round_index=%s error=%s",
                recruiter_id,
                job_id,
                next_round_index,
                str(exc),
            )
            next_pair = {}
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
    state["candidate_source"] = state.get("candidate_source", "synthetic")
    state["vetting_mode"] = _job_mode(job)
    state["telemetry"] = {
        **dict(state.get("telemetry") or {}),
        "recruiter_preference_confidence": round(min(1.0, float(intent_profile.get("history_signal_strength") or 0.0) + len(selection_rounds) * 0.12), 4),
    }
    final_state = _save_state(recruiter_id=recruiter_id, job_id=job_id, state=state)
    _persist_selection_snapshot(db=db, job_id=job_id, state=final_state)
    return final_state


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
        "candidate_source": state.get("candidate_source", "real" if len(state.get("candidate_pool") or []) >= 6 else "synthetic"),
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


_CALIBRATION_STATE_PREFIX = "pontis:recruiter-preference-calibration:"
_CALIBRATION_STATE_TTL_SECONDS = 24 * 60 * 60
_CALIBRATION_SET_COUNT = 3
_CALIBRATION_OPTIONS_PER_SET = 2


def _calibration_state_key(*, recruiter_id: str, job_id: str) -> str:
    return f"{_CALIBRATION_STATE_PREFIX}{_normalize_text(recruiter_id)}:{_normalize_text(job_id)}"


def _load_calibration_state(*, recruiter_id: str, job_id: str) -> dict[str, Any] | None:
    redis = get_redis()
    if redis is None:
        return None
    raw = redis.get(_calibration_state_key(recruiter_id=recruiter_id, job_id=job_id))
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _save_calibration_state(*, recruiter_id: str, job_id: str, state: dict[str, Any]) -> dict[str, Any]:
    redis = get_redis()
    state = _calibration_state_snapshot(state)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    if redis is not None:
        try:
            redis.set(_calibration_state_key(recruiter_id=recruiter_id, job_id=job_id), json.dumps(state), ex=_CALIBRATION_STATE_TTL_SECONDS)
        except Exception:
            pass
    return state


def _load_calibration_state_from_job(*, db: Session, recruiter_id: str, job_id: str) -> dict[str, Any] | None:
    job = JobRepository(db).get(job_id)
    if not job:
        return None

    structured = dict(job.structured_data or {})
    calibration = structured.get("recruiterCalibration")
    if not isinstance(calibration, dict):
        return None

    snapshot = calibration.get("state")
    if not isinstance(snapshot, dict):
        return None

    snapshot_recruiter_id = _normalize_text(snapshot.get("recruiter_id") or snapshot.get("recruiterId"))
    if snapshot_recruiter_id and snapshot_recruiter_id != _normalize_text(recruiter_id):
        return None

    restored = dict(snapshot)
    restored.setdefault("job_id", job_id)
    restored.setdefault("recruiter_id", _normalize_text(recruiter_id))
    restored.setdefault("archetype_sets", [])
    restored.setdefault("rounds", [])
    restored.setdefault("history", [])
    restored.setdefault("selected_candidate_ids", [])
    restored.setdefault("selected_archetype_ids", [])
    restored.setdefault("rejected_candidate_ids", [])
    restored.setdefault("telemetry", {})
    restored.setdefault("intent_profile", {})
    restored.setdefault("gap_analysis", {})
    restored.setdefault("recommended_questions", [])
    restored.setdefault("current_pair", {})
    restored.setdefault("orchestration_session_id", _normalize_text(calibration.get("orchestrationSessionId") or calibration.get("orchestration_session_id") or snapshot.get("orchestration_session_id") or snapshot.get("orchestrationSessionId")))
    return restored


def _job_text_field(job: Any, *keys: str) -> str:
    for key in keys:
        value = _compat_get(job, key, "")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _job_list_values(job: Any, *keys: str) -> list[str]:
    values: Any = None
    for key in keys:
        value = _compat_get(job, key, None)
        if isinstance(value, list):
            values = value
            break
    if not isinstance(values, list):
        return []
    return [_normalize_text(value) for value in values if _normalize_text(value)]


def _archetype_prompt(*, job: Any, voice_summary: str, gap_analysis: dict[str, Any], intent_profile: dict[str, Any]) -> str:
    missing_fields = ", ".join(gap_analysis.get("missing_fields") or []) or "none"
    ambiguous_fields = ", ".join(gap_analysis.get("ambiguous_fields") or []) or "none"
    preferred_skills = ", ".join(intent_profile.get("preferred_skills") or []) or "none"
    required_skills = ", ".join(intent_profile.get("required_skills") or []) or "none"
    culture_preferences = ", ".join(intent_profile.get("culture_preferences") or []) or "none"
    company_stage = _text_field(job, "company_stage", "companyStage", "stage", "company_type") or "unknown"
    role_seniority = _text_field(job, "experience_level", "experienceRequired", "seniority") or "unknown"

    return (
        "You are generating ideal candidate resume profiles for recruiter calibration and X-Ray sourcing.\n"
        "Do not create personality personas, abstract archetypes, or vague dimensions.\n"
        "Every profile should feel like a believable resume a recruiter would actually source.\n"
        "Generate exactly 3 sets with 2 archetypes in each set.\n"
        "Each profile must be short, distinct, and ready to drive sourcing queries.\n"
        "Rules:\n"
        "- Return ONLY valid JSON.\n"
        "- Do NOT invent real candidates, names, companies, or emails.\n"
        "- Each set must contain exactly 2 archetypes.\n"
        "- Each profile must include: profile_title, resume_summary, typical_background, strongest_skills, typical_companies, engineering_style, ownership_pattern, tradeoff, why_recruiter_would_prefer_them.\n"
        "- Keep the profile title to 2 to 6 words max.\n"
        "- Keep the profile title role-like, sourcing-oriented, and specific, e.g. 'Scale-Stage Backend Engineer' or 'Startup Generalist Engineer'.\n"
        "- Make the resume summary short, concrete, and resume-like.\n"
        "- If the recruiter gave explicit years of experience in the voice intake or job requirements, stay close to that band while varying the profile shape.\n"
        "- Keep the typical background short and recruiter-facing.\n"
        "- Strongest_skills must include the requested stack or close equivalents from the job and voice intake.\n"
        "- engineering_style, ownership_pattern, tradeoff, and why_recruiter_would_prefer_them should be concise notes, not paragraphs.\n"
        "- Use the job title, voice summary, company stage, hiring intent, and technical stack to shape the profiles.\n"
        "- Prefer two clearly differentiated profiles per set, such as scale-stage systems owner versus startup generalist or platform reliability engineer versus product-minded engineer.\n"
        "- Keep the set theme grounded in the real hiring decision being made.\n"
        "- Return schema: {\"sets\": [{\"round_index\": 1, \"set_title\": \"...\", \"set_theme\": \"...\", \"archetypes\": [{...}, {...}]}]}\n"
        "- Preserve compatibility fields when possible, but the core content must read like resume profiles rather than archetypes.\n\n"
        f"{sanitize_prompt_block('Job title', _job_text_field(job, 'title'), max_length=200)}\n"
        f"{sanitize_prompt_block('Job description', _job_text_field(job, 'description'), max_length=2200)}\n"
        f"{sanitize_prompt_block('Location', _job_text_field(job, 'location'), max_length=160)}\n"
        f"{sanitize_prompt_block('Company stage', company_stage, max_length=160)}\n"
        f"{sanitize_prompt_block('Seniority', role_seniority, max_length=160)}\n"
        f"{sanitize_prompt_block('Compensation', _job_text_field(job, 'compensation', 'salary_range'), max_length=160)}\n"
        f"{sanitize_prompt_block('Work authorization', _job_text_field(job, 'work_authorization', 'workAuthorization'), max_length=160)}\n"
        f"{sanitize_prompt_block('Experience', _job_text_field(job, 'experience_level', 'experienceRequired', 'seniority'), max_length=160)}\n"
        f"{sanitize_prompt_block('Skills', ', '.join(_job_list_values(job, 'skills_required', 'skills')), max_length=1200)}\n"
        f"{sanitize_prompt_block('Responsibilities', ', '.join(_job_list_values(job, 'responsibilities')), max_length=1200)}\n"
        f"{sanitize_prompt_block('Voice summary', voice_summary, max_length=1200)}\n"
        f"{sanitize_prompt_block('Missing fields', missing_fields, max_length=800)}\n"
        f"{sanitize_prompt_block('Ambiguous fields', ambiguous_fields, max_length=800)}\n"
        f"{sanitize_prompt_block('Preferred skills', preferred_skills, max_length=800)}\n"
        f"{sanitize_prompt_block('Required skills', required_skills, max_length=800)}\n"
        f"{sanitize_prompt_block('Culture preferences', culture_preferences, max_length=800)}\n"
    )


def _normalize_archetype_field(value: Any) -> str:
    return _normalize_text_value(value)


def _normalize_archetype_option(
    option: dict[str, Any],
    *,
    job: Any,
    set_index: int,
    option_index: int,
    calibration_set_id: str,
) -> dict[str, Any]:
    set_suffix = f"r{set_index + 1}-{chr(ord('a') + option_index)}"
    fallback_headline = f"{_job_text_field(job, 'title') or 'Candidate'} {set_suffix}".strip()
    profile_title = _normalize_archetype_field(
        option.get("profile_title")
        or option.get("profileTitle")
        or option.get("candidate_headline")
        or option.get("candidateHeadline")
        or option.get("headline")
        or option.get("title")
        or option.get("name")
        or option.get("role")
    ) or _candidate_headline_from_option(option, fallback=fallback_headline)
    resume_summary = _text_field(
        option,
        "resume_summary",
        "resumeSummary",
        "experience_snapshot",
        "experienceSnapshot",
        "summary",
        "experience",
        "background",
    )
    typical_background = _text_field(
        option,
        "typical_background",
        "typicalBackground",
        "career_pattern",
        "careerPattern",
        "career_arc",
        "careerArc",
        "pattern",
        "trajectory",
    )
    strongest_skills = _list_field(
        option,
        "strongest_skills",
        "strongestSkills",
        "technical_strengths",
        "technicalStrengths",
        "strengths",
        "skills",
    )
    typical_companies = _list_field(
        option,
        "typical_companies",
        "typicalCompanies",
        "current_company",
        "currentCompany",
        "company",
        "companies",
    )
    engineering_style = _text_field(
        option,
        "engineering_style",
        "engineeringStyle",
        "work_style",
        "workStyle",
        "execution_style",
        "executionStyle",
    )
    ownership_pattern = _text_field(
        option,
        "ownership_pattern",
        "ownershipPattern",
        "ownership_style",
        "ownershipStyle",
        "ownership_level",
        "ownershipLevel",
    )
    tradeoff = _text_field(option, "tradeoff", "tradeOff", "hiring_tradeoffs", "hiringTradeoffs", "tradeoffs", "trade_offs")
    why_recruiter_would_prefer_them = _text_field(
        option,
        "why_recruiter_would_prefer_them",
        "whyRecruiterWouldPreferThem",
        "fit_note",
        "fitNote",
    )
    leadership_profile = _list_field(option, "leadership_profile", "leadershipProfile", "leadership_signals", "leadershipSignals")
    ideal_environment = _text_field(option, "ideal_environment", "idealEnvironment")
    communication_style = _normalize_archetype_field(option.get("communication_style") or option.get("communicationStyle"))
    risk_tolerance = _normalize_archetype_field(option.get("risk_tolerance") or option.get("riskTolerance"))
    headline_role = _normalize_archetype_field(option.get("headline_role") or option.get("headlineRole") or option.get("role") or option.get("title") or _job_text_field(job, "title") or "the role")
    job_title = _job_text_field(job, "title") or "the role"

    if not resume_summary:
        resume_summary = (
            f"{profile_title} with experience across {', '.join(strongest_skills[:4]) or 'the core stack'}."
        )
    if not typical_background:
        typical_background = f"Resume patterns shaped by {', '.join(strongest_skills[:4]) or job_title.lower()} and the hiring context."
    if not engineering_style:
        engineering_style = ownership_pattern or "Practical, delivery-minded engineering."
    if not ownership_pattern:
        ownership_pattern = "End-to-end ownership with a bias toward shipping."
    if not tradeoff:
        tradeoff = "Broader ownership over narrow specialization."
    if not why_recruiter_would_prefer_them:
        why_recruiter_would_prefer_them = f"Can own the {job_title.lower()} work with the right balance of speed and reliability."

    strengths = _ordered_unique([*strongest_skills, *leadership_profile, *[tradeoff]])
    skills = _ordered_unique([*strongest_skills, *strengths, *leadership_profile])
    archetype_id = _stable_archetype_id(calibration_set_id, option_index)
    summary = (
        f"{profile_title} is a believable ideal candidate resume profile for {job_title}. "
        f"Resume summary: {resume_summary or 'not specified'}. "
        f"Typical background: {typical_background or 'not specified'}."
    )
    if why_recruiter_would_prefer_them:
        summary = f"{summary} {why_recruiter_would_prefer_them}"

    fit_score = max(3.2, min(4.8, 4.6 - (set_index * 0.08) - (option_index * 0.05)))
    explanation = CandidateExplanation(
        semanticScore=round(fit_score / 5.0, 3),
        skillOverlap=min(1.0, len(strengths) / max(1, 6)),
        finalScore=round(fit_score / 5.0, 3),
        pdlRelevance=0.0,
        recencyScore=0.5,
        engineeringScore=min(1.0, len(strengths) / max(1, 8)),
        penalties={
            "semanticPenalty": 0.0,
            "missingSkillsPenalty": 0.0,
            "selectionPreferenceBonus": 0.0,
        },
        skillsMatched=strongest_skills[:4] or strengths[:4],
        experienceMatch="Preference calibration profile",
        candidateExperience="Preference calibration profile",
        jobExperience=_job_text_field(job, "experience_level", "experienceRequired", "seniority"),
        aiReasoning="Groq-generated ideal candidate resume profile used to calibrate recruiter taste before real sourcing begins.",
        sourceBreakdown={
            "vector": 0.0,
            "lexical": 0.0,
            "structured": 0.0,
            "recruiterPreference": 0.0,
            "freshness": 0.0,
            "selectionRound": 0.0,
            "voiceInterview": 0.0,
        },
    )
    return {
        "id": archetype_id,
        "archetype_id": archetype_id,
        "calibration_set_id": calibration_set_id,
        "calibrationSetId": calibration_set_id,
        "name": profile_title,
        "role": headline_role or profile_title,
        "company": _normalize_archetype_field(", ".join(typical_companies[:2]) if typical_companies else ""),
        "current_company": _normalize_archetype_field(typical_companies[0] if typical_companies else ""),
        "email": "",
        "isMockEmail": True,
        "headline": resume_summary or typical_background or option.get("set_title") or f"Profile for {job_title}",
        "location": _normalize_archetype_field(option.get("location") or option.get("current_location") or option.get("currentLocation") or _job_text_field(job, "location") or "Remote"),
        "yearsExperience": round(float(option.get("years_experience") or option.get("yearsExperience") or 0.0), 1) if str(option.get("years_experience") or option.get("yearsExperience") or "").strip() else 0.0,
        "skills": skills,
        "summary": summary,
        "education": [],
        "projects": [],
        "certifications": [],
        "companiesHistory": [],
        "domainExperience": [],
        "resumeText": "\n".join(
            [
                f"Profile title: {profile_title}",
                f"Resume summary: {resume_summary}",
                f"Typical background: {typical_background}",
                f"Strongest skills: {', '.join(strongest_skills)}",
                f"Typical companies: {', '.join(typical_companies)}",
                f"Engineering style: {engineering_style}",
                f"Ownership pattern: {ownership_pattern}",
                f"Tradeoff: {tradeoff}",
                f"Why recruiter would prefer them: {why_recruiter_would_prefer_them}",
                f"Ideal environment: {ideal_environment}",
                f"Communication style: {communication_style}",
                f"Risk tolerance: {risk_tolerance}",
                f"Leadership signals: {', '.join(leadership_profile)}",
            ]
        ).strip(),
        "profileData": {
            "source": "groq_candidate_profile_calibration",
            "isArchetype": True,
            "isCandidateProfile": True,
            "calibrationSetId": calibration_set_id,
            "calibration_set_id": calibration_set_id,
            "archetypeId": archetype_id,
            "archetype_id": archetype_id,
            "setIndex": set_index + 1,
            "optionIndex": option_index + 1,
            "setTitle": _normalize_archetype_field(option.get("set_title") or option.get("setTitle") or ""),
            "setTheme": _normalize_archetype_field(option.get("set_theme") or option.get("setTheme") or ""),
            "profileTitle": profile_title,
            "profile_title": profile_title,
            "candidateHeadline": profile_title,
            "candidate_headline": profile_title,
            "title": profile_title,
            "headlineRole": headline_role,
            "headline_role": headline_role,
            "currentCompany": _normalize_archetype_field(typical_companies[0] if typical_companies else ""),
            "current_company": _normalize_archetype_field(typical_companies[0] if typical_companies else ""),
            "typicalCompanies": typical_companies,
            "typical_companies": typical_companies,
            "location": _normalize_archetype_field(option.get("location") or option.get("current_location") or option.get("currentLocation") or _job_text_field(job, "location") or "Remote"),
            "yearsExperience": round(float(option.get("years_experience") or option.get("yearsExperience") or 0.0), 1) if str(option.get("years_experience") or option.get("yearsExperience") or "").strip() else 0.0,
            "resumeSummary": resume_summary,
            "resume_summary": resume_summary,
            "experienceSnapshot": resume_summary,
            "experience_snapshot": resume_summary,
            "typicalBackground": typical_background,
            "typical_background": typical_background,
            "careerPattern": typical_background,
            "career_pattern": typical_background,
            "strongestSkills": strongest_skills,
            "strongest_skills": strongest_skills,
            "technicalStrengths": strongest_skills,
            "technical_strengths": strongest_skills,
            "strengths": strengths,
            "engineeringStyle": engineering_style,
            "engineering_style": engineering_style,
            "workStyle": engineering_style,
            "work_style": engineering_style,
            "ownershipPattern": ownership_pattern,
            "ownership_pattern": ownership_pattern,
            "ownershipStyle": ownership_pattern,
            "ownership_style": ownership_pattern,
            "ownershipLevel": ownership_pattern,
            "idealEnvironment": ideal_environment,
            "ideal_environment": ideal_environment,
            "executionStyle": engineering_style,
            "execution_style": engineering_style,
            "leadershipProfile": leadership_profile,
            "leadership_profile": leadership_profile,
            "leadershipSignals": leadership_profile,
            "leadership_signals": leadership_profile,
            "hiringTradeoffs": [tradeoff] if tradeoff else [],
            "hiring_tradeoffs": [tradeoff] if tradeoff else [],
            "tradeoff": tradeoff,
            "communicationStyle": communication_style,
            "communication_style": communication_style,
            "riskTolerance": risk_tolerance,
            "risk_tolerance": risk_tolerance,
            "fitNote": why_recruiter_would_prefer_them,
            "fit_note": why_recruiter_would_prefer_them,
            "whyRecruiterWouldPreferThem": why_recruiter_would_prefer_them,
            "why_recruiter_would_prefer_them": why_recruiter_would_prefer_them,
        },
        "fitScore": round(fit_score, 2),
        "decision": "strong_match" if fit_score >= 4.1 else "potential",
        "explanation": explanation,
        "strategy": "CALIBRATION",
        "status": "new",
        "outreachStatus": "pending",
        "exportStatus": "pending",
        "ats_export_status": "not_sent",
    }


def _fallback_archetype_sets(*, job: Any) -> list[dict[str, Any]]:
    job_title = _job_text_field(job, "title") or "the role"
    job_description = _job_text_field(job, "description")
    job_location = _job_text_field(job, "location")
    job_experience = _job_text_field(job, "experience_level", "experienceRequired", "seniority")
    job_stage = _job_text_field(job, "company_stage", "companyStage", "stage", "company_type")
    job_skills = _ordered_unique(_job_list_values(job, "skills_required", "skills"))
    skill_phrase = ", ".join(job_skills[:6]) if job_skills else job_title.lower()
    stage_phrase = job_stage or "a growth-stage team"
    location_phrase = job_location or "the target market"
    role_tokens = [
        token
        for token in re.split(r"[^a-zA-Z0-9+.#-]+", job_title)
        if token and token.lower() not in {"senior", "sr", "staff", "principal", "lead", "junior", "jr", "mid", "level"}
    ]
    base_role = _compact_archetype_label(" ".join(role_tokens[:3]) or job_title, "Engineer")
    profile_titles = [
        f"Scale-Stage {base_role}",
        f"Startup Generalist {base_role}",
        f"Platform & Reliability {base_role}",
        f"Product-Minded {base_role}",
        f"Domain Specialist {base_role}",
        f"Staff Systems {base_role}",
    ]
    summaries = [
        f"{job_experience or '5-7 years'} building production backend systems at high-growth companies, with a strong bias toward scaling and operational stability.",
        f"{job_experience or '4-7 years'} in early-stage teams where the engineer owned APIs, deployments, debugging, and product iteration without much hand-holding.",
        f"{job_experience or '6-9 years'} focused on reliability, observability, queues, CI/CD, and platform work that keeps systems stable under load.",
        f"{job_experience or '4-8 years'} building customer-facing product features while staying close to product decisions and rapid delivery cycles.",
        f"{job_experience or '6-10 years'} in domains where the engineer had to learn a business area deeply and turn that context into technical decisions.",
        f"{job_experience or '8+ years'} leading technical direction across services, architecture, and cross-team execution for a larger product surface.",
    ]
    backgrounds = [
        f"Scaled APIs, async jobs, PostgreSQL, Redis, and AWS while working through performance bottlenecks in a high-growth product team.",
        "Early-stage startup background with broad ownership across feature work, deployment issues, infra debugging, and product changes.",
        "Infrastructure-heavy background across monitoring, alerting, build pipelines, queues, service health, and reliability work.",
        "Product-led backend background with frequent collaboration with product, design, and customer-facing teams.",
        "Deep domain background in one business area, using that context to shape systems and product decisions.",
        "Staff-level background across system design, architecture reviews, and multi-team technical alignment.",
    ]
    strongest_skills_pool = [
        _ordered_unique([*(job_skills[:4] or [base_role]), "PostgreSQL", "Redis", "AWS"]),
        _ordered_unique([*(job_skills[:4] or [base_role]), "Rapid shipping", "Deployment debugging", "API ownership"]),
        _ordered_unique([*(job_skills[:4] or [base_role]), "Observability", "CI/CD", "Queue reliability"]),
        _ordered_unique([*(job_skills[:4] or [base_role]), "Product judgment", "Feature delivery", "Cross-functional execution"]),
        _ordered_unique([*(job_skills[:4] or [base_role]), "Domain modeling", "Workflow design", "Technical tradeoffs"]),
        _ordered_unique([*(job_skills[:4] or [base_role]), "Architecture", "System design", "Multi-team coordination"]),
    ]
    typical_companies_pool = [
        ["Razorpay", "Meesho", "Swiggy", "PhonePe", "Groww"],
        ["YC-backed startups", "seed-stage SaaS teams", "lean product startups"],
        ["Platform engineering orgs", "infra teams", "DevOps-heavy product companies"],
        ["Product-led SaaS teams", "consumer internet companies", "feature-heavy product orgs"],
        ["Domain-heavy fintech teams", "logistics platforms", "workflow SaaS companies"],
        ["Large product orgs", "multi-team platform groups", "complex service environments"],
    ]
    engineering_styles = [
        "Production-minded and systems-aware.",
        "Fast-moving, adaptable, and execution-heavy.",
        "Reliability-first with a strong platform mindset.",
        "Pragmatic, product-aware, and delivery-focused.",
        "Context-rich and thoughtful about the business domain.",
        "Architectural, deliberate, and cross-team oriented.",
    ]
    ownership_patterns = [
        "Owns services end to end and cares about scaling them safely.",
        "Takes broad ownership and keeps momentum high even when ambiguity is heavy.",
        "Owns operational stability, tooling, and the plumbing that keeps teams shipping.",
        "Stays close to the product surface and turns product decisions into shipped code.",
        "Owns a domain deeply and turns that into clean technical decisions.",
        "Owns technical direction across services and keeps alignment high across teams.",
    ]
    tradeoffs = [
        "Less experimentation, stronger systems reliability.",
        "Broader ownership over deep specialization.",
        "Less product proximity, stronger infrastructure maturity.",
        "Less infrastructure depth, stronger product judgment.",
        "Less breadth across unrelated domains, stronger context inside one business area.",
        "Less hands-on feature throughput, stronger architectural leverage.",
    ]
    recruiter_reasons = [
        "Can immediately own and scale backend systems.",
        "Can move quickly and handle ambiguity well.",
        "Great fit when the team needs stability and operational maturity.",
        "Great when the role needs someone close to product decisions and execution.",
        "Useful when the team wants a deep specialist in one business domain.",
        "Ideal when the team needs technical leadership across multiple engineers and services.",
    ]
    locations = [
        job_location or "Remote",
        "Remote",
        "Bengaluru, India",
        "Hyderabad, India",
        "Pune, India",
        "Chennai, India",
    ]
    years_pool = [7.0, 5.5, 8.5, 6.0, 9.0, 10.0]
    sets: list[dict[str, Any]] = []
    for index in range(_CALIBRATION_SET_COUNT):
        calibration_set_id = _stable_calibration_set_id(index + 1)
        option_indices = [index * 2, index * 2 + 1]
        set_titles = [
            "Scale vs startup breadth",
            "Platform reliability vs product closeness",
            "Domain depth vs staff-level systems leadership",
        ]
        set_themes = [
            f"Choose between a scale-stage operator and a startup generalist for {skill_phrase}.",
            f"Choose between operational maturity and product proximity in {skill_phrase}.",
            f"Choose between deep domain expertise and broader architectural leverage for {skill_phrase}.",
        ]
        archetypes: list[dict[str, Any]] = []
        for option_index, pool_index in enumerate(option_indices):
            profile_title = profile_titles[pool_index]
            strongest_skills = strongest_skills_pool[pool_index]
            typical_companies = typical_companies_pool[pool_index]
            profile_option = {
                "profile_title": profile_title,
                "resume_summary": summaries[pool_index],
                "typical_background": backgrounds[pool_index],
                "strongest_skills": strongest_skills,
                "typical_companies": typical_companies,
                "engineering_style": engineering_styles[pool_index],
                "ownership_pattern": ownership_patterns[pool_index],
                "tradeoff": tradeoffs[pool_index],
                "why_recruiter_would_prefer_them": recruiter_reasons[pool_index],
                "leadership_profile": [
                    "handles ownership well",
                    "communicates tradeoffs clearly",
                ]
                if pool_index in {0, 3}
                else [
                    "moves quickly",
                    "works well with ambiguity",
                ]
                if pool_index in {1, 4}
                else [
                    "keeps systems stable",
                    "aligns teams",
                ],
                "location": locations[pool_index],
                "years_experience": years_pool[pool_index],
            }
            profile_option.update(
                {
                    "headline_role": base_role,
                    "current_location": locations[pool_index],
                }
            )
            archetypes.append(profile_option)

        title = set_titles[index]
        theme = set_themes[index]
        sets.append(
            {
                "round_index": index + 1,
                "calibration_set_id": calibration_set_id,
                "calibrationSetId": calibration_set_id,
                "set_title": title,
                "set_theme": theme,
                "archetypes": [
                    _normalize_archetype_option(
                        {**archetype, "set_title": title, "set_theme": theme},
                        job=job,
                        set_index=index,
                        option_index=option_index,
                        calibration_set_id=calibration_set_id,
                    )
                    for option_index, archetype in enumerate(archetypes[:_CALIBRATION_OPTIONS_PER_SET])
                ],
            }
        )
    return sets


def _generate_archetype_sets(*, job: Any, voice_summary: str, gap_analysis: dict[str, Any], intent_profile: dict[str, Any]) -> list[dict[str, Any]]:
    prompt = _archetype_prompt(job=job, voice_summary=voice_summary, gap_analysis=gap_analysis, intent_profile=intent_profile)
    try:
        payload = generate(prompt, expect_json=True)
    except Exception as exc:
        logger.warning("recruiter_candidate_profile_generation_failed error=%s", str(exc))
        payload = {}

    raw_sets: list[Any] = []
    if isinstance(payload, dict):
        raw_sets = list(payload.get("sets") or payload.get("profile_sets") or payload.get("profileSets") or payload.get("archetypeSets") or [])
    elif isinstance(payload, list):
        raw_sets = payload

    normalized_sets: list[dict[str, Any]] = []
    for set_index, raw_set in enumerate(raw_sets[:_CALIBRATION_SET_COUNT]):
        if not isinstance(raw_set, dict):
            continue
        archetypes = raw_set.get("archetypes") or raw_set.get("profiles") or raw_set.get("candidate_profiles") or raw_set.get("items") or []
        if not isinstance(archetypes, list):
            continue
        calibration_set_id = _stable_calibration_set_id(int(raw_set.get("round_index") or set_index + 1))
        normalized_sets.append(
            {
                "round_index": int(raw_set.get("round_index") or set_index + 1),
                "calibration_set_id": calibration_set_id,
                "calibrationSetId": calibration_set_id,
                "set_title": _normalize_archetype_field(raw_set.get("set_title") or raw_set.get("title") or f"Calibration set {set_index + 1}"),
                "set_theme": _normalize_archetype_field(raw_set.get("set_theme") or raw_set.get("theme") or ""),
                "archetypes": [
                    _normalize_archetype_option(
                        {
                            **(archetype if isinstance(archetype, dict) else {}),
                            "set_title": raw_set.get("set_title") or raw_set.get("title") or "",
                            "set_theme": raw_set.get("set_theme") or raw_set.get("theme") or "",
                        },
                        job=job,
                        set_index=set_index,
                        option_index=option_index,
                        calibration_set_id=calibration_set_id,
                    )
                    for option_index, archetype in enumerate(archetypes[:_CALIBRATION_OPTIONS_PER_SET])
                    if isinstance(archetype, dict)
                ],
            }
        )

    if len(normalized_sets) < _CALIBRATION_SET_COUNT:
        fallback_sets = _fallback_archetype_sets(job=job)
        for index in range(len(normalized_sets), _CALIBRATION_SET_COUNT):
            if index < len(fallback_sets):
                normalized_sets.append(fallback_sets[index])

    logger.info(
        "recruiter_candidate_profile_sets_generated source=%s count=%s",
        "groq" if normalized_sets and raw_sets else "fallback",
        len(normalized_sets),
    )
    return normalized_sets[:_CALIBRATION_SET_COUNT]


def _flatten_archetype_sets(archetype_sets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for set_index, archetype_set in enumerate(archetype_sets):
        calibration_set_id = str(archetype_set.get("calibration_set_id") or archetype_set.get("calibrationSetId") or _stable_calibration_set_id(set_index + 1)).strip()
        for option_index, archetype in enumerate(archetype_set.get("archetypes") or []):
            flattened.append(
                {
                    **archetype,
                    "setIndex": set_index + 1,
                    "optionIndex": option_index + 1,
                    "setTitle": archetype_set.get("set_title", ""),
                    "setTheme": archetype_set.get("set_theme", ""),
                    "calibration_set_id": calibration_set_id,
                    "calibrationSetId": calibration_set_id,
                }
            )
    return flattened


def _persist_calibration_snapshot(*, db: Session, job_id: str, state: dict[str, Any]) -> None:
    job_repo = JobRepository(db)
    job = job_repo.get(job_id)
    if not job:
        return

    structured = dict(job.structured_data or {})
    archetype_pool = list(state.get("archetype_pool") or [])
    structured["recruiterCalibration"] = {
        "source": state.get("candidate_source", "groq_candidate_profiles"),
        "archetypeCount": len(archetype_pool),
        "calibrationSetIds": [str(item.get("calibration_set_id") or "").strip() for item in list(state.get("archetype_sets") or []) if str(item.get("calibration_set_id") or "").strip()],
        "currentCalibrationSetId": str((state.get("current_pair") or {}).get("calibration_set_id") or "").strip(),
        "selectedArchetypeIds": list(state.get("selected_archetype_ids") or []),
        "selectedCandidateIds": list(state.get("selected_candidate_ids") or []),
        "rejectedCandidateIds": list(state.get("rejected_candidate_ids") or []),
        "setTitles": [str(item.get("set_title") or "").strip() for item in list(state.get("archetype_sets") or []) if str(item.get("set_title") or "").strip()],
        "currentRoundIndex": int(state.get("current_round_index") or 1),
        "history": list(state.get("history") or []),
        "state": _calibration_state_snapshot(state),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    job_repo.update_structured_fields(job_id=job_id, structured_data=structured)


def bootstrap_preference_calibration_session(
    *,
    db: Session,
    recruiter_id: str,
    job_id: str,
    voice_summary: str = "",
    gap_analysis: dict[str, Any] | None = None,
    orchestration_session_id: str = "",
) -> dict[str, Any]:
    job = JobRepository(db).get(job_id)
    if not job:
        raise ValueError("Job not found")

    recruiter_id = _normalize_text(recruiter_id)
    existing_state = _load_calibration_state(recruiter_id=recruiter_id, job_id=job_id)
    if existing_state:
        return existing_state

    restored_state = _load_calibration_state_from_job(db=db, recruiter_id=recruiter_id, job_id=job_id)
    if restored_state:
        _save_calibration_state(recruiter_id=recruiter_id, job_id=job_id, state=restored_state)
        return restored_state

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

    archetype_sets = _generate_archetype_sets(
        job=job,
        voice_summary=voice_summary,
        gap_analysis=gap_analysis,
        intent_profile=intent_profile,
    )
    archetype_pool = _flatten_archetype_sets(archetype_sets)
    current_pair = dict(archetype_sets[0] or {}) if archetype_sets else {}
    state = {
        "job_id": job_id,
        "recruiter_id": recruiter_id,
        "orchestration_session_id": _normalize_text(orchestration_session_id),
        "status": "active",
        "stage": "archetype_calibration",
        "current_round_index": 1,
        "archetype_sets": archetype_sets,
        "archetype_pool": archetype_pool,
        "rounds": [
            {
                "round_index": item.get("round_index", index + 1),
                "calibration_set_id": str(item.get("calibration_set_id") or item.get("calibrationSetId") or _stable_calibration_set_id(index + 1)).strip(),
                "candidate_ids": [archetype.get("id", "") for archetype in item.get("archetypes", [])],
                "candidates": [dict(archetype) for archetype in item.get("archetypes", [])],
                "signal_quality": round(0.75 - (index * 0.05), 4),
                "contrast_axes": ["work_style", "ownership_level", "risk_tolerance"],
                "rationale": item.get("set_theme") or item.get("set_title") or "Contrast recruiter taste across candidate profile sets.",
                "pair_explanation": {
                    "why_selected": item.get("set_theme") or item.get("set_title") or "",
                    "contrast_axes": ["work_style", "ownership_level", "risk_tolerance"],
                    "signal_quality": round(0.75 - (index * 0.05), 4),
                },
            }
            for index, item in enumerate(archetype_sets)
        ],
        "current_pair": current_pair,
        "current_calibration_set_id": str(current_pair.get("calibration_set_id") or current_pair.get("calibrationSetId") or "").strip(),
        "selected_candidate_ids": [],
        "selected_archetype_ids": [],
        "rejected_candidate_ids": [],
        "history": [],
        "gap_analysis": gap_analysis,
        "recommended_questions": list(gap_analysis.get("recommended_questions") or []),
        "vetting_mode": _job_mode(job),
        "candidate_source": "groq_candidate_profiles",
        "intent_profile": summarize_intent_profile(intent_profile),
        "voice_summary": _normalize_text(voice_summary),
        "session_id": "",
        "telemetry": {
            "preference_learning_gain": 0.0,
            "rerank_precision_gain": 0.0,
            "pair_signal_quality": round(float((archetype_sets[0] or {}).get("signal_quality") or 0.0), 4) if archetype_sets else 0.0,
            "recruiter_preference_confidence": float(intent_profile.get("history_signal_strength") or 0.0),
        },
    }
    _save_calibration_state(recruiter_id=recruiter_id, job_id=job_id, state=state)
    _persist_calibration_snapshot(db=db, job_id=job_id, state=state)
    if archetype_sets:
        log_metric("pair_signal_quality", value=float((archetype_sets[0] or {}).get("signal_quality") or 0.0))
    return state


def get_preference_calibration_session(*, recruiter_id: str, job_id: str) -> dict[str, Any] | None:
    return _load_calibration_state(recruiter_id=recruiter_id, job_id=job_id)


def _calibration_current_set(state: dict[str, Any]) -> dict[str, Any]:
    sets = list(state.get("archetype_sets") or [])
    current_round_index = max(1, int(state.get("current_round_index") or 1))
    for item in sets:
        if int(item.get("round_index") or 0) == current_round_index:
            return item
    return sets[0] if sets else {}


def _calibration_set_by_id(state: dict[str, Any], calibration_set_id: str) -> dict[str, Any]:
    target = _normalize_text(calibration_set_id)
    if not target:
        return {}
    for item in list(state.get("archetype_sets") or []):
        item_set_id = _normalize_text(item.get("calibration_set_id") or item.get("calibrationSetId"))
        if item_set_id == target:
            return item
    return {}


def _calibration_history_for_set(state: dict[str, Any], calibration_set_id: str) -> dict[str, Any] | None:
    target = _normalize_text(calibration_set_id)
    if not target:
        return None
    for item in list(state.get("history") or []):
        if _normalize_text(item.get("calibration_set_id") or item.get("calibrationSetId")) == target:
            return item
    return None


def record_preference_calibration_choice(
    *,
    db: Session,
    recruiter_id: str,
    job_id: str,
    selected_candidate_id: str,
    calibration_set_id: str = "",
) -> dict[str, Any]:
    recruiter_id = _normalize_text(recruiter_id)
    selected_candidate_id = _normalize_text(selected_candidate_id)
    calibration_set_id = _normalize_text(calibration_set_id)
    job = JobRepository(db).get(job_id)
    if not job:
        raise ValueError("Job not found")

    state = _load_calibration_state(recruiter_id=recruiter_id, job_id=job_id)
    if not state:
        state = _load_calibration_state_from_job(db=db, recruiter_id=recruiter_id, job_id=job_id)
    if not state:
        raise ValueError("Calibration state is missing or expired")

    active_set = _calibration_current_set(state)
    current_set = _calibration_set_by_id(state, calibration_set_id) if calibration_set_id else _calibration_current_set(state)
    if not current_set and calibration_set_id:
        current_set = _calibration_current_set(state)
    if not current_set:
        raise ValueError("Calibration set is missing or expired")

    resolved_set_id = _normalize_text(current_set.get("calibration_set_id") or current_set.get("calibrationSetId") or calibration_set_id)
    if not resolved_set_id:
        resolved_set_id = _stable_calibration_set_id(int(current_set.get("round_index") or state.get("current_round_index") or 1))

    active_set_id = _normalize_text(active_set.get("calibration_set_id") or active_set.get("calibrationSetId"))
    if active_set_id and resolved_set_id != active_set_id:
        previous_selection = _calibration_history_for_set(state, resolved_set_id)
        if previous_selection:
            previous_selected_id = _normalize_text(previous_selection.get("selected_archetype_id") or previous_selection.get("selectedArchetypeId"))
            if previous_selected_id == selected_candidate_id:
                return state
        raise ValueError("Archetype is not part of the active calibration set")

    previous_selection = _calibration_history_for_set(state, resolved_set_id)
    if previous_selection:
        previous_selected_id = _normalize_text(previous_selection.get("selected_archetype_id") or previous_selection.get("selectedArchetypeId"))
        if previous_selected_id == selected_candidate_id:
            return state
        raise ValueError("Calibration set has already been resolved")

    current_options = list(current_set.get("archetypes") or [])
    option_lookup = {str(option.get("id") or "").strip(): option for option in current_options if str(option.get("id") or "").strip()}
    if selected_candidate_id not in option_lookup:
        raise ValueError("Archetype is not part of the selected calibration set")

    selected_option = option_lookup[selected_candidate_id]
    rejected_options = [option for option in current_options if option.get("id") != selected_candidate_id]
    try:
        update_recruiter_preferences(
            db,
            recruiter_id,
            selected_option,
            rejected_options,
            signal_multiplier=1.0 + (max(1, int(state.get("current_round_index") or 1)) * 0.15),
        )
    except Exception as exc:
        logger.warning(
            "recruiter_calibration_update_failed recruiter_id=%s job_id=%s round_index=%s error=%s",
            recruiter_id,
            job_id,
            int(state.get("current_round_index") or 1),
            str(exc),
        )

    history_entry = {
        "round_index": int(state.get("current_round_index") or 1),
        "calibration_set_id": resolved_set_id,
        "calibrationSetId": resolved_set_id,
        "selected_archetype_id": selected_candidate_id,
        "selectedArchetypeId": selected_candidate_id,
        "selected_archetype_title": selected_option.get("name") or selected_option.get("role") or "",
        "rejected_archetype_ids": [str(option.get("id") or "").strip() for option in rejected_options if str(option.get("id") or "").strip()],
        "selected_at": datetime.now(timezone.utc).isoformat(),
        "set_title": current_set.get("set_title", ""),
        "set_theme": current_set.get("set_theme", ""),
    }
    state["selected_candidate_ids"] = list(dict.fromkeys([*state.get("selected_candidate_ids", []), selected_candidate_id]))
    state["selected_archetype_ids"] = list(dict.fromkeys([*state.get("selected_archetype_ids", []), selected_candidate_id]))
    state["rejected_candidate_ids"] = list(
        dict.fromkeys([*state.get("rejected_candidate_ids", []), *history_entry["rejected_archetype_ids"]])
    )
    state["history"] = [*list(state.get("history") or []), history_entry]
    state["current_calibration_set_id"] = resolved_set_id
    state["telemetry"] = {
        **dict(state.get("telemetry") or {}),
        "preference_learning_gain": round(min(1.0, (int(state.get("current_round_index") or 1) / float(_CALIBRATION_SET_COUNT)) * 0.4), 4),
        "recruiter_preference_confidence": round(
            min(1.0, float((state.get("telemetry") or {}).get("recruiter_preference_confidence") or 0.0) + 0.18),
            4,
        ),
    }

    next_round_index = int(state.get("current_round_index") or 1) + 1
    if next_round_index <= _CALIBRATION_SET_COUNT:
        state["current_round_index"] = next_round_index
        next_set = next((item for item in state.get("archetype_sets") or [] if int(item.get("round_index") or 0) == next_round_index), {})
        state["current_pair"] = dict(next_set) if isinstance(next_set, dict) else {}
        state["current_calibration_set_id"] = str((state["current_pair"] or {}).get("calibration_set_id") or (state["current_pair"] or {}).get("calibrationSetId") or "").strip()
        state["status"] = "active"
        state["stage"] = "archetype_calibration"
    else:
        state["status"] = "completed"
        state["stage"] = "real_sourcing_ready"
        state["current_pair"] = {}
        state["current_calibration_set_id"] = ""

    final_state = _save_calibration_state(recruiter_id=recruiter_id, job_id=job_id, state=state)
    _persist_calibration_snapshot(db=db, job_id=job_id, state=final_state)
    save_cached_intent_profile(recruiter_id=recruiter_id, job_id=job_id, profile=final_state.get("intent_profile") or {})
    log_metric("rerank_precision_gain", value=float(state.get("telemetry", {}).get("preference_learning_gain", 0.0)))
    return final_state


def finalize_preference_calibration_session(*, db: Session, recruiter_id: str, job_id: str) -> dict[str, Any]:
    recruiter_id = _normalize_text(recruiter_id)
    job = JobRepository(db).get(job_id)
    if not job:
        raise ValueError("Job not found")

    state = _load_calibration_state(recruiter_id=recruiter_id, job_id=job_id)
    if not state:
        state = bootstrap_preference_calibration_session(db=db, recruiter_id=recruiter_id, job_id=job_id)

    state["status"] = "completed"
    state["stage"] = "real_sourcing_ready"
    state["current_pair"] = {}
    state["telemetry"] = {
        **dict(state.get("telemetry") or {}),
        "recruiter_preference_confidence": round(
            min(1.0, float((state.get("telemetry") or {}).get("recruiter_preference_confidence") or 0.0) + 0.12),
            4,
        ),
    }
    final_state = _save_calibration_state(recruiter_id=recruiter_id, job_id=job_id, state=state)
    _persist_calibration_snapshot(db=db, job_id=job_id, state=final_state)
    return final_state


def build_calibration_state_response(state: dict[str, Any] | None) -> dict[str, Any]:
    if not state:
        return {
            "status": "missing",
            "stage": "archetype_calibration",
            "rounds": [],
            "current_pair": {},
            "current_profile_set": {},
            "current_calibration_set_id": "",
            "history": [],
            "intent_profile": {},
            "recommended_questions": [],
            "telemetry": {},
            "archetype_sets": [],
            "profile_sets": [],
            "orchestration_session_id": "",
        }

    current_pair = dict(state.get("current_pair") or {})
    if current_pair and "profile_sets" not in current_pair:
        current_pair["profile_sets"] = list(current_pair.get("archetypes") or [])
    if current_pair and "profileSets" not in current_pair:
        current_pair["profileSets"] = list(current_pair.get("archetypes") or [])
    if current_pair and "candidate_profiles" not in current_pair:
        current_pair["candidate_profiles"] = list(current_pair.get("archetypes") or [])
    if current_pair and "candidateProfiles" not in current_pair:
        current_pair["candidateProfiles"] = list(current_pair.get("archetypes") or [])

    profile_sets = list(state.get("archetype_sets") or [])
    return {
        "status": state.get("status", "active"),
        "stage": state.get("stage", "archetype_calibration"),
        "vetting_mode": state.get("vetting_mode", "volume"),
        "candidate_source": state.get("candidate_source", "groq_candidate_profiles"),
        "rounds": list(state.get("rounds") or []),
        "current_round_index": int(state.get("current_round_index") or 1),
        "current_pair": current_pair,
        "current_profile_set": current_pair,
        "current_calibration_set_id": str(state.get("current_calibration_set_id") or (state.get("current_pair") or {}).get("calibration_set_id") or (state.get("current_pair") or {}).get("calibrationSetId") or "").strip(),
        "history": list(state.get("history") or []),
        "gap_analysis": state.get("gap_analysis") or {},
        "recommended_questions": list(state.get("recommended_questions") or []),
        "intent_profile": state.get("intent_profile") or {},
        "telemetry": state.get("telemetry") or {},
        "voice_summary": state.get("voice_summary", ""),
        "archetype_sets": profile_sets,
        "profile_sets": profile_sets,
        "candidate_profile_sets": profile_sets,
        "orchestration_session_id": str(state.get("orchestration_session_id") or "").strip(),
    }
