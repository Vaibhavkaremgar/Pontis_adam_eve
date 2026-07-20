from __future__ import annotations

import asyncio
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
    OrchestrationSessionRepository,
    OutreachEventRepository,
    RecruiterNoteRepository,
    RecruiterTaskRepository,
)
from app.services.email_service import send_email
from app.services.ats_lifecycle_service import transition_candidate_ats_state
from app.services.interview_session_service import mark_interview_no_show, _session_stage_name
from app.services.outreach_service import run_followup_cycle
from app.services.audit_service import record_audit_event
from app.services.metrics_service import log_metric
from app.services.notification_intelligence_service import route_recruiter_notification
from app.services.sourcing.apify_enrichment_service import enrich_selected_candidate
from app.services.sourcing.outreach_trigger_service import trigger_outreach_after_enrichment
from app.services.slack_integration import post_slack_message
from app.services.slack_tenant_service import SlackCompanyResolver

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _next_business_day_same_time(value: datetime) -> datetime:
    candidate = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    candidate = candidate + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate = candidate + timedelta(days=1)
    return candidate


def _metadata_map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


async def _post_recruiter_slack_message(*, db: Session, job_id: str, text: str) -> bool:
    session = OrchestrationSessionRepository(db).get_by_job(job_id)
    if not session:
        return False
    slack_context = dict(getattr(session, "slack_context", {}) or {})
    channel_id = _normalize_text(slack_context.get("channelId") or slack_context.get("channel_id") or "")
    company_id = _normalize_text(slack_context.get("companyId") or slack_context.get("company_id") or getattr(session, "company_id", "") or "")
    if not channel_id or not company_id:
        return False
    try:
        bot_token = SlackCompanyResolver(db).resolve_bot_token(company_id=company_id)
    except Exception as exc:
        logger.warning("automation_slack_token_failed job_id=%s error=%s", job_id, str(exc), exc_info=exc)
        return False
    if not bot_token:
        return False
    try:
        await post_slack_message(channel_id=channel_id, text=text, bot_token=bot_token)
        return True
    except Exception as exc:
        logger.warning("automation_slack_post_failed job_id=%s error=%s", job_id, str(exc), exc_info=exc)
        return False


