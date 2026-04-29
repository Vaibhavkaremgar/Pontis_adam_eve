from __future__ import annotations

import imaplib
import logging
import mimetypes
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from email import message_from_bytes
from email.header import decode_header
from email.message import Message
from email.utils import parseaddr
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import (
    ENABLE_REPLY_POLLING,
    REPLY_ATTACHMENT_PUBLIC_BASE_URL,
    REPLY_ATTACHMENT_STORAGE_DIR,
    REPLY_IMAP_FOLDER,
    REPLY_IMAP_HOST,
    REPLY_IMAP_PASSWORD,
    REPLY_IMAP_PORT,
    REPLY_IMAP_USERNAME,
    REPLY_INBOX_PROVIDER,
)
from app.db.repositories import CandidateProfileRepository, OutreachEventRepository
from app.db.session import SessionLocal
from app.services.slack_service import notify_slack

logger = logging.getLogger(__name__)

_EMAIL_PATTERN = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,63}$", re.IGNORECASE)


@dataclass(frozen=True)
class ReplyAttachment:
    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class ParsedReply:
    sender_email: str
    subject: str
    message_text: str
    attachments: list[ReplyAttachment]
    message_id: str
    received_at: datetime


def _normalize_email(value: str) -> str:
    candidate = (value or "").strip().lower()
    if not candidate or len(candidate) > 320:
        return ""
    if ".." in candidate or not _EMAIL_PATTERN.match(candidate):
        return ""
    local, _, domain = candidate.rpartition("@")
    if not local or not domain or domain.startswith(".") or domain.endswith("."):
        return ""
    return candidate


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for decoded, encoding in decode_header(value):
        if isinstance(decoded, bytes):
            parts.append(decoded.decode(encoding or "utf-8", errors="ignore"))
        else:
            parts.append(decoded)
    return "".join(parts).strip()


def _decode_part_payload(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw_payload = part.get_payload()
        return raw_payload if isinstance(raw_payload, str) else ""

    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="ignore")
    except Exception:
        return payload.decode("utf-8", errors="ignore")


def _html_to_text(html_body: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html_body)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\s*>", "\n\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_sender_email(message: Message) -> str:
    name, email_address = parseaddr(message.get("Reply-To") or message.get("From") or "")
    return _normalize_email(email_address or name)


def _extract_subject(message: Message) -> str:
    return _decode_header_value(message.get("Subject"))


def _extract_message_text(message: Message) -> str:
    text_parts: list[str] = []
    html_parts: list[str] = []

    if message.is_multipart():
        for part in message.walk():
            content_disposition = (part.get_content_disposition() or "").lower()
            if content_disposition == "attachment":
                continue

            content_type = (part.get_content_type() or "").lower()
            if content_type == "text/plain":
                payload = _decode_part_payload(part).strip()
                if payload:
                    text_parts.append(payload)
            elif content_type == "text/html":
                payload = _decode_part_payload(part).strip()
                if payload:
                    html_parts.append(_html_to_text(payload))
    else:
        content_type = (message.get_content_type() or "").lower()
        payload = _decode_part_payload(message).strip()
        if content_type == "text/html":
            return _html_to_text(payload)
        return payload

    if text_parts:
        return "\n\n".join(text_parts).strip()
    if html_parts:
        return "\n\n".join(html_parts).strip()
    return ""


def _extract_attachments(message: Message) -> list[ReplyAttachment]:
    attachments: list[ReplyAttachment] = []
    if not message.is_multipart():
        return attachments

    for part in message.walk():
        disposition = (part.get_content_disposition() or "").lower()
        filename = _decode_header_value(part.get_filename())
        if disposition != "attachment" and not filename:
            continue

        payload = part.get_payload(decode=True)
        if not payload:
            continue

        attachments.append(
            ReplyAttachment(
                filename=filename or _guess_attachment_name(part.get_content_type()),
                content_type=(part.get_content_type() or "application/octet-stream").lower(),
                data=payload,
            )
        )
    return attachments


