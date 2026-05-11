from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any

from app.core.config import RESEND_WEBHOOK_SECRET, WEBHOOK_SHARED_SECRET

logger = logging.getLogger(__name__)

SVIX_ID_HEADER = "svix-id"
SVIX_TIMESTAMP_HEADER = "svix-timestamp"
SVIX_SIGNATURE_HEADER = "svix-signature"
WEBHOOK_SIGNATURE_HEADER = SVIX_SIGNATURE_HEADER
WEBHOOK_TIMESTAMP_HEADER = SVIX_TIMESTAMP_HEADER
WEBHOOK_ID_HEADER = SVIX_ID_HEADER
WEBHOOK_MAX_AGE_SECONDS = 300


@dataclass(frozen=True)
class WebhookVerificationResult:
    is_valid: bool
    reason: str = ""


def _active_webhook_secret() -> str:
    return (RESEND_WEBHOOK_SECRET or WEBHOOK_SHARED_SECRET or "").strip()


def _normalize_secret(secret: str) -> bytes:
    cleaned = (secret or "").strip()
    if cleaned.startswith("whsec_"):
        cleaned = cleaned.removeprefix("whsec_")
    padding = "=" * (-len(cleaned) % 4)
    try:
        return base64.b64decode(cleaned + padding)
    except Exception:
        return cleaned.encode("utf-8")


def _iter_signatures(signature_header: str) -> list[tuple[str, str]]:
    signatures: list[tuple[str, str]] = []
    for chunk in (signature_header or "").split():
        piece = chunk.strip()
        if not piece:
            continue
        if "," not in piece:
            signatures.append(("v1", piece))
            continue
        version, value = piece.split(",", 1)
        signatures.append((version.strip() or "v1", value.strip()))
    return signatures


def verify_resend_webhook(
    *,
    raw_body: bytes,
    webhook_id: str,
    timestamp: str,
    signature: str,
    secret: str | None = None,
) -> WebhookVerificationResult:
    active_secret = (secret or _active_webhook_secret()).strip()
    if not active_secret:
        logger.warning("webhook_verification_disabled reason=missing_secret")
        return WebhookVerificationResult(False, "missing_secret")

    webhook_id = (webhook_id or "").strip()
    timestamp = (timestamp or "").strip()
    signature = (signature or "").strip()
    if not webhook_id or not timestamp or not signature:
        logger.warning("webhook_verification_failed reason=missing_headers")
        return WebhookVerificationResult(False, "missing_headers")

    try:
        received_at = int(timestamp)
    except (TypeError, ValueError):
        logger.warning("webhook_verification_failed reason=invalid_timestamp")
        return WebhookVerificationResult(False, "invalid_timestamp")

    now = int(time.time())
    if abs(now - received_at) > WEBHOOK_MAX_AGE_SECONDS:
        logger.warning("webhook_verification_failed reason=replay_window_exceeded now=%s received=%s", now, received_at)
        return WebhookVerificationResult(False, "replay_window_exceeded")

    signed_content = f"{webhook_id}.{timestamp}.".encode("utf-8") + (raw_body or b"")
    signing_key = _normalize_secret(active_secret)
    expected = hmac.new(signing_key, signed_content, hashlib.sha256).digest()
    expected_signature = base64.b64encode(expected).decode("ascii")

    for version, candidate_signature in _iter_signatures(signature):
        if version != "v1":
            continue
        if hmac.compare_digest(expected_signature, candidate_signature):
            return WebhookVerificationResult(True, "")

    logger.warning("webhook_verification_failed reason=signature_mismatch")
    return WebhookVerificationResult(False, "signature_mismatch")


def verify_shared_secret_webhook(*, raw_body: bytes, signature: str, timestamp: str) -> bool:
    """
    Backwards-compatible alias for older tests and legacy shared-secret code paths.
    For Resend webhooks, callers should prefer verify_resend_webhook() with svix headers.
    """
    if "v1" in (signature or "") or "," in (signature or ""):
        result = verify_resend_webhook(
            raw_body=raw_body,
            webhook_id="legacy",
            timestamp=timestamp,
            signature=signature,
        )
        return result.is_valid

    secret = (WEBHOOK_SHARED_SECRET or "").strip()
    if not secret:
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
    expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, (signature or "").strip()):
        logger.warning("webhook_verification_failed reason=signature_mismatch")
        return False
    return True


def get_webhook_header(headers: Any, *names: str) -> str:
    for name in names:
        if hasattr(headers, "get"):
            value = headers.get(name, "")
        else:
            value = ""
        text = str(value or "").strip()
        if text:
            return text
    return ""
