from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import GROQ_API_KEY
from app.db.repositories import (
    CompanyRepository,
    JobRepository,
    OrchestrationEventRepository,
    OrchestrationSessionRepository,
    UserRepository,
)
from app.services.candidate_service import fetch_ranked_candidates
from app.services.hiring_service import create_hiring_job
from app.services.llm_service import generate
from app.services.recruiter_preference_round_service import (
    bootstrap_preference_calibration_session,
    build_calibration_state_response,
)
from app.services.slack_integration import build_calibration_blocks, build_candidate_blocks, post_slack_message_with_result
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)

ORCHESTRATION_SOURCE = "slack"
ORCHESTRATION_STAGE_INITIATED = "initiated"
ORCHESTRATION_STAGE_SLACK = "slack_intake"
ORCHESTRATION_STAGE_VOICE = "voice_intake"
ORCHESTRATION_STAGE_COMPLETED = "intake_completed"
ORCHESTRATION_STAGE_SOURCING = "sourcing"
ORCHESTRATION_STAGE_CALIBRATION = "calibration"
ORCHESTRATION_STAGE_CANDIDATES = "candidates_ready"
ORCHESTRATION_STAGE_OUTREACH = "outreach"
ORCHESTRATION_STAGE_INTERVIEW = "interview"
ORCHESTRATION_STAGE_PLACED = "placed"
ORCHESTRATION_STAGE_CLOSED = "closed"

VOICE_TOKEN_TTL_MINUTES = 60
SYSTEM_SLACK_USER_EMAIL = "slack-system@pontis.local"

CORE_QUESTION_PLAN: list[tuple[str, str, str]] = [
    ("company_name", "What company is hiring for this role?", "company_name"),
    ("role_title", "What role are you hiring for?", "role_title"),
    ("must_have_requirements", "What are the must-have requirements?", "must_have_requirements"),
    ("success_profile", "What kind of person succeeds in this role?", "success_profile"),
    ("compensation", "What compensation range are you targeting?", "compensation"),
    ("urgency", "How urgent is this hire?", "urgency"),
    ("team_structure", "What does the team look like today?", "team_structure"),
]

FOLLOWUP_QUESTION_BANK: list[tuple[str, str]] = [
    ("startup_ownership", "How much ownership should this person be comfortable with in a fast-moving environment?"),
    ("leadership_expectations", "How much technical leadership or mentoring should they bring?"),
    ("architecture_complexity", "How complex is the architecture or domain they need to navigate?"),
    ("culture_fit", "What kind of culture fit matters most for this team?"),
    ("communication_style", "How should they communicate with stakeholders and the team?"),
    ("team_maturity", "Is the team early-stage, scaling, or already well established?"),
    ("stakeholder_management", "How much stakeholder management will this role need?"),
    ("tech_stack", "Are there any specific tools or technologies that are non-negotiable?"),
]

INTAKE_FIELDS: list[str] = [
    "company_name",
    "role_title",
    "must_have_requirements",
    "success_profile",
    "skills",
    "seniority",
    "location",
    "compensation",
    "hiring_signals",
    "tech_stack",
    "hiring_priorities",
    "culture_fit",
    "communication_style",
    "team_maturity",
    "leadership_expectations",
    "architecture_complexity",
    "urgency",
    "team_structure",
    "stakeholder_management",
]


@dataclass(frozen=True)
class IntakeQuestionSpec:
    key: str
    field_name: str
    question_type: str
    prompt: str
    required: bool = False
    min_confidence: float = 0.7
    max_items: int = 8


INTAKE_QUESTION_REGISTRY: dict[str, IntakeQuestionSpec] = {
    "company_name": IntakeQuestionSpec(
        key="company_name",
        field_name="company_name",
        question_type="text",
        prompt="What company is hiring for this role?",
        required=True,
        min_confidence=0.6,
    ),
    "role_title": IntakeQuestionSpec(
        key="role_title",
        field_name="role_title",
        question_type="text",
        prompt="What role are you hiring for?",
        required=True,
        min_confidence=0.6,
    ),
    "must_have_requirements": IntakeQuestionSpec(
        key="must_have_requirements",
        field_name="must_have_requirements",
        question_type="list",
        prompt="What are the must-have requirements?",
        required=True,
        min_confidence=0.7,
        max_items=12,
    ),
    "success_profile": IntakeQuestionSpec(
        key="success_profile",
        field_name="success_profile",
        question_type="text",
        prompt="What kind of person succeeds in this role?",
        required=True,
        min_confidence=0.55,
    ),
    "skills": IntakeQuestionSpec(
        key="skills",
        field_name="skills",
        question_type="list",
        prompt="Which skills should we target?",
        required=True,
        min_confidence=0.7,
        max_items=12,
    ),
    "seniority": IntakeQuestionSpec(
        key="seniority",
        field_name="seniority",
        question_type="text",
        prompt="What seniority level are you hiring for?",
        required=True,
        min_confidence=0.7,
    ),
    "location": IntakeQuestionSpec(
        key="location",
        field_name="location",
        question_type="location",
        prompt="Where should this person be based?",
        required=True,
        min_confidence=0.7,
    ),
    "compensation": IntakeQuestionSpec(
        key="compensation",
        field_name="compensation",
        question_type="compensation",
        prompt="What compensation range are you targeting?",
        required=True,
        min_confidence=0.7,
    ),
    "hiring_signals": IntakeQuestionSpec(
        key="hiring_signals",
        field_name="hiring_signals",
        question_type="list",
        prompt="What signals would make this candidate stand out?",
        required=False,
        min_confidence=0.65,
        max_items=8,
    ),
    "tech_stack": IntakeQuestionSpec(
        key="tech_stack",
        field_name="tech_stack",
        question_type="list",
        prompt="Are there any specific tools or technologies that are non-negotiable?",
        required=False,
        min_confidence=0.65,
        max_items=8,
    ),
    "hiring_priorities": IntakeQuestionSpec(
        key="hiring_priorities",
        field_name="hiring_priorities",
        question_type="list",
        prompt="What are the top priorities for this hire?",
        required=False,
        min_confidence=0.65,
        max_items=8,
    ),
    "culture_fit": IntakeQuestionSpec(
        key="culture_fit",
        field_name="culture_fit",
        question_type="text",
        prompt="What kind of culture fit matters most for this team?",
        required=False,
        min_confidence=0.65,
    ),
    "communication_style": IntakeQuestionSpec(
        key="communication_style",
        field_name="communication_style",
        question_type="text",
        prompt="How should they communicate with stakeholders and the team?",
        required=False,
        min_confidence=0.65,
    ),
    "team_maturity": IntakeQuestionSpec(
        key="team_maturity",
        field_name="team_maturity",
        question_type="text",
        prompt="Is the team early-stage, scaling, or already established?",
        required=False,
        min_confidence=0.65,
    ),
    "leadership_expectations": IntakeQuestionSpec(
        key="leadership_expectations",
        field_name="leadership_expectations",
        question_type="text",
        prompt="How much technical leadership or mentoring should they bring?",
        required=False,
        min_confidence=0.65,
    ),
    "architecture_complexity": IntakeQuestionSpec(
        key="architecture_complexity",
        field_name="architecture_complexity",
        question_type="text",
        prompt="How complex is the architecture or domain they need to navigate?",
        required=False,
        min_confidence=0.65,
    ),
    "urgency": IntakeQuestionSpec(
        key="urgency",
        field_name="urgency",
        question_type="text",
        prompt="How urgent is this hire?",
        required=False,
        min_confidence=0.65,
    ),
    "team_structure": IntakeQuestionSpec(
        key="team_structure",
        field_name="team_structure",
        question_type="text",
        prompt="What does the team look like today?",
        required=False,
        min_confidence=0.65,
    ),
    "stakeholder_management": IntakeQuestionSpec(
        key="stakeholder_management",
        field_name="stakeholder_management",
        question_type="text",
        prompt="How much stakeholder management will this role need?",
        required=False,
        min_confidence=0.65,
    ),
    "startup_ownership": IntakeQuestionSpec(
        key="startup_ownership",
        field_name="hiring_priorities",
        question_type="text",
        prompt="How much ownership should this person be comfortable with in a fast-moving environment?",
        required=False,
        min_confidence=0.65,
    ),
    "path_selection": IntakeQuestionSpec(
        key="path_selection",
        field_name="path_selection",
        question_type="choice",
        prompt="Choose whether to continue in Slack or switch to Voice.",
        required=False,
        min_confidence=1.0,
    ),
    "final_confirmation": IntakeQuestionSpec(
        key="final_confirmation",
        field_name="final_confirmation",
        question_type="choice",
        prompt="Is there anything important we should still capture before I lock this intake?",
        required=False,
        min_confidence=1.0,
    ),
}

CORE_QUESTION_SEQUENCE: list[str] = [key for key, _, _ in CORE_QUESTION_PLAN]

FOLLOWUP_QUESTION_BANK: list[tuple[str, str]] = [
    ("startup_ownership", INTAKE_QUESTION_REGISTRY["startup_ownership"].prompt),
    ("leadership_expectations", INTAKE_QUESTION_REGISTRY["leadership_expectations"].prompt),
    ("architecture_complexity", INTAKE_QUESTION_REGISTRY["architecture_complexity"].prompt),
    ("culture_fit", INTAKE_QUESTION_REGISTRY["culture_fit"].prompt),
    ("communication_style", INTAKE_QUESTION_REGISTRY["communication_style"].prompt),
    ("team_maturity", INTAKE_QUESTION_REGISTRY["team_maturity"].prompt),
    ("stakeholder_management", INTAKE_QUESTION_REGISTRY["stakeholder_management"].prompt),
    ("tech_stack", INTAKE_QUESTION_REGISTRY["tech_stack"].prompt),
]

