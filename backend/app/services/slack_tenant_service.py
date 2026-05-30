from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.parse import urlencode

import requests
from sqlalchemy.orm import Session

from app.core.config import (
    PUBLIC_APP_URL,
    SLACK_BOT_TOKEN,
    SLACK_CLIENT_ID,
    SLACK_CLIENT_SECRET,
    SLACK_OAUTH_SCOPES,
    SLACK_REDIRECT_URI,
    SLACK_STATE_SECRET,
)
from app.db.repositories import (
    CompanyRepository,
    SlackInstallationRepository,
    SlackUserRepository,
    UserRepository,
)
from app.models.entities import CompanyEntity, SlackInstallationEntity, SlackUserEntity, UserEntity
from app.services.audit_service import record_audit_event
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)

SLACK_OAUTH_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_OAUTH_ACCESS_URL = "https://slack.com/api/oauth.v2.access"
SLACK_USER_INFO_URL = "https://slack.com/api/users.info"
SLACK_API_TIMEOUT_SECONDS = 20
SLACK_STATE_TTL_SECONDS = 15 * 60


@dataclass(frozen=True)
class SlackWorkspaceContext:
    company: CompanyEntity
    installation: SlackInstallationEntity
    slack_user: SlackUserEntity | None = None
    internal_user: UserEntity | None = None


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _state_secret() -> str:
    configured = _normalize_text(SLACK_STATE_SECRET or "")
    if configured:
        return configured
    fallback = _normalize_text(SLACK_CLIENT_SECRET or "")
    if fallback:
        return fallback
    return _normalize_text(SLACK_BOT_TOKEN or "")


def build_slack_oauth_state(*, company_id: str, issued_at: datetime | None = None) -> str:
    payload = {
        "companyId": _normalize_text(company_id),
        "iat": int((issued_at or datetime.now(timezone.utc)).timestamp()),
        "exp": int((issued_at or datetime.now(timezone.utc)).timestamp()) + SLACK_STATE_TTL_SECONDS,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    secret = _state_secret()
    if not secret:
        raise APIError("Slack OAuth state secret is not configured", status_code=500)
    signature = hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    sig = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{encoded}.{sig}"


def parse_slack_oauth_state(state: str) -> dict[str, Any]:
    raw_state = _normalize_text(state)
    if not raw_state or "." not in raw_state:
        raise APIError("Invalid Slack OAuth state", status_code=400)
    encoded, sig = raw_state.split(".", 1)
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
        payload = json.loads(raw)
    except Exception as exc:  # pragma: no cover - defensive guard
        raise APIError("Invalid Slack OAuth state", status_code=400) from exc
    secret = _state_secret()
    if not secret:
        raise APIError("Slack OAuth state secret is not configured", status_code=500)
    expected = base64.urlsafe_b64encode(hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).digest()).decode("ascii").rstrip("=")
    if not hmac.compare_digest(expected, sig):
        raise APIError("Invalid Slack OAuth state signature", status_code=400)
    exp = int(payload.get("exp") or 0)
    if exp and exp < int(datetime.now(timezone.utc).timestamp()):
        raise APIError("Slack OAuth state expired", status_code=400)
    return payload if isinstance(payload, dict) else {}


def build_slack_install_url(*, company_id: str) -> str:
    if not _normalize_text(SLACK_CLIENT_ID):
        raise APIError("SLACK_CLIENT_ID is required for Slack OAuth", status_code=500)
    redirect_uri = _normalize_text(SLACK_REDIRECT_URI or f"{PUBLIC_APP_URL.rstrip('/')}/slack/oauth/callback")
    state = build_slack_oauth_state(company_id=company_id)
    params = urlencode(
        {
            "client_id": SLACK_CLIENT_ID,
            "scope": SLACK_OAUTH_SCOPES,
            "redirect_uri": redirect_uri,
            "state": state,
            "user_scope": "users:read,users:read.email",
        }
    )
    return f"{SLACK_OAUTH_AUTHORIZE_URL}?{params}"


