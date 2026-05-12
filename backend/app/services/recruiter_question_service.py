from __future__ import annotations

import json
import logging
from typing import Any

from app.services.llm_service import generate
from app.services.prompt_sanitizer import sanitize_prompt_block

logger = logging.getLogger(__name__)


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

_DEFAULT_TASTE_QUESTIONS = [
    "What kind of candidate background should we bias toward if two people look equally strong on paper?",
    "What is the biggest dealbreaker we should screen for before outreach?",
    "Should we favor speed and adaptability, depth and specialization, or a balance of both?",
    "If you had to choose, would you rather optimize for startup energy, enterprise rigor, or something in between?",
    "What should a standout candidate have done recently that would make you say yes quickly?",
    "Which trait matters most to you: ownership, execution, communication, or technical depth?",
    "Are there any experience patterns that look good on paper but actually are not a fit for this team?",
]


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


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _extract_questions(payload: Any) -> list[str]:
    raw_items: list[Any] = []
    if isinstance(payload, dict):
        for key in ("questions", "recommended_questions", "recommendedQuestions", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                raw_items = value
                break
    elif isinstance(payload, list):
        raw_items = payload

    questions: list[str] = []
    for item in raw_items:
        if isinstance(item, str):
            text = _normalize_text(item)
        elif isinstance(item, dict):
            text = _normalize_text(item.get("question") or item.get("text") or item.get("content"))
        else:
            text = ""
        if text:
            questions.append(text)

    return _ordered_unique(questions)


def _job_field(job: Any, *keys: str) -> str:
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


def _job_list_field(job: Any, *keys: str) -> list[str]:
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
    return [_normalize_text(value) for value in values if _normalize_text(value)]


def _build_ai_prompt(*, gap_analysis: dict[str, Any], job: Any, voice_summary: str, max_questions: int) -> str:
    missing_fields = ", ".join(gap_analysis.get("missing_fields") or []) or "none"
    ambiguous_fields = ", ".join(gap_analysis.get("ambiguous_fields") or []) or "none"
    missing_preferences = ", ".join(gap_analysis.get("missing_preferences") or []) or "none"
    confidence_scores = gap_analysis.get("confidence_scores") or {}
    if not isinstance(confidence_scores, dict):
        confidence_scores = {}

    prompt = (
        "You are an expert recruiter assistant.\n"
        "Your job is to generate the best follow-up questions for a recruiter voice intake.\n"
        "Use the job details and analysis below to produce concise, natural questions.\n"
        "Rules:\n"
        f"- Return ONLY valid JSON with this schema: {{\"questions\": [\"question 1\", \"question 2\"]}}\n"
        f"- Return between 1 and {max_questions} questions\n"
        "- Keep each question short, practical, and human\n"
        "- Do not include numbering, bullets, markdown, or explanations\n"
        "- Do not repeat the same idea twice\n"
        "- Prioritize the most important missing or ambiguous information first\n"
        "- If the job is already very clear, ask taste/fit questions that help refine the profile\n\n"
        f"{sanitize_prompt_block('Job title', _job_field(job, 'title'), max_length=160)}\n"
        f"{sanitize_prompt_block('Job description', _job_field(job, 'description'), max_length=2400)}\n"
        f"{sanitize_prompt_block('Location', _job_field(job, 'location'), max_length=160)}\n"
        f"{sanitize_prompt_block('Compensation', _job_field(job, 'compensation', 'salary_range'), max_length=160)}\n"
        f"{sanitize_prompt_block('Work authorization', _job_field(job, 'work_authorization', 'workAuthorization'), max_length=160)}\n"
        f"{sanitize_prompt_block('Experience', _job_field(job, 'experience_level', 'experienceRequired', 'seniority'), max_length=160)}\n"
        f"{sanitize_prompt_block('Skills', ', '.join(_job_list_field(job, 'skills_required', 'skills')), max_length=1200)}\n"
        f"{sanitize_prompt_block('Responsibilities', ', '.join(_job_list_field(job, 'responsibilities')), max_length=1200)}\n"
        f"{sanitize_prompt_block('Voice summary', voice_summary, max_length=2000)}\n"
        f"{sanitize_prompt_block('Missing fields', missing_fields, max_length=800)}\n"
        f"{sanitize_prompt_block('Ambiguous fields', ambiguous_fields, max_length=800)}\n"
        f"{sanitize_prompt_block('Missing preferences', missing_preferences, max_length=800)}\n"
        f"{sanitize_prompt_block('Confidence scores', json.dumps(confidence_scores, ensure_ascii=False), max_length=800)}\n"
    )
    return prompt


def generate_recruiter_questions(
    *,
    gap_analysis: dict[str, Any],
    job: Any,
    voice_summary: str = "",
    max_questions: int = 7,
) -> list[str]:
    prompt = _build_ai_prompt(
        gap_analysis=gap_analysis,
        job=job,
        voice_summary=voice_summary,
        max_questions=max_questions,
    )

    try:
        payload = generate(prompt, expect_json=True)
        ai_questions = _extract_questions(payload)
        if ai_questions:
            logger.info("recruiter_questions_generated source=groq count=%s", len(ai_questions))
            return ai_questions[:max_questions]
    except Exception as exc:
        logger.warning("recruiter_questions_llm_failed error=%s", str(exc))

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
        logger.info("recruiter_questions_generated source=fallback_default count=%s", len(_DEFAULT_TASTE_QUESTIONS[:max_questions]))
        return _DEFAULT_TASTE_QUESTIONS[:max_questions]

    for question in _DEFAULT_TASTE_QUESTIONS:
        if len(questions) >= max_questions:
            break
        if question.lower() not in {item.lower() for item in questions}:
            questions.append(question)

    final_questions = questions[:max_questions]
    logger.info("recruiter_questions_generated source=fallback_rules count=%s", len(final_questions))
    return final_questions
