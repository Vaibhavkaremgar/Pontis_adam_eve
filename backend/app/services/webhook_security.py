from __future__ import annotations

import hashlib
import hmac
import logging
import time

from app.core.config import WEBHOOK_SHARED_SECRET

logger = logging.getLogger(__name__)

WEBHOOK_SIGNATURE_HEADER = "X-Pontis-Signature"
WEBHOOK_TIMESTAMP_HEADER = "X-Pontis-Timestamp"
WEBHOOK_MAX_AGE_SECONDS = 300


def verify_shared_secret_webhook(*, raw_body: bytes, signature: str, timestamp: str) -> bool:
    if not WEBHOOK_SHARED_SECRET:
        logger.warning("webhook_verification_disabled reason=missing_secret")
        return False

    try:
        received_at = int(timestamp)
    except (TypeError, ValueError):
        logger.warning("webhook_verification_failed reason=invalid_timestamp")
        return False

    now = int(time.time())
    if abs(now - received_at) > WEBHOOK_MAX_AGE_SECONDS:
        logger.warning("webhook_verification_failed reason=replay_window_exceeded now=%s received=%s", now, received_at)
        return False

    message = f"{received_at}.".encode("utf-8") + raw_body
    expected = hmac.new(WEBHOOK_SHARED_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, (signature or "").strip()):
        logger.warning("webhook_verification_failed reason=signature_mismatch")
        return False

    return True
