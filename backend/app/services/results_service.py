from __future__ import annotations

# Interview project writes directly to Adam's DB (shared DATABASE_URL).
# It writes to: interviews.transcript, interview_score, technical_score,
# communication_score, culture_fit_score, ai_summary, feedback,
# interviewer_notes, video_url, completed_at, status="completed"
# Adam only serves the data — it never pulls or receives a push.
# Video is streamed from interview project volume via:
# GET {INTERVIEW_APP_URL}/api/video/{video_url} with X-Internal-API-Key

import asyncio
import logging
from typing import Any
from types import SimpleNamespace
from urllib.parse import quote

import requests
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import (
    INTERVIEW_INTERNAL_SERVICE_TOKEN,
    HTTP_TIMEOUT_SECONDS,
    INTERVIEW_APP_URL,
    PUBLIC_APP_URL,
)
from app.db.repositories import (
    CandidateProfileRepository,
    InterviewEvaluationRepository,
    InterviewSessionRepository,
    JobRepository,
    NotificationEventRepository,
    NotificationWorkflowTokenRepository,
    OrchestrationSessionRepository,
)
from app.models.entities import CandidateRequestEntity
from app.services.ready_profile_serializer import build_ready_card, build_ready_profile
from app.services.ats_lifecycle_service import candidate_timeline, normalize_ats_status
from app.services.interview_stage_service import get_interview_insights
from app.services.slack_integration import post_slack_message
from app.services.slack_tenant_service import SlackCompanyResolver
from app.utils.exceptions import APIError
from app.utils.observability import emit_trace

logger = logging.getLogger(__name__)

def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_lower(value: Any) -> str:
    return _normalize_text(value).lower()


def _metadata_map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _current_stage_label(stage_code: str) -> str:
    normalized = _normalize_text(stage_code).upper()
    return {
        "SHORTLISTED": "Shortlisted",
        "WAITING_FOR_CANDIDATE": "Waiting for Candidate",
        "RESUME_SUBMITTED": "Resume Submitted",
        "RESUME_SHORTLISTED": "Resume Shortlisted",
        "INTERVIEW_SCHEDULED": "Interview Scheduled",
        "INTERVIEW_COMPLETED": "Interview Completed",
        "PASSED": "Passed",
        "REJECTED": "Rejected",
    }.get(normalized, normalized.replace("_", " ").title() if normalized else "In progress")


def _result_stage(profile: Any, interview_row: dict[str, Any] | None, session_row: Any) -> tuple[str, str]:
    interview_status = _normalize_lower((interview_row or {}).get("status"))
    transcript = _normalize_text((interview_row or {}).get("transcript"))
    session_status = _normalize_lower(getattr(session_row, "status", ""))
    session_booking_status = _normalize_lower(getattr(session_row, "booking_status", ""))
    session_stage = _normalize_lower(getattr(session_row, "stage", ""))
    acquisition_status = _normalize_lower(getattr(profile, "acquisition_status", ""))
    ats_status = _normalize_lower(getattr(profile, "ats_status", "") or getattr(profile, "candidate_status", ""))
    decision = _normalize_lower(getattr(profile, "decision", ""))
    review_status = _normalize_lower(getattr(profile, "review_status", ""))
    resume_received = bool(
        getattr(profile, "resume_received_at", None)
        or _normalize_text(getattr(profile, "resume_text", ""))
        or _normalize_text(getattr(profile, "parsed_resume_text", ""))
    )

    if interview_status in {"completed", "interview_completed", "results_ready"} or transcript:
        return "INTERVIEW_COMPLETED", "Interview Completed"
    if (
        interview_status in {"scheduled", "booked"}
        or session_status in {"scheduled", "interview_scheduled"}
        or session_booking_status in {"confirmed", "booked"}
        or session_stage in {"interview_scheduled", "booked"}
    ):
        return "INTERVIEW_SCHEDULED", "Interview Scheduled"
    if ats_status in {"advanced", "final_round", "offer_stage", "offer_sent", "hired", "placed"} or decision in {"passed", "approved", "advance"}:
        return "PASSED", "Passed"
    if ats_status in {"rejected", "archived", "disqualified", "closed_with_reason", "interview_no_show"} or interview_status == "rejected":
        return "REJECTED", "Rejected"
    if acquisition_status in {"waiting_for_eve", "handoff"} or session_status == "waiting_for_candidate":
        return "WAITING_FOR_CANDIDATE", "Waiting for Candidate"
    if ats_status in {"shortlisted", "selected"} or review_status in {"shortlisted", "selected"} or decision in {"selected", "shortlisted"}:
        if resume_received:
            return "RESUME_SHORTLISTED", "Resume Shortlisted"
        return "SHORTLISTED", "Shortlisted"
    if resume_received or _normalize_lower(getattr(profile, "parsing_status", "")) in {"parsed", "complete", "completed"}:
        return "RESUME_SUBMITTED", "Resume Submitted"
    if acquisition_status:
        return acquisition_status.upper(), _progress_text(acquisition_status)
    if ats_status:
        return ats_status.upper(), _current_stage_label(ats_status)
    return "SHORTLISTED", "Shortlisted"


