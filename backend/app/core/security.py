from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import hashlib
import logging
import secrets
from typing import Iterable

import jwt
from fastapi import Request
from starlette.responses import Response

from app.core.config import (
    AUTH_COOKIE_NAME,
    COOKIE_SAMESITE,
    COOKIE_SECURE,
    CSRF_COOKIE_NAME,
    CSRF_TOKEN_TTL_SECONDS,
    JWT_EXPIRY_DAYS,
    JWT_SECRET,
    is_production_environment,
)
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)
_ephemeral_jwt_secret: str | None = None
SUPER_ADMIN_ROLE = "superadmin"
AGENCY_USER_ROLE = "AGENCY_USER"
_ROLE_ALIASES = {
    "admin": SUPER_ADMIN_ROLE,
    "super_admin": SUPER_ADMIN_ROLE,
    "superadmin": SUPER_ADMIN_ROLE,
    "internal_ops": SUPER_ADMIN_ROLE,
    "agency_user": AGENCY_USER_ROLE,
    "recruiter": AGENCY_USER_ROLE,
    "user": AGENCY_USER_ROLE,
}


def _resolved_jwt_secret() -> str:
    global _ephemeral_jwt_secret

    configured = (JWT_SECRET or "").strip()
    if configured:
        if is_production_environment() and configured.lower() in {"changeme", "change-me", "replace-me", "replace_me", "your-secret"}:
            raise RuntimeError("JWT_SECRET is a placeholder and cannot be used in production")
        return configured

    if is_production_environment():
        raise RuntimeError("JWT_SECRET is required in production")

    if not _ephemeral_jwt_secret:
        _ephemeral_jwt_secret = secrets.token_urlsafe(48)
        logger.warning(
            "JWT_SECRET is missing; using ephemeral in-memory signing key. "
            "All tokens will be invalid after process restart. Configure JWT_SECRET for stable auth."
        )
    return _ephemeral_jwt_secret


def normalize_app_role(role: str | None) -> str:
    normalized = (role or "").strip().lower()
    if not normalized:
        return AGENCY_USER_ROLE
    return _ROLE_ALIASES.get(normalized, AGENCY_USER_ROLE)


def is_super_admin_role(role: str | None) -> bool:
    return normalize_app_role(role) == SUPER_ADMIN_ROLE


def create_access_token(*, user_id: str, email: str, role: str = AGENCY_USER_ROLE) -> str:
    expiry = datetime.now(tz=timezone.utc) + timedelta(days=JWT_EXPIRY_DAYS)
    payload = {
        "sub": user_id,
        "email": email,
        "role": normalize_app_role(role),
        "exp": int(expiry.timestamp()),
    }
    return jwt.encode(payload, _resolved_jwt_secret(), algorithm="HS256")


def verify_access_token(token: str) -> dict:
    try:
        decoded = jwt.decode(token, _resolved_jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise APIError("Invalid or expired token", status_code=401) from exc

    if not decoded.get("sub"):
        raise APIError("Invalid token payload", status_code=401)
    return decoded


def create_csrf_token(*, user_id: str | None = None) -> str:
    expiry = datetime.now(tz=timezone.utc) + timedelta(seconds=max(60, CSRF_TOKEN_TTL_SECONDS))
    nonce = secrets.token_urlsafe(24)
    payload = f"{user_id or 'anon'}:{int(expiry.timestamp())}:{nonce}"
    signature = hashlib.sha256(f"{payload}:{_resolved_jwt_secret()}".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(f"{payload}:{base64.urlsafe_b64encode(signature).decode('ascii')}".encode("utf-8")).decode("ascii")


def verify_csrf_token(token: str | None, *, user_id: str | None = None) -> None:
    raw = (token or "").strip()
    if not raw:
        raise APIError("Missing CSRF token", status_code=403)
    try:
        decoded = base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8")
        parts = decoded.split(":")
        if len(parts) != 4:
            raise ValueError("invalid token")
        token_user, expires_at_raw, nonce, signature_raw = parts
        if user_id and token_user != user_id and token_user != "anon":
            raise ValueError("user mismatch")
        if int(expires_at_raw) < int(datetime.now(timezone.utc).timestamp()):
            raise ValueError("expired")
        payload = f"{token_user}:{expires_at_raw}:{nonce}"
        expected = hashlib.sha256(f"{payload}:{_resolved_jwt_secret()}".encode("utf-8")).digest()
        if not secrets.compare_digest(base64.urlsafe_b64encode(expected).decode("ascii"), signature_raw):
            raise ValueError("signature mismatch")
    except Exception as exc:
        raise APIError("Invalid CSRF token", status_code=403) from exc


def set_auth_cookies(response: Response, *, token: str, csrf_token: str, max_age_seconds: int) -> None:
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
        max_age=max_age_seconds,
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
        max_age=max_age_seconds,
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


def get_current_user(request: Request) -> dict[str, str]:
    user = getattr(request.state, "user", None)
    if not user:
        raise APIError("Unauthorized", status_code=401)
    return user


def _normalize_role(role: str) -> str:
    return normalize_app_role(role)


def has_role(user: dict[str, str], allowed_roles: Iterable[str]) -> bool:
    user_role = _normalize_role(user.get("role") or "recruiter")
    allowed = {_normalize_role(role) for role in allowed_roles if _normalize_role(role)}
    return bool(user_role and user_role in allowed)


def require_role(*allowed_roles: str):
    from fastapi import Depends

    def _dependency(user: dict[str, str] = Depends(get_current_user)) -> dict[str, str]:
        if allowed_roles and not has_role(user, allowed_roles):
            raise APIError("Forbidden", status_code=403)
        return user

    return _dependency
