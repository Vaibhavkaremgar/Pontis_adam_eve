from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import OUTREACH_REPLY_TO_EMAIL, TEST_INVITE_EMAIL
from app.db.repositories import CandidateProfileRepository, CompanyRepository, InterviewRepository, JobIntakeRepository, JobRepository, OrchestrationSessionRepository
from app.db.session import SessionLocal
from app.services.email_service import send_email
from app.services.interview_session_service import create_interview_session
from app.services.job_queue_service import enqueue_job
from app.services.slack_integration import post_slack_message
from app.services.slack_tenant_service import SlackCompanyResolver
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)


def _normalize_email(value: Any) -> str:
    email = str(value or "").strip().lower()
    if not email or "@" not in email or email.endswith("@test.local"):
        return ""
    if ".." in email:
        return ""
    return email


def _extract_candidate_email(profile) -> str:
    raw_data = getattr(profile, "raw_data", None)
    if not isinstance(raw_data, dict):
        return ""

    for key in ("work_email", "email", "personal_email"):
        email = _normalize_email(raw_data.get(key))
        if email:
            return email

    for key in ("emails", "work_emails", "personal_emails"):
        values = raw_data.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, str):
                email = _normalize_email(item)
            elif isinstance(item, dict):
                email = _normalize_email(item.get("address") or item.get("email"))
            else:
                email = ""
            if email:
                return email
    return ""


def _normalize_timezone_name(value: Any, *, default: str = "Asia/Kolkata") -> str:
    text = str(value or "").strip()
    return text or default


def _safe_zoneinfo(timezone_name: str) -> ZoneInfo | dt_timezone:
    try:
        return ZoneInfo(_normalize_timezone_name(timezone_name))
    except Exception:
        return dt_timezone.utc


def _generate_default_slots(timezone: str, count: int = 5) -> list[str]:
    tz_name = _normalize_timezone_name(timezone)
    tz = _safe_zoneinfo(tz_name)
    current_day = datetime.now(tz).date() + timedelta(days=1)
    while current_day.weekday() >= 5:
        current_day += timedelta(days=1)

    slot_hours = (10, 11, 14, 15, 16)
    generated: list[str] = []
    target_count = max(0, int(count or 0))
    while len(generated) < target_count:
        if current_day.weekday() >= 5:
            current_day += timedelta(days=1)
            continue
        for hour in slot_hours:
            if len(generated) >= target_count:
                break
            slot = datetime(
                current_day.year,
                current_day.month,
                current_day.day,
                hour,
                0,
                tzinfo=tz,
            )
            generated.append(slot.astimezone(dt_timezone.utc).isoformat())
        current_day += timedelta(days=1)
        while current_day.weekday() >= 5:
            current_day += timedelta(days=1)
    return generated


def _nested_value(node: Any, *keys: str) -> Any:
    if isinstance(node, dict):
        for key in keys:
            value = node.get(key)
            if value not in (None, "", [], {}):
                return value
        for value in node.values():
            nested = _nested_value(value, *keys)
            if nested not in (None, "", [], {}):
                return nested
    elif isinstance(node, list):
        for item in node:
            nested = _nested_value(item, *keys)
            if nested not in (None, "", [], {}):
                return nested
    return None