def parse_reply_message(raw_message: bytes, *, fallback_message_id: str = "") -> ParsedReply:
    message = message_from_bytes(raw_message)
    sender_email = _extract_sender_email(message)
    subject = _extract_subject(message)
    message_text = _extract_message_text(message)
    attachments = _extract_attachments(message)
    message_id = _normalize_message_id(
        _decode_header_value(message.get("Message-ID")) or fallback_message_id or ""
    )
    received_at = datetime.now(timezone.utc)
    return ParsedReply(
        sender_email=sender_email,
        subject=subject,
        message_text=message_text,
        attachments=attachments,
        message_id=message_id,
        received_at=received_at,
    )


def _normalize_message_id(value: str) -> str:
    return value.strip().strip("<>").strip()


def _guess_attachment_name(content_type: str) -> str:
    extension = mimetypes.guess_extension(content_type or "") or ".bin"
    return f"attachment{extension}"


def _looks_like_resume(attachment: ReplyAttachment) -> bool:
    filename = attachment.filename.lower()
    content_type = attachment.content_type.lower()
    if any(token in filename for token in ("resume", "cv", "curriculum")):
        return True
    if content_type in {"application/pdf", "application/msword"}:
        return True
    if content_type in {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
    } and filename.endswith((".docx", ".doc", ".txt")):
        return True
    return False


def _sanitize_filename(filename: str) -> str:
    safe = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", safe).strip("._-")
    return safe or "attachment.bin"


def _reply_storage_root() -> Path:
    return Path(REPLY_ATTACHMENT_STORAGE_DIR).resolve()


def _reply_storage_path(reply_id: str, filename: str) -> Path:
    safe_reply_id = _sanitize_filename(reply_id)
    safe_filename = _sanitize_filename(filename)
    return _reply_storage_root() / safe_reply_id / safe_filename


def _reply_public_url(reply_id: str, filename: str) -> str:
    attachment_path = f"/api/replies/attachments/{_sanitize_filename(reply_id)}/{_sanitize_filename(filename)}"
    base_url = (REPLY_ATTACHMENT_PUBLIC_BASE_URL or "").rstrip("/")
    if base_url:
        return f"{base_url}{attachment_path}"
    return attachment_path


