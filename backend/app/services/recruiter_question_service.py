from __future__ import annotations

from typing import Any


_QUESTION_BANK: dict[str, str] = {
    "startup": "How important is startup experience?",
    "seniority": "Should candidates have senior or leadership-level experience?",
    "domain": "Is domain experience important for this role?",
    "culture_team": "What kind of team or culture fit matters most?",
    "leadership": "How much system design or technical leadership should they have?",
    "location_flexibility": "Would remote candidates be acceptable?",
    "infra_depth": "How important is deep AWS or infrastructure experience versus general cloud experience?",
    "team_stage": "Does the team need someone comfortable in an early-stage environment?",
    "work_authorization": "Are there any work authorization requirements we should optimize for?",
    "compensation": "Is compensation a hard filter or just a guideline?",
    "experience": "What seniority band should we optimize for?",
    "skills": "Are there any must-have skills we should treat as non-negotiable?",
    "responsibilities": "Which responsibilities matter most on day one?",
}


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = " ".join(str(value or "").split()).strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(normalized)
    return ordered


def generate_recruiter_questions(
    *,
    gap_analysis: dict[str, Any],
    job: Any,
    voice_summary: str = "",
    max_questions: int = 7,
) -> list[str]:
    confidence_scores = gap_analysis.get("confidence_scores") if isinstance(gap_analysis, dict) else {}
    if not isinstance(confidence_scores, dict):
        confidence_scores = {}

    missing_fields = list(gap_analysis.get("missing_fields") or [])
    ambiguous_fields = list(gap_analysis.get("ambiguous_fields") or [])
    missing_preferences = list(gap_analysis.get("missing_preferences") or [])

    prioritized_fields: list[str] = []
    for field_name in (
        "startup",
        "domain",
        "seniority",
        "leadership",
        "infra_depth",
        "culture_team",
        "location_flexibility",
        "team_stage",
        "work_authorization",
        "compensation",
        "experience",
        "skills",
        "responsibilities",
    ):
        if field_name in missing_preferences:
            prioritized_fields.append(field_name)

    for field_name in missing_fields + ambiguous_fields:
        if field_name not in prioritized_fields:
            prioritized_fields.append(field_name)

    scored_fields = sorted(
        prioritized_fields,
        key=lambda field_name: (
            -float(1.0 - float(confidence_scores.get(field_name, 0.5)) if isinstance(confidence_scores, dict) else 0.5),
            field_name,
        ),
    )

    questions = [_QUESTION_BANK.get(field_name, "") for field_name in scored_fields if _QUESTION_BANK.get(field_name)]
    questions = _ordered_unique(questions)

    if not questions:
        base_questions = [
            "What kind of background would make a candidate stand out to you?",
            "What is the one thing you'd most like to clarify before we search?",
        ]
        return base_questions[:max_questions]

    return questions[:max_questions]