def _slot_list_from_value(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    slots: list[str] = []
    for item in value:
        slot = str(item or "").strip()
        if slot:
            slots.append(slot)
    return slots


def _resolve_job_booking_context(*, db: Session, job_id: str) -> tuple[list[str], str]:
    job = JobRepository(db).get(job_id)
    available_slots: list[str] = []
    timezone_name = ""

    if job:
        structured_data = getattr(job, "structured_data", {})
        available_slots = _slot_list_from_value(_nested_value(structured_data, "available_slots", "availableSlots", "slots") or [])
        timezone_name = _normalize_timezone_name(
            _nested_value(structured_data, "timezone", "timezone_name", "timezoneName") or "",
            default="",
        )
        if not available_slots or not timezone_name:
            intake = JobIntakeRepository(db).get_by_job(job.id)
            if intake:
                intake_data = getattr(intake, "structured_data_json", {})
                if not available_slots:
                    available_slots = _slot_list_from_value(
                        _nested_value(intake_data, "available_slots", "availableSlots", "slots") or []
                    )
                if not timezone_name:
                    timezone_name = _normalize_timezone_name(
                        _nested_value(intake_data, "timezone", "timezone_name", "timezoneName") or "",
                        default="",
                    )

    timezone_name = _normalize_timezone_name(timezone_name, default="Asia/Kolkata")
    if not available_slots:
        available_slots = _generate_default_slots(timezone_name, count=5)
    return available_slots, timezone_name


def _build_invite_template(
    *,
    candidate_name: str,
    role: str,
    company_name: str,
    interviewer: dict[str, Any] | None,
    booking_link: str,
    timezone_name: str,
) -> tuple[str, str]:
    interviewer_name = str((interviewer or {}).get("name") or "").strip()
    interviewer_title = str((interviewer or {}).get("title") or "").strip()
    interviewer_line = ""
    if interviewer_name and interviewer_title:
        interviewer_line = f"Your interviewer will be {interviewer_name}, {interviewer_title}."
    elif interviewer_name:
        interviewer_line = f"Your interviewer will be {interviewer_name}."
    elif interviewer_title:
        interviewer_line = f"Your interviewer will be a {interviewer_title}."

    subject = f"Interview Invitation for {role}"
    body_parts = [
        f"Hi {candidate_name},",
        "",
        f"We'd like to move forward with your application for {role} at {company_name}.",
    ]
    if interviewer_line:
        body_parts.extend(["", interviewer_line])
    body_parts.extend(
        [
            "",
            f"Slots are shown in {timezone_name}.",
            "",
            "Book your interview here:",
            booking_link,
            "",
            "Best,",
            "Adam",
        ]
    )
    return subject, "\n".join(body_parts)


def _build_invite_html(
    *,
    candidate_name: str,
    role: str,
    company_name: str,
    interviewer: dict[str, Any] | None,
    booking_link: str,
    timezone_name: str,
) -> str:
    interviewer_name = str((interviewer or {}).get("name") or "").strip()
    interviewer_title = str((interviewer or {}).get("title") or "").strip()
    interviewer_line = ""
    if interviewer_name and interviewer_title:
        interviewer_line = f"<p>Your interviewer will be <strong>{interviewer_name}</strong>, {interviewer_title}.</p>"
    elif interviewer_name:
        interviewer_line = f"<p>Your interviewer will be <strong>{interviewer_name}</strong>.</p>"
    elif interviewer_title:
        interviewer_line = f"<p>Your interviewer will be a {interviewer_title}.</p>"

    return (
        f"<p>Hi {candidate_name},</p>"
        f"<p>We'd like to move forward with your application for <b>{role}</b> at <b>{company_name}</b>.</p>"
        f"{interviewer_line}"
        f"<p>Slots are shown in {timezone_name}.</p>"
        f"<p><a href=\"{booking_link}\" style=\"display:inline-block;background:#111827;color:#ffffff;text-decoration:none;padding:12px 18px;border-radius:8px;font-weight:600;\">Book your interview</a></p>"
        f"<p>If the button does not work, use this link: <a href=\"{booking_link}\">{booking_link}</a></p>"
        "<p>Best,<br>Adam</p>"
    )


async def _post_slack_warning(channel_id: str | None, text: str, bot_token: str | None = None) -> None:
    target = (channel_id or "").strip()
    if not target or not bot_token:
        return
    try:
        await post_slack_message(channel_id=target, text=text, bot_token=bot_token)
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.error("interview_invite_slack_warning_failed channel_id=%s error=%s", target, str(exc), exc_info=exc)


def send_interview_invite(
    candidate_id: str,
    job_id: str,
    *,
    outreach_event_id: str | None = None,
    channel_id: str | None = None,
    resume_text: str | None = None,
    available_slots: list[str] | None = None,
    timezone: str = "Asia/Kolkata",
) -> dict[str, Any]:
    with SessionLocal() as db:
        return _send_interview_invite(
            db=db,
            candidate_id=candidate_id,
            job_id=job_id,
            outreach_event_id=outreach_event_id,
            channel_id=channel_id,
            resume_text=resume_text,
            available_slots=available_slots,
            timezone=timezone,
        )


def _send_interview_invite(
    *,
    db: Session,
    candidate_id: str,
    job_id: str,
    outreach_event_id: str | None = None,
    channel_id: str | None = None,
    resume_text: str | None = None,
    available_slots: list[str] | None = None,
    timezone: str = "Asia/Kolkata",
) -> dict[str, Any]:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)

    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if not profile:
        raise APIError("Candidate not found", status_code=404)
    if not (resume_text or "").strip():
        raise APIError("Resume text is required before sending an interview invite.", status_code=409)

    candidate_name = (profile.name or "").strip() or "there"
    role = (job.title or "").strip() or "the role"
    candidate_email = _extract_candidate_email(profile)
    using_test_email = not candidate_email and bool(TEST_INVITE_EMAIL)
    recipient_email = candidate_email or TEST_INVITE_EMAIL
    if not recipient_email:
        logger.info("invite_skipped reason=no_email candidate_id=%s", candidate_id)
        return {"success": False, "reason": "no_email"}

    company = CompanyRepository(db).get_by_id(job.company_id)
    company_name = str(getattr(company, "name", "") or "").strip() or "the company"
    normalized_timezone = _normalize_timezone_name(timezone, default="Asia/Kolkata")
    normalized_slots = list(available_slots or [])

    session = create_interview_session(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        outreach_event_id=outreach_event_id,
        source_app="adam",
        resume_text=resume_text,
        available_slots=normalized_slots,
        timezone_name=normalized_timezone,
        candidate_email_override=recipient_email,
    )
    booking_link = str(session.get("slot_link") or session.get("slotLink") or session.get("bookingLink") or session.get("bookingUrl") or "")
    workflow_token = str(session.get("workflowToken") or session.get("workflow_token") or session.get("token") or "").strip()
    interviewer = session.get("interviewer") if isinstance(session.get("interviewer"), dict) else {}
    subject, body = _build_invite_template(
        candidate_name=candidate_name,
        role=role,
        company_name=company_name,
        interviewer=interviewer,
        booking_link=booking_link,
        timezone_name=normalized_timezone,
    )
    if using_test_email:
        subject = f"[TEST] {subject}"
    html_body = _build_invite_html(
        candidate_name=candidate_name,
        role=role,
        company_name=company_name,
        interviewer=interviewer,
        booking_link=booking_link,
        timezone_name=normalized_timezone,
    )
    InterviewRepository(db).upsert_status(
        job_id=job_id,
        candidate_id=candidate_id,
        status="interview_requested",
        create_default="interview_requested",
        async_token=workflow_token,
    )
    db.commit()

    slack_context = {}
    orchestration_session = OrchestrationSessionRepository(db).get_by_job(job_id)
    if orchestration_session and isinstance(getattr(orchestration_session, "slack_context", None), dict):
        slack_context = dict(orchestration_session.slack_context or {})
    slack_channel_id = str(slack_context.get("channelId") or slack_context.get("channel_id") or "").strip()
    company_id = str(slack_context.get("companyId") or slack_context.get("company_id") or "").strip()
    bot_token = ""
    if company_id:
        bot_token = SlackCompanyResolver(db).resolve_bot_token(company_id=company_id)
        if not bot_token:
            logger.warning("slack_token_missing company_id=%s", company_id)

    last_error = ""
    for attempt in range(1, 4):
        try:
            send_email(
                to_email=recipient_email,
                subject=subject,
                body=body,
                html=html_body,
                text=body,
                reply_to=OUTREACH_REPLY_TO_EMAIL,
                tags={"product": "pontis", "flow": "interview_invite"},
            )
            db.commit()
            logger.info(
                "interview_invite_sent job_id=%s candidate_id=%s to_email=%s",
                job_id,
                candidate_id,
                recipient_email,
            )
            if using_test_email:
                logger.info("test_invite_sent candidate_id=%s to=%s", candidate_id, TEST_INVITE_EMAIL)
            if slack_channel_id and bot_token:
                try:
                    asyncio.run(
                        post_slack_message(
                            channel_id=slack_channel_id,
                            text=f"Interview invite sent to {candidate_name}. Booking link: {booking_link}",
                            bot_token=bot_token,
                        )
                    )
                except Exception as exc:
                    logger.error("slack_post_failed error=%s", str(exc))
            return {
                "success": True,
                "jobId": job_id,
                "candidateId": candidate_id,
                "candidateEmail": recipient_email,
                "subject": subject,
                "body": body,
                "bookingLink": booking_link,
                "status": "interview_requested",
            }
        except Exception as exc:
            last_error = str(exc)
            if attempt < 3:
                time.sleep(2)

    logger.error(
        "interview_invite_failed candidate_id=%s job_id=%s error=%s",
        candidate_id,
        job_id,
        last_error,
    )
    try:
        enqueue_job(
            "candidate_refresh",
            {
                "job_id": job_id,
                "candidate_id": candidate_id,
                "reason": "interview_invite_email_failed",
            },
            idempotency_key=f"candidate_refresh:interview_invite_email_failed:{job_id}:{candidate_id}",
        )
    except Exception as exc:
        logger.warning(
            "interview_invite_retry_enqueue_failed candidate_id=%s job_id=%s error=%s",
            candidate_id,
            job_id,
            str(exc),
            exc_info=exc,
        )
    asyncio.run(_post_slack_warning(channel_id, "\u26a0\ufe0f Failed to send interview invite", bot_token=bot_token))
    return {
        "success": False,
        "jobId": job_id,
        "candidateId": candidate_id,
        "candidateEmail": recipient_email,
        "subject": subject,
        "body": body,
        "bookingLink": booking_link,
        "status": "email_failed",
        "error": last_error,
        "retryQueued": True,
    }