def _second_round_outcome_notification_text(*, profile: Any, job: Any, final: bool) -> str:
    candidate_name = _normalize_text(getattr(profile, "name", "") or "")
    job_title = _normalize_text(getattr(job, "title", "") or "")
    base = f"Any update on {candidate_name or 'the candidate'} for {job_title or 'this role'}? Please confirm outcome"
    return f"Final reminder: {base}" if final else base


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
    return {"id": row.id, "automationKey": row.automation_key, "status": row.status, "scheduledAt": _isoformat(row.scheduled_at)}


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
            if session.status == "interview_scheduled" and scheduled_at:
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

                if _session_stage_name(session) == "second_round_scheduled":
                    second_round_followups = [
                        (5, "second_round_outcome_nudge"),
                        (10, "second_round_outcome_final"),
                    ]
                    for delay_days, automation_type in second_round_followups:
                        due_at = scheduled_at + timedelta(days=delay_days)
                        if due_at <= now:
                            key = _key("second-round-outcome", job.id, session.candidate_id, session.token, str(delay_days))
                            automation_repo.upsert(
                                automation_key=key,
                                automation_type=automation_type,
                                job_id=job.id,
                                candidate_id=session.candidate_id,
                                scheduled_at=due_at,
                                payload={
                                    "token": session.token,
                                    "scheduledAt": scheduled_at.isoformat(),
                                    "delayDays": delay_days,
                                    "stageName": "second_round_scheduled",
                                },
                            )
                            created += 1

        for profile in profile_repo.list_for_job(job.id):
            profile_status = (profile.ats_status or profile.candidate_status or "").strip().lower()
            if profile_status in {"reviewed", "sourced"} and profile.ats_status_updated_at and (now - profile.ats_status_updated_at) >= timedelta(hours=48):
                key = _key("no-swipe", job.id, profile.candidate_id, profile.ats_status_updated_at.isoformat())
                automation_repo.upsert(
                    automation_key=key,
                    automation_type="recruiter_reminder",
                    job_id=job.id,
                    candidate_id=profile.candidate_id,
                    scheduled_at=profile.ats_status_updated_at + timedelta(hours=48),
                    payload={
                        "reason": "no_swipe_48h",
                        "candidateName": profile.name or profile.candidate_id,
                        "candidateRole": profile.current_role or "",
                    },
                )
                created += 1
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
    payload = _metadata_map(row.automation_payload)
    title = "Recruiter reminder"
    body = "There is an active recruiter task waiting."
    if str(payload.get("reason") or "").strip() == "no_swipe_48h" and profile:
        body = f"You haven't reviewed {profile.name or profile.candidate_id} for {profile.current_role or 'this role'} yet"
        title = "Candidate review reminder"
    elif profile:
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
        to_status="reviewed",
        source="automation",
        reason="candidate_reactivation",
        metadata={"automationJobId": row.id, **_metadata_map(row.automation_payload)},
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

    transition_candidate_ats_state(
        db=db,
        job_id=row.job_id or "",
        candidate_id=row.candidate_id or "",
        to_status="enriching",
        source="candidate_enrichment",
        reason="apify_enrichment_started",
        metadata={
            "selectionSessionId": str((row.automation_payload or {}).get("selectionSessionId") or ""),
            "automationJobId": str(row.id),
            "sourceType": str((row.automation_payload or {}).get("sourceType") or "linkedin_xray"),
        },
    )

    enrichment = enrich_selected_candidate(
        db=db,
        job_id=row.job_id or "",
        candidate_id=row.candidate_id or "",
        source_type=str((row.automation_payload or {}).get("sourceType") or "linkedin_xray"),
        linkedin_url=str((row.automation_payload or {}).get("linkedinUrl") or (row.automation_payload or {}).get("linkedin_url") or ""),
        workflow_token=str((row.automation_payload or {}).get("workflowToken") or ""),
        selection_session_id=str((row.automation_payload or {}).get("selectionSessionId") or ""),
        automation_job_id=str(row.id),
    )

    status = str(enrichment.get("status") or "").strip().lower()
    outreach_result = trigger_outreach_after_enrichment(
        db=db,
        job_id=row.job_id or "",
        candidate_id=row.candidate_id or "",
        enrichment_result=enrichment,
        selection_session_id=str((row.automation_payload or {}).get("selectionSessionId") or ""),
        automation_job_id=str(row.id),
        source_type=str((row.automation_payload or {}).get("sourceType") or "linkedin_xray"),
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
    session_repo = InterviewSessionRepository(db)
    session = session_repo.get_by_token(token)
    if not session:
        return {"status": "skipped", "reason": "session_missing"}

    scheduled_at = session.scheduled_at or _utcnow()
    metadata = _metadata_map(session.scheduling_metadata)
    already_rescheduled = bool(metadata.get("autoNoShowRescheduled"))
    profile = CandidateProfileRepository(db).get(job_id=row.job_id or "", candidate_id=row.candidate_id or "")
    result = mark_interview_no_show(db=db, token=token, reason=str((row.automation_payload or {}).get("reason") or "scheduled_window_elapsed"))
    recruiter_id = JobRepository(db).get_recruiter_id(row.job_id or "")
    recruiter_name = (profile.name or profile.candidate_id) if profile else (row.candidate_id or "")

    if already_rescheduled:
        RecruiterTaskRepository(db).create(
            job_id=row.job_id or "",
            candidate_id=row.candidate_id,
            recruiter_id=recruiter_id,
            title="Interview no-show recovery",
            body=f"{recruiter_name} missed the interview again. No further reschedule.",
            priority="high",
            due_at=_utcnow(),
            metadata={"automationJobId": row.id, **_metadata_map(row.automation_payload)},
        )
        route_recruiter_notification(
            db=db,
            job_id=row.job_id or "",
            candidate_id=row.candidate_id,
            notification_key=_key("automation", "interview_no_show_repeat", row.job_id or "", row.candidate_id or "", row.automation_key),
            notification_type="interview_no_show",
            title="Interview no-show detected again",
            body=f"{recruiter_name} no-show again. No further reschedule.",
            metadata={"automationJobId": row.id, **_metadata_map(row.automation_payload)},
        )
        return {"status": "handled", "result": result, "rescheduled": False}

    new_slot = _next_business_day_same_time(scheduled_at)
    try:
        from app.services.interview_session_service import reschedule_interview_session

        reschedule_result = reschedule_interview_session(
            db=db,
            token=token,
            scheduled_at=new_slot.isoformat(),
            reason="auto_no_show_recovery",
        )
        fresh_session = session_repo.get_by_token(token)
        if fresh_session:
            fresh_session.scheduling_metadata = {
                **_metadata_map(fresh_session.scheduling_metadata),
                "autoNoShowRescheduled": True,
                "autoNoShowRescheduledAt": _utcnow().isoformat(),
                "autoNoShowOriginalAt": scheduled_at.isoformat() if scheduled_at else "",
            }
            db.flush()

        if fresh_session and str(getattr(fresh_session, "email", "") or "").strip():
            try:
                send_email(
                    to_email=str(fresh_session.email).strip(),
                    subject="Interview rescheduled",
                    body=f"Your interview has been rescheduled to {new_slot.isoformat()}.",
                    text=f"Your interview has been rescheduled to {new_slot.isoformat()}.",
                )
            except Exception as exc:
                logger.warning(
                    "interview_no_show_reschedule_email_failed job_id=%s candidate_id=%s error=%s",
                    row.job_id,
                    row.candidate_id,
                    str(exc),
                    exc_info=exc,
                )

        if profile:
            RecruiterTaskRepository(db).create(
                job_id=row.job_id or "",
                candidate_id=row.candidate_id,
                recruiter_id=recruiter_id,
                title="Interview no-show recovery",
                body=f"{profile.name or profile.candidate_id} missed the interview. Review reschedule options.",
                priority="high",
                due_at=_utcnow(),
                metadata={"automationJobId": row.id, **_metadata_map(row.automation_payload)},
            )
        route_recruiter_notification(
            db=db,
            job_id=row.job_id or "",
            candidate_id=row.candidate_id,
            notification_key=_key("automation", "interview_no_show", row.job_id or "", row.candidate_id or "", row.automation_key),
            notification_type="interview_no_show",
            title="Interview no-show recovered",
            body=f"{recruiter_name} no-show. Rescheduled once for {new_slot.isoformat()}. Will flag if missed again",
            metadata={"automationJobId": row.id, "rescheduledAt": new_slot.isoformat(), **_metadata_map(row.automation_payload)},
        )
        return {"status": "handled", "result": result, "rescheduled": True, "rescheduleResult": reschedule_result}
    except Exception as exc:
        logger.warning(
            "interview_no_show_reschedule_failed job_id=%s candidate_id=%s error=%s",
            row.job_id,
            row.candidate_id,
            str(exc),
            exc_info=exc,
        )
        if profile:
            RecruiterTaskRepository(db).create(
                job_id=row.job_id or "",
                candidate_id=row.candidate_id,
                recruiter_id=recruiter_id,
                title="Interview no-show recovery",
                body=f"{profile.name or profile.candidate_id} missed the interview. Review reschedule options.",
                priority="high",
                due_at=_utcnow(),
                metadata={"automationJobId": row.id, **_metadata_map(row.automation_payload)},
            )
        route_recruiter_notification(
            db=db,
            job_id=row.job_id or "",
            candidate_id=row.candidate_id,
            notification_key=_key("automation", "interview_no_show_failed", row.job_id or "", row.candidate_id or "", row.automation_key),
            notification_type="interview_no_show",
            title="Interview no-show detected",
            body=f"{recruiter_name} no-show. Reschedule failed; please review manually.",
            metadata={"automationJobId": row.id, **_metadata_map(row.automation_payload)},
        )
        return {"status": "handled", "result": result, "rescheduled": False}


def _handle_second_round_outcome_prompt(db: Session, row, *, final: bool = False) -> dict[str, Any]:
    job = JobRepository(db).get(row.job_id or "")
    profile = CandidateProfileRepository(db).get(job_id=row.job_id or "", candidate_id=row.candidate_id or "")
    if not job or not profile:
        return {"status": "skipped", "reason": "candidate_or_job_missing"}

    current_status = _normalize_text(profile.ats_status or profile.candidate_status).lower()
    if current_status != "second_round_scheduled":
        return {"status": "skipped", "reason": "signal_present"}

    message = _second_round_outcome_notification_text(profile=profile, job=job, final=final)
    step_name = "second_round_outcome_prompt"
    try:
        posted = asyncio.run(_post_recruiter_slack_message(db=db, job_id=row.job_id or "", text=message))
    except RuntimeError as exc:
        logger.error("automation_failed step=%s error=%s", step_name, str(exc))
        posted = False
    except Exception as exc:
        logger.error("automation_failed step=%s error=%s", step_name, str(exc))
        posted = False
    if not posted:
        return {"status": "skipped", "reason": "slack_unavailable"}

    if final:
        profile.ats_metadata = {
            **dict(getattr(profile, "ats_metadata", {}) or {}),
            "outcomeStatus": "outcome_pending",
            "outcomePending": True,
            "outcomePendingAt": _utcnow().isoformat(),
        }
        profile.ats_status_reason = "outcome_pending"
        profile.ats_status_updated_at = _utcnow()
        db.flush()

    return {"status": "notified", "final": final}


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
            elif row.automation_type == "second_round_outcome_nudge":
                outcome = _handle_second_round_outcome_prompt(db, row, final=False)
            elif row.automation_type == "second_round_outcome_final":
                outcome = _handle_second_round_outcome_prompt(db, row, final=True)
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
                "scheduledAt": _isoformat(row.scheduled_at),
                "startedAt": _isoformat(row.started_at),
                "completedAt": _isoformat(row.completed_at),
                "attemptCount": row.attempt_count,
                "maxAttempts": row.max_attempts,
                "lastError": row.last_error,
                "payload": _metadata_map(row.automation_payload),
                "createdAt": _isoformat(row.created_at),
                "updatedAt": _isoformat(row.updated_at),
            }
            for row in rows
    ]
