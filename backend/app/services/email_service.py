from __future__ import annotations

import logging

import requests

from app.core.config import FROM_EMAIL, RESEND_API_KEY
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)


def send_email(
    *,
    to_email: str,
    subject: str,
    body: str,
    from_email: str | None = None,
    reply_to: str | None = None,
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
            "text": body,
        }
        normalized_reply_to = (reply_to or "").strip()
        if normalized_reply_to:
            payload["reply_to"] = normalized_reply_to

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
