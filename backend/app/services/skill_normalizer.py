from __future__ import annotations

import re
from typing import Any

SKILL_MAP: dict[str, list[str]] = {
    "frontend": ["react", "react.js", "vue", "angular", "javascript", "typescript", "html", "css"],
    "backend": ["node", "node.js", "django", "flask", "java", "spring", "api", "rest"],
    "cloud": ["aws", "gcp", "azure", "cloud", "amazon web services"],
    "data": ["python", "pandas", "machine learning", "sql", "analytics", "numpy"],
}

_CANONICAL_LOOKUP: dict[str, str] = {}
for canonical, variants in SKILL_MAP.items():
    _CANONICAL_LOOKUP[canonical] = canonical
    for variant in variants:
        _CANONICAL_LOOKUP[variant.strip().lower()] = canonical


def expand_skill(skill: str) -> list[str]:
    token = _normalize_skill_text(skill)
    if not token:
        return []
    canonical = _CANONICAL_LOOKUP.get(token)
    if canonical:
        return [canonical, *SKILL_MAP.get(canonical, [])]
    return [token]


def _normalize_skill_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip().lower()


def normalize_skills(skills: list[str] | tuple[str, ...] | set[str] | Any) -> set[str]:
    normalized: set[str] = set()
    if not isinstance(skills, (list, tuple, set)):
        return normalized

    for skill in skills:
        token = _normalize_skill_text(skill)
        if not token:
            continue
        canonical = _CANONICAL_LOOKUP.get(token)
        if canonical:
            normalized.add(canonical)
            continue
        expanded = expand_skill(token)
        if expanded:
            normalized.update(expanded[:1])
            continue
        normalized.add(token)
    return normalized


def parse_experience(value: Any) -> int:
    if isinstance(value, (int, float)):
        return max(0, int(value))

    if not isinstance(value, str):
        return 0

    text = re.sub(r"\s+", " ", value).strip().lower()
    if not text:
        return 0

    range_match = re.search(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s+years?", text)
    if range_match:
        return max(0, int(float(range_match.group(1))))

    plus_match = re.search(r"(\d+(?:\.\d+)?)\+?\s+years?", text)
    if plus_match:
        return max(0, int(float(plus_match.group(1))))

    bare_years_match = re.search(r"(\d+(?:\.\d+)?)", text)
    if bare_years_match and "year" in text:
        return max(0, int(float(bare_years_match.group(1))))

    return 0
