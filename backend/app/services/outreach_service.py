from __future__ import annotations

import asyncio
import hashlib
import html
import json
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
    GROQ_API_KEY,
    OUTREACH_DRY_RUN,
    OUTREACH_FROM_EMAIL,
    OUTREACH_REPLY_TO_EMAIL,
    OUTREACH_PROVIDER,
    OUTREACH_RESEND_FALLBACK_FROM_EMAIL,
    RESEND_API_KEY,
    PUBLIC_APP_URL,
)
from app.db.repositories import (
    CandidateFeedbackRepository,
    CandidateProfileRepository,
    CandidateSelectionSessionRepository,
    InterviewRepository,
    JobRepository,
    NotificationEventRepository,
    OutreachEventRepository,
    _candidate_email_value,
)
from app.db.session import SessionLocal
from app.models.entities import OutreachEventEntity
from app.services.lifecycle_service import record_job_lifecycle_event
from app.services.job_queue_service import enqueue_job
from app.services.llm_service import generate
from app.services.metrics_service import log_metric
from app.services.outreach_intelligence_service import (
    classify_reply_state,
    compute_outreach_engagement_snapshot,
    follow_up_delay_days,
    outreach_reply_state_to_ats_state,
    outreach_reply_state_to_notification_title,
    scheduled_reengagement_delay,
)
from app.services.ats_lifecycle_service import transition_candidate_ats_state
from app.services.prompt_sanitizer import sanitize_prompt_block, sanitize_prompt_text
from app.services.recruiter_preference_service import update_recruiter_preferences
from app.services.slack_integration import post_slack_message
from app.services.slack_service import notify_slack
from app.services.state_machine import assert_valid_transition
from app.services.redis_service import get_redis
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)
_EMAIL_PATTERN = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,63}$", re.IGNORECASE)
_DISPOSABLE_EMAIL_DOMAINS = {
    "mailinator.com",
    "10minutemail.com",
    "guerrillamail.com",
    "tempmail.com",
    "yopmail.com",
}
_SUPPRESSION_SET_KEY = "pontis:outreach:suppression:emails"
_DOMAIN_SUPPRESSION_KEY = "pontis:outreach:suppression:domains"
_RECRUITER_DAILY_QUOTA_PREFIX = "pontis:outreach:quota:"
_DOMAIN_REPUTATION_PREFIX = "pontis:outreach:domain:"
_OPEN_TRACKING_PREFIX = "pontis:outreach:open:"
_SPAM_RISK_THRESHOLD = 0.8
_DEFAULT_DAILY_OUTREACH_QUOTA = 50
_DEFAULT_DOMAIN_DAILY_QUOTA = 20
_INVALID_EMAIL_BLOCK_REASONS = {"invalid_email", "invalid_email_domain", "missing_email"}