QUESTION_KEYS_BY_FIELD: dict[str, str] = {
    spec.field_name: key for key, spec in INTAKE_QUESTION_REGISTRY.items() if spec.field_name in INTAKE_FIELDS
}

ALLOWED_STAGE_TRANSITIONS: dict[str, set[str]] = {
    ORCHESTRATION_STAGE_INITIATED: {ORCHESTRATION_STAGE_SLACK, ORCHESTRATION_STAGE_VOICE},
    ORCHESTRATION_STAGE_SLACK: {ORCHESTRATION_STAGE_SLACK, ORCHESTRATION_STAGE_VOICE, ORCHESTRATION_STAGE_COMPLETED},
    ORCHESTRATION_STAGE_VOICE: {ORCHESTRATION_STAGE_VOICE, ORCHESTRATION_STAGE_COMPLETED},
    ORCHESTRATION_STAGE_COMPLETED: {ORCHESTRATION_STAGE_SOURCING, ORCHESTRATION_STAGE_CANDIDATES, ORCHESTRATION_STAGE_CLOSED, ORCHESTRATION_STAGE_COMPLETED},
    ORCHESTRATION_STAGE_SOURCING: {ORCHESTRATION_STAGE_CANDIDATES, ORCHESTRATION_STAGE_CLOSED, ORCHESTRATION_STAGE_SOURCING},
    ORCHESTRATION_STAGE_CANDIDATES: {ORCHESTRATION_STAGE_OUTREACH, ORCHESTRATION_STAGE_INTERVIEW, ORCHESTRATION_STAGE_PLACED, ORCHESTRATION_STAGE_CLOSED, ORCHESTRATION_STAGE_CANDIDATES},
    ORCHESTRATION_STAGE_OUTREACH: {ORCHESTRATION_STAGE_INTERVIEW, ORCHESTRATION_STAGE_PLACED, ORCHESTRATION_STAGE_CLOSED, ORCHESTRATION_STAGE_OUTREACH},
    ORCHESTRATION_STAGE_INTERVIEW: {ORCHESTRATION_STAGE_PLACED, ORCHESTRATION_STAGE_CLOSED, ORCHESTRATION_STAGE_INTERVIEW},
    ORCHESTRATION_STAGE_PLACED: {ORCHESTRATION_STAGE_CLOSED, ORCHESTRATION_STAGE_PLACED},
    ORCHESTRATION_STAGE_CLOSED: {ORCHESTRATION_STAGE_CLOSED},
}