def _recording_snapshot(*, interview_row: dict[str, Any] | None, session_row: Any) -> dict[str, Any]:
    row = interview_row or {}
    session_metadata = _metadata_map(getattr(session_row, "scheduling_metadata", {}) if session_row else {})
    interviewer_metadata = _metadata_map(getattr(session_row, "interviewer_metadata", {}) if session_row else {})
    recording_path = _normalize_text(
        row.get("video_url")
        or session_metadata.get("recordingPath")
        or session_metadata.get("recording_path")
        or session_metadata.get("videoUrl")
        or session_metadata.get("video_url")
    )
    recording_status = _normalize_text(
        row.get("status")
        or getattr(session_row, "status", "")
        or getattr(session_row, "booking_status", "")
        or session_metadata.get("recordingStatus")
        or session_metadata.get("recording_status")
        or "pending"
    )
    duration_value = (
        row.get("duration_minutes")
        or session_metadata.get("recordingDuration")
        or session_metadata.get("recording_duration")
        or session_metadata.get("durationMinutes")
        or session_metadata.get("duration_minutes")
    )
    try:
        recording_duration = int(duration_value) if duration_value is not None and str(duration_value).strip() != "" else None
    except (TypeError, ValueError):
        recording_duration = None
    recording_metadata = {
        "interviewId": _normalize_text(row.get("interview_id") or ""),
        "interviewStatus": _normalize_text(row.get("status") or ""),
        "interviewCompletedAt": row.get("completed_at").isoformat() if row.get("completed_at") else None,
        "sessionId": _normalize_text(getattr(session_row, "id", "") or ""),
        "sessionStatus": _normalize_text(getattr(session_row, "status", "") or ""),
        "sessionStage": _normalize_text(getattr(session_row, "stage", "") or ""),
        "bookingStatus": _normalize_text(getattr(session_row, "booking_status", "") or ""),
        "bookingUrl": _normalize_text(getattr(session_row, "booking_url", "") or ""),
        "scheduledAt": getattr(session_row, "scheduled_at", None).isoformat() if getattr(session_row, "scheduled_at", None) else None,
        "timeZone": _normalize_text(getattr(session_row, "timezone", "") or ""),
        "availableSlots": list(getattr(session_row, "available_slots", []) or []),
        "interviewerMetadata": interviewer_metadata,
        "schedulingMetadata": session_metadata,
    }
    return {
        "recordingPath": recording_path,
        "recordingStatus": recording_status,
        "recordingDuration": recording_duration,
        "recordingMetadata": recording_metadata,
        "videoAvailable": bool(recording_path),
    }


def _result_agency_matches(*, db: Session, job_id: str, candidate_id: str, agency_id: str) -> bool:
    normalized_agency_id = _normalize_text(agency_id)
    if not normalized_agency_id:
        return False
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if profile and _normalize_text(getattr(profile, "agency_id", "")) == normalized_agency_id:
        return True
    interview_row = _fetch_interview_result_row(db, job_id=job_id, candidate_id=candidate_id)
    if _normalize_text(interview_row.get("agency_id") or interview_row.get("interview_agency_id") or "") == normalized_agency_id:
        return True
    job = JobRepository(db).get(job_id)
    return bool(job and _normalize_text(getattr(job, "company_id", "")) == normalized_agency_id)


def _format_score(value: Any) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "0.0"


