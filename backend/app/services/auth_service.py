from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import GOOGLE_OAUTH_CLIENT_ID
from app.core.security import create_access_token
from app.db.repositories import UserRepository
from app.schemas.user import LoginData, UserProfile
from app.utils.exceptions import APIError


ALLOWED_PROVIDERS = {"email", "google"}


def login_user(*, db: Session, email: str, provider: str) -> LoginData:
    normalized_email = (email or "").strip().lower()
    normalized_provider = (provider or "email").strip().lower()

    if not normalized_email:
        raise APIError("Email is required", status_code=400)
    if normalized_provider not in ALLOWED_PROVIDERS:
        raise APIError("provider must be 'email' or 'google'", status_code=400)

    users = UserRepository(db)
    user = users.get_by_email(normalized_email)
    if not user:
        user = users.create(normalized_email)
        db.commit()
    else:
        db.flush()

    token = create_access_token(user_id=user.id, email=user.email)
    return LoginData(
        user=UserProfile(id=user.id, email=user.email, provider=normalized_provider),
        token=token,
        access_token=token,
    )


def login_with_google_token(*, db: Session, token: str) -> LoginData:
    raw_token = (token or "").strip()
    if not raw_token:
        raise APIError("Google token is required", status_code=400)
    if not GOOGLE_OAUTH_CLIENT_ID:
        raise APIError("Google OAuth is not configured on server", status_code=500)

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
