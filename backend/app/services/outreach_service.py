from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import (
    ENABLE_FOLLOWUPS,
    ENABLE_REAL_EMAIL_SENDING,
    ENABLE_REPLY_DETECTION,
    FOLLOW_UP_DELAY_MINUTES,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    OUTREACH_DRY_RUN,
    OUTREACH_FROM_EMAIL,
    OUTREACH_REPLY_TO_EMAIL,
    OUTREACH_PROVIDER,
    OUTREACH_RESEND_FALLBACK_FROM_EMAIL,
    RESEND_API_KEY,
)
from app.db.repositories import (
    CandidateProfileRepository,
    InterviewRepository,
    JobRepository,
    OutreachEventRepository,
)
from app.db.session import SessionLocal
from app.models.entities import OutreachEventEntity
from app.services.metrics_service import log_metric
from app.services.slack_integration import post_slack_message
from app.services.slack_service import notify_slack
from app.services.state_machine import assert_valid_transition
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)
_EMAIL_PATTERN = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,63}$", re.IGNORECASE)


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


# ── Email content helpers ────────────────────────────────────────────────────

def _error_debug_string(exc: Exception) -> str:
    parts: list[str] = []
    message = str(exc).strip()
    if message:
        parts.append(message)
    for attr in ("status_code", "body", "response"):
        value = getattr(exc, attr, None)
        if value is not None:
            parts.append(f"{attr}={value}")
    return " | ".join(parts) or exc.__class__.__name__


def _extract_email(raw: dict) -> str:
    def _normalize_valid_email(value: str) -> str:
        candidate = (value or "").strip().lower()
        if not candidate or len(candidate) > 320:
            return ""
        if ".." in candidate:
            return ""
        if not _EMAIL_PATTERN.match(candidate):
            return ""
        local, _, domain = candidate.rpartition("@")
        if not local or not domain or domain.startswith(".") or domain.endswith("."):
            return ""
        return candidate

    for key in ("work_email", "email", "personal_email"):
        value = raw.get(key)
        if isinstance(value, str):
            normalized = _normalize_valid_email(value)
            if normalized:
                return normalized
    for key in ("personal_emails", "emails"):
        value = raw.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    normalized = _normalize_valid_email(item)
                    if normalized:
                        return normalized
    return ""


def _build_heuristic_email(*, candidate_profile, job) -> tuple[str, str]:
    """Deterministic template-based email — used when LLM is unavailable."""
    first_name = (candidate_profile.name or "").split()[0] if candidate_profile.name else "there"
    their_role = candidate_profile.role or "your background"
    their_company = candidate_profile.company
    skills = (candidate_profile.skills or [])[:3]
    hook = skills[0] if skills else their_role

    subject = f"quick question about {job.title} - thought of you"

    opening = (
        f"I was looking at your profile and your time at {their_company} caught my eye"
        f" - especially the {their_role} work."
        if their_company
        else f"I came across your profile and your {their_role} experience stood out to me."
    )
    role_line = f"We're looking for a {job.title}"
    if job.location:
        role_line += f" based in {job.location}"
    role_line += f", and the way you approach {hook} looks especially relevant."

    skills_line = f"\n\nThe {', '.join(skills)} side of things is exactly what the team needs right now." if skills else ""
    comp_line = f" The range is {job.compensation}." if job.compensation else ""

    body = (
        f"Hey {first_name},\n\n"
        f"{opening}\n\n"
        f"{role_line}{skills_line}\n\n"
        f"{comp_line + chr(10) + chr(10) if comp_line else ''}"
        "Are you open to this role?\n"
        "Please share your updated resume if you're interested.\n\n"
        "Would you be up for a quick chat this week? Even 15 minutes would be great - no pressure at all.\n\n"
        "Cheers"
    )
    return subject, body


def generate_personalized_email(*, candidate_profile, job) -> tuple[str, str]:
    """
    Generate a personalized outreach email using the LLM.
    Falls back to the heuristic template if OpenAI is unavailable or fails.
    Prompts are deterministic — no hallucinated claims about the candidate.
    """
    if not OPENAI_API_KEY:
        return _build_heuristic_email(candidate_profile=candidate_profile, job=job)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=OPENAI_API_KEY)
        skills_text = ", ".join((candidate_profile.skills or [])[:5]) or "not listed"
        prompt = (
            "Write a short, warm, personalized recruiting outreach email.\n"
            "Rules:\n"
            "- Max 120 words in the body\n"
            "- Do NOT invent facts about the candidate beyond what is given\n"
            "- Do NOT use buzzwords or corporate language\n"
            "- Sound like a human recruiter, not a bot\n"
            "- Ask whether the candidate is open to the role\n"
            "- Ask the candidate to share an updated resume\n"
            "- End with a soft call-to-action for a 15-minute chat\n\n"
            f"Candidate name: {candidate_profile.name or 'there'}\n"
            f"Candidate current role: {candidate_profile.role or 'unknown'}\n"
            f"Candidate current company: {candidate_profile.company or 'unknown'}\n"
            f"Candidate skills: {skills_text}\n"
            f"Candidate summary: {candidate_profile.summary or 'not listed'}\n"
            f"Job title: {job.title}\n"
            f"Job location: {job.location or 'flexible'}\n"
            f"Compensation: {job.compensation or 'competitive'}\n\n"
            "Include one concrete hook tying a candidate skill or background to the role.\n"
            "Return ONLY:\n"
            "SUBJECT: <subject line>\n"
            "BODY:\n<email body>"
        )
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=300,
        )
        text = (response.choices[0].message.content or "").strip()

        subject = ""
        body = ""
        if "SUBJECT:" in text and "BODY:" in text:
            subject_part, body_part = text.split("BODY:", 1)
            subject = subject_part.replace("SUBJECT:", "").strip()
            body = body_part.strip()

        if subject and body:
            logger.info(
                "llm_email_generated candidate_id=%s job_id=%s",
                candidate_profile.candidate_id,
                candidate_profile.job_id,
            )
            return subject, body

        logger.warning(
            "llm_email_parse_failed candidate_id=%s — falling back to template",
            candidate_profile.candidate_id,
        )
    except Exception as exc:
        logger.warning(
            "llm_email_generation_failed candidate_id=%s error=%s — falling back to template",
            candidate_profile.candidate_id,
            str(exc),
        )

    return _build_heuristic_email(candidate_profile=candidate_profile, job=job)