def _summary_paragraphs(summary: str) -> list[str]:
    text = _normalize_text(summary)
    if not text:
        return [""]
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    if len(paragraphs) >= 3:
        return paragraphs[:3]
    if len(paragraphs) == 1:
        sentences = [part.strip() for part in text.replace("\n", " ").split(". ") if part.strip()]
        if len(sentences) >= 3:
            chunk_size = max(1, len(sentences) // 3)
            paragraphs = [
                ". ".join(sentences[0:chunk_size]),
                ". ".join(sentences[chunk_size : chunk_size * 2]),
                ". ".join(sentences[chunk_size * 2 :]),
            ]
            cleaned = []
            for part in paragraphs:
                normalized = part.strip()
                if normalized and not normalized.endswith("."):
                    normalized += "."
                if normalized:
                    cleaned.append(normalized)
            return cleaned[:3]
        words = text.split()
        if len(words) > 1:
            chunk_size = max(1, len(words) // 3)
            paragraphs = [
                " ".join(words[0:chunk_size]),
                " ".join(words[chunk_size : chunk_size * 2]),
                " ".join(words[chunk_size * 2 :]),
            ]
            return [part.strip() for part in paragraphs if part.strip()]
        return [text]
    return paragraphs[:3]


def _candidate_resume_url(profile: Any) -> str:
    parsed_resume_json = getattr(profile, "parsed_resume_json", {})
    raw_data = getattr(profile, "raw_data", {})
    for source in (parsed_resume_json, raw_data):
        if not isinstance(source, dict):
            continue
        for key in ("resume_url", "resumeUrl", "resume_link", "resumeLink", "source_path", "sourcePath"):
            value = _normalize_text(source.get(key) or "")
            if value and (value.startswith("http://") or value.startswith("https://")):
                return value
    return ""


def _acquisition_progress(status: str) -> str:
    normalized = _normalize_text(status).upper()
    return {
        "DISCOVERED": "Candidate sourced",
        "QUEUED": "Queued for connection",
        "CONNECTION_SENT": "Connection request sent",
        "PENDING_ACCEPTANCE": "Waiting for acceptance",
        "ACCEPTED": "Accepted",
        "MESSAGE_QUEUED": "Message queued",
        "MESSAGE_SENT": "Message sent",
        "WAITING_FOR_EVE": "Waiting for candidate",
        "HANDOFF": "Handed off to Eve",
        "FAILED": "Failed",
        "BLOCKED": "Blocked",
        "RETRYING": "Retrying",
    }.get(normalized, "In progress")


def _source_category(profile: Any) -> str:
    raw_data = getattr(profile, "raw_data", {})
    if not isinstance(raw_data, dict):
        raw_data = {}
    source_type = _normalize_text(raw_data.get("source_type") or raw_data.get("sourceType"))
    source_provider = _normalize_text(raw_data.get("source_provider") or raw_data.get("sourceProvider") or raw_data.get("source"))
    source_hint = f"{source_type} {source_provider}".lower()
    if any(token in source_hint for token in ("internal", "manual", "referral", "ats")):
        return "internal"
    return "serp"


def _engagement_snapshot(*, profile: Any, job: Any) -> dict[str, Any]:
    acquisition_status = _normalize_text(getattr(profile, "acquisition_status", "") or "").upper()
    raw_data = getattr(profile, "raw_data", {})
    if not isinstance(raw_data, dict):
        raw_data = {}
    engagement = dict(raw_data.get("engagement") or {})
    return {
        "currentStage": acquisition_status or _normalize_text(getattr(profile, "ats_status", "") or getattr(profile, "candidate_status", "")) or "DISCOVERED",
        "connectionStatus": _normalize_text(getattr(profile, "acquisition_status", "") or "").upper() or "UNKNOWN",
        "invitationStatus": acquisition_status if acquisition_status in {"MESSAGE_QUEUED", "MESSAGE_SENT", "WAITING_FOR_EVE", "HANDOFF"} else ("PENDING" if acquisition_status in {"PENDING_ACCEPTANCE", "QUEUED", "CONNECTION_SENT"} else "UNKNOWN"),
        "currentProgress": _progress_text(acquisition_status),
        "sourceCategory": _source_category(profile),
        "reason": _normalize_text(getattr(profile, "acquisition_status_reason", "")),
        "retryCount": int(getattr(profile, "acquisition_retry_count", 0) or 0),
        "priority": int(getattr(profile, "acquisition_priority", 0) or 0),
        "updatedAt": getattr(profile, "acquisition_updated_at", None).isoformat() if getattr(profile, "acquisition_updated_at", None) else None,
        "timeline": engagement,
    }


def _progress_text(status: str) -> str:
    normalized = _normalize_text(status).upper()
    return {
        "DISCOVERED": "Candidate sourced",
        "QUEUED": "Queued for connection",
        "CONNECTION_SENT": "Connection request sent",
        "PENDING_ACCEPTANCE": "Waiting for acceptance",
        "ACCEPTED": "Connection accepted",
        "MESSAGE_QUEUED": "Message queued",
        "MESSAGE_SENT": "Message sent",
        "WAITING_FOR_EVE": "Waiting for candidate",
        "HANDOFF": "Handed off to Eve",
        "FAILED": "Failed",
        "BLOCKED": "Blocked",
        "RETRYING": "Retrying",
    }.get(normalized, "In progress")


async def _post_full_profile_card_to_slack(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    workflow_token: str,
    result: dict[str, Any],
) -> None:
    orchestration_session = OrchestrationSessionRepository(db).get_by_job(job_id)
    slack_context = dict(getattr(orchestration_session, "slack_context", {}) or {}) if orchestration_session else {}
    if not slack_context:
        return

    channel_id = _normalize_text(slack_context.get("channelId") or slack_context.get("channel_id") or "")
    company_id = _normalize_text(slack_context.get("companyId") or slack_context.get("company_id") or getattr(orchestration_session, "company_id", "") or "")
    if not channel_id or not company_id:
        return

    notification_key = f"results-profile-card:{job_id}:{candidate_id}"
    notification_repo = NotificationEventRepository(db)
    existing_notification = notification_repo.get_by_key(notification_key)
    if existing_notification and _normalize_text(getattr(existing_notification, "status", "")).lower() == "delivered":
        return

    job = JobRepository(db).get(job_id)
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if not job or not profile:
        return

    try:
        bot_token = SlackCompanyResolver(db).resolve_bot_token(company_id=company_id)
    except Exception as exc:
        logger.warning("results_profile_card_slack_token_failed job_id=%s error=%s", job_id, str(exc), exc_info=exc)
        return
    if not bot_token:
        return

    candidate_name = _normalize_text(result.get("candidate", {}).get("name") or getattr(profile, "name", "") or candidate_id)
    current_role = _normalize_text(result.get("candidate", {}).get("headline") or result.get("candidate", {}).get("role") or getattr(profile, "current_title", "") or getattr(profile, "role", ""))
    job_title = _normalize_text(getattr(job, "title", ""))
    summary_text = _normalize_text(result.get("summary") or getattr(profile, "summary", ""))
    summary_body = "\n\n".join(part for part in _summary_paragraphs(summary_text) if part) or "No ai_summary available."
    scores = result.get("scores") or {}
    video_link = f"{PUBLIC_APP_URL.rstrip('/')}/results/{_normalize_text(workflow_token)}" if _normalize_text(workflow_token) else ""
    resume_url = _candidate_resume_url(profile)

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{candidate_name} - {current_role or job_title or 'Interview profile'}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{candidate_name}*\n{current_role or job_title or 'Current role not set'}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*AI Summary*\n{summary_body}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Interview Score*\n{_format_score(scores.get('overall'))}"},
                {"type": "mrkdwn", "text": f"*Technical Score*\n{_format_score(scores.get('technical'))}"},
                {"type": "mrkdwn", "text": f"*Communication Score*\n{_format_score(scores.get('communication'))}"},
                {"type": "mrkdwn", "text": f"*Culture Fit Score*\n{_format_score(scores.get('cultureFit'))}"},
            ],
        },
    ]
    link_lines = []
    if video_link:
        link_lines.append(f"*Video Link*\n<{video_link}|Open results>")
    if resume_url:
        link_lines.append(f"*Resume Link*\n<{resume_url}|Open resume>")
    if link_lines:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n\n".join(link_lines)}})
    blocks.append(
        {
            "type": "actions",
            "block_id": f"result-profile:{job_id}:{candidate_id}",
            "elements": [
                {
                    "type": "button",
                    "action_id": "like",
                    "text": {"type": "plain_text", "text": "LIKE -> 2nd round"},
                    "style": "primary",
                    "value": f"like:{candidate_id}:{job_id}",
                },
                {
                    "type": "button",
                    "action_id": "pass",
                    "text": {"type": "plain_text", "text": "PASS -> Disqualify"},
                    "style": "danger",
                    "value": f"pass:{candidate_id}:{job_id}",
                },
            ],
        }
    )

    try:
        await post_slack_message(
            channel_id=channel_id,
            text=f"{candidate_name} - {current_role or job_title or 'Interview profile'}",
            blocks=blocks,
            bot_token=bot_token,
        )
    except Exception as exc:
        logger.warning("results_profile_card_slack_post_failed job_id=%s candidate_id=%s error=%s", job_id, candidate_id, str(exc), exc_info=exc)
        return

    notification_repo.upsert(
        notification_key=notification_key,
        job_id=job_id,
        company_id=company_id,
        candidate_id=candidate_id,
        recipient_type="recruiter",
        recipient=channel_id,
        channel="slack",
        title="Interview profile posted",
        body=f"Profile card posted for {candidate_name}",
        status="delivered",
        notification_type="results_profile_card",
        notification_metadata={"workflowToken": workflow_token, "channelId": channel_id},
        delivery_reference=notification_key,
    )
    db.commit()
    logger.info("results_profile_card_slack_posted job_id=%s candidate_id=%s", job_id, candidate_id)


