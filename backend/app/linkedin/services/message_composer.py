from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TypedDict
from uuid import uuid4

from app.core.config import GEMINI_API_KEY, GROQ_API_KEY, OPEN_ROUTER_API
from app.services.interview_link_providers import get_booking_link

logger = logging.getLogger(__name__)

_MAX_MESSAGE_CHARS = 1000


class MessageComposerResult(TypedDict):
    candidate_id: str
    job_id: str
    message_text: str
    eve_link: str
    queued: bool


def compose_linkedin_message(
    *,
    candidate_id: str,
    job_id: str,
    agency_id: str,
) -> MessageComposerResult:
    """Compose and persist a queued LinkedIn outreach message.

    Loads candidate, job, and agency. Generates message text via LLM if
    available, otherwise uses a deterministic template. Persists a
    linkedin_messages row with status queued (sent_at=NULL). Does not send.

    Returns {candidate_id, job_id, message_text, eve_link, queued: True}.
    """
    from app.db.session import SessionLocal
    from app.db.repositories import CandidateProfileRepository, CompanyRepository, JobRepository

    db = SessionLocal()
    try:
        job = JobRepository(db).get(job_id)
        if job is None:
            raise ValueError(f"job not found job_id={job_id}")

        candidate = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
        if candidate is None:
            raise ValueError(f"candidate not found candidate_id={candidate_id} job_id={job_id}")

        agency = CompanyRepository(db).get_by_id(agency_id)
    finally:
        db.close()

    eve_link = get_booking_link(candidate, job)

    message_text = _generate_message(
        candidate=candidate,
        job=job,
        agency=agency,
        eve_link=eve_link,
    )

    _persist_queued_message(
        candidate_id=candidate_id,
        job_id=job_id,
        message_text=message_text,
    )

    logger.info(
        "linkedin_message_composer queued candidate_id=%s job_id=%s chars=%d",
        candidate_id,
        job_id,
        len(message_text),
    )

    return {
        "candidate_id": candidate_id,
        "job_id": job_id,
        "message_text": message_text,
        "eve_link": eve_link,
        "queued": True,
    }


# ── Message generation ────────────────────────────────────────────────────────

def _generate_message(*, candidate, job, agency, eve_link: str) -> str:
    if GEMINI_API_KEY or GROQ_API_KEY or OPEN_ROUTER_API:
        try:
            text = _llm_message(candidate=candidate, job=job, agency=agency, eve_link=eve_link)
            if text:
                return text
        except Exception:
            logger.warning("linkedin_message_composer llm_failed — using heuristic", exc_info=True)

    return _heuristic_message(candidate=candidate, job=job, agency=agency, eve_link=eve_link)


def _llm_message(*, candidate, job, agency, eve_link: str) -> str:
    from app.services.llm_service import generate
    from app.services.prompt_sanitizer import sanitize_prompt_block

    first_name = _first_name(candidate)
    agency_name = _agency_name(agency)
    skills_text = ", ".join((candidate.skills or [])[:4]) or "not listed"

    prompt = (
        "Write a short, warm LinkedIn connection message from a recruiter to a candidate.\n"
        "Rules:\n"
        "- Plain text only, no markdown, no bullet points\n"
        f"- Maximum {_MAX_MESSAGE_CHARS} characters total\n"
        "- Do NOT invent facts beyond what is given\n"
        "- Do NOT use hype or pressure language\n"
        "- Sound like a human recruiter, not a bot\n"
        "- Mention the role and invite a quick conversation\n"
        f"- Include this booking link naturally: {eve_link}\n\n"
        f"{sanitize_prompt_block('Candidate first name', first_name, max_length=60)}\n"
        f"{sanitize_prompt_block('Candidate current role', candidate.current_role or 'unknown', max_length=120)}\n"
        f"{sanitize_prompt_block('Candidate skills', skills_text, max_length=400)}\n"
        f"{sanitize_prompt_block('Job title', job.title, max_length=120)}\n"
        f"{sanitize_prompt_block('Job location', job.location or 'flexible', max_length=120)}\n"
        f"{sanitize_prompt_block('Agency name', agency_name, max_length=120)}\n\n"
        "Return ONLY the message text, nothing else."
    )

    raw = str(generate(prompt)).strip()
    if not raw or len(raw) < 20:
        return ""

    return raw[:_MAX_MESSAGE_CHARS]