def _build_followup_email(*, candidate_profile, job, follow_up_number: int) -> tuple[str, str]:
    """Build a short follow-up email. No LLM — deterministic and safe."""
    first_name = (candidate_profile.name or "").split()[0] if candidate_profile.name else "there"
    subject = f"following up - {job.title} opportunity"
    body = (
        f"Hey {first_name},\n\n"
        f"Just wanted to follow up on my previous note about the {job.title} role"
        f"{' in ' + job.location if job.location else ''}.\n\n"
        "I know inboxes get busy — totally understand. If the timing isn't right, no worries at all.\n\n"
        "But if you're open to a quick 15-minute chat, I'd love to connect.\n\n"
        "Cheers"
    )
    return subject, body


def _build_shortlist_outreach_email(*, candidate_profile, job) -> tuple[str, str]:
    candidate_name = html.escape((candidate_profile.name or "").strip() or "there")
    role = html.escape((getattr(job, "title", "") or "").strip() or "the role")
    company_name = html.escape(_job_company_name(job))
    subject = f"Opportunity at {_job_company_name(job)}"
    email_template = f"""
<p>Hi {candidate_name},</p>

<p>You've been shortlisted for the <b>{role}</b> position at <b>{company_name}</b>.</p>

<p>Are you open to this role?</p>

<p>Please share your updated resume.</p>

<p>We'd love to move forward with you.</p>

<p>Could you please reply with your availability for an interview?</p>

<p>Best,<br>Adam</p>
"""
    return subject, email_template


def _job_company_name(job) -> str:
    company = getattr(job, "company", None)
    company_name = getattr(company, "name", "") if company is not None else ""
    return (company_name or "your company").strip() or "your company"


def _candidate_raw_data(candidate_profile) -> dict[str, Any]:
    raw_data = getattr(candidate_profile, "raw_data", None)
    return raw_data if isinstance(raw_data, dict) else {}


def _extract_candidate_email(candidate_profile) -> str:
    raw_data = _candidate_raw_data(candidate_profile)
    if bool(raw_data.get("is_mock_email")) or str(raw_data.get("email_source") or "").strip().lower() == "generated":
        return ""
    for key in ("work_email", "email", "personal_email"):
        value = str(raw_data.get(key) or "").strip()
        if value:
            if value.lower().endswith("@test.local"):
                return ""
            return value
    return ""


def _extract_candidate_linkedin_url(candidate_profile) -> str:
    raw_data = _candidate_raw_data(candidate_profile)
    for key in ("linkedin", "linkedin_url", "linkedinUrl", "profile_url"):
        value = str(raw_data.get(key) or "").strip()
        if "linkedin.com" in value.lower():
            return value
    return ""


def _extract_resend_message_id(response: Any) -> str:
    if isinstance(response, dict):
        return str(response.get("id") or response.get("message_id") or "").strip()
    return str(getattr(response, "id", "") or getattr(response, "message_id", "") or "").strip()


def _send_shortlist_outreach_email(*, to_email: str, subject: str, html_body: str) -> tuple[bool, str, str]:
    if not RESEND_API_KEY:
        return False, "RESEND_API_KEY_missing", ""

    try:
        import resend

        resend.api_key = RESEND_API_KEY
        response = resend.emails.send(
            {
                "from": OUTREACH_FROM_EMAIL,
                "reply_to": OUTREACH_REPLY_TO_EMAIL,
                "to": to_email,
                "subject": subject,
                "html": html_body,
            }
        )
        message_id = _extract_resend_message_id(response)
        if not message_id:
            logger.warning("resend_shortlist_send_missing_id to=%s response=%s", to_email, response)
        return True, "", message_id
    except Exception as exc:
        error = _error_debug_string(exc)
        logger.error("resend_shortlist_send_failed to=%s error=%s", to_email, error, exc_info=exc)
        return False, error, ""


async def _safe_post_slack_message(*, channel_id: str, text: str) -> bool:
    try:
        return await post_slack_message(channel_id=channel_id, text=text)
    except Exception as exc:
        logger.error("slack_message_post_failed channel_id=%s error=%s", channel_id, str(exc), exc_info=exc)
        return False


# ── Email sending ────────────────────────────────────────────────────────────