def _fetch_interview_result_row(db: Session, *, job_id: str, candidate_id: str) -> dict[str, Any]:
    if not job_id or not candidate_id:
        return {}
    try:
        result = db.execute(
            text("""
                SELECT
                    i.id                         AS interview_id,
                    i.job_id,
                    i.agency_id,
                    i.candidate_id,
                    i.status,
                    i.interview_score,
                    i.duration_minutes,
                    i.technical_score,
                    i.communication_score,
                    i.culture_fit_score,
                    i.interview_score_reason,
                    i.technical_score_reason,
                    i.communication_score_reason,
                    i.culture_fit_score_reason,
                    i.ai_summary,
                    i.transcript,
                    i.feedback,
                    i.interviewer_notes,
                    i.video_url,
                    NULL AS completed_at,
                    i.created_at,
                    s.id                     AS session_id,
                    s.session_token          AS session_token,
                    s.status                 AS session_status,
                    s.booking_status         AS session_booking_status,
                    s.stage                  AS session_stage,
                    s.booking_url            AS session_booking_url,
                    s.scheduled_at           AS session_scheduled_at,
                    s.timezone               AS session_timezone,
                    s.available_slots        AS session_available_slots,
                    s.interviewer_metadata    AS session_interviewer_metadata,
                    s.scheduling_metadata     AS session_scheduling_metadata,
                    cp.name                    AS candidate_name,
                    cp.current_role            AS candidate_role,
                    cp.current_company         AS candidate_company,
                    cp.summary                 AS candidate_summary,
                    cp.skills                  AS candidate_skills,
                    cp.raw_data                AS candidate_raw_data,
                    cp.agency_id               AS candidate_agency_id,
                    nt.token                   AS workflow_token
                FROM interviews i
                LEFT JOIN candidates cp
                    ON cp.job_id = i.job_id
                   AND cp.candidate_id = i.candidate_id
                LEFT JOIN interview_sessions s
                    ON s.job_id = i.job_id
                   AND s.candidate_id = i.candidate_id
                LEFT JOIN notification_workflow_tokens nt
                    ON nt.job_id = i.job_id
                   AND nt.candidate_id = i.candidate_id
                   AND nt.is_active = 1
                WHERE i.job_id = :job_id
                  AND i.candidate_id = :candidate_id
                ORDER BY i.created_at DESC
                LIMIT 1
            """),
            {"job_id": job_id, "candidate_id": candidate_id},
        ).mappings().first()
        if result:
            return dict(result)
    except Exception as e:
        logger.error("results_read_failed error=%s", str(e))
        raise
    return {}


def _workflow_token_payload(db: Session, workflow_token: str) -> tuple[dict[str, Any] | None, str, str, str]:
    token_row = NotificationWorkflowTokenRepository(db).get_by_token(workflow_token, source_app="ui")
    if not token_row:
        return None, "", "", ""
    job_id = str(token_row.job_id or "").strip()
    candidate_id = str(token_row.candidate_id or "").strip()
    payload = dict(token_row.payload or {})
    return payload, job_id, candidate_id, str(token_row.token or workflow_token).strip()


def resolve_result_context(*, db: Session, workflow_token: str) -> dict[str, str]:
    payload, job_id, candidate_id, resolved_workflow_token = _workflow_token_payload(db, workflow_token)
    if not job_id or not candidate_id:
        raise APIError("Result not found", status_code=404)
    return {
        "jobId": job_id,
        "candidateId": candidate_id,
        "workflowToken": resolved_workflow_token,
        "sourceApp": str((payload or {}).get("sourceApp") or (payload or {}).get("source_type") or (payload or {}).get("sourceType") or "ui"),
    }


