from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import secrets

import jwt
from fastapi import Request

from app.core.config import JWT_EXPIRY_DAYS, JWT_SECRET
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)
_ephemeral_jwt_secret: str | None = None


def _resolved_jwt_secret() -> str:
    global _ephemeral_jwt_secret

    configured = (JWT_SECRET or "").strip()
    if configured:
        return configured

    if not _ephemeral_jwt_secret:
        _ephemeral_jwt_secret = secrets.token_urlsafe(48)
        logger.warning(
            "JWT_SECRET is missing; using ephemeral in-memory signing key. "
            "All tokens will be invalid after process restart. Configure JWT_SECRET for stable auth."
        )
    return _ephemeral_jwt_secret


def create_access_token(*, user_id: str, email: str) -> str:
    expiry = datetime.now(tz=timezone.utc) + timedelta(days=JWT_EXPIRY_DAYS)
    payload = {
        "sub": user_id,
        "email": email,
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


def get_current_user(request: Request) -> dict[str, str]:
    user = getattr(request.state, "user", None)
    if not user:
        raise APIError("Unauthorized", status_code=401)
    return user
