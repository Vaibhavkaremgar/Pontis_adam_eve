from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import config_diagnostics
from app.db.repositories import (
    AutomationJobRepository,
    CandidateProfileRepository,
    InterviewEvaluationRepository,
    InterviewSessionRepository,
    JobRepository,
    NotificationEventRepository,
    NotificationWorkflowTokenRepository,
    OutreachEventRepository,
    RankingRunRepository,
    RecruiterNoteRepository,
    RecruiterTaskRepository,
)
from app.models.entities import AuditEventEntity, CandidateProfileEntity
from app.services.candidate_refresh_service import refresh_candidate
from app.services.embedding_registry_service import promote_embedding_version
from app.services.job_queue_service import (
    list_dead_letter_jobs,
    queue_depth_snapshot,
    queue_health_snapshot,
    replay_dead_letter_job,
)
from app.services.metrics_service import get_metrics_snapshot
from app.services.platform_event_stream import list_recent_platform_events, record_platform_event
from app.services.recruiter_preference_service import get_recruiter_learning_metrics, load_recruiter_preference_profile
from app.services.refresh_scheduler import scheduler_status
from app.services.qdrant_service import qdrant_health_snapshot
from app.services.llm_service import llm_health
from app.services.pdl_service import pdl_health_snapshot

logger = logging.getLogger(__name__)


def _metadata_map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def get_platform_diagnostics(db: Session) -> dict[str, Any]:
    return {
        "config": config_diagnostics(),
        "db": _db_health_snapshot(db),
        "queue": queue_health_snapshot(),
        "queueDepth": queue_depth_snapshot(),
        "llm": llm_health(),
        "pdl": pdl_health_snapshot(),
        "qdrant": qdrant_health_snapshot(),
        "scheduler": scheduler_status(),
        "metrics": get_metrics_snapshot(),
        "events": list_recent_platform_events(limit=25),
        "outreach": get_outreach_analytics(db),
        "enrichment": get_enrichment_health(db),
        "workflowTokens": get_workflow_token_health(db),
        "interviews": get_interview_health(db),
        "replay": {
            "deadLetters": len(list_dead_letter_jobs(limit=100)),
            "queueHealth": queue_health_snapshot(),
        },
    }