def _build_candidate_snapshot(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    workflow_token: str,
    recruiter_id: str,
) -> dict[str, Any]:
    job = JobRepository(db).get(job_id)
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    session = InterviewSessionRepository(db).get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
    try:
        insights = get_interview_insights(db=db, job_id=job_id, candidate_id=candidate_id)
    except APIError:
        insights = {}
    evaluations = InterviewEvaluationRepository(db).list_for_candidate(job_id=job_id, candidate_id=candidate_id, limit=20)
    timeline = candidate_timeline(db=db, job_id=job_id, candidate_id=candidate_id, limit=100)

    # ── Primary source: interviews row keyed by job_id + candidate_id ─────────
    interview_row = _fetch_interview_result_row(db, job_id=job_id, candidate_id=candidate_id)
    transcript = _normalize_text(interview_row.get("transcript") or "")

    # ── AI Summary ────────────────────────────────────────────────────────────
    summary = _normalize_text(
        interview_row.get("ai_summary")
        or (evaluations[0].summary if evaluations else "")
        or insights.get("intelligence", {}).get("summary") or ""
    )

    # ── Scores ────────────────────────────────────────────────────────────────
    def _f(val: Any, fallback: float = 0.0) -> float:
        try:
            return float(val) if val is not None else fallback
        except (TypeError, ValueError):
            return fallback

    overall_score = _f(interview_row.get("interview_score"), _f(getattr(profile, "fit_score", 0.0)))
    technical_score = _f(interview_row.get("technical_score"), overall_score)
    communication_score = _f(interview_row.get("communication_score"), 0.0)
    culture_fit_score = _f(interview_row.get("culture_fit_score"), 0.0)
    score_reasons = {
        "overall": _normalize_text(interview_row.get("interview_score_reason") or interview_row.get("feedback") or getattr(profile, "ats_status_reason", "") or getattr(profile, "decision", "")),
        "technical": _normalize_text(interview_row.get("technical_score_reason") or getattr(profile, "ats_status_reason", "")),
        "communication": _normalize_text(interview_row.get("communication_score_reason") or interview_row.get("interviewer_notes") or ""),
        "cultureFit": _normalize_text(interview_row.get("culture_fit_score_reason") or getattr(profile, "decision", "")),
    }
    stage_code, stage_label = _result_stage(profile, interview_row, session)
    current_status = _normalize_text(
        interview_row.get("status")
        or getattr(profile, "ats_status", "")
        or getattr(profile, "candidate_status", "")
        or getattr(session, "status", "")
        or stage_code.lower()
    )
    evaluation_ready = bool(
        interview_row.get("interview_score")
        or transcript
        or (session and _normalize_lower(getattr(session, "evaluation_status", "")) == "completed")
    )
    recommendation = _normalize_text(
        interview_row.get("feedback")
        or (evaluations[0].recommendation if evaluations else "")
        or getattr(profile, "decision", "")
    ).lower()
    raw_data = getattr(profile, "raw_data", {}) if isinstance(getattr(profile, "raw_data", {}), dict) else {}
    recording = _recording_snapshot(interview_row=interview_row, session_row=session)

    # ── Video ─────────────────────────────────────────────────────────────────
    recording_path = _normalize_text(interview_row.get("video_url") or "")
    video_available = bool(recording_path)

    # ── Status ────────────────────────────────────────────────────────────────
    raw_data = getattr(profile, "raw_data", {}) if isinstance(getattr(profile, "raw_data", {}), dict) else {}
    current_status = normalize_ats_status(getattr(profile, "ats_status", "") or getattr(profile, "candidate_status", ""))
    ats_metadata = getattr(profile, "ats_metadata", {}) if isinstance(getattr(profile, "ats_metadata", {}), dict) else {}
    interview_status = _normalize_text(interview_row.get("status") or "")
    status = interview_status or current_status or getattr(session, "status", "") or "interview_completed"
    evaluation_ready = bool(interview_row.get("interview_score") or (session and (session.evaluation_status or "").strip().lower() == "completed"))
    recommendation = _normalize_text(
        interview_row.get("feedback")
        or (evaluations[0].recommendation if evaluations else "")
        or getattr(profile, "decision", "")
    ).lower()

    response = {
        "job": {
            "id": job_id,
            "title": _normalize_text(getattr(job, "title", "") or getattr(profile, "current_role", "") or "Untitled"),
            "location": _normalize_text(getattr(job, "location", "") or getattr(profile, "location", "") or ""),
            "companyName": _normalize_text(getattr(job, "company_name", "") or ""),
            "sourceApp": _normalize_text(getattr(job, "source_app", "") or "ui"),
        },
        "candidate": {
            "id": candidate_id,
            "name": _normalize_text(getattr(profile, "name", "") or candidate_id),
            "role": _normalize_text(getattr(profile, "current_role", "") or getattr(profile, "role", "")),
            "company": _normalize_text(getattr(profile, "current_company", "") or getattr(profile, "company", "")),
            "headline": _normalize_text(getattr(profile, "current_title", "") or getattr(profile, "current_role", "")),
            "location": _normalize_text(getattr(profile, "location", "") or raw_data.get("location") or ""),
            "email": _normalize_text(getattr(profile, "email", "") or raw_data.get("email") or ""),
            "summary": _normalize_text(getattr(profile, "summary", "")),
            "skills": list(getattr(profile, "skills", []) or []),
            "source": "shared_db",
        },
        "recording": {
            "sessionToken": _normalize_text(interview_row.get("session_token") or ""),
            **recording,
        },
        "transcript": transcript,
        "summary": summary,
        "scores": {
            "overall": overall_score,
            "technical": technical_score,
            "communication": communication_score,
            "cultureFit": culture_fit_score,
        },
        "scoreReasons": score_reasons,
        "decision": recommendation,
        "status": current_status,
        "stage": {
            "code": stage_code,
            "label": stage_label,
        },
        "interview": {
            "status": _normalize_text(interview_row.get("status") or getattr(session, "status", "") or stage_code.lower()),
            "statusLabel": stage_label,
            "completedAt": interview_row.get("completed_at").isoformat() if interview_row.get("completed_at") else None,
            "createdAt": interview_row.get("created_at").isoformat() if interview_row.get("created_at") else None,
            "durationMinutes": interview_row.get("duration_minutes"),
        },
        "timeline": {"events": timeline},
        "recommendation": recommendation,
        "engagement": {
            **_engagement_snapshot(profile=profile, job=job),
            "currentStage": stage_code,
            "currentStageLabel": stage_label,
        },
        "analysis": {
            "strengths": [],
            "weaknesses": [],
            "riskAreas": [],
            "communication": score_reasons["communication"],
            "technicalDepth": score_reasons["technical"],
            "scoreReasons": score_reasons,
        },
        "metadata": {
            "workflowToken": workflow_token,
            "jobId": job_id,
            "candidateId": candidate_id,
            "recruiterId": recruiter_id,
            "agencyId": _normalize_text(getattr(profile, "agency_id", "") or interview_row.get("candidate_agency_id") or getattr(job, "company_id", "") or ""),
            "evaluationReady": evaluation_ready,
            "sessionToken": _normalize_text(interview_row.get("session_token") or ""),
            "scheduledAt": (
                interview_row["completed_at"].isoformat()
                if interview_row.get("completed_at") else
                (session.scheduled_at.isoformat() if session and session.scheduled_at else None)
            ),
            "recording": recording["recordingMetadata"],
            "insights": insights,
            "evaluations": [
                {
                    "id": row.id,
                    "stageName": row.stage_name,
                    "summary": row.summary,
                    "recommendation": row.recommendation,
                    "competencyScores": row.competency_scores,
                    "updatedAt": row.updated_at.isoformat(),
                }
                for row in evaluations
            ],
        },
        "operations": {
            "decisionState": _normalize_text(ats_metadata.get("recruiterDecision")).lower() or "pending",
            "availableActions": ["pass", "advance", "hold", "reject"],
            "followUpPrompt": {
                "show": stage_code == "INTERVIEW_COMPLETED" and not _normalize_text(ats_metadata.get("recruiterDecision")),
                "message": "Would you like to advance this candidate?",
            },
        },
    }

    return response


