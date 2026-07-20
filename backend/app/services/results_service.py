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
from urllib.parse import quote

import requests
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import (
    INTERNAL_API_KEY,
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
from app.services.ats_lifecycle_service import candidate_timeline, normalize_ats_status
from app.services.interview_stage_service import get_interview_insights
from app.services.slack_integration import post_slack_message
from app.services.slack_tenant_service import SlackCompanyResolver
from app.utils.exceptions import APIError
from app.utils.observability import emit_trace

logger = logging.getLogger(__name__)

def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


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
                    i.company_id,
                    i.candidate_id,
                    i.status,
                    i.interview_score,
                    i.technical_score,
                    i.communication_score,
                    i.culture_fit_score,
                    i.ai_summary,
                    i.transcript,
                    i.feedback,
                    i.interviewer_notes,
                    i.video_url,
                    i.completed_at,
                    i.created_at,
                    cp.name                    AS candidate_name,
                    cp.current_role            AS candidate_role,
                    cp.current_company         AS candidate_company,
                    cp.summary                 AS candidate_summary,
                    cp.skills                  AS candidate_skills,
                    cp.raw_data                AS candidate_raw_data,
                    nt.token                   AS workflow_token
                FROM interviews i
                LEFT JOIN candidates cp
                    ON cp.job_id = i.job_id
                   AND cp.candidate_id = i.candidate_id
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
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    session = InterviewSessionRepository(db).get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
    insights = get_interview_insights(db=db, job_id=job_id, candidate_id=candidate_id)
    evaluations = InterviewEvaluationRepository(db).list_for_candidate(job_id=job_id, candidate_id=candidate_id, limit=20)
    timeline = candidate_timeline(db=db, job_id=job_id, candidate_id=candidate_id, limit=100)

    # ── Primary source: interviews row keyed by job_id + candidate_id ─────────
    interview_row = _fetch_interview_result_row(db, job_id=job_id, candidate_id=candidate_id)
    transcript = _normalize_text(interview_row.get("transcript") or "")
    if not transcript:
        return {"status": "pending"}

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
        "candidate": {
            "id": candidate_id,
            "name": _normalize_text(getattr(profile, "name", "") or candidate_id),
            "role": _normalize_text(getattr(profile, "role", "")),
            "company": _normalize_text(getattr(profile, "company", "")),
            "headline": _normalize_text(getattr(profile, "current_title", "")),
            "location": _normalize_text(raw_data.get("location") or ""),
            "email": _normalize_text(raw_data.get("email") or ""),
            "summary": _normalize_text(getattr(profile, "summary", "")),
            "skills": list(getattr(profile, "skills", []) or []),
            "source": "interviews_table",
        },
        "recording": {
            "sessionToken": _normalize_text(interview_row.get("workflow_token") or workflow_token),
            "recordingPath": "",
            "videoAvailable": video_available,
        },
        "transcript": transcript,
        "summary": summary,
        "scores": {
            "overall": overall_score,
            "technical": technical_score,
            "communication": communication_score,
            "cultureFit": culture_fit_score,
        },
        "decision": recommendation,
        "status": status,
        "timeline": {"events": timeline},
        "recommendation": recommendation,
        "analysis": {
            "strengths": [],
            "weaknesses": [],
            "riskAreas": [],
            "communication": _normalize_text(interview_row.get("interviewer_notes") or ""),
            "technicalDepth": "",
        },
        "metadata": {
            "workflowToken": workflow_token,
            "jobId": job_id,
            "candidateId": candidate_id,
            "recruiterId": recruiter_id,
            "evaluationReady": evaluation_ready,
            "sessionToken": _normalize_text(interview_row.get("workflow_token") or workflow_token),
            "scheduledAt": (
                interview_row["completed_at"].isoformat()
                if interview_row.get("completed_at") else
                (session.scheduled_at.isoformat() if session and session.scheduled_at else None)
            ),
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
                "show": status in {"interview_completed", "results_ready"} and not _normalize_text(ats_metadata.get("recruiterDecision")),
                "message": "Would you like to advance this candidate?",
            },
        },
    }

    return response