def _send_resend(*, to_email: str, subject: str, body: str, from_email: str) -> tuple[bool, str, str]:
    """Returns (success, error_message, provider_message_id)."""
    if not RESEND_API_KEY:
        return False, "RESEND_API_KEY_missing", ""

    try:
        import resend
        resend.api_key = RESEND_API_KEY
        response = resend.Emails.send(
            {
                "from": from_email,
                "reply_to": OUTREACH_REPLY_TO_EMAIL,
                "to": [to_email],
                "subject": subject,
                "text": body,
            }
        )
        try:
            email_id = response["id"]
        except (KeyError, TypeError):
            email_id = getattr(response, "id", None) or ""
        if email_id:
            logger.info("resend_email_sent to=%s resend_id=%s", to_email, email_id)
            return True, "", str(email_id)
        logger.warning("resend_email_no_id to=%s response=%s", to_email, response)
        return False, f"resend_no_id response={response}", ""
    except ImportError:
        logger.warning("resend_sdk_missing_using_http_api to=%s", to_email)
    except Exception as exc:
        logger.error("resend_sdk_failed_falling_back_http to=%s error=%s", to_email, _error_debug_string(exc))

    # HTTP fallback
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={
                "from": from_email,
                "reply_to": OUTREACH_REPLY_TO_EMAIL,
                "to": [to_email],
                "subject": subject,
                "text": body,
            },
            timeout=20,
        )
        if 200 <= resp.status_code < 300:
            try:
                payload = resp.json()
            except Exception:
                payload = {}
            email_id = str(payload.get("id", ""))
            logger.info("resend_http_email_sent to=%s resend_id=%s", to_email, email_id)
            return True, "", email_id
        error = f"resend_http_failed status={resp.status_code} body={resp.text[:200]}"
        logger.error("resend_http_send_failed to=%s error=%s", to_email, error)
        return False, error, ""
    except Exception as exc:
        error = _error_debug_string(exc)
        logger.error("resend_http_exception to=%s error=%s", to_email, error, exc_info=exc)
        return False, error, ""


def _is_email_provider_configured() -> tuple[bool, str]:
    if OUTREACH_PROVIDER == "resend" and RESEND_API_KEY:
        return True, ""
    if OUTREACH_PROVIDER == "resend":
        return False, "RESEND_API_KEY is missing"
    return False, f"Unsupported OUTREACH_PROVIDER '{OUTREACH_PROVIDER}'"


def _send_outreach_email(*, to_email: str, subject: str, body: str) -> tuple[bool, str, str]:
    """Returns (success, error, provider_message_id)."""
    ok, error, msg_id = _send_resend(to_email=to_email, subject=subject, body=body, from_email=OUTREACH_FROM_EMAIL)
    if ok:
        return True, "", msg_id

    fallback_from = OUTREACH_RESEND_FALLBACK_FROM_EMAIL.strip()
    if fallback_from and fallback_from.lower() != OUTREACH_FROM_EMAIL.lower():
        logger.warning("resend_retry_fallback_from to=%s fallback=%s", to_email, fallback_from)
        ok2, error2, msg_id2 = _send_resend(to_email=to_email, subject=subject, body=body, from_email=fallback_from)
        if ok2:
            return True, "", msg_id2
        return False, f"{error}; retry={error2}", ""

    return False, error, ""


def _follow_up_time() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=max(1, FOLLOW_UP_DELAY_MINUTES))


def _detect_reply_intent(raw_event: dict[str, Any]) -> str:
    body = _normalize_text(raw_event.get("body") or raw_event.get("text") or raw_event.get("snippet") or "")
    lowered = body.lower()
    if any(token in lowered for token in ("not interested", "no thanks", "unsubscribe", "stop")):
        return "not_interested"
    if any(token in lowered for token in ("interested", "sounds good", "let's talk", "happy to chat")):
        return "interested"
    return "unknown"