def _candidate_result_rows(db: Session, job_id: str, *, agency_id: str) -> list[dict[str, Any]]:
    try:
        results = db.execute(
            text("""
                SELECT
                    cp.job_id,
                    cp.candidate_id,
                    cp.name,
                    cp.current_role,
                    cp.current_company,
                    cp.location,
                    cp.summary,
                    cp.skills,
                    cp.raw_data,
                    cp.fit_score,
                    cp.decision,
                    cp.ats_status,
                    cp.candidate_status,
                    cp.ats_status_reason,
                    cp.review_status,
                    cp.resume_received_at,
                    cp.parsing_status,
                    cp.acquisition_status,
                    cp.acquisition_status_reason,
                    cp.acquisition_retry_count,
                    cp.acquisition_priority,
                    cp.acquisition_updated_at,
                    cp.agency_id AS candidate_agency_id,
                    i.id AS interview_id,
                    i.agency_id AS interview_agency_id,
                    i.status,
                    i.interview_score,
                    i.technical_score,
                    i.communication_score,
                    i.culture_fit_score,
                    i.interview_score_reason,
                    i.technical_score_reason,
                    i.communication_score_reason,
                    i.culture_fit_score_reason,
                    i.transcript,
                    i.video_url,
                    i.feedback,
                    NULL AS completed_at,
                    i.created_at AS interview_created_at,
                    i.duration_minutes,
                    s.id AS session_id,
                    s.session_token AS session_token,
                    s.status AS session_status,
                    s.booking_status,
                    s.stage AS session_stage,
                    s.booking_url,
                    s.scheduled_at,
                    s.timezone,
                    s.available_slots,
                    s.interviewer_metadata,
                    s.scheduling_metadata,
                    nt.token AS workflow_token
                FROM candidates cp
                LEFT JOIN interviews i
                    ON i.job_id = cp.job_id
                   AND i.candidate_id = cp.candidate_id
                LEFT JOIN interview_sessions s
                    ON s.job_id = cp.job_id
                   AND s.candidate_id = cp.candidate_id
                LEFT JOIN notification_workflow_tokens nt
                    ON nt.job_id = cp.job_id
                   AND nt.candidate_id = cp.candidate_id
                   AND nt.is_active = 1
                WHERE cp.job_id = :job_id
                  AND (cp.agency_id = :agency_id OR i.agency_id = :agency_id)
                ORDER BY COALESCE(i.interview_score, 0) DESC, COALESCE(cp.fit_score, 0) DESC, cp.name ASC
            """),
            {"job_id": job_id, "agency_id": agency_id},
        ).mappings().all()
    except Exception as exc:
        logger.warning("results_list_fetch_failed job_id=%s error=%s", job_id, str(exc))
        return []

    rows: list[dict[str, Any]] = []
    for row in results:
        row_data = dict(row)
        profile = SimpleNamespace(**row_data)
        session_row = SimpleNamespace(
            id=row_data.get("session_id"),
            status=row_data.get("session_status"),
            booking_status=row_data.get("booking_status"),
            stage=row_data.get("session_stage"),
            booking_url=row_data.get("booking_url"),
            scheduled_at=row_data.get("scheduled_at"),
            timezone=row_data.get("timezone"),
            available_slots=row_data.get("available_slots") or [],
            interviewer_metadata=row_data.get("interviewer_metadata") or {},
            scheduling_metadata=row_data.get("scheduling_metadata") or {},
        )
        interview_row = {
            "interview_id": row_data.get("interview_id"),
            "session_token": row_data.get("session_token"),
            "agency_id": row_data.get("interview_agency_id"),
            "status": row_data.get("status"),
            "interview_score": row_data.get("interview_score"),
            "technical_score": row_data.get("technical_score"),
            "communication_score": row_data.get("communication_score"),
            "culture_fit_score": row_data.get("culture_fit_score"),
            "transcript": row_data.get("transcript"),
            "video_url": row_data.get("video_url"),
            "feedback": row_data.get("feedback"),
            "completed_at": row_data.get("completed_at"),
            "created_at": row_data.get("interview_created_at"),
            "duration_minutes": row_data.get("duration_minutes"),
        }
        score = float(row_data.get("interview_score") or row_data.get("fit_score") or 0.0)
        acquisition_status = _normalize_text(row_data.get("acquisition_status") or "").upper()
        raw_data = row_data.get("raw_data") if isinstance(row_data.get("raw_data"), dict) else {}
        source_category = "internal" if any(
            token in _normalize_text((raw_data or {}).get("source_type") or (raw_data or {}).get("source_provider") or (raw_data or {}).get("source")).lower()
            for token in ("internal", "manual", "referral", "ats")
        ) else "serp"
        current_progress = _progress_text(acquisition_status)
        connection_status = acquisition_status or "UNKNOWN"
        if acquisition_status in {"DISCOVERED", "QUEUED", "CONNECTION_SENT", "PENDING_ACCEPTANCE"}:
            invitation_status = "PENDING"
        elif acquisition_status in {"MESSAGE_QUEUED", "MESSAGE_SENT", "WAITING_FOR_EVE", "HANDOFF"}:
            invitation_status = "SENT"
        elif acquisition_status in {"ACCEPTED"}:
            invitation_status = "ACCEPTED"
        elif acquisition_status in {"DECLINED"}:
            invitation_status = "DECLINED"
        elif acquisition_status in {"BLOCKED"}:
            invitation_status = "BLOCKED"
        else:
            invitation_status = "UNKNOWN"
        stage_code, stage_label = _result_stage(profile, interview_row, session_row)
        transcript = _normalize_text(interview_row.get("transcript") or "")
        completed_status = _normalize_lower(interview_row.get("status")) in {"completed", "interview_completed", "results_ready"}
        if not completed_status and not interview_row.get("completed_at"):
            continue
        rows.append(
            {
                "candidateId": _normalize_text(row_data.get("candidate_id") or ""),
                "name": _normalize_text(row_data.get("name") or row_data.get("candidate_id") or ""),
                "status": "pending" if not transcript and stage_code in {"SHORTLISTED", "WAITING_FOR_CANDIDATE", "RESUME_SUBMITTED", "RESUME_SHORTLISTED"} else (_normalize_text(interview_row.get("status") or "") or stage_label.lower()),
                "workflowToken": _normalize_text(row_data.get("workflow_token") or ""),
                "score": score,
                "recommendation": _normalize_text(row_data.get("feedback") or row_data.get("decision") or "review"),
                "completionState": "pending" if not transcript and not _normalize_text(interview_row.get("status") or "") else ("results_ready" if _normalize_text(interview_row.get("status") or "") == "completed" else _normalize_text(interview_row.get("status") or "") or "results_ready"),
                "videoAvailable": bool(transcript and _normalize_text(interview_row.get("video_url") or "")),
                "currentStage": stage_code,
                "currentStageLabel": stage_label,
                "connectionStatus": connection_status,
                "invitationStatus": invitation_status,
                "currentProgress": current_progress,
                "sourceCategory": source_category,
            }
        )
    return rows


