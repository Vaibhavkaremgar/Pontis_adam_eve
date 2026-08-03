from __future__ import annotations

import json
import hashlib
import logging
import random
import re
import string
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import GOOGLE_OAUTH_CLIENT_ID
from app.core.config import AUTH_REQUIRE_OTP, ADMIN_EMAILS, OPS_EMAILS
from app.core.security import AGENCY_USER_ROLE, SUPER_ADMIN_ROLE, create_access_token, normalize_app_role
from app.db.repositories import OtpRepository, UserRepository
from app.models.entities import AllowedUserEntity, CompanyEntity, UserEntity
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


def _resolve_user_role(email: str) -> str:
    normalized = _normalize_email(email)
    if normalized in ADMIN_EMAILS:
        return "admin"
    if normalized in OPS_EMAILS:
        return "internal_ops"
    return "recruiter"


def _allowed_user_metadata(*, db: Session, email: str) -> dict[str, str]:
    allowed = db.scalar(select(AllowedUserEntity).where(AllowedUserEntity.email == email))
    if not allowed:
        return {}
    raw_note = str(getattr(allowed, "note", "") or "").strip()
    if not raw_note:
        return {}
    try:
        parsed = json.loads(raw_note)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        "name": str(parsed.get("name") or parsed.get("fullName") or "").strip(),
        "agency_id": str(getattr(allowed, "agency_id", "") or parsed.get("agencyId") or parsed.get("agency_id") or "").strip(),
        "role": str(parsed.get("role") or parsed.get("userRole") or "").strip(),
    }


def _ensure_email_allowed(*, db: Session, email: str) -> None:
    allowed = db.scalar(
        select(AllowedUserEntity)
        .where(AllowedUserEntity.email == email)
        .where(AllowedUserEntity.is_active == True)  # noqa: E712
    )
    if not allowed:
        logger.warning("login_blocked email=%s reason=not_in_allowlist", email)
        raise HTTPException(
            status_code=403,
            detail="Access restricted. Contact your administrator to request access.",
        )


def _load_portal_user(*, db: Session, email: str):
    users = UserRepository(db)
    user = users.get_by_email(email)
    allowed_metadata = _allowed_user_metadata(db=db, email=email)
    mutated = False
    if not user:
        allowed = db.scalar(
            select(AllowedUserEntity)
            .where(AllowedUserEntity.email == email)
            .where(AllowedUserEntity.is_active == True)  # noqa: E712
        )
        if not allowed:
            raise APIError("Access restricted. Contact your administrator to request access.", status_code=403)
        agency_id = allowed_metadata.get("agency_id", "")
        role = normalize_app_role(allowed_metadata.get("role") or AGENCY_USER_ROLE)
        if role != SUPER_ADMIN_ROLE and not agency_id:
            raise APIError("Account is not linked to an agency.", status_code=403)
        if not agency_id:
            agency_id = ""
        user = UserEntity(
            id=str(uuid4()),
            email=email,
            full_name=allowed_metadata.get("name") or "",
            role=role,
            agency_id=agency_id or None,
            is_active=True,
        )
        db.add(user)
        db.flush()
        mutated = True
    agency_id = str(getattr(user, "agency_id", "") or "").strip()
    role = normalize_app_role(getattr(user, "role", "") or allowed_metadata.get("role") or AGENCY_USER_ROLE)
    if not agency_id:
        agency_id = allowed_metadata.get("agency_id", "")
        if agency_id:
            user.agency_id = agency_id
            db.flush()
            mutated = True
        elif role != SUPER_ADMIN_ROLE:
            raise APIError("Account is not linked to an agency.", status_code=403)
    if role == SUPER_ADMIN_ROLE and getattr(user, "agency_id", None) is not None:
        user.agency_id = None
        agency_id = ""
        db.flush()
        mutated = True
    if allowed_metadata.get("name") and not str(getattr(user, "full_name", "") or "").strip():
        user.full_name = allowed_metadata["name"]
        db.flush()
        mutated = True
    if allowed_metadata.get("role") and normalize_app_role(user.role) != SUPER_ADMIN_ROLE:
        user.role = normalize_app_role(allowed_metadata["role"])
        db.flush()
        mutated = True
    role = normalize_app_role(getattr(user, "role", "") or AGENCY_USER_ROLE)
    if role != SUPER_ADMIN_ROLE and agency_id:
        agency = db.scalar(select(CompanyEntity).where(CompanyEntity.id == agency_id))
        if not agency or not bool(getattr(agency, "is_active", False)):
            raise APIError("Your organization has been deactivated. Please contact your administrator.", status_code=403)
    if mutated:
        db.commit()
    return user, (agency_id or None), role


def request_otp(*, db: Session, email: str) -> dict:
    normalized = _normalize_email(email)
    if not normalized:
        raise APIError("Valid email is required", status_code=400)
    _ensure_email_allowed(db=db, email=normalized)

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
    _ensure_email_allowed(db=db, email=normalized_email)

    otp_hash = _hash_otp(normalized_otp)
    now = datetime.now(timezone.utc)

    otp_repo = OtpRepository(db)
    row = otp_repo.consume_valid(email=normalized_email, otp_hash=otp_hash, now=now)
    if not row:
        logger.warning("otp_verification_failed email=%s reason=invalid_or_expired", normalized_email)
        raise APIError("Invalid or expired OTP", status_code=401)

    user, agency_id, role = _load_portal_user(db=db, email=normalized_email)
    token = create_access_token(user_id=user.id, email=user.email, role=role)
    logger.info("otp_verified_success email=%s user_id=%s", normalized_email, user.id)

    return LoginData(
        user=UserProfile(id=user.id, email=user.email, provider="email", role=role, agency_id=agency_id),
        token=token,
        access_token=token,
    )


def login_with_google_token(*, db: Session, token: str) -> LoginData:
    raw_token = (token or "").strip()
    if not raw_token:
        raise APIError("Google token is required", status_code=400)
    if not GOOGLE_OAUTH_CLIENT_ID:
        raise APIError("Google OAuth is not configured on server", status_code=503)
    if AUTH_REQUIRE_OTP:
        logger.warning("google_login_blocked reason=otp_required")
        raise APIError("Google login disabled", status_code=403)

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token
    except ModuleNotFoundError as exc:
        raise APIError(
            "google-auth is not installed on server. Run: pip install google-auth",
            status_code=503,
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
    _ensure_email_allowed(db=db, email=email)

    user, agency_id, role = _load_portal_user(db=db, email=email)
    app_token = create_access_token(user_id=user.id, email=user.email, role=role)
    return LoginData(
        user=UserProfile(
            id=user.id,
            email=user.email,
            name=str(idinfo.get("name") or ""),
            picture=str(idinfo.get("picture") or ""),
            provider="google",
            role=role,
            agency_id=agency_id,
        ),
        token=app_token,
        access_token=app_token,
    )
