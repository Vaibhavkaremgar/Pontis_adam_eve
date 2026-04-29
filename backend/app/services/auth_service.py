from __future__ import annotations

import hashlib
import logging
import random
import re
import string
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import GOOGLE_OAUTH_CLIENT_ID
from app.core.config import AUTH_REQUIRE_OTP
from app.core.security import create_access_token
from app.db.repositories import OtpRepository, UserRepository
from app.services.email_service import send_email
from app.schemas.user import LoginData, UserProfile
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)

OTP_EXPIRY_MINUTES = 10
_EMAIL_PATTERN = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,63}$", re.IGNORECASE)


def _hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode()).hexdigest()


def _generate_otp() -> str:
    return "".join(random.choices(string.digits, k=6))


def _normalize_email(email: str) -> str:
    normalized = (email or "").strip().lower()
    if not normalized or len(normalized) > 320:
        return ""
    if ".." in normalized or not _EMAIL_PATTERN.match(normalized):
        return ""
    local, _, domain = normalized.rpartition("@")
    if not local or not domain or domain.startswith(".") or domain.endswith("."):
        return ""
    return normalized


def request_otp(*, db: Session, email: str) -> dict:
    normalized = _normalize_email(email)
    if not normalized:
        raise APIError("Valid email is required", status_code=400)

    otp = _generate_otp()
    otp_hash = _hash_otp(otp)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES)

    OtpRepository(db).create(email=normalized, otp_hash=otp_hash, expires_at=expires_at)
    db.commit()

    logger.info("otp_generated email=%s", normalized)
    logger.info("otp_sending_started email=%s", normalized)
    logger.info("otp_requested email=%s", normalized)

    subject = "Your Pontis login code"
    body = f"Your Pontis login code is {otp}. It expires in {OTP_EXPIRY_MINUTES} minutes."
    try:
        send_email(to_email=normalized, subject=subject, body=body)
    except APIError as exc:
        logger.error("otp_email_failed email=%s error=%s", normalized, exc.message)
        raise APIError("Failed to send OTP email", status_code=502) from exc

    return {"message": "OTP sent to email", "email": normalized}


def verify_otp(*, db: Session, email: str, otp: str) -> LoginData:
    normalized_email = _normalize_email(email)
    normalized_otp = (otp or "").strip()

    if not normalized_email or not normalized_otp:
        raise APIError("Valid email and OTP are required", status_code=400)

    otp_hash = _hash_otp(normalized_otp)
    now = datetime.now(timezone.utc)

    otp_repo = OtpRepository(db)
    row = otp_repo.consume_valid(email=normalized_email, otp_hash=otp_hash, now=now)
    if not row:
        logger.warning("otp_verification_failed email=%s reason=invalid_or_expired", normalized_email)
        raise APIError("Invalid or expired OTP", status_code=401)

    users = UserRepository(db)
    user = users.get_by_email(normalized_email)
    if not user:
        user = users.create(normalized_email)

    db.commit()

    token = create_access_token(user_id=user.id, email=user.email)
    logger.info("otp_verified_success email=%s user_id=%s", normalized_email, user.id)

    return LoginData(
        user=UserProfile(id=user.id, email=user.email, provider="email"),
        token=token,
        access_token=token,
    )


def login_with_google_token(*, db: Session, token: str) -> LoginData:
    raw_token = (token or "").strip()
    if not raw_token:
        raise APIError("Google token is required", status_code=400)
    if not GOOGLE_OAUTH_CLIENT_ID:
        raise APIError("Google OAuth is not configured on server", status_code=500)
    if AUTH_REQUIRE_OTP:
        logger.warning("google_login_blocked reason=otp_required")
        raise APIError("Google login disabled", status_code=403)

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token
    except ModuleNotFoundError as exc:
        raise APIError(
            "google-auth is not installed on server. Run: pip install google-auth",
            status_code=500,
        ) from exc

    try:
        idinfo = google_id_token.verify_oauth2_token(
            raw_token,
            google_requests.Request(),
            GOOGLE_OAUTH_CLIENT_ID,
        )
    except ValueError as exc:
        raise APIError("Invalid Google token", status_code=401) from exc

    email = str(idinfo.get("email") or "").strip().lower()
    if not email:
        raise APIError("Google account email is missing", status_code=401)

    users = UserRepository(db)
    user = users.get_by_email(email)
    if not user:
        user = users.create(email)
        db.commit()
    else:
        db.flush()

    app_token = create_access_token(user_id=user.id, email=user.email)
    return LoginData(
        user=UserProfile(
            id=user.id,
            email=user.email,
            name=str(idinfo.get("name") or ""),
            picture=str(idinfo.get("picture") or ""),
            provider="google",
        ),
        token=app_token,
        access_token=app_token,
    )
