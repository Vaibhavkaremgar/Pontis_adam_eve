from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import requests
from fastapi.responses import StreamingResponse
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

_RESULT_STATUSES = {"interview_completed", "evaluation_processing", "results_ready"}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


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

    remote = dict(remote_result or {})
    candidate_remote = remote.get("candidate") if isinstance(remote.get("candidate"), dict) else {}
    recording_remote = remote.get("recording") if isinstance(remote.get("recording"), dict) else {}
    scores_remote = remote.get("scores") if isinstance(remote.get("scores"), dict) else {}
    timeline_remote = remote.get("timeline") if isinstance(remote.get("timeline"), dict) else {}
    raw_data = getattr(profile, "raw_data", {}) if isinstance(getattr(profile, "raw_data", {}), dict) else {}
    raw_location = candidate_remote.get("location") or raw_data.get("location") or ""
    raw_email = candidate_remote.get("email") or raw_data.get("email") or ""

    overall_score = float(scores_remote.get("overall") or getattr(profile, "fit_score", 0.0) or 0.0)
    recommendation = _normalize_text(remote.get("decision") or remote.get("recommendation") or getattr(profile, "decision", "")).lower()
    current_status = normalize_ats_status(getattr(profile, "ats_status", "") or getattr(profile, "candidate_status", ""))
    evaluation_ready = (session.evaluation_status or "").strip().lower() == "completed" if session else False
    status = _normalize_text(remote.get("status") or current_status or getattr(session, "status", "") or "interview_completed")

    transcript = _normalize_text(remote.get("transcript") or "")
    if not transcript:
        transcript = _normalize_text(remote.get("conversation") or remote.get("transcriptText") or "")
    if not transcript and evaluations:
        transcript = "\n".join(
            [
                f"{row.stage_name}: {row.summary}"
                for row in evaluations
                if _normalize_text(row.summary)
            ]
        ).strip()

    summary = _normalize_text(remote.get("summary") or remote.get("analysisSummary") or "")
    if not summary:
        summary = _normalize_text(getattr(evaluations[0], "summary", "") if evaluations else "") or _normalize_text(insights.get("intelligence", {}).get("summary"))

    response = {
        "candidate": {
            "id": candidate_id,
            "name": _normalize_text(getattr(profile, "name", "") or candidate_remote.get("name") or candidate_id),
            "role": _normalize_text(getattr(profile, "role", "") or candidate_remote.get("role")),
            "company": _normalize_text(getattr(profile, "company", "") or candidate_remote.get("company")),
            "headline": _normalize_text(getattr(profile, "current_title", "") or candidate_remote.get("headline")),
            "location": _normalize_text(raw_location),
            "email": _normalize_text(raw_email),
            "summary": _normalize_text(getattr(profile, "summary", "") or candidate_remote.get("summary")),
            "skills": list(getattr(profile, "skills", []) or candidate_remote.get("skills") or []),
            "source": "pontis" if remote_result else "local_fallback",
        },
        "recording": {
            "sessionToken": _normalize_text(recording_remote.get("sessionToken") or session.token if session else ""),
            # Keep the frontend blind to Pontis filesystem or internal recording URLs.
            "recordingPath": "",
            "videoAvailable": bool(recording_remote.get("videoAvailable", False) or status in {"interview_completed", "results_ready"}),
        },
        "transcript": transcript,
        "summary": summary,
        "scores": {
            "overall": overall_score,
            "technical": float(scores_remote.get("technical") or getattr(profile, "fit_score", 0.0) or 0.0),
            "communication": float(scores_remote.get("communication") or insights.get("intelligence", {}).get("communicationScore") or 0.0),
            "cultureFit": float(scores_remote.get("cultureFit") or insights.get("intelligence", {}).get("cultureFitScore") or 0.0),
        },
        "decision": _normalize_text(remote.get("decision") or recommendation or getattr(profile, "decision", "")).lower(),
        "status": _normalize_text(remote.get("status") or status or "interview_completed"),
        "timeline": timeline_remote or {"events": timeline},
        "recommendation": recommendation,
        "analysis": {
            "strengths": list(remote.get("strengths") or []),
            "weaknesses": list(remote.get("weaknesses") or []),
            "riskAreas": list(remote.get("riskAreas") or []),
            "communication": _normalize_text(remote.get("communication") or ""),
            "technicalDepth": _normalize_text(remote.get("technicalDepth") or ""),
        },
        "metadata": {
            "workflowToken": workflow_token,
            "jobId": job_id,
            "candidateId": candidate_id,
            "recruiterId": recruiter_id,
            "evaluationReady": evaluation_ready,
            "insights": insights,
            "evaluations": [
                {
                    "id": row.id,
                    "stageName": row.stage_name,
                    "summary": row.summary,
                    "recommendation": row.recommendation,
                    "updatedAt": row.updated_at.isoformat(),
                }
                for row in evaluations
            ],
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

    video_url = ""
    if PONTIS_API_BASE_URL:
        target_path = PONTIS_INTERVIEW_RECORDING_PATH.format(workflowToken=quote(resolved_workflow_token, safe=""))
        video_url = f"{_pontis_base_url()}{target_path}"

    emit_trace(
        logger,
        "recording_fetch",
        workflow_token=resolved_workflow_token,
        candidate_id=candidate_id,
        recruiter_id=recruiter_id,
        video_url=video_url or "local_fallback",
    )

    if not video_url:
        raise APIError("Video is not available yet", status_code=404)

    headers = _pontis_headers()
    if range_header.strip():
        headers["Range"] = range_header.strip()
    headers.setdefault("Accept", "video/*,application/octet-stream;q=0.9,*/*;q=0.8")

    upstream = requests.get(
        video_url,
        headers=headers,
        timeout=PONTIS_REQUEST_TIMEOUT_SECONDS or HTTP_TIMEOUT_SECONDS,
        stream=True,
    )
    if upstream.status_code >= 400:
        emit_trace(
            logger,
            "video_stream_failed",
            workflow_token=resolved_workflow_token,
            candidate_id=candidate_id,
            recruiter_id=recruiter_id,
            status_code=upstream.status_code,
        )
        raise APIError("Video stream unavailable", status_code=upstream.status_code if upstream.status_code < 500 else 502)

    response_headers = {}
    for header_name in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Cache-Control", "ETag", "Last-Modified"):
        value = upstream.headers.get(header_name)
        if value:
            response_headers[header_name] = value

    def _iter_content():
        try:
            for chunk in upstream.iter_content(chunk_size=1024 * 64):
                if chunk:
                    yield chunk
        finally:
            upstream.close()
            emit_trace(
                logger,
                "video_stream_completed",
                workflow_token=resolved_workflow_token,
                candidate_id=candidate_id,
                recruiter_id=recruiter_id,
            )

    return StreamingResponse(
        _iter_content(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("Content-Type") or "video/mp4",
        headers=response_headers,
    )
