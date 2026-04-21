from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy.orm import Session

from app.core.config import (
    HTTP_TIMEOUT_SECONDS,
    OUTREACH_DRY_RUN,
    OUTREACH_FOLLOWUP_DAYS,
    OUTREACH_FROM_EMAIL,
    OUTREACH_PROVIDER,
    POSTMARK_SERVER_TOKEN,
    SENDGRID_API_KEY,
)
from app.db.repositories import CandidateProfileRepository, InterviewRepository, JobRepository, OutreachEventRepository
from app.services.metrics_service import log_metric
from app.services.slack_service import notify_slack
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)


def _extract_email(raw: dict) -> str:
    for key in ("work_email", "email", "personal_email"):
        value = raw.get(key)
        if isinstance(value, str) and "@" in value:
            return value.strip()

    for key in ("personal_emails", "emails"):
        value = raw.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and "@" in item:
                    return item.strip()

    return ""


def _build_email_message(*, candidate_profile, job) -> tuple[str, str]:
    candidate_name = candidate_profile.name or "there"
    role_hint = candidate_profile.role or "your recent work"
    company_hint = candidate_profile.company or "your current team"
    top_skills = ", ".join((candidate_profile.skills or [])[:4])

    subject = f"{job.title}: quick intro from Pontis"
    body = (
        f"Hi {candidate_name},\n\n"
        f"I noticed your {role_hint} experience at {company_hint} and the way you use {top_skills or 'modern engineering practices'}. "
        f"We are hiring for {job.title}"
        f"{' in ' + job.location if job.location else ''}"
        f"{' with compensation ' + job.compensation if job.compensation else ''}.\n\n"
        "Your profile looks closely aligned with what we need. "
        "Would you be open to a 15-minute conversation this week?\n\n"
        "Best,\nPontis Hiring Team"
    )
    return subject, body


def _send_sendgrid(*, to_email: str, subject: str, body: str) -> bool:
    if not SENDGRID_API_KEY:
        return False

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": OUTREACH_FROM_EMAIL},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }
    headers = {
        "Authorization": f"Bearer {SENDGRID_API_KEY}",
        "Content-Type": "application/json",
    }
    response = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers=headers,
        json=payload,
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    return response.status_code in {200, 202}


def _send_postmark(*, to_email: str, subject: str, body: str) -> bool:
    if not POSTMARK_SERVER_TOKEN:
        return False

    payload = {
        "From": OUTREACH_FROM_EMAIL,
        "To": to_email,
        "Subject": subject,
        "TextBody": body,
    }
    headers = {
        "X-Postmark-Server-Token": POSTMARK_SERVER_TOKEN,
        "Content-Type": "application/json",
    }
    response = requests.post(
        "https://api.postmarkapp.com/email",
        headers=headers,
        json=payload,
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    return 200 <= response.status_code < 300


def _send_outreach_email(*, to_email: str, subject: str, body: str) -> bool:
    provider = OUTREACH_PROVIDER
    if provider == "postmark":
        return _send_postmark(to_email=to_email, subject=subject, body=body)
    return _send_sendgrid(to_email=to_email, subject=subject, body=body)


def _follow_up_time() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=max(1, OUTREACH_FOLLOWUP_DAYS))


