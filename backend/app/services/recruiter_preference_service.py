from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.repositories import CandidateFeedbackRepository, RecruiterPreferenceRepository
from app.services.embedding_service import embed
from app.services.metrics_service import log_metric
from app.services.qdrant_service import load_recruiter_preferences as load_recruiter_preferences_vector
from app.services.qdrant_service import upsert_recruiter_preferences
from app.services.skill_normalizer import normalize_skills, parse_experience

logger = logging.getLogger(__name__)

_MAX_TOP_ITEMS = 8
_MAX_TOP_EXPERIENCE = 4
_SELECTED_SKILL_DELTA = 1.0
_SELECTED_ROLE_DELTA = 1.0
_SELECTED_EXPERIENCE_DELTA = 0.8
_REJECTED_SKILL_DELTA = -0.15
_REJECTED_ROLE_DELTA = -0.1
_REJECTED_EXPERIENCE_DELTA = -0.08
_EXPERIENCE_WEIGHT = 0.2
_MIN_FEEDBACK_FOR_FULL_RECRUITER_WEIGHT = 5

_EXPERIENCE_BUCKETS = ("0-2", "3-5", "6-9", "10+")
_COMPANY_DOMAIN_KEYWORDS = {
    "payments": ("payment", "payments", "fintech", "settlement", "card", "wallet", "upi", "fraud", "risk"),
    "developer_infrastructure": ("infra", "platform", "observability", "devops", "sre", "cloud", "distributed", "developer"),
    "ai_infrastructure": ("ai", "ml", "machine learning", "llm", "retrieval", "ranking", "model", "inference", "prompt"),
    "security": ("security", "identity", "auth", "iam", "zero trust", "threat", "vulnerability", "appsec"),
    "data": ("data", "analytics", "warehouse", "etl", "pipeline", "lakehouse", "bi"),
    "sales": ("sales", "revenue", "pipeline", "revops", "account executive", "bdr", "sdr", "gtm"),
    "enterprise": ("enterprise", "saas", "workforce", "erp", "crm", "workflow", "it"),
}


def _infer_recruiter_archetype(*, top_skills: list[dict[str, Any]], top_roles: list[dict[str, Any]], average_experience: float | None) -> str:
    signals = " ".join(
        [
            " ".join(str(item.get("skill") or "") for item in top_skills[:3]),
            " ".join(str(item.get("role") or "") for item in top_roles[:3]),
        ]
    ).lower()
    if any(token in signals for token in ("backend", "platform", "infra", "distributed")):
        return "systems_builder"
    if any(token in signals for token in ("ml", "ai", "data", "search", "ranking")):
        return "ai_hiring_specialist"
    if any(token in signals for token in ("product", "design", "ux")):
        return "product_generalist"
    if average_experience is not None and average_experience >= 8:
        return "senior_generalist"
    return "generalist"


