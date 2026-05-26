from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.repositories import (
    AutomationJobRepository,
    CandidateProfileRepository,
    InterviewEvaluationRepository,
    InterviewSessionRepository,
    NotificationEventRepository,
    OutreachEventRepository,
    OrchestrationSessionRepository,
    RecruiterNoteRepository,
    RecruiterTaskRepository,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_status(value: str | None) -> str:
    return (value or "").strip().lower().replace(" ", "_")


def get_candidate_engagement_intelligence(*, db: Session, job_id: str, candidate_id: str) -> dict[str, Any]:
    outreach_repo = OutreachEventRepository(db)
    notification_repo = NotificationEventRepository(db)
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    outreach_rows = [row for row in outreach_repo.list_for_job(job_id) if row.candidate_id == candidate_id]
    reply_count = sum(1 for row in outreach_rows if row.responded_at or _normalize_status(getattr(row, "reply_intent", "")) in {"interested", "needs_more_info", "ambiguous", "declined", "unsubscribe"})
    sent_count = sum(1 for row in outreach_rows if _normalize_status(row.status) in {"sent", "delivered", "opened", "replied", "sending", "queued"})
    engagement_score = 0.0
    if sent_count:
        engagement_score += min(1.0, reply_count / sent_count)
    if profile and profile.resume_received_at:
        engagement_score += 0.1
    interview_session = InterviewSessionRepository(db).get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
    if interview_session:
        engagement_score += 0.1
        if _normalize_status(interview_session.status) == "interview_scheduled":
            engagement_score += 0.1
    unread_notifications = [
        row for row in notification_repo.list_for_job(job_id)
        if row.candidate_id == candidate_id and not bool(getattr(row, "is_read", False))
    ]
    momentum = "warm" if engagement_score >= 0.65 else "cool" if engagement_score >= 0.3 else "cold"
    return {
        "jobId": job_id,
        "candidateId": candidate_id,
        "engagementScore": round(min(1.0, engagement_score), 3),
        "sentCount": sent_count,
        "replyCount": reply_count,
        "unreadNotifications": len(unread_notifications),
        "lastOutreachAt": outreach_rows[0].last_contacted_at.isoformat() if outreach_rows and outreach_rows[0].last_contacted_at else None,
        "momentum": momentum,
        "atsStatus": profile.ats_status if profile else None,
    }


def get_interview_intelligence(*, db: Session, job_id: str, candidate_id: str) -> dict[str, Any]:
    evaluations = InterviewEvaluationRepository(db).list_for_candidate(job_id=job_id, candidate_id=candidate_id, limit=20)
    if not evaluations:
        return {
            "jobId": job_id,
            "candidateId": candidate_id,
            "interviewQualityScore": 0.0,
            "consistencyScore": 0.0,
            "competencyTrend": {},
            "recommendationSignal": "unknown",
            "evaluationCount": 0,
        }

    competency_totals: defaultdict[str, list[float]] = defaultdict(list)
    recommendation_counts: Counter[str] = Counter()
    quality_scores: list[float] = []
    for evaluation in evaluations:
        scores = evaluation.competency_scores or {}
        for key, value in scores.items():
            try:
                competency_totals[str(key)].append(float(value))
            except (TypeError, ValueError):
                continue
        recommendation_counts[_normalize_status(evaluation.recommendation)] += 1
        avg_score = sum(float(v) for v in scores.values() if isinstance(v, (int, float))) / max(1, len([v for v in scores.values() if isinstance(v, (int, float))]))
        quality_scores.append(avg_score)

    trend = {key: round(sum(values) / len(values), 3) for key, values in competency_totals.items() if values}
    consistency_score = 1.0
    if len(quality_scores) > 1:
        mean_quality = sum(quality_scores) / len(quality_scores)
        variance = sum((score - mean_quality) ** 2 for score in quality_scores) / len(quality_scores)
        consistency_score = max(0.0, min(1.0, 1.0 - min(1.0, variance)))
    dominant_recommendation = recommendation_counts.most_common(1)[0][0] if recommendation_counts else "unknown"
    interview_quality = max(0.0, min(1.0, sum(quality_scores) / len(quality_scores) if quality_scores else 0.0))
    return {
        "jobId": job_id,
        "candidateId": candidate_id,
        "interviewQualityScore": round(interview_quality, 3),
        "consistencyScore": round(consistency_score, 3),
        "competencyTrend": trend,
        "recommendationSignal": dominant_recommendation,
        "evaluationCount": len(evaluations),
    }


def suggest_interview_slots(*, db: Session, job_id: str, candidate_id: str, timezone_name: str = "UTC", days_ahead: int = 7) -> list[dict[str, Any]]:
    sessions = InterviewSessionRepository(db)
    candidate_session = sessions.get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
    existing_slots = [
        row.scheduled_at.astimezone(timezone.utc)
        for row in [candidate_session] if row and row.scheduled_at
    ]
    now = _utcnow()
    suggestions: list[dict[str, Any]] = []
    cursor = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    end = now + timedelta(days=max(1, days_ahead))
    while cursor <= end and len(suggestions) < 5:
        weekday = cursor.weekday()
        hour = cursor.hour
        if weekday < 5 and 9 <= hour <= 16:
            conflict = any(abs((cursor - slot).total_seconds()) < 3600 for slot in existing_slots)
            if not conflict:
                suggestions.append(
                    {
                        "scheduledAt": cursor.isoformat(),
                        "timezone": timezone_name,
                        "label": cursor.astimezone(timezone.utc).strftime("%a %b %d, %H:%M UTC"),
                        "conflict": False,
                    }
                )
        cursor += timedelta(hours=1)
    return suggestions


def get_calendar_intelligence(*, db: Session, job_id: str, candidate_id: str, timezone_name: str = "UTC") -> dict[str, Any]:
    return {
        "jobId": job_id,
        "candidateId": candidate_id,
        "timezone": timezone_name,
        "slotSuggestions": suggest_interview_slots(db=db, job_id=job_id, candidate_id=candidate_id, timezone_name=timezone_name),
        "conflictDetected": False,
        "providerDepth": {
            "googleCalendar": False,
            "outlookCalendar": False,
            "availabilityStored": False,
        },
    }


def detect_operational_anomalies(*, db: Session, job_id: str | None = None) -> list[dict[str, Any]]:
    profiles = CandidateProfileRepository(db).list_for_job(job_id) if job_id else []
    automation_rows = AutomationJobRepository(db).list_recent(limit=100)
    tasks = RecruiterTaskRepository(db).list_for_job(job_id, status="open", limit=100) if job_id else []
    interviews = InterviewSessionRepository(db)
    anomalies: list[dict[str, Any]] = []
    now = _utcnow()

    for profile in profiles:
        status = _normalize_status(profile.ats_status)
        if status in {"outreach_sent"} and profile.ats_status_updated_at and (now - profile.ats_status_updated_at) > timedelta(days=14):
            anomalies.append(
                {
                    "type": "stuck_candidate",
                    "jobId": profile.job_id,
                    "candidateId": profile.candidate_id,
                    "status": status,
                    "ageDays": int((now - profile.ats_status_updated_at).days),
                }
            )
        if status == "interview_scheduled" and profile.ats_status_updated_at and (now - profile.ats_status_updated_at) > timedelta(days=7):
            anomalies.append(
                {
                    "type": "stale_interview",
                    "jobId": profile.job_id,
                    "candidateId": profile.candidate_id,
                    "status": status,
                    "ageDays": int((now - profile.ats_status_updated_at).days),
                }
            )

        session = interviews.get_by_job_and_candidate(job_id=profile.job_id, candidate_id=profile.candidate_id)
        if session:
            scheduling_metadata = dict(session.scheduling_metadata or {})
            if (session.status or "").strip().lower() == "interview_scheduled" and not session.scheduled_at:
                anomalies.append(
                    {
                        "type": "stale_booking_state",
                        "jobId": profile.job_id,
                        "candidateId": profile.candidate_id,
                        "token": session.token,
                        "stage": scheduling_metadata.get("stageName") or session.stage,
                    }
                )
            if not (scheduling_metadata.get("workflowToken") or scheduling_metadata.get("workflow_token")):
                anomalies.append(
                    {
                        "type": "missing_workflow_token_linkage",
                        "jobId": profile.job_id,
                        "candidateId": profile.candidate_id,
                        "token": session.token,
                        "stage": scheduling_metadata.get("stageName") or session.stage,
                    }
                )
            stage_name = str(scheduling_metadata.get("stageName") or "").strip().lower()
            if stage_name and stage_name not in {"recruiter_screen", "technical_round", "hiring_manager_round", "final_round", "offer_stage", "placed"}:
                anomalies.append(
                    {
                        "type": "unknown_interview_stage",
                        "jobId": profile.job_id,
                        "candidateId": profile.candidate_id,
                        "stage": stage_name,
                        "token": session.token,
                    }
                )
            if session.status == "pending" and session.created_at and (now - session.created_at) > timedelta(hours=24):
                anomalies.append(
                    {
                        "type": "stale_interview_invite",
                        "jobId": profile.job_id,
                        "candidateId": profile.candidate_id,
                        "token": session.token,
                        "ageHours": int((now - session.created_at).total_seconds() // 3600),
                    }
                )

    failed_jobs = [row for row in automation_rows if _normalize_status(row.status) in {"failed", "retryable"}]
    for row in failed_jobs[:20]:
        anomalies.append(
            {
                "type": "automation_failure",
                "jobId": row.job_id,
                "candidateId": row.candidate_id,
                "automationType": row.automation_type,
                "status": row.status,
                "attemptCount": row.attempt_count,
                "lastError": row.last_error,
            }
        )

    if tasks:
        overdue_tasks = [task for task in tasks if task.due_at and task.due_at < now]
        for task in overdue_tasks[:20]:
            anomalies.append(
                {
                    "type": "overdue_task",
                    "jobId": task.job_id,
                    "candidateId": task.candidate_id,
                    "title": task.title,
                    "dueAt": task.due_at.isoformat(),
                }
            )

    return anomalies[:50]


def get_interview_stage_progression(*, db: Session, job_id: str, candidate_id: str) -> dict[str, Any]:
    session = InterviewSessionRepository(db).get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
    if not session:
        return {"jobId": job_id, "candidateId": candidate_id, "stage": None, "progression": []}
    progression = [
        {"stage": "recruiter_screen", "label": "Recruiter screen"},
        {"stage": "technical_round", "label": "Technical round"},
        {"stage": "hiring_manager_round", "label": "Hiring manager round"},
        {"stage": "final_round", "label": "Final round"},
        {"stage": "offer_sent", "label": "Offer stage"},
        {"stage": "hired", "label": "Placement"},
        {"stage": "no_show", "label": "No-show"},
        {"stage": "withdrawn", "label": "Withdrawn"},
    ]
    current_stage = str((dict(session.scheduling_metadata or {}).get("stageName") or session.stage or session.status or "")).strip().lower()
    current_index = next((index for index, item in enumerate(progression) if item["stage"] == current_stage), 0)
    return {
        "jobId": job_id,
        "candidateId": candidate_id,
        "stage": current_stage,
        "progression": [
            {**item, "active": item["stage"] == current_stage, "completed": index < current_index}
            for item in progression
        ],
    }


def get_recruiter_coordination_intelligence(*, db: Session, job_id: str) -> dict[str, Any]:
    tasks = RecruiterTaskRepository(db).list_for_job(job_id, status="open", limit=100)
    notes = RecruiterNoteRepository(db).list_for_job(job_id, limit=100)
    sessions = OrchestrationSessionRepository(db)
    job_sessions = sessions.get_by_job(job_id)
    recruiter_slack_user_id = getattr(job_sessions, "slack_user_id", "") if job_sessions else ""
    workloads = len(tasks)
    return {
        "jobId": job_id,
        "recruiterSlackUserId": recruiter_slack_user_id,
        "openTaskCount": workloads,
        "openNotesCount": len(notes),
        "coordinationMode": "slack_dm" if recruiter_slack_user_id else "dashboard",
        "recommendedAction": "reduce_task_load" if workloads > 10 else "normal",
    }


def get_candidate_reactivation_intelligence(*, db: Session, job_id: str) -> list[dict[str, Any]]:
    profiles = CandidateProfileRepository(db).list_for_job(job_id)
    ranked: list[dict[str, Any]] = []
    for profile in profiles:
        if _normalize_status(profile.ats_status) != "archived":
            continue
        engagement = get_candidate_engagement_intelligence(db=db, job_id=job_id, candidate_id=profile.candidate_id)
        interview = get_interview_intelligence(db=db, job_id=job_id, candidate_id=profile.candidate_id)
        score = round(
            min(
                1.0,
                0.5 * engagement["engagementScore"] + 0.3 * interview["interviewQualityScore"] + 0.2 * float(profile.fit_score or 0.0) / 5.0,
            ),
            3,
        )
        ranked.append(
            {
                "jobId": job_id,
                "candidateId": profile.candidate_id,
                "name": profile.name,
                "score": score,
                "engagement": engagement,
                "interview": interview,
                "lastRefreshedAt": profile.last_refreshed_at.isoformat() if profile.last_refreshed_at else None,
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[:20]


def get_operational_intelligence_snapshot(*, db: Session, job_id: str | None = None, candidate_id: str | None = None) -> dict[str, Any]:
    resolved_job_id = job_id or ""
    resolved_candidate_id = candidate_id or ""
    snapshot: dict[str, Any] = {
        "jobId": resolved_job_id or None,
        "candidateId": resolved_candidate_id or None,
        "anomalies": detect_operational_anomalies(db=db, job_id=resolved_job_id or None),
    }
    if resolved_job_id and resolved_candidate_id:
        snapshot["engagement"] = get_candidate_engagement_intelligence(db=db, job_id=resolved_job_id, candidate_id=resolved_candidate_id)
        snapshot["interview"] = get_interview_intelligence(db=db, job_id=resolved_job_id, candidate_id=resolved_candidate_id)
        snapshot["calendar"] = get_calendar_intelligence(db=db, job_id=resolved_job_id, candidate_id=resolved_candidate_id)
        snapshot["progression"] = get_interview_stage_progression(db=db, job_id=resolved_job_id, candidate_id=resolved_candidate_id)
    if resolved_job_id:
        snapshot["coordination"] = get_recruiter_coordination_intelligence(db=db, job_id=resolved_job_id)
        snapshot["reactivation"] = get_candidate_reactivation_intelligence(db=db, job_id=resolved_job_id)
    return snapshot
