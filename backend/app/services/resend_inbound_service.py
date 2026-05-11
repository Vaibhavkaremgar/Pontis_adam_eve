from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.core.config import (
    HTTP_TIMEOUT_SECONDS,
    INBOUND_ATTACHMENT_MAX_BYTES,
    PUBLIC_APP_URL,
    REPLY_ATTACHMENT_PUBLIC_BASE_URL,
    REPLY_ATTACHMENT_STORAGE_DIR,
    RESEND_API_KEY,
)
from app.db.repositories import CandidateProfileRepository, InboundEmailRepository, OutreachEventRepository
from app.services.slack_service import notify_slack
from app.services.webhook_security import get_webhook_header, verify_resend_webhook

logger = logging.getLogger(__name__)

_EMAIL_PATTERN = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,63}$", re.IGNORECASE)
_ALLOWED_ATTACHMENT_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_SUPPORTED_ATTACHMENT_EXTENSIONS = {".pdf", ".doc", ".docx"}


@dataclass(frozen=True)
class InboundAttachmentDownload:
    attachment_id: str
    filename: str
    content_type: str
    size: int
    content: bytes


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


def _decode_header_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(_decode_header_value(item) for item in value if item is not None)
    if isinstance(value, dict):
        return str(value.get("value") or value.get("content") or value.get("name") or "").strip()
    return str(value).strip()


def _extract_sender_address(from_value: str) -> tuple[str, str]:
    display_name, email_address = parseaddr(from_value or "")
    return _normalize_email(email_address), display_name.strip()


def _resend_headers() -> dict[str, str]:
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY is required for inbound email fetching")
    return {"Authorization": f"Bearer {RESEND_API_KEY}"}


def _resend_get(path: str) -> dict[str, Any]:
    response = requests.get(f"https://api.resend.com{path}", headers=_resend_headers(), timeout=HTTP_TIMEOUT_SECONDS)
    if response.status_code >= 400:
        raise RuntimeError(f"Resend API error {response.status_code}: {response.text[:300]}")
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Invalid Resend response")
    return data


def _list_resend_attachments(email_id: str) -> list[dict[str, Any]]:
    data = _resend_get(f"/emails/receiving/{email_id}/attachments")
    attachments = data.get("data") if isinstance(data, dict) else []
    return [item for item in attachments if isinstance(item, dict)]


def _download_resend_attachment(attachment: dict[str, Any]) -> InboundAttachmentDownload:
    attachment_id = _decode_header_value(attachment.get("id"))
    download_url = _decode_header_value(attachment.get("download_url"))
    filename = _decode_header_value(attachment.get("filename")) or "attachment.bin"
    content_type = _decode_header_value(attachment.get("content_type")).lower() or "application/octet-stream"
    size = int(attachment.get("size") or 0)
    if not download_url:
        raise RuntimeError(f"Missing download_url for attachment {attachment_id or filename}")

    response = requests.get(download_url, timeout=HTTP_TIMEOUT_SECONDS)
    if response.status_code >= 400:
        raise RuntimeError(f"Failed to download attachment {filename}: {response.status_code}")
    content = bytes(response.content)
    return InboundAttachmentDownload(
        attachment_id=attachment_id or hashlib.sha256(download_url.encode("utf-8")).hexdigest()[:24],
        filename=filename,
        content_type=content_type,
        size=size or len(content),
        content=content,
    )


def _sanitize_filename(filename: str) -> str:
    safe = unicodedata.normalize("NFKD", filename or "").encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", safe).strip("._-")
    return safe or "attachment.bin"


def _reply_storage_root() -> Path:
    return Path(REPLY_ATTACHMENT_STORAGE_DIR).resolve()


def _reply_storage_path(reply_id: str, filename: str) -> Path:
    return _reply_storage_root() / _sanitize_filename(reply_id) / _sanitize_filename(filename)