def _text_value(candidate: Any, *keys: str) -> str:
    if candidate is None:
        return ""
    for key in keys:
        value = candidate.get(key) if isinstance(candidate, dict) else getattr(candidate, key, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _text_list_value(candidate: Any, *keys: str) -> list[str]:
    collected: list[str] = []

    def visit(item: Any) -> None:
        if item is None:
            return
        if isinstance(item, str):
            cleaned = " ".join(item.split()).strip()
            if not cleaned:
                return
            if any(sep in cleaned for sep in [",", ";", "|"]):
                for piece in cleaned.replace(";", ",").replace("|", ",").split(","):
                    piece = " ".join(piece.split()).strip()
                    if piece:
                        collected.append(piece)
                return
            collected.append(cleaned)
            return
        if isinstance(item, list):
            for nested in item:
                visit(nested)
            return
        if isinstance(item, dict):
            for key in ("text", "label", "title", "name", "role", "value", "skill", "strength", "signal", "tradeoff"):
                nested = item.get(key)
                if nested is not None:
                    visit(nested)
                    return
            for nested in item.values():
                visit(nested)
            return
        cleaned = " ".join(str(item).split()).strip()
        if cleaned:
            collected.append(cleaned)

    if isinstance(candidate, dict):
        for key in keys:
            visit(candidate.get(key))
    else:
        for key in keys:
            visit(getattr(candidate, key, None))

    ordered: list[str] = []
    seen: set[str] = set()
    for item in collected:
        normalized = " ".join(item.split()).strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(normalized)
    return ordered


def _normalize_company_name(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _candidate_company(candidate: Any) -> str:
    return _normalize_company_name(_text_value(candidate, "company", "current_company", "job_company_name"))


def _candidate_domain(candidate: Any) -> str:
    text = " ".join(
        [
            _text_value(candidate, "company", "current_company", "job_company_name"),
            _candidate_role(candidate),
            _candidate_experience_text(candidate),
            " ".join(_candidate_skills(candidate)),
        ]
    ).lower()
    for domain, keywords in _COMPANY_DOMAIN_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return domain
    return "general"


def _unique_non_empty(values: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _normalize_company_name(value)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(cleaned)
    return ordered


def _candidate_profile_data(candidate: Any) -> dict[str, Any]:
    if isinstance(candidate, dict):
        profile = candidate.get("profileData")
        return profile if isinstance(profile, dict) else {}
    profile = getattr(candidate, "profileData", None)
    return profile if isinstance(profile, dict) else {}


def _candidate_calibration_tokens(candidate: Any) -> dict[str, list[str] | str]:
    profile = _candidate_profile_data(candidate)
    return {
        "headline": _text_value(profile, "candidateHeadline", "candidate_headline", "title"),
        "experience_snapshot": _text_value(profile, "experienceSnapshot", "experience_snapshot"),
        "career_pattern": _text_value(profile, "careerPattern", "career_pattern"),
        "technical_strengths": _text_list_value(profile, "technicalStrengths", "technical_strengths", "strengths", "skills"),
        "ownership_style": _text_value(profile, "ownershipStyle", "ownership_style", "ownershipLevel", "ownership_level"),
        "leadership_profile": _text_list_value(profile, "leadershipProfile", "leadership_profile", "leadershipSignals", "leadership_signals"),
        "ideal_environment": _text_value(profile, "idealEnvironment", "ideal_environment"),
        "execution_style": _text_value(profile, "executionStyle", "execution_style"),
        "hiring_tradeoffs": _text_list_value(profile, "hiringTradeoffs", "hiring_tradeoffs"),
    }


def _candidate_skills(candidate: Any) -> list[str]:
    raw = candidate.get("skills") if isinstance(candidate, dict) else getattr(candidate, "skills", None)
    if not isinstance(raw, list):
        return []
    return [str(skill).strip() for skill in raw if str(skill).strip()]


def _candidate_role(candidate: Any) -> str:
    return _text_value(candidate, "role", "job_title", "title")


def _candidate_experience_text(candidate: Any) -> str:
    explanation = candidate.get("explanation") if isinstance(candidate, dict) else getattr(candidate, "explanation", None)
    if explanation is not None:
        for key in ("candidateExperience", "experience"):
            value = _text_value(explanation, key)
            if value:
                return value
    return _text_value(candidate, "summary", "bio", "experience_summary")


def map_experience_to_bucket(years: int | float | None) -> str:
    try:
        value = int(float(years or 0))
    except (TypeError, ValueError):
        return ""
    if value <= 2:
        return "0-2"
    if value <= 5:
        return "3-5"
    if value <= 9:
        return "6-9"
    return "10+"


def _candidate_experience_years(candidate: Any) -> int | None:
    text = _candidate_experience_text(candidate)
    if not text.strip():
        return None
    return parse_experience(text)


def _experience_bucket_weight(rows: list[dict[str, Any]], bucket: str) -> float:
    bucket = (bucket or "").strip()
    if not bucket or not rows:
        return 0.0
    for row in rows:
        if (str(row.get("experience_bucket") or "").strip() == bucket):
            return float(row.get("weight") or 0.0)
    return 0.0


def _normalize_items(items: list[str]) -> list[str]:
    normalized = normalize_skills(items)
    ordered: list[str] = []
    for item in normalized:
        token = item.strip().lower()
        if token and token not in ordered:
            ordered.append(token)
        if len(ordered) >= _MAX_TOP_ITEMS:
            break
    return ordered


def _normalize_weight_rows(rows: list[dict[str, Any]], *, key_field: str) -> list[dict[str, Any]]:
    if not rows:
        return []
    max_weight = max(float(row.get("weight") or 0.0) for row in rows) or 1.0
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        weight = float(row.get("weight") or 0.0)
        updated_at = row.get("updated_at")
        decay_factor = 1.0
        if isinstance(updated_at, datetime):
            age_days = max(0.0, (datetime.now(timezone.utc) - updated_at.astimezone(timezone.utc)).total_seconds() / 86400.0)
            if age_days > 30:
                decay_factor = max(0.75, 0.95 ** (age_days / 30.0))
        normalized_rows.append(
            {
                key_field: row.get(key_field, ""),
                "weight": round(max(0.0, (weight / max_weight) * decay_factor), 4),
                "rawWeight": round(weight, 4),
                "positiveCount": int(row.get("positive_count") or 0),
                "negativeCount": int(row.get("negative_count") or 0),
                "updatedAt": updated_at.isoformat() if isinstance(updated_at, datetime) else None,
            }
        )
    normalized_rows.sort(key=lambda item: (-float(item.get("weight") or 0.0), str(item.get(key_field) or "")))
    return normalized_rows


def _preference_text(
    *,
    skills: list[dict[str, Any]],
    roles: list[dict[str, Any]],
    experiences: list[dict[str, Any]] | None = None,
    average_experience: float | None = None,
    calibration_signals: dict[str, list[str]] | None = None,
) -> str:
    parts: list[str] = []
    top_skills = [str(item["skill"]).strip() for item in skills[:_MAX_TOP_ITEMS] if item.get("skill")]
    top_roles = [str(item["role"]).strip() for item in roles[:_MAX_TOP_ITEMS] if item.get("role")]
    top_experiences = [str(item["experience_bucket"]).strip() for item in (experiences or [])[:_MAX_TOP_EXPERIENCE] if item.get("experience_bucket")]
    if top_skills:
        parts.append(f"Preferred skills: {', '.join(top_skills)}")
    if top_roles:
        parts.append(f"Preferred roles: {', '.join(top_roles)}")
    if top_experiences:
        parts.append(f"Preferred experience: {', '.join(top_experiences)}")
    if average_experience is not None:
        parts.append(f"Typical experience: {average_experience:.1f} years")
    if calibration_signals:
        for label, values in calibration_signals.items():
            cleaned = [str(item).strip() for item in (values or []) if str(item).strip()]
            if cleaned:
                parts.append(f"{label.replace('_', ' ').title()}: {', '.join(cleaned[:6])}")
    return " | ".join(parts)


def load_recruiter_preference_profile(db: Session, recruiter_id: str) -> dict[str, Any]:
    recruiter_id = (recruiter_id or "").strip()
    if not recruiter_id:
        return {
            "recruiter_id": "",
            "top_skills": [],
            "top_roles": [],
            "top_experience": [],
            "skill_tokens": [],
            "role_tokens": [],
            "experience_tokens": [],
            "preferred_technical_strengths": [],
            "preferred_ownership_styles": [],
            "preferred_leadership_profiles": [],
            "preferred_ideal_environments": [],
            "preferred_execution_styles": [],
            "preferred_hiring_tradeoffs": [],
            "preferred_companies": [],
            "rejected_companies": [],
            "preferred_domains": [],
            "rejected_domains": [],
            "average_experience_years": None,
            "preference_text": "",
            "vector": [],
            "feedback_count": 0,
            "signal_strength": 0.0,
            "archetype": "generalist",
            "cold_start": True,
        }

    repo = RecruiterPreferenceRepository(db)
    skill_rows = repo.list_skill_preferences(recruiter_id=recruiter_id, limit=_MAX_TOP_ITEMS)
    role_rows = repo.list_role_preferences(recruiter_id=recruiter_id, limit=_MAX_TOP_ITEMS)
    experience_rows = repo.list_experience_preferences(recruiter_id=recruiter_id, limit=_MAX_TOP_EXPERIENCE)
    qdrant_snapshot = load_recruiter_preferences_vector(recruiter_id)

    top_skills = _normalize_weight_rows(
        [
            {
                "skill": row.skill,
                "weight": row.weight,
                "positive_count": row.positive_count,
                "negative_count": row.negative_count,
                "updated_at": row.updated_at,
            }
            for row in skill_rows
        ],
        key_field="skill",
    )
    top_roles = _normalize_weight_rows(
        [
            {
                "role": row.role,
                "weight": row.weight,
                "positive_count": row.positive_count,
                "negative_count": row.negative_count,
                "updated_at": row.updated_at,
            }
            for row in role_rows
        ],
        key_field="role",
    )
    top_experience = _normalize_weight_rows(
        [
            {
                "experience_bucket": row.experience_bucket,
                "weight": row.weight,
                "updated_at": row.updated_at,
            }
            for row in experience_rows
        ],
        key_field="experience_bucket",
    )

    average_experience_value = (qdrant_snapshot or {}).get("payload", {}).get("averageExperienceYears")
    try:
        average_experience = float(average_experience_value) if average_experience_value is not None else None
    except (TypeError, ValueError):
        average_experience = None

    payload = (qdrant_snapshot or {}).get("payload", {}) if qdrant_snapshot else {}
    preferred_technical_strengths = _text_list_value(payload, "preferredTechnicalStrengths", "technicalStrengths", "technical_strengths")
    preferred_ownership_styles = _text_list_value(payload, "preferredOwnershipStyles", "ownershipStyles", "ownership_styles")
    preferred_leadership_profiles = _text_list_value(payload, "preferredLeadershipProfiles", "leadershipProfiles", "leadership_profiles")
    preferred_ideal_environments = _text_list_value(payload, "preferredIdealEnvironments", "idealEnvironments", "ideal_environments")
    preferred_execution_styles = _text_list_value(payload, "preferredExecutionStyles", "executionStyles", "execution_styles")
    preferred_hiring_tradeoffs = _text_list_value(payload, "preferredHiringTradeoffs", "hiringTradeoffs", "hiring_tradeoffs")
    preferred_companies = _text_list_value(payload, "preferredCompanies", "selectedCompanies", "topCompanies")
    rejected_companies = _text_list_value(payload, "rejectedCompanies", "negativeCompanies")
    preferred_domains = _text_list_value(payload, "preferredDomains", "selectedDomains", "topDomains")
    rejected_domains = _text_list_value(payload, "rejectedDomains", "negativeDomains")

    preference_text = _preference_text(
        skills=top_skills,
        roles=top_roles,
        experiences=top_experience,
        average_experience=average_experience,
        calibration_signals={
            "technical_strengths": preferred_technical_strengths,
            "ownership_styles": preferred_ownership_styles,
            "leadership_profiles": preferred_leadership_profiles,
            "ideal_environments": preferred_ideal_environments,
            "execution_styles": preferred_execution_styles,
            "hiring_tradeoffs": preferred_hiring_tradeoffs,
        },
    )
    vector = [float(value) for value in ((qdrant_snapshot or {}).get("vector") or [])] if qdrant_snapshot else []
    if not vector and preference_text:
        vector = embed(preference_text)

    feedback_count = CandidateFeedbackRepository(db).count_for_recruiter(recruiter_id)
    signal_strength = min(1.0, feedback_count / float(_MIN_FEEDBACK_FOR_FULL_RECRUITER_WEIGHT))
    archetype = _infer_recruiter_archetype(top_skills=top_skills, top_roles=top_roles, average_experience=average_experience)
    cold_start = feedback_count < 3

    return {
        "recruiter_id": recruiter_id,
        "top_skills": top_skills,
        "top_roles": top_roles,
        "top_experience": top_experience,
        "skill_tokens": [item["skill"] for item in top_skills if item.get("skill")],
        "role_tokens": [item["role"] for item in top_roles if item.get("role")],
        "experience_tokens": [item["experience_bucket"] for item in top_experience if item.get("experience_bucket")],
        "preferred_technical_strengths": preferred_technical_strengths,
        "preferred_ownership_styles": preferred_ownership_styles,
        "preferred_leadership_profiles": preferred_leadership_profiles,
        "preferred_ideal_environments": preferred_ideal_environments,
        "preferred_execution_styles": preferred_execution_styles,
        "preferred_hiring_tradeoffs": preferred_hiring_tradeoffs,
        "preferred_companies": preferred_companies,
        "rejected_companies": rejected_companies,
        "preferred_domains": preferred_domains,
        "rejected_domains": rejected_domains,
        "average_experience_years": average_experience,
        "preference_text": preference_text,
        "vector": vector,
        "feedback_count": feedback_count,
        "signal_strength": signal_strength,
        "archetype": archetype,
        "cold_start": cold_start,
    }


def get_recruiter_learning_metrics(db: Session, recruiter_id: str) -> dict[str, Any]:
    recruiter_id = (recruiter_id or "").strip()
    if not recruiter_id:
        return {
            "feedback_count": 0,
            "selection_count": 0,
            "rejection_count": 0,
            "silent_negative_count": 0,
            "top_skills": [],
            "top_roles": [],
        }

    feedback_summary = CandidateFeedbackRepository(db).get_learning_summary_for_recruiter(recruiter_id)
    preference_repo = RecruiterPreferenceRepository(db)
    top_skills = [
        {"skill": row.skill, "weight": round(float(row.weight or 0.0), 4)}
        for row in preference_repo.list_skill_preferences(recruiter_id=recruiter_id, limit=10)
    ]
    top_roles = [
        {"role": row.role, "weight": round(float(row.weight or 0.0), 4)}
        for row in preference_repo.list_role_preferences(recruiter_id=recruiter_id, limit=10)
    ]

    silent_negative_count = preference_repo.count_silent_learning_events(recruiter_id)

    return {
        "feedback_count": int(feedback_summary["feedback_count"]),
        "selection_count": int(feedback_summary["selection_count"]),
        "rejection_count": int(feedback_summary["rejection_count"]),
        "silent_negative_count": int(silent_negative_count),
        "top_skills": top_skills,
        "top_roles": top_roles,
    }


def get_recruiter_experience_preferences(db: Session, recruiter_id: str) -> dict[str, Any]:
    recruiter_id = (recruiter_id or "").strip()
    if not recruiter_id:
        return {
            "buckets": [],
            "top_bucket": "",
            "preferred_range": [],
            "spread": 0.0,
            "last_updated": None,
        }

    repo = RecruiterPreferenceRepository(db)
    rows = repo.list_experience_preferences(recruiter_id=recruiter_id, limit=_MAX_TOP_EXPERIENCE)
    buckets: list[dict[str, Any]] = []
    weights: list[float] = []
    last_updated = None
    for row in rows:
        updated_at = row.updated_at
        if isinstance(updated_at, datetime):
            updated_iso = updated_at.isoformat()
            if last_updated is None or updated_at > last_updated:
                last_updated = updated_at
        else:
            updated_iso = None
        weight = round(float(row.weight or 0.0), 4)
        weights.append(weight)
        buckets.append(
            {
                "bucket": row.experience_bucket,
                "weight": weight,
                "updated_at": updated_iso,
            }
        )

    top_bucket = buckets[0]["bucket"] if buckets else ""
    preferred_range = [item["bucket"] for item in buckets[:2] if item.get("bucket")]
    spread = round((max(weights) - min(weights)), 4) if len(weights) > 1 else 0.0

    return {
        "buckets": buckets,
        "top_bucket": top_bucket,
        "preferred_range": preferred_range,
        "spread": spread,
        "last_updated": last_updated.isoformat() if last_updated else None,
    }


def _candidate_signal_delta(*, selected: bool, experience_years: int | None, base_delta: float, signal_multiplier: float) -> float:
    if experience_years is None:
        return 0.0
    experience_bonus = min(max(int(experience_years), 0), 12) * 0.02
    delta = base_delta + experience_bonus if selected else base_delta - min(experience_bonus * 0.2, 0.05)
    return delta * max(0.1, float(signal_multiplier))


def _update_candidate_preferences(
    *,
    repo: RecruiterPreferenceRepository,
    recruiter_id: str,
    candidate: Any,
    selected: bool,
    signal_multiplier: float = 1.0,
) -> dict[str, Any]:
    skills = _normalize_items(_candidate_skills(candidate))
    role = _candidate_role(candidate).strip().lower()
    experience_years = _candidate_experience_years(candidate)
    experience_bucket = map_experience_to_bucket(experience_years) if experience_years is not None else ""

    if not skills and not role and not experience_bucket:
        return {"skills": [], "role": "", "experience_years": experience_years, "experience_bucket": ""}

    skill_delta = _candidate_signal_delta(
        selected=selected,
        experience_years=experience_years,
        base_delta=_SELECTED_SKILL_DELTA if selected else _REJECTED_SKILL_DELTA,
        signal_multiplier=signal_multiplier,
    )
    role_delta = _candidate_signal_delta(
        selected=selected,
        experience_years=experience_years,
        base_delta=_SELECTED_ROLE_DELTA if selected else _REJECTED_ROLE_DELTA,
        signal_multiplier=signal_multiplier,
    )
    experience_delta = _candidate_signal_delta(
        selected=selected,
        experience_years=experience_years,
        base_delta=_SELECTED_EXPERIENCE_DELTA if selected else _REJECTED_EXPERIENCE_DELTA,
        signal_multiplier=signal_multiplier,
    )

    for skill in skills:
        repo.upsert_skill_preference(recruiter_id=recruiter_id, skill=skill, delta=skill_delta)
    if role:
        repo.upsert_role_preference(recruiter_id=recruiter_id, role=role, delta=role_delta)
    if experience_bucket:
        repo.upsert_experience_preference(recruiter_id=recruiter_id, experience_bucket=experience_bucket, delta=experience_delta)

    return {
        "skills": skills,
        "role": role,
        "experience_years": experience_years,
        "experience_bucket": experience_bucket,
    }


def update_recruiter_preferences(
    db: Session,
    recruiter_id: str,
    selected_candidate: Any | None,
    rejected_candidates: list[Any],
    *,
    signal_multiplier: float = 1.0,
) -> dict[str, Any]:
    recruiter_id = (recruiter_id or "").strip()
    if not recruiter_id:
        return {
            "recruiter_id": "",
            "top_skills": [],
            "top_roles": [],
            "top_experience": [],
            "skill_tokens": [],
            "role_tokens": [],
            "experience_tokens": [],
            "preferred_technical_strengths": [],
            "preferred_ownership_styles": [],
            "preferred_leadership_profiles": [],
            "preferred_ideal_environments": [],
            "preferred_execution_styles": [],
            "preferred_hiring_tradeoffs": [],
            "average_experience_years": None,
            "preference_text": "",
            "vector": [],
            "feedback_count": 0,
            "signal_strength": 0.0,
        }

    repo = RecruiterPreferenceRepository(db)
    selected_snapshot = {"skills": [], "role": "", "experience_years": 0}
    selected_company = ""
    selected_domain = ""
    if selected_candidate is not None:
        selected_snapshot = _update_candidate_preferences(
            repo=repo,
            recruiter_id=recruiter_id,
            candidate=selected_candidate,
            selected=True,
            signal_multiplier=signal_multiplier,
        )
        selected_company = _candidate_company(selected_candidate)
        selected_domain = _candidate_domain(selected_candidate)

    rejected_snapshots = [
        _update_candidate_preferences(
            repo=repo,
            recruiter_id=recruiter_id,
            candidate=candidate,
            selected=False,
            signal_multiplier=signal_multiplier,
        )
        for candidate in (rejected_candidates or [])
    ]
    rejected_companies = _unique_non_empty([_candidate_company(candidate) for candidate in (rejected_candidates or [])])
    rejected_domains = _unique_non_empty([_candidate_domain(candidate) for candidate in (rejected_candidates or [])])

    selected_calibration = _candidate_calibration_tokens(selected_candidate) if selected_candidate is not None else {
        "headline": "",
        "experience_snapshot": "",
        "career_pattern": "",
        "technical_strengths": [],
        "ownership_style": "",
        "leadership_profile": [],
        "ideal_environment": "",
        "execution_style": "",
        "hiring_tradeoffs": [],
    }
    profile = load_recruiter_preference_profile(db, recruiter_id)
    try:
        preference_vector = profile.get("vector") or embed(profile.get("preference_text", "") or " ")
        upsert_recruiter_preferences(
            recruiter_id,
            preference_vector,
            payload={
                "updatedAt": datetime.now(timezone.utc).isoformat(),
                "topSkills": profile.get("skill_tokens", []),
                "topRoles": profile.get("role_tokens", []),
                "topExperience": profile.get("experience_tokens", []),
                "preferredTechnicalStrengths": list(selected_calibration.get("technical_strengths") or []),
                "preferredOwnershipStyles": [str(selected_calibration.get("ownership_style") or "").strip()] if str(selected_calibration.get("ownership_style") or "").strip() else [],
                "preferredLeadershipProfiles": list(selected_calibration.get("leadership_profile") or []),
                "preferredIdealEnvironments": [str(selected_calibration.get("ideal_environment") or "").strip()] if str(selected_calibration.get("ideal_environment") or "").strip() else [],
                "preferredExecutionStyles": [str(selected_calibration.get("execution_style") or "").strip()] if str(selected_calibration.get("execution_style") or "").strip() else [],
                "preferredHiringTradeoffs": list(selected_calibration.get("hiring_tradeoffs") or []),
                "preferredCompanies": [selected_company] if selected_company else [],
                "preferredDomains": [selected_domain] if selected_domain else [],
                "rejectedCompanies": rejected_companies,
                "rejectedDomains": rejected_domains,
                "averageExperienceYears": profile.get("average_experience_years"),
                "selectedCount": 1 if selected_candidate is not None else 0,
                "rejectedCount": len([item for item in rejected_snapshots if item.get("skills") or item.get("role")]),
                "preferenceText": profile.get("preference_text", ""),
            },
        )
    except Exception as exc:
        logger.info("recruiter_qdrant_update_skipped recruiter_id=%s error=%s", recruiter_id, str(exc))

    logger.info(
        "recruiter_preferences_updated recruiter_id=%s selected_skills=%s rejected_count=%s selected_company=%s selected_domain=%s",
        recruiter_id,
        len(selected_snapshot.get("skills", [])),
        len(rejected_snapshots),
        selected_company,
        selected_domain,
    )
    log_metric(
        "recruiter_feedback_learning",
        recruiter_id=recruiter_id,
        selected_company=selected_company,
        selected_domain=selected_domain,
        rejected_companies="|".join(rejected_companies),
        rejected_domains="|".join(rejected_domains),
        selected_count=1 if selected_candidate is not None else 0,
        rejected_count=len(rejected_snapshots),
    )
    return load_recruiter_preference_profile(db, recruiter_id)


def _vector_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    length = min(len(left), len(right))
    if length <= 0:
        return 0.0
    left_vec = left[:length]
    right_vec = right[:length]
    left_norm = sum(value * value for value in left_vec) ** 0.5
    right_norm = sum(value * value for value in right_vec) ** 0.5
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return max(-1.0, min(1.0, sum(l * r for l, r in zip(left_vec, right_vec)) / (left_norm * right_norm)))


def _token_match(candidate_tokens: list[str], preferred_tokens: list[dict[str, Any]], *, key: str) -> float:
    if not candidate_tokens or not preferred_tokens:
        return 0.0

    preferred = [(str(item.get(key) or "").strip().lower(), float(item.get("weight") or 0.0)) for item in preferred_tokens]
    weighted_total = sum(weight for _, weight in preferred) or 1.0
    score = 0.0
    for candidate_token in candidate_tokens:
        normalized_candidate = candidate_token.strip().lower()
        if not normalized_candidate:
            continue
        for preferred_token, weight in preferred:
            if not preferred_token:
                continue
            if (
                normalized_candidate == preferred_token
                or normalized_candidate in preferred_token
                or preferred_token in normalized_candidate
            ):
                score += weight
                break
    return max(0.0, min(1.0, score / weighted_total))


def _text_match_score(candidate_text: str, preferred_tokens: list[str]) -> float:
    cleaned_tokens = [str(token).strip().lower() for token in preferred_tokens if str(token).strip()]
    if not candidate_text or not cleaned_tokens:
        return 0.0
    lowered = candidate_text.lower()
    hits = sum(1 for token in cleaned_tokens if token and token in lowered)
    return max(0.0, min(1.0, hits / max(1, len(cleaned_tokens))))


def compute_recruiter_score_details(
    candidate: Any,
    recruiter_profile: dict[str, Any],
    candidate_vector: list[float] | None = None,
) -> dict[str, Any]:
    if not recruiter_profile:
        return {
            "score": 0.0,
            "skill_score": 0.0,
            "role_score": 0.0,
            "vector_score": 0.0,
            "experience_bucket": "",
            "experience_score": 0.0,
            "experience_component": 0.0,
        }

    candidate_skills = _normalize_items(_candidate_skills(candidate))
    recruiter_skills = list(recruiter_profile.get("top_skills") or [])
    candidate_roles = _normalize_items([_candidate_role(candidate)])
    recruiter_roles = list(recruiter_profile.get("top_roles") or [])
    candidate_years = _candidate_experience_years(candidate)
    experience_bucket = map_experience_to_bucket(candidate_years) if candidate_years is not None else ""
    recruiter_experience = list(recruiter_profile.get("top_experience") or [])
    calibration_profile = _candidate_profile_data(candidate)
    candidate_text = " ".join(
        [
            _text_value(candidate, "name", "full_name"),
            _candidate_role(candidate),
            _text_value(candidate, "company", "job_company_name"),
            _text_value(candidate, "summary", "bio", "experience_summary"),
            " ".join(candidate_skills),
            _text_value(calibration_profile, "candidateHeadline", "candidate_headline", "title"),
            _text_value(calibration_profile, "experienceSnapshot", "experience_snapshot"),
            _text_value(calibration_profile, "careerPattern", "career_pattern"),
            " ".join(_text_list_value(calibration_profile, "technicalStrengths", "technical_strengths", "strengths", "skills")),
            _text_value(calibration_profile, "ownershipStyle", "ownership_style", "ownershipLevel", "ownership_level"),
            _text_value(calibration_profile, "idealEnvironment", "ideal_environment"),
            _text_value(calibration_profile, "executionStyle", "execution_style"),
            " ".join(_text_list_value(calibration_profile, "leadershipProfile", "leadership_profile", "leadershipSignals", "leadership_signals")),
            " ".join(_text_list_value(calibration_profile, "hiringTradeoffs", "hiring_tradeoffs")),
        ]
    ).strip()

    skill_score = _token_match(candidate_skills, recruiter_skills, key="skill") if candidate_skills and recruiter_skills else 0.0
    role_score = _token_match(candidate_roles, recruiter_roles, key="role") if candidate_roles and recruiter_roles else 0.0
    vector_score = 0.0
    if candidate_vector and recruiter_profile.get("vector"):
        vector_score = max(0.0, ( _vector_similarity(candidate_vector, recruiter_profile["vector"]) + 1.0) / 2.0)

    experience_score = _experience_bucket_weight(recruiter_experience, experience_bucket) if experience_bucket and recruiter_experience else 0.0
    experience_component = experience_score * _EXPERIENCE_WEIGHT

    calibration_signal_score = _text_match_score(
        candidate_text,
        [
            *list(recruiter_profile.get("preferred_technical_strengths") or []),
            *list(recruiter_profile.get("preferred_ownership_styles") or []),
            *list(recruiter_profile.get("preferred_leadership_profiles") or []),
            *list(recruiter_profile.get("preferred_ideal_environments") or []),
            *list(recruiter_profile.get("preferred_execution_styles") or []),
            *list(recruiter_profile.get("preferred_hiring_tradeoffs") or []),
        ],
    )

    base_score = (skill_score * 0.62) + (role_score * 0.16) + (vector_score * 0.07) + (calibration_signal_score * 0.15)
    recruiter_score = (base_score * (1.0 - _EXPERIENCE_WEIGHT)) + experience_component
    recruiter_score = max(0.0, min(1.0, recruiter_score))
    return {
        "score": recruiter_score,
        "skill_score": skill_score,
        "role_score": role_score,
        "vector_score": vector_score,
        "calibration_signal_score": calibration_signal_score,
        "experience_bucket": experience_bucket,
        "experience_score": experience_score,
        "experience_component": experience_component,
    }


def compute_recruiter_score(candidate: Any, recruiter_profile: dict[str, Any], candidate_vector: list[float] | None = None) -> float:
    return float(compute_recruiter_score_details(candidate, recruiter_profile, candidate_vector=candidate_vector)["score"])