SLACK_ACTION_NAMES = {
    "continue_in_slack",
    "resume_intake",
    "cancel_search",
    "confirm_intake",
    "start_sourcing",
    "continue_with_voice",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_list(value: Any, *, max_items: int = 12) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        return []

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _normalize_text(item)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= max_items:
            break
    return cleaned


def _extract_json(raw: str) -> dict[str, Any] | None:
    text = _normalize_text(raw)
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _confidence(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _stable_hash(*parts: Any) -> str:
    payload = "|".join(_normalize_text(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _question_spec(question_key: str) -> IntakeQuestionSpec | None:
    return INTAKE_QUESTION_REGISTRY.get(_normalize_text(question_key))


def _question_schema(question_key: str) -> dict[str, Any]:
    spec = _question_spec(question_key)
    if not spec:
        return {"key": question_key, "fieldName": "", "questionType": "unknown", "required": False, "minConfidence": 0.0}
    return {
        "key": spec.key,
        "fieldName": spec.field_name,
        "questionType": spec.question_type,
        "required": spec.required,
        "minConfidence": spec.min_confidence,
        "prompt": spec.prompt,
    }


def _field_for_question(question_key: str) -> str:
    spec = _question_spec(question_key)
    if spec and spec.field_name in INTAKE_FIELDS:
        return spec.field_name
    return ""


def _is_allowed_transition(current_stage: str, next_stage: str) -> bool:
    return next_stage in ALLOWED_STAGE_TRANSITIONS.get(current_stage, {current_stage, next_stage})


def _normalize_seniority(answer: str) -> str:
    text = _normalize_text(answer).lower()
    if not text:
        return ""
    if any(token in text for token in ("staff", "principal", "architect", "vp", "head of", "director", "cxo", "cto", "cpo")):
        return "senior leadership"
    if any(token in text for token in ("lead", "tech lead", "tl", "manager")):
        return "lead"
    if any(token in text for token in ("senior", "sr", "sen.", "experienced")):
        return "senior"
    if any(token in text for token in ("mid", "intermediate", "midsenior")):
        return "mid"
    if any(token in text for token in ("junior", "jr", "entry", "new grad", "graduate")):
        return "junior"
    return _normalize_text(answer)


def _normalize_urgency(answer: str) -> str:
    text = _normalize_text(answer).lower()
    if not text:
        return ""
    if any(token in text for token in ("asap", "immediate", "urgent", "this week", "hot")):
        return "high"
    if any(token in text for token in ("soon", "next month", "quarter")):
        return "medium"
    if any(token in text for token in ("whenever", "flexible", "when ready")):
        return "low"
    return _normalize_text(answer)


def _parse_compensation(answer: str) -> tuple[str, bool]:
    text = _normalize_text(answer)
    lowered = text.lower()
    if not text:
        return "", False
    if re.search(r"\d", text) or any(token in lowered for token in ("salary", "comp", "compensation", "range", "k", "usd", "base", "ote", "$")):
        return text, True
    return text, False


def _parse_location(answer: str) -> tuple[str, bool]:
    text = _normalize_text(answer)
    lowered = text.lower()
    if not text:
        return "", False
    if any(token in lowered for token in ("year", "yrs", "experience", "senior", "junior", "lead", "staff", "principal")) and not any(token in lowered for token in ("remote", "hybrid", "onsite", "on-site")):
        return text, False
    return text, True


def _normalize_field_value(field_name: str, answer: str) -> tuple[Any, bool, float, str]:
    text = _normalize_text(answer)
    if not text:
        return "", False, 0.0, "empty_answer"
    field_name = _normalize_text(field_name)
    if field_name in {"must_have_requirements", "skills", "hiring_signals", "tech_stack", "hiring_priorities"}:
        values = _normalize_list(re.split(r"[,\n;/]", text), max_items=12)
        if not values:
            return [], False, 0.0, "empty_list"
        return values, True, 0.8 if len(values) > 1 else 0.72, ""
    if field_name in {"success_profile", "culture_fit", "communication_style", "team_maturity", "leadership_expectations", "architecture_complexity", "urgency", "team_structure", "stakeholder_management"}:
        return text, True, 0.65 if len(text) > 8 else 0.58, ""
    if field_name == "location":
        normalized, accepted = _parse_location(text)
        return normalized, accepted, 0.85 if accepted else 0.25, "" if accepted else "answer_not_location"
    if field_name == "compensation":
        normalized, accepted = _parse_compensation(text)
        return normalized, accepted, 0.85 if accepted else 0.3, "" if accepted else "answer_not_compensation"
    if field_name == "seniority":
        return _normalize_seniority(text), True, 0.82, ""
    if field_name == "urgency":
        return _normalize_urgency(text), True, 0.8, ""
    return text, True, 0.75 if len(text) > 12 else 0.68, ""


def _build_question_context(question_key: str, question_text: str) -> dict[str, Any]:
    spec = _question_spec(question_key)
    return {
        "questionKey": question_key,
        "questionText": question_text,
        "questionType": spec.question_type if spec else "unknown",
        "fieldName": spec.field_name if spec else "",
        "required": bool(spec.required) if spec else False,
        "minConfidence": float(spec.min_confidence) if spec else 0.0,
    }


def _session_payload(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "sessionToken": row.session_token,
        "source": row.source,
        "currentStage": row.current_stage,
        "stateVersion": getattr(row, "state_version", 0) or 0,
        "slackTeamId": row.slack_team_id,
        "slackChannelId": row.slack_channel_id,
        "slackThreadTs": row.slack_thread_ts,
        "slackUserId": row.slack_user_id,
        "intakeMode": row.intake_mode,
        "selectedPath": row.selected_path,
        "currentQuestion": row.current_question,
        "currentQuestionKey": row.current_question_key,
        "currentQuestionType": getattr(row, "current_question_type", "") or "",
        "currentQuestionSchema": getattr(row, "current_question_schema", {}) or {},
        "structuredContext": row.structured_context or {},
        "rawConversation": row.raw_conversation or [],
        "normalizedIntake": row.normalized_intake or {},
        "voiceContext": row.voice_context or {},
        "slackContext": row.slack_context or {},
        "voiceHandoffToken": row.voice_handoff_token or "",
        "voiceHandoffExpiresAt": row.voice_handoff_expires_at.isoformat() if row.voice_handoff_expires_at else None,
        "voiceHandoffConsumedAt": row.voice_handoff_consumed_at.isoformat() if row.voice_handoff_consumed_at else None,
        "voiceTokenUsed": bool(row.voice_token_used),
        "lastProcessedMessageTs": getattr(row, "last_processed_message_ts", "") or "",
        "lastProcessedActionHash": getattr(row, "last_processed_action_hash", "") or "",
        "lastProcessedTranscriptHash": getattr(row, "last_processed_transcript_hash", "") or "",
        "intakeVersion": getattr(row, "intake_version", "") or "",
        "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
        "completedAt": row.completed_at.isoformat() if row.completed_at else None,
        "companyId": row.company_id,
        "jobId": row.job_id,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def _append_event(db: Session, *, session_id: str, event_type: str, payload: dict[str, Any] | None = None, source: str = ORCHESTRATION_SOURCE) -> None:
    OrchestrationEventRepository(db).create(
        session_id=session_id,
        event_type=event_type,
        event_payload=payload or {},
        source=source,
    )


def _ensure_system_user_id(db: Session) -> str:
    user_repo = UserRepository(db)
    user = user_repo.get_by_email(SYSTEM_SLACK_USER_EMAIL)
    if user:
        return str(user.id)
    user = user_repo.create(email=SYSTEM_SLACK_USER_EMAIL)
    db.flush()
    return str(user.id)


def _initial_intake_state() -> dict[str, Any]:
    return {
        "company_name": "",
        "role_title": "",
        "must_have_requirements": [],
        "success_profile": "",
        "skills": [],
        "seniority": "",
        "location": "",
        "compensation": "",
        "hiring_signals": [],
        "tech_stack": [],
        "hiring_priorities": [],
        "culture_fit": "",
        "communication_style": "",
        "team_maturity": "",
        "leadership_expectations": "",
        "architecture_complexity": "",
        "urgency": "",
        "team_structure": "",
        "stakeholder_management": "",
        "confidence_scores": {},
        "question_count": 0,
        "answer_count": 0,
        "completion_score": 0.0,
        "summary": "",
        "field_confidence": {},
        "field_status": {},
        "last_question_key": "",
    }


def _merge_lists(existing: Any, incoming: Any, *, limit: int = 12) -> list[str]:
    return _normalize_list([*(existing or []), *(incoming or [])], max_items=limit)


def _merge_answer_into_state(existing: dict[str, Any], extracted: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing or _initial_intake_state())
    field_confidence = dict(merged.get("field_confidence") or {})
    field_status = dict(merged.get("field_status") or {})
    for key in INTAKE_FIELDS:
        if key not in extracted:
            continue
        value = extracted.get(key)
        if isinstance(value, list):
            merged[key] = _merge_lists(merged.get(key, []), value)
        else:
            text = _normalize_text(value)
            if text:
                merged[key] = text
        if key in extracted.get("confidence_scores", {}):
            field_confidence[key] = _confidence(extracted["confidence_scores"][key], default=float(field_confidence.get(key, 0.0) or 0.0))
        if key in extracted.get("field_status", {}):
            field_status[key] = _normalize_text(extracted["field_status"][key]) or field_status.get(key, "")

    confidence_scores = dict(merged.get("confidence_scores") or {})
    extracted_confidence = extracted.get("confidence_scores")
    if isinstance(extracted_confidence, dict):
        for key, value in extracted_confidence.items():
            confidence_scores[str(key)] = _confidence(value, default=float(confidence_scores.get(str(key), 0.0) or 0.0))
    merged["confidence_scores"] = confidence_scores
    merged["field_confidence"] = field_confidence
    merged["field_status"] = field_status
    if extracted.get("last_question_key"):
        merged["last_question_key"] = _normalize_text(extracted.get("last_question_key"))

    for key in ("question_count", "answer_count"):
        try:
            merged[key] = int(merged.get(key) or 0)
        except (TypeError, ValueError):
            merged[key] = 0

    merged["question_count"] = max(0, int(merged.get("question_count") or 0))
    merged["answer_count"] = max(0, int(merged.get("answer_count") or 0))
    return merged


def _completed_score(intake: dict[str, Any]) -> float:
    observed = 0
    for key in (
        "company_name",
        "role_title",
        "must_have_requirements",
        "success_profile",
        "skills",
        "seniority",
        "location",
        "compensation",
        "hiring_signals",
        "tech_stack",
        "hiring_priorities",
        "culture_fit",
        "communication_style",
        "team_maturity",
        "leadership_expectations",
        "architecture_complexity",
        "urgency",
        "team_structure",
        "stakeholder_management",
    ):
        value = intake.get(key)
        if isinstance(value, list) and value:
            observed += 1
        elif _normalize_text(value):
            observed += 1
    return round(observed / 17.0, 4)


def _missing_fields(intake: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not _normalize_text(intake.get("company_name")):
        missing.append("company_name")
    if not _normalize_text(intake.get("role_title")):
        missing.append("role_title")
    if not _normalize_list(intake.get("must_have_requirements")):
        missing.append("must_have_requirements")
    if not _normalize_text(intake.get("success_profile")):
        missing.append("success_profile")
    if not _normalize_list(intake.get("skills")):
        missing.append("skills")
    if not _normalize_text(intake.get("seniority")):
        missing.append("seniority")
    if not _normalize_text(intake.get("location")):
        missing.append("location")
    if not _normalize_text(intake.get("compensation")):
        missing.append("compensation")
    if not _normalize_list(intake.get("hiring_signals")):
        missing.append("hiring_signals")
    if not _normalize_list(intake.get("tech_stack")):
        missing.append("tech_stack")
    if not _normalize_list(intake.get("hiring_priorities")):
        missing.append("hiring_priorities")
    if not _normalize_text(intake.get("culture_fit")):
        missing.append("culture_fit")
    if not _normalize_text(intake.get("communication_style")):
        missing.append("communication_style")
    if not _normalize_text(intake.get("team_maturity")):
        missing.append("team_maturity")
    if not _normalize_text(intake.get("leadership_expectations")):
        missing.append("leadership_expectations")
    if not _normalize_text(intake.get("architecture_complexity")):
        missing.append("architecture_complexity")
    if not _normalize_text(intake.get("urgency")):
        missing.append("urgency")
    if not _normalize_text(intake.get("team_structure")):
        missing.append("team_structure")
    if not _normalize_text(intake.get("stakeholder_management")):
        missing.append("stakeholder_management")
    return missing


def _missing_required_fields(intake: dict[str, Any]) -> list[str]:
    required_fields = [
        "company_name",
        "role_title",
        "must_have_requirements",
        "skills",
        "seniority",
        "location",
        "compensation",
    ]
    missing: list[str] = []
    for field in required_fields:
        value = intake.get(field)
        if isinstance(value, list) and value:
            continue
        if _normalize_text(value):
            continue
        missing.append(field)
    return missing


def _question_acceptance_threshold(question_key: str) -> float:
    spec = _question_spec(question_key)
    if not spec:
        return 0.7
    return spec.min_confidence


def _question_field_status(question_key: str, *, accepted: bool, confidence: float, reason: str = "") -> str:
    if not accepted:
        return f"rejected:{reason or 'validation_failed'}"
    spec = _question_spec(question_key)
    threshold = spec.min_confidence if spec else 0.7
    if confidence < threshold:
        return "low_confidence"
    return "accepted"


def _next_core_question(intake: dict[str, Any]) -> tuple[str, str] | None:
    for key, question, field in CORE_QUESTION_PLAN:
        value = intake.get(field)
        if isinstance(value, list) and value:
            continue
        if _normalize_text(value):
            continue
        return key, question
    return None


def _build_followup_prompt(*, intake: dict[str, Any], recent_conversation: list[dict[str, Any]], max_questions: int = 1) -> str:
    prompt = {
        "company_name": intake.get("company_name", ""),
        "role_title": intake.get("role_title", ""),
        "must_have_requirements": intake.get("must_have_requirements", []),
        "success_profile": intake.get("success_profile", ""),
        "skills": intake.get("skills", []),
        "seniority": intake.get("seniority", ""),
        "location": intake.get("location", ""),
        "compensation": intake.get("compensation", ""),
        "hiring_signals": intake.get("hiring_signals", []),
        "tech_stack": intake.get("tech_stack", []),
        "hiring_priorities": intake.get("hiring_priorities", []),
        "culture_fit": intake.get("culture_fit", ""),
        "communication_style": intake.get("communication_style", ""),
        "team_maturity": intake.get("team_maturity", ""),
        "leadership_expectations": intake.get("leadership_expectations", ""),
        "architecture_complexity": intake.get("architecture_complexity", ""),
        "urgency": intake.get("urgency", ""),
        "team_structure": intake.get("team_structure", ""),
        "stakeholder_management": intake.get("stakeholder_management", ""),
        "recent_conversation": recent_conversation[-6:],
    }
    return (
        "You are Adam, a senior recruiter assistant.\n"
        "Generate one highly contextual follow-up question for Slack or voice intake.\n"
        "Rules:\n"
        f"- Return ONLY valid JSON with schema: {{\"question\":\"...\",\"question_key\":\"...\",\"confidence\":0.0}}\n"
        "- Ask exactly one short question.\n"
        "- Make it specific to the hiring brief, not generic.\n"
        "- Prefer missing or ambiguous details that will change sourcing.\n"
        "- If the intake is already complete, ask a final confirmation question.\n\n"
        f"Context:\n{json.dumps(prompt, ensure_ascii=False, indent=2)}\n"
    )


def _parse_llm_question(payload: Any) -> tuple[str, str, float] | None:
    if isinstance(payload, dict):
        question = _normalize_text(payload.get("question"))
        question_key = _normalize_text(payload.get("question_key"))
        confidence = _confidence(payload.get("confidence"), default=0.0)
        if question:
            return question_key or "adaptive_followup", question, confidence
    return None


def _generate_adaptive_question(
    intake: dict[str, Any],
    recent_conversation: list[dict[str, Any]],
    *,
    current_question_key: str = "",
) -> tuple[str, str, float]:
    current_question_key = _normalize_text(current_question_key)
    if current_question_key in INTAKE_QUESTION_REGISTRY and current_question_key not in {"path_selection", "final_confirmation"}:
        spec = INTAKE_QUESTION_REGISTRY[current_question_key]
        field_name = spec.field_name
        if field_name in INTAKE_FIELDS and not _normalize_text(intake.get(field_name)) and not _normalize_list(intake.get(field_name)):
            return spec.key, spec.prompt, spec.min_confidence
    missing = _missing_fields(intake)
    for key in CORE_QUESTION_SEQUENCE:
        if key in missing:
            for plan_key, plan_question, _field in CORE_QUESTION_PLAN:
                if plan_key == key:
                    return plan_key, plan_question, 1.0
    if GROQ_API_KEY:
        try:
            payload = generate(_build_followup_prompt(intake=intake, recent_conversation=recent_conversation), expect_json=True)
            parsed = _parse_llm_question(payload)
            if parsed:
                return parsed
        except Exception as exc:
            logger.warning("orchestration_followup_llm_failed error=%s", str(exc))
    for key, question in FOLLOWUP_QUESTION_BANK:
        if key in missing:
            return key, question, 0.55
    return "final_confirmation", "Is there anything important we should still capture before I lock this intake?", 0.5


def _extract_answer_payload(
    *,
    question_key: str,
    question: str,
    answer: str,
    intake: dict[str, Any],
    recent_conversation: list[dict[str, Any]],
) -> dict[str, Any]:
    transcript = {
        "question_key": question_key,
        "question": question,
        "answer": answer,
        "current_intake": intake,
        "recent_conversation": recent_conversation[-6:],
    }
    field_name = _field_for_question(question_key)
    spec = _question_spec(question_key)
    accepted = False
    normalized_value: Any = ""
    confidence = 0.0
    status_reason = ""
    if GROQ_API_KEY:
        prompt = (
            "Extract structured hiring intake data from the recruiter answer.\n"
            "Return ONLY valid JSON with this schema:\n"
            "{\n"
            '  "field_name": "",\n'
            '  "field_value": "",\n'
            '  "field_values": [],\n'
            '  "confidence": 0.0,\n'
            '  "accepted": false,\n'
            '  "field_status": "",\n'
            '  "completion_confidence": 0.0,\n'
            '  "summary": ""\n'
            "}\n"
            "Only populate the field being asked right now. Do not map the answer into unrelated fields.\n"
            "For list fields, return arrays of short strings. For text fields, return concise normalized text.\n"
            "If the answer does not match the field, set accepted=false and keep field_value empty.\n"
            "Do not invent facts.\n\n"
            f"{json.dumps(transcript, ensure_ascii=False, indent=2)}\n"
        )
        try:
            payload = generate(prompt, expect_json=True)
            if isinstance(payload, dict):
                confidence = _confidence(payload.get("confidence"), default=0.0)
                accepted = bool(payload.get("accepted"))
                status_reason = _normalize_text(payload.get("field_status"))
                raw_value = payload.get("field_values") if isinstance(payload.get("field_values"), list) else payload.get("field_value")
                normalized_value, heuristic_accepted, heuristic_confidence, heuristic_reason = _normalize_field_value(field_name, raw_value if raw_value is not None else answer)
                if isinstance(normalized_value, list):
                    normalized_value = _normalize_list(normalized_value, max_items=spec.max_items if spec else 12)
                confidence = max(confidence, heuristic_confidence)
                accepted = accepted and heuristic_accepted if accepted else heuristic_accepted
                status_reason = status_reason or heuristic_reason
                if accepted and confidence >= max(_question_acceptance_threshold(question_key), spec.min_confidence if spec else 0.7):
                    return {
                        field_name: normalized_value,
                        "confidence_scores": {field_name: confidence},
                        "field_status": {field_name: "accepted"},
                        "completion_confidence": _confidence(payload.get("completion_confidence"), default=confidence),
                        "summary": _normalize_text(payload.get("summary")) or _normalize_text(answer),
                        "last_question_key": question_key,
                        "accepted": True,
                    }
        except Exception as exc:
            logger.warning("orchestration_answer_llm_failed error=%s", str(exc))

    normalized_value, heuristic_accepted, heuristic_confidence, status_reason = _normalize_field_value(field_name, answer)
    accepted = heuristic_accepted and heuristic_confidence >= _question_acceptance_threshold(question_key)
    status = "accepted" if accepted else ("low_confidence" if heuristic_accepted else f"rejected:{status_reason or 'validation_failed'}")
    if not accepted and heuristic_accepted and heuristic_confidence >= 0.55:
        status = "low_confidence"
    if isinstance(normalized_value, list):
        normalized_value = _normalize_list(normalized_value, max_items=spec.max_items if spec else 12)
    return {
        field_name: normalized_value if accepted or status == "low_confidence" else (normalized_value if field_name in {"must_have_requirements", "skills", "hiring_signals", "tech_stack", "hiring_priorities"} else ""),
        "confidence_scores": {field_name: heuristic_confidence},
        "field_status": {field_name: status},
        "completion_confidence": heuristic_confidence,
        "summary": _normalize_text(answer),
        "last_question_key": question_key,
        "accepted": accepted,
    }


def _compose_company_payload(intake: dict[str, Any]) -> dict[str, Any]:
    company_name = _normalize_text(intake.get("company_name")) or "Hiring Company"
    return {
        "name": company_name,
        "website": f"https://{hashlib.sha256(company_name.lower().encode('utf-8')).hexdigest()[:8]}.example.com",
        "description": _normalize_text(intake.get("company_description")) or "Hiring brief captured from Slack or voice intake.",
        "industry": _normalize_text(intake.get("industry")) or "Recruiting",
    }


def _compose_job_payload(intake: dict[str, Any]) -> dict[str, Any]:
    role_title = _normalize_text(intake.get("role_title")) or "Hiring Role"
    must_haves = _normalize_list(intake.get("must_have_requirements"), max_items=12)
    skills = _normalize_list(intake.get("skills"), max_items=12)
    hiring_priorities = _normalize_list(intake.get("hiring_priorities"), max_items=8)
    hiring_signals = _normalize_list(intake.get("hiring_signals"), max_items=8)
    description_parts = [
        f"Role: {role_title}",
        f"Why now: {_normalize_text(intake.get('urgency')) or 'Not specified'}",
        f"Team structure: {_normalize_text(intake.get('team_structure')) or 'Not specified'}",
        f"Success profile: {_normalize_text(intake.get('culture_fit')) or 'Not specified'}",
        f"Communication style: {_normalize_text(intake.get('communication_style')) or 'Not specified'}",
        f"Leadership expectations: {_normalize_text(intake.get('leadership_expectations')) or 'Not specified'}",
        f"Architecture complexity: {_normalize_text(intake.get('architecture_complexity')) or 'Not specified'}",
        f"Stakeholder management: {_normalize_text(intake.get('stakeholder_management')) or 'Not specified'}",
    ]
    if must_haves:
        description_parts.append("Must-haves: " + ", ".join(must_haves))
    if skills:
        description_parts.append("Skills: " + ", ".join(skills))
    if hiring_priorities:
        description_parts.append("Hiring priorities: " + ", ".join(hiring_priorities))
    if hiring_signals:
        description_parts.append("Signals: " + ", ".join(hiring_signals))

    location = _normalize_text(intake.get("location")) or "Remote"
    compensation = _normalize_text(intake.get("compensation"))
    seniority = _normalize_text(intake.get("seniority"))
    return {
        "title": role_title,
        "description": "\n".join(description_parts).strip(),
        "location": location,
        "compensation": compensation,
        "workAuthorization": "required",
        "remotePolicy": "remote" if "remote" in location.lower() else "",
        "experienceRequired": seniority,
        "vettingMode": "volume",
        "autoExportToAts": False,
    }


def _build_question_blocks(*, session_id: str, question_key: str, question: str, include_actions: bool = True, voice_token: str = "") -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Adam*: {question}",
            },
        }
    ]
    if include_actions:
        actions: list[dict[str, Any]] = [
            {
                "type": "button",
                "action_id": "continue_in_slack",
                "text": {"type": "plain_text", "text": "Continue in Slack"},
                "value": f"continue_in_slack:{session_id}:{question_key}",
            },
            {
                "type": "button",
                "action_id": "resume_intake",
                "text": {"type": "plain_text", "text": "Resume Intake"},
                "value": f"resume_intake:{session_id}:{question_key}",
            },
            {
                "type": "button",
                "action_id": "cancel_search",
                "text": {"type": "plain_text", "text": "Cancel Search"},
                "style": "danger",
                "value": f"cancel_search:{session_id}:{question_key}",
            },
            {
                "type": "button",
                "action_id": "confirm_intake",
                "text": {"type": "plain_text", "text": "Confirm Intake"},
                "style": "primary",
                "value": f"confirm_intake:{session_id}:{question_key}",
            },
            {
                "type": "button",
                "action_id": "start_sourcing",
                "text": {"type": "plain_text", "text": "Start Sourcing"},
                "style": "primary",
                "value": f"start_sourcing:{session_id}:{question_key}",
            },
        ]
        if voice_token:
            actions.insert(
                1,
                {
                    "type": "button",
                    "action_id": "continue_with_voice",
                    "text": {"type": "plain_text", "text": "Continue with Voice"},
                    "url": f"/voice?token={voice_token}",
                    "value": f"continue_with_voice:{session_id}:{question_key}",
                },
            )
        blocks.append({"type": "actions", "elements": actions})
    return blocks


def _ensure_session_row(
    db: Session,
    *,
    slack_team_id: str = "",
    slack_channel_id: str = "",
    slack_thread_ts: str = "",
    slack_user_id: str = "",
    source: str = ORCHESTRATION_SOURCE,
) -> Any:
    repo = OrchestrationSessionRepository(db)
    existing = repo.get_active_by_slack_context(
        slack_team_id=slack_team_id,
        slack_channel_id=slack_channel_id,
        slack_thread_ts=slack_thread_ts,
        slack_user_id=slack_user_id,
        source=source,
    )
    if existing:
        return existing
    session_token = secrets.token_urlsafe(24)
    row = repo.create(
        session_token=session_token,
        source=source,
        current_stage=ORCHESTRATION_STAGE_INITIATED,
        slack_team_id=slack_team_id,
        slack_channel_id=slack_channel_id,
        slack_thread_ts=slack_thread_ts,
        slack_user_id=slack_user_id,
        intake_mode="slack",
        selected_path="slack",
        structured_context={"question_plan": [key for key, _, _ in CORE_QUESTION_PLAN]},
        raw_conversation=[],
        normalized_intake=_initial_intake_state(),
        voice_context={},
        slack_context={
            "teamId": slack_team_id,
            "channelId": slack_channel_id,
            "threadTs": slack_thread_ts,
            "userId": slack_user_id,
        },
        company_id=None,
        job_id=None,
    )
    _append_event(db, session_id=row.id, event_type="SESSION_CREATED", payload=_session_payload(row))
    db.commit()
    return row


def _generate_voice_token(db: Session, session_row) -> dict[str, Any]:
    voice_context = dict(session_row.voice_context or {})
    active_token = _normalize_text(voice_context.get("handoffToken"))
    active_expires_at = _normalize_text(voice_context.get("handoffExpiresAt"))
    active_consumed_at = _normalize_text(voice_context.get("handoffConsumedAt"))
    if active_token and active_expires_at:
        try:
            expires_at = datetime.fromisoformat(active_expires_at)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at > _now() and not session_row.voice_token_used and not active_consumed_at:
                return {
                    "token": active_token,
                    "expiresAt": expires_at,
                }
        except ValueError:
            pass

    token = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(minutes=VOICE_TOKEN_TTL_MINUTES)
    voice_context["handoffToken"] = token
    voice_context["handoffExpiresAt"] = expires_at.isoformat()
    voice_context["handoffTokenCreatedAt"] = _now().isoformat()
    voice_context["handoffConsumedAt"] = ""
    session_row.voice_handoff_token = token
    session_row.voice_handoff_expires_at = expires_at
    session_row.voice_handoff_consumed_at = None
    session_row.voice_context = voice_context
    session_row.updated_at = _now()
    db.flush()
    return {
        "token": token,
        "expiresAt": expires_at,
    }


def _build_voice_payload(session_row, *, token: str, token_expires_at: datetime) -> dict[str, Any]:
    intake = session_row.normalized_intake or _initial_intake_state()
    recent_conversation = list(session_row.raw_conversation or [])
    question_key, question, confidence = _generate_adaptive_question(
        intake,
        recent_conversation,
        current_question_key=_normalize_text(getattr(session_row, "current_question_key", "")),
    )
    question_schema = _question_schema(question_key)
    variable_values = {
        "sessionToken": session_row.session_token,
        "sessionId": session_row.id,
        "currentStage": session_row.current_stage,
        "companyName": intake.get("company_name", ""),
        "roleTitle": intake.get("role_title", ""),
        "mustHaveRequirements": json.dumps(intake.get("must_have_requirements", []), ensure_ascii=False),
        "skills": json.dumps(intake.get("skills", []), ensure_ascii=False),
        "seniority": intake.get("seniority", ""),
        "location": intake.get("location", ""),
        "compensation": intake.get("compensation", ""),
        "teamStructure": intake.get("team_structure", ""),
        "cultureFit": intake.get("culture_fit", ""),
        "communicationStyle": intake.get("communication_style", ""),
        "leadershipExpectations": intake.get("leadership_expectations", ""),
        "architectureComplexity": intake.get("architecture_complexity", ""),
        "stakeholderManagement": intake.get("stakeholder_management", ""),
        "urgency": intake.get("urgency", ""),
        "currentQuestion": question,
        "currentQuestionKey": question_key,
        "currentQuestionType": question_schema.get("questionType", ""),
        "conversationSummary": (session_row.structured_context or {}).get("summary", ""),
        "normalizedIntake": json.dumps(intake, ensure_ascii=False),
    }
    first_message = (
        f"Let's continue the hiring intake from Slack. {question}"
        if question
        else "Let's continue the hiring intake from Slack."
    )
    session_row.voice_context = {
        **dict(session_row.voice_context or {}),
        "lastVoiceQuestion": question,
        "lastVoiceQuestionKey": question_key,
        "lastVoiceQuestionConfidence": confidence,
        "lastVoiceQuestionSchema": question_schema,
        "voiceToken": token,
        "voiceTokenExpiresAt": token_expires_at.isoformat(),
        "lastPreparedAt": _now().isoformat(),
    }
    session_row.current_stage = ORCHESTRATION_STAGE_VOICE
    session_row.selected_path = "voice"
    session_row.current_question = question
    session_row.current_question_key = question_key
    session_row.current_question_type = question_schema.get("questionType", "")
    session_row.current_question_schema = question_schema
    session_row.updated_at = _now()
    return {
        "token": token,
        "tokenExpiresAt": token_expires_at.isoformat(),
        "session": _session_payload(session_row),
        "firstMessage": first_message,
        "variableValues": variable_values,
        "currentQuestion": question,
        "currentQuestionKey": question_key,
        "currentQuestionSchema": question_schema,
        "confidence": confidence,
    }


def _build_slack_thread_message(
    *,
    session_row,
    question_key: str,
    question: str,
    voice_token: str = "",
    with_buttons: bool = True,
) -> dict[str, Any]:
    blocks = _build_question_blocks(
        session_id=session_row.id,
        question_key=question_key,
        question=question,
        include_actions=with_buttons,
        voice_token=voice_token,
    )
    return {
        "text": question,
        "blocks": blocks,
    }


def _update_session_after_answer(
    *,
    session_row,
    question: str,
    question_key: str,
    answer: str,
    source: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if _normalize_text(getattr(session_row, "current_question_key", "")) and _normalize_text(session_row.current_question_key) != _normalize_text(question_key):
        return {
            "accepted": False,
            "field_status": {question_key: "rejected:stale_question"},
            "confidence_scores": {question_key: 0.0},
            "completion_confidence": 0.0,
            "summary": _normalize_text(answer),
            "last_question_key": question_key,
        }

    normalized_intake = dict(session_row.normalized_intake or _initial_intake_state())
    recent_conversation = list(session_row.raw_conversation or [])

    extracted = _extract_answer_payload(
        question_key=question_key,
        question=question,
        answer=answer,
        intake=normalized_intake,
        recent_conversation=recent_conversation,
    )
    field_name = _field_for_question(question_key)
    accepted = bool(extracted.get("accepted"))
    field_status = dict(extracted.get("field_status") or {})
    confidence_scores = dict(extracted.get("confidence_scores") or {})
    answer_confidence = _confidence(confidence_scores.get(field_name) if field_name else 0.0, default=0.0)
    if accepted and field_name:
        normalized_intake = _merge_answer_into_state(normalized_intake, extracted)
        normalized_intake["summary"] = _normalize_text(extracted.get("summary")) or _normalize_text(answer)
    else:
        normalized_intake["summary"] = _normalize_text(normalized_intake.get("summary")) or _normalize_text(answer)

    normalized_intake["answer_count"] = int(normalized_intake.get("answer_count") or 0) + 1
    normalized_intake["question_count"] = int(normalized_intake.get("question_count") or 0) + 1
    normalized_intake["completion_score"] = _completed_score(normalized_intake)
    normalized_intake["last_question_key"] = question_key
    if field_name:
        field_confidence = dict(normalized_intake.get("field_confidence") or {})
        field_confidence[field_name] = answer_confidence
        normalized_intake["field_confidence"] = field_confidence
        field_status_store = dict(normalized_intake.get("field_status") or {})
        field_status_store[field_name] = field_status.get(field_name) or ("accepted" if accepted else "low_confidence")
        normalized_intake["field_status"] = field_status_store
    normalized_intake["confidence_scores"] = {
        **dict(normalized_intake.get("confidence_scores") or {}),
        **confidence_scores,
    }

    raw_row = {
        "question": question,
        "questionKey": question_key,
        "answer": answer,
        "timestamp": _now().isoformat(),
        "source": source,
        "metadata": metadata or {},
        "accepted": accepted,
        "confidence": answer_confidence,
        "fieldName": field_name,
    }
    recent_conversation.append(raw_row)

    session_row.raw_conversation = recent_conversation
    session_row.normalized_intake = normalized_intake
    session_row.structured_context = {
        **dict(session_row.structured_context or {}),
        "completionScore": normalized_intake["completion_score"],
        "missingFields": _missing_fields(normalized_intake),
        "missingRequiredFields": _missing_required_fields(normalized_intake),
        "summary": normalized_intake["summary"],
        "questionPlan": [key for key, _, _ in CORE_QUESTION_PLAN],
        "lastQuestionKey": question_key,
        "lastAnswerAccepted": accepted,
        "lastAnswerConfidence": answer_confidence,
        "needsClarification": not accepted,
    }
    session_row.updated_at = _now()
    session_row.state_version = int(getattr(session_row, "state_version", 0) or 0) + 1
    return extracted


def _mark_session_complete(session_row) -> None:
    if not _is_allowed_transition(session_row.current_stage, ORCHESTRATION_STAGE_COMPLETED):
        logger.warning(
            "orchestration_invalid_stage_transition session_id=%s current_stage=%s next_stage=%s",
            getattr(session_row, "id", ""),
            session_row.current_stage,
            ORCHESTRATION_STAGE_COMPLETED,
        )
    session_row.current_stage = ORCHESTRATION_STAGE_COMPLETED
    session_row.completed_at = _now()
    session_row.updated_at = _now()
    session_row.state_version = int(getattr(session_row, "state_version", 0) or 0) + 1


def _session_is_complete(session_row) -> bool:
    intake = session_row.normalized_intake or _initial_intake_state()
    score = _completed_score(intake)
    session_row.structured_context = {
        **dict(session_row.structured_context or {}),
        "completionScore": score,
        "missingFields": _missing_fields(intake),
        "missingRequiredFields": _missing_required_fields(intake),
    }
    required_missing = _missing_required_fields(intake)
    return score >= 0.75 and not required_missing


def _finalize_sourcing(db: Session, session_row) -> dict[str, Any]:
    if session_row.job_id:
        logger.info("orchestration_finalize_idempotent session_id=%s job_id=%s", session_row.id, session_row.job_id)
        recruiter_id = _normalize_text(session_row.slack_user_id or (dict(session_row.slack_context or {})).get("userId") or "")
        calibration_state = None
        if recruiter_id:
            calibration_state = bootstrap_preference_calibration_session(
                db=db,
                recruiter_id=recruiter_id,
                job_id=session_row.job_id,
                voice_summary=str((session_row.structured_context or {}).get("voiceSummary") or ""),
                gap_analysis=dict((session_row.structured_context or {}).get("gapAnalysis") or {}),
            )
            session_row.structured_context = {
                **dict(session_row.structured_context or {}),
                "calibrationState": build_calibration_state_response(calibration_state),
                "calibrationStage": calibration_state.get("stage", "archetype_calibration"),
                "calibrationStartedAt": (session_row.structured_context or {}).get("calibrationStartedAt") or _now().isoformat(),
            }
            session_row.current_stage = ORCHESTRATION_STAGE_CALIBRATION
            session_row.updated_at = _now()
            session_row.state_version = int(getattr(session_row, "state_version", 0) or 0) + 1
            db.commit()
        return {
            "jobId": session_row.job_id,
            "companyId": session_row.company_id,
            "calibration": build_calibration_state_response(calibration_state) if calibration_state else None,
            "idempotent": True,
        }

    intake = session_row.normalized_intake or _initial_intake_state()
    company_payload = _compose_company_payload(intake)
    job_payload = _compose_job_payload(intake)

    existing_company = None
    if session_row.company_id:
        existing_company = CompanyRepository(db).get_by_id(session_row.company_id)
    if existing_company:
        company_payload["name"] = existing_company.name
        company_payload["website"] = existing_company.website
        company_payload["description"] = existing_company.description
        company_payload["industry"] = existing_company.industry

    user_id = _ensure_system_user_id(db)

    job_id = create_hiring_job(
        db=db,
        user_id=user_id,
        company=company_payload,
        job=job_payload,
    )

    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found after creation", status_code=404)

    session_row.company_id = job.company_id
    session_row.job_id = job.id
    session_row.current_stage = ORCHESTRATION_STAGE_CALIBRATION
    session_row.updated_at = _now()
    session_row.state_version = int(getattr(session_row, "state_version", 0) or 0) + 1
    _append_event(db, session_id=session_row.id, event_type="INTAKE_COMPLETED", payload={"jobId": job.id, "companyId": job.company_id, "intake": intake})
    _append_event(db, session_id=session_row.id, event_type="CALIBRATION_STARTED", payload={"jobId": job.id, "companyId": job.company_id})

    recruiter_id = _normalize_text(session_row.slack_user_id or (dict(session_row.slack_context or {})).get("userId") or "")
    calibration_state = None
    if recruiter_id:
        calibration_state = bootstrap_preference_calibration_session(
            db=db,
            recruiter_id=recruiter_id,
            job_id=job.id,
            voice_summary=str((session_row.structured_context or {}).get("voiceSummary") or ""),
            gap_analysis=dict((session_row.structured_context or {}).get("gapAnalysis") or {}),
        )
        session_row.structured_context = {
            **dict(session_row.structured_context or {}),
            "calibrationState": build_calibration_state_response(calibration_state),
            "calibrationStage": calibration_state.get("stage", "archetype_calibration"),
            "calibrationStartedAt": _now().isoformat(),
        }

    session_row.current_stage = ORCHESTRATION_STAGE_CALIBRATION
    session_row.completed_at = _now()
    session_row.structured_context = {
        **dict(session_row.structured_context or {}),
        "finalJobId": job.id,
        "finalCompanyId": job.company_id,
        "calibrationState": build_calibration_state_response(calibration_state) if calibration_state else {},
        "calibrationStarted": True,
        "completed": True,
    }
    session_row.updated_at = _now()
    session_row.state_version = int(getattr(session_row, "state_version", 0) or 0) + 1
    db.commit()
    return {
        "jobId": job.id,
        "companyId": job.company_id,
        "calibration": build_calibration_state_response(calibration_state) if calibration_state else None,
    }


def start_or_resume_slack_intake(
    *,
    db: Session,
    slack_team_id: str,
    slack_channel_id: str,
    slack_thread_ts: str = "",
    slack_user_id: str = "",
    initial_brief: str = "",
) -> dict[str, Any]:
    session_row = _ensure_session_row(
        db,
        slack_team_id=slack_team_id,
        slack_channel_id=slack_channel_id,
        slack_thread_ts=slack_thread_ts,
        slack_user_id=slack_user_id,
    )
    brief = _normalize_text(initial_brief)
    if brief:
        session_row.structured_context = {
            **dict(session_row.structured_context or {}),
            "initialBrief": brief,
            "initialBriefSource": "slash_command",
        }
        session_row.updated_at = _now()
    intake = session_row.normalized_intake or _initial_intake_state()
    next_question = _next_core_question(intake)
    path_selection_needed = next_question is None
    if not path_selection_needed:
        next_question_key, next_question_text = next_question
        confidence = 1.0
    else:
        next_question_key = "path_selection"
        next_question_text = "Core intake looks good. Choose whether to continue in Slack or switch to Voice."
        confidence = 1.0
    question_schema = _question_schema(next_question_key)

    if not _is_allowed_transition(session_row.current_stage, ORCHESTRATION_STAGE_SLACK):
        logger.warning(
            "orchestration_invalid_stage_transition session_id=%s current_stage=%s next_stage=%s",
            session_row.id,
            session_row.current_stage,
            ORCHESTRATION_STAGE_SLACK,
        )
    session_row.current_stage = ORCHESTRATION_STAGE_SLACK
    session_row.selected_path = "slack"
    session_row.current_question_key = next_question_key
    session_row.current_question = next_question_text
    session_row.current_question_type = question_schema.get("questionType", "")
    session_row.current_question_schema = question_schema
    session_row.slack_context = {
        **dict(session_row.slack_context or {}),
        "teamId": slack_team_id,
        "channelId": slack_channel_id,
        "threadTs": slack_thread_ts,
        "userId": slack_user_id,
        "nextQuestionConfidence": confidence,
        "workflowId": session_row.id,
    }
    session_row.updated_at = _now()
    session_row.state_version = int(getattr(session_row, "state_version", 0) or 0) + 1
    _append_event(
        db,
        session_id=session_row.id,
        event_type="QUESTION_ASKED",
        payload={
            "question": next_question_text,
            "questionKey": next_question_key,
            "questionSchema": question_schema,
            "source": "slack",
            "initialBrief": brief,
            "stateVersion": session_row.state_version,
        },
    )
    db.commit()

    result = {
        "session": _session_payload(session_row),
        "questionKey": next_question_key,
        "question": next_question_text,
        "questionConfidence": confidence,
        "pathSelectionNeeded": path_selection_needed,
    }
    logger.info(
        "orchestration_session_started session_id=%s channel_id=%s user_id=%s question_key=%s",
        session_row.id,
        slack_channel_id,
        slack_user_id,
        next_question_key,
    )
    return result


def process_slack_answer(
    *,
    db: Session,
    slack_team_id: str,
    slack_channel_id: str,
    slack_user_id: str,
    thread_ts: str,
    answer: str,
    timestamp: str,
) -> dict[str, Any]:
    session_repo = OrchestrationSessionRepository(db)
    session_row = session_repo.get_active_by_slack_context(
        slack_team_id=slack_team_id,
        slack_channel_id=slack_channel_id,
        slack_thread_ts=thread_ts,
        slack_user_id=slack_user_id,
        source=ORCHESTRATION_SOURCE,
    )
    if not session_row:
        session_row = session_repo.get_active_by_slack_context(
            slack_team_id=slack_team_id,
            slack_channel_id=slack_channel_id,
            slack_user_id=slack_user_id,
            source=ORCHESTRATION_SOURCE,
        )
    if not session_row:
        raise APIError("No active orchestration session found", status_code=404)

    if thread_ts and not _normalize_text(getattr(session_row, "slack_thread_ts", "")):
        session_row.slack_thread_ts = thread_ts
        session_row.updated_at = _now()
        db.flush()

    question = _normalize_text(session_row.current_question)
    question_key = _normalize_text(session_row.current_question_key) or "adaptive_followup"
    if _normalize_text(getattr(session_row, "last_processed_message_ts", "")) == _normalize_text(timestamp):
        logger.info(
            "orchestration_answer_duplicate session_id=%s timestamp=%s",
            session_row.id,
            timestamp,
        )
        return {
            "completed": _session_is_complete(session_row),
            "session": _session_payload(session_row),
            "nextQuestion": question,
            "nextQuestionKey": question_key,
            "questionConfidence": float((session_row.structured_context or {}).get("lastAnswerConfidence") or 0.0),
            "normalizedIntake": session_row.normalized_intake,
            "duplicate": True,
            "pathSelectionNeeded": False,
        }

    if session_row.current_stage not in {ORCHESTRATION_STAGE_SLACK, ORCHESTRATION_STAGE_VOICE, ORCHESTRATION_STAGE_COMPLETED}:
        raise APIError("Session is not accepting intake answers", status_code=409)

    _append_event(
        db,
        session_id=session_row.id,
        event_type="ANSWER_RECEIVED",
        payload={
            "question": question,
            "questionKey": question_key,
            "answer": answer,
            "timestamp": timestamp,
            "source": "slack",
            "stateVersion": getattr(session_row, "state_version", 0) or 0,
        },
    )
    extracted = _update_session_after_answer(
        session_row=session_row,
        question=question,
        question_key=question_key,
        answer=answer,
        source="slack",
        metadata={"timestamp": timestamp},
    )
    session_row.last_processed_message_ts = timestamp
    _append_event(
        db,
        session_id=session_row.id,
        event_type="ANSWER_NORMALIZED",
        payload={
            "questionKey": question_key,
            "extracted": extracted,
            "normalizedIntake": session_row.normalized_intake,
            "stateVersion": getattr(session_row, "state_version", 0) or 0,
        },
    )

    accepted = bool(extracted.get("accepted"))
    path_selection_needed = _next_core_question(session_row.normalized_intake or _initial_intake_state()) is None
    if _session_is_complete(session_row) and path_selection_needed:
        session_row.current_stage = ORCHESTRATION_STAGE_SLACK
        session_row.selected_path = "slack"
        session_row.current_question_key = "path_selection"
        session_row.current_question = "Core intake looks good. Choose whether to continue in Slack or switch to Voice."
        session_row.current_question_type = _question_schema("path_selection").get("questionType", "")
        session_row.current_question_schema = _question_schema("path_selection")
        session_row.updated_at = _now()
        session_row.state_version = int(getattr(session_row, "state_version", 0) or 0) + 1
        db.commit()
        return {
            "completed": False,
            "session": _session_payload(session_row),
            "nextQuestion": session_row.current_question,
            "nextQuestionKey": "path_selection",
            "questionConfidence": 1.0,
            "normalizedIntake": session_row.normalized_intake,
            "pathSelectionNeeded": True,
            "needsClarification": False,
        }

    if _session_is_complete(session_row):
        _mark_session_complete(session_row)
        _append_event(db, session_id=session_row.id, event_type="INTAKE_COMPLETED", payload={"source": "slack"})
        db.commit()
        return {
            "completed": True,
            "session": _session_payload(session_row),
            "nextQuestion": "",
            "nextQuestionKey": "",
            "questionConfidence": 0.0,
        }

    session_row.current_stage = ORCHESTRATION_STAGE_SLACK
    session_row.selected_path = "slack"
    path_selection_needed = _next_core_question(session_row.normalized_intake or _initial_intake_state()) is None
    if not accepted:
        next_question_key = question_key
        next_question_text = question
        question_confidence = float((extracted.get("confidence_scores") or {}).get(_field_for_question(question_key)) or 0.0)
    elif path_selection_needed:
        next_question_key = "path_selection"
        next_question_text = "Core intake looks good. Choose whether to continue in Slack or switch to Voice."
        question_confidence = 1.0
    else:
        next_core = _next_core_question(session_row.normalized_intake or _initial_intake_state())
        if next_core:
            next_question_key, next_question_text = next_core
            question_confidence = 1.0
        else:
            next_question_key, next_question_text, question_confidence = _generate_adaptive_question(
                session_row.normalized_intake or _initial_intake_state(),
                session_row.raw_conversation or [],
                current_question_key=question_key,
            )
    question_schema = _question_schema(next_question_key)
    session_row.current_question_key = next_question_key
    session_row.current_question = next_question_text
    session_row.current_question_type = question_schema.get("questionType", "")
    session_row.current_question_schema = question_schema
    session_row.updated_at = _now()
    session_row.state_version = int(getattr(session_row, "state_version", 0) or 0) + 1
    _append_event(
        db,
        session_id=session_row.id,
        event_type="QUESTION_ASKED",
        payload={
            "question": next_question_text,
            "questionKey": next_question_key,
            "questionSchema": question_schema,
            "source": "slack",
            "stateVersion": session_row.state_version,
            "clarification": not accepted,
        },
    )
    db.commit()
    return {
        "completed": False,
        "session": _session_payload(session_row),
        "nextQuestion": next_question_text,
        "nextQuestionKey": next_question_key,
        "questionConfidence": question_confidence,
        "normalizedIntake": session_row.normalized_intake,
        "pathSelectionNeeded": path_selection_needed,
        "needsClarification": not accepted,
    }


def handle_slack_action(
    *,
    db: Session,
    action: str,
    session_id: str,
    slack_channel_id: str,
    slack_user_id: str,
    thread_ts: str,
    question_key: str = "",
) -> dict[str, Any]:
    repo = OrchestrationSessionRepository(db)
    session_row = repo.get(session_id)
    if not session_row:
        raise APIError("Orchestration session not found", status_code=404)

    normalized_action = (action or "").strip().lower()
    requested_question_key = _normalize_text(question_key)
    current_question_key = _normalize_text(session_row.current_question_key)
    action_hash = _stable_hash(session_row.id, normalized_action, slack_channel_id, slack_user_id, thread_ts, requested_question_key, current_question_key)
    if _normalize_text(getattr(session_row, "last_processed_action_hash", "")) == action_hash:
        logger.info("orchestration_action_duplicate session_id=%s action=%s", session_row.id, normalized_action)
        return {"ok": True, "duplicate": True, "session": _session_payload(session_row)}

    if normalized_action in {"confirm_intake", "start_sourcing", "continue_with_voice"}:
        if requested_question_key and current_question_key and requested_question_key != current_question_key:
            logger.warning(
                "orchestration_action_stale session_id=%s action=%s requested_question_key=%s current_question_key=%s",
                session_row.id,
                normalized_action,
                requested_question_key,
                current_question_key,
            )
            return {
                "ok": False,
                "status": "stale_action",
                "session": _session_payload(session_row),
                "currentQuestion": session_row.current_question,
                "currentQuestionKey": current_question_key,
            }

    _append_event(
        db,
        session_id=session_row.id,
        event_type="BUTTON_CLICKED",
        payload={
            "action": normalized_action,
            "channelId": slack_channel_id,
            "userId": slack_user_id,
            "threadTs": thread_ts,
            "questionKey": requested_question_key,
            "currentQuestionKey": current_question_key,
            "actionHash": action_hash,
            "stateVersion": getattr(session_row, "state_version", 0) or 0,
        },
    )
    session_row.last_processed_action_hash = action_hash

    if normalized_action == "cancel_search":
        session_row.current_stage = ORCHESTRATION_STAGE_CLOSED
        session_row.completed_at = _now()
        session_row.updated_at = _now()
        session_row.state_version = int(getattr(session_row, "state_version", 0) or 0) + 1
        _append_event(db, session_id=session_row.id, event_type="SEARCH_CANCELLED", payload={"source": "slack"})
        db.commit()
        return {"ok": True, "status": "cancelled"}

    if normalized_action == "resume_intake" or normalized_action == "continue_in_slack":
        session_row.current_stage = ORCHESTRATION_STAGE_SLACK
        session_row.selected_path = "slack"
        session_row.slack_channel_id = slack_channel_id or session_row.slack_channel_id
        session_row.slack_user_id = slack_user_id or session_row.slack_user_id
        session_row.slack_thread_ts = thread_ts or session_row.slack_thread_ts
        session_row.updated_at = _now()
        session_row.state_version = int(getattr(session_row, "state_version", 0) or 0) + 1
        next_question_key, next_question_text, question_confidence = _generate_adaptive_question(
            session_row.normalized_intake or _initial_intake_state(),
            session_row.raw_conversation or [],
            current_question_key=current_question_key,
        )
        question_schema = _question_schema(next_question_key)
        session_row.current_question_key = next_question_key
        session_row.current_question = next_question_text
        session_row.current_question_type = question_schema.get("questionType", "")
        session_row.current_question_schema = question_schema
        _append_event(
            db,
            session_id=session_row.id,
            event_type="QUESTION_ASKED",
            payload={
                "question": next_question_text,
                "questionKey": next_question_key,
                "questionSchema": question_schema,
                "source": "slack",
                "stateVersion": session_row.state_version,
            },
        )
        db.commit()
        return {
            "ok": True,
            "question": next_question_text,
            "questionKey": next_question_key,
            "confidence": question_confidence,
            "session": _session_payload(session_row),
        }

    if normalized_action == "continue_with_voice":
        token_data = _generate_voice_token(db, session_row)
        db.commit()
        _append_event(db, session_id=session_row.id, event_type="VOICE_TOKEN_ISSUED", payload={"tokenExpiresAt": token_data["expiresAt"].isoformat(), "source": "slack"})
        db.commit()
        return {
            "ok": True,
            "voiceToken": token_data["token"],
            "voiceTokenExpiresAt": token_data["expiresAt"].isoformat(),
            "session": _session_payload(session_row),
            "voiceUrl": f"/voice?token={token_data['token']}",
        }

    if normalized_action == "confirm_intake" or normalized_action == "start_sourcing":
        if not _session_is_complete(session_row):
            missing_required = _missing_required_fields(session_row.normalized_intake or _initial_intake_state())
            next_question_key, next_question_text, question_confidence = _generate_adaptive_question(
                session_row.normalized_intake or _initial_intake_state(),
                session_row.raw_conversation or [],
                current_question_key=current_question_key,
            )
            question_schema = _question_schema(next_question_key)
            session_row.current_question_key = next_question_key
            session_row.current_question = next_question_text
            session_row.current_question_type = question_schema.get("questionType", "")
            session_row.current_question_schema = question_schema
            session_row.current_stage = ORCHESTRATION_STAGE_SLACK
            session_row.updated_at = _now()
            session_row.state_version = int(getattr(session_row, "state_version", 0) or 0) + 1
            _append_event(
                db,
                session_id=session_row.id,
                event_type="INTAKE_REJECTED_FOR_COMPLETENESS",
                payload={
                    "source": "slack",
                    "missingRequiredFields": missing_required,
                    "nextQuestionKey": next_question_key,
                    "nextQuestion": next_question_text,
                    "stateVersion": session_row.state_version,
                },
            )
            db.commit()
            return {
                "ok": True,
                "status": "needs_clarification",
                "question": next_question_text,
                "questionKey": next_question_key,
                "confidence": question_confidence,
                "missingRequiredFields": missing_required,
                "session": _session_payload(session_row),
            }
        _mark_session_complete(session_row)
        db.commit()
        result = _finalize_sourcing(db, session_row)
        return {"ok": True, **result}

    raise APIError(f"Unsupported orchestration action '{action}'", status_code=400)


def prepare_voice_handoff(*, db: Session, session_id: str) -> dict[str, Any]:
    session_row = OrchestrationSessionRepository(db).get(session_id)
    if not session_row:
        raise APIError("Orchestration session not found", status_code=404)
    token_data = _generate_voice_token(db, session_row)
    db.commit()
    return {
        "voiceToken": token_data["token"],
        "voiceTokenExpiresAt": token_data["expiresAt"].isoformat(),
        "session": _session_payload(session_row),
        "voiceUrl": f"/voice?token={token_data['token']}",
    }


def start_voice_handoff(*, db: Session, token: str) -> dict[str, Any]:
    session_repo = OrchestrationSessionRepository(db)
    session_row = session_repo.get_by_voice_handoff_token(token)
    if not session_row:
        raise APIError("Orchestration session not found", status_code=404)
    if _normalize_text(session_row.voice_handoff_token) != _normalize_text(token):
        raise APIError("Invalid voice handoff token", status_code=404)
    expires_at = session_row.voice_handoff_expires_at
    if expires_at and expires_at <= _now():
        raise APIError("Voice handoff token expired", status_code=410)
    if session_row.voice_handoff_consumed_at is not None or session_row.voice_token_used:
        existing_voice_context = dict(session_row.voice_context or {})
        if _normalize_text(existing_voice_context.get("voiceToken")) == _normalize_text(token):
            logger.info("voice_handoff_idempotent session_id=%s token=%s", session_row.id, token[:8])
            return existing_voice_context
        raise APIError("Voice handoff token already used", status_code=410)

    session_row.voice_handoff_consumed_at = _now()
    session_row.voice_token_used = True
    session_row.current_stage = ORCHESTRATION_STAGE_VOICE
    session_row.selected_path = "voice"
    session_row.updated_at = _now()
    _append_event(db, session_id=session_row.id, event_type="VOICE_STARTED", payload={"token": token, "source": "voice"})
    next_question_key, next_question_text, question_confidence = _generate_adaptive_question(
        session_row.normalized_intake or _initial_intake_state(),
        session_row.raw_conversation or [],
        current_question_key=_normalize_text(session_row.current_question_key),
    )
    question_schema = _question_schema(next_question_key)
    session_row.current_question_key = next_question_key
    session_row.current_question = next_question_text
    session_row.current_question_type = question_schema.get("questionType", "")
    session_row.current_question_schema = question_schema
    voice_payload = _build_voice_payload(
        session_row,
        token=token,
        token_expires_at=expires_at or (_now() + timedelta(minutes=VOICE_TOKEN_TTL_MINUTES)),
    )
    session_row.voice_context = {
        **dict(session_row.voice_context or {}),
        **voice_payload,
        "questionConfidence": question_confidence,
        "questionSchema": question_schema,
    }
    session_row.state_version = int(getattr(session_row, "state_version", 0) or 0) + 1
    db.commit()
    logger.info("voice_handoff_started session_id=%s token=%s", session_row.id, token[:8])
    return voice_payload


def complete_voice_handoff(
    *,
    db: Session,
    token: str,
    transcript: str,
    voice_notes: list[str] | None = None,
) -> dict[str, Any]:
    session_repo = OrchestrationSessionRepository(db)
    session_row = session_repo.get_by_voice_handoff_token(token)
    if not session_row:
        raise APIError("Invalid voice handoff token", status_code=404)
    if _normalize_text(session_row.voice_handoff_token) != _normalize_text(token):
        raise APIError("Invalid voice handoff token", status_code=404)

    combined_text = _normalize_text(transcript)
    if voice_notes:
        combined_text = "\n".join([part for part in [combined_text, *[_normalize_text(item) for item in voice_notes if _normalize_text(item)]] if part]).strip()
    if not combined_text:
        raise APIError("Transcript is required", status_code=400)

    transcript_hash = _stable_hash(session_row.id, token, combined_text)
    if _normalize_text(getattr(session_row, "last_processed_transcript_hash", "")) == transcript_hash:
        logger.info("voice_transcript_duplicate session_id=%s token=%s", session_row.id, token[:8])
        calibration_state = None
        finalization = None
        if session_row.job_id:
            finalization = {
                "jobId": session_row.job_id,
                "companyId": session_row.company_id,
            }
            recruiter_id = _normalize_text(session_row.slack_user_id or (dict(session_row.slack_context or {})).get("userId") or "")
            if recruiter_id:
                calibration_state = bootstrap_preference_calibration_session(
                    db=db,
                    recruiter_id=recruiter_id,
                    job_id=session_row.job_id,
                    voice_summary=str((session_row.structured_context or {}).get("voiceSummary") or ""),
                    gap_analysis=dict((session_row.structured_context or {}).get("gapAnalysis") or {}),
                )
                finalization["calibration"] = build_calibration_state_response(calibration_state)
        return {
            "completed": _session_is_complete(session_row),
            "session": _session_payload(session_row),
            "duplicate": True,
            "finalization": finalization if session_row.job_id else None,
            "calibration": build_calibration_state_response(calibration_state) if calibration_state else None,
        }

    session_row.last_processed_transcript_hash = transcript_hash

    _append_event(
        db,
        session_id=session_row.id,
        event_type="VOICE_COMPLETED",
        payload={
            "source": "voice",
            "token": token,
            "transcriptLength": len(combined_text),
            "transcriptHash": transcript_hash,
            "stateVersion": getattr(session_row, "state_version", 0) or 0,
        },
    )
    extracted = _update_session_after_answer(
        session_row=session_row,
        question=session_row.current_question or "Let's continue the hiring intake.",
        question_key=session_row.current_question_key or "voice_followup",
        answer=combined_text,
        source="voice",
        metadata={"voiceNotes": voice_notes or []},
    )
    _append_event(db, session_id=session_row.id, event_type="ANSWER_NORMALIZED", payload={"source": "voice", "extracted": extracted})

    if _session_is_complete(session_row):
        _mark_session_complete(session_row)
        db.commit()
        finalization = _finalize_sourcing(db, session_row)
        return {
            "completed": True,
            "session": _session_payload(session_row),
            "finalization": finalization,
            "calibration": finalization.get("calibration") if isinstance(finalization, dict) else None,
        }

    next_question_key, next_question_text, question_confidence = _generate_adaptive_question(
        session_row.normalized_intake or _initial_intake_state(),
        session_row.raw_conversation or [],
        current_question_key=_normalize_text(session_row.current_question_key),
    )
    question_schema = _question_schema(next_question_key)
    session_row.current_question_key = next_question_key
    session_row.current_question = next_question_text
    session_row.current_stage = ORCHESTRATION_STAGE_VOICE
    session_row.current_question_type = question_schema.get("questionType", "")
    session_row.current_question_schema = question_schema
    session_row.updated_at = _now()
    session_row.state_version = int(getattr(session_row, "state_version", 0) or 0) + 1
    db.commit()
    return {
        "completed": False,
        "session": _session_payload(session_row),
        "nextQuestion": next_question_text,
        "nextQuestionKey": next_question_key,
        "questionConfidence": question_confidence,
        "questionSchema": question_schema,
    }


def get_voice_session_start(*, db: Session, token: str) -> dict[str, Any]:
    return start_voice_handoff(db=db, token=token)


def get_orchestration_session(*, db: Session, token: str) -> dict[str, Any]:
    session_row = OrchestrationSessionRepository(db).get_by_token(token)
    if not session_row:
        raise APIError("Orchestration session not found", status_code=404)
    return _session_payload(session_row)
