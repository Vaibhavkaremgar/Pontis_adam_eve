from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import re
import unicodedata
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

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
from app.db.repositories import CandidateProfileRepository, InboundEmailRepository, OutreachEventRepository, JobRepository
from app.services.email_service import send_email
from app.services.interview_invite_service import send_interview_invite
from app.services.resume_ingestion_service import parse_resume_profile
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
_INTERESTED_REPLY_KEYWORDS = (
    "interested",
    "yes",
    "sounds good",
    "open to discuss",
    "happy to proceed",
    "keen to explore",
)
_NOT_INTERESTED_REPLY_KEYWORDS = (
    "not interested",
    "no thanks",
    "pass",
    "decline",
    "not looking",
)
_NEEDS_MORE_INFO_REPLY_KEYWORDS = (
    "tell me more",
    "share details",
    "salary",
    "compensation",
    "job description",
)
_UNSUBSCRIBE_REPLY_KEYWORDS = (
    "unsubscribe",
    "remove me",
    "stop emailing",
    "do not contact",
)


@dataclass(frozen=True)
class InboundAttachmentDownload:
    attachment_id: str
    filename: str
    content_type: str
    size: int
    content: bytes


@dataclass(frozen=True)
class ResumeParseResult:
    text: str
    profile: Any
    contact_email: str
    phone: str
    linkedin_url: str
    github_url: str


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


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


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


def _is_resume_attachment(filename: str, content_type: str) -> bool:
    extension = Path(filename or "").suffix.lower()
    normalized_type = (content_type or "").strip().lower()
    return extension in _SUPPORTED_ATTACHMENT_EXTENSIONS or normalized_type in _ALLOWED_ATTACHMENT_TYPES


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in phrases)


def _detect_reply_intent(raw_event: dict[str, Any]) -> str:
    body = _normalize_text(raw_event.get("body") or raw_event.get("text") or raw_event.get("snippet") or "")
    lowered = body.lower()
    if not lowered:
        return "ambiguous"
    if _contains_any(lowered, _UNSUBSCRIBE_REPLY_KEYWORDS):
        return "unsubscribe"
    if _contains_any(lowered, _NOT_INTERESTED_REPLY_KEYWORDS):
        return "not_interested"
    if _contains_any(lowered, _NEEDS_MORE_INFO_REPLY_KEYWORDS):
        return "needs_more_info"
    if _contains_any(lowered, _INTERESTED_REPLY_KEYWORDS):
        return "interested"
    return "ambiguous"


