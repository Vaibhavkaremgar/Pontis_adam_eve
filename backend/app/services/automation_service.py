from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.repositories import (
    AutomationJobRepository,
    CandidateProfileRepository,
    InterviewEvaluationRepository,
    InterviewSessionRepository,
    JobRepository,
    NotificationEventRepository,
    OutreachEventRepository,
    RecruiterNoteRepository,
    RecruiterTaskRepository,
)
from app.services.ats_lifecycle_service import transition_candidate_ats_state
from app.services.interview_session_service import mark_interview_no_show
from app.services.outreach_service import run_followup_cycle
from app.services.audit_service import record_audit_event
from app.services.apollo_enrichment_service import enrich_candidate_with_apollo
from app.services.metrics_service import log_metric
from app.services.notification_intelligence_service import route_recruiter_notification
from app.services.outreach_service import process_outreach

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _key(*parts: str) -> str:
    material = "::".join(part.strip() for part in parts if part is not None)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _upsert_notification(
    *,
    db: Session,
    notification_key: str,
    job_id: str | None,
    candidate_id: str | None,
    recipient_type: str,
    recipient: str,
    channel: str,
    title: str,
    body: str,
    notification_type: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    NotificationEventRepository(db).upsert(
        notification_key=notification_key,
        job_id=job_id,
        candidate_id=candidate_id,
        recipient_type=recipient_type,
        recipient=recipient,
        channel=channel,
        title=title,
        body=body,
        status="delivered",
        notification_type=notification_type,
        notification_metadata=dict(metadata or {}),
        delivery_reference=notification_key,
    )


def schedule_automation_job(
    *,
    db: Session,
    automation_type: str,
    job_id: str | None = None,
    candidate_id: str | None = None,
    run_at: datetime | None = None,
    payload: dict[str, Any] | None = None,
    automation_key: str | None = None,
) -> dict[str, Any]:
    key = automation_key or _key(automation_type, job_id or "", candidate_id or "", (run_at or _utcnow()).isoformat())
    row = AutomationJobRepository(db).upsert(
        automation_key=key,
        automation_type=automation_type,
        job_id=job_id,
        candidate_id=candidate_id,
        scheduled_at=run_at or _utcnow(),
        payload=payload or {},
    )
    db.commit()
    return {"id": row.id, "automationKey": row.automation_key, "status": row.status, "scheduledAt": row.scheduled_at.isoformat()}


def seed_automation_jobs(*, db: Session, job_id: str | None = None, limit: int = 25) -> dict[str, int]:
    now = _utcnow()
    job_repo = JobRepository(db)
    outreach_repo = OutreachEventRepository(db)
    interview_repo = InterviewSessionRepository(db)
    profile_repo = CandidateProfileRepository(db)
    automation_repo = AutomationJobRepository(db)

    scanned_jobs = job_repo.list_recent(limit=limit) if not job_id else [job_repo.get(job_id)] if job_repo.get(job_id) else []
    created = 0
    for job in scanned_jobs:
        if not job:
            continue
        for outreach in outreach_repo.list_for_job(job.id):
            candidate_id = str(outreach.candidate_id or "").strip()
            if not candidate_id:
                continue
            if outreach.next_follow_up_at and outreach.next_follow_up_at <= now and (outreach.status or "").strip().lower() in {"sent", "delivered", "opened"}:
                key = _key("followup", job.id, candidate_id, str(int(outreach.follow_up_count or 0) + 1))
                automation_repo.upsert(
                    automation_key=key,
                    automation_type="outreach_followup",
                    job_id=job.id,
                    candidate_id=candidate_id,
                    scheduled_at=outreach.next_follow_up_at,
                    payload={"outreachEventId": outreach.id, "followUpCount": int(outreach.follow_up_count or 0)},
                )
                created += 1
            reply_state = str(getattr(outreach, "reply_state", "") or "").strip().lower()
            if reply_state in {"asked_to_follow_up_later", "out_of_office"} and outreach.next_follow_up_at:
                delay_key = str(outreach.next_follow_up_at.isoformat())
                key = _key("reengagement", job.id, candidate_id, delay_key)
                automation_repo.upsert(
                    automation_key=key,
                    automation_type="recruiter_reminder",
                    job_id=job.id,
                    candidate_id=candidate_id,
                    scheduled_at=outreach.next_follow_up_at,
                    payload={
                        "outreachEventId": outreach.id,
                        "replyState": reply_state,
                        "reason": "candidate_reconnect_requested" if reply_state == "asked_to_follow_up_later" else "out_of_office_followup",
                    },
                )
                created += 1

        for session in [
            row
            for row in [
                interview_repo.get_by_job_and_candidate(job_id=job.id, candidate_id=candidate.id)
                for candidate in profile_repo.list_for_job(job.id)
            ]
            if row
        ]:
            scheduled_at = getattr(session, "scheduled_at", None)
            if session.status == "booked" and scheduled_at:
                reminder_windows = [timedelta(hours=24), timedelta(hours=1)]
                for window in reminder_windows:
                    reminder_at = scheduled_at - window
                    if reminder_at <= now:
                        key = _key("interview-reminder", job.id, session.candidate_id, session.token, str(int(window.total_seconds())))
                        automation_repo.upsert(
                            automation_key=key,
                            automation_type="interview_reminder",
                            job_id=job.id,
                            candidate_id=session.candidate_id,
                            scheduled_at=reminder_at,
                            payload={"token": session.token, "scheduledAt": scheduled_at.isoformat(), "windowHours": int(window.total_seconds() // 3600)},
                        )
                        created += 1

                no_show_grace = scheduled_at + timedelta(minutes=90)
                if no_show_grace <= now:
                    key = _key("interview-no-show", job.id, session.candidate_id, session.token)
                    automation_repo.upsert(
                        automation_key=key,
                        automation_type="interview_no_show",
                        job_id=job.id,
                        candidate_id=session.candidate_id,
                        scheduled_at=no_show_grace,
                        payload={"token": session.token, "scheduledAt": scheduled_at.isoformat(), "reason": "scheduled_window_elapsed"},
                    )
                    created += 1

        for profile in profile_repo.list_for_job(job.id):
            if (profile.ats_status or "").strip().lower() == "archived" and profile.last_refreshed_at and (now - profile.last_refreshed_at) >= timedelta(days=30):
                key = _key("reactivation", job.id, profile.candidate_id, profile.last_refreshed_at.isoformat())
                automation_repo.upsert(
                    automation_key=key,
                    automation_type="candidate_reactivation",
                    job_id=job.id,
                    candidate_id=profile.candidate_id,
                    scheduled_at=now,
                    payload={"fitScore": float(profile.fit_score or 0.0), "lastRefreshedAt": profile.last_refreshed_at.isoformat()},
                )
                created += 1

    db.commit()
    return {"created": created, "scannedJobs": len(scanned_jobs)}


def _handle_outreach_followup(db: Session, row) -> dict[str, Any]:
    result = run_followup_cycle(db)
    route_recruiter_notification(
        db=db,
        job_id=row.job_id or "",
        candidate_id=row.candidate_id,
        notification_key=_key("automation", "outreach_followup", row.job_id or "", row.candidate_id or "", str(row.attempt_count)),
        notification_type="outreach_followup",
        title="Follow-up cycle executed",
        body=f"Outreach follow-up cycle ran for {row.candidate_id}",
        metadata=result,
    )
    return {"result": result}


def _handle_recruiter_reminder(db: Session, row) -> dict[str, Any]:
    profile = CandidateProfileRepository(db).get(job_id=row.job_id or "", candidate_id=row.candidate_id or "")
    title = "Recruiter reminder"
    body = "There is an active recruiter task waiting."
    if profile:
        body = f"{profile.name or profile.candidate_id} is waiting for a recruiter action."
    RecruiterTaskRepository(db).create(
        job_id=row.job_id or "",
        candidate_id=row.candidate_id,
        title=title,
        body=body,
        priority="high",
        due_at=_utcnow(),
        metadata=row.automation_payload,
    )
    route_recruiter_notification(
        db=db,
        job_id=row.job_id or "",
        candidate_id=row.candidate_id,
        notification_key=_key("automation", "recruiter_reminder", row.job_id or "", row.candidate_id or "", str(row.attempt_count)),
        notification_type="recruiter_reminder",
        title=title,
        body=body,
        metadata=row.automation_payload,
    )
    return {"status": "queued"}


def _handle_candidate_reactivation(db: Session, row) -> dict[str, Any]:
    profile = CandidateProfileRepository(db).get(job_id=row.job_id or "", candidate_id=row.candidate_id or "")
    if not profile:
        return {"status": "skipped", "reason": "candidate_missing"}
    transition_candidate_ats_state(
        db=db,
        job_id=row.job_id or "",
        candidate_id=row.candidate_id or "",
        to_status="review_pending",
        source="automation",
        reason="candidate_reactivation",
        metadata={"automationJobId": row.id, **dict(row.automation_payload or {})},
    )
    RecruiterNoteRepository(db).create(
        job_id=row.job_id or "",
        candidate_id=row.candidate_id,
        recruiter_id=JobRepository(db).get_recruiter_id(row.job_id or ""),
        note_type="reactivation",
        body=f"Candidate resurfaced for review: {profile.name or profile.candidate_id}",
        metadata={"automationJobId": row.id},
    )
    route_recruiter_notification(
        db=db,
        job_id=row.job_id or "",
        candidate_id=row.candidate_id,
        notification_key=_key("automation", "candidate_reactivation", row.job_id or "", row.candidate_id or "", str(row.attempt_count)),
        notification_type="candidate_reactivation",
        title="Candidate reactivated",
        body=f"{profile.name or profile.candidate_id} resurfaced for review.",
        metadata=row.automation_payload,
    )
    return {"status": "reactivated"}


def _handle_candidate_enrichment(db: Session, row) -> dict[str, Any]:
    profile = CandidateProfileRepository(db).get(job_id=row.job_id or "", candidate_id=row.candidate_id or "")
    if not profile:
        return {"status": "skipped", "reason": "candidate_missing"}
    job = JobRepository(db).get(row.job_id or "")
    if not job:
        return {"status": "skipped", "reason": "job_missing"}

    enrichment = enrich_candidate_with_apollo(
        db=db,
        job_id=row.job_id or "",
        candidate_id=row.candidate_id or "",
        source_type=str((row.automation_payload or {}).get("sourceType") or "adam"),
        workflow_token=str((row.automation_payload or {}).get("workflowToken") or ""),
        selection_session_id=str((row.automation_payload or {}).get("selectionSessionId") or ""),
        automation_job_id=str(row.id),
    )

    status = str(enrichment.get("status") or "").strip().lower()
    should_outreach = bool(enrichment.get("shouldOutreach"))
    outreach_result: dict[str, Any] = {}
    if should_outreach:
        outreach_result = process_outreach(
            db=db,
            job_id=row.job_id or "",
            selected_candidates=[row.candidate_id or ""],
            custom_body="",
            recipient_email=str(enrichment.get("contactEmail") or ""),
        )
    return {"status": status or "completed", "enrichment": enrichment, "outreach": outreach_result}


def _handle_interview_reminder(db: Session, row) -> dict[str, Any]:
    profile = CandidateProfileRepository(db).get(job_id=row.job_id or "", candidate_id=row.candidate_id or "")
    reminder_body = "Upcoming interview reminder"
    if profile:
        reminder_body = f"Interview reminder for {profile.name or profile.candidate_id}"
    route_recruiter_notification(
        db=db,
        job_id=row.job_id or "",
        candidate_id=row.candidate_id,
        notification_key=_key("automation", "interview_reminder", row.job_id or "", row.candidate_id or "", row.automation_key),
        notification_type="interview_reminder",
        title="Interview reminder",
        body=reminder_body,
        metadata=row.automation_payload,
    )
    return {"status": "notified"}


def _handle_interview_no_show(db: Session, row) -> dict[str, Any]:
    token = str((row.automation_payload or {}).get("token") or "").strip()
    if not token:
        return {"status": "skipped", "reason": "missing_token"}
    result = mark_interview_no_show(db=db, token=token, reason=str((row.automation_payload or {}).get("reason") or "scheduled_window_elapsed"))
    profile = CandidateProfileRepository(db).get(job_id=row.job_id or "", candidate_id=row.candidate_id or "")
    if profile:
        RecruiterTaskRepository(db).create(
            job_id=row.job_id or "",
            candidate_id=row.candidate_id,
            recruiter_id=JobRepository(db).get_recruiter_id(row.job_id or ""),
            title="Interview no-show recovery",
            body=f"{profile.name or profile.candidate_id} missed the interview. Review reschedule options.",
            priority="high",
            due_at=_utcnow(),
            metadata={"automationJobId": row.id, **dict(row.automation_payload or {})},
        )
    route_recruiter_notification(
        db=db,
        job_id=row.job_id or "",
        candidate_id=row.candidate_id,
        notification_key=_key("automation", "interview_no_show", row.job_id or "", row.candidate_id or "", row.automation_key),
        notification_type="interview_no_show",
        title="Interview no-show recovered",
        body=f"No-show workflow executed for {row.candidate_id}",
        metadata={"automationJobId": row.id, **dict(row.automation_payload or {})},
    )
    return {"status": "handled", "result": result}


def _handle_inactivity_nudge(db: Session, row) -> dict[str, Any]:
    profile = CandidateProfileRepository(db).get(job_id=row.job_id or "", candidate_id=row.candidate_id or "")
    if not profile:
        return {"status": "skipped", "reason": "candidate_missing"}
    RecruiterTaskRepository(db).create(
        job_id=row.job_id or "",
        candidate_id=row.candidate_id,
        recruiter_id=JobRepository(db).get_recruiter_id(row.job_id or ""),
        title="Inactivity nudge",
        body=f"{profile.name or profile.candidate_id} has been inactive and may need follow-up.",
        priority="normal",
        due_at=_utcnow(),
        metadata=row.automation_payload,
    )
    return {"status": "queued"}


def run_automation_cycle(*, db: Session, scan_limit: int = 25) -> dict[str, Any]:
    seed_result = seed_automation_jobs(db=db, limit=scan_limit)
    automation_repo = AutomationJobRepository(db)
    due_jobs = automation_repo.list_due(limit=scan_limit)
    executed = 0
    failed = 0

    for row in due_jobs:
        try:
            automation_repo.mark_started(row)
            if row.automation_type == "outreach_followup":
                outcome = _handle_outreach_followup(db, row)
            elif row.automation_type == "recruiter_reminder":
                outcome = _handle_recruiter_reminder(db, row)
            elif row.automation_type == "candidate_reactivation":
                outcome = _handle_candidate_reactivation(db, row)
            elif row.automation_type == "candidate_enrichment":
                outcome = _handle_candidate_enrichment(db, row)
            elif row.automation_type == "interview_reminder":
                outcome = _handle_interview_reminder(db, row)
            elif row.automation_type == "interview_no_show":
                outcome = _handle_interview_no_show(db, row)
            else:
                outcome = _handle_inactivity_nudge(db, row)
            automation_repo.mark_completed(row)
            record_audit_event(
                db=db,
                actor_id=None,
                action="automation_executed",
                entity_type="automation_job",
                entity_id=row.id,
                metadata={"automationType": row.automation_type, "jobId": row.job_id, "candidateId": row.candidate_id, **dict(outcome or {})},
            )
            executed += 1
        except Exception as exc:
            automation_repo.mark_failed(row, error=str(exc))
            record_audit_event(
                db=db,
                actor_id=None,
                action="automation_failed",
                entity_type="automation_job",
                entity_id=row.id,
                metadata={"automationType": row.automation_type, "jobId": row.job_id, "candidateId": row.candidate_id, "error": str(exc)},
            )
            failed += 1
            logger.warning("automation_job_failed job_id=%s candidate_id=%s type=%s error=%s", row.job_id, row.candidate_id, row.automation_type, str(exc))

    db.commit()
    summary = {"seeded": seed_result.get("created", 0), "executed": executed, "failed": failed}
    log_metric("automation_cycle", **summary)
    return summary


def list_automation_jobs(*, db: Session, limit: int = 100) -> list[dict[str, Any]]:
    rows = AutomationJobRepository(db).list_recent(limit=limit)
    return [
        {
            "id": row.id,
            "jobId": row.job_id,
            "candidateId": row.candidate_id,
            "automationType": row.automation_type,
            "automationKey": row.automation_key,
            "status": row.status,
            "scheduledAt": row.scheduled_at.isoformat() if row.scheduled_at else None,
            "startedAt": row.started_at.isoformat() if row.started_at else None,
            "completedAt": row.completed_at.isoformat() if row.completed_at else None,
            "attemptCount": row.attempt_count,
            "maxAttempts": row.max_attempts,
            "lastError": row.last_error,
            "payload": dict(row.automation_payload or {}),
            "createdAt": row.created_at.isoformat(),
            "updatedAt": row.updated_at.isoformat(),
        }
        for row in rows
    ]