def process_outreach(*, db: Session, job_id: str, selected_candidates: list[str]) -> dict:
    jobs = JobRepository(db)
    job = jobs.get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    if not selected_candidates:
        raise APIError("selectedCandidates is required", status_code=400)

    interviews = InterviewRepository(db)
    profiles = CandidateProfileRepository(db)
    outreach_events = OutreachEventRepository(db)

    contacted = 0
    emailed = 0
    follow_up_scheduled = 0
    skipped = 0

    for candidate_id in selected_candidates:
        profile = profiles.get(job_id=job_id, candidate_id=candidate_id)
        if not profile:
            skipped += 1
            continue

        to_email = _extract_email(profile.raw_data or {})
        subject, body = _build_email_message(candidate_profile=profile, job=job)
        next_follow_up = _follow_up_time()

        if OUTREACH_DRY_RUN:
            status = "dry_run"
            outreach_events.upsert(
                job_id=job_id,
                candidate_id=candidate_id,
                provider=OUTREACH_PROVIDER,
                to_email=to_email,
                subject=subject,
                body=body,
                status=status,
                sent_at=datetime.now(timezone.utc),
                next_follow_up_at=next_follow_up,
            )
            if to_email:
                interviews.upsert_status(
                    job_id=job_id,
                    candidate_id=candidate_id,
                    status="contacted",
                    create_default="shortlisted",
                )
                contacted += 1
                emailed += 1
                follow_up_scheduled += 1
            else:
                skipped += 1
            continue

        if not to_email:
            skipped += 1
            outreach_events.upsert(
                job_id=job_id,
                candidate_id=candidate_id,
                provider=OUTREACH_PROVIDER,
                to_email=to_email,
                subject=subject,
                body=body,
                status="failed",
                last_error="missing_email",
                next_follow_up_at=None,
            )
            continue

        try:
            if _send_outreach_email(to_email=to_email, subject=subject, body=body):
                interviews.upsert_status(
                    job_id=job_id,
                    candidate_id=candidate_id,
                    status="contacted",
                    create_default="shortlisted",
                )
                contacted += 1
                emailed += 1
                follow_up_scheduled += 1
                outreach_events.upsert(
                    job_id=job_id,
                    candidate_id=candidate_id,
                    provider=OUTREACH_PROVIDER,
                    to_email=to_email,
                    subject=subject,
                    body=body,
                    status="sent",
                    sent_at=datetime.now(timezone.utc),
                    next_follow_up_at=next_follow_up,
                )
            else:
                skipped += 1
                outreach_events.upsert(
                    job_id=job_id,
                    candidate_id=candidate_id,
                    provider=OUTREACH_PROVIDER,
                    to_email=to_email,
                    subject=subject,
                    body=body,
                    status="failed",
                    last_error="provider_rejected",
                    next_follow_up_at=None,
                )
        except requests.RequestException as exc:
            logger.warning("Outreach email request failed for candidateId=%s", candidate_id, exc_info=exc)
            skipped += 1
            outreach_events.upsert(
                job_id=job_id,
                candidate_id=candidate_id,
                provider=OUTREACH_PROVIDER,
                to_email=to_email,
                subject=subject,
                body=body,
                status="failed",
                last_error=str(exc),
                next_follow_up_at=None,
            )

    db.commit()
    log_metric("outreach_sent", job_id=job_id, contacted=contacted, emailed=emailed, follow_up=follow_up_scheduled)
    notify_slack(
        title="Pontis Outreach Processed",
        lines=[
            f"job_id={job_id}",
            f"contacted={contacted}",
            f"emailed={emailed}",
            f"failed_or_skipped={skipped}",
        ],
    )
    return {
        "message": (
            f"Outreach processed for {contacted} candidates "
            f"({emailed} emailed, {follow_up_scheduled} follow-up scheduled, {skipped} skipped)"
        ),
    }


def list_outreach_status(*, db: Session, job_id: str) -> list[dict]:
    jobs = JobRepository(db)
    if not jobs.get(job_id):
        raise APIError("Job not found", status_code=404)

    rows = OutreachEventRepository(db).list_for_job(job_id)
    return [
        {
            "candidateId": row.candidate_id,
            "status": row.status,
            "provider": row.provider,
            "toEmail": row.to_email,
            "attemptCount": row.attempt_count,
            "lastSentAt": row.last_sent_at.isoformat() if row.last_sent_at else None,
            "nextFollowUpAt": row.next_follow_up_at.isoformat() if row.next_follow_up_at else None,
            "lastError": row.last_error,
        }
        for row in rows
    ]