def _db_health_snapshot(db: Session) -> dict[str, Any]:
    try:
        db.execute(select(1))
        return {"status": "ok", "checked_at": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        return {"status": "down", "error": str(exc), "checked_at": datetime.now(timezone.utc).isoformat()}


def get_outreach_analytics(db: Session, job_id: str | None = None) -> dict[str, Any]:
    repo = OutreachEventRepository(db)
    if job_id:
        rows = repo.list_for_job(job_id)
    else:
        rows = repo.list_recent(limit=500)

    totals = {
        "queued": 0,
        "sending": 0,
        "sent": 0,
        "follow_up_sent": 0,
        "failed": 0,
        "replied": 0,
        "bounced": 0,
        "unsubscribed": 0,
        "opened": 0,
        "archived": 0,
    }
    reply_states: dict[str, int] = {}
    engagement_scores: list[float] = []
    reply_likelihood_scores: list[float] = []
    responsiveness_scores: list[float] = []
    sent_events = 0
    replied_events = 0
    opened_events = 0
    no_response_archived = 0
    for row in rows:
        status = (row.status or "").strip().lower()
        if status in totals:
            totals[status] += 1
        if status in {"sent", "follow_up_sent", "opened", "delivered"}:
            sent_events += 1
        if row.responded_at:
            replied_events += 1
        if getattr(row, "last_opened_at", None) or int(getattr(row, "open_count", 0) or 0) > 0:
            opened_events += 1
        reply_state = (getattr(row, "reply_state", "") or "").strip().lower()
        if reply_state:
            reply_states[reply_state] = reply_states.get(reply_state, 0) + 1
        if "bounce" in (row.last_error or "").lower():
            totals["bounced"] += 1
        if "unsubscribe" in (row.last_error or "").lower():
            totals["unsubscribed"] += 1
        if status == "opened":
            totals["opened"] += 1
        if status == "archived":
            totals["archived"] += 1
        if status == "archived" and (getattr(row, "archive_reason", "") or "").strip().lower() == "no_response_archive":
            no_response_archived += 1
        engagement_scores.append(float(getattr(row, "engagement_score", 0.0) or 0.0))
        reply_likelihood_scores.append(float(getattr(row, "reply_likelihood_score", 0.0) or 0.0))
        responsiveness_scores.append(float(getattr(row, "responsiveness_score", 0.0) or 0.0))

    sent_total = sent_events or totals["sent"] or (totals["opened"] + totals["replied"])
    bounce_rate = totals["bounced"] / sent_total if sent_total else 0.0
    reply_rate = replied_events / sent_total if sent_total else 0.0
    open_rate = opened_events / sent_total if sent_total else 0.0
    follow_up_effectiveness = replied_events / totals["follow_up_sent"] if totals["follow_up_sent"] else 0.0
    average = lambda values: round(sum(values) / len(values), 4) if values else 0.0

    return {
        "counts": totals,
        "replyRate": round(reply_rate, 4),
        "openRate": round(open_rate, 4),
        "followUpEffectiveness": round(follow_up_effectiveness, 4),
        "bounceRate": round(bounce_rate, 4),
        "replyStates": reply_states,
        "engagementScoreAverage": average(engagement_scores),
        "replyLikelihoodAverage": average(reply_likelihood_scores),
        "responsivenessAverage": average(responsiveness_scores),
        "sentTotal": sent_total,
        "replyTotal": replied_events,
        "noResponseArchived": no_response_archived,
        "total": len(rows),
    }


def get_workflow_token_health(db: Session) -> dict[str, Any]:
    from app.models.entities import NotificationWorkflowTokenEntity

    token_rows = db.scalars(select(NotificationWorkflowTokenEntity).order_by(NotificationWorkflowTokenEntity.created_at.desc()).limit(200)).all()
    active = [row for row in token_rows if row.is_active]
    missing_source_type = sum(1 for row in token_rows if not str((_metadata_map(row.payload).get("source_type") or _metadata_map(row.payload).get("sourceType") or "").strip()))
    expired = sum(1 for row in token_rows if row.expires_at and row.expires_at < datetime.now(timezone.utc))
    enrichment_status_counts: dict[str, int] = {}
    enrichment_confidences: list[float] = []
    apollo_person_tokens = 0
    for row in token_rows:
        payload = _metadata_map(row.payload)
        enrichment_status = str(payload.get("enrichmentStatus") or payload.get("enrichment_status") or "").strip().lower()
        if enrichment_status:
            enrichment_status_counts[enrichment_status] = enrichment_status_counts.get(enrichment_status, 0) + 1
        confidence = payload.get("enrichmentConfidence")
        if isinstance(confidence, (int, float)):
            enrichment_confidences.append(float(confidence))
        if str(payload.get("apolloPersonId") or "").strip():
            apollo_person_tokens += 1
    return {
        "active": len(active),
        "total": len(token_rows),
        "expired": expired,
        "missingSourceType": missing_source_type,
        "consumed": sum(1 for row in token_rows if row.consumed_at is not None),
        "enrichmentStatusCounts": enrichment_status_counts,
        "averageEnrichmentConfidence": round(sum(enrichment_confidences) / len(enrichment_confidences), 4) if enrichment_confidences else 0.0,
        "apolloPersonTokens": apollo_person_tokens,
    }


def get_enrichment_health(db: Session) -> dict[str, Any]:
    profiles = db.scalars(select(CandidateProfileEntity).order_by(CandidateProfileEntity.last_refreshed_at.desc()).limit(200)).all()
    status_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    confidence_values: list[float] = []
    apollo_person_ids: set[str] = set()
    contact_found = 0
    cache_hits = 0
    queued_enrichment_jobs = 0
    running_enrichment_jobs = 0
    for automation in AutomationJobRepository(db).list_recent(limit=200):
        if (automation.automation_type or "").strip().lower() != "candidate_enrichment":
            continue
        state = (automation.status or "").strip().lower()
        if state in {"queued", "retryable"}:
            queued_enrichment_jobs += 1
        elif state == "running":
            running_enrichment_jobs += 1
    for row in profiles:
        enrichment = _metadata_map(getattr(row, "raw_data", {})).get("enrichment") or {}
        status = str(enrichment.get("status") or enrichment.get("enrichmentStatus") or "pending").strip().lower() or "pending"
        source = str(enrichment.get("source") or "").strip().lower() or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
        source_counts[source] = source_counts.get(source, 0) + 1
        if enrichment.get("cachedAt"):
            cache_hits += 1
        confidence = enrichment.get("confidence", enrichment.get("identityMatchConfidence"))
        if isinstance(confidence, (int, float)):
            confidence_values.append(float(confidence))
        apollo_person_id = str(enrichment.get("apolloPersonId") or "").strip()
        if apollo_person_id:
            apollo_person_ids.add(apollo_person_id)
        raw_data = _metadata_map(getattr(row, "raw_data", {}))
        if str(getattr(row, "phone", "") or "").strip() or str(
            raw_data.get("email")
            or raw_data.get("work_email")
            or raw_data.get("contact_email")
            or raw_data.get("contactEmail")
            or raw_data.get("contact_phone")
            or raw_data.get("contactPhone")
            or ""
        ).strip():
            contact_found += 1
    return {
        "total": len(profiles),
        "statusCounts": status_counts,
        "sourceCounts": source_counts,
        "contactFound": contact_found,
        "pendingOrResolving": status_counts.get("pending", 0) + status_counts.get("resolving", 0),
        "enrichedOrPartial": status_counts.get("enriched", 0) + status_counts.get("partial", 0),
        "ambiguous": status_counts.get("ambiguous_match", 0),
        "noMatchFound": status_counts.get("no_match_found", 0),
        "failed": status_counts.get("failed", 0),
        "averageConfidence": round(sum(confidence_values) / len(confidence_values), 4) if confidence_values else 0.0,
        "apolloPersonCount": len(apollo_person_ids),
        "cacheHits": cache_hits,
        "queuedCandidateEnrichmentJobs": queued_enrichment_jobs,
        "runningCandidateEnrichmentJobs": running_enrichment_jobs,
        "replaySafe": bool(status_counts.get("enriched", 0) or status_counts.get("partial", 0) or status_counts.get("ambiguous_match", 0) or status_counts.get("no_match_found", 0)),
    }


def get_interview_health(db: Session) -> dict[str, Any]:
    from app.models.entities import InterviewEvaluationEntity, InterviewSessionEntity

    rows = db.scalars(select(InterviewSessionEntity).order_by(InterviewSessionEntity.created_at.desc()).limit(200)).all()
    stage_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    missing_workflow_links = 0
    for row in rows:
        status = (row.status or "").strip().lower() or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
        stage = str((_metadata_map(row.scheduling_metadata).get("stageName") or row.stage or "unknown")).strip().lower()
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        scheduling_metadata = _metadata_map(row.scheduling_metadata)
        if not (scheduling_metadata.get("workflowToken") or scheduling_metadata.get("workflow_token")):
            missing_workflow_links += 1
    return {
        "total": len(rows),
        "statusCounts": status_counts,
        "stageCounts": stage_counts,
        "missingWorkflowLinkage": missing_workflow_links,
        "evaluationsSubmitted": len(db.scalars(select(InterviewEvaluationEntity).order_by(InterviewEvaluationEntity.created_at.desc()).limit(200)).all()),
    }


def get_recruiter_learning_state(db: Session, recruiter_id: str) -> dict[str, Any]:
    return {
        "profile": load_recruiter_preference_profile(db, recruiter_id),
        "metrics": get_recruiter_learning_metrics(db, recruiter_id),
    }


def get_pipeline_board(db: Session, job_id: str | None = None) -> dict[str, Any]:
    job_repo = JobRepository(db)
    job = job_repo.get(job_id) if job_id else None
    target_job_id = job_id or (job.id if job else None)
    if not target_job_id:
        latest_job = job_repo.list_recent(limit=1)
        target_job_id = latest_job[0].id if latest_job else None
        job = latest_job[0] if latest_job else None

    if not target_job_id:
        return {"jobId": None, "counts": {}, "pendingActions": [], "upcomingInterviews": [], "automation": []}

    profiles = CandidateProfileRepository(db).list_for_job(target_job_id)
    interviews = InterviewSessionRepository(db)
    notifications = NotificationEventRepository(db)
    notes = RecruiterNoteRepository(db).list_for_job(target_job_id, limit=25)
    tasks = RecruiterTaskRepository(db).list_for_job(target_job_id, status="open", limit=25)
    automation = AutomationJobRepository(db).list_recent(limit=25)

    counts: dict[str, int] = {}
    for profile in profiles:
        key = (profile.ats_status or "reviewed").strip().lower()
        counts[key] = counts.get(key, 0) + 1

    upcoming_interviews: list[dict[str, Any]] = []
    for profile in profiles[:25]:
        session = interviews.get_by_job_and_candidate(job_id=target_job_id, candidate_id=profile.candidate_id)
        if not session:
            continue
        upcoming_interviews.append(
            {
                "candidateId": profile.candidate_id,
                "name": profile.name,
                "status": session.status,
                "stage": session.stage,
                "scheduledAt": session.scheduled_at.isoformat() if session.scheduled_at else None,
                "token": session.token,
            }
        )

    pending_notifications = notifications.list_recent(limit=25, unread_only=True)
    pending_actions = [
        {
            "type": "task",
            "id": task.id,
            "title": task.title,
            "candidateId": task.candidate_id,
            "priority": task.priority,
            "dueAt": task.due_at.isoformat() if task.due_at else None,
        }
        for task in tasks
    ] + [
        {
            "type": "notification",
            "id": notification.id,
            "title": notification.title,
            "candidateId": notification.candidate_id,
            "channel": notification.channel,
            "createdAt": notification.created_at.isoformat(),
        }
        for notification in pending_notifications[:10]
    ]

    return {
        "jobId": target_job_id,
        "jobTitle": job.title if job else "",
        "counts": counts,
        "notesCount": len(notes),
        "taskCount": len(tasks),
        "pendingActions": pending_actions[:20],
        "upcomingInterviews": upcoming_interviews[:20],
        "automation": [
            {
                "id": row.id,
                "type": row.automation_type,
                "status": row.status,
                "scheduledAt": row.scheduled_at.isoformat() if row.scheduled_at else None,
            }
            for row in automation[:10]
        ],
    }


def get_notification_center(db: Session, *, job_id: str | None = None, unread_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
    rows = NotificationEventRepository(db).list_recent(limit=limit, unread_only=unread_only)
    if job_id:
        rows = [row for row in rows if row.job_id == job_id]
    return [
        {
            "id": row.id,
            "jobId": row.job_id,
            "candidateId": row.candidate_id,
            "recipientType": row.recipient_type,
            "recipient": row.recipient,
            "channel": row.channel,
            "title": row.title,
            "body": row.body,
            "status": row.status,
            "notificationType": row.notification_type,
            "notificationKey": row.notification_key,
            "deliveryReference": row.delivery_reference,
            "isRead": bool(getattr(row, "is_read", False)),
            "readAt": row.read_at.isoformat() if getattr(row, "read_at", None) else None,
            "metadata": row.notification_metadata,
            "createdAt": row.created_at.isoformat() if row.created_at else None,
            "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]


def get_pipeline_analytics(db: Session, job_id: str | None = None) -> dict[str, Any]:
    jobs = [JobRepository(db).get(job_id)] if job_id else JobRepository(db).list_recent(limit=25)
    jobs = [job for job in jobs if job]
    total_candidates = 0
    ats_counts: dict[str, int] = {}
    interviews_scheduled = 0
    interviews_completed = 0
    evaluations_submitted = 0
    for job in jobs:
        profiles = CandidateProfileRepository(db).list_for_job(job.id)
        total_candidates += len(profiles)
        for profile in profiles:
            status = (profile.ats_status or "reviewed").strip().lower()
            ats_counts[status] = ats_counts.get(status, 0) + 1
        interviews = InterviewSessionRepository(db)
        for profile in profiles:
            session = interviews.get_by_job_and_candidate(job_id=job.id, candidate_id=profile.candidate_id)
            if not session:
                continue
            if (session.status or "").strip().lower() in {"interview_scheduled", "scheduled"}:
                interviews_scheduled += 1
            if (session.stage or "").strip().lower() == "completed":
                interviews_completed += 1
        evaluations_submitted += sum(
            1
            for profile in profiles
            for evaluation in InterviewEvaluationRepository(db).list_for_candidate(job_id=job.id, candidate_id=profile.candidate_id, limit=10)
            if (evaluation.status or "").strip().lower() in {"submitted", "complete", "completed"}
        )

    return {
        "jobs": len(jobs),
        "candidateCount": total_candidates,
        "atsCounts": ats_counts,
        "interviewsScheduled": interviews_scheduled,
        "interviewsCompleted": interviews_completed,
        "evaluationsSubmitted": evaluations_submitted,
        "jobsWithAutomation": len(AutomationJobRepository(db).list_recent(limit=100)),
    }


def inspect_audit_logs(db: Session, limit: int = 100) -> list[dict[str, Any]]:
    rows = db.scalars(select(AuditEventEntity).order_by(AuditEventEntity.created_at.desc()).limit(max(1, limit))).all()
    return [
        {
            "id": row.id,
            "actorId": row.actor_id,
            "actorType": row.actor_type,
            "action": row.action,
            "entityType": row.entity_type,
            "entityId": row.entity_id,
            "metadata": row.event_metadata,
            "requestId": row.request_id,
            "createdAt": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


def force_embedding_migration(db: Session, embedding_version: str, vector_size: int, details: dict[str, Any] | None = None) -> dict[str, Any]:
    row = promote_embedding_version(
        db,
        embedding_version=embedding_version,
        vector_size=vector_size,
        details=details or {"source": "admin"},
    )
    record_platform_event(
        event_type="embedding_migrated",
        source="admin",
        db=db,
        entity_type="embedding_version",
        entity_id=embedding_version,
        payload={"vectorSize": vector_size, "status": row.status},
    )
    return {
        "embeddingVersion": row.embedding_version,
        "status": row.status,
        "vectorSize": row.vector_size,
        "details": row.details,
    }


def refresh_candidate_manually(db: Session, job_id: str, candidate_id: str) -> dict[str, Any]:
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if not profile:
        raise ValueError("Candidate not found")
    refreshed = refresh_candidate(db, profile)
    db.commit()
    return {"jobId": job_id, "candidateId": candidate_id, "refreshed": bool(refreshed)}


def replay_dead_letter(queue_type: str, job_id: str) -> dict[str, Any]:
    return replay_dead_letter_job(queue_type, job_id)


def inspect_dead_letters(queue_type: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    return list_dead_letter_jobs(queue_type=queue_type, limit=limit)