def list_ready_candidates(*, db: Session, job_id: str, agency_id: str) -> dict[str, Any]:
    """Return the deduplicated, pre-interview lifecycle for one authorized job."""
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    if _normalize_text(getattr(job, "company_id", "") or getattr(job, "agency_id", "")) != _normalize_text(agency_id):
        raise APIError("Forbidden", status_code=403)

    requests = db.scalars(
        select(CandidateRequestEntity).where(
            CandidateRequestEntity.job_id == job_id,
            CandidateRequestEntity.agency_id == agency_id,
            CandidateRequestEntity.status.in_(["PENDING", "ACCEPTED"]),
        ).order_by(CandidateRequestEntity.created_at.asc())
    ).all()
    ready = {"toBeAccepted": [], "accepted": [], "toBeInterviewed": []}
    latest_requests: dict[str, CandidateRequestEntity] = {}

    for request_row in requests:
        candidate_id = _normalize_text(request_row.candidate_id)
        if not candidate_id:
            continue
        existing = latest_requests.get(candidate_id)
        if not existing:
            latest_requests[candidate_id] = request_row
            continue
        existing_updated_at = getattr(existing, "updated_at", None) or getattr(existing, "created_at", None)
        current_updated_at = getattr(request_row, "updated_at", None) or getattr(request_row, "created_at", None)
        if current_updated_at and existing_updated_at and current_updated_at >= existing_updated_at:
            latest_requests[candidate_id] = request_row
        elif current_updated_at and not existing_updated_at:
            latest_requests[candidate_id] = request_row

    for candidate_id, request_row in latest_requests.items():
        session = InterviewSessionRepository(db).get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
        if session and _normalize_text(getattr(session, "agency_id", "")) not in {"", _normalize_text(agency_id)}:
            continue
        interview_row = _fetch_interview_result_row(db, job_id=job_id, candidate_id=candidate_id)
        interview_status = _normalize_lower(interview_row.get("status"))
        if interview_status in {"completed", "interview_completed", "results_ready"} or interview_row.get("completed_at"):
            continue

        candidate_row = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
        if not candidate_row:
            continue

        lifecycle_state = (
            "TO_BE_ACCEPTED" if _normalize_lower(request_row.status) == "pending"
            else ("TO_BE_INTERVIEWED" if session else "ACCEPTED")
        )

        card = build_ready_card(candidate_row, request_row)
        card["job_id"] = job_id
        card["lifecycle_state"] = lifecycle_state
        card["interview_status"] = _normalize_text(interview_row.get("status") or getattr(session, "status", ""))
        card["booking_status"] = _normalize_text(getattr(session, "booking_status", "") if session else "")
        card["stage"] = _normalize_text(getattr(session, "stage", "") if session else "")
        card["scheduled_at"] = getattr(session, "scheduled_at", None).isoformat() if session and getattr(session, "scheduled_at", None) else None
        card["session_token"] = _normalize_text(getattr(session, "session_token", "") if session else "")
        card["profile"] = build_ready_profile(candidate_row, request_row)

        if _normalize_lower(request_row.status) == "pending":
            ready["toBeAccepted"].append(card)
        elif session:
            ready["toBeInterviewed"].append(card)
        else:
            ready["accepted"].append(card)

    return {
        "jobId": job_id,
        "ready": ready,
        "counts": {key: len(value) for key, value in ready.items()},
    }


def list_results(*, db: Session, job_id: str, recruiter_id: str, agency_id: str) -> dict[str, Any]:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    if str(getattr(job, "company_id", "") or "").strip() != str(agency_id or "").strip():
        raise APIError("Forbidden", status_code=403)

    emit_trace(logger, "results_fetch", workflow_token="", candidate_id="", recruiter_id=recruiter_id, job_id=job_id)
    candidates = _candidate_result_rows(db, job_id, agency_id=agency_id)
    return {
        "jobId": job_id,
        "agencyId": agency_id,
        "companyId": agency_id,
        "recruiterId": recruiter_id,
        "candidates": candidates,
        "counts": {
            "completed": len([item for item in candidates if item["completionState"] == "results_ready"]),
            "available": len(candidates),
            "internalCandidates": len([item for item in candidates if item.get("sourceCategory") == "internal"]),
            "serpCandidates": len([item for item in candidates if item.get("sourceCategory") == "serp"]),
            "connectionsSent": len([item for item in candidates if item.get("currentStage") in {"CONNECTION_SENT", "PENDING_ACCEPTANCE", "ACCEPTED", "MESSAGE_QUEUED", "MESSAGE_SENT", "WAITING_FOR_EVE", "HANDOFF"}]),
            "connectionsAccepted": len([item for item in candidates if item.get("currentStage") in {"ACCEPTED", "MESSAGE_QUEUED", "MESSAGE_SENT", "WAITING_FOR_EVE", "HANDOFF"}]),
            "invitationsSent": len([item for item in candidates if item.get("invitationStatus") in {"SENT", "ACCEPTED"}]),
            "waitingForCandidate": len([item for item in candidates if item.get("currentStage") in {"WAITING_FOR_CANDIDATE", "WAITING_FOR_EVE", "HANDOFF"}]),
        },
    }


