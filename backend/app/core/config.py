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
VECTOR_SIZE = int(os.getenv("VECTOR_SIZE", "384"))
QDRANT_SEARCH_LIMIT = int(os.getenv("QDRANT_SEARCH_LIMIT", "5"))
PDL_SEARCH_SIZE = int(os.getenv("PDL_SEARCH_SIZE", "5"))
HTTP_TIMEOUT_SECONDS = int(os.getenv("HTTP_TIMEOUT_SECONDS", "15"))
DATABASE_URL = os.getenv("DATABASE_URL", "")
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_EXPIRY_DAYS = int(os.getenv("JWT_EXPIRY_DAYS", "7"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
CORS_ALLOW_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",") if origin.strip()]
AUTO_RECREATE_SCHEMA = os.getenv("AUTO_RECREATE_SCHEMA", "false").strip().lower() in {"1", "true", "yes", "on"}
SCORING_DEFAULT_MODE = os.getenv("SCORING_DEFAULT_MODE", "volume").strip().lower()
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "").strip()
POSTMARK_SERVER_TOKEN = os.getenv("POSTMARK_SERVER_TOKEN", "").strip()
OUTREACH_PROVIDER = os.getenv("OUTREACH_PROVIDER", "sendgrid").strip().lower()
OUTREACH_FROM_EMAIL = os.getenv("OUTREACH_FROM_EMAIL", "talent@pontis.local").strip()
OUTREACH_DRY_RUN = os.getenv("OUTREACH_DRY_RUN", "true").strip().lower() in {"1", "true", "yes", "on"}
MERGE_API_KEY = os.getenv("MERGE_API_KEY", "").strip()
MERGE_ACCOUNT_TOKEN = os.getenv("MERGE_ACCOUNT_TOKEN", "").strip()
MERGE_BASE_URL = os.getenv("MERGE_BASE_URL", "https://api.merge.dev/api/ats/v1").strip()
REFRESH_CRON_ENABLED = os.getenv("REFRESH_CRON_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
REFRESH_INTERVAL_MINUTES = int(os.getenv("REFRESH_INTERVAL_MINUTES", "180"))
REFRESH_JOB_SCAN_LIMIT = int(os.getenv("REFRESH_JOB_SCAN_LIMIT", "20"))
REFRESH_CANDIDATE_LIMIT = int(os.getenv("REFRESH_CANDIDATE_LIMIT", "25"))
REFRESH_MIN_WINDOW_MINUTES = int(os.getenv("REFRESH_MIN_WINDOW_MINUTES", "30"))
PDL_MIN_REQUEST_INTERVAL_SECONDS = float(os.getenv("PDL_MIN_REQUEST_INTERVAL_SECONDS", "0.35"))
RLHF_SMOOTHING_ALPHA = float(os.getenv("RLHF_SMOOTHING_ALPHA", "0.20"))
RLHF_FEEDBACK_HALF_LIFE_DAYS = int(os.getenv("RLHF_FEEDBACK_HALF_LIFE_DAYS", "21"))
RLHF_BASE_FEEDBACK_BIAS = float(os.getenv("RLHF_BASE_FEEDBACK_BIAS", "0.15"))
OUTREACH_FOLLOWUP_DAYS = int(os.getenv("OUTREACH_FOLLOWUP_DAYS", "4"))
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "").strip()
# Disabled after Postgres migration: persistent sqlite cache backend is no longer active.
PERSISTENT_CACHE_PATH = os.getenv("PERSISTENT_CACHE_PATH", "disabled").strip()
REDIS_URL = os.getenv("REDIS_URL", "").strip()

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
    return warnings
