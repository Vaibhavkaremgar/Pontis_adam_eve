from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.repositories import RecruiterPreferenceRepository
from app.services.embedding_service import embed
from app.services.qdrant_service import upsert_recruiter_preferences
from app.services.redis_service import get_redis
from app.services.recruiter_preference_service import load_recruiter_preference_profile
from app.services.skill_normalizer import normalize_skills

_CACHE_PREFIX = "pontis:recruiter-intent:"


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _get_field(job: Any, *keys: str) -> str:
    if isinstance(job, dict):
        for key in keys:
            value = job.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    for key in keys:
        value = getattr(job, key, "")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _get_list_field(job: Any, *keys: str) -> list[str]:
    if isinstance(job, dict):
        for key in keys:
            value = job.get(key)
            if isinstance(value, list):
                return [_normalize_text(item) for item in value if _normalize_text(item)]
    else:
        for key in keys:
            value = getattr(job, key, None)
            if isinstance(value, list):
                return [_normalize_text(item) for item in value if _normalize_text(item)]
    return []


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _float_bias(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _cache_key(recruiter_id: str, job_id: str) -> str:
    return f"{_CACHE_PREFIX}{recruiter_id.strip()}:{job_id.strip()}"


def load_cached_intent_profile(*, recruiter_id: str, job_id: str) -> dict[str, Any] | None:
    redis = get_redis()
    if redis is None:
        return None
    raw = redis.get(_cache_key(recruiter_id, job_id))
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def save_cached_intent_profile(*, recruiter_id: str, job_id: str, profile: dict[str, Any], ttl_seconds: int = 86400) -> None:
    redis = get_redis()
    if redis is None:
        return
    try:
        redis.set(_cache_key(recruiter_id, job_id), json.dumps(profile), ex=ttl_seconds)
    except Exception:
        return


def _build_preference_text(*, job: Any, voice_summary: str, selection_rounds: list[dict[str, Any]], recruiter_history: dict[str, Any]) -> str:
    parts = [
        f"Role: {_get_field(job, 'title')}",
        f"Location: {_get_field(job, 'location')}",
        f"Experience: {_get_field(job, 'experience_level', 'experienceRequired')}",
        f"Skills: {', '.join(_get_list_field(job, 'skills_required', 'skills'))}",
        f"Voice summary: {_normalize_text(voice_summary)}",
    ]
    if recruiter_history.get("top_skills"):
        parts.append("Historical skills: " + ", ".join(item.get("skill", "") for item in recruiter_history.get("top_skills", []) if item.get("skill")))
    if recruiter_history.get("top_roles"):
        parts.append("Historical roles: " + ", ".join(item.get("role", "") for item in recruiter_history.get("top_roles", []) if item.get("role")))
    if recruiter_history.get("preferred_technical_strengths"):
        parts.append(
            "Preferred technical strengths: "
            + ", ".join(item for item in recruiter_history.get("preferred_technical_strengths", []) if item)
        )
    if recruiter_history.get("preferred_ownership_styles"):
        parts.append(
            "Preferred ownership styles: "
            + ", ".join(item for item in recruiter_history.get("preferred_ownership_styles", []) if item)
        )
    if recruiter_history.get("preferred_leadership_profiles"):
        parts.append(
            "Preferred leadership profiles: "
            + ", ".join(item for item in recruiter_history.get("preferred_leadership_profiles", []) if item)
        )
    if recruiter_history.get("preferred_ideal_environments"):
        parts.append(
            "Preferred environments: "
            + ", ".join(item for item in recruiter_history.get("preferred_ideal_environments", []) if item)
        )
    if recruiter_history.get("preferred_execution_styles"):
        parts.append(
            "Preferred execution styles: "
            + ", ".join(item for item in recruiter_history.get("preferred_execution_styles", []) if item)
        )
    if recruiter_history.get("preferred_hiring_tradeoffs"):
        parts.append(
            "Preferred tradeoffs: "
            + ", ".join(item for item in recruiter_history.get("preferred_hiring_tradeoffs", []) if item)
        )
    if selection_rounds:
        selection_bits = []
        for round_item in selection_rounds[-3:]:
            selected = _normalize_text(round_item.get("selected_candidate_name") or round_item.get("selected_candidate_id"))
            contrast = _normalize_text(round_item.get("signal_summary") or "")
            selection_bits.append(f"{selected}: {contrast}".strip(": "))
        parts.append("Selection rounds: " + " | ".join(bit for bit in selection_bits if bit))
    return "\n".join(part for part in parts if part.strip())


def _selected_skill_tokens(selection_rounds: list[dict[str, Any]]) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for round_item in selection_rounds:
        for skill in round_item.get("selected_candidate_skills", []) or []:
            normalized = _normalize_text(skill).lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                tokens.append(normalized)
    return tokens


def build_recruiter_intent_profile(
    *,
    db: Session,
    recruiter_id: str,
    job: Any,
    voice_summary: str = "",
    gap_analysis: dict[str, Any] | None = None,
    selection_rounds: list[dict[str, Any]] | None = None,
    transcript: str = "",
) -> dict[str, Any]:
    recruiter_id = _normalize_text(recruiter_id)
    job_id = _normalize_text(getattr(job, "id", "") or (job.get("id") if isinstance(job, dict) else ""))
    selection_rounds = selection_rounds or []
    gap_analysis = gap_analysis or {}

    recruiter_history = load_recruiter_preference_profile(db, recruiter_id) if recruiter_id else {
        "top_skills": [],
        "top_roles": [],
        "top_experience": [],
        "preferred_technical_strengths": [],
        "preferred_ownership_styles": [],
        "preferred_leadership_profiles": [],
        "preferred_ideal_environments": [],
        "preferred_execution_styles": [],
        "preferred_hiring_tradeoffs": [],
        "average_experience_years": None,
        "vector": [],
        "preference_text": "",
        "feedback_count": 0,
        "signal_strength": 0.0,
        "archetype": "generalist",
    }

    required_skills = _get_list_field(job, "skills_required", "skills")
    preferred_skills = _selected_skill_tokens(selection_rounds) or required_skills[:4]
    seniority_text = _get_field(job, "experience_level", "experienceRequired")
    seniority_weight = 0.8 if any(token in seniority_text.lower() for token in ("senior", "lead", "principal", "staff")) else 0.5

    startup_weight = 0.8 if any(token in " ".join([_get_field(job, "description"), voice_summary]).lower() for token in ("startup", "early-stage", "seed", "series a", "series b")) else 0.35
    domain_weight = 0.8 if any(token in " ".join([_get_field(job, "description"), voice_summary]).lower() for token in ("fintech", "healthcare", "payments", "security", "infra", "platform", "enterprise")) else 0.45
    leadership_weight = 0.8 if any(token in " ".join([_get_field(job, "description"), voice_summary]).lower() for token in ("lead", "mentor", "architect", "ownership", "technical leadership")) else 0.4
    infra_weight = 0.8 if any(token in " ".join([_get_field(job, "description"), voice_summary, " ".join(required_skills)]).lower() for token in ("aws", "gcp", "azure", "kubernetes", "terraform", "cloud", "infra", "platform")) else 0.4

    culture_preferences = []
    for token in (
        "ownership",
        "collaboration",
        "speed",
        "craft",
        "mentorship",
        "systems thinking",
        "product sense",
        "startup depth",
        "execution over polish",
        "founder-compatible",
        "enterprise rigor",
        "product judgment",
    ):
        if token in " ".join([_get_field(job, "description"), voice_summary]).lower():
            culture_preferences.append(token)
    if not culture_preferences:
        culture_preferences = ["cross-functional ownership", "clear communication"]

    hiring_biases = {
        "seniority_bias": "senior" if seniority_weight >= 0.7 else "mid",
        "startup_bias": "startup" if startup_weight >= 0.7 else "balanced",
        "domain_bias": "domain" if domain_weight >= 0.7 else "generalist",
        "leadership_bias": "leadership" if leadership_weight >= 0.7 else "ic",
        "infra_bias": "infra" if infra_weight >= 0.7 else "general_cloud",
        "gap_signal": gap_analysis.get("missing_preferences", []),
    }

    intent_text = _build_preference_text(
        job=job,
        voice_summary=voice_summary or transcript,
        selection_rounds=selection_rounds,
        recruiter_history=recruiter_history,
    )
    if not intent_text.strip():
        intent_text = _normalize_text(getattr(job, "description", "") or "")

    calibration_biases = {
        "preferred_technical_strengths": list(recruiter_history.get("preferred_technical_strengths") or []),
        "preferred_ownership_styles": list(recruiter_history.get("preferred_ownership_styles") or []),
        "preferred_leadership_profiles": list(recruiter_history.get("preferred_leadership_profiles") or []),
        "preferred_ideal_environments": list(recruiter_history.get("preferred_ideal_environments") or []),
        "preferred_execution_styles": list(recruiter_history.get("preferred_execution_styles") or []),
        "preferred_hiring_tradeoffs": list(recruiter_history.get("preferred_hiring_tradeoffs") or []),
    }

    vector = embed(intent_text or " ")
    history_vector = [float(value) for value in recruiter_history.get("vector", []) or []]
    if history_vector and len(history_vector) == len(vector):
        vector = [round((existing * 0.65) + (fresh * 0.35), 8) for existing, fresh in zip(history_vector, vector)]

    profile = {
        "job_id": job_id,
        "recruiter_id": recruiter_id,
        "required_skills": required_skills,
        "preferred_skills": preferred_skills,
        "seniority_weight": round(seniority_weight, 4),
        "startup_weight": round(startup_weight, 4),
        "domain_weight": round(domain_weight, 4),
        "leadership_weight": round(leadership_weight, 4),
        "infra_weight": round(infra_weight, 4),
        "culture_preferences": culture_preferences,
        "preferred_technical_strengths": list(recruiter_history.get("preferred_technical_strengths") or []),
        "preferred_ownership_styles": list(recruiter_history.get("preferred_ownership_styles") or []),
        "preferred_leadership_profiles": list(recruiter_history.get("preferred_leadership_profiles") or []),
        "preferred_ideal_environments": list(recruiter_history.get("preferred_ideal_environments") or []),
        "preferred_execution_styles": list(recruiter_history.get("preferred_execution_styles") or []),
        "preferred_hiring_tradeoffs": list(recruiter_history.get("preferred_hiring_tradeoffs") or []),
        "hiring_biases": {**hiring_biases, **calibration_biases},
        "recruiter_preference_embedding": vector,
        "preference_text": intent_text,
        "voice_summary": _normalize_text(voice_summary or transcript),
        "selection_round_count": len(selection_rounds),
        "history_signal_strength": float(recruiter_history.get("signal_strength") or 0.0),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "profile_hash": _hash_text(intent_text),
    }
    return profile


def persist_recruiter_intent_profile(*, db: Session, recruiter_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    recruiter_id = _normalize_text(recruiter_id)
    if not recruiter_id:
        return profile

    profile_to_store = dict(profile)
    save_cached_intent_profile(recruiter_id=recruiter_id, job_id=str(profile.get("job_id") or ""), profile=profile_to_store)

    vector = [float(value) for value in (profile_to_store.get("recruiter_preference_embedding") or [])]
    try:
        upsert_recruiter_preferences(
            recruiter_id,
            vector,
            payload={
                "updatedAt": profile_to_store.get("updated_at"),
                "jobId": profile_to_store.get("job_id"),
                "preferenceText": profile_to_store.get("preference_text", ""),
                "requiredSkills": profile_to_store.get("required_skills", []),
                "preferredSkills": profile_to_store.get("preferred_skills", []),
                "culturePreferences": profile_to_store.get("culture_preferences", []),
                "hiringBiases": profile_to_store.get("hiring_biases", {}),
                "startupWeight": profile_to_store.get("startup_weight"),
                "domainWeight": profile_to_store.get("domain_weight"),
                "leadershipWeight": profile_to_store.get("leadership_weight"),
                "infraWeight": profile_to_store.get("infra_weight"),
                "seniorityWeight": profile_to_store.get("seniority_weight"),
            },
        )
    except Exception:
        pass

    try:
        profile_repo = RecruiterPreferenceRepository(db)
        history_weight = float(profile.get("history_signal_strength") or 0.0)
        if profile.get("preferred_skills"):
            for skill in profile["preferred_skills"][:6]:
                profile_repo.upsert_skill_preference(recruiter_id=recruiter_id, skill=skill, delta=0.25 + history_weight * 0.1)
        for role in profile.get("culture_preferences", [])[:4]:
            profile_repo.upsert_role_preference(recruiter_id=recruiter_id, role=role, delta=0.15 + history_weight * 0.05)
    except Exception:
        pass

    save_cached_intent_profile(recruiter_id=recruiter_id, job_id=str(profile.get("job_id") or ""), profile=profile_to_store)
    return profile_to_store


def summarize_intent_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "required_skills": list(profile.get("required_skills") or []),
        "preferred_skills": list(profile.get("preferred_skills") or []),
        "seniority_weight": float(profile.get("seniority_weight") or 0.0),
        "startup_weight": float(profile.get("startup_weight") or 0.0),
        "domain_weight": float(profile.get("domain_weight") or 0.0),
        "leadership_weight": float(profile.get("leadership_weight") or 0.0),
        "infra_weight": float(profile.get("infra_weight") or 0.0),
        "culture_preferences": list(profile.get("culture_preferences") or []),
        "preferred_technical_strengths": list(profile.get("preferred_technical_strengths") or []),
        "preferred_ownership_styles": list(profile.get("preferred_ownership_styles") or []),
        "preferred_leadership_profiles": list(profile.get("preferred_leadership_profiles") or []),
        "preferred_ideal_environments": list(profile.get("preferred_ideal_environments") or []),
        "preferred_execution_styles": list(profile.get("preferred_execution_styles") or []),
        "preferred_hiring_tradeoffs": list(profile.get("preferred_hiring_tradeoffs") or []),
        "hiring_biases": dict(profile.get("hiring_biases") or {}),
        "recruiter_preference_embedding": list(profile.get("recruiter_preference_embedding") or []),
        "preference_text": profile.get("preference_text", ""),
        "voice_summary": profile.get("voice_summary", ""),
        "selection_round_count": int(profile.get("selection_round_count") or 0),
        "profile_hash": profile.get("profile_hash", ""),
    }
