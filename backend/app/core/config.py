import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
COLLECTION_NAME = os.getenv("COLLECTION_NAME")
JOB_COLLECTION_NAME = os.getenv("JOB_COLLECTION_NAME", "job_chunks")
CANDIDATE_COLLECTION_NAME = os.getenv("CANDIDATE_COLLECTION_NAME", "candidate_chunks")
PROXYCURL_API_KEY = os.getenv("PROXYCURL_API_KEY")
PDL_API_KEY = os.getenv("PDL_API_KEY")
PDL_URL = os.getenv("PDL_URL", "https://api.peopledatalabs.com/v5/person/search")
PROXYCURL_URL = os.getenv("PROXYCURL_URL", "https://api.ninjapear.com/v1/person/search")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
EMBEDDING_VERSION = os.getenv("EMBEDDING_VERSION", "v2_structured").strip()
VECTOR_SIZE = int(os.getenv("VECTOR_SIZE", "384"))
QDRANT_SEARCH_LIMIT = int(os.getenv("QDRANT_SEARCH_LIMIT", "5"))
PDL_SEARCH_SIZE = int(os.getenv("PDL_SEARCH_SIZE", "5"))
HTTP_TIMEOUT_SECONDS = int(os.getenv("HTTP_TIMEOUT_SECONDS", "15"))
DATABASE_URL = os.getenv("DATABASE_URL", "")
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_EXPIRY_DAYS = int(os.getenv("JWT_EXPIRY_DAYS", "7"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
CORS_ALLOW_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",") if origin.strip()]
PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", "http://localhost:3000").strip().rstrip("/")
AUTO_RECREATE_SCHEMA = os.getenv("AUTO_RECREATE_SCHEMA", "false").strip().lower() in {"1", "true", "yes", "on"}
SCORING_DEFAULT_MODE = os.getenv("SCORING_DEFAULT_MODE", "volume").strip().lower()
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "").strip()
POSTMARK_SERVER_TOKEN = os.getenv("POSTMARK_SERVER_TOKEN", "").strip()
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
OUTREACH_PROVIDER = os.getenv("OUTREACH_PROVIDER", "resend").strip().lower()
OUTREACH_FROM_EMAIL = os.getenv("OUTREACH_FROM_EMAIL", "info@pontis.one").strip()
FROM_EMAIL = os.getenv("FROM_EMAIL", OUTREACH_FROM_EMAIL).strip()
OUTREACH_REPLY_TO_EMAIL = os.getenv("OUTREACH_REPLY_TO_EMAIL", "hiring@yourdomain.com").strip()
OUTREACH_RESEND_FALLBACK_FROM_EMAIL = os.getenv("OUTREACH_RESEND_FALLBACK_FROM_EMAIL", "onboarding@resend.dev").strip()
OUTREACH_DRY_RUN = os.getenv("OUTREACH_DRY_RUN", "false").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_REAL_EMAIL_SENDING = os.getenv("ENABLE_REAL_EMAIL_SENDING", "false").strip().lower() in {"1", "true", "yes", "on"}
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
ENABLE_FOLLOWUPS = os.getenv("ENABLE_FOLLOWUPS", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_REPLY_DETECTION = os.getenv("ENABLE_REPLY_DETECTION", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_REPLY_POLLING = os.getenv("ENABLE_REPLY_POLLING", "true").strip().lower() in {"1", "true", "yes", "on"}
REPLY_POLL_INTERVAL_MINUTES = int(os.getenv("REPLY_POLL_INTERVAL_MINUTES", "3"))
REPLY_INBOX_PROVIDER = os.getenv("REPLY_INBOX_PROVIDER", "imap").strip().lower()
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
SLACK_SKIP_SIGNATURE_VERIFICATION = os.getenv("SLACK_SKIP_SIGNATURE_VERIFICATION", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
# Disabled after Postgres migration: persistent sqlite cache backend is no longer active.
PERSISTENT_CACHE_PATH = os.getenv("PERSISTENT_CACHE_PATH", "disabled").strip()
REDIS_URL = os.getenv("REDIS_URL", "").strip()
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "").strip()
RATE_LIMIT_AUTH_REQUEST_OTP_PER_MINUTE = int(os.getenv("RATE_LIMIT_AUTH_REQUEST_OTP_PER_MINUTE", "5"))
RATE_LIMIT_AUTH_VERIFY_OTP_PER_MINUTE = int(os.getenv("RATE_LIMIT_AUTH_VERIFY_OTP_PER_MINUTE", "5"))
RATE_LIMIT_CANDIDATES_PER_MINUTE = int(os.getenv("RATE_LIMIT_CANDIDATES_PER_MINUTE", "60"))
ENABLE_MOCK_PDL = os.getenv("ENABLE_MOCK_PDL", "false").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_FAKE_EMAILS = os.getenv("ENABLE_FAKE_EMAILS", "true").strip().lower() in {"1", "true", "yes", "on"}
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

if "http://localhost:3000" not in CORS_ALLOW_ORIGINS and "*" not in CORS_ALLOW_ORIGINS:
    CORS_ALLOW_ORIGINS.append("http://localhost:3000")


def missing_secret_warnings() -> list[str]:
    warnings: list[str] = []
    if not JWT_SECRET.strip():
        warnings.append("JWT_SECRET is missing; authentication is not production-safe.")
    if not OPENAI_API_KEY:
        warnings.append("OPENAI_API_KEY is missing; OpenAI features will use local fallback.")
    if not PDL_API_KEY:
        warnings.append("PDL_API_KEY is missing; candidate enrichment will skip PDL.")
    if not REDIS_URL:
        warnings.append("REDIS_URL is missing; cache will use in-memory fallback.")
    if not GOOGLE_OAUTH_CLIENT_ID:
        warnings.append("GOOGLE_OAUTH_CLIENT_ID is missing; Google login will be unavailable.")
    if not INTERNAL_API_KEY:
        warnings.append("INTERNAL_API_KEY is missing; /api/health and /metrics should rely on bearer auth only.")
    if not SLACK_BOT_TOKEN:
        warnings.append("SLACK_BOT_TOKEN is missing; Slack message delivery will be disabled.")
    if not SLACK_SIGNING_SECRET:
        warnings.append("SLACK_SIGNING_SECRET is missing; Slack request verification will fail.")
    if SLACK_SKIP_SIGNATURE_VERIFICATION:
        warnings.append("Slack signature verification is disabled; re-enable it after debugging.")
    if ENABLE_REPLY_POLLING and REPLY_INBOX_PROVIDER == "imap":
        if not REPLY_IMAP_HOST:
            warnings.append("REPLY_IMAP_HOST is missing; reply polling will stay disabled.")
        if not REPLY_IMAP_USERNAME:
            warnings.append("REPLY_IMAP_USERNAME is missing; reply polling will stay disabled.")
        if not REPLY_IMAP_PASSWORD:
            warnings.append("REPLY_IMAP_PASSWORD is missing; reply polling will stay disabled.")
    return warnings