def _reply_public_url(reply_id: str, filename: str) -> str:
    attachment_path = f"/api/replies/attachments/{_sanitize_filename(reply_id)}/{_sanitize_filename(filename)}"
    base_url = (REPLY_ATTACHMENT_PUBLIC_BASE_URL or "").rstrip("/")
    return f"{base_url}{attachment_path}" if base_url else attachment_path


def _filename_with_attachment_id(attachment_id: str, filename: str) -> str:
    safe_name = _sanitize_filename(filename)
    safe_id = _sanitize_filename(attachment_id)
    return f"{safe_id}_{safe_name}" if safe_id else safe_name


def _looks_supported(filename: str, content_type: str, size_bytes: int) -> tuple[bool, str]:
    if size_bytes <= 0:
        return False, "attachment_size_missing"
    if size_bytes > INBOUND_ATTACHMENT_MAX_BYTES:
        return False, "attachment_too_large"
    normalized_type = (content_type or "").strip().lower()
    if normalized_type in _ALLOWED_ATTACHMENT_TYPES:
        return True, ""
    extension = Path(filename or "").suffix.lower()
    if extension in _SUPPORTED_ATTACHMENT_EXTENSIONS:
        guessed_type, _ = mimetypes.guess_type(filename)
        if guessed_type in _ALLOWED_ATTACHMENT_TYPES:
            return True, ""
        if normalized_type in {"", "application/octet-stream"}:
            return True, ""
    return False, "unsupported_attachment_type"


def _build_candidate_profile_link(*, job_id: str, candidate_id: str) -> str:
    base_url = (PUBLIC_APP_URL or "").rstrip("/")
    if not job_id or not candidate_id:
        return f"{base_url}/review" if base_url else "/review"
    path = f"/review?jobId={job_id}&candidateId={candidate_id}"
    return f"{base_url}{path}" if base_url else path


def _candidate_profile_display_name(profile: Any, candidate_email: str) -> str:
    name = str(getattr(profile, "name", "") or "").strip()
    if name:
        return name
    if candidate_email:
        return candidate_email.split("@", 1)[0]
    return "Unmatched candidate"


def _store_attachment(
    *,
    repo: InboundEmailRepository,
    reply_id: str,
    attachment: InboundAttachmentDownload,
) -> dict[str, Any]:
    stored_filename = _filename_with_attachment_id(attachment.attachment_id, attachment.filename)
    storage_path = _reply_storage_path(reply_id, stored_filename)
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path.write_bytes(attachment.content)
    sha256 = hashlib.sha256(attachment.content).hexdigest()
    public_url = _reply_public_url(reply_id, stored_filename)
    repo.add_attachment(
        reply_id=reply_id,
        provider_attachment_id=attachment.attachment_id,
        filename=stored_filename,
        content_type=attachment.content_type,
        size_bytes=len(attachment.content),
        storage_path=str(storage_path),
        public_url=public_url,
        sha256=sha256,
    )
    return {
        "filename": stored_filename,
        "originalFilename": attachment.filename,
        "contentType": attachment.content_type,
        "sizeBytes": len(attachment.content),
        "storagePath": str(storage_path),
        "publicUrl": public_url,
        "sha256": sha256,
    }


def _build_slack_message(
    *,
    candidate_name: str,
    sender_email: str,
    subject: str,
    attachments: list[dict[str, Any]],
    profile_link: str,
) -> list[str]:
    lines = [
        f"Candidate: {candidate_name or 'Unmatched candidate'}",
        f"Sender: {sender_email or 'unknown'}",
        f"Subject: {subject or '(no subject)'}",
    ]
    if attachments:
        lines.append("Attachments: " + ", ".join(item["filename"] for item in attachments))
    else:
        lines.append("Attachments: none")
    lines.append(f"Profile: {profile_link}")
    return lines


def _extract_message_headers(email_record: dict[str, Any]) -> dict[str, Any]:
    headers = email_record.get("headers")
    if isinstance(headers, dict):
        return headers
    return {}


