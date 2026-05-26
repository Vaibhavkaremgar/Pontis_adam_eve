from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from fastapi.responses import StreamingResponse, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import (
    INTERNAL_API_KEY,
    HTTP_TIMEOUT_SECONDS,
    PONTIS_API_BASE_URL,
    PONTIS_INTERNAL_API_KEY,
    PONTIS_INTERVIEW_RECORDING_PATH,
    PONTIS_INTERVIEW_RESULT_PATH,
    PONTIS_REQUEST_TIMEOUT_SECONDS,
)
from app.db.repositories import (
    CandidateProfileRepository,
    InterviewEvaluationRepository,
    InterviewSessionRepository,
    JobRepository,
    NotificationWorkflowTokenRepository,
)
from app.services.ats_lifecycle_service import candidate_timeline, normalize_ats_status
from app.services.interview_stage_service import get_interview_insights
from app.utils.exceptions import APIError
from app.utils.observability import emit_trace

logger = logging.getLogger(__name__)

# Base directory where the interview project stores recordings on disk.
# Must be the same mounted volume path accessible to Adam's backend.
RECORDING_STORAGE_DIR = os.getenv("RECORDING_STORAGE_DIR", "").strip().rstrip("/")

_RESULT_STATUSES = {
    "interview_completed",
    "evaluation_processing",
    "results_ready",
    "advanced",
    "second_round_requested",
    "second_round_scheduled",
    "final_round",
    "offer_stage",
    "offer_sent",
    "placed",
    "search_closed",
    "rejected",
    "hired",
}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _fetch_interview_session_by_workflow_token(db: Session, workflow_token: str) -> dict[str, Any]:
    """
    Query interview_sessions directly using workflow_token column.
    Returns all result fields: ai_summary, transcript, scores, recording_path, session_token.
    Also joins interviews table (source_app='adam') for score columns.
    """
    if not workflow_token:
        return {}
    try:
        result = db.execute(
            text("""
                SELECT
                    s.id                        AS session_id,
                    s.session_token,
                    s.recording_path,
                    s.vapi_recording_url,
                    s.ai_summary,
                    s.last_transcript_snapshot   AS transcript,
                    s.status                     AS session_status,
                    s.scheduled_at,
                    s.candidate_id               AS session_candidate_id,
                    i.id                         AS interview_id,
                    i.interview_score,
                    i.technical_score,
                    i.communication_score,
                    i.culture_fit_score,
                    i.ai_summary                 AS interview_ai_summary,
                    i.transcript                 AS interview_transcript,
                    i.video_url,
                    i.status                     AS interview_status,
                    i.feedback,
                    i.interviewer_notes
                FROM interview_sessions s
                LEFT JOIN interviews i
                    ON i.candidate_id = s.candidate_id
                    AND i.source_app = 'adam'
                WHERE s.workflow_token = :wt
                   OR s.token = :wt
                ORDER BY s.created_at DESC
                LIMIT 1
            """),
            {"wt": workflow_token},
        ).mappings().first()
        if result:
            return dict(result)
    except Exception as exc:
        logger.warning("interview_session_fetch_failed workflow_token=%s error=%s", workflow_token, str(exc))
    return {}


def _workflow_token_payload(db: Session, workflow_token: str) -> tuple[dict[str, Any] | None, str, str, str]:
    token_row = NotificationWorkflowTokenRepository(db).get_by_token(workflow_token, source_app="adam")
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
        "sourceApp": str((payload or {}).get("sourceApp") or "adam"),
    }


