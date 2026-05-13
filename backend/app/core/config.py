import os
from urllib.parse import urlparse
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv

load_dotenv()


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _is_placeholder_value(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    if not normalized:
        return True
    placeholder_markers = {
        "changeme",
        "change-me",
        "replace-me",
        "replace_me",
        "your-value",
        "your-secret",
        "your-key",
        "your-api-key",
        "your-database-url",
        "your-redis-url",
        "your-qdrant-url",
        "your-resend-api-key",
        "example",
        "example.com",
        "todo",
        "tbd",
        "dummy",
    }
    if normalized in placeholder_markers:
        return True
    return (
        normalized.startswith("your-")
        or normalized.startswith("placeholder")
        or "your-" in normalized
        or "placeholder" in normalized
        or "example" in normalized
        or "changeme" in normalized
        or "replace-me" in normalized
    )


GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
COLLECTION_NAME = os.getenv("COLLECTION_NAME")
JOB_COLLECTION_NAME = os.getenv("JOB_COLLECTION_NAME", "job_chunks")
CANDIDATE_COLLECTION_NAME = os.getenv("CANDIDATE_COLLECTION_NAME", "candidate_chunks")
INTERNAL_CANDIDATE_COLLECTION_NAME = os.getenv("INTERNAL_CANDIDATE_COLLECTION_NAME", "internal_candidate_chunks")
RECRUITER_PREFERENCES_COLLECTION_NAME = os.getenv("RECRUITER_PREFERENCES_COLLECTION_NAME", "recruiter_preferences")
PROXYCURL_API_KEY = os.getenv("PROXYCURL_API_KEY")
PDL_API_KEY = os.getenv("PDL_API_KEY")
PDL_URL = os.getenv("PDL_URL", "https://api.peopledatalabs.com/v5/person/search")
PDL_ENABLED = os.getenv("PDL_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
USE_INTERNAL_CANDIDATE_DB = os.getenv("USE_INTERNAL_CANDIDATE_DB", "false").strip().lower() in {"1", "true", "yes", "on"}
PROXYCURL_URL = os.getenv("PROXYCURL_URL", "https://api.ninjapear.com/v1/person/search")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
EMBEDDING_VERSION = os.getenv("EMBEDDING_VERSION", "v2_structured").strip()
VECTOR_SIZE = int(os.getenv("VECTOR_SIZE", "384"))
QDRANT_SEARCH_LIMIT = int(os.getenv("QDRANT_SEARCH_LIMIT", "5"))
PDL_SEARCH_SIZE = int(os.getenv("PDL_SEARCH_SIZE", "5"))
HTTP_TIMEOUT_SECONDS = int(os.getenv("HTTP_TIMEOUT_SECONDS", "15"))
DATABASE_URL = _required_env("DATABASE_URL")
JWT_SECRET = _required_env("JWT_SECRET")
JWT_EXPIRY_DAYS = int(os.getenv("JWT_EXPIRY_DAYS", "7"))
PUBLIC_APP_URL = _required_env("PUBLIC_APP_URL").strip().rstrip("/")
APP_ENV = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", os.getenv("NODE_ENV", "production"))).strip().lower() or "production"
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", PUBLIC_APP_URL).strip().rstrip("/")
if not FRONTEND_ORIGIN and PUBLIC_APP_URL:
    FRONTEND_ORIGIN = PUBLIC_APP_URL
if not FRONTEND_ORIGIN:
    FRONTEND_ORIGIN = "https://adam.pontis.one"

def _normalize_origin(value: str) -> str:
    parsed = urlparse(value.strip())
    if not parsed.scheme or not parsed.netloc:
        return value.strip().rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


CORS_ALLOW_ORIGINS = [_normalize_origin(FRONTEND_ORIGIN)]
COOKIE_SECURE = APP_ENV not in {"development", "dev", "local", "test"}
COOKIE_SAMESITE = "none"
AUTH_COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "pontis_auth").strip() or "pontis_auth"
CSRF_COOKIE_NAME = os.getenv("CSRF_COOKIE_NAME", "pontis_csrf").strip() or "pontis_csrf"
CSRF_HEADER_NAME = os.getenv("CSRF_HEADER_NAME", "X-CSRF-Token").strip() or "X-CSRF-Token"
CSRF_TOKEN_TTL_SECONDS = int(os.getenv("CSRF_TOKEN_TTL_SECONDS", "43200"))
AUTO_RECREATE_SCHEMA = os.getenv("AUTO_RECREATE_SCHEMA", "false").strip().lower() in {"1", "true", "yes", "on"}
SCORING_DEFAULT_MODE = os.getenv("SCORING_DEFAULT_MODE", "volume").strip().lower()
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "").strip()
POSTMARK_SERVER_TOKEN = os.getenv("POSTMARK_SERVER_TOKEN", "").strip()
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
RESEND_WEBHOOK_SECRET = os.getenv("RESEND_WEBHOOK_SECRET", "").strip()
OUTREACH_PROVIDER = os.getenv("OUTREACH_PROVIDER", "resend").strip().lower()
OUTREACH_FROM_EMAIL = os.getenv("OUTREACH_FROM_EMAIL", "info@pontis.one").strip()
FROM_EMAIL = os.getenv("FROM_EMAIL", OUTREACH_FROM_EMAIL).strip()
OUTREACH_REPLY_TO_EMAIL = os.getenv("OUTREACH_REPLY_TO_EMAIL", "info@pontis.one").strip()
OUTREACH_RESEND_FALLBACK_FROM_EMAIL = os.getenv("OUTREACH_RESEND_FALLBACK_FROM_EMAIL", "onboarding@resend.dev").strip()
BOOKING_PROVIDER = os.getenv("BOOKING_PROVIDER", "placeholder").strip().lower() or "placeholder"
INTERVIEW_PROVIDER = os.getenv("INTERVIEW_PROVIDER", "placeholder").strip().lower() or "placeholder"
INTERVIEW_BOOKING_LINK = os.getenv("INTERVIEW_BOOKING_LINK", "").strip()
BOOKING_PROVIDER_URL = os.getenv("BOOKING_PROVIDER_URL", INTERVIEW_BOOKING_LINK).strip()
INTERVIEW_PROVIDER_URL = os.getenv("INTERVIEW_PROVIDER_URL", "").strip()
OUTREACH_DRY_RUN = os.getenv("OUTREACH_DRY_RUN", "false").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_REAL_EMAIL_SENDING = os.getenv("ENABLE_REAL_EMAIL_SENDING", "true").strip().lower() in {"1", "true", "yes", "on"}
MERGE_API_KEY = os.getenv("MERGE_API_KEY", "").strip()
MERGE_ACCOUNT_TOKEN = os.getenv("MERGE_ACCOUNT_TOKEN", "").strip()
MERGE_BASE_URL = os.getenv("MERGE_BASE_URL", "https://api.merge.dev/api/ats/v1").strip()
REFRESH_CRON_ENABLED = os.getenv("REFRESH_CRON_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
REFRESH_INTERVAL_MINUTES = int(os.getenv("REFRESH_INTERVAL_MINUTES", "10"))
REFRESH_JOB_SCAN_LIMIT = int(os.getenv("REFRESH_JOB_SCAN_LIMIT", "20"))
REFRESH_CANDIDATE_LIMIT = int(os.getenv("REFRESH_CANDIDATE_LIMIT", "100"))
REFRESH_MIN_WINDOW_MINUTES = int(os.getenv("REFRESH_MIN_WINDOW_MINUTES", "30"))
STALE_DAYS = int(os.getenv("STALE_DAYS", "7"))
PDL_MIN_REQUEST_INTERVAL_SECONDS = float(os.getenv("PDL_MIN_REQUEST_INTERVAL_SECONDS", "0.35"))
RLHF_SMOOTHING_ALPHA = float(os.getenv("RLHF_SMOOTHING_ALPHA", "0.20"))
RLHF_FEEDBACK_HALF_LIFE_DAYS = int(os.getenv("RLHF_FEEDBACK_HALF_LIFE_DAYS", "21"))
RLHF_BASE_FEEDBACK_BIAS = float(os.getenv("RLHF_BASE_FEEDBACK_BIAS", "0.15"))
RLHF_MIN_FEEDBACK_BIAS = float(os.getenv("RLHF_MIN_FEEDBACK_BIAS", "0.06"))
OUTREACH_FOLLOWUP_DAYS = int(os.getenv("OUTREACH_FOLLOWUP_DAYS", "4"))
OUTREACH_FOLLOWUP_MAX_ATTEMPTS = int(os.getenv("OUTREACH_FOLLOWUP_MAX_ATTEMPTS", "2"))
OUTREACH_FOLLOWUP_INTERVAL_MINUTES = int(os.getenv("OUTREACH_FOLLOWUP_INTERVAL_MINUTES", "60"))
OUTREACH_LEARNING_INTERVAL_MINUTES = int(os.getenv("OUTREACH_LEARNING_INTERVAL_MINUTES", "15"))
OUTREACH_LEARNING_BATCH_LIMIT = int(os.getenv("OUTREACH_LEARNING_BATCH_LIMIT", "75"))
ENABLE_FOLLOWUPS = os.getenv("ENABLE_FOLLOWUPS", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_REPLY_DETECTION = os.getenv("ENABLE_REPLY_DETECTION", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_REPLY_POLLING = os.getenv("ENABLE_REPLY_POLLING", "true").strip().lower() in {"1", "true", "yes", "on"}
REPLY_POLL_INTERVAL_MINUTES = int(os.getenv("REPLY_POLL_INTERVAL_MINUTES", "3"))
REPLY_POLL_BATCH_SIZE = int(os.getenv("REPLY_POLL_BATCH_SIZE", "50"))
REPLY_INBOX_PROVIDER = os.getenv("REPLY_INBOX_PROVIDER", "imap").strip().lower()
ENABLE_PLAYWRIGHT_JOB_PARSER = os.getenv("ENABLE_PLAYWRIGHT_JOB_PARSER", "false").strip().lower() in {"1", "true", "yes", "on"}
INBOUND_ATTACHMENT_MAX_BYTES = int(os.getenv("INBOUND_ATTACHMENT_MAX_BYTES", "10485760"))
REPLY_IMAP_HOST = os.getenv("REPLY_IMAP_HOST", "").strip()
REPLY_IMAP_PORT = int(os.getenv("REPLY_IMAP_PORT", "993"))
REPLY_IMAP_USERNAME = os.getenv("REPLY_IMAP_USERNAME", "").strip()
REPLY_IMAP_PASSWORD = os.getenv("REPLY_IMAP_PASSWORD", "").strip()
REPLY_IMAP_FOLDER = os.getenv("REPLY_IMAP_FOLDER", "INBOX").strip() or "INBOX"
REPLY_ATTACHMENT_STORAGE_DIR = os.getenv("REPLY_ATTACHMENT_STORAGE_DIR", "backend/storage/reply_attachments").strip()
REPLY_ATTACHMENT_PUBLIC_BASE_URL = os.getenv("REPLY_ATTACHMENT_PUBLIC_BASE_URL", "").strip().rstrip("/")
FOLLOW_UP_DELAY_MINUTES = int(os.getenv("FOLLOW_UP_DELAY_MINUTES", "60"))
AUTH_REQUIRE_OTP = os.getenv("AUTH_REQUIRE_OTP", "false").strip().lower() in {"1", "true", "yes", "on"}
NO_CANDIDATES_COOLDOWN_MINUTES = int(os.getenv("NO_CANDIDATES_COOLDOWN_MINUTES", "60"))
ATS_RETRY_INTERVAL_MINUTES = int(os.getenv("ATS_RETRY_INTERVAL_MINUTES", "30"))
ATS_RETRY_MAX_ATTEMPTS = int(os.getenv("ATS_RETRY_MAX_ATTEMPTS", "3"))
DEFAULT_ATS_PROVIDER = os.getenv("DEFAULT_ATS_PROVIDER", "mock").strip().lower() or "mock"
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "").strip()
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "").strip()
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "").strip()
SLACK_SKIP_SIGNATURE_VERIFICATION = os.getenv("SLACK_SKIP_SIGNATURE_VERIFICATION", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
# Disabled after Postgres migration: persistent sqlite cache backend is no longer active.
PERSISTENT_CACHE_PATH = os.getenv("PERSISTENT_CACHE_PATH", "disabled").strip()
REDIS_URL = os.getenv("REDIS_URL", "").strip()
if not REDIS_URL:
    REDIS_URL = os.getenv("RAILWAY_REDIS_URL", os.getenv("RAILWAY_PRIVATE_URL", "")).strip()
if REDIS_URL and "YOUR-REDIS-HOST" in REDIS_URL:
    REDIS_URL = ""
INTERNAL_API_KEY = _required_env("INTERNAL_API_KEY")
WEBHOOK_SHARED_SECRET = os.getenv("WEBHOOK_SHARED_SECRET", "").strip()
ADMIN_EMAILS = {item.strip().lower() for item in os.getenv("ADMIN_EMAILS", "").split(",") if item.strip()}
OPS_EMAILS = {item.strip().lower() for item in os.getenv("OPS_EMAILS", "").split(",") if item.strip()}
JOB_QUEUE_WORKERS_PER_TYPE = int(os.getenv("JOB_QUEUE_WORKERS_PER_TYPE", "1"))
JOB_QUEUE_VISIBILITY_TIMEOUT_SECONDS = int(os.getenv("JOB_QUEUE_VISIBILITY_TIMEOUT_SECONDS", "120"))
JOB_QUEUE_JOB_TTL_SECONDS = int(os.getenv("JOB_QUEUE_JOB_TTL_SECONDS", "604800"))
JOB_QUEUE_BACKOFF_BASE_SECONDS = int(os.getenv("JOB_QUEUE_BACKOFF_BASE_SECONDS", "10"))
RATE_LIMIT_AUTH_REQUEST_OTP_PER_MINUTE = int(os.getenv("RATE_LIMIT_AUTH_REQUEST_OTP_PER_MINUTE", "5"))
RATE_LIMIT_AUTH_VERIFY_OTP_PER_MINUTE = int(os.getenv("RATE_LIMIT_AUTH_VERIFY_OTP_PER_MINUTE", "5"))
RATE_LIMIT_CANDIDATES_PER_MINUTE = int(os.getenv("RATE_LIMIT_CANDIDATES_PER_MINUTE", "60"))
ENABLE_MOCK_PDL = os.getenv("ENABLE_MOCK_PDL", "false").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_FAKE_EMAILS = os.getenv("ENABLE_FAKE_EMAILS", "false").strip().lower() in {"1", "true", "yes", "on"}
INTERVIEW_SESSION_TTL_MINUTES = int(os.getenv("INTERVIEW_SESSION_TTL_MINUTES", "120"))
MIN_SKILL_MATCH_THRESHOLD = int(os.getenv("MIN_SKILL_MATCH_THRESHOLD", "1"))
ENABLE_HARD_FILTERING = os.getenv("ENABLE_HARD_FILTERING", "true").strip().lower() in {"1", "true", "yes", "on"}
RANKING_WEIGHTS = {
    "similarity": float(os.getenv("RANKING_WEIGHT_SIMILARITY", "0.7")),
    "skill_overlap": float(os.getenv("RANKING_WEIGHT_SKILL_OVERLAP", "0.2")),
    "experience": float(os.getenv("RANKING_WEIGHT_EXPERIENCE", "0.1")),
}
FEEDBACK_WEIGHTS = {
    "accept": float(os.getenv("FEEDBACK_WEIGHT_ACCEPT", "0.15")),
    "reject": float(os.getenv("FEEDBACK_WEIGHT_REJECT", "-0.25")),
}


def missing_secret_warnings() -> list[str]:
    warnings: list[str] = []
    if not GROQ_API_KEY:
        warnings.append("GROQ_API_KEY is missing; LLM features will use local fallback.")
    if PDL_ENABLED and not PDL_API_KEY:
        warnings.append("PDL_API_KEY is missing; candidate enrichment will skip PDL.")
    if not REDIS_URL:
        warnings.append("REDIS_URL is missing; cache will use in-memory fallback.")
    if not GOOGLE_OAUTH_CLIENT_ID:
        warnings.append("GOOGLE_OAUTH_CLIENT_ID is missing; Google login will be unavailable.")
    if not SLACK_BOT_TOKEN:
        warnings.append("SLACK_BOT_TOKEN is missing; Slack message delivery will be disabled.")
    if not SLACK_SIGNING_SECRET:
        warnings.append("SLACK_SIGNING_SECRET is missing; Slack request verification will fail.")
    if SLACK_SKIP_SIGNATURE_VERIFICATION:
        warnings.append("Slack signature verification is disabled; re-enable it after debugging.")
    if BOOKING_PROVIDER == "calendly" and not BOOKING_PROVIDER_URL:
        warnings.append("BOOKING_PROVIDER is calendly but BOOKING_PROVIDER_URL is missing.")
    if INTERVIEW_PROVIDER == "zoom" and not INTERVIEW_PROVIDER_URL:
        warnings.append("INTERVIEW_PROVIDER is zoom but INTERVIEW_PROVIDER_URL is missing.")
    return warnings


@dataclass(frozen=True)
class ConfigIssue:
    key: str
    severity: str
    message: str
    value: str = ""


def _is_valid_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return bool(parsed.scheme and parsed.netloc)


def validate_runtime_config(*, production_mode: bool | None = None) -> dict[str, Any]:
    """Validate startup config once and surface a clear platform diagnostic."""
    resolved_production = APP_ENV in {"production", "prod"} if production_mode is None else bool(production_mode)
    issues: list[ConfigIssue] = []

    critical_checks = {
        "DATABASE_URL": DATABASE_URL,
        "JWT_SECRET": JWT_SECRET,
        "PUBLIC_APP_URL": PUBLIC_APP_URL,
        "INTERNAL_API_KEY": INTERNAL_API_KEY,
    }
    for key, value in critical_checks.items():
        if _is_placeholder_value(str(value or "")):
            issues.append(ConfigIssue(key=key, severity="critical", message=f"{key} is required and must not be a placeholder"))

    if DATABASE_URL and not str(DATABASE_URL).startswith(("postgresql://", "postgres://", "sqlite:///")):
        issues.append(ConfigIssue(key="DATABASE_URL", severity="critical", message="DATABASE_URL must be a valid database URL"))
    if resolved_production and str(DATABASE_URL or "").startswith("sqlite:///"):
        issues.append(ConfigIssue(key="DATABASE_URL", severity="critical", message="SQLite is not allowed in production"))

    if PUBLIC_APP_URL and not _is_valid_url(PUBLIC_APP_URL):
        issues.append(ConfigIssue(key="PUBLIC_APP_URL", severity="critical", message="PUBLIC_APP_URL must be an absolute URL"))

    if FRONTEND_ORIGIN and not _is_valid_url(FRONTEND_ORIGIN):
        issues.append(ConfigIssue(key="FRONTEND_ORIGIN", severity="warning", message="FRONTEND_ORIGIN should be an absolute URL"))

    if resolved_production:
        production_required = {
            "JWT_SECRET": JWT_SECRET,
            "DATABASE_URL": DATABASE_URL,
            "REDIS_URL": REDIS_URL,
            "QDRANT_URL": QDRANT_URL,
            "QDRANT_API_KEY": QDRANT_API_KEY,
            "RESEND_API_KEY": RESEND_API_KEY,
            "INTERNAL_API_KEY": INTERNAL_API_KEY,
            "GOOGLE_OAUTH_CLIENT_ID": GOOGLE_OAUTH_CLIENT_ID,
        }
        for key, value in production_required.items():
            if _is_placeholder_value(str(value or "")):
                issues.append(ConfigIssue(key=key, severity="critical", message=f"{key} is required in production and must not be empty or placeholder"))
        if OUTREACH_DRY_RUN:
            issues.append(ConfigIssue(key="OUTREACH_DRY_RUN", severity="warning", message="Outreach is running in dry-run mode"))
        if not REDIS_URL:
            issues.append(ConfigIssue(key="REDIS_URL", severity="critical", message="Redis is required in production"))
        if not COOKIE_SECURE:
            issues.append(ConfigIssue(key="COOKIE_SECURE", severity="warning", message="Secure cookies are disabled in a production-like environment"))
    if OUTREACH_PROVIDER == "resend" and not RESEND_API_KEY:
        issues.append(ConfigIssue(key="RESEND_API_KEY", severity="warning", message="Real outreach is configured but RESEND_API_KEY is missing"))
    if not RESEND_WEBHOOK_SECRET:
        issues.append(ConfigIssue(key="RESEND_WEBHOOK_SECRET", severity="warning", message="Inbound Resend webhooks are enabled but RESEND_WEBHOOK_SECRET is missing"))

    return {
        "environment": APP_ENV,
        "production_mode": resolved_production,
        "issues": [issue.__dict__ for issue in issues],
        "has_critical_issues": any(issue.severity == "critical" for issue in issues),
        "has_warnings": any(issue.severity == "warning" for issue in issues),
        "missing_secrets": missing_secret_warnings(),
    }


def config_diagnostics() -> dict[str, Any]:
    validation = validate_runtime_config()
    return {
        "environment": APP_ENV,
        "production_mode": validation["production_mode"],
        "critical": [item for item in validation["issues"] if item["severity"] == "critical"],
        "warnings": [item for item in validation["issues"] if item["severity"] == "warning"],
        "missing_secrets": validation["missing_secrets"],
        "derived": {
            "cookie_secure": COOKIE_SECURE,
            "cookie_samesite": COOKIE_SAMESITE,
            "cors_allow_origins": CORS_ALLOW_ORIGINS,
            "queue_workers_per_type": JOB_QUEUE_WORKERS_PER_TYPE,
            "queue_visibility_timeout_seconds": JOB_QUEUE_VISIBILITY_TIMEOUT_SECONDS,
            "queue_job_ttl_seconds": JOB_QUEUE_JOB_TTL_SECONDS,
        },
    }


def is_production_environment() -> bool:
    return APP_ENV in {"production", "prod"}
