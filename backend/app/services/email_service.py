from __future__ import annotations

import logging

import requests

from app.core.config import FROM_EMAIL, RESEND_API_KEY
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)


def email_health_snapshot() -> dict[str, str]:
    if not RESEND_API_KEY:
        return {"status": "unconfigured", "error": "RESEND_API_KEY missing"}
    try:
        response = requests.get(
            "https://api.resend.com/emails?limit=1",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            timeout=5,
        )
        if response.status_code >= 500:
            return {"status": "degraded", "error": f"resend_status_{response.status_code}"}
        return {"status": "ok", "error": ""}
    except Exception as exc:
        return {"status": "degraded", "error": str(exc)}


def send_email(
    *,
    to_email: str,
    subject: str,
    body: str,
    from_email: str | None = None,
    reply_to: str | None = None,
    html: str | None = None,
    text: str | None = None,
    tags: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> None:
    sender = (from_email or FROM_EMAIL).strip()
    logger.info("email_config_check from=%s api_key_present=%s", sender, bool(RESEND_API_KEY))
    logger.info("email_send_called to=%s", to_email)

    if not RESEND_API_KEY:
        raise APIError("RESEND_API_KEY is missing", status_code=500)
    if not sender:
        raise APIError("FROM_EMAIL is missing", status_code=500)

    try:
        payload = {
            "from": sender,
            "to": [to_email],
            "subject": subject,
            "text": text if text is not None else body,
        }
        normalized_html = (html or "").strip()
        if normalized_html:
            payload["html"] = normalized_html
        normalized_reply_to = (reply_to or "").strip()
        if normalized_reply_to:
            payload["reply_to"] = normalized_reply_to
        normalized_tags = {str(key).strip(): str(value).strip() for key, value in (tags or {}).items() if str(key).strip() and str(value).strip()}
        if normalized_tags:
            payload["tags"] = normalized_tags
        normalized_headers = {str(key).strip(): str(value).strip() for key, value in (headers or {}).items() if str(key).strip() and str(value).strip()}
        if normalized_headers:
            payload["headers"] = normalized_headers

        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
        if response.status_code >= 400:
            raise APIError(f"Email API failed: {response.text}", status_code=502)
        logger.info("email_sent_success to=%s", to_email)
    except APIError:
        logger.error("email_send_failed to=%s error=%s", to_email, "provider_rejected")
        raise
    except Exception as exc:
        logger.error("email_send_failed to=%s error=%s", to_email, str(exc))
        raise APIError("Failed to send email", status_code=502) from exc