def _build_candidate_snapshot(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    workflow_token: str,
    recruiter_id: str,
    remote_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    session = InterviewSessionRepository(db).get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
    insights = get_interview_insights(db=db, job_id=job_id, candidate_id=candidate_id)
    evaluations = InterviewEvaluationRepository(db).list_for_candidate(job_id=job_id, candidate_id=candidate_id, limit=20)
    timeline = candidate_timeline(db=db, job_id=job_id, candidate_id=candidate_id, limit=100)

    # ── Primary source: interview_sessions joined with interviews via workflow_token ──
    interview_row = _fetch_interview_session_by_workflow_token(db, workflow_token)

    # ── AI Summary ────────────────────────────────────────────────────────────
    # Priority: interviews.ai_summary > interview_sessions.ai_summary > evaluations
    summary = _normalize_text(
        interview_row.get("interview_ai_summary")
        or interview_row.get("ai_summary")
        or (evaluations[0].summary if evaluations else "")
        or insights.get("intelligence", {}).get("summary") or ""
    )

    # ── Transcript ────────────────────────────────────────────────────────────
    # Priority: interviews.transcript > interview_sessions.last_transcript_snapshot > evaluations
    transcript = _normalize_text(
        interview_row.get("interview_transcript")
        or interview_row.get("transcript")
        or ""
    )
    if not transcript and evaluations:
        transcript = "\n".join(
            f"{ev.stage_name}: {ev.summary}" for ev in evaluations if _normalize_text(ev.summary)
        ).strip()

    # ── Scores ────────────────────────────────────────────────────────────────
    # Priority: interviews score columns > candidate fit_score fallback
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
    # Priority: interviews.video_url > interview_sessions.recording_path > vapi_recording_url
    recording_path = _normalize_text(
        interview_row.get("video_url")
        or interview_row.get("recording_path")
        or ""
    )
    session_token = _normalize_text(
        interview_row.get("session_token")
        or getattr(session, "token", "") or ""
    )
    vapi_url = _normalize_text(interview_row.get("vapi_recording_url") or "")
    video_available = bool(recording_path or vapi_url)

    # ── Status ────────────────────────────────────────────────────────────────
    raw_data = getattr(profile, "raw_data", {}) if isinstance(getattr(profile, "raw_data", {}), dict) else {}
    current_status = normalize_ats_status(getattr(profile, "ats_status", "") or getattr(profile, "candidate_status", ""))
    ats_metadata = getattr(profile, "ats_metadata", {}) if isinstance(getattr(profile, "ats_metadata", {}), dict) else {}
    interview_status = _normalize_text(interview_row.get("interview_status") or interview_row.get("session_status") or "")
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
            "sessionToken": session_token,
            # recordingPath hidden from frontend — video served via /results/video/{workflowToken}
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
            "sessionToken": session_token,
            "scheduledAt": (
                interview_row["scheduled_at"].isoformat()
                if interview_row.get("scheduled_at") else
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


def _pontis_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "X-Request-Source": "adam",
    }
    internal_key = (PONTIS_INTERNAL_API_KEY or INTERNAL_API_KEY or "").strip()
    if internal_key:
        headers["X-Internal-API-Key"] = internal_key
    return headers


def _pontis_base_url() -> str:
    return PONTIS_API_BASE_URL.rstrip("/")


def _call_pontis_json(*, workflow_token: str, route_template: str) -> dict[str, Any]:
    if not PONTIS_API_BASE_URL:
        return {}
    target_path = route_template.format(workflowToken=quote(workflow_token, safe=""))
    url = f"{_pontis_base_url()}{target_path}"
    emit_trace(logger, "results_pontis_proxy_start", workflow_token=workflow_token, candidate_id="", recruiter_id="", url=url)
    response = requests.get(url, headers=_pontis_headers(), timeout=PONTIS_REQUEST_TIMEOUT_SECONDS or HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and "data" in payload and isinstance(payload.get("data"), dict):
        return dict(payload["data"])
    return payload if isinstance(payload, dict) else {}


def _candidate_result_rows(db: Session, job_id: str) -> list[dict[str, Any]]:
    profiles = CandidateProfileRepository(db).list_for_job(job_id)
    rows: list[dict[str, Any]] = []
    for profile in profiles:
        workflow_row = NotificationWorkflowTokenRepository(db).get_active_by_candidate(
            job_id=job_id,
            candidate_id=profile.candidate_id,
            source_app="adam",
            token_type="slot_booking",
        )
        if not workflow_row:
            continue
        interview_session = InterviewSessionRepository(db).get_by_job_and_candidate(job_id=job_id, candidate_id=profile.candidate_id)
        ats_status = normalize_ats_status(getattr(profile, "ats_status", "") or getattr(profile, "candidate_status", ""))
        evaluation_ready = bool(interview_session and (interview_session.evaluation_status or "").strip().lower() == "completed")
        result_status = (interview_session.status if interview_session else ats_status).strip().lower()
        if result_status not in _RESULT_STATUSES and not evaluation_ready:
            continue
        rows.append(
            {
                "candidateId": profile.candidate_id,
                "name": profile.name or profile.candidate_id,
                "status": result_status or "results_ready",
                "workflowToken": workflow_row.token,
                "score": float(getattr(profile, "fit_score", 0.0) or 0.0),
                "recommendation": getattr(profile, "decision", "") or "review",
                "completionState": "results_ready" if evaluation_ready or result_status == "results_ready" else result_status,
                "videoAvailable": bool(interview_session and (interview_session.evaluation_status or "").strip().lower() == "completed"),
            }
        )
    rows.sort(key=lambda item: (-float(item.get("score") or 0.0), item.get("name", "")))
    return rows


def list_results(*, db: Session, job_id: str, recruiter_id: str) -> dict[str, Any]:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)

    emit_trace(logger, "results_fetch", workflow_token="", candidate_id="", recruiter_id=recruiter_id, job_id=job_id)
    candidates = _candidate_result_rows(db, job_id)
    return {
        "jobId": job_id,
        "recruiterId": recruiter_id,
        "candidates": candidates,
        "counts": {
            "completed": len([item for item in candidates if item["completionState"] == "results_ready"]),
            "available": len(candidates),
        },
    }


def get_result_by_workflow_token(*, db: Session, workflow_token: str, recruiter_id: str) -> dict[str, Any]:
    payload, job_id, candidate_id, resolved_workflow_token = _workflow_token_payload(db, workflow_token)
    if not job_id or not candidate_id:
        raise APIError("Result not found", status_code=404)

    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)

    remote_result: dict[str, Any] = {}
    if PONTIS_API_BASE_URL:
        try:
            remote_result = _call_pontis_json(workflow_token=resolved_workflow_token, route_template=PONTIS_INTERVIEW_RESULT_PATH)
        except requests.RequestException as exc:
            emit_trace(
                logger,
                "results_pontis_proxy_failed",
                workflow_token=resolved_workflow_token,
                candidate_id=candidate_id,
                recruiter_id=recruiter_id,
                error=str(exc),
            )
            remote_result = {}

    emit_trace(
        logger,
        "results_fetch",
        workflow_token=resolved_workflow_token,
        candidate_id=candidate_id,
        recruiter_id=recruiter_id,
        job_id=job_id,
    )
    return _build_candidate_snapshot(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        workflow_token=resolved_workflow_token,
        recruiter_id=recruiter_id,
        remote_result=remote_result or payload or {},
    )