def _match_candidate(db: Session, sender_email: str):
    profile = CandidateProfileRepository(db).find_by_email(sender_email)
    return profile


def _match_outreach_event(db: Session, *, profile: Any, reply_message_id: str) -> Any | None:
    outreach_repo = OutreachEventRepository(db)
    if reply_message_id:
        matched = outreach_repo.get_by_provider_message_id(reply_message_id)
        if matched:
            return matched
    if profile:
        return outreach_repo.get(job_id=profile.job_id, candidate_id=profile.candidate_id)
    return None


def _update_outreach_status(db: Session, *, outreach_event: Any | None, received_at: datetime) -> None:
    if not outreach_event:
        return
    outreach_event.status = "replied"
    outreach_event.responded_at = received_at
    outreach_event.last_contacted_at = received_at
    outreach_event.last_error = ""
    outreach_event.next_follow_up_at = None


def process_resend_inbound_webhook(*, db: Session, raw_body: bytes, headers: Any) -> dict[str, Any]:
    svix_id = get_webhook_header(headers, "svix-id", "webhook-id")
    svix_timestamp = get_webhook_header(headers, "svix-timestamp", "webhook-timestamp")
    svix_signature = get_webhook_header(headers, "svix-signature", "webhook-signature")

    verification = verify_resend_webhook(
        raw_body=raw_body,
        webhook_id=svix_id,
        timestamp=svix_timestamp,
        signature=svix_signature,
    )
    if not verification.is_valid:
        raise RuntimeError(f"invalid_webhook_signature:{verification.reason}")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"invalid_webhook_payload:{exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("invalid_webhook_payload:not_object")

    event_type = str(payload.get("type") or "").strip()
    if event_type != "email.received":
        return {"status": "ignored", "reason": "unsupported_event", "eventType": event_type}

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    email_id = _decode_header_value(data.get("email_id"))
    if not email_id:
        raise RuntimeError("missing_email_id")

    inbound_repo = InboundEmailRepository(db)
    existing = inbound_repo.get_by_svix_id(svix_id)
    if existing and (existing.processing_status or "").strip().lower() == "processed":
        return {
            "status": "duplicate",
            "replyId": existing.id,
            "emailId": existing.email_id,
            "candidateId": existing.candidate_id,
        }

    webhook_created_at = None
    created_at_raw = _decode_header_value(payload.get("created_at"))
    if created_at_raw:
        try:
            webhook_created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
        except Exception:
            webhook_created_at = None

    email_record = _resend_get(f"/emails/receiving/{email_id}")
    headers_map = _extract_message_headers(email_record)
    body_html = _decode_header_value(email_record.get("html"))
    body_text = _decode_header_value(email_record.get("text"))
    if not body_text and body_html:
        body_text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", body_html)
        body_text = re.sub(r"(?is)<br\s*/?>", "\n", body_text)
        body_text = re.sub(r"(?is)</p\s*>", "\n\n", body_text)
        body_text = re.sub(r"(?is)<[^>]+>", " ", body_text)
        body_text = re.sub(r"&nbsp;", " ", body_text)
        body_text = re.sub(r"\s+", " ", body_text).strip()

    sender_email, sender_name = _extract_sender_address(_decode_header_value(email_record.get("from")))
    subject = _decode_header_value(email_record.get("subject"))
    provider_message_id = _decode_header_value(email_record.get("message_id")) or _decode_header_value(headers_map.get("message-id"))
    received_at_raw = _decode_header_value(email_record.get("created_at"))
    received_at = datetime.now(timezone.utc)
    if received_at_raw:
        try:
            received_at = datetime.fromisoformat(received_at_raw.replace("Z", "+00:00"))
        except Exception:
            pass

    candidate_profile = _match_candidate(db, sender_email) if sender_email else None
    candidate_id = getattr(candidate_profile, "candidate_id", None)
    job_id = getattr(candidate_profile, "job_id", None)
    outreach_event = _match_outreach_event(
        db,
        profile=candidate_profile,
        reply_message_id=_decode_header_value(headers_map.get("in-reply-to") or headers_map.get("In-Reply-To")),
    )

    row, created = inbound_repo.create_or_get(
        svix_id=svix_id,
        event_type=event_type,
        email_id=email_id,
        provider_message_id=provider_message_id or _decode_header_value(headers_map.get("message-id")),
        sender_email=sender_email,
        sender_name=sender_name,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        received_at=received_at,
        webhook_created_at=webhook_created_at,
        raw_payload=payload,
    )

    if not created and (row.processing_status or "").strip().lower() == "processing":
        return {"status": "duplicate", "replyId": row.id, "emailId": row.email_id}

    row.processing_status = "processing"
    row.updated_at = datetime.now(timezone.utc)
    db.flush()

    attachments_payload = _list_resend_attachments(email_id)
    stored_attachments: list[dict[str, Any]] = []
    skipped_attachments: list[dict[str, Any]] = []

    for attachment_meta in attachments_payload:
        try:
            download = _download_resend_attachment(attachment_meta)
            supported, reason = _looks_supported(download.filename, download.content_type, download.size)
            if not supported:
                skipped_attachments.append(
                    {
                        "filename": download.filename,
                        "contentType": download.content_type,
                        "sizeBytes": download.size,
                        "reason": reason,
                    }
                )
                continue
            stored_attachments.append(_store_attachment(repo=inbound_repo, reply_id=row.id, attachment=download))
        except Exception as exc:
            skipped_attachments.append(
                {
                    "filename": _decode_header_value(attachment_meta.get("filename")) or "attachment.bin",
                    "contentType": _decode_header_value(attachment_meta.get("content_type")),
                    "reason": str(exc),
                }
            )
            logger.warning(
                "inbound_attachment_processing_failed email_id=%s attachment_id=%s error=%s",
                email_id,
                _decode_header_value(attachment_meta.get("id")),
                str(exc),
            )

    match_status = "matched" if candidate_profile else "unmatched"
    processing_error = ""
    if candidate_profile and outreach_event:
        _update_outreach_status(db, outreach_event=outreach_event, received_at=received_at)
    elif candidate_profile and not outreach_event:
        logger.warning("inbound_reply_outreach_event_missing candidate_id=%s job_id=%s", candidate_id, job_id)
    elif not candidate_profile:
        logger.info("inbound_reply_unmatched sender_email=%s subject=%s", sender_email, subject)

    inbound_repo.mark_processed(
        row,
        processing_status="processed",
        match_status=match_status,
        attachment_count=len(stored_attachments),
        processing_error=processing_error,
        candidate_id=candidate_id,
        job_id=job_id,
        outreach_event_id=getattr(outreach_event, "id", None),
    )
    db.commit()

    candidate_name = _candidate_profile_display_name(candidate_profile, sender_email)
    profile_link = _build_candidate_profile_link(job_id=job_id or "", candidate_id=candidate_id or "") if candidate_profile else _build_candidate_profile_link(job_id="", candidate_id="")
    notify_slack(
        title=f"Candidate reply received: {candidate_name}",
        lines=_build_slack_message(
            candidate_name=candidate_name,
            sender_email=sender_email,
            subject=subject,
            attachments=stored_attachments,
            profile_link=profile_link,
        ),
    )

    return {
        "status": "processed",
        "replyId": row.id,
        "emailId": email_id,
        "candidateId": candidate_id,
        "jobId": job_id,
        "matchStatus": match_status,
        "senderEmail": sender_email,
        "subject": subject,
        "attachmentsStored": len(stored_attachments),
        "attachmentsSkipped": len(skipped_attachments),
        "storedAttachments": stored_attachments,
        "skippedAttachments": skipped_attachments,
    }