def _candidate_result_rows(db: Session, job_id: str, *, company_id: str) -> list[dict[str, Any]]:
    try:
        results = db.execute(
            text("""
                SELECT
                    i.candidate_id,
                    i.company_id,
                    i.status,
                    i.interview_score,
                    i.transcript,
                    i.video_url,
                    i.feedback,
                    i.completed_at,
                    cp.name,
                    cp.fit_score,
                    cp.decision,
                    nt.token AS workflow_token
                FROM interviews i
                LEFT JOIN candidates cp
                    ON cp.job_id = i.job_id
                   AND cp.candidate_id = i.candidate_id
                LEFT JOIN notification_workflow_tokens nt
                    ON nt.job_id = i.job_id
                   AND nt.candidate_id = i.candidate_id
                   AND nt.is_active = 1
                WHERE i.job_id = :job_id
                  AND i.company_id = :company_id
                ORDER BY COALESCE(i.interview_score, 0) DESC, COALESCE(cp.fit_score, 0) DESC, cp.name ASC
            """),
            {"job_id": job_id, "company_id": company_id},
        ).mappings().all()
    except Exception as exc:
        logger.warning("results_list_fetch_failed job_id=%s error=%s", job_id, str(exc))
        return []

    rows: list[dict[str, Any]] = []
    for row in results:
        transcript = _normalize_text(row.get("transcript") or "")
        status = _normalize_text(row.get("status") or "")
        score = float(row.get("interview_score") or row.get("fit_score") or 0.0)
        rows.append(
            {
                "candidateId": _normalize_text(row.get("candidate_id") or ""),
                "name": _normalize_text(row.get("name") or row.get("candidate_id") or ""),
                "status": "pending" if not transcript else (status or "completed"),
                "workflowToken": _normalize_text(row.get("workflow_token") or ""),
                "score": score,
                "recommendation": _normalize_text(row.get("feedback") or row.get("decision") or "review"),
                "completionState": "pending" if not transcript else ("results_ready" if status == "completed" else status or "results_ready"),
                "videoAvailable": bool(transcript and _normalize_text(row.get("video_url") or "")),
            }
        )
    return rows


def list_results(*, db: Session, job_id: str, recruiter_id: str, company_id: str) -> dict[str, Any]:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    if str(getattr(job, "company_id", "") or "").strip() != str(company_id or "").strip():
        raise APIError("Forbidden", status_code=403)

    emit_trace(logger, "results_fetch", workflow_token="", candidate_id="", recruiter_id=recruiter_id, job_id=job_id)
    candidates = _candidate_result_rows(db, job_id, company_id=company_id)
    return {
        "jobId": job_id,
        "companyId": company_id,
        "recruiterId": recruiter_id,
        "candidates": candidates,
        "counts": {
            "completed": len([item for item in candidates if item["completionState"] == "results_ready"]),
            "available": len(candidates),
        },
    }


def get_result_by_workflow_token(*, db: Session, workflow_token: str, recruiter_id: str, company_id: str) -> dict[str, Any]:
    payload, job_id, candidate_id, resolved_workflow_token = _workflow_token_payload(db, workflow_token)
    if not job_id or not candidate_id:
        raise APIError("Result not found", status_code=404)

    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    if str(getattr(job, "company_id", "") or "").strip() != str(company_id or "").strip():
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


def stream_result_video(*, db: Session, workflow_token: str, recruiter_id: str, company_id: str, range_header: str = ""):
    payload, job_id, candidate_id, resolved_workflow_token = _workflow_token_payload(db, workflow_token)
    if not job_id or not candidate_id:
        raise APIError("Result not found", status_code=404)

    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    if str(getattr(job, "company_id", "") or "").strip() != str(company_id or "").strip():
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

    interview_app_url = (INTERVIEW_APP_URL or "").rstrip("/")
    if not interview_app_url:
        raise APIError("Video is not available yet", status_code=404)

    video_url = f"{interview_app_url}/api/video/{quote(recording_path, safe='/')}"
    headers = {
        "Accept": "video/*,application/octet-stream;q=0.9,*/*;q=0.8",
        "X-Internal-API-Key": INTERNAL_API_KEY,
    }
    if range_header.strip():
        headers["Range"] = range_header.strip()
    try:
        upstream = requests.get(video_url, headers=headers, timeout=HTTP_TIMEOUT_SECONDS, stream=True)
        if upstream.status_code < 400:
            resp_headers = {}
            for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Cache-Control"):
                if upstream.headers.get(h):
                    resp_headers[h] = upstream.headers[h]
            return StreamingResponse(
                upstream.iter_content(chunk_size=65536),
                status_code=upstream.status_code,
                media_type=upstream.headers.get("Content-Type") or "video/mp4",
                headers=resp_headers,
            )
    except Exception as exc:
        logger.warning("interview_video_proxy_failed url=%s error=%s", video_url, str(exc))

    raise APIError("Video is not available yet", status_code=404)