def _heuristic_message(*, candidate, job, agency, eve_link: str) -> str:
    first_name = _first_name(candidate)
    job_title = (job.title or "an exciting role").strip()
    agency_name = _agency_name(agency)
    location_clause = f" based in {job.location}" if getattr(job, "location", "") else ""

    text = (
        f"Hi {first_name}, I came across your profile and thought you'd be a great fit for "
        f"a {job_title}{location_clause} opportunity at {agency_name}. "
        f"I'd love to connect and share more details. "
        f"If you're open to a quick chat, you can book a time here: {eve_link}"
    )
    return text[:_MAX_MESSAGE_CHARS]


# ── Persistence ───────────────────────────────────────────────────────────────

def _persist_queued_message(
    *,
    candidate_id: str,
    job_id: str,
    message_text: str,
) -> None:
    from app.db.session import SessionLocal
    from app.linkedin.models import LinkedInConnectionEntity, LinkedInConversationEntity, LinkedInMessageEntity
    from app.linkedin.repository import LinkedInConnectionRepository, LinkedInConversationRepository, LinkedInMessageRepository

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        # Resolve account_id from the accepted connection row
        conn_row = (
            db.query(LinkedInConnectionEntity)
            .filter(
                LinkedInConnectionEntity.candidate_id == candidate_id,
                LinkedInConnectionEntity.connection_status == "accepted",
            )
            .first()
        )
        account_id = str(conn_row.account_id) if conn_row is not None else ""

        # Find or create a conversation stub for this candidate+account
        conversation_id = _find_or_create_conversation(
            db=db,
            candidate_id=candidate_id,
            account_id=account_id,
            now=now,
        )

        # Upsert: replace any existing queued outreach message for this candidate
        existing = (
            db.query(LinkedInMessageEntity)
            .filter(
                LinkedInMessageEntity.candidate_id == candidate_id,
                LinkedInMessageEntity.message_type == "outreach",
                LinkedInMessageEntity.sent_at.is_(None),
            )
            .first()
        )

        if existing is not None:
            existing.message_text = message_text
            existing.conversation_id = conversation_id
            existing.created_at = now
            db.flush()
        else:
            msg = LinkedInMessageEntity(
                id=str(uuid4()),
                conversation_id=conversation_id,
                candidate_id=candidate_id,
                sender_type="system",
                message_type="outreach",
                message_text=message_text,
                linkedin_message_id="",
                attachment_count=0,
                sent_at=None,
                created_at=now,
            )
            db.add(msg)
            db.flush()

        db.commit()
        logger.info(
            "linkedin_message_persisted candidate_id=%s conversation_id=%s",
            candidate_id,
            conversation_id,
        )
    except Exception:
        db.rollback()
        logger.exception(
            "linkedin_message_persist_failed candidate_id=%s job_id=%s",
            candidate_id,
            job_id,
        )
        raise
    finally:
        db.close()


def _find_or_create_conversation(
    *,
    db,
    candidate_id: str,
    account_id: str,
    now: datetime,
) -> str:
    from app.linkedin.models import LinkedInConversationEntity

    existing = (
        db.query(LinkedInConversationEntity)
        .filter(LinkedInConversationEntity.candidate_id == candidate_id)
        .first()
    )
    if existing is not None:
        return str(existing.id)

    stub = LinkedInConversationEntity(
        id=str(uuid4()),
        candidate_id=candidate_id,
        account_id=account_id or str(uuid4()),  # placeholder if no account resolved
        conversation_id="",
        conversation_status="unknown",
        last_message_at=None,
        last_synced_at=None,
        created_at=now,
        updated_at=now,
    )
    db.add(stub)
    db.flush()
    return str(stub.id)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _first_name(candidate) -> str:
    name = (getattr(candidate, "name", "") or "").strip()
    if name:
        return name.split()[0]
    return "there"


def _agency_name(agency) -> str:
    if agency is None:
        return "our company"
    return (getattr(agency, "name", "") or "our company").strip() or "our company"