def save_resume_attachment(*, reply_id: str, attachment: ReplyAttachment) -> str:
    path = _reply_storage_path(reply_id, attachment.filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(attachment.data)
    return _reply_public_url(reply_id, attachment.filename)


def build_message_preview(message_text: str, *, limit: int = 220) -> str:
    preview = re.sub(r"\s+", " ", (message_text or "")).strip()
    if len(preview) <= limit:
        return preview
    return preview[: max(0, limit - 1)].rstrip() + "…"


def notify_candidate_reply_slack(*, candidate_name: str, message_text: str, resume_url: str = "") -> None:
    preview = build_message_preview(message_text)
    lines = [preview or "(no message body)"]
    if resume_url:
        lines.append(f"Resume: {resume_url}")
    notify_slack(title=f"📩 Candidate {candidate_name} replied", lines=lines)


def _resolve_candidate_for_reply(db: Session, sender_email: str):
    profile_repo = CandidateProfileRepository(db)
    profile = profile_repo.find_by_email(sender_email)
    if not profile:
        return None
    return profile


def store_candidate_reply(
    *,
    db: Session,
    candidate_id: str,
    job_id: str,
    message_text: str,
    resume_url: str,
    provider_message_id: str = "",
    received_at: datetime | None = None,
) -> object:
    repo = OutreachEventRepository(db)
    return repo.upsert_response(
        job_id=job_id,
        candidate_id=candidate_id,
        provider="inbox",
        message_text=message_text,
        resume_url=resume_url,
        status="responded",
        provider_message_id=provider_message_id or None,
        received_at=received_at or datetime.now(timezone.utc),
    )


def _imap_is_configured() -> bool:
    return bool(
        ENABLE_REPLY_POLLING
        and REPLY_INBOX_PROVIDER == "imap"
        and REPLY_IMAP_HOST
        and REPLY_IMAP_USERNAME
        and REPLY_IMAP_PASSWORD
    )


def poll_candidate_replies(*, db: Session | None = None) -> dict[str, int]:
    if not _imap_is_configured():
        logger.info("reply_polling_skipped provider=%s enabled=%s", REPLY_INBOX_PROVIDER, ENABLE_REPLY_POLLING)
        return {"checked": 0, "matched": 0, "stored": 0, "ignored": 0, "failed": 0}

    owns_session = db is None
    session = db or SessionLocal()
    summary = {"checked": 0, "matched": 0, "stored": 0, "ignored": 0, "failed": 0}

    try:
        with imaplib.IMAP4_SSL(REPLY_IMAP_HOST, REPLY_IMAP_PORT) as mailbox:
            mailbox.login(REPLY_IMAP_USERNAME, REPLY_IMAP_PASSWORD)
            mailbox.select(REPLY_IMAP_FOLDER)
            status, data = mailbox.search(None, "UNSEEN")
            if status != "OK":
                logger.warning("reply_poll_search_failed status=%s", status)
                return summary

            message_ids = (data[0] or b"").split()
            for message_id in message_ids:
                summary["checked"] += 1
                try:
                    fetch_status, parts = mailbox.fetch(message_id, "(RFC822)")
                    if fetch_status != "OK" or not parts:
                        raise RuntimeError(f"imap_fetch_failed status={fetch_status}")

                    raw_message = b""
                    for part in parts:
                        if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                            raw_message = bytes(part[1])
                            break
                    if not raw_message:
                        raise RuntimeError("imap_message_missing_payload")

                    parsed = parse_reply_message(raw_message, fallback_message_id=message_id.decode("utf-8", errors="ignore"))
                    sender_email = parsed.sender_email
                    if not sender_email:
                        summary["ignored"] += 1
                        mailbox.store(message_id, "+FLAGS", "\\Seen")
                        continue

                    profile = _resolve_candidate_for_reply(session, sender_email)
                    if not profile:
                        summary["ignored"] += 1
                        logger.info("reply_poll_unknown_sender sender_email=%s", sender_email)
                        mailbox.store(message_id, "+FLAGS", "\\Seen")
                        continue

                    resume_url = ""
                    storage_id = str(uuid4())
                    for attachment in parsed.attachments:
                        if _looks_like_resume(attachment):
                            resume_url = save_resume_attachment(reply_id=storage_id, attachment=attachment)
                            break

                    store_candidate_reply(
                        db=session,
                        candidate_id=profile.candidate_id,
                        job_id=profile.job_id,
                        message_text=parsed.message_text,
                        resume_url=resume_url,
                        provider_message_id=parsed.message_id,
                        received_at=parsed.received_at,
                    )
                    notify_candidate_reply_slack(
                        candidate_name=profile.name or profile.candidate_id,
                        message_text=parsed.message_text,
                        resume_url=resume_url,
                    )
                    mailbox.store(message_id, "+FLAGS", "\\Seen")
                    session.commit()
                    summary["matched"] += 1
                    summary["stored"] += 1
                except Exception as exc:
                    session.rollback()
                    summary["failed"] += 1
                    logger.error("reply_poll_message_failed message_id=%s error=%s", message_id, str(exc), exc_info=exc)
    except Exception as exc:
        if owns_session:
            session.rollback()
        logger.error("reply_polling_failed error=%s", str(exc), exc_info=exc)
        summary["failed"] += 1
    finally:
        if owns_session:
            session.close()

    return summary


def resolve_attachment_path(reply_id: str, filename: str) -> Path:
    return _reply_storage_path(reply_id, filename)
