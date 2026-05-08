from __future__ import annotations

import re
from typing import Any

from app.services.skill_normalizer import normalize_skills
from app.services.recruiter_question_service import generate_recruiter_questions

_AMBIGUOUS_PATTERNS = (
    r"\bvarious\b",
    r"\betc\b",
    r"\bfamily\s+feel\b",
    r"\bstrong\b",
    r"\bgreat\b",
    r"\bsolid\b",
    r"\bhands?-?on\b",
    r"\bfast[- ]?paced\b",
    r"\bstartup[- ]?like\b",
    r"\bscale\b",
    r"\bflexib(le|ility)\b",
)


def _text_value(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


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
    values: Any = None
    if isinstance(job, dict):
        for key in keys:
            value = job.get(key)
            if isinstance(value, list):
                values = value
                break
    else:
        for key in keys:
            value = getattr(job, key, None)
            if isinstance(value, list):
                values = value
                break
    if not isinstance(values, list):
        return []
    return [_text_value(value) for value in values if _text_value(value)]


def _field_confidence(value: str, *, kind: str) -> float:
    if not value:
        return 0.0
    lowered = value.lower()
    if kind == "skills":
        tokens = normalize_skills([item.strip() for item in re.split(r"[,/|]", lowered) if item.strip()])
        return 0.35 if len(tokens) <= 1 else 0.85 if len(tokens) >= 3 else 0.65
    if kind == "experience":
        if any(token in lowered for token in ("years", "senior", "lead", "principal", "staff")):
            return 0.85
        if re.search(r"\d", lowered):
            return 0.7
        return 0.45
    if kind == "location":
        if any(token in lowered for token in ("remote", "hybrid", "onsite")):
            return 0.8
        return 0.65 if len(lowered.split()) <= 4 else 0.5
    if kind == "compensation":
        if re.search(r"\$|\d", lowered):
            return 0.8
        return 0.45
    if kind == "work_authorization":
        if any(token in lowered for token in ("required", "preferred", "visa", "sponsorship")):
            return 0.8
        return 0.45
    if kind == "title":
        return 0.9 if len(lowered.split()) <= 6 else 0.7
    if kind == "description":
        length_score = min(1.0, len(lowered) / 400.0)
        return max(0.35, length_score)
    return 0.5


def _is_ambiguous(value: str) -> bool:
    lowered = value.lower()
    return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in _AMBIGUOUS_PATTERNS)


def _missing_preference_flags(job: Any, voice_summary: str, entities: dict[str, Any]) -> dict[str, bool]:
    lowered_summary = (voice_summary or "").lower()
    job_text = " ".join(
        [
            _get_field(job, "description"),
            _get_field(job, "title"),
            " ".join(_get_list_field(job, "skills_required", "skills")),
            lowered_summary,
        ]
    ).lower()

    return {
        "seniority": not any(token in job_text for token in ("senior", "lead", "principal", "staff", "manager")),
        "domain": not any(token in job_text for token in ("fintech", "healthcare", "ecommerce", "platform", "infra", "payments", "security")),
        "startup": not any(token in job_text for token in ("startup", "scale-up", "startup experience", "early-stage")),
        "culture_team": not any(token in job_text for token in ("cross-functional", "ownership", "collaboration", "mentorship", "team")),
        "leadership": not any(token in job_text for token in ("leadership", "mentor", "architect", "drive", "owns")),
        "location_flexibility": not any(token in job_text for token in ("remote", "hybrid", "onsite", "location", "timezone")),
        "infra_depth": not any(token in job_text for token in ("aws", "gcp", "azure", "kubernetes", "terraform", "infra", "platform")),
        "team_stage": not any(token in job_text for token in ("seed", "series a", "series b", "enterprise", "startup", "mature")),
        "domain_signal": bool(entities.get("domain") or entities.get("industry")),
    }


def analyze_job_gap(*, job: Any, voice_summary: str = "", entities: dict[str, Any] | None = None) -> dict[str, Any]:
    entities = entities or {}
    title = _get_field(job, "title")
    description = _get_field(job, "description")
    location = _get_field(job, "location")
    compensation = _get_field(job, "compensation", "salary_range")
    work_authorization = _get_field(job, "work_authorization", "workAuthorization")
    experience = _get_field(job, "experience_level", "experienceRequired", "seniority")
    skills = _get_list_field(job, "skills_required", "skills")
    responsibilities = _get_list_field(job, "responsibilities")

    confidence_scores = {
        "title": _field_confidence(title, kind="title"),
        "description": _field_confidence(description, kind="description"),
        "location": _field_confidence(location, kind="location"),
        "compensation": _field_confidence(compensation, kind="compensation"),
        "work_authorization": _field_confidence(work_authorization, kind="work_authorization"),
        "experience": _field_confidence(experience, kind="experience"),
        "skills": _field_confidence(", ".join(skills), kind="skills"),
        "responsibilities": 0.9 if responsibilities else 0.25,
    }

    ambiguous_fields: list[str] = []
    for field_name, value in {
        "title": title,
        "description": description,
        "location": location,
        "compensation": compensation,
        "work_authorization": work_authorization,
        "experience": experience,
    }.items():
        if value and _is_ambiguous(value):
            ambiguous_fields.append(field_name)

    missing_fields = [
        field_name
        for field_name, value in {
            "title": title,
            "description": description,
            "location": location,
            "compensation": compensation,
            "work_authorization": work_authorization,
            "experience": experience,
            "skills": ", ".join(skills),
        }.items()
        if not value.strip()
    ]

    preference_flags = _missing_preference_flags(job, voice_summary, entities)
    missing_preferences = [name for name, missing in preference_flags.items() if missing]

    low_confidence_fields = [field_name for field_name, value in confidence_scores.items() if value < 0.65]
    for item in low_confidence_fields:
        if item not in missing_fields and item not in ambiguous_fields:
            ambiguous_fields.append(item)

    analysis = {
        "missing_fields": missing_fields,
        "ambiguous_fields": sorted(set(ambiguous_fields)),
        "confidence_scores": confidence_scores,
        "missing_preferences": missing_preferences,
        "recommended_questions": [],
    }
    analysis["recommended_questions"] = generate_recruiter_questions(
        gap_analysis=analysis,
        job=job,
        voice_summary=voice_summary,
        max_questions=7,
    )
    return analysis
