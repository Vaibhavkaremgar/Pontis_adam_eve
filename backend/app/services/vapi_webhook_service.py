from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.repositories import JobRepository
from app.services.redis_service import get_redis

logger = logging.getLogger(__name__)

_WEBHOOK_DEDUPE_PREFIX = "pontis:vapi:webhook:"
_WEBHOOK_DEDUPE_TTL_SECONDS = 48 * 60 * 60
_TERMINAL_EVENT_TYPES = {"end-of-call-report", "call-ended", "call.ended", "call.completed"}


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _parse_jsonish(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _to_iso(value: Any) -> str:
    raw = _normalize_text(value)
    if not raw:
        return ""
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        return datetime.fromisoformat(raw).astimezone(timezone.utc).isoformat()
    except Exception:
        return raw


def _build_voice_summary(transcript: str) -> str:
    cleaned = _normalize_text(transcript)
    if not cleaned:
        return ""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", cleaned) if part.strip()]
    summary = " ".join(sentences[:3]) if sentences else cleaned
    summary = _normalize_text(summary)
    return summary if len(summary) <= 800 else f"{summary[:797].rstrip()}..."


def _extract_call_object(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("call", "callData"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    message = payload.get("message")
    if isinstance(message, dict):
        for key in ("call", "callData"):
            value = message.get(key)
            if isinstance(value, dict):
                return value
    return {}


def _extract_artifact(call: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    for source in (call, payload, payload.get("message") if isinstance(payload.get("message"), dict) else {}):
        if isinstance(source, dict):
            artifact = source.get("artifact")
            if isinstance(artifact, dict):
                return artifact
    return {}


def _extract_metadata(payload: dict[str, Any], call: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        payload.get("metadata"),
        payload.get("callMetadata"),
        call.get("metadata"),
        call.get("assistant", {}).get("metadata") if isinstance(call.get("assistant"), dict) else {},
        payload.get("assistant", {}).get("metadata") if isinstance(payload.get("assistant"), dict) else {},
    ]
    merged: dict[str, Any] = {}
    for candidate in candidates:
        merged.update(_parse_jsonish(candidate))
    return merged


def _extract_transcript_turns(artifact: dict[str, Any]) -> list[dict[str, str]]:
    turns: list[dict[str, str]] = []
    messages = artifact.get("messages")
    if not isinstance(messages, list):
        return turns

    for message in messages:
        if not isinstance(message, dict):
            continue
        text = _normalize_text(message.get("content") or message.get("message") or message.get("transcript") or "")
        if not text:
            continue
        role = _normalize_text(message.get("role") or message.get("speaker") or "")
        if role not in {"assistant", "user"}:
            role = "assistant" if text.startswith("Assistant:") else "user"
        turns.append({"role": role, "text": text})
    return turns


def _extract_transcript(call: dict[str, Any], artifact: dict[str, Any], payload: dict[str, Any]) -> str:
    candidates = [
        artifact.get("transcript"),
        artifact.get("messages"),
        payload.get("transcript"),
        payload.get("voiceTranscript"),
        call.get("transcript"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str):
            text = _normalize_text(candidate)
            if text:
                return text

    turns = _extract_transcript_turns(artifact)
    if turns:
        return "\n".join(f"{turn['role'].title()}: {turn['text']}" for turn in turns).strip()
    return ""


def _extract_webhook_id(payload: dict[str, Any]) -> str:
    for key in ("id", "messageId", "webhookId", "eventId"):
        value = _normalize_text(payload.get(key))
        if value:
            return value
    message = payload.get("message")
    if isinstance(message, dict):
        for key in ("id", "messageId", "webhookId", "eventId"):
            value = _normalize_text(message.get(key))
            if value:
                return value
    return ""


def _dedupe_key(*, call_id: str, webhook_id: str) -> str:
    return f"{_WEBHOOK_DEDUPE_PREFIX}{call_id or webhook_id}"


def _mark_processed(*, call_id: str, webhook_id: str) -> bool:
    redis = get_redis()
    if redis is None:
        return False
    try:
        return bool(redis.set(_dedupe_key(call_id=call_id, webhook_id=webhook_id), "1", nx=True, ex=_WEBHOOK_DEDUPE_TTL_SECONDS))
    except Exception:
        return False


def _load_current_recruiter_intelligence(job_repo: JobRepository, job_id: str) -> dict[str, Any]:
    job = job_repo.get(job_id)
    if not job:
        return {}
    structured = job.structured_data if isinstance(job.structured_data, dict) else {}
    recruiter = structured.get("recruiterIntelligence")
    return recruiter if isinstance(recruiter, dict) else {}


def _apply_recruiter_intelligence(
    *,
    db: Session,
    job_id: str,
    recruiter_id: str,
    session_id: str,
    call_id: str,
    assistant_id: str,
    recording_url: str,
    transcript: str,
    transcript_turns: list[dict[str, str]],
    ended_at: str,
    event_type: str,
) -> dict[str, Any]:
    job_repo = JobRepository(db)
    job = job_repo.get(job_id)
    if not job:
        raise ValueError("Job not found")

    existing = _load_current_recruiter_intelligence(job_repo, job_id)
    existing_transcript = _normalize_text(existing.get("transcript", ""))
    incoming_transcript = _normalize_text(transcript)

    if existing_transcript and not incoming_transcript:
        return {"updated": False, "reason": "existing_transcript_present", "job_id": job_id, "call_id": call_id}

    if existing_transcript and incoming_transcript and _normalize_text(existing.get("callId", "")) == _normalize_text(call_id):
        return {"updated": False, "reason": "duplicate_call_id", "job_id": job_id, "call_id": call_id}

    summary = _build_voice_summary(incoming_transcript)
    if not summary:
        summary = "Structured job intake captured. Proceeding with recruiter calibration."

    structured = dict(job.structured_data or {})
    recruiter_intelligence = dict(structured.get("recruiterIntelligence") or {})
    recruiter_intelligence.update(
        {
            "transcript": incoming_transcript,
            "voiceTranscript": incoming_transcript,
            "voiceSummary": summary,
            "voice_summary": summary,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "callId": call_id,
            "assistantId": assistant_id,
            "recordingUrl": recording_url,
            "sessionId": session_id,
            "recruiterId": recruiter_id,
            "endedAt": ended_at,
            "eventType": event_type,
        }
    )
    if transcript_turns:
        recruiter_intelligence["speakerTurns"] = transcript_turns

    structured["recruiterIntelligence"] = recruiter_intelligence
    job_repo.update_structured_fields(job_id=job_id, structured_data=structured)
    return {"updated": True, "job_id": job_id, "call_id": call_id, "transcript_length": len(incoming_transcript)}


def process_vapi_webhook(*, db: Session, raw_body: bytes, headers: Any) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except Exception as exc:
        raise ValueError(f"invalid_vapi_payload:{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid_vapi_payload:not_object")

    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    event = payload if _normalize_text(payload.get("type", "")) else message if isinstance(message, dict) else payload
    event_type = _normalize_text(event.get("type") if isinstance(event, dict) else payload.get("type"))
    call = _extract_call_object(payload)
    artifact = _extract_artifact(call, payload)
    metadata = _extract_metadata(payload, call)
    call_id = _normalize_text(call.get("id") or payload.get("callId") or artifact.get("callId") or "")
    assistant_id = _normalize_text(
        call.get("assistantId")
        or (call.get("assistant", {}) or {}).get("id")
        or payload.get("assistantId")
        or (payload.get("assistant", {}) or {}).get("id")
    )
    recording_url = _normalize_text(
        artifact.get("recordingUrl")
        or artifact.get("stereoRecordingUrl")
        or (artifact.get("recording", {}) or {}).get("url")
    )
    transcript = _extract_transcript(call, artifact, payload)
    transcript_turns = _extract_transcript_turns(artifact)
    ended_at = _to_iso(call.get("endedAt") or payload.get("endedAt") or event.get("endedAt") if isinstance(event, dict) else "")
    job_id = _normalize_text(metadata.get("jobId") or metadata.get("job_id") or "")
    recruiter_id = _normalize_text(metadata.get("recruiterId") or metadata.get("recruiter_id") or "")
    session_id = _normalize_text(metadata.get("sessionId") or metadata.get("session_id") or "")

    logger.info(
        "vapi_webhook_received event_type=%s call_id=%s assistant_id=%s transcript_length=%s",
        event_type or "unknown",
        call_id or "unknown",
        assistant_id or "unknown",
        len(transcript),
    )
    logger.info(
        "vapi_webhook_metadata event_type=%s job_id=%s recruiter_id=%s session_id=%s",
        event_type or "unknown",
        job_id or "",
        recruiter_id or "",
        session_id or "",
    )

    if event_type not in _TERMINAL_EVENT_TYPES:
        return {"ignored": True, "reason": "non_terminal_event", "event_type": event_type, "call_id": call_id}

    if not job_id or not recruiter_id:
        return {"ignored": True, "reason": "missing_metadata", "event_type": event_type, "call_id": call_id}

    if not transcript:
        logger.warning("vapi_webhook_missing_transcript event_type=%s call_id=%s job_id=%s", event_type, call_id, job_id)

    webhook_id = _extract_webhook_id(payload)
    deduped = call_id and _mark_processed(call_id=call_id, webhook_id=webhook_id)
    if deduped:
        logger.info("vapi_webhook_deduped_by_redis event_type=%s call_id=%s job_id=%s", event_type, call_id, job_id)

    result = _apply_recruiter_intelligence(
        db=db,
        job_id=job_id,
        recruiter_id=recruiter_id,
        session_id=session_id,
        call_id=call_id,
        assistant_id=assistant_id,
        recording_url=recording_url,
        transcript=transcript,
        transcript_turns=transcript_turns,
        ended_at=ended_at,
        event_type=event_type,
    )
    logger.info(
        "vapi_webhook_db_update_success event_type=%s job_id=%s call_id=%s updated=%s transcript_length=%s",
        event_type,
        job_id,
        call_id or "unknown",
        result.get("updated", False),
        len(transcript),
    )
    return result