def _notification_key(*, job_id: str, candidate_id: str, notification_type: str, suffix: str = "") -> str:
    material = {
        "jobId": job_id,
        "candidateId": candidate_id,
        "notificationType": notification_type,
        "suffix": suffix,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _record_notification(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    notification_type: str,
    recipient_type: str,
    recipient: str,
    channel: str,
    title: str,
    body: str,
    status: str = "queued",
    delivery_reference: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    job = JobRepository(db).get(job_id)
    company_id = str(getattr(job, "company_id", "") or "") if job else ""
    NotificationEventRepository(db).upsert(
        notification_key=_notification_key(
            job_id=job_id,
            candidate_id=candidate_id,
            notification_type=notification_type,
            suffix=delivery_reference or status,
        ),
        job_id=job_id,
        company_id=company_id or None,
        candidate_id=candidate_id,
        recipient_type=recipient_type,
        recipient=recipient,
        channel=channel,
        title=title,
        body=body,
        status=status,
        notification_type=notification_type,
        notification_metadata=metadata or {},
        delivery_reference=delivery_reference,
    )


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _extract_email_from_text(value: str) -> str:
    text = _normalize_text(value)
    if not text:
        return ""
    if _EMAIL_PATTERN.fullmatch(text):
        return text.lower()
    match = _EMAIL_PATTERN.search(text)
    return match.group(0).lower() if match else ""


def _email_domain(email: str) -> str:
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1].strip().lower()


def _is_blocked_outbound_email(email: str) -> tuple[bool, str]:
    normalized = _extract_email({"email": email})
    if not normalized:
        return True, "invalid_email"
    domain = _email_domain(normalized)
    if not domain:
        return True, "invalid_email_domain"
    if domain in _DISPOSABLE_EMAIL_DOMAINS:
        return True, "disposable_email_blocked"
    return False, ""


def _redis_client():
    return get_redis()


def _suppression_key(email: str) -> str:
    return (email or "").strip().lower()


def _is_suppressed(email: str) -> bool:
    normalized = _suppression_key(email)
    if not normalized:
        return False
    redis = _redis_client()
    if redis is None:
        return False
    try:
        return bool(redis.sismember(_SUPPRESSION_SET_KEY, normalized) or redis.sismember(_DOMAIN_SUPPRESSION_KEY, _email_domain(normalized)))
    except Exception:
        return False


def _suppress_email(email: str, *, reason: str = "suppressed") -> None:
    normalized = _suppression_key(email)
    if not normalized:
        return
    redis = _redis_client()
    if redis is None:
        return
    try:
        redis.sadd(_SUPPRESSION_SET_KEY, normalized)
        redis.hset(f"{_SUPPRESSION_SET_KEY}:meta", normalized, reason)
    except Exception as exc:
        logger.warning("outreach_suppression_write_failed email=%s error=%s", normalized, str(exc))


def _suppress_domain(domain: str, *, reason: str = "suppressed") -> None:
    normalized = (domain or "").strip().lower()
    if not normalized:
        return
    redis = _redis_client()
    if redis is None:
        return
    try:
        redis.sadd(_DOMAIN_SUPPRESSION_KEY, normalized)
        redis.hset(f"{_DOMAIN_SUPPRESSION_KEY}:meta", normalized, reason)
    except Exception as exc:
        logger.warning("outreach_domain_suppression_write_failed domain=%s error=%s", normalized, str(exc))


def _quota_key(prefix: str, scope: str) -> str:
    return f"{prefix}{scope}"


def _increment_daily_quota(prefix: str, scope: str, *, ttl_seconds: int = 86400) -> int:
    redis = _redis_client()
    if redis is None:
        return 1
    key = _quota_key(prefix, scope)
    try:
        count = int(redis.incr(key))
        if count == 1:
            redis.expire(key, ttl_seconds)
        return count
    except Exception:
        return 1


def _daily_quota_allowed(prefix: str, scope: str, *, limit: int) -> bool:
    redis = _redis_client()
    if redis is None:
        return True
    key = _quota_key(prefix, scope)
    try:
        current = int(redis.get(key) or 0)
        return current < limit
    except Exception:
        return True


def _spam_risk_score(*, subject: str, body: str, to_email: str) -> float:
    haystack = " ".join([subject, body, to_email]).lower()
    risk = 0.0
    if any(token in haystack for token in ("free", "urgent", "guarantee", "act now", "click here", "bonus")):
        risk += 0.35
    if haystack.count("!") > 2:
        risk += 0.15
    if len(body) < 60:
        risk += 0.15
    if len(body) > 1200:
        risk += 0.1
    if "unsubscribe" not in haystack:
        risk += 0.05
    return max(0.0, min(1.0, risk))


def _tracking_token(*, event_id: str, candidate_id: str, job_id: str) -> str:
    material = f"{event_id}:{candidate_id}:{job_id}:{OUTREACH_PROVIDER}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _candidate_ids_from_snapshot(snapshot: Any) -> set[str]:
    candidate_ids: set[str] = set()
    if not isinstance(snapshot, list):
        return candidate_ids
    for item in snapshot:
        if isinstance(item, dict):
            candidate_id = str(item.get("id") or item.get("candidateId") or item.get("candidate_id") or "").strip()
        else:
            candidate_id = str(getattr(item, "id", "") or getattr(item, "candidateId", "") or getattr(item, "candidate_id", "") or "").strip()
        if candidate_id:
            candidate_ids.add(candidate_id)
    return candidate_ids


def _append_open_tracking_pixel(*, html_body: str, event_id: str, candidate_id: str, job_id: str) -> str:
    token = _tracking_token(event_id=event_id, candidate_id=candidate_id, job_id=job_id)
    pixel_url = f"{PUBLIC_APP_URL}/api/backend/outreach/open?eventId={event_id}&token={token}"
    pixel = f'<img src="{html.escape(pixel_url)}" alt="" width="1" height="1" style="display:none" />'
    if "</body>" in html_body.lower():
        return re.sub(r"</body>", f"{pixel}</body>", html_body, flags=re.IGNORECASE)
    return f"{html_body}\n{pixel}"


# ── Email content helpers ────────────────────────────────────────────────────

def _text_to_html_paragraphs(text: str) -> str:
    blocks: list[str] = []
    for raw_block in re.split(r"\n\s*\n", (text or "").strip()):
        block = raw_block.strip()
        if not block:
            continue
        if block.startswith(("- ", "* ")):
            items = [
                f"<li style=\"margin:0 0 8px;\">{html.escape(line.strip()[2:])}</li>"
                for line in block.splitlines()
                if line.strip().startswith(("- ", "* "))
            ]
            if items:
                blocks.append(
                    "<ul style=\"margin:0 0 20px 20px;padding:0;font-size:16px;line-height:1.7;color:#334155;\">"
                    + "".join(items)
                    + "</ul>"
                )
                continue
        escaped_block = html.escape(block).replace("\n", "<br>")
        blocks.append(
            f'<p style="margin:0 0 18px;font-size:16px;line-height:1.8;color:#334155;">{escaped_block}</p>'
        )
    return "".join(blocks)


def _render_professional_outreach_html(*, subject: str, body: str, brand_name: str = "Pontis Talent", accent_label: str = "Hiring Update") -> str:
    rendered_body = _text_to_html_paragraphs(body)
    safe_subject = html.escape(subject)
    safe_brand = html.escape(brand_name)
    safe_accent = html.escape(accent_label)
    return f"""
<div style="margin:0;padding:0;background-color:#eef2f7;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:0;padding:0;background-color:#eef2f7;width:100%;">
    <tr>
      <td align="center" style="padding:36px 16px;">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="max-width:680px;background-color:#ffffff;border:1px solid #dbe3ee;border-radius:18px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#0f172a;box-shadow:0 10px 30px rgba(15,23,42,0.08);">
          <tr>
            <td style="padding:22px 30px;background:linear-gradient(135deg,#0f172a 0%,#172554 100%);color:#ffffff;">
              <div style="font-size:12px;letter-spacing:0.12em;text-transform:uppercase;font-weight:700;opacity:0.82;">{safe_brand}</div>
              <div style="margin-top:10px;font-size:28px;line-height:1.2;font-weight:700;">{safe_accent}</div>
              <div style="margin-top:8px;font-size:14px;line-height:1.6;opacity:0.88;">{safe_subject}</div>
            </td>
          </tr>
          <tr>
            <td style="padding:34px 30px 26px;">
              {rendered_body}
              <div style="margin:26px 0;padding:18px 20px;border:1px solid #dbe3ee;border-left:4px solid #0f172a;background-color:#f8fafc;border-radius:12px;">
                <div style="font-size:13px;letter-spacing:0.08em;text-transform:uppercase;font-weight:700;color:#475569;margin-bottom:6px;">Next step</div>
                <div style="font-size:16px;line-height:1.7;color:#0f172a;font-weight:600;">Reply with your updated resume if you're open to exploring the role. We can then arrange a short 15-minute conversation at your convenience.</div>
              </div>
              <p style="margin:0 0 12px;font-size:16px;line-height:1.8;color:#334155;">If you'd like to discuss the role, team, or process, just reply and we will be glad to help.</p>
              <p style="margin:28px 0 0;font-size:16px;line-height:1.8;color:#0f172a;">Best regards,<br><strong>{safe_brand} Team</strong></p>
            </td>
          </tr>
          <tr>
            <td style="padding:0 30px 26px;">
              <div style="border-top:1px solid #e2e8f0;padding-top:16px;font-size:12px;line-height:1.6;color:#64748b;">
                This message was sent by Pontis for candidate outreach and is intended to be concise and professional.
              </div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</div>
"""


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

    if not isinstance(raw, dict):
        return ""

    if bool(raw.get("is_mock_email")) or str(raw.get("email_source") or "").strip().lower() == "generated":
        return ""

    candidate_email = _candidate_email_value(raw)
    if not candidate_email:
        return ""

    normalized = _normalize_valid_email(candidate_email)
    if not normalized or normalized.endswith("@test.local"):
        return ""
    return normalized


def _extract_email_display(raw: dict[str, Any]) -> str:
    if not isinstance(raw, dict):
        return ""

    extracted = _candidate_email_value(raw)
    if extracted and not extracted.endswith("@test.local"):
        return extracted

    priority_keys = (
        "email",
        "work_email",
        "personal_email",
        "primary_email",
        "reply_email",
        "contact_email",
        "emails_primary",
    )
    array_keys = ("emails", "personal_emails", "work_emails")

    for key in priority_keys:
        value = raw.get(key)
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    text = item.strip()
                    if text:
                        return text
                if isinstance(item, dict):
                    for nested_key in ("email", "address", "value", "work_email", "personal_email", "primary_email"):
                        nested_value = item.get(nested_key)
                        if isinstance(nested_value, str):
                            text = nested_value.strip()
                            if text:
                                return text

    for key in array_keys:
        value = raw.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    text = item.strip()
                    if text:
                        return text
                if isinstance(item, dict):
                    extracted = _extract_email_display(item)
                    if extracted:
                        return extracted

    parsed_data = raw.get("parsed_data") or raw.get("parsedData")
    if isinstance(parsed_data, dict):
        extracted = _extract_email_display(parsed_data)
        if extracted:
            return extracted
    return ""


def _build_heuristic_email(*, candidate_profile, job) -> tuple[str, str]:
    """Deterministic template-based email — used when LLM is unavailable."""
    first_name = (candidate_profile.name or "").split()[0] if candidate_profile.name else "there"
    their_role = candidate_profile.role or "your background"
    their_company = candidate_profile.company
    skills = (candidate_profile.skills or [])[:3]
    hook = skills[0] if skills else their_role

    if _is_elite_job(job):
        company_name = _job_company_name(job)
        subject = f"Confidential leadership opportunity: {job.title} at {company_name}"
        body = (
            f"Dear {first_name},\n\n"
            f"I am reaching out on behalf of {company_name} regarding a selective search for {job.title}"
            f"{' in ' + job.location if getattr(job, 'location', '') else ''}.\n\n"
            f"Your background in {hook} appears closely aligned with the mandate, particularly for a role where judgment, ownership, and senior execution matter as much as technical depth.\n\n"
            "If the timing is appropriate, we would value the opportunity to share a concise overview and understand whether this aligns with your current priorities.\n\n"
            "If you are open to exploring, please reply with your updated resume and we will coordinate next steps discreetly.\n\n"
            "Best regards,\n"
            "Pontis Talent Team"
        )
        return subject, body

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
    Falls back to the heuristic template if the LLM is unavailable or fails.
    Prompts are deterministic — no hallucinated claims about the candidate.
    """
    if not GROQ_API_KEY:
        return _build_heuristic_email(candidate_profile=candidate_profile, job=job)

    try:
        skills_text = ", ".join((candidate_profile.skills or [])[:5]) or "not listed"
        elite = _is_elite_job(job)
        opening_instruction = (
            (
                "Write a polished, senior executive recruiting outreach email suitable for a CEO, CTO, VP, or principal-level leader.\n"
                "The tone should be discreet, respectful, concise, and high-trust.\n"
            )
            if elite
            else "Write a short, warm, personalized recruiting outreach email.\n"
        )
        prompt = (
            opening_instruction +
            "Rules:\n"
            f"- Max {'150' if elite else '120'} words in the body\n"
            "- Do NOT invent facts about the candidate beyond what is given\n"
            "- Do NOT use hype, pressure, or sales language\n"
            f"- Sound like a {'senior search partner' if elite else 'human recruiter'}, not a bot\n"
            "- Ask whether the candidate is open to the role\n"
            "- Ask the candidate to share an updated resume\n"
            f"- End with a {'discreet next-step offer' if elite else 'soft call-to-action for a 15-minute chat'}\n\n"
            f"{sanitize_prompt_block('Candidate name', candidate_profile.name or 'there', max_length=120)}\n"
            f"{sanitize_prompt_block('Candidate current role', candidate_profile.role or 'unknown', max_length=120)}\n"
            f"{sanitize_prompt_block('Candidate current company', candidate_profile.company or 'unknown', max_length=120)}\n"
            f"{sanitize_prompt_block('Candidate skills', skills_text, max_length=1000)}\n"
            f"{sanitize_prompt_block('Candidate summary', candidate_profile.summary or 'not listed', max_length=2000)}\n"
            f"{sanitize_prompt_block('Job title', job.title, max_length=120)}\n"
            f"{sanitize_prompt_block('Job location', job.location or 'flexible', max_length=120)}\n"
            f"{sanitize_prompt_block('Compensation', job.compensation or 'competitive', max_length=120)}\n\n"
            "Include one concrete hook tying a candidate skill or background to the role.\n"
            "Return ONLY:\n"
            "SUBJECT: <subject line>\n"
            "BODY:\n<email body>"
        )
        text = str(generate(prompt)).strip()

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


def _build_shortlist_outreach_email(*, candidate_profile, job) -> tuple[str, str, str]:
    first_name = html.escape(_candidate_first_name(candidate_profile))
    job_title_raw = (getattr(job, "title", "") or "").strip() or "the role"
    job_title = html.escape(job_title_raw)
    company_name_raw = _job_company_name(job)
    company_name = html.escape(company_name_raw)
    location_raw = (getattr(job, "location", "") or "").strip() or "flexible"
    location = html.escape(location_raw)
    if _is_elite_job(job):
        subject = f"Confidential opportunity: {job_title_raw} at {company_name_raw}"
        email_template = f"""
<div style="margin:0;padding:0;background-color:#eef2f7;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:0;padding:0;background-color:#eef2f7;width:100%;">
    <tr>
      <td align="center" style="padding:36px 16px;">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="max-width:680px;background-color:#ffffff;border:1px solid #d7dee8;border-radius:18px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#0f172a;box-shadow:0 18px 42px rgba(15,23,42,0.10);">
          <tr>
            <td style="padding:26px 32px;background:linear-gradient(135deg,#0b1220 0%,#1e293b 100%);color:#ffffff;">
              <div style="font-size:12px;letter-spacing:0.14em;text-transform:uppercase;font-weight:700;opacity:0.82;">Pontis Executive Search</div>
              <div style="margin-top:12px;font-size:28px;line-height:1.25;font-weight:700;">Confidential leadership opportunity</div>
              <div style="margin-top:8px;font-size:14px;line-height:1.6;opacity:0.86;">{job_title} at {company_name}</div>
            </td>
          </tr>
          <tr>
            <td style="padding:34px 32px;">
              <p style="margin:0 0 18px;font-size:16px;line-height:1.7;">Dear {first_name},</p>
              <p style="margin:0 0 18px;font-size:16px;line-height:1.8;">We are reaching out on behalf of <strong>{company_name}</strong> regarding a selective search for <strong>{job_title}</strong>{' in <strong>' + location + '</strong>' if location_raw else ''}.</p>
              <p style="margin:0 0 18px;font-size:16px;line-height:1.8;">Your profile stood out as closely aligned with the mandate, particularly for a role where judgment, ownership, and senior execution matter as much as technical depth.</p>
              <div style="margin:26px 0;padding:20px 22px;border:1px solid #d7dee8;border-left:4px solid #0b1220;background-color:#f8fafc;border-radius:12px;">
                <p style="margin:0;font-size:16px;line-height:1.8;font-weight:700;color:#0f172a;">If the timing is appropriate, please reply with your updated resume and we will coordinate next steps discreetly.</p>
              </div>
              <p style="margin:0 0 14px;font-size:16px;line-height:1.8;">We would be glad to share a concise overview and understand whether this aligns with your current priorities.</p>
              <p style="margin:28px 0 0;font-size:16px;line-height:1.8;">Best regards,<br><strong>Pontis Talent Team</strong></p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</div>
"""
        text_template = (
            f"Dear {_candidate_first_name(candidate_profile)},\n\n"
            f"We are reaching out on behalf of {company_name_raw} regarding a selective search for {job_title_raw}"
            f"{' in ' + location_raw if location_raw else ''}.\n\n"
            "Your profile stood out as closely aligned with the mandate, particularly for a role where judgment, ownership, and senior execution matter as much as technical depth.\n\n"
            "If the timing is appropriate, please reply with your updated resume and we will coordinate next steps discreetly.\n\n"
            "We would be glad to share a concise overview and understand whether this aligns with your current priorities.\n\n"
            "Best regards,\nPontis Talent Team"
        )
        return subject, email_template, text_template

    subject = f"Opportunity: {job_title_raw} at {company_name_raw}"

    intro = (
        f"We're reaching out about the <strong>{job_title}</strong> opportunity at <strong>{company_name}</strong> in <strong>{location}</strong>."
    )
    if (getattr(candidate_profile, "summary", "") or "").strip():
        intro += " Your background stood out to us, and we think there may be a strong fit."
    else:
        intro += " We think your profile may be a strong fit for the role."

    email_template = f"""
<div style="margin:0;padding:0;background-color:#f4f7fb;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:0;padding:0;background-color:#f4f7fb;width:100%;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="max-width:640px;background-color:#ffffff;border:1px solid #e5e7eb;border-radius:16px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#111827;">
          <tr>
            <td style="padding:24px 28px;background:#0f172a;color:#ffffff;">
              <div style="font-size:13px;letter-spacing:0.08em;text-transform:uppercase;font-weight:700;opacity:0.85;">Pontis Talent</div>
              <div style="margin-top:10px;font-size:28px;line-height:1.2;font-weight:700;">Opportunity update</div>
            </td>
          </tr>
          <tr>
            <td style="padding:32px 28px;">
              <p style="margin:0 0 16px;font-size:16px;line-height:1.6;">Hi {first_name},</p>
              <p style="margin:0 0 16px;font-size:16px;line-height:1.7;">{intro}</p>
              <div style="margin:24px 0;padding:18px 20px;border-left:4px solid #0f172a;background-color:#f8fafc;border-radius:10px;">
                <p style="margin:0;font-size:16px;line-height:1.7;font-weight:700;color:#0f172a;">If you are interested in exploring this opportunity, please reply to this email with your updated resume.</p>
              </div>
              <p style="margin:0 0 12px;font-size:16px;line-height:1.7;">If you'd like to learn more about the team, the role, or the process, just reply and we will be happy to help.</p>
              <p style="margin:24px 0 0;font-size:16px;line-height:1.7;">Best regards,<br><strong>Pontis Talent Team</strong></p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</div>
"""
    text_template = (
        f"Hi {_candidate_first_name(candidate_profile)},\n\n"
        f"We're reaching out about the {job_title_raw} opportunity at {company_name_raw} in {location_raw}.\n\n"
        + (
            "Your background stood out to us, and we think there may be a strong fit.\n\n"
            if (getattr(candidate_profile, "summary", "") or "").strip()
            else "We think your profile may be a strong fit for the role.\n\n"
        )
        + "If you are interested in exploring this opportunity, please reply to this email with your updated resume.\n\n"
        + "If you'd like to learn more about the team, the role, or the process, just reply and we will be happy to help.\n\n"
        + "Best regards,\nPontis Talent Team"
    )
    return subject, email_template, text_template


def _job_company_name(job) -> str:
    company = getattr(job, "company", None)
    company_name = getattr(company, "name", "") if company is not None else ""
    return (company_name or "your company").strip() or "your company"


def _is_elite_job(job) -> bool:
    return ((getattr(job, "vetting_mode", "") or getattr(job, "vettingMode", "") or "").strip().lower() == "elite")


def _candidate_first_name(candidate_profile) -> str:
    name = (getattr(candidate_profile, "name", "") or "").strip()
    if name:
        return name.split()[0]
    raw_data = _candidate_raw_data(candidate_profile)
    for key in ("full_name", "first_name", "name"):
        value = str(raw_data.get(key) or "").strip()
        if value:
            return value.split()[0]
    return "there"


def _candidate_raw_data(candidate_profile) -> dict[str, Any]:
    if isinstance(candidate_profile, dict):
        return candidate_profile
    raw_data = getattr(candidate_profile, "raw_data", None)
    return raw_data if isinstance(raw_data, dict) else {}


def _extract_candidate_email(candidate_profile) -> str:
    raw_data = _candidate_raw_data(candidate_profile)
    if bool(raw_data.get("is_mock_email")) or str(raw_data.get("email_source") or "").strip().lower() == "generated":
        return ""
    for key in ("work_email", "email", "personal_email", "emails_primary"):
        value = str(raw_data.get(key) or "").strip()
        if value:
            extracted = _extract_email_from_text(value)
            if extracted.endswith("@test.local"):
                return ""
            if extracted:
                return extracted

    for key in ("emails", "personal_emails", "work_emails"):
        value = raw_data.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    extracted = _extract_email_from_text(item)
                    if extracted and not extracted.endswith("@test.local"):
                        return extracted
                if isinstance(item, dict):
                    extracted = _extract_email_from_text(str(item.get("address") or item.get("email") or ""))
                    if extracted and not extracted.endswith("@test.local"):
                        return extracted

    for key in ("raw_resume_text", "rawResumeText"):
        value = str(raw_data.get(key) or "").strip()
        if value:
            extracted = _extract_email_from_text(value)
            if extracted and not extracted.endswith("@test.local"):
                return extracted

    parsed_data = raw_data.get("parsed_data") or raw_data.get("parsedData")
    if isinstance(parsed_data, dict):
        extracted = _extract_candidate_email(parsed_data)
        if extracted:
            return extracted
    return ""


def _resolve_outreach_recipient(*, raw_data: dict[str, Any], recipient_email: str = "") -> dict[str, Any]:
    candidate_email = _extract_email(raw_data)
    candidate_email_display = _extract_email_display(raw_data)
    blocked = True
    block_reason = "missing_email"
    if candidate_email:
        blocked, block_reason = _is_blocked_outbound_email(candidate_email)
        if not blocked:
            return {
                "to_email": candidate_email,
                "original_email": candidate_email_display or candidate_email,
                "manual_required": False,
                "reason": "",
            }

    manual_email = _extract_email_from_text(recipient_email)
    if manual_email:
        manual_blocked, manual_reason = _is_blocked_outbound_email(manual_email)
        if not manual_blocked:
            return {
                "to_email": manual_email,
                "original_email": candidate_email_display or candidate_email or manual_email,
                "manual_required": False,
                "reason": "",
                "override_used": True,
            }
        return {
            "to_email": "",
            "original_email": candidate_email_display or candidate_email or manual_email,
            "manual_required": True,
            "reason": manual_reason or "invalid_manual_email",
        }

    return {
        "to_email": "",
        "original_email": candidate_email_display or candidate_email,
        "manual_required": True,
        "reason": block_reason,
    }


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


def _shortlist_bcc_recipients(*, main_recipient: str | None = None) -> list[str]:
    return []


def _send_shortlist_outreach_email(*, to_email: str, subject: str, html_body: str, text_body: str) -> tuple[bool, str, str]:
    if not RESEND_API_KEY:
        return False, "RESEND_API_KEY_missing", ""

    bcc_recipients = _shortlist_bcc_recipients(main_recipient=to_email)
    try:
        import resend

        resend.api_key = RESEND_API_KEY
        payload: dict[str, Any] = {
            "from": OUTREACH_FROM_EMAIL,
            "reply_to": OUTREACH_REPLY_TO_EMAIL,
            "to": [to_email],
            "subject": subject,
            "html": html_body,
            "text": text_body,
            "tags": {"product": "pontis", "flow": "outreach_shortlist"},
        }
        if bcc_recipients:
            payload["bcc"] = bcc_recipients
        response = resend.emails.send(payload)
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

def _send_resend(*, to_email: str, subject: str, body: str, from_email: str, html_body: str | None = None) -> tuple[bool, str, str]:
    """Returns (success, error_message, provider_message_id)."""
    if not RESEND_API_KEY:
        return False, "RESEND_API_KEY_missing", ""

    bcc_recipients = _shortlist_bcc_recipients(main_recipient=to_email)
    try:
        import resend
        resend.api_key = RESEND_API_KEY
        payload: dict[str, Any] = {
            "from": from_email,
            "reply_to": OUTREACH_REPLY_TO_EMAIL,
            "to": [to_email],
            "subject": subject,
            "text": body,
            "tags": {"product": "pontis", "flow": "outreach"},
        }
        if html_body:
            payload["html"] = html_body
        if bcc_recipients:
            payload["bcc"] = bcc_recipients
        response = resend.Emails.send(payload)
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
        bcc_recipients = _shortlist_bcc_recipients(main_recipient=to_email)
        payload: dict[str, Any] = {
            "from": from_email,
            "reply_to": OUTREACH_REPLY_TO_EMAIL,
            "to": [to_email],
            "subject": subject,
            "text": body,
            "tags": {"product": "pontis", "flow": "outreach"},
        }
        if html_body:
            payload["html"] = html_body
        if bcc_recipients:
            payload["bcc"] = bcc_recipients
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json=payload,
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


def _send_outreach_email(*, to_email: str, subject: str, body: str, html_body: str | None = None) -> tuple[bool, str, str]:
    """Returns (success, error, provider_message_id)."""
    rendered_html = html_body or _render_professional_outreach_html(subject=subject, body=body)
    ok, error, msg_id = _send_resend(
        to_email=to_email,
        subject=subject,
        body=body,
        from_email=OUTREACH_FROM_EMAIL,
        html_body=rendered_html,
    )
    if ok:
        return True, "", msg_id

    fallback_from = OUTREACH_RESEND_FALLBACK_FROM_EMAIL.strip()
    if fallback_from and fallback_from.lower() != OUTREACH_FROM_EMAIL.lower():
        logger.warning("resend_retry_fallback_from to=%s fallback=%s", to_email, fallback_from)
        ok2, error2, msg_id2 = _send_resend(
            to_email=to_email,
            subject=subject,
            body=body,
            from_email=fallback_from,
            html_body=rendered_html,
        )
        if ok2:
            return True, "", msg_id2
        return False, f"{error}; retry={error2}", ""

    return False, error, ""


def _follow_up_time() -> datetime:
    # Day 0 -> 48 hour reminder cadence.
    return datetime.now(timezone.utc) + timedelta(days=2)


def _next_follow_up_time(*, follow_up_count_after_send: int, base_time: datetime | None = None) -> datetime | None:
    delay_days = follow_up_delay_days(follow_up_count_after_send=follow_up_count_after_send)
    if delay_days is None:
        return None
    reference_time = base_time or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    return reference_time + timedelta(days=delay_days)


def _detect_reply_intent(raw_event: dict[str, Any]) -> str:
    body = _normalize_text(raw_event.get("body") or raw_event.get("text") or raw_event.get("snippet") or "")
    lowered = body.lower()
    if any(token in lowered for token in ("unsubscribe", "remove me", "stop emailing", "do not contact")):
        return "unsubscribe"
    if any(token in lowered for token in ("not interested", "no thanks", "pass", "decline", "not looking")):
        return "not_interested"
    if any(token in lowered for token in ("tell me more", "share details", "salary", "compensation", "job description")):
        return "needs_more_info"
    if any(token in lowered for token in ("interested", "sounds good", "open to discuss", "happy to proceed", "keen to explore", "yes")):
        return "interested"
    return "ambiguous"


def _detect_bounce_or_unsubscribe(raw_event: dict[str, Any]) -> str:
    body = _normalize_text(raw_event.get("body") or raw_event.get("text") or raw_event.get("snippet") or "")
    subject = _normalize_text(raw_event.get("subject") or "")
    lowered = f"{subject} {body}".lower()
    if any(token in lowered for token in ("bounce", "undelivered", "delivery failed", "mail delivery", "message rejected")):
        return "bounced"
    if any(token in lowered for token in ("unsubscribe", "stop", "do not contact", "do not email")):
        return "unsubscribed"
    return ""


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
            row = repo.find_latest_by_email(email_from)

        if not row and job_id and candidate_id:
            logger.info("fallback_used reply_lookup=job_candidate")
            row = repo.get(job_id=job_id, candidate_id=candidate_id)

        if not row:
            logger.warning("error_occurred reply_mapping_failed email_from=%s provider_message_id=%s", email_from, provider_message_id)
            return {"status": "ignored"}

        if (row.status or "").strip().lower() == "replied":
            logger.info("result_returned reply_already_replied job_id=%s candidate_id=%s", row.job_id, row.candidate_id)
            return {"status": "ignored"}

        reply_state = classify_reply_state(text=body, subject=subject, raw_event={**raw_event, **nested_event, **provider_event})
        lifecycle_event = _detect_bounce_or_unsubscribe({**raw_event, **nested_event, **provider_event, "from": email_from, "subject": subject, "body": body, "text": body})

        now = datetime.now(timezone.utc)
        row.status = "replied"
        row.reply_state = reply_state.lower()
        row.reply_intent = row.reply_state
        row.reply_count = int(row.reply_count or 0) + 1
        row.last_contacted_at = now
        row.last_replied_at = now
        row.responded_at = row.responded_at or now
        row.last_error = ""
        row.archive_reason = ""
        row.next_follow_up_at = None

        interview_repo = InterviewRepository(db)
        interview_row = interview_repo.get_by_job_and_candidate(row.job_id, row.candidate_id)
        if interview_row:
            interview_row.status = "replied"

        recruiter_id = JobRepository(db).get_recruiter_id(row.job_id)
        candidate_profile = CandidateProfileRepository(db).get(job_id=row.job_id, candidate_id=row.candidate_id)
        if lifecycle_event == "bounced":
            row.status = "bounced"
            row.reply_state = "invalid_contact"
            row.reply_intent = row.reply_state
            row.archive_reason = "delivery_bounce_detected"
            row.last_error = "delivery_bounce_detected"
            if row.to_email:
                _suppress_email(row.to_email, reason="bounce")
                _suppress_domain(_email_domain(row.to_email), reason="bounce")
        elif lifecycle_event == "unsubscribed":
            row.status = "unsubscribed"
            row.reply_state = "invalid_contact"
            row.reply_intent = row.reply_state
            row.archive_reason = "unsubscribe_detected"
            row.last_error = "unsubscribe_detected"
            if row.to_email:
                _suppress_email(row.to_email, reason="unsubscribe")
                _suppress_domain(_email_domain(row.to_email), reason="unsubscribe")
        elif row.reply_state == "invalid_contact":
            row.status = "archived"
            row.archive_reason = "invalid_contact"
            row.last_error = "invalid_contact"
            if row.to_email:
                _suppress_email(row.to_email, reason="invalid_contact")

        if recruiter_id and candidate_profile:
            normalized_reply_state = row.reply_state
            if normalized_reply_state == "interested":
                update_recruiter_preferences(db, recruiter_id, candidate_profile, [], signal_multiplier=2.5)
            elif normalized_reply_state in {"need_more_info", "asked_to_follow_up_later", "out_of_office"}:
                update_recruiter_preferences(db, recruiter_id, candidate_profile, [], signal_multiplier=1.2)
            elif normalized_reply_state in {"not_interested", "negative_response"}:
                update_recruiter_preferences(db, recruiter_id, None, [candidate_profile], signal_multiplier=0.5)
            else:
                update_recruiter_preferences(db, recruiter_id, candidate_profile, [], signal_multiplier=1.0)

        ats_target = outreach_reply_state_to_ats_state(row.reply_state)
        if lifecycle_event in {"bounced", "unsubscribed"}:
            ats_target = "archived"
        transition_candidate_ats_state(
            db=db,
            job_id=row.job_id,
            candidate_id=row.candidate_id,
            to_status=ats_target,
            source="outreach_reply",
            actor_id=recruiter_id,
            reason=row.reply_state or lifecycle_event or "reply_received",
            metadata={
                "replyState": row.reply_state,
                "intent": row.reply_state,
                "lifecycleEvent": lifecycle_event,
                "providerMessageId": provider_message_id or (row.provider_message_id or ""),
            },
        )

        snapshot = compute_outreach_engagement_snapshot(
            status=row.status,
            reply_state=row.reply_state,
            open_count=row.open_count,
            reply_count=row.reply_count,
            follow_up_count=row.follow_up_count,
            sent_at=row.sent_at,
            responded_at=row.responded_at,
            last_opened_at=row.last_opened_at,
            last_replied_at=row.last_replied_at,
        )
        row.engagement_score = snapshot.engagement_score
        row.reply_likelihood_score = snapshot.reply_likelihood_score
        row.responsiveness_score = snapshot.responsiveness_score
        if snapshot.archive_reason and not row.archive_reason:
            row.archive_reason = snapshot.archive_reason

        if row.reply_state == "asked_to_follow_up_later":
            delay_days = scheduled_reengagement_delay(reply_state=row.reply_state)
            if delay_days:
                row.next_follow_up_at = now + timedelta(days=delay_days)
        elif row.reply_state == "out_of_office":
            delay_days = scheduled_reengagement_delay(reply_state=row.reply_state)
            if delay_days:
                row.next_follow_up_at = now + timedelta(days=delay_days)

        candidate_name = _candidate_profile_display_name(candidate_profile, email_from)
        reply_title = outreach_reply_state_to_notification_title(row.reply_state, candidate_name=candidate_name)
        reply_body = f"State: {row.reply_state or 'unknown'}"
        if row.reply_state == "asked_to_follow_up_later" and row.next_follow_up_at:
            reply_body += f" | Reconnect after {row.next_follow_up_at.date().isoformat()}"
        elif row.reply_state == "out_of_office" and row.next_follow_up_at:
            reply_body += f" | Reconnect after {row.next_follow_up_at.date().isoformat()}"
        elif row.reply_state == "interested":
            reply_body += " | Interview-ready candidate waiting"
        elif row.reply_state in {"not_interested", "negative_response"}:
            reply_body += " | Candidate declined"
        elif row.reply_state == "invalid_contact":
            reply_body += " | Contact details need correction"

        _record_notification(
            db=db,
            job_id=row.job_id,
            candidate_id=row.candidate_id,
            notification_type="candidate_reply",
            recipient_type="recruiter",
            recipient=str(recruiter_id or ""),
            channel="slack",
            title=reply_title,
            body=reply_body,
            status="delivered",
            metadata={
                "replyState": row.reply_state,
                "providerMessageId": provider_message_id or (row.provider_message_id or ""),
                "replyStatus": row.status,
                "engagementScore": row.engagement_score,
                "replyLikelihoodScore": row.reply_likelihood_score,
                "responsivenessScore": row.responsiveness_score,
            },
        )

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
            row.reply_state,
        )
        record_job_lifecycle_event(
            db=db,
            job_id=row.job_id,
            event_type="OUTREACH_REPLIED",
            payload={
                "jobId": row.job_id,
                "candidateId": row.candidate_id,
                "providerMessageId": provider_message_id or (row.provider_message_id or ""),
                "intent": row.reply_state,
                "status": row.status,
            },
            source="outreach",
        )
        if row.reply_state == "interested":
            try:
                from app.services.interview_session_service import create_interview_session

                session = create_interview_session(
                    db=db,
                    job_id=row.job_id,
                    candidate_id=row.candidate_id,
                    outreach_event_id=row.id,
                    source_app="adam",
                )
                booking_link = session.get("slot_link", session.get("slotLink", session.get("bookingLink", session.get("bookingUrl", ""))))
                transition_candidate_ats_state(
                    db=db,
                    job_id=row.job_id,
                    candidate_id=row.candidate_id,
                    to_status="interview_requested",
                    source="outreach_reply",
                    actor_id=recruiter_id,
                    reason="interview_session_requested",
                    metadata={"interviewToken": session.get("token", ""), "bookingLink": booking_link},
                )
                logger.info(
                    "decision_taken interview_session_created job_id=%s candidate_id=%s token=%s booking_url=%s",
                    row.job_id,
                    row.candidate_id,
                    session.get("token", ""),
                    booking_link,
                )
            except Exception as exc:
                logger.warning(
                    "fallback_used interview_session_creation_failed job_id=%s candidate_id=%s error=%s",
                    row.job_id,
                    row.candidate_id,
                    str(exc),
                    exc_info=exc,
                )
        if row.reply_state in {"asked_to_follow_up_later", "out_of_office"}:
            try:
                from app.services.automation_service import schedule_automation_job

                reminder_days = scheduled_reengagement_delay(reply_state=row.reply_state) or 30
                schedule_automation_job(
                    db=db,
                    automation_type="recruiter_reminder",
                    job_id=row.job_id,
                    candidate_id=row.candidate_id,
                    run_at=now + timedelta(days=reminder_days),
                    payload={
                        "outreachEventId": row.id,
                        "replyState": row.reply_state,
                        "reason": "candidate_reconnect_requested",
                    },
                )
            except Exception as exc:
                logger.warning(
                    "reengagement_schedule_failed job_id=%s candidate_id=%s error=%s",
                    row.job_id,
                    row.candidate_id,
                    str(exc),
                    exc_info=exc,
                )
        log_metric("reply_received", job_id=row.job_id, candidate_id=row.candidate_id, intent=row.reply_state)
        return {
            "status": "replied",
            "job_id": row.job_id,
            "candidate_id": row.candidate_id,
            "provider_message_id": provider_message_id or (row.provider_message_id or ""),
            "intent": row.reply_state,
            "replyState": row.reply_state,
        }
    except Exception as exc:
        logger.error("error_occurred reply_handler_exception error=%s", str(exc), exc_info=exc)
        raise


def record_outreach_open(*, db: Session, event_id: str, token: str) -> dict[str, Any]:
    repo = OutreachEventRepository(db)
    row = repo.get_by_id(event_id)
    if not row:
        return {"status": "ignored"}

    expected_token = _tracking_token(event_id=str(row.id), candidate_id=row.candidate_id, job_id=row.job_id)
    if not token or token != expected_token:
        return {"status": "ignored"}

    if (row.status or "").strip().lower() not in {"bounced", "unsubscribed"}:
        row.status = "opened"
    row.open_count = int(row.open_count or 0) + 1
    row.last_contacted_at = datetime.now(timezone.utc)
    row.last_opened_at = row.last_contacted_at
    row.last_error = ""
    snapshot = compute_outreach_engagement_snapshot(
        status=row.status,
        reply_state=row.reply_state,
        open_count=row.open_count,
        reply_count=row.reply_count,
        follow_up_count=row.follow_up_count,
        sent_at=row.sent_at,
        responded_at=row.responded_at,
        last_opened_at=row.last_opened_at,
        last_replied_at=row.last_replied_at,
    )
    row.engagement_score = snapshot.engagement_score
    row.reply_likelihood_score = snapshot.reply_likelihood_score
    row.responsiveness_score = snapshot.responsiveness_score
    db.commit()
    log_metric("open_tracked", job_id=row.job_id, candidate_id=row.candidate_id, event_id=row.id)
    logger.info("outreach_open_tracked job_id=%s candidate_id=%s event_id=%s", row.job_id, row.candidate_id, row.id)
    return {"status": "opened", "job_id": row.job_id, "candidate_id": row.candidate_id, "event_id": row.id}

# ── Main outreach process ────────────────────────────────────────────────────

def process_outreach(
    *, db: Session, job_id: str, selected_candidates: list[str], custom_body: str = "", recipient_email: str = ""
) -> dict:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    profiles = CandidateProfileRepository(db)
    selection_sessions = CandidateSelectionSessionRepository(db)
    feedback_repo = CandidateFeedbackRepository(db)
    interviews = InterviewRepository(db)
    outreach_events = OutreachEventRepository(db)
    recruiter_id = JobRepository(db).get_recruiter_id(job_id)

    # ── Enforce selected-only ────────────────────────────────────────────────
    session = selection_sessions.get_by_job(job_id)
    session_status = ""
    session_selected_candidate_ids: list[str] = []
    session_final_candidate_ids: set[str] = set()
    if session:
        session_status = (session.status or "").strip().lower()
        session_selected_candidate_ids = [
            str(candidate_id).strip()
            for candidate_id in (session.selected_candidate_ids or [])
            if str(candidate_id).strip()
        ]
        session_final_candidate_ids = _candidate_ids_from_snapshot(getattr(session, "final_candidate_snapshot", None))
        logger.info(
            "outreach_selection_session_loaded job_id=%s session_id=%s status=%s selected_count=%s final_count=%s",
            job_id,
            session.id,
            session_status,
            len(session_selected_candidate_ids),
            len(session_final_candidate_ids),
        )
        if session_status != "completed":
            logger.warning(
                "outreach_selection_session_not_completed job_id=%s session_id=%s status=%s",
                job_id,
                session.id,
                session_status,
            )
    else:
        logger.info("outreach_selection_session_missing job_id=%s", job_id)

    if not session:
        raise APIError(
            "Outreach is selection-driven. Complete the current candidate selection session before sending outreach.",
            status_code=409,
        )
    if session_status != "completed":
        logger.info(
            "outreach_session_not_completed job_id=%s session_id=%s status=%s allowing_enrichment_pipeline=%s",
            job_id,
            session.id,
            session_status,
            True,
        )

    unique_selected_candidates = list(dict.fromkeys((candidate_id or "").strip() for candidate_id in selected_candidates if str(candidate_id or "").strip()))
    if not unique_selected_candidates and session_selected_candidate_ids:
        if len(session_selected_candidate_ids) == 1:
            unique_selected_candidates = list(session_selected_candidate_ids)
        else:
            logger.warning(
                "outreach_session_has_multiple_selected_candidates job_id=%s session_id=%s selected_count=%s",
                job_id,
                session.id if session else "",
                len(session_selected_candidate_ids),
            )
    if not unique_selected_candidates:
        raise APIError(
            "No selected candidate available for outreach. Select exactly one candidate before sending outreach.",
            status_code=400,
        )
    if len(unique_selected_candidates) != 1:
        raise APIError(
            "Outreach is now selection-driven and must target exactly one candidate.",
            status_code=400,
        )
    selected_candidate_id = unique_selected_candidates[0]
    feedback = feedback_repo.get(job_id=job_id, candidate_id=selected_candidate_id)
    feedback_session_id = str(getattr(feedback, "session_id", "") or "").strip()
    feedback_value = str(getattr(feedback, "feedback", "") or "").strip().lower()
    has_current_session_feedback = feedback_session_id == str(session.id) and feedback_value == "accept"
    is_current_session_selected = (
        selected_candidate_id in session_selected_candidate_ids
        or (selected_candidate_id in session_final_candidate_ids and has_current_session_feedback)
    )
    if not is_current_session_selected:
        logger.warning(
            "outreach_candidate_not_current_session_selection job_id=%s session_id=%s candidate_id=%s feedback_session_id=%s feedback=%s",
            job_id,
            session.id,
            selected_candidate_id,
            feedback_session_id,
            feedback_value,
        )
        raise APIError(
            "Selected candidate is not part of the current completed selection session.",
            status_code=409,
        )

    candidate_profiles = profiles.latest_by_candidate_ids(job_id=job_id, candidate_ids=unique_selected_candidates)
    valid_candidates = [candidate_id for candidate_id in unique_selected_candidates if candidate_id in candidate_profiles]
    rejected_count = len(unique_selected_candidates) - len(valid_candidates)

    for candidate_id in unique_selected_candidates:
        if candidate_id not in candidate_profiles:
            logger.warning(
                "outreach_profile_missing job_id=%s candidate_id=%s",
                job_id,
                candidate_id,
            )

    selected_profile = candidate_profiles.get(valid_candidates[0])
    if not selected_profile:
        raise APIError("Selected candidate profile is missing", status_code=404)
    raw_data = dict(getattr(selected_profile, "raw_data", {}) or {})
    enrichment_state = raw_data.get("enrichment") if isinstance(raw_data.get("enrichment"), dict) else {}
    enrichment_status = str(enrichment_state.get("status") or getattr(selected_profile, "ats_status", "") or "").strip().lower()
    if enrichment_status not in {"verified", "high_confidence"}:
        logger.warning(
            "outreach_enrichment_gate_blocked job_id=%s candidate_id=%s status=%s",
            job_id,
            valid_candidates[0],
            enrichment_status or "missing",
        )
        raise APIError("Outreach is only allowed after verified or high-confidence candidate enrichment.", status_code=409)

    logger.info(
        "outreach_candidates job_id=%s selected=%s rejected_non_selected=%s",
        job_id, len(valid_candidates), rejected_count,
    )
    log_metric("outreach_candidates", job_id=job_id, selected=len(valid_candidates), rejected=rejected_count)

    if not valid_candidates:
        raise APIError(
            "No selected candidate available for outreach. Select exactly one candidate before sending outreach.",
            status_code=400,
        )

    existing_outreach = outreach_events.get(job_id=job_id, candidate_id=valid_candidates[0])
    if existing_outreach and (existing_outreach.status or "").strip().lower() in {
        "queued",
        "sending",
        "sent",
        "simulated",
        "delivered",
        "follow_up_sent",
        "replied",
        "archived",
    }:
        logger.info(
            "outreach_duplicate_suppressed job_id=%s candidate_id=%s status=%s",
            job_id,
            valid_candidates[0],
            existing_outreach.status,
        )
        return {
            "processed": 0,
            "sent": 0,
            "skipped": 0,
            "followUpScheduled": 0,
            "details": [
                {
                    "candidateId": valid_candidates[0],
                    "status": existing_outreach.status,
                    "reason": "duplicate_suppressed",
                    "toEmail": existing_outreach.to_email,
                }
            ],
            "skipReasons": {"duplicate_suppressed": 1},
            "warnings": [],
            "duplicate": True,
        }

    recipient_email = recipient_email.strip()
    if recipient_email and len(valid_candidates) != 1:
        raise APIError("recipientEmail can only be provided when sending outreach to one candidate.", status_code=400)

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
        profile = candidate_profiles[candidate_id]
        raw_data = profile.raw_data or {}
        enrichment_state = dict(raw_data.get("enrichment") or {})
        enrichment_status = str(enrichment_state.get("status") or "").strip().lower()
        if enrichment_status not in {"verified", "high_confidence"}:
            raise APIError(
                "Candidate must be verified or high-confidence enriched before outreach can be sent.",
                status_code=409,
            )
        delivery_target = _resolve_outreach_recipient(
            raw_data=raw_data,
            recipient_email=recipient_email if recipient_email and len(valid_candidates) == 1 else "",
        )
        original_to_email = str(delivery_target.get("original_email") or "").strip()
        to_email = str(delivery_target.get("to_email") or "").strip()
        manual_required = bool(delivery_target.get("manual_required"))
        reason = str(delivery_target.get("reason") or "").strip()

        if not to_email:
            skipped += 1
            blocked_reason = reason or "invalid_or_missing_email"
            skip_reasons[blocked_reason] = skip_reasons.get(blocked_reason, 0) + 1
            logger.warning(
                "outreach_email_blocked job_id=%s candidate_id=%s reason=%s email=%s",
                job_id,
                candidate_id,
                blocked_reason,
                original_to_email,
            )
            details.append(
                {
                    "candidateId": candidate_id,
                    "status": "manual_required" if manual_required else "skipped",
                    "reason": blocked_reason,
                    "toEmail": original_to_email,
                    "originalEmail": original_to_email,
                }
            )
            skipped_candidates.append({"candidateId": candidate_id, "reason": blocked_reason})
            outreach_events.upsert(
                job_id=job_id,
                candidate_id=candidate_id,
                provider=OUTREACH_PROVIDER,
                to_email=original_to_email,
                subject="",
                body="",
                status="manual_required" if manual_required else "failed",
                last_error=blocked_reason,
            )
            continue

        if original_to_email and _is_suppressed(original_to_email):
            skipped += 1
            blocked_reason = "suppressed"
            skip_reasons[blocked_reason] = skip_reasons.get(blocked_reason, 0) + 1
            details.append(
                {
                    "candidateId": candidate_id,
                    "status": "skipped",
                    "reason": blocked_reason,
                    "toEmail": original_to_email,
                    "originalEmail": original_to_email,
                }
            )
            skipped_candidates.append({"candidateId": candidate_id, "reason": blocked_reason})
            outreach_events.upsert(
                job_id=job_id,
                candidate_id=candidate_id,
                provider=OUTREACH_PROVIDER,
                to_email=original_to_email,
                subject="",
                body="",
                status="failed",
                last_error=blocked_reason,
            )
            continue

        blocked, block_reason = _is_blocked_outbound_email(to_email)
        if blocked:
            skipped += 1
            blocked_reason = block_reason or "invalid_email"
            skip_reasons[blocked_reason] = skip_reasons.get(blocked_reason, 0) + 1
            logger.warning(
                "outreach_email_blocked job_id=%s candidate_id=%s reason=%s email=%s",
                job_id,
                candidate_id,
                blocked_reason,
                to_email,
            )
            details.append(
                {
                    "candidateId": candidate_id,
                    "status": "manual_required" if manual_required else "skipped",
                    "reason": blocked_reason,
                    "toEmail": original_to_email or to_email,
                    "originalEmail": original_to_email,
                }
            )
            skipped_candidates.append({"candidateId": candidate_id, "reason": blocked_reason})
            outreach_events.upsert(
                job_id=job_id,
                candidate_id=candidate_id,
                provider=OUTREACH_PROVIDER,
                to_email=original_to_email or to_email,
                subject="",
                body="",
                status="manual_required" if manual_required else "failed",
                last_error=blocked_reason,
            )
            continue

        if custom_body.strip():
            subject, _ = generate_personalized_email(candidate_profile=profile, job=job)
            body = custom_body.strip()
        else:
            subject, body = generate_personalized_email(candidate_profile=profile, job=job)

        if recruiter_id and not _daily_quota_allowed(_RECRUITER_DAILY_QUOTA_PREFIX, recruiter_id, limit=_DEFAULT_DAILY_OUTREACH_QUOTA):
            skipped += 1
            blocked_reason = "recruiter_quota_exceeded"
            skip_reasons[blocked_reason] = skip_reasons.get(blocked_reason, 0) + 1
            details.append({"candidateId": candidate_id, "status": "skipped", "reason": blocked_reason, "toEmail": to_email})
            skipped_candidates.append({"candidateId": candidate_id, "reason": blocked_reason})
            continue

        spam_risk = _spam_risk_score(subject=subject, body=body, to_email=to_email)
        if spam_risk >= _SPAM_RISK_THRESHOLD:
            skipped += 1
            blocked_reason = "spam_risk_high"
            skip_reasons[blocked_reason] = skip_reasons.get(blocked_reason, 0) + 1
            details.append({"candidateId": candidate_id, "status": "skipped", "reason": blocked_reason, "toEmail": to_email})
            skipped_candidates.append({"candidateId": candidate_id, "reason": blocked_reason})
            outreach_events.upsert(
                job_id=job_id,
                candidate_id=candidate_id,
                provider=OUTREACH_PROVIDER,
                to_email=to_email,
                subject=subject,
                body=body,
                status="failed",
                last_error=blocked_reason,
            )
            continue

        try:
            assert_valid_transition(
                candidate_id=candidate_id,
                job_id=job_id,
                from_status=current_status,
            to_status="outreach_sent",
            )
        except APIError as exc:
            skipped += 1
            blocked_reason = f"invalid_state_transition:{exc.message}"
            skip_reasons[blocked_reason] = skip_reasons.get(blocked_reason, 0) + 1
            logger.warning(
                "outreach_invalid_transition_blocked job_id=%s candidate_id=%s current_status=%s reason=%s",
                job_id,
                candidate_id,
                current_status,
                blocked_reason,
            )
            details.append({"candidateId": candidate_id, "status": "skipped", "reason": blocked_reason, "toEmail": to_email})
            skipped_candidates.append({"candidateId": candidate_id, "reason": blocked_reason})
            continue

        simulate_send = OUTREACH_DRY_RUN or to_email.endswith("@test.local") or not ENABLE_REAL_EMAIL_SENDING
        if simulate_send:
            interviews.upsert_status(job_id=job_id, candidate_id=candidate_id, status="outreach_sent", create_default="selected")
            outreach_events.upsert(
                job_id=job_id,
                candidate_id=candidate_id,
                provider=OUTREACH_PROVIDER,
                to_email=to_email,
                subject=subject,
                body=body,
                status="simulated",
                sent_at=datetime.now(timezone.utc),
                next_follow_up_at=_follow_up_time() if ENABLE_FOLLOWUPS else None,
                last_error="",
            )
            current_event = outreach_events.get(job_id=job_id, candidate_id=candidate_id)
            if current_event:
                snapshot = compute_outreach_engagement_snapshot(
                    status=current_event.status,
                    reply_state=current_event.reply_state,
                    open_count=current_event.open_count,
                    reply_count=current_event.reply_count,
                    follow_up_count=current_event.follow_up_count,
                    sent_at=current_event.sent_at,
                    responded_at=current_event.responded_at,
                    last_opened_at=getattr(current_event, "last_opened_at", None),
                    last_replied_at=getattr(current_event, "last_replied_at", None),
                )
                current_event.engagement_score = snapshot.engagement_score
                current_event.reply_likelihood_score = snapshot.reply_likelihood_score
                current_event.responsiveness_score = snapshot.responsiveness_score
            transition_candidate_ats_state(
                db=db,
                job_id=job_id,
                candidate_id=candidate_id,
                to_status="outreach_sent",
                source="outreach",
                reason="simulated_send",
                metadata={"status": "simulated", "toEmail": to_email},
            )
            _record_notification(
                db=db,
                job_id=job_id,
                candidate_id=candidate_id,
                notification_type="outreach_sent",
                recipient_type="candidate",
                recipient=to_email,
                channel="email",
                title=subject,
                body=body,
                status="delivered",
                metadata={"simulated": True, "provider": OUTREACH_PROVIDER},
            )
            db.commit()
            sent += 1
            follow_up_scheduled += 1
            record_job_lifecycle_event(
                db=db,
                job_id=job_id,
                event_type="OUTREACH_SENT",
                payload={
                    "jobId": job_id,
                    "candidateId": candidate_id,
                    "toEmail": to_email,
                    "status": "simulated",
                    "simulated": True,
                },
                source="outreach",
            )
            logger.info(
                "outreach_simulated job_id=%s candidate_id=%s to_email=%s simulated=%s",
                job_id,
                candidate_id,
                to_email,
                True,
            )
            log_metric("outreach_email_sent", job_id=job_id, candidate_id=candidate_id, provider=OUTREACH_PROVIDER, simulated=True)
            details.append(
                {
                    "candidateId": candidate_id,
                    "status": "simulated",
                    "toEmail": to_email,
                    "originalEmail": original_to_email,
                    "reason": "",
                }
            )
            continue

        if not provider_configured:
            skipped += 1
            blocked_reason = "email_provider_not_configured"
            skip_reasons[blocked_reason] = skip_reasons.get(blocked_reason, 0) + 1
            logger.warning("outreach_skipped job_id=%s candidate_id=%s reason=%s", job_id, candidate_id, blocked_reason)
            details.append({"candidateId": candidate_id, "status": "skipped", "reason": blocked_reason, "toEmail": to_email})
            skipped_candidates.append({"candidateId": candidate_id, "reason": blocked_reason})
            outreach_events.upsert(
                job_id=job_id,
                candidate_id=candidate_id,
                provider=OUTREACH_PROVIDER,
                to_email=to_email,
                subject=subject,
                body=body,
                status="failed",
                last_error=blocked_reason,
            )
            db.commit()
            continue

        interviews.upsert_status(job_id=job_id, candidate_id=candidate_id, status="outreach_sent", create_default="selected")
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
            blocked_reason = "already_claimed"
            skip_reasons[blocked_reason] = skip_reasons.get(blocked_reason, 0) + 1
            logger.info("outreach_skipped_already_claimed job_id=%s candidate_id=%s", job_id, candidate_id)
            details.append({"candidateId": candidate_id, "status": "skipped", "reason": blocked_reason, "toEmail": to_email})
            skipped_candidates.append({"candidateId": candidate_id, "reason": blocked_reason})
            db.commit()
            continue

        db.commit()
        logger.info("outreach_claimed job_id=%s candidate_id=%s to_email=%s", job_id, candidate_id, to_email)
        logger.info("outreach_sending_started job_id=%s candidate_id=%s to_email=%s", job_id, candidate_id, to_email)

        try:
            email_html = _render_professional_outreach_html(subject=subject, body=body)
            tracked_html = _append_open_tracking_pixel(
                html_body=email_html,
                event_id=str(event.id),
                candidate_id=candidate_id,
                job_id=job_id,
            )
            event.body = tracked_html
            if recruiter_id:
                _increment_daily_quota(_RECRUITER_DAILY_QUOTA_PREFIX, recruiter_id)
            _increment_daily_quota(_DOMAIN_REPUTATION_PREFIX, _email_domain(to_email), ttl_seconds=86400)
            email_sent, send_error, msg_id = _send_outreach_email(
                to_email=to_email,
                subject=subject,
                body=body,
                html_body=tracked_html,
            )
            if email_sent:
                now = datetime.now(timezone.utc)
                event.provider_message_id = msg_id or None
                event.status = "sent"
                event.last_sent_at = now
                event.last_contacted_at = now
                event.next_follow_up_at = _follow_up_time() if ENABLE_FOLLOWUPS else None
                event.follow_up_count = 0
                event.last_error = ""
                snapshot = compute_outreach_engagement_snapshot(
                    status=event.status,
                    reply_state=event.reply_state,
                    open_count=event.open_count,
                    reply_count=event.reply_count,
                    follow_up_count=event.follow_up_count,
                    sent_at=event.sent_at,
                    responded_at=event.responded_at,
                    last_opened_at=getattr(event, "last_opened_at", None),
                    last_replied_at=getattr(event, "last_replied_at", None),
                )
                event.engagement_score = snapshot.engagement_score
                event.reply_likelihood_score = snapshot.reply_likelihood_score
                event.responsiveness_score = snapshot.responsiveness_score
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
                    details.append(
                        {
                            "candidateId": candidate_id,
                            "status": "sending",
                            "toEmail": to_email,
                            "providerId": msg_id,
                            "originalEmail": original_to_email,
                            "reason": "",
                        }
                    )
                    continue
                sent += 1
                follow_up_scheduled += 1
                transition_candidate_ats_state(
                    db=db,
                    job_id=job_id,
                    candidate_id=candidate_id,
                    to_status="outreach_sent",
                    source="outreach",
                    actor_id=recruiter_id,
                    reason="initial_outreach_sent",
                    metadata={"status": "sent", "providerMessageId": msg_id, "toEmail": to_email},
                )
                _record_notification(
                    db=db,
                    job_id=job_id,
                    candidate_id=candidate_id,
                    notification_type="outreach_sent",
                    recipient_type="candidate",
                    recipient=to_email,
                    channel="email",
                    title=subject,
                    body=body,
                    status="delivered",
                    delivery_reference=msg_id or "",
                    metadata={"provider": OUTREACH_PROVIDER, "source": "initial_outreach"},
                )
                logger.info(
                    "outreach_sent job_id=%s candidate_id=%s to_email=%s provider_id=%s",
                    job_id,
                    candidate_id,
                    to_email,
                    msg_id,
                )
                record_job_lifecycle_event(
                    db=db,
                    job_id=job_id,
                    event_type="OUTREACH_SENT",
                    payload={
                        "jobId": job_id,
                        "candidateId": candidate_id,
                        "providerMessageId": msg_id,
                        "toEmail": to_email,
                        "status": "sent",
                    },
                    source="outreach",
                )
                log_metric("outreach_usage", job_id=job_id, candidate_id=candidate_id, provider=OUTREACH_PROVIDER, action="send")
                log_metric("outreach_email_sent", job_id=job_id, candidate_id=candidate_id, provider=OUTREACH_PROVIDER, provider_id=msg_id)
                details.append(
                    {
                        "candidateId": candidate_id,
                        "status": "sent",
                        "toEmail": to_email,
                        "providerId": msg_id,
                        "originalEmail": original_to_email,
                        "reason": "",
                    }
                )
            else:
                skipped += 1
                blocked_reason = send_error or "provider_rejected"
                skip_reasons[blocked_reason] = skip_reasons.get(blocked_reason, 0) + 1
                logger.warning("outreach_failed job_id=%s candidate_id=%s reason=%s", job_id, candidate_id, blocked_reason)
                log_metric("outreach_email_failed", job_id=job_id, candidate_id=candidate_id, error=blocked_reason)
                event.status = "failed"
                event.last_error = blocked_reason
                event.provider_message_id = None
                event.next_follow_up_at = None
                transition_candidate_ats_state(
                    db=db,
                    job_id=job_id,
                    candidate_id=candidate_id,
                    to_status="archived" if blocked_reason in {"suppressed", "invalid_or_missing_email"} else "rejected",
                    source="outreach",
                    reason=blocked_reason,
                    metadata={"status": "failed", "reason": blocked_reason},
                )
                db.commit()
                details.append(
                    {
                        "candidateId": candidate_id,
                        "status": "failed",
                        "reason": blocked_reason,
                        "toEmail": to_email,
                        "originalEmail": original_to_email,
                    }
                )
                skipped_candidates.append({"candidateId": candidate_id, "reason": blocked_reason})
        except Exception as exc:
            blocked_reason = _error_debug_string(exc)
            skip_reasons[blocked_reason] = skip_reasons.get(blocked_reason, 0) + 1
            skipped += 1
            logger.error("outreach_exception job_id=%s candidate_id=%s error=%s", job_id, candidate_id, blocked_reason, exc_info=exc)
            log_metric("outreach_email_failed", job_id=job_id, candidate_id=candidate_id, error=blocked_reason)
            event.status = "failed"
            event.last_error = blocked_reason
            event.provider_message_id = None
            event.next_follow_up_at = None
            transition_candidate_ats_state(
                db=db,
                job_id=job_id,
                candidate_id=candidate_id,
                to_status="rejected",
                source="outreach",
                reason=blocked_reason,
                metadata={"status": "failed", "reason": blocked_reason},
            )
            db.commit()
            details.append(
                {
                    "candidateId": candidate_id,
                    "status": "failed",
                    "reason": blocked_reason,
                    "toEmail": to_email,
                    "originalEmail": original_to_email,
                }
            )
            skipped_candidates.append({"candidateId": candidate_id, "reason": blocked_reason})

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
        subject, email_template, text_template = _build_shortlist_outreach_email(candidate_profile=profile, job=job)
        logger.info(
            "outreach_email_rendered job_id=%s candidate_id=%s subject=%s",
            job_id,
            candidate_id,
            subject,
        )
        raw_data = _candidate_raw_data(profile)
        delivery_target = _resolve_outreach_recipient(raw_data=raw_data)
        original_to_email = str(delivery_target.get("original_email") or "").strip()
        to_email = str(delivery_target.get("to_email") or "").strip()
        manual_required = bool(delivery_target.get("manual_required"))
        fallback_reason = str(delivery_target.get("reason") or "").strip()
        outreach_repo = OutreachEventRepository(db)
        recruiter_id = JobRepository(db).get_recruiter_id(job_id)

        if original_to_email and _is_suppressed(original_to_email):
            outreach_repo.upsert(
                job_id=job_id,
                candidate_id=candidate_id,
                provider=OUTREACH_PROVIDER,
                to_email=original_to_email,
                subject=subject,
                body=email_template,
                status="failed",
                last_error="suppressed",
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
                "candidateEmail": original_to_email,
                "linkedinUrl": linkedin_url,
                "status": "suppressed",
                "outreachStatus": "suppressed",
                "subject": subject,
                "html": email_template,
                "providerMessageId": "",
            }

        blocked, block_reason = _is_blocked_outbound_email(original_to_email)
        if blocked:
            logger.warning(
                "outreach_email_blocked job_id=%s candidate_id=%s reason=%s to_email=%s",
                job_id,
                candidate_id,
                block_reason or "missing_email",
                original_to_email,
            )
            outreach_repo.upsert(
                job_id=job_id,
                candidate_id=candidate_id,
                provider=OUTREACH_PROVIDER,
                to_email=original_to_email,
                subject=subject,
                body=email_template,
                status="failed",
                last_error=block_reason or "missing_email",
                sent_at=None,
                next_follow_up_at=None,
                provider_message_id=None,
            )
            db.commit()
            raise APIError("Selected candidate must have a valid email before outreach", status_code=422)

        if recruiter_id and not _daily_quota_allowed(_RECRUITER_DAILY_QUOTA_PREFIX, recruiter_id, limit=_DEFAULT_DAILY_OUTREACH_QUOTA):
            outreach_repo.upsert(
                job_id=job_id,
                candidate_id=candidate_id,
                provider=OUTREACH_PROVIDER,
                to_email=to_email,
                subject=subject,
                body=email_template,
                status="failed",
                last_error="recruiter_quota_exceeded",
                sent_at=None,
                next_follow_up_at=None,
                provider_message_id=None,
            )
            db.commit()
            raise APIError("Recruiter outreach quota exceeded", status_code=429)

        spam_risk = _spam_risk_score(subject=subject, body=email_template, to_email=to_email)
        if spam_risk >= _SPAM_RISK_THRESHOLD:
            outreach_repo.upsert(
                job_id=job_id,
                candidate_id=candidate_id,
                provider=OUTREACH_PROVIDER,
                to_email=to_email,
                subject=subject,
                body=email_template,
                status="failed",
                last_error="spam_risk_high",
                sent_at=None,
                next_follow_up_at=None,
                provider_message_id=None,
            )
            db.commit()
            raise APIError("Outreach message scored too risky for deliverability", status_code=422)

        event = outreach_repo.claim_outreach_for_sending(
            job_id=job_id,
            candidate_id=candidate_id,
            provider=OUTREACH_PROVIDER,
            to_email=to_email,
            subject=subject,
            body=email_template,
        )
        if not event:
            raise APIError("Outreach event is already being processed", status_code=409)

        tracked_template = _append_open_tracking_pixel(
            html_body=email_template,
            event_id=str(event.id),
            candidate_id=candidate_id,
            job_id=job_id,
        )
        email_sent, send_error, msg_id = _send_shortlist_outreach_email(
            to_email=to_email,
            subject=subject,
            html_body=tracked_template,
            text_body=text_template,
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
            body=tracked_template,
            status="sent",
            last_error="",
            sent_at=now,
            next_follow_up_at=_follow_up_time() if ENABLE_FOLLOWUPS else None,
            provider_message_id=msg_id or None,
        )
        db.commit()
        if recruiter_id:
            _increment_daily_quota(_RECRUITER_DAILY_QUOTA_PREFIX, recruiter_id)
        log_metric("outreach_usage", job_id=job_id, candidate_id=candidate_id, provider=OUTREACH_PROVIDER, action="shortlist_send")
        return {
            "success": True,
            "jobId": job_id,
            "candidateId": candidate_id,
            "candidateName": name,
            "candidateEmail": to_email or original_to_email,
            "linkedinUrl": linkedin_url,
            "status": "sent",
            "outreachStatus": "sent",
            "subject": subject,
            "html": tracked_template,
            "providerMessageId": msg_id,
            "manualRequired": manual_required,
            "fallbackReason": fallback_reason,
            "originalCandidateEmail": original_to_email,
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
    unique_selected_candidates = list(dict.fromkeys((candidate_id or "").strip() for candidate_id in selected_candidates if str(candidate_id or "").strip()))
    if not unique_selected_candidates:
        raise APIError(
            "No selected candidate available for outreach. Select exactly one candidate before queueing outreach.",
            status_code=400,
        )
    if len(unique_selected_candidates) != 1:
        raise APIError(
            "Outreach is now selection-driven and must target exactly one candidate.",
            status_code=400,
        )
    idempotency_material = {
        "job_id": job_id,
        "selected_candidates": unique_selected_candidates,
        "custom_body": custom_body.strip(),
    }
    idempotency_key = hashlib.sha256(
        json.dumps(idempotency_material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    result = enqueue_job(
        "outreach_send",
        {
            "job_id": job_id,
            "selected_candidates": unique_selected_candidates,
            "custom_body": custom_body,
        },
        idempotency_key=idempotency_key,
    )
    logger.info(
        "request_started outreach_pending job_id=%s selected_count=%s mode=%s",
        job_id,
        len(unique_selected_candidates),
        result.get("mode", "redis"),
    )
    return {
        "queued": bool(result.get("queued", True)),
        "job_id": result.get("job_id") or job_id,
        "selected_count": len(unique_selected_candidates),
        "mode": result.get("mode", "redis"),
        "deduplicated": bool(result.get("deduplicated")),
    }


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
        due = outreach_repo.list_due_follow_ups_locked(now=now, max_follow_up_count=3)
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
            if int(event.follow_up_count or 0) >= 3:
                event.status = "archived"
                event.next_follow_up_at = None
                event.last_error = "no_response_archive"
                event.archive_reason = "no_response_archive"
                event.updated_at = now
                snapshot = compute_outreach_engagement_snapshot(
                    status=event.status,
                    reply_state=event.reply_state,
                    open_count=event.open_count,
                    reply_count=event.reply_count,
                    follow_up_count=event.follow_up_count,
                    sent_at=event.sent_at,
                    responded_at=event.responded_at,
                    last_opened_at=getattr(event, "last_opened_at", None),
                    last_replied_at=getattr(event, "last_replied_at", None),
                )
                event.engagement_score = snapshot.engagement_score
                event.reply_likelihood_score = snapshot.reply_likelihood_score
                event.responsiveness_score = snapshot.responsiveness_score
                transition_candidate_ats_state(
                    db=db,
                    job_id=event.job_id,
                    candidate_id=event.candidate_id,
                    to_status="archived",
                    source="outreach",
                    reason="no_response_14_days",
                    metadata={"followUpCount": int(event.follow_up_count or 0), "status": "archived"},
                )
                _record_notification(
                    db=db,
                    job_id=event.job_id,
                    candidate_id=event.candidate_id,
                    notification_type="outreach_no_response",
                    recipient_type="recruiter",
                    recipient=str(job_repo.get_recruiter_id(event.job_id) or ""),
                    channel="slack",
                    title="No response after final follow-up",
                    body=f"Candidate {event.candidate_id} was archived after no response.",
                    status="delivered",
                    metadata={"followUpCount": int(event.follow_up_count or 0), "replyState": event.reply_state},
                )
                db.commit()
                skipped += 1
                logger.info(
                    "followup_archived job_id=%s candidate_id=%s follow_up_count=%s",
                    event.job_id,
                    event.candidate_id,
                    int(event.follow_up_count or 0),
                )
                log_metric("followup_archived", job_id=event.job_id, candidate_id=event.candidate_id, follow_up_count=int(event.follow_up_count or 0))
                continue
            subject, body = _build_followup_email(
                candidate_profile=profile, job=job, follow_up_number=follow_up_number
            )
            to_email = event.to_email

            if OUTREACH_DRY_RUN:
                outreach_repo.upsert(
                    job_id=event.job_id, candidate_id=event.candidate_id, provider=event.provider,
                    to_email=to_email, subject=subject, body=body, status="follow_up_sent",
                    sent_at=now, next_follow_up_at=_next_follow_up_time(follow_up_count_after_send=follow_up_number, base_time=now), increment_follow_up=True,
                )
                current_event = outreach_repo.get(job_id=event.job_id, candidate_id=event.candidate_id)
                if current_event:
                    snapshot = compute_outreach_engagement_snapshot(
                        status=current_event.status,
                        reply_state=current_event.reply_state,
                        open_count=current_event.open_count,
                        reply_count=current_event.reply_count,
                        follow_up_count=current_event.follow_up_count,
                        sent_at=current_event.sent_at,
                        responded_at=current_event.responded_at,
                        last_opened_at=getattr(current_event, "last_opened_at", None),
                        last_replied_at=getattr(current_event, "last_replied_at", None),
                    )
                    current_event.engagement_score = snapshot.engagement_score
                    current_event.reply_likelihood_score = snapshot.reply_likelihood_score
                    current_event.responsiveness_score = snapshot.responsiveness_score
                transition_candidate_ats_state(
                    db=db,
                    job_id=event.job_id,
                    candidate_id=event.candidate_id,
                    to_status="outreach_sent",
                    source="outreach",
                    reason=f"dry_run_followup_{follow_up_number}",
                    metadata={"followUpCount": follow_up_number, "dryRun": True},
                )
                sent += 1
                logger.info(
                    "outreach_followup_sent job_id=%s candidate_id=%s follow_up_count=%s dry_run=%s",
                    event.job_id, event.candidate_id, follow_up_number,
                    True,
                )
                log_metric("outreach_followup_sent", job_id=event.job_id, candidate_id=event.candidate_id,
                           follow_up_count=follow_up_number, dry_run=True)
                continue

            if not provider_configured:
                skipped += 1
                logger.warning("followup_skipped job_id=%s candidate_id=%s reason=provider_not_configured", event.job_id, event.candidate_id)
                continue

            try:
                followup_html = _render_professional_outreach_html(subject=subject, body=body, accent_label="Follow-up")
                tracked_followup_html = _append_open_tracking_pixel(
                    html_body=followup_html,
                    event_id=str(event.id),
                    candidate_id=event.candidate_id,
                    job_id=event.job_id,
                )
                email_sent, send_error, msg_id = _send_outreach_email(
                    to_email=to_email,
                    subject=subject,
                    body=body,
                    html_body=tracked_followup_html,
                )
                if email_sent:
                    try:
                        outreach_repo.upsert(
                            job_id=event.job_id, candidate_id=event.candidate_id, provider=event.provider,
                            to_email=to_email, subject=subject, body=body, status="follow_up_sent",
                            sent_at=now, next_follow_up_at=_next_follow_up_time(follow_up_count_after_send=follow_up_number, base_time=now),
                            provider_message_id=msg_id, increment_follow_up=True,
                        )
                        current_event = outreach_repo.get(job_id=event.job_id, candidate_id=event.candidate_id)
                        if current_event:
                            snapshot = compute_outreach_engagement_snapshot(
                                status=current_event.status,
                                reply_state=current_event.reply_state,
                                open_count=current_event.open_count,
                                reply_count=current_event.reply_count,
                                follow_up_count=current_event.follow_up_count,
                                sent_at=current_event.sent_at,
                                responded_at=current_event.responded_at,
                                last_opened_at=getattr(current_event, "last_opened_at", None),
                                last_replied_at=getattr(current_event, "last_replied_at", None),
                            )
                            current_event.engagement_score = snapshot.engagement_score
                            current_event.reply_likelihood_score = snapshot.reply_likelihood_score
                            current_event.responsiveness_score = snapshot.responsiveness_score
                        transition_candidate_ats_state(
                            db=db,
                            job_id=event.job_id,
                            candidate_id=event.candidate_id,
                            to_status="outreach_sent",
                            source="outreach",
                            reason=f"followup_{follow_up_number}_sent",
                            metadata={"followUpCount": follow_up_number, "providerMessageId": msg_id},
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
                        "outreach_followup_sent job_id=%s candidate_id=%s follow_up_count=%s provider_id=%s",
                        event.job_id, event.candidate_id, follow_up_number, msg_id,
                    )
                    log_metric("outreach_followup_sent", job_id=event.job_id, candidate_id=event.candidate_id,
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
    session = CandidateSelectionSessionRepository(db).get_by_job(job_id)
    if session and (getattr(session, "status", "") or "").strip().lower() == "completed":
        current_session_id = str(getattr(session, "id", "") or "").strip()
        session_selected_ids = {
            str(candidate_id).strip()
            for candidate_id in (getattr(session, "selected_candidate_ids", None) or [])
            if str(candidate_id).strip()
        }
        session_final_ids = _candidate_ids_from_snapshot(getattr(session, "final_candidate_snapshot", None))
        feedback_repo = CandidateFeedbackRepository(db)

        def belongs_to_current_session(row: OutreachEventEntity) -> bool:
            candidate_id = str(getattr(row, "candidate_id", "") or "").strip()
            if not candidate_id:
                return False
            if candidate_id in session_selected_ids:
                return True
            if candidate_id not in session_final_ids:
                return False
            feedback = feedback_repo.get(job_id=job_id, candidate_id=candidate_id)
            return (
                str(getattr(feedback, "session_id", "") or "").strip() == current_session_id
                and str(getattr(feedback, "feedback", "") or "").strip().lower() == "accept"
            )

        rows = [row for row in rows if belongs_to_current_session(row)]
    return [
        {
            "candidateId": row.candidate_id,
            "status": row.status,
            "provider": row.provider,
            "toEmail": row.to_email,
            "attemptCount": row.attempt_count,
            "followUpCount": row.follow_up_count,
            "providerMessageId": row.provider_message_id,
            "sentAt": row.sent_at.isoformat() if row.sent_at else None,
            "lastSentAt": row.last_sent_at.isoformat() if row.last_sent_at else None,
            "lastContactedAt": row.last_contacted_at.isoformat() if row.last_contacted_at else None,
            "lastOpenedAt": row.last_opened_at.isoformat() if getattr(row, "last_opened_at", None) else None,
            "lastRepliedAt": row.last_replied_at.isoformat() if getattr(row, "last_replied_at", None) else None,
            "nextFollowUpAt": row.next_follow_up_at.isoformat() if row.next_follow_up_at else None,
            "lastError": row.last_error,
            "replyState": row.reply_state,
            "archiveReason": row.archive_reason,
            "openCount": int(row.open_count or 0),
            "replyCount": int(row.reply_count or 0),
            "engagementScore": float(row.engagement_score or 0.0),
            "replyLikelihoodScore": float(row.reply_likelihood_score or 0.0),
            "responsivenessScore": float(row.responsiveness_score or 0.0),
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
    delivery_target = _resolve_outreach_recipient(raw_data=profile.raw_data or {})
    to_email = str(delivery_target.get("to_email") or "").strip()
    candidate_email = str(delivery_target.get("original_email") or "").strip()
    manual_required = bool(delivery_target.get("manual_required"))
    subject, body = generate_personalized_email(candidate_profile=profile, job=job)
    return {
        "subject": subject,
        "body": body,
        "toEmail": to_email,
        "candidateEmail": candidate_email,
        "manualRequired": manual_required,
        "fallbackReason": str(delivery_target.get("reason") or "").strip(),
    }