def stream_result_video(*, db: Session, workflow_token: str, recruiter_id: str, range_header: str = ""):
    payload, job_id, candidate_id, resolved_workflow_token = _workflow_token_payload(db, workflow_token)
    if not job_id or not candidate_id:
        raise APIError("Result not found", status_code=404)

    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)

    # ── Get recording_path from interview_sessions / interviews table ──────────
    interview_row = _fetch_interview_session_by_workflow_token(db, resolved_workflow_token)
    recording_path = _normalize_text(
        interview_row.get("video_url")
        or interview_row.get("recording_path")
        or ""
    )
    vapi_url = _normalize_text(interview_row.get("vapi_recording_url") or "")
    session_token = _normalize_text(interview_row.get("session_token") or "")

    emit_trace(
        logger,
        "recording_fetch",
        workflow_token=resolved_workflow_token,
        candidate_id=candidate_id,
        recruiter_id=recruiter_id,
        video_url=recording_path or vapi_url or "none",
    )

    # ── Option 1: vapi_recording_url — proxy from external URL ─────────────────
    if vapi_url and vapi_url.startswith("http"):
        headers: dict[str, str] = {}
        if range_header.strip():
            headers["Range"] = range_header.strip()
        try:
            upstream = requests.get(vapi_url, headers=headers, timeout=HTTP_TIMEOUT_SECONDS, stream=True)
            if upstream.status_code < 400:
                resp_headers = {}
                for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                    if upstream.headers.get(h):
                        resp_headers[h] = upstream.headers[h]
                return StreamingResponse(
                    upstream.iter_content(chunk_size=65536),
                    status_code=upstream.status_code,
                    media_type=upstream.headers.get("Content-Type") or "video/webm",
                    headers=resp_headers,
                )
        except Exception as exc:
            logger.warning("vapi_video_proxy_failed url=%s error=%s", vapi_url, str(exc))

    # ── Option 2: recording_path on shared filesystem ────────────────────────
    if recording_path:
        # recording_path may be just a filename like "72b494ee-...mp4"
        # or a full path. Try RECORDING_STORAGE_DIR prefix first.
        candidate_paths = []
        if RECORDING_STORAGE_DIR:
            candidate_paths.append(Path(RECORDING_STORAGE_DIR) / recording_path)
        candidate_paths.append(Path(recording_path))

        for file_path in candidate_paths:
            if file_path.exists() and file_path.is_file():
                file_size = file_path.stat().st_size
                content_type = "video/mp4" if str(file_path).endswith(".mp4") else "video/webm"

                # Handle Range requests for video seeking
                start = 0
                end = file_size - 1
                status_code = 200
                resp_headers: dict[str, str] = {
                    "Accept-Ranges": "bytes",
                    "Content-Type": content_type,
                }

                if range_header.strip():
                    try:
                        range_val = range_header.strip().replace("bytes=", "")
                        parts = range_val.split("-")
                        start = int(parts[0]) if parts[0] else 0
                        end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
                        end = min(end, file_size - 1)
                        status_code = 206
                        resp_headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
                        resp_headers["Content-Length"] = str(end - start + 1)
                    except Exception:
                        start = 0
                        end = file_size - 1
                else:
                    resp_headers["Content-Length"] = str(file_size)

                def _iter_file(path: Path, s: int, e: int):
                    with open(path, "rb") as f:
                        f.seek(s)
                        remaining = e - s + 1
                        chunk_size = 65536
                        while remaining > 0:
                            chunk = f.read(min(chunk_size, remaining))
                            if not chunk:
                                break
                            yield chunk
                            remaining -= len(chunk)

                return StreamingResponse(
                    _iter_file(file_path, start, end),
                    status_code=status_code,
                    media_type=content_type,
                    headers=resp_headers,
                )

    # ── Option 3: Pontis API proxy (legacy fallback) ───────────────────────
    if PONTIS_API_BASE_URL and session_token:
        target_path = PONTIS_INTERVIEW_RECORDING_PATH.format(workflowToken=quote(session_token, safe=""))
        video_url = f"{PONTIS_API_BASE_URL.rstrip('/')}{target_path}"
        proxy_headers = {"Accept": "video/*,application/octet-stream;q=0.9,*/*;q=0.8"}
        if PONTIS_INTERNAL_API_KEY:
            proxy_headers["X-Internal-API-Key"] = PONTIS_INTERNAL_API_KEY
        if range_header.strip():
            proxy_headers["Range"] = range_header.strip()
        try:
            upstream = requests.get(video_url, headers=proxy_headers, timeout=PONTIS_REQUEST_TIMEOUT_SECONDS or HTTP_TIMEOUT_SECONDS, stream=True)
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
            logger.warning("pontis_video_proxy_failed url=%s error=%s", video_url, str(exc))

    raise APIError("Video is not available yet", status_code=404)
