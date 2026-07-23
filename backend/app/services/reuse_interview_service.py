"""
reuse_interview_service.py — Phase 2.2: Internal Talent Reuse.

Single entry point for detecting and reusing a completed interview
when a recruiter selects an internal candidate.

Design rules
------------
* Only one place in the codebase checks for a reusable interview.
* Never overwrites a completed interview's status, transcript, scores, or video.
* Never creates a new interview, enrichment job, or notification when reusing.
* Returns a typed result so callers can branch without knowing internals.
* All operations safe-fail — selection flow is never blocked.
* Reuses InterviewRepository, NotificationWorkflowTokenRepository, and
  results_service._fetch_interview_result_row without duplicating their logic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Status values that qualify an interview as "completed and reusable"
_REUSABLE_STATUSES = {"completed", "interview_completed", "results_ready"}


@dataclass
class ReuseInterviewResult:
    """Returned by check_and_reuse_interview regardless of outcome."""

    reused: bool = False                  # True  → skip enrichment / new workflow
    interview_id: str = ""
    workflow_token: str = ""
    interview_status: str = ""
    has_transcript: bool = False
    has_ai_summary: bool = False
    has_recording: bool = False
    overall_score: float = 0.0
    skip_reason: str = ""                 # populated when reused=False
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def check_and_reuse_interview(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
) -> ReuseInterviewResult:
    """
    Check whether a completed interview already exists for this candidate.

    If one exists:
      - Return ReuseInterviewResult(reused=True, ...) with all available data.
      - The caller MUST skip enrichment, interview creation, scheduling,
        notification generation, and workflow-token creation.

    If none exists:
      - Return ReuseInterviewResult(reused=False, skip_reason=...).
      - The caller continues with the normal selection workflow unchanged.

    Never raises — all errors are captured in result.error.
    """
    result = ReuseInterviewResult()

    try:
        from app.db.repositories import InterviewRepository, NotificationWorkflowTokenRepository
        from app.services.results_service import _fetch_interview_result_row

        # ── 1. Look up the interviews row ─────────────────────────────────────
        interview_row = InterviewRepository(db).get_by_job_and_candidate(
            job_id, candidate_id
        )
        if not interview_row:
            result.skip_reason = "no_interview_row"
            return result

        status = str(interview_row.status or "").strip().lower()
        result.interview_id = str(interview_row.id or "")
        result.interview_status = status

        if status not in _REUSABLE_STATUSES:
            result.skip_reason = f"interview_status_not_completed:{status}"
            return result

        # ── 2. Verify transcript exists (required for Results page) ───────────
        raw = _fetch_interview_result_row(db, job_id=job_id, candidate_id=candidate_id)
        transcript = str(raw.get("transcript") or "").strip()
        if not transcript:
            result.skip_reason = "completed_interview_has_no_transcript"
            return result

        # ── 3. Resolve workflow token ─────────────────────────────────────────
        # Prefer the token stored on the interview row itself (written by the
        # interview project), then fall back to notification_workflow_tokens.
        workflow_token = str(raw.get("workflow_token") or "").strip()
        if not workflow_token:
            token_row = NotificationWorkflowTokenRepository(db).get_active_by_candidate(
                job_id=job_id,
                candidate_id=candidate_id,
                source_app="ui",
            )
            if token_row:
                workflow_token = str(token_row.token or "").strip()

        if not workflow_token:
            result.skip_reason = "no_workflow_token_for_completed_interview"
            return result

        # ── 4. Build reuse payload ────────────────────────────────────────────
        result.reused = True
        result.workflow_token = workflow_token
        result.has_transcript = True
        result.has_ai_summary = bool(str(raw.get("ai_summary") or "").strip())
        result.has_recording = bool(str(raw.get("video_url") or "").strip())
        result.overall_score = _safe_float(raw.get("interview_score"))
        result.metadata = {
            "interviewId": result.interview_id,
            "workflowToken": workflow_token,
            "interviewStatus": status,
            "hasTranscript": result.has_transcript,
            "hasAiSummary": result.has_ai_summary,
            "hasRecording": result.has_recording,
            "overallScore": result.overall_score,
            "reuseSource": "completed_interview",
        }

        logger.info(
            "interview_reuse_detected job_id=%s candidate_id=%s "
            "interview_id=%s workflow_token=%s score=%.1f",
            job_id,
            candidate_id,
            result.interview_id,
            workflow_token,
            result.overall_score,
        )

    except Exception as exc:
        result.reused = False
        result.skip_reason = "reuse_check_error"
        result.error = str(exc)[:300]
        logger.warning(
            "interview_reuse_check_failed job_id=%s candidate_id=%s error=%s",
            job_id,
            candidate_id,
            result.error,
        )

    return result


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default
