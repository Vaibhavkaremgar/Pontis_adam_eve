from __future__ import annotations

import re
from typing import Any


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _normalize_list(values: Any) -> list[str]:
    if isinstance(values, list):
        items = values
    elif isinstance(values, str) and values.strip():
        items = [values]
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _normalize_text(item)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized


def _get_value(candidate: Any, *keys: str) -> Any:
    if isinstance(candidate, dict):
        for key in keys:
            value = candidate.get(key)
            if value not in (None, ""):
                return value
        return None

    for key in keys:
        value = getattr(candidate, key, None)
        if value not in (None, ""):
            return value
    return None


def build_candidate_text(candidate: Any) -> str:
    role = _normalize_text(_get_value(candidate, "role", "title", "job_title") or "")
    skills = _normalize_list(_get_value(candidate, "skills", "skills_required") or [])
    experience = _normalize_text(
        _get_value(candidate, "experience", "experience_level", "years_experience", "experience_summary") or ""
    )
    summary = _normalize_text(_get_value(candidate, "summary", "bio", "experience_summary") or "")

    return (
        f"Role: {role}\n"
        f"Skills: {', '.join(skills)}\n"
        f"Experience: {experience}\n\n"
        f"Summary:\n{summary}"
    ).strip()