def get_result_by_workflow_token(*, db: Session, workflow_token: str, recruiter_id: str, agency_id: str) -> dict[str, Any]:
    payload, job_id, candidate_id, resolved_workflow_token = _workflow_token_payload(db, workflow_token)
    if not job_id or not candidate_id:
        raise APIError("Result not found", status_code=404)

    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    if not _result_agency_matches(db=db, job_id=job_id, candidate_id=candidate_id, agency_id=agency_id):
        raise APIError("Forbidden", status_code=403)

    emit_trace(
        logger,
        "results_fetch",
        workflow_token=resolved_workflow_token,
        candidate_id=candidate_id,
        recruiter_id=recruiter_id,
        job_id=job_id,
    )
    result = _build_candidate_snapshot(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        workflow_token=resolved_workflow_token,
        recruiter_id=recruiter_id,
    )
    try:
        asyncio.run(
            _post_full_profile_card_to_slack(
                db=db,
                job_id=job_id,
                candidate_id=candidate_id,
                workflow_token=resolved_workflow_token,
                result=result,
            )
        )
    except RuntimeError as exc:
        logger.error("slack_post_failed error=%s", str(exc))
    except Exception as exc:
        logger.error("slack_post_failed error=%s", str(exc))
    return result


def _proxy_recording(*, recording_path: str, range_header: str = ""):
    """Stream a recording from Interview Project without buffering it in Adam."""
    recording_path = _normalize_text(recording_path).lstrip("/")
    if not recording_path:
        raise APIError("Video is not available yet", status_code=404)

    interview_app_url = (INTERVIEW_APP_URL or "").rstrip("/")
    if not interview_app_url:
        raise APIError("Video is not available yet", status_code=404)

    video_url = f"{interview_app_url}/api/video/{quote(recording_path, safe='/')}"
    headers = {
        "Accept": "video/*,application/octet-stream;q=0.9,*/*;q=0.8",
        "X-Internal-API-Key": INTERVIEW_INTERNAL_SERVICE_TOKEN,
    }
    if range_header.strip():
        headers["Range"] = range_header.strip()
    try:
        upstream = requests.get(video_url, headers=headers, timeout=HTTP_TIMEOUT_SECONDS, stream=True)
    except requests.RequestException:
        raise APIError("Recording service unavailable", status_code=502, retryable=True)

    if upstream.status_code in (401, 403):
        upstream.close()
        raise APIError("Recording service unavailable", status_code=502, retryable=True)
    if upstream.status_code == 404:
        upstream.close()
        raise APIError("Video is not available yet", status_code=404)
    if upstream.status_code >= 400:
        upstream.close()
        raise APIError("Recording service unavailable", status_code=502, retryable=True)

    response_headers = {}
    for header in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Cache-Control"):
        if upstream.headers.get(header):
            response_headers[header] = upstream.headers[header]
    return StreamingResponse(
        upstream.iter_content(chunk_size=65536),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("Content-Type") or "video/mp4",
        headers=response_headers,
    )


def stream_result_video_by_session(*, db: Session, session_token: str, recruiter_id: str, agency_id: str, range_header: str = ""):
    session = InterviewSessionRepository(db).get_by_token(session_token)
    if not session or not session.job_id or not session.candidate_id:
        raise APIError("Interview session not found", status_code=404)
    if not _result_agency_matches(db=db, job_id=session.job_id, candidate_id=session.candidate_id, agency_id=agency_id):
        raise APIError("Forbidden", status_code=403)

    interview_row = _fetch_interview_result_row(db, job_id=session.job_id, candidate_id=session.candidate_id)
    recording_path = _normalize_text(
        interview_row.get("video_url")
        or _metadata_map(getattr(session, "scheduling_metadata", {})).get("recordingPath")
        or _metadata_map(getattr(session, "scheduling_metadata", {})).get("recording_path")
    )
    if not recording_path:
        raise APIError("Video is not available yet", status_code=404)
    interview_status = _normalize_lower(interview_row.get("status"))
    if interview_status not in {"completed", "interview_completed", "results_ready"} and not interview_row.get("completed_at"):
        raise APIError("Interview is not completed", status_code=409)
    return _proxy_recording(recording_path=recording_path, range_header=range_header)


def stream_result_video(*, db: Session, workflow_token: str, recruiter_id: str, agency_id: str, range_header: str = ""):
    payload, job_id, candidate_id, resolved_workflow_token = _workflow_token_payload(db, workflow_token)
    if not job_id or not candidate_id:
        raise APIError("Result not found", status_code=404)

    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    if not _result_agency_matches(db=db, job_id=job_id, candidate_id=candidate_id, agency_id=agency_id):
        raise APIError("Forbidden", status_code=403)

    interview_row = _fetch_interview_result_row(db, job_id=job_id, candidate_id=candidate_id)
    recording_path = _normalize_text(interview_row.get("video_url") or "")

    emit_trace(
        logger,
        "recording_fetch",
        workflow_token=resolved_workflow_token,
        candidate_id=candidate_id,
        recruiter_id=recruiter_id,
        video_url=recording_path or "none",
    )

    if not recording_path:
        raise APIError("Video is not available yet", status_code=404)

    return _proxy_recording(recording_path=recording_path, range_header=range_header)