def exchange_slack_oauth_code(*, code: str) -> dict[str, Any]:
    if not _normalize_text(SLACK_CLIENT_ID) or not _normalize_text(SLACK_CLIENT_SECRET):
        raise APIError("Slack OAuth client credentials are not configured", status_code=500)
    redirect_uri = _normalize_text(SLACK_REDIRECT_URI or f"{PUBLIC_APP_URL.rstrip('/')}/slack/oauth/callback")
    response = requests.post(
        SLACK_OAUTH_ACCESS_URL,
        data={
            "client_id": SLACK_CLIENT_ID,
            "client_secret": SLACK_CLIENT_SECRET,
            "code": _normalize_text(code),
            "redirect_uri": redirect_uri,
        },
        timeout=SLACK_API_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise APIError(f"Slack OAuth exchange failed: {payload.get('error') if isinstance(payload, dict) else 'invalid_response'}", status_code=400)
    return payload


def fetch_slack_user_profile(*, bot_token: str, slack_user_id: str) -> dict[str, str]:
    token = _normalize_text(bot_token)
    user_id = _normalize_text(slack_user_id)
    if not token or not user_id:
        return {"email": "", "display_name": ""}
    try:
        response = requests.get(
            SLACK_USER_INFO_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"user": user_id},
            timeout=SLACK_API_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not payload.get("ok"):
            return {"email": "", "display_name": ""}
        user = payload.get("user") or {}
        profile = user.get("profile") or {}
        return {
            "email": _normalize_text(profile.get("email") or ""),
            "display_name": _normalize_text(profile.get("display_name") or profile.get("real_name") or user.get("name") or ""),
        }
    except Exception:
        return {"email": "", "display_name": ""}


class SlackCompanyResolver:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.installations = SlackInstallationRepository(db)
        self.users = SlackUserRepository(db)
        self.companies = CompanyRepository(db)

    def resolve_installation(self, team_id: str) -> SlackInstallationEntity:
        installation = self.installations.get_active_by_team_id(team_id)
        if not installation:
            raise APIError("Slack workspace is not installed or inactive", status_code=404)
        return installation

    def resolve_company(self, team_id: str) -> CompanyEntity:
        installation = self.resolve_installation(team_id)
        company = self.companies.get_by_id(installation.company_id)
        if not company:
            raise APIError("Company not found for Slack workspace", status_code=404)
        return company

    def resolve_workspace_context(
        self,
        *,
        team_id: str,
        slack_user_id: str = "",
        slack_user_email: str = "",
        slack_user_display_name: str = "",
    ) -> SlackWorkspaceContext:
        installation = self.resolve_installation(team_id)
        company = self.companies.get_by_id(installation.company_id)
        if not company:
            raise APIError("Company not found for Slack workspace", status_code=404)

        slack_user: SlackUserEntity | None = None
        internal_user: UserEntity | None = None
        if _normalize_text(slack_user_id):
            slack_user = self.users.upsert(
                company_id=company.id,
                slack_installation_id=installation.id,
                slack_user_id=slack_user_id,
                email=slack_user_email,
                display_name=slack_user_display_name,
                role="recruiter",
            )
            if slack_user.internal_user_id:
                internal_user = self.db.get(UserEntity, slack_user.internal_user_id) if slack_user.internal_user_id else None
        return SlackWorkspaceContext(company=company, installation=installation, slack_user=slack_user, internal_user=internal_user)

    def resolve_bot_token(self, *, team_id: str = "", company_id: str = "") -> str:
        installation: SlackInstallationEntity | None = None
        if _normalize_text(team_id):
            installation = self.installations.get_active_by_team_id(team_id)
        if not installation and _normalize_text(company_id):
            installation = self.installations.get_active_for_company(company_id)
        if not installation or not _normalize_text(installation.bot_access_token):
            return ""
        return installation.bot_access_token

    def resolve_internal_user_id(self, *, company_id: str, slack_user_id: str) -> str:
        row = self.users.get_by_company_and_user_id(company_id=company_id, slack_user_id=slack_user_id)
        if not row:
            return ""
        return _normalize_text(row.internal_user_id or "")


def record_slack_audit_event(
    *,
    db: Session,
    company_id: str | None = None,
    user_id: str | None = None,
    slack_user_id: str = "",
    action_type: str,
    entity_type: str,
    entity_id: str,
    payload: dict | None = None,
    actor_id: str | None = None,
    request_id: str = "",
    ip_address: str = "",
    user_agent: str = "",
) -> Any:
    return record_audit_event(
        db=db,
        actor_id=actor_id or user_id,
        actor_type="user",
        action=action_type,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata=dict(payload or {}),
        ip_address=ip_address,
        user_agent=user_agent,
        request_id=request_id,
        company_id=company_id,
        user_id=user_id or actor_id,
        slack_user_id=slack_user_id,
        action_type=action_type,
        payload=dict(payload or {}),
    )