def _extract_docx_text(content: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            document_xml = archive.read("word/document.xml")
    except Exception:
        return ""

    try:
        root = ET.fromstring(document_xml)
    except Exception:
        return ""

    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    parts = [node.text for node in root.findall(".//w:t", namespace) if node.text]
    return re.sub(r"\s+", " ", "\n".join(parts)).strip()


def _extract_plain_text(content: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            text = content.decode(encoding, errors="ignore").strip()
            if text:
                return re.sub(r"\s+", " ", text).strip()
        except Exception:
            continue
    return ""


def _extract_resume_text(*, attachment: InboundAttachmentDownload) -> str:
    suffix = Path(attachment.filename or "").suffix.lower()
    if suffix == ".pdf":
        temp_file = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        try:
            temp_file.write(attachment.content)
            temp_file.flush()
            temp_file.close()
            from app.services.resume_ingestion_service import extract_pdf_text

            resume_text, _ = extract_pdf_text(Path(temp_file.name))
            return resume_text.strip()
        finally:
            try:
                Path(temp_file.name).unlink(missing_ok=True)
            except Exception:
                pass

    if suffix == ".docx" or zipfile.is_zipfile(BytesIO(attachment.content)):
        text = _extract_docx_text(attachment.content)
        if text:
            return text

    if suffix == ".doc":
        text = _extract_docx_text(attachment.content)
        if text:
            return text

    return _extract_plain_text(attachment.content)


def _extract_phone_number(text: str) -> str:
    match = re.search(r"(?:\+?\d[\d\s().-]{7,}\d)", text or "")
    if not match:
        return ""
    value = re.sub(r"[^0-9+]", "", match.group(0))
    return value if len(value) >= 8 else ""


def _extract_social_url(text: str, pattern: str) -> str:
    match = re.search(pattern, text or "", re.IGNORECASE)
    return match.group(0).rstrip(").,;]") if match else ""


def _parse_resume_attachment(*, attachment: InboundAttachmentDownload) -> ResumeParseResult:
    resume_text = _extract_resume_text(attachment=attachment)
    profile = parse_resume_profile(resume_text=resume_text, file_name=attachment.filename)
    resume_json = profile.model_dump()
    contact_email = ""
    email_match = re.search(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,63}", resume_text, re.IGNORECASE)
    if email_match:
        contact_email = email_match.group(0).lower()
    phone = _extract_phone_number(resume_text)
    linkedin_url = _extract_social_url(resume_text, r"https?://(?:www\.)?linkedin\.com/[^\s)>\]]+")
    github_url = _extract_social_url(resume_text, r"https?://(?:www\.)?github\.com/[^\s)>\]]+")
    return ResumeParseResult(
        text=resume_text,
        profile=resume_json,
        contact_email=contact_email,
        phone=phone,
        linkedin_url=linkedin_url,
        github_url=github_url,
    )


def _update_candidate_from_resume(
    *,
    candidate_profile: Any,
    parsed_resume: ResumeParseResult,
    sender_email: str,
) -> None:
    profile = parsed_resume.profile if isinstance(parsed_resume.profile, dict) else {}
    current_company = ""
    companies = profile.get("companies") if isinstance(profile.get("companies"), list) else []
    if companies:
        current_company = str(companies[0] or "").strip()

    candidate_profile.name = str(profile.get("full_name") or candidate_profile.name or "").strip()
    candidate_profile.role = str(profile.get("headline") or candidate_profile.role or "").strip()
    candidate_profile.company = current_company or candidate_profile.company or ""
    candidate_profile.summary = str(profile.get("summary") or candidate_profile.summary or "").strip()
    candidate_profile.skills = list(profile.get("skills") or candidate_profile.skills or [])
    candidate_profile.candidate_status = "qualified"
    candidate_profile.resume_received_at = datetime.now(timezone.utc)
    candidate_profile.total_experience_years = float(profile.get("years_experience") or 0.0)
    candidate_profile.current_title = candidate_profile.role
    candidate_profile.current_company = current_company
    candidate_profile.phone = parsed_resume.phone
    candidate_profile.linkedin_url = parsed_resume.linkedin_url
    candidate_profile.github_url = parsed_resume.github_url
    candidate_profile.parsed_resume_text = parsed_resume.text
    candidate_profile.parsed_resume_json = {
        **profile,
        "email": parsed_resume.contact_email or sender_email,
        "phone": parsed_resume.phone,
        "linkedin_url": parsed_resume.linkedin_url,
        "github_url": parsed_resume.github_url,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
    }
    raw_data = dict(candidate_profile.raw_data or {})
    raw_data.update(
        {
            "email": parsed_resume.contact_email or sender_email,
            "phone": parsed_resume.phone,
            "linkedin_url": parsed_resume.linkedin_url,
            "github_url": parsed_resume.github_url,
            "candidate_status": "qualified",
            "resume_received_at": candidate_profile.resume_received_at.isoformat() if candidate_profile.resume_received_at else "",
            "parsed_resume_json": candidate_profile.parsed_resume_json,
            "parsed_resume_text": parsed_resume.text,
        }
    )
    candidate_profile.raw_data = raw_data
    logger.info(
        "candidate_profile_updated job_id=%s candidate_id=%s candidate_status=%s",
        getattr(candidate_profile, "job_id", ""),
        getattr(candidate_profile, "candidate_id", ""),
        candidate_profile.candidate_status,
    )


def _set_candidate_status(candidate_profile: Any, status: str) -> None:
    candidate_profile.candidate_status = status
    raw_data = dict(candidate_profile.raw_data or {})
    raw_data["candidate_status"] = status
    candidate_profile.raw_data = raw_data
    logger.info(
        "candidate_profile_updated job_id=%s candidate_id=%s candidate_status=%s",
        getattr(candidate_profile, "job_id", ""),
        getattr(candidate_profile, "candidate_id", ""),
        status,
    )


def _send_resume_request_followup(*, to_email: str, job_title: str, company_name: str) -> None:
    subject = "Please share your updated resume"
    body = (
        f"Thank you for your interest in the {job_title} opportunity at {company_name}.\n\n"
        "To proceed with your application, please reply to this email with your most recent resume attached.\n\n"
        "Once we receive your resume, we will review your profile and share the next steps.\n\n"
        "Best regards,\nPontis Talent Team"
    )
    send_email(
        to_email=to_email,
        subject=subject,
        body=body,
        from_email="info@pontis.one",
        reply_to="info@pontis.one",
        text=body,
        html=body.replace("\n\n", "<br><br>").replace("\n", "<br>"),
        tags={"product": "pontis", "flow": "resume_request_followup"},
    )
    logger.info("resume_request_sent to_email=%s", to_email)


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
    if existing and (existing.processing_status or "").strip().lower() in {"processed", "completed"}:
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
    if not job_id and outreach_event is not None:
        job_id = getattr(outreach_event, "job_id", None)
    reply_company_id = getattr(candidate_profile, "company_id", None) if candidate_profile else None
    if not reply_company_id and outreach_event is not None:
        reply_company_id = getattr(outreach_event, "company_id", None)
    if not reply_company_id and job_id:
        job = JobRepository(db).get(job_id)
        if job:
            reply_company_id = job.company_id

    row, created = inbound_repo.create_or_get(
        svix_id=svix_id,
        event_type=event_type,
        email_id=email_id,
        provider_message_id=provider_message_id or _decode_header_value(headers_map.get("message-id")),
        company_id=reply_company_id,
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
    downloaded_attachments: list[InboundAttachmentDownload] = []

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
            downloaded_attachments.append(download)
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
    intent = _detect_reply_intent({**payload, **email_record, "body": body_text, "text": body_text, "subject": subject})
    logger.info("reply_intent_detected sender_email=%s intent=%s", sender_email, intent)
    resume_attachment: InboundAttachmentDownload | None = next(
        (attachment for attachment in downloaded_attachments if _is_resume_attachment(attachment.filename, attachment.content_type)),
        None,
    )
    if resume_attachment is not None:
        logger.info(
            "resume_attachment_detected sender_email=%s filename=%s content_type=%s",
            sender_email,
            resume_attachment.filename,
            resume_attachment.content_type,
        )

    resume_parse_result: ResumeParseResult | None = None
    if candidate_profile and intent == "interested" and resume_attachment is not None:
        try:
            resume_parse_result = _parse_resume_attachment(attachment=resume_attachment)
            logger.info(
                "resume_parsed_successfully sender_email=%s filename=%s",
                sender_email,
                resume_attachment.filename,
            )
            _update_candidate_from_resume(candidate_profile=candidate_profile, parsed_resume=resume_parse_result, sender_email=sender_email)
        except Exception as exc:
            processing_error = f"resume_parse_failed:{exc}"
            logger.warning(
                "resume_parse_failed sender_email=%s filename=%s error=%s",
                sender_email,
                resume_attachment.filename if resume_attachment else "",
                str(exc),
                exc_info=exc,
            )
            _set_candidate_status(candidate_profile, "qualified")
    elif candidate_profile and intent == "interested":
        _set_candidate_status(candidate_profile, "awaiting_resume")
    elif candidate_profile and intent == "not_interested":
        _set_candidate_status(candidate_profile, "declined")
    elif candidate_profile and intent == "unsubscribe":
        _set_candidate_status(candidate_profile, "do_not_contact")
    elif candidate_profile and intent == "needs_more_info":
        _set_candidate_status(candidate_profile, "awaiting_recruiter_response")
    elif candidate_profile and intent == "ambiguous":
        _set_candidate_status(candidate_profile, "manual_review")

    if candidate_profile and outreach_event:
        _update_outreach_status(db, outreach_event=outreach_event, received_at=received_at)
        outreach_event.reply_intent = intent
    elif candidate_profile and not outreach_event:
        logger.warning("inbound_reply_outreach_event_missing candidate_id=%s job_id=%s", candidate_id, job_id)
    elif not candidate_profile:
        logger.info("inbound_reply_unmatched sender_email=%s subject=%s", sender_email, subject)

    if candidate_profile and intent == "unsubscribe" and sender_email:
        from app.services.outreach_service import _suppress_domain, _suppress_email, _email_domain

        _suppress_email(sender_email, reason="unsubscribe")
        _suppress_domain(_email_domain(sender_email), reason="unsubscribe")

    if candidate_profile and intent == "interested" and resume_attachment is not None and resume_parse_result is not None:
        try:
            send_interview_invite(
                candidate_id=candidate_id or "",
                job_id=job_id or "",
                outreach_event_id=getattr(outreach_event, "id", None),
            )
        except Exception as exc:
            logger.warning(
                "interview_invite_failed sender_email=%s candidate_id=%s job_id=%s error=%s",
                sender_email,
                candidate_id,
                job_id,
                str(exc),
                exc_info=exc,
            )

    inbound_repo.mark_processed(
        row,
        processing_status="completed",
        match_status=match_status,
        attachment_count=len(stored_attachments),
        processing_error=processing_error,
        candidate_id=candidate_id,
        job_id=job_id,
        outreach_event_id=getattr(outreach_event, "id", None),
    )
    row.intent = intent
    db.commit()

    if candidate_profile and intent == "interested" and resume_attachment is None and sender_email:
        try:
            job_title = str(getattr(candidate_profile, "role", "") or getattr(candidate_profile, "current_title", "") or "the role").strip()
            company_name = str(getattr(candidate_profile, "company", "") or getattr(candidate_profile, "current_company", "") or "Pontis").strip()
            _send_resume_request_followup(to_email=sender_email, job_title=job_title, company_name=company_name)
            if outreach_event:
                outreach_event.follow_up_count = int(outreach_event.follow_up_count or 0) + 1
                outreach_event.last_sent_at = datetime.now(timezone.utc)
                outreach_event.last_contacted_at = datetime.now(timezone.utc)
                outreach_event.updated_at = datetime.now(timezone.utc)
                db.commit()
        except Exception as exc:
            logger.warning(
                "resume_request_failed sender_email=%s candidate_id=%s error=%s",
                sender_email,
                candidate_id,
                str(exc),
                exc_info=exc,
            )

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