def _coerce_event_payload(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        return event
    model_dump = getattr(event, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            return {}
    return {}


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str):
            normalized = _normalize_text(value)
            if normalized:
                return normalized
    return ""


def _normalize_message_id(value: Any) -> str:
    message_id = _first_text(value)
    if not message_id:
        return ""
    return message_id.strip().strip("<>").strip()


def _extract_inbound_reply_fields(event: Any) -> dict[str, str]:
    raw_event = _coerce_event_payload(event)
    headers = _coerce_event_payload(raw_event.get("headers"))
    provider_event = _coerce_event_payload(raw_event.get("data") or raw_event.get("email"))
    nested_event = _coerce_event_payload(raw_event.get("rawEvent"))

    email_from = _first_text(
        raw_event.get("from"),
        raw_event.get("sender"),
        provider_event.get("from"),
        provider_event.get("sender"),
        nested_event.get("from"),
        nested_event.get("sender"),
    )
    subject = _first_text(raw_event.get("subject"), provider_event.get("subject"), nested_event.get("subject"))
    body = _first_text(
        raw_event.get("text"),
        raw_event.get("html"),
        raw_event.get("body"),
        provider_event.get("text"),
        provider_event.get("html"),
        provider_event.get("body"),
        nested_event.get("text"),
        nested_event.get("html"),
        nested_event.get("body"),
    )
    provider_message_id = _normalize_message_id(
        raw_event.get("providerMessageId")
        or raw_event.get("provider_message_id")
        or raw_event.get("messageId")
        or raw_event.get("message_id")
        or raw_event.get("Message-Id")
        or raw_event.get("emailId")
        or raw_event.get("email_id")
        or raw_event.get("id")
        or headers.get("message-id")
        or headers.get("Message-Id")
        or provider_event.get("providerMessageId")
        or provider_event.get("provider_message_id")
        or provider_event.get("messageId")
        or provider_event.get("message_id")
        or provider_event.get("Message-Id")
        or provider_event.get("emailId")
        or provider_event.get("email_id")
        or provider_event.get("id")
    )
    job_id = _first_text(raw_event.get("jobId"), raw_event.get("job_id"), nested_event.get("jobId"), nested_event.get("job_id"))
    candidate_id = _first_text(
        raw_event.get("candidateId"),
        raw_event.get("candidate_id"),
        nested_event.get("candidateId"),
        nested_event.get("candidate_id"),
    )

    return {
        "email_from": email_from,
        "subject": subject,
        "body": body,
        "provider_message_id": provider_message_id,
        "job_id": job_id,
        "candidate_id": candidate_id,
    }


def handle_email_reply(event, db: Session) -> dict[str, str]:
    logger.info("request_started reply_handler event_received=%s", bool(event))

    try:
        if not ENABLE_REPLY_DETECTION:
            logger.info("decision_taken reply_handler=disabled")
            return {"status": "skipped", "reason": "disabled"}

        fields = _extract_inbound_reply_fields(event)
        email_from = fields["email_from"]
        subject = fields["subject"]
        body = fields["body"]
        provider_message_id = fields["provider_message_id"]
        message_id = provider_message_id
        job_id = fields["job_id"]
        candidate_id = fields["candidate_id"]

        logger.info(
            "decision_taken reply_fields email_from=%s provider_message_id=%s job_id=%s candidate_id=%s",
            email_from,
            provider_message_id,
            job_id,
            candidate_id,
        )
        if not message_id:
            logger.info("fallback_used reply_handler=missing_message_id")

        raw_event = _coerce_event_payload(event)
        nested_event = _coerce_event_payload(raw_event.get("rawEvent"))
        provider_event = _coerce_event_payload(raw_event.get("data") or raw_event.get("email"))

        repo = OutreachEventRepository(db)
        row = None
        if message_id:
            row = repo.get_by_provider_message_id(message_id)
            logger.info("fallback_used reply_lookup=provider_message_id found=%s", bool(row))

        if not row and email_from:
            logger.info("fallback_used reply_lookup=email")
            row = db.scalar(
                select(OutreachEventEntity)
                .where(OutreachEventEntity.to_email == email_from)
                .order_by(OutreachEventEntity.created_at.desc())
            )

        if not row and job_id and candidate_id:
            logger.info("fallback_used reply_lookup=job_candidate")
            row = repo.get(job_id=job_id, candidate_id=candidate_id)

        if not row:
            logger.warning("error_occurred reply_mapping_failed email_from=%s provider_message_id=%s", email_from, provider_message_id)
            return {"status": "ignored"}

        if (row.status or "").strip().lower() == "replied":
            logger.info("result_returned reply_already_replied job_id=%s candidate_id=%s", row.job_id, row.candidate_id)
            return {"status": "ignored"}

        intent = _detect_reply_intent({**raw_event, **nested_event, **provider_event, "from": email_from, "subject": subject, "body": body, "text": body})
        row.status = "replied"
        row.last_contacted_at = datetime.now(timezone.utc)
        row.last_error = ""
        row.next_follow_up_at = None

        interview_repo = InterviewRepository(db)
        interview_row = interview_repo.get_by_job_and_candidate(row.job_id, row.candidate_id)
        if interview_row:
            interview_row.status = "replied"

        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error(
                "error_occurred reply_finalize_failed job_id=%s candidate_id=%s provider_message_id=%s error=%s",
                row.job_id,
                row.candidate_id,
                provider_message_id or getattr(row, "provider_message_id", ""),
                str(exc),
                exc_info=exc,
            )
            return {"status": "ignored"}

        logger.info(
            "result_returned reply_mapping_success job_id=%s candidate_id=%s provider_message_id=%s intent=%s",
            row.job_id,
            row.candidate_id,
            provider_message_id or (row.provider_message_id or ""),
            intent,
        )
        if intent == "interested":
            try:
                from app.services.interview_session_service import create_interview_session

                session = create_interview_session(db=db, job_id=row.job_id, candidate_id=row.candidate_id)
                logger.info(
                    "decision_taken interview_session_created job_id=%s candidate_id=%s token=%s booking_url=%s",
                    row.job_id,
                    row.candidate_id,
                    session.get("token", ""),
                    session.get("bookingUrl", ""),
                )
            except Exception as exc:
                logger.warning(
                    "fallback_used interview_session_creation_failed job_id=%s candidate_id=%s error=%s",
                    row.job_id,
                    row.candidate_id,
                    str(exc),
                    exc_info=exc,
                )
        log_metric("reply_received", job_id=row.job_id, candidate_id=row.candidate_id, intent=intent)
        return {
            "status": "replied",
            "job_id": row.job_id,
            "candidate_id": row.candidate_id,
            "provider_message_id": provider_message_id or (row.provider_message_id or ""),
            "intent": intent,
        }
    except Exception as exc:
        logger.error("error_occurred reply_handler_exception error=%s", str(exc), exc_info=exc)
        raise
# ── Main outreach process ────────────────────────────────────────────────────

def process_outreach(
    *, db: Session, job_id: str, selected_candidates: list[str], custom_body: str = ""
) -> dict:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    if not selected_candidates:
        raise APIError("selectedCandidates is required", status_code=400)

    interviews = InterviewRepository(db)
    profiles = CandidateProfileRepository(db)
    outreach_events = OutreachEventRepository(db)

    # ── Enforce shortlisted-only ──────────────────────────────────────────────
    unique_selected_candidates = list(dict.fromkeys(selected_candidates))
    interview_status_map: dict[str, str] = {
        row.candidate_id: (row.status or "").strip().lower()
        for row in interviews.list_for_job(job_id)
    }
    valid_candidates = [c for c in unique_selected_candidates if interview_status_map.get(c, "new") == "shortlisted"]
    rejected_count = len(unique_selected_candidates) - len(valid_candidates)

    for cid in unique_selected_candidates:
        if interview_status_map.get(cid, "new") != "shortlisted":
            logger.warning(
                "outreach_rejected_non_shortlisted job_id=%s candidate_id=%s status=%s",
                job_id, cid, interview_status_map.get(cid, "new"),
            )

    logger.info(
        "outreach_candidates job_id=%s shortlisted=%s rejected_non_shortlisted=%s",
        job_id, len(valid_candidates), rejected_count,
    )
    log_metric("outreach_candidates", job_id=job_id, shortlisted=len(valid_candidates), rejected=rejected_count)

    if not valid_candidates:
        raise APIError(
            "No shortlisted candidates in selection. Accept candidates in the Review step before sending outreach.",
            status_code=400,
        )

    provider_configured, provider_warning = _is_email_provider_configured()
    processed = sent = skipped = follow_up_scheduled = 0
    details: list[dict] = []
    skipped_candidates: list[dict] = []
    skip_reasons: dict[str, int] = {}
    warnings: list[str] = []
    if provider_warning:
        warnings.append(provider_warning)

    for candidate_id in valid_candidates:
        processed += 1
        profile = profiles.get(job_id=job_id, candidate_id=candidate_id)
        current_status = interview_status_map.get(candidate_id, "new")
        if not profile:
            logger.warning("outreach_profile_missing job_id=%s candidate_id=%s", job_id, candidate_id)
            logger.warning(
                "invalid_candidate_reference_detected table=outreach_events job_id=%s candidate_id=%s",
                job_id,
                candidate_id,
            )
            skipped += 1
            reason = "candidate_profile_not_found"
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            details.append({"candidateId": candidate_id, "status": "skipped", "reason": reason})
            skipped_candidates.append({"candidateId": candidate_id, "reason": reason})
            continue

        to_email = _extract_email(profile.raw_data or {})
        if not to_email:
            logger.warning("outreach_email_invalid_or_missing job_id=%s candidate_id=%s", job_id, candidate_id)
            skipped += 1
            reason = "invalid_or_missing_email"
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            details.append({"candidateId": candidate_id, "status": "skipped", "reason": reason, "toEmail": ""})
            skipped_candidates.append({"candidateId": candidate_id, "reason": reason})
            outreach_events.upsert(
                job_id=job_id, candidate_id=candidate_id, provider=OUTREACH_PROVIDER,
                to_email="", subject="", body="", status="failed", last_error=reason,
            )
            continue

        # Generate personalized email (LLM with heuristic fallback)
        if custom_body.strip():
            subject, _ = generate_personalized_email(candidate_profile=profile, job=job)
            body = custom_body.strip()
        else:
            subject, body = generate_personalized_email(candidate_profile=profile, job=job)

        next_follow_up = _follow_up_time()
        try:
            assert_valid_transition(
                candidate_id=candidate_id,
                job_id=job_id,
                from_status=current_status,
                to_status="contacted",
            )
        except APIError as exc:
            skipped += 1
            reason = f"invalid_state_transition:{exc.message}"
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            logger.warning(
                "outreach_invalid_transition_blocked job_id=%s candidate_id=%s current_status=%s reason=%s",
                job_id,
                candidate_id,
                current_status,
                reason,
            )
            details.append({"candidateId": candidate_id, "status": "skipped", "reason": reason, "toEmail": to_email})
            skipped_candidates.append({"candidateId": candidate_id, "reason": reason})
            continue

        simulate_send = OUTREACH_DRY_RUN or to_email.endswith("@test.local") or not ENABLE_REAL_EMAIL_SENDING
        if simulate_send:
            interviews.upsert_status(job_id=job_id, candidate_id=candidate_id, status="contacted", create_default="shortlisted")
            outreach_events.upsert(
                job_id=job_id, candidate_id=candidate_id, provider=OUTREACH_PROVIDER,
                to_email=to_email, subject=subject, body=body, status="simulated",
                sent_at=datetime.now(timezone.utc),
                next_follow_up_at=next_follow_up if ENABLE_FOLLOWUPS else None,
            )
            db.commit()
            sent += 1
            follow_up_scheduled += 1
            logger.info(
                "outreach_simulated job_id=%s candidate_id=%s to_email=%s simulated=%s",
                job_id,
                candidate_id,
                to_email,
                True,
            )
            log_metric("outreach_email_sent", job_id=job_id, candidate_id=candidate_id, provider=OUTREACH_PROVIDER, simulated=True)
            details.append({"candidateId": candidate_id, "status": "simulated", "toEmail": to_email})
            continue

        if not provider_configured:
            skipped += 1
            reason = "email_provider_not_configured"
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            logger.warning("outreach_skipped job_id=%s candidate_id=%s reason=%s", job_id, candidate_id, reason)
            details.append({"candidateId": candidate_id, "status": "skipped", "reason": reason, "toEmail": to_email})
            skipped_candidates.append({"candidateId": candidate_id, "reason": reason})
            outreach_events.upsert(
                job_id=job_id, candidate_id=candidate_id, provider=OUTREACH_PROVIDER,
                to_email=to_email, subject=subject, body=body, status="failed", last_error=reason,
            )
            db.commit()
            continue

        interviews.upsert_status(job_id=job_id, candidate_id=candidate_id, status="contacted", create_default="shortlisted")
        event = outreach_events.claim_outreach_for_sending(
            job_id=job_id,
            candidate_id=candidate_id,
            provider=OUTREACH_PROVIDER,
            to_email=to_email,
            subject=subject,
            body=body,
        )
        if not event:
            skipped += 1
            reason = "already_claimed"
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            logger.info("outreach_skipped_already_claimed job_id=%s candidate_id=%s", job_id, candidate_id)
            details.append({"candidateId": candidate_id, "status": "skipped", "reason": reason, "toEmail": to_email})
            skipped_candidates.append({"candidateId": candidate_id, "reason": reason})
            db.commit()
            continue

        db.commit()
        logger.info("outreach_claimed job_id=%s candidate_id=%s to_email=%s", job_id, candidate_id, to_email)
        logger.info("outreach_sending_started job_id=%s candidate_id=%s to_email=%s", job_id, candidate_id, to_email)

        try:
            email_sent, send_error, msg_id = _send_outreach_email(to_email=to_email, subject=subject, body=body)
            if email_sent:
                now = datetime.now(timezone.utc)
                event.provider_message_id = msg_id or None
                event.status = "sent"
                event.last_sent_at = now
                event.last_contacted_at = now
                event.next_follow_up_at = next_follow_up if ENABLE_FOLLOWUPS else None
                event.follow_up_count = 0
                event.last_error = ""
                try:
                    db.commit()
                except Exception as db_exc:
                    db.rollback()
                    logger.error(
                        "outreach_finalize_failed job_id=%s candidate_id=%s provider_id=%s error=%s",
                        job_id,
                        candidate_id,
                        msg_id,
                        str(db_exc),
                        exc_info=db_exc,
                    )
                    details.append({"candidateId": candidate_id, "status": "sending", "toEmail": to_email, "providerId": msg_id})
                    continue
                sent += 1
                follow_up_scheduled += 1
                logger.info(
                    "outreach_sent job_id=%s candidate_id=%s to_email=%s provider_id=%s",
                    job_id, candidate_id, to_email, msg_id,
                )
                log_metric("outreach_email_sent", job_id=job_id, candidate_id=candidate_id, provider=OUTREACH_PROVIDER, provider_id=msg_id)
                details.append({"candidateId": candidate_id, "status": "sent", "toEmail": to_email, "providerId": msg_id})
            else:
                skipped += 1
                reason = send_error or "provider_rejected"
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                logger.warning("outreach_failed job_id=%s candidate_id=%s reason=%s", job_id, candidate_id, reason)
                log_metric("outreach_email_failed", job_id=job_id, candidate_id=candidate_id, error=reason)
                event.status = "failed"
                event.last_error = reason
                event.provider_message_id = None
                event.next_follow_up_at = None
                db.commit()
                details.append({"candidateId": candidate_id, "status": "failed", "reason": reason, "toEmail": to_email})
                skipped_candidates.append({"candidateId": candidate_id, "reason": reason})
        except Exception as exc:
            reason = _error_debug_string(exc)
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            skipped += 1
            logger.error("outreach_exception job_id=%s candidate_id=%s error=%s", job_id, candidate_id, reason, exc_info=exc)
            log_metric("outreach_email_failed", job_id=job_id, candidate_id=candidate_id, error=reason)
            event.status = "failed"
            event.last_error = reason
            event.provider_message_id = None
            event.next_follow_up_at = None
            db.commit()
            details.append({"candidateId": candidate_id, "status": "failed", "reason": reason, "toEmail": to_email})
            skipped_candidates.append({"candidateId": candidate_id, "reason": reason})

    log_metric("outreach_cycle", job_id=job_id, processed=processed, sent=sent, skipped=skipped)
    notify_slack(
        title="Pontis Outreach Processed",
        lines=[f"job_id={job_id}", f"processed={processed}", f"sent={sent}", f"skipped={skipped}"],
    )
    payload: dict = {
        "success": True,
        "processed": processed,
        "sent": sent,
        "skipped": skipped,
        "details": details,
        "skippedCandidates": skipped_candidates,
        "skipReasons": skip_reasons,
        "debug": {
            "provider": OUTREACH_PROVIDER,
            "fromEmail": OUTREACH_FROM_EMAIL,
            "providerConfigured": provider_configured,
            "dryRun": OUTREACH_DRY_RUN,
        },
    }
    if warnings:
        payload["warnings"] = warnings
    return payload


def _trigger_candidate_outreach_sync(*, candidate_id: str, job_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        job = JobRepository(db).get(job_id)
        if not job:
            raise APIError("Job not found", status_code=404)

        profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
        if not profile:
            raise APIError("Candidate not found", status_code=404)

        name = (profile.name or "").strip() or candidate_id
        linkedin_url = _extract_candidate_linkedin_url(profile)
        subject, email_template = _build_shortlist_outreach_email(candidate_profile=profile, job=job)
        to_email = _extract_candidate_email(profile)
        outreach_repo = OutreachEventRepository(db)

        if not to_email:
            outreach_repo.upsert(
                job_id=job_id,
                candidate_id=candidate_id,
                provider=OUTREACH_PROVIDER,
                to_email="",
                subject=subject,
                body=email_template,
                status="manual_required",
                last_error="missing_email",
                sent_at=None,
                next_follow_up_at=None,
                provider_message_id=None,
            )
            db.commit()
            return {
                "success": True,
                "jobId": job_id,
                "candidateId": candidate_id,
                "candidateName": name,
                "candidateEmail": "",
                "linkedinUrl": linkedin_url,
                "status": "manual_required",
                "outreachStatus": "manual_required",
                "subject": subject,
                "html": email_template,
                "providerMessageId": "",
            }

        email_sent, send_error, msg_id = _send_shortlist_outreach_email(
            to_email=to_email,
            subject=subject,
            html_body=email_template,
        )
        now = datetime.now(timezone.utc)
        if not email_sent:
            outreach_repo.upsert(
                job_id=job_id,
                candidate_id=candidate_id,
                provider=OUTREACH_PROVIDER,
                to_email=to_email,
                subject=subject,
                body=email_template,
                status="failed",
                last_error=send_error or "provider_rejected",
                sent_at=None,
                next_follow_up_at=None,
                provider_message_id=None,
            )
            db.commit()
            raise APIError("Failed to send outreach email", status_code=502)

        outreach_repo.upsert(
            job_id=job_id,
            candidate_id=candidate_id,
            provider=OUTREACH_PROVIDER,
            to_email=to_email,
            subject=subject,
            body=email_template,
            status="sent",
            last_error="",
            sent_at=now,
            next_follow_up_at=_follow_up_time() if ENABLE_FOLLOWUPS else None,
            provider_message_id=msg_id or None,
        )
        db.commit()
        return {
            "success": True,
            "jobId": job_id,
            "candidateId": candidate_id,
            "candidateName": name,
            "candidateEmail": to_email,
            "linkedinUrl": linkedin_url,
            "status": "sent",
            "outreachStatus": "sent",
            "subject": subject,
            "html": email_template,
            "providerMessageId": msg_id,
        }


async def trigger_candidate_outreach(candidate_id: str, job_id: str, channel_id: str) -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(_trigger_candidate_outreach_sync, candidate_id=candidate_id, job_id=job_id)
        candidate_name = str(result.get("candidateName") or candidate_id).strip() or candidate_id
        status = str(result.get("status") or "").strip().lower()

        if status == "sent":
            await _safe_post_slack_message(
                channel_id=channel_id,
                text=f"📩 Outreach email sent to {candidate_name}",
            )
        elif status == "manual_required":
            linkedin_url = str(result.get("linkedinUrl") or "").strip()
            message = f"⚠️ No email available for {candidate_name}. Reach out via LinkedIn: {linkedin_url}"
            await _safe_post_slack_message(channel_id=channel_id, text=message)
        return result
    except APIError as exc:
        logger.error(
            "outreach_shortlist_pipeline_api_error job_id=%s candidate_id=%s error=%s",
            job_id,
            candidate_id,
            exc.message,
            exc_info=exc,
        )
        await _safe_post_slack_message(channel_id=channel_id, text="⚠️ Failed to send outreach email")
        raise
    except Exception as exc:
        logger.error(
            "outreach_shortlist_pipeline_failed job_id=%s candidate_id=%s error=%s",
            job_id,
            candidate_id,
            _error_debug_string(exc),
            exc_info=exc,
        )
        await _safe_post_slack_message(channel_id=channel_id, text="⚠️ Failed to send outreach email")
        raise APIError("Failed to send outreach email", status_code=502) from exc


def queue_outreach_delivery(*, job_id: str, selected_candidates: list[str], custom_body: str = "") -> dict:
    from threading import Thread

    def _worker() -> None:
        db = SessionLocal()
        try:
            process_outreach(db=db, job_id=job_id, selected_candidates=selected_candidates, custom_body=custom_body)
        except Exception as exc:
            logger.error("error_occurred outreach_queue_worker_failed job_id=%s error=%s", job_id, str(exc), exc_info=exc)
        finally:
            db.close()

    Thread(target=_worker, daemon=True).start()
    logger.info("request_started outreach_queued job_id=%s selected_count=%s", job_id, len(selected_candidates))
    return {"queued": True, "job_id": job_id, "selected_count": len(selected_candidates)}


# ── Follow-up CRON engine ────────────────────────────────────────────────────

def run_followup_cycle(db: Session) -> dict:
    """
    CRON-driven follow-up engine.
    Finds outreach events due for a follow-up and sends exactly one follow-up max.
    """
    if not ENABLE_FOLLOWUPS:
        logger.info("followup_skipped reason=disabled")
        return {"sent": 0, "skipped": 0, "total": 0}

    now = datetime.now(timezone.utc)
    outreach_repo = OutreachEventRepository(db)
    profile_repo = CandidateProfileRepository(db)
    job_repo = JobRepository(db)

    sent = skipped = 0
    provider_configured, _ = _is_email_provider_configured()

    with db.begin():
        due = outreach_repo.list_due_follow_ups_locked(now=now, max_follow_up_count=1)
        logger.info("followup_cycle_start due_count=%s", len(due))
        log_metric("followup_cycle_start", due_count=len(due))

        for event in due:
            logger.info("followup_claimed job_id=%s candidate_id=%s", event.job_id, event.candidate_id)

            job = job_repo.get(event.job_id)
            if not job:
                skipped += 1
                logger.warning("followup_skipped job_id=%s candidate_id=%s reason=job_missing", event.job_id, event.candidate_id)
                continue

            profile = profile_repo.get(job_id=event.job_id, candidate_id=event.candidate_id)
            if not profile:
                skipped += 1
                logger.warning("followup_skipped job_id=%s candidate_id=%s reason=profile_missing", event.job_id, event.candidate_id)
                logger.warning(
                    "invalid_candidate_reference_detected table=outreach_events job_id=%s candidate_id=%s",
                    event.job_id,
                    event.candidate_id,
                )
                continue

            follow_up_number = int(event.follow_up_count or 0) + 1
            subject, body = _build_followup_email(
                candidate_profile=profile, job=job, follow_up_number=follow_up_number
            )
            to_email = event.to_email

            if OUTREACH_DRY_RUN:
                outreach_repo.upsert(
                    job_id=event.job_id, candidate_id=event.candidate_id, provider=event.provider,
                    to_email=to_email, subject=subject, body=body, status="follow_up_sent",
                    sent_at=now, next_follow_up_at=None, increment_follow_up=True,
                )
                sent += 1
                logger.info(
                    "followup_sent job_id=%s candidate_id=%s follow_up_count=%s dry_run=%s",
                    event.job_id, event.candidate_id, follow_up_number,
                    True,
                )
                log_metric("followup_sent", job_id=event.job_id, candidate_id=event.candidate_id,
                           follow_up_count=follow_up_number, dry_run=True)
                continue

            if not provider_configured:
                skipped += 1
                logger.warning("followup_skipped job_id=%s candidate_id=%s reason=provider_not_configured", event.job_id, event.candidate_id)
                continue

            try:
                email_sent, send_error, msg_id = _send_outreach_email(to_email=to_email, subject=subject, body=body)
                if email_sent:
                    try:
                        outreach_repo.upsert(
                            job_id=event.job_id, candidate_id=event.candidate_id, provider=event.provider,
                            to_email=to_email, subject=subject, body=body, status="follow_up_sent",
                            sent_at=now, next_follow_up_at=None,
                            provider_message_id=msg_id, increment_follow_up=True,
                        )
                        db.commit()
                    except Exception as db_exc:
                        db.rollback()
                        logger.error(
                            "followup_finalize_failed job_id=%s candidate_id=%s provider_id=%s error=%s",
                            event.job_id,
                            event.candidate_id,
                            msg_id,
                            str(db_exc),
                            exc_info=db_exc,
                        )
                        skipped += 1
                        continue
                    sent += 1
                    logger.info(
                        "followup_sent job_id=%s candidate_id=%s follow_up_count=%s provider_id=%s",
                        event.job_id, event.candidate_id, follow_up_number, msg_id,
                    )
                    log_metric("followup_sent", job_id=event.job_id, candidate_id=event.candidate_id,
                               follow_up_count=follow_up_number, provider_id=msg_id)
                else:
                    skipped += 1
                    logger.warning(
                        "followup_failed job_id=%s candidate_id=%s error=%s",
                        event.job_id, event.candidate_id, send_error,
                    )
                    log_metric("followup_failed", job_id=event.job_id, candidate_id=event.candidate_id, error=send_error)
            except Exception as exc:
                skipped += 1
                logger.error(
                    "followup_failed job_id=%s candidate_id=%s error=%s",
                    event.job_id, event.candidate_id, _error_debug_string(exc), exc_info=exc,
                )
                log_metric("followup_failed", job_id=event.job_id, candidate_id=event.candidate_id, error=_error_debug_string(exc))

    logger.info("followup_cycle_complete sent=%s skipped=%s", sent, skipped)
    log_metric("followup_cycle_complete", sent=sent, skipped=skipped)
    return {"sent": sent, "skipped": skipped, "total": len(due)}


# ── Status / preview helpers ─────────────────────────────────────────────────

def list_outreach_status(*, db: Session, job_id: str) -> list[dict]:
    if not JobRepository(db).get(job_id):
        raise APIError("Job not found", status_code=404)
    rows = OutreachEventRepository(db).list_for_job(job_id)
    return [
        {
            "candidateId": row.candidate_id,
            "status": row.status,
            "provider": row.provider,
            "toEmail": row.to_email,
            "attemptCount": row.attempt_count,
            "followUpCount": row.follow_up_count,
            "providerMessageId": row.provider_message_id,
            "lastSentAt": row.last_sent_at.isoformat() if row.last_sent_at else None,
            "lastContactedAt": row.last_contacted_at.isoformat() if row.last_contacted_at else None,
            "nextFollowUpAt": row.next_follow_up_at.isoformat() if row.next_follow_up_at else None,
            "lastError": row.last_error,
        }
        for row in rows
    ]


def build_email_preview(*, db: Session, job_id: str, candidate_id: str) -> dict:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if not profile:
        raise APIError("Candidate not found", status_code=404)
    to_email = _extract_email(profile.raw_data or {})
    subject, body = generate_personalized_email(candidate_profile=profile, job=job)
    return {
        "subject": subject,
        "body": body,
        "toEmail": to_email,
        "usingFallbackEmail": not bool(to_email),
    }
