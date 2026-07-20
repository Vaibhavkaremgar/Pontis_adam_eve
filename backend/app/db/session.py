from __future__ import annotations

import os
import logging

from sqlalchemy import inspect, text
from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import AUTO_RECREATE_SCHEMA, DATABASE_URL, USE_INTERNAL_CANDIDATE_DB
from app.db.database_url import normalize_database_url

logger = logging.getLogger(__name__)

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required")


def _is_railway_environment() -> bool:
    return any(
        os.getenv(name, "").strip()
        for name in (
            "RAILWAY_ENVIRONMENT",
            "RAILWAY_PROJECT_ID",
            "RAILWAY_SERVICE_ID",
            "RAILWAY_DEPLOYMENT_ID",
        )
    )


database_url = normalize_database_url(DATABASE_URL)
engine_kwargs: dict[str, object] = {"pool_pre_ping": True}
if database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
elif _is_railway_environment():
    engine_kwargs["pool_recycle"] = 300
    engine_kwargs["pool_size"] = 10
    engine_kwargs["max_overflow"] = 20
else:
    # Non-Railway PostgreSQL: still set sensible pool limits
    if not database_url.startswith("sqlite"):
        engine_kwargs["pool_size"] = 10
        engine_kwargs["max_overflow"] = 20
        engine_kwargs["pool_recycle"] = 300

engine = create_engine(database_url, **engine_kwargs)

if database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _configure_sqlite_connection(dbapi_connection, _connection_record):
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()
        except Exception:
            logger.debug("sqlite_pragmas_skipped", exc_info=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    _verify_database_connection()
    _ensure_optional_schema_columns()
    _verify_migrated_schema()


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def db_health_snapshot() -> dict:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "error": ""}
    except Exception as exc:
        return {"status": "down", "error": str(exc)}


def _verify_database_connection() -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def _verify_migrated_schema() -> None:
    with engine.begin() as conn:
        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())
        required_tables = {
            "users",
            "agencies",
            "job_descriptions",
            "job_intakes",
            "orchestration_sessions",
            "orchestration_events",
            "candidates",
            "interviews",
            "outreach_events",
            "notification_workflow_tokens",
            "inbound_email_replies",
            "inbound_email_attachments",
        }
        if USE_INTERNAL_CANDIDATE_DB:
            required_tables.add("internal_candidate_resumes")
        missing_tables = sorted(required_tables - table_names)
        if missing_tables:
            raise RuntimeError(
                "Database schema is not migrated. Missing tables: " + ", ".join(missing_tables)
            )


def _reconcile_legacy_schema_if_needed() -> None:
    """
    Keep local/dev startup resilient when an older Postgres schema is incompatible
    with the current UUID-based model definitions.
    """
    if not database_url.startswith("postgresql"):
        return

    with engine.begin() as conn:
        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())
        if not {"users", "agencies", "job_descriptions", "interviews"}.intersection(table_names):
            return

        schema_incompatible, reason = _has_schema_incompatibility(inspector, table_names)

        if not schema_incompatible:
            return

        # Disabled after Postgres migration: prevent destructive runtime schema changes.
        if AUTO_RECREATE_SCHEMA:
            logger.warning(
                "AUTO_RECREATE_SCHEMA is enabled but destructive runtime reconcile is disabled for safety. "
                "Detected schema issue: %s",
                reason,
            )
        raise RuntimeError(
            "Incompatible database schema detected for core tables. "
            "Automatic destructive schema reconcile is disabled. Run explicit migrations."
        )


def _has_schema_incompatibility(inspector, table_names: set[str]) -> tuple[bool, str]:
    expected_columns = {
        "users": ["id", "email", "created_at"],
        "agencies": ["id", "name", "slug", "is_active", "created_at", "updated_at"],
        "job_descriptions": [
            "id",
            "source_app",
            "job_status",
            "vetting_mode",
            "title",
            "description",
            "location",
            "salary_range",
            "company_name",
            "agency_id",
            "created_by",
            "remote_policy",
            "experience_required",
            "last_candidate_attempt_at",
            "updated_at",
            "created_at",
        ],
        "interviews": ["id", "source_app", "job_id", "agency_id", "candidate_id", "status", "created_at"],
    }

    for table_name, columns in expected_columns.items():
        if table_name not in table_names:
            continue

        observed = {column["name"]: column for column in inspector.get_columns(table_name)}
        missing = [column for column in columns if column not in observed]
        if missing:
            return True, f"{table_name} missing columns: {', '.join(missing)}"

        for uuid_column in ("id", "user_id", "company_id", "job_id"):
            if uuid_column not in observed:
                continue
            sql_type = str(observed[uuid_column].get("type", "")).lower()
            if "uuid" not in sql_type:
                return True, f"{table_name}.{uuid_column} has incompatible type: {sql_type or 'unknown'}"

    return False, ""


def _ensure_optional_schema_columns() -> None:
    """
    Add additive/non-destructive columns needed by newer voice extraction features.
    This keeps older dev databases compatible without requiring immediate manual migrations.
    """
    with engine.begin() as conn:
        dialect = engine.dialect.name
        json_empty_list_default = "'[]'::json" if dialect == "postgresql" else "'[]'"
        json_empty_object_default = "'{}'::json" if dialect == "postgresql" else "'{}'"

        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())
        if "job_descriptions" not in table_names and "agencies" not in table_names:
            return

        if "job_descriptions" in table_names:
            job_columns = {column["name"] for column in inspector.get_columns("job_descriptions")}
            if "source_app" not in job_columns:
                conn.execute(text("ALTER TABLE job_descriptions ADD COLUMN source_app VARCHAR(32) NOT NULL DEFAULT 'ui'"))
            if "job_status" not in job_columns:
                conn.execute(text("ALTER TABLE job_descriptions ADD COLUMN job_status VARCHAR(32) NOT NULL DEFAULT 'active'"))
            if "vetting_mode" not in job_columns:
                conn.execute(text("ALTER TABLE job_descriptions ADD COLUMN vetting_mode VARCHAR(16) NOT NULL DEFAULT 'volume'"))
            if "last_candidate_attempt_at" not in job_columns:
                conn.execute(text("ALTER TABLE job_descriptions ADD COLUMN last_candidate_attempt_at TIMESTAMPTZ NULL DEFAULT NULL"))
            if "responsibilities" not in job_columns:
                conn.execute(
                    text(
                        f"ALTER TABLE job_descriptions ADD COLUMN responsibilities JSON NOT NULL DEFAULT {json_empty_list_default}"
                    )
                )
            if "skills_required" not in job_columns:
                conn.execute(
                    text(
                        f"ALTER TABLE job_descriptions ADD COLUMN skills_required JSON NOT NULL DEFAULT {json_empty_list_default}"
                    )
                )
            if "experience_level" not in job_columns:
                conn.execute(text("ALTER TABLE job_descriptions ADD COLUMN experience_level VARCHAR(255) NOT NULL DEFAULT ''"))
            if "structured_data" not in job_columns:
                conn.execute(
                    text(
                        f"ALTER TABLE job_descriptions ADD COLUMN structured_data JSON NOT NULL DEFAULT {json_empty_object_default}"
                    )
                )
            if "ats_job_id" not in job_columns:
                conn.execute(text("ALTER TABLE job_descriptions ADD COLUMN ats_job_id VARCHAR(128) NULL DEFAULT NULL"))
            if "created_by" not in job_columns:
                conn.execute(text("ALTER TABLE job_descriptions ADD COLUMN created_by VARCHAR(36) NOT NULL DEFAULT ''"))
            if "remote_policy" not in job_columns:
                conn.execute(text("ALTER TABLE job_descriptions ADD COLUMN remote_policy VARCHAR(64) NOT NULL DEFAULT ''"))
            if "experience_required" not in job_columns:
                conn.execute(text("ALTER TABLE job_descriptions ADD COLUMN experience_required VARCHAR(255) NOT NULL DEFAULT ''"))
            if "updated_at" not in job_columns:
                conn.execute(text("ALTER TABLE job_descriptions ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"))

        if "users" in table_names:
            user_columns = {column["name"] for column in inspector.get_columns("users")}
            if "role" not in user_columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(32) NOT NULL DEFAULT 'recruiter'"))

        if "candidate_feedback" in table_names:
            feedback_columns = {column["name"] for column in inspector.get_columns("candidate_feedback")}
            if "recruiter_id" not in feedback_columns:
                conn.execute(text("ALTER TABLE candidate_feedback ADD COLUMN recruiter_id VARCHAR(36) NULL DEFAULT NULL"))
            if "session_id" not in feedback_columns:
                conn.execute(text("ALTER TABLE candidate_feedback ADD COLUMN session_id VARCHAR(36) NULL DEFAULT NULL"))

        if "candidates" in table_names:
            candidate_columns = {column["name"] for column in inspector.get_columns("candidates")}
            if "agency_id" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidates ADD COLUMN agency_id UUID NULL DEFAULT NULL"))
            if "candidate_status" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidates ADD COLUMN candidate_status VARCHAR(64) NOT NULL DEFAULT 'new'"))
            if "resume_received_at" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidates ADD COLUMN resume_received_at TIMESTAMPTZ NULL DEFAULT NULL"))
            if "total_experience_years" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidates ADD COLUMN total_experience_years DOUBLE PRECISION NOT NULL DEFAULT 0"))
            if "current_company" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidates ADD COLUMN current_company VARCHAR(255) NOT NULL DEFAULT ''"))
            if "phone" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidates ADD COLUMN phone VARCHAR(64) NOT NULL DEFAULT ''"))
            if "linkedin_url" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidates ADD COLUMN linkedin_url VARCHAR(500) NOT NULL DEFAULT ''"))
            if "github_url" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidates ADD COLUMN github_url VARCHAR(500) NOT NULL DEFAULT ''"))
            if "parsed_resume_json" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidates ADD COLUMN parsed_resume_json JSON NOT NULL DEFAULT '{}'"))
            if "parsed_resume_text" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidates ADD COLUMN parsed_resume_text TEXT NOT NULL DEFAULT ''"))
            if "workflow_token" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidates ADD COLUMN workflow_token VARCHAR(255) NOT NULL DEFAULT ''"))
            if "ats_status" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidates ADD COLUMN ats_status VARCHAR(64) NOT NULL DEFAULT 'reviewed'"))
            if "ats_status_source" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidates ADD COLUMN ats_status_source VARCHAR(32) NOT NULL DEFAULT 'system'"))
            if "ats_status_reason" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidates ADD COLUMN ats_status_reason TEXT NOT NULL DEFAULT ''"))
            if "ats_status_updated_at" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidates ADD COLUMN ats_status_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"))
            if "ats_metadata" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidates ADD COLUMN ats_metadata JSON NOT NULL DEFAULT '{}'"))

        if "candidate_applications" in table_names:
            application_columns = {column["name"] for column in inspector.get_columns("candidate_applications")}
            if "shortlist_email_sent_at" not in application_columns:
                conn.execute(text("ALTER TABLE candidate_applications ADD COLUMN shortlist_email_sent_at TIMESTAMPTZ NULL DEFAULT NULL"))
            if "shortlist_email_status" not in application_columns:
                conn.execute(text("ALTER TABLE candidate_applications ADD COLUMN shortlist_email_status VARCHAR(32) NOT NULL DEFAULT ''"))

        if "ranking_explanations" in table_names:
            ranking_columns = {column["name"] for column in inspector.get_columns("ranking_explanations")}
            if "existing_score" not in ranking_columns:
                conn.execute(text("ALTER TABLE ranking_explanations ADD COLUMN existing_score DOUBLE PRECISION NOT NULL DEFAULT 0"))
            if "recruiter_score" not in ranking_columns:
                conn.execute(text("ALTER TABLE ranking_explanations ADD COLUMN recruiter_score DOUBLE PRECISION NOT NULL DEFAULT 0"))
            if "session_signal" not in ranking_columns:
                conn.execute(text("ALTER TABLE ranking_explanations ADD COLUMN session_signal DOUBLE PRECISION NOT NULL DEFAULT 0"))
            if "final_score" not in ranking_columns:
                conn.execute(text("ALTER TABLE ranking_explanations ADD COLUMN final_score DOUBLE PRECISION NOT NULL DEFAULT 0"))
            if "recruiter_capped" not in ranking_columns:
                conn.execute(text("ALTER TABLE ranking_explanations ADD COLUMN recruiter_capped BOOLEAN NOT NULL DEFAULT FALSE"))

        if "ranking_runs" in table_names:
            run_columns = {column["name"] for column in inspector.get_columns("ranking_runs")}
            if "recruiter_id" not in run_columns:
                conn.execute(text("ALTER TABLE ranking_runs ADD COLUMN recruiter_id VARCHAR(36) NULL DEFAULT NULL"))
            if "run_type" not in run_columns:
                conn.execute(text("ALTER TABLE ranking_runs ADD COLUMN run_type VARCHAR(32) NOT NULL DEFAULT 'initial'"))
            if "avg_existing_score" not in run_columns:
                conn.execute(text("ALTER TABLE ranking_runs ADD COLUMN avg_existing_score DOUBLE PRECISION NOT NULL DEFAULT 0"))

        if "outreach_events" in table_names:
            outreach_columns = {column["name"] for column in inspector.get_columns("outreach_events")}
            if "source_app" not in outreach_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN source_app VARCHAR(32) NOT NULL DEFAULT 'ui'"))
            if "agency_id" not in outreach_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN agency_id VARCHAR(36) NOT NULL DEFAULT ''"))
            if "reply_intent" not in outreach_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN reply_intent VARCHAR(64) NOT NULL DEFAULT ''"))
            if "reply_state" not in outreach_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN reply_state VARCHAR(64) NOT NULL DEFAULT ''"))
            if "archive_reason" not in outreach_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN archive_reason VARCHAR(255) NOT NULL DEFAULT ''"))
            if "sent_at" not in outreach_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN sent_at TIMESTAMPTZ NULL DEFAULT NULL"))

        if "interviews" in table_names:
            interview_columns = {column["name"] for column in inspector.get_columns("interviews")}
            if "source_app" not in interview_columns:
                conn.execute(text("ALTER TABLE interviews ADD COLUMN source_app VARCHAR(32) NOT NULL DEFAULT 'ui'"))

        if "notification_workflow_tokens" not in table_names:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE notification_workflow_tokens (
                        id VARCHAR(36) PRIMARY KEY,
                        source_app VARCHAR(32) NOT NULL DEFAULT 'ui',
                        job_id VARCHAR(36) NOT NULL,
                        candidate_id VARCHAR(128) NOT NULL,
                        token_type VARCHAR(64) NOT NULL DEFAULT '',
                        workflow_name VARCHAR(64) NOT NULL DEFAULT '',
                        token VARCHAR(255) NOT NULL UNIQUE,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        status VARCHAR(32) NOT NULL DEFAULT 'active',
                        payload JSON NOT NULL DEFAULT {json_empty_object_default},
                        expires_at TIMESTAMPTZ NULL DEFAULT NULL,
                        used_at TIMESTAMPTZ NULL DEFAULT NULL,
                        consumed_at TIMESTAMPTZ NULL DEFAULT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
        else:
            token_columns = {column["name"] for column in inspector.get_columns("notification_workflow_tokens")}
            if "token_type" not in token_columns:
                conn.execute(text("ALTER TABLE notification_workflow_tokens ADD COLUMN token_type VARCHAR(64) NOT NULL DEFAULT ''"))
            if "workflow_name" not in token_columns:
                conn.execute(text("ALTER TABLE notification_workflow_tokens ADD COLUMN workflow_name VARCHAR(64) NOT NULL DEFAULT ''"))
            if "is_active" not in token_columns:
                conn.execute(text("ALTER TABLE notification_workflow_tokens ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE"))
            if "used_at" not in token_columns:
                conn.execute(text("ALTER TABLE notification_workflow_tokens ADD COLUMN used_at TIMESTAMPTZ NULL DEFAULT NULL"))

        if "inbound_email_replies" in table_names:
            inbound_columns = {column["name"] for column in inspector.get_columns("inbound_email_replies")}
            if "agency_id" not in inbound_columns:
                conn.execute(text("ALTER TABLE inbound_email_replies ADD COLUMN agency_id VARCHAR(36) NULL DEFAULT NULL"))
            if "intent" not in inbound_columns:
                conn.execute(text("ALTER TABLE inbound_email_replies ADD COLUMN intent VARCHAR(64) NOT NULL DEFAULT ''"))

        if "ranking_runs" in table_names:
            run_columns = {column["name"] for column in inspector.get_columns("ranking_runs")}
            if "avg_final_score" not in run_columns:
                conn.execute(text("ALTER TABLE ranking_runs ADD COLUMN avg_final_score DOUBLE PRECISION NOT NULL DEFAULT 0"))
            if "avg_recruiter_score" not in run_columns:
                conn.execute(text("ALTER TABLE ranking_runs ADD COLUMN avg_recruiter_score DOUBLE PRECISION NOT NULL DEFAULT 0"))
            if "percent_recruiter_capped" not in run_columns:
                conn.execute(text("ALTER TABLE ranking_runs ADD COLUMN percent_recruiter_capped DOUBLE PRECISION NOT NULL DEFAULT 0"))
            if "candidate_count" not in run_columns:
                conn.execute(text("ALTER TABLE ranking_runs ADD COLUMN candidate_count INTEGER NOT NULL DEFAULT 0"))
            if "drift_delta" not in run_columns:
                conn.execute(text("ALTER TABLE ranking_runs ADD COLUMN drift_delta DOUBLE PRECISION NOT NULL DEFAULT 0"))

        if "recruiter_experience_preferences" in table_names:
            exp_columns = {column["name"] for column in inspector.get_columns("recruiter_experience_preferences")}
            if "experience_bucket" not in exp_columns:
                conn.execute(text("ALTER TABLE recruiter_experience_preferences ADD COLUMN experience_bucket VARCHAR(16) NOT NULL DEFAULT ''"))
            if "weight" not in exp_columns:
                conn.execute(text("ALTER TABLE recruiter_experience_preferences ADD COLUMN weight DOUBLE PRECISION NOT NULL DEFAULT 0"))
            if "updated_at" not in exp_columns:
                conn.execute(text("ALTER TABLE recruiter_experience_preferences ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"))

        if "companies" in table_names:
            company_columns = {column["name"] for column in inspector.get_columns("companies")}
            if "industry" not in company_columns:
                conn.execute(text("ALTER TABLE companies ADD COLUMN industry VARCHAR(255) NOT NULL DEFAULT ''"))
            if "ats_provider" not in company_columns:
                conn.execute(text("ALTER TABLE companies ADD COLUMN ats_provider VARCHAR(64) NOT NULL DEFAULT ''"))
            if "ats_connected" not in company_columns:
                conn.execute(text("ALTER TABLE companies ADD COLUMN ats_connected BOOLEAN NOT NULL DEFAULT FALSE"))
            if dialect == "postgresql":
                conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_user_name ON companies (user_id, name)"))

        if "outreach_events" in table_names:
            oe_columns = {column["name"] for column in inspector.get_columns("outreach_events")}
            if "follow_up_count" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN follow_up_count INTEGER NOT NULL DEFAULT 0"))
            if "open_count" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN open_count INTEGER NOT NULL DEFAULT 0"))
            if "reply_count" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN reply_count INTEGER NOT NULL DEFAULT 0"))
            if "provider_message_id" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN provider_message_id VARCHAR(255) NULL DEFAULT NULL"))
            else:
                conn.execute(text("UPDATE outreach_events SET provider_message_id = NULL WHERE provider_message_id = ''"))
                conn.execute(text("ALTER TABLE outreach_events ALTER COLUMN provider_message_id DROP NOT NULL"))
                conn.execute(text("ALTER TABLE outreach_events ALTER COLUMN provider_message_id DROP DEFAULT"))
            if "last_contacted_at" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN last_contacted_at TIMESTAMPTZ NULL DEFAULT NULL"))
            if "last_opened_at" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN last_opened_at TIMESTAMPTZ NULL DEFAULT NULL"))
            if "last_replied_at" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN last_replied_at TIMESTAMPTZ NULL DEFAULT NULL"))
            if "next_follow_up_at" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN next_follow_up_at TIMESTAMPTZ NULL DEFAULT NULL"))
            if "message_text" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN message_text TEXT NOT NULL DEFAULT ''"))
            if "resume_url" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN resume_url VARCHAR(500) NOT NULL DEFAULT ''"))
            if "responded_at" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN responded_at TIMESTAMPTZ NULL DEFAULT NULL"))
            if "engagement_score" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN engagement_score DOUBLE PRECISION NOT NULL DEFAULT 0"))
            if "reply_likelihood_score" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN reply_likelihood_score DOUBLE PRECISION NOT NULL DEFAULT 0"))
            if "responsiveness_score" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN responsiveness_score DOUBLE PRECISION NOT NULL DEFAULT 0"))
            if "learning_applied" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN learning_applied BOOLEAN NOT NULL DEFAULT FALSE"))
            if dialect == "postgresql":
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS unique_provider_message_id_idx "
                        "ON outreach_events (provider_message_id) "
                        "WHERE provider_message_id IS NOT NULL"
                    )
                )

        if "interview_sessions" in table_names:
            interview_session_columns = {column["name"] for column in inspector.get_columns("interview_sessions")}
            if "agency_id" not in interview_session_columns:
                conn.execute(text("ALTER TABLE interview_sessions ADD COLUMN agency_id VARCHAR(36) NOT NULL DEFAULT ''"))
            if "outreach_event_id" not in interview_session_columns:
                conn.execute(text("ALTER TABLE interview_sessions ADD COLUMN outreach_event_id VARCHAR(36) NULL DEFAULT NULL"))
            if "booked_at" not in interview_session_columns:
                booked_column_type = "TIMESTAMPTZ" if dialect == "postgresql" else "DATETIME"
                conn.execute(text(f"ALTER TABLE interview_sessions ADD COLUMN booked_at {booked_column_type} NULL DEFAULT NULL"))
            if "stage" not in interview_session_columns:
                conn.execute(text("ALTER TABLE interview_sessions ADD COLUMN stage VARCHAR(64) NOT NULL DEFAULT 'requested'"))
            if "booking_status" not in interview_session_columns:
                conn.execute(text("ALTER TABLE interview_sessions ADD COLUMN booking_status VARCHAR(32) NOT NULL DEFAULT 'pending'"))
            if "scheduled_at" not in interview_session_columns:
                scheduled_column_type = "TIMESTAMPTZ" if dialect == "postgresql" else "DATETIME"
                conn.execute(text(f"ALTER TABLE interview_sessions ADD COLUMN scheduled_at {scheduled_column_type} NULL DEFAULT NULL"))
            if "available_slots" not in interview_session_columns:
                conn.execute(text(f"ALTER TABLE interview_sessions ADD COLUMN available_slots JSON NOT NULL DEFAULT {json_empty_list_default}"))
            if "timezone" not in interview_session_columns:
                conn.execute(text("ALTER TABLE interview_sessions ADD COLUMN timezone VARCHAR(64) NOT NULL DEFAULT 'UTC'"))
            if "interviewer_metadata" not in interview_session_columns:
                conn.execute(text(f"ALTER TABLE interview_sessions ADD COLUMN interviewer_metadata JSON NOT NULL DEFAULT {json_empty_object_default}"))
            if "scheduling_metadata" not in interview_session_columns:
                conn.execute(text(f"ALTER TABLE interview_sessions ADD COLUMN scheduling_metadata JSON NOT NULL DEFAULT {json_empty_object_default}"))
            if "evaluation_status" not in interview_session_columns:
                conn.execute(text("ALTER TABLE interview_sessions ADD COLUMN evaluation_status VARCHAR(32) NOT NULL DEFAULT 'pending'"))
            if "evaluation_ready_at" not in interview_session_columns:
                conn.execute(text("ALTER TABLE interview_sessions ADD COLUMN evaluation_ready_at TIMESTAMPTZ NULL DEFAULT NULL"))
            elif dialect == "postgresql":
                result = conn.execute(text("""
                    SELECT data_type
                    FROM information_schema.columns
                    WHERE table_name = 'interview_sessions'
                    AND column_name = 'scheduled_at'
                """)).scalar()
                if result != "timestamp with time zone":
                    conn.execute(
                        text(
                            "ALTER TABLE interview_sessions "
                            "ALTER COLUMN scheduled_at TYPE TIMESTAMPTZ "
                            "USING ("
                            "    CASE "
                            "        WHEN scheduled_at IS NULL THEN NULL "
                            "        WHEN TRIM(scheduled_at) = '' THEN NULL "
                            "        ELSE scheduled_at::timestamptz "
                            "    END"
                            ")"
                        )
                    )
            if "booked_at" in interview_session_columns and dialect == "postgresql":
                result = conn.execute(text("""
                    SELECT data_type
                    FROM information_schema.columns
                    WHERE table_name = 'interview_sessions'
                    AND column_name = 'booked_at'
                """)).scalar()
                if result == "timestamp without time zone":
                    conn.execute(
                        text(
                            "ALTER TABLE interview_sessions "
                            "ALTER COLUMN booked_at TYPE TIMESTAMPTZ "
                            "USING booked_at AT TIME ZONE 'UTC'"
                        )
                    )
                elif result and result != "timestamp with time zone":
                    conn.execute(
                        text(
                            "ALTER TABLE interview_sessions "
                            "ALTER COLUMN booked_at TYPE TIMESTAMPTZ "
                            "USING ("
                            "    CASE "
                            "        WHEN booked_at IS NULL THEN NULL "
                            "        WHEN TRIM(booked_at) = '' THEN NULL "
                            "        ELSE booked_at::timestamptz "
                            "    END"
                            ")"
                        )
                    )
            if "booking_url" not in interview_session_columns:
                conn.execute(text("ALTER TABLE interview_sessions ADD COLUMN booking_url VARCHAR(1024) NOT NULL DEFAULT ''"))

        if "job_intakes" in table_names:
            job_intake_columns = {column["name"] for column in inspector.get_columns("job_intakes")}
            if "agency_id" not in job_intake_columns:
                conn.execute(text("ALTER TABLE job_intakes ADD COLUMN agency_id VARCHAR(36) NOT NULL DEFAULT ''"))
            if "transcript" not in job_intake_columns:
                conn.execute(text("ALTER TABLE job_intakes ADD COLUMN transcript TEXT NOT NULL DEFAULT ''"))
            if "structured_data_json" not in job_intake_columns:
                conn.execute(text("ALTER TABLE job_intakes ADD COLUMN structured_data_json JSON NOT NULL DEFAULT '{}'"))
            if "intake_status" not in job_intake_columns:
                conn.execute(text("ALTER TABLE job_intakes ADD COLUMN intake_status VARCHAR(32) NOT NULL DEFAULT 'pending'"))
            if "completed_at" not in job_intake_columns:
                conn.execute(text("ALTER TABLE job_intakes ADD COLUMN completed_at TIMESTAMPTZ NULL DEFAULT NULL"))
            if "updated_at" not in job_intake_columns:
                conn.execute(text("ALTER TABLE job_intakes ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"))

        if "orchestration_sessions" not in table_names:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE orchestration_sessions (
                        id VARCHAR(36) PRIMARY KEY,
                        session_token VARCHAR(255) NOT NULL UNIQUE,
                        source VARCHAR(32) NOT NULL DEFAULT 'slack',
                        current_stage VARCHAR(32) NOT NULL DEFAULT 'initiated',
                        slack_team_id VARCHAR(64) NOT NULL DEFAULT '',
                        slack_channel_id VARCHAR(64) NOT NULL DEFAULT '',
                        slack_thread_ts VARCHAR(64) NOT NULL DEFAULT '',
                        slack_user_id VARCHAR(64) NOT NULL DEFAULT '',
                        intake_mode VARCHAR(32) NOT NULL DEFAULT 'slack',
                        selected_path VARCHAR(32) NOT NULL DEFAULT '',
                        current_question TEXT NOT NULL DEFAULT '',
                        current_question_key VARCHAR(128) NOT NULL DEFAULT '',
                        structured_context JSON NOT NULL DEFAULT {json_empty_object_default},
                        raw_conversation JSON NOT NULL DEFAULT {json_empty_list_default},
                        normalized_intake JSON NOT NULL DEFAULT {json_empty_object_default},
                        voice_context JSON NOT NULL DEFAULT {json_empty_object_default},
                        slack_context JSON NOT NULL DEFAULT {json_empty_object_default},
                        voice_handoff_token VARCHAR(255) NOT NULL DEFAULT '',
                        voice_handoff_expires_at TIMESTAMPTZ NULL DEFAULT NULL,
                        voice_handoff_consumed_at TIMESTAMPTZ NULL DEFAULT NULL,
                        voice_token_used BOOLEAN NOT NULL DEFAULT FALSE,
                        expires_at TIMESTAMPTZ NULL DEFAULT NULL,
                        completed_at TIMESTAMPTZ NULL DEFAULT NULL,
                        agency_id VARCHAR(36) NULL DEFAULT NULL,
                        job_id VARCHAR(36) NULL DEFAULT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
        else:
            orchestration_columns = {column["name"] for column in inspector.get_columns("orchestration_sessions")}
            if "session_token" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN session_token VARCHAR(255) NOT NULL DEFAULT ''"))
            if "source" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN source VARCHAR(32) NOT NULL DEFAULT 'slack'"))
            if "current_stage" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN current_stage VARCHAR(32) NOT NULL DEFAULT 'initiated'"))
            if "slack_team_id" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN slack_team_id VARCHAR(64) NOT NULL DEFAULT ''"))
            if "slack_channel_id" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN slack_channel_id VARCHAR(64) NOT NULL DEFAULT ''"))
            if "slack_thread_ts" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN slack_thread_ts VARCHAR(64) NOT NULL DEFAULT ''"))
            if "slack_user_id" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN slack_user_id VARCHAR(64) NOT NULL DEFAULT ''"))
            if "intake_mode" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN intake_mode VARCHAR(32) NOT NULL DEFAULT 'slack'"))
            if "selected_path" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN selected_path VARCHAR(32) NOT NULL DEFAULT ''"))
            if "current_question" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN current_question TEXT NOT NULL DEFAULT ''"))
            if "current_question_key" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN current_question_key VARCHAR(128) NOT NULL DEFAULT ''"))
            if "current_question_type" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN current_question_type VARCHAR(64) NOT NULL DEFAULT ''"))
            if "current_question_schema" not in orchestration_columns:
                conn.execute(text(f"ALTER TABLE orchestration_sessions ADD COLUMN current_question_schema JSON NOT NULL DEFAULT {json_empty_object_default}"))
            if "structured_context" not in orchestration_columns:
                conn.execute(text(f"ALTER TABLE orchestration_sessions ADD COLUMN structured_context JSON NOT NULL DEFAULT {json_empty_object_default}"))
            if "raw_conversation" not in orchestration_columns:
                conn.execute(text(f"ALTER TABLE orchestration_sessions ADD COLUMN raw_conversation JSON NOT NULL DEFAULT {json_empty_list_default}"))
            if "normalized_intake" not in orchestration_columns:
                conn.execute(text(f"ALTER TABLE orchestration_sessions ADD COLUMN normalized_intake JSON NOT NULL DEFAULT {json_empty_object_default}"))
            if "voice_context" not in orchestration_columns:
                conn.execute(text(f"ALTER TABLE orchestration_sessions ADD COLUMN voice_context JSON NOT NULL DEFAULT {json_empty_object_default}"))
            if "slack_context" not in orchestration_columns:
                conn.execute(text(f"ALTER TABLE orchestration_sessions ADD COLUMN slack_context JSON NOT NULL DEFAULT {json_empty_object_default}"))
            if "voice_handoff_token" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN voice_handoff_token VARCHAR(255) NOT NULL DEFAULT ''"))
            if "voice_handoff_expires_at" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN voice_handoff_expires_at TIMESTAMPTZ NULL DEFAULT NULL"))
            if "voice_handoff_consumed_at" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN voice_handoff_consumed_at TIMESTAMPTZ NULL DEFAULT NULL"))
            if "voice_token_used" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN voice_token_used BOOLEAN NOT NULL DEFAULT FALSE"))
            if "expires_at" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN expires_at TIMESTAMPTZ NULL DEFAULT NULL"))
            if "completed_at" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN completed_at TIMESTAMPTZ NULL DEFAULT NULL"))
            if "state_version" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN state_version INTEGER NOT NULL DEFAULT 0"))
            if "last_processed_message_ts" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN last_processed_message_ts VARCHAR(64) NOT NULL DEFAULT ''"))
            if "last_processed_action_hash" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN last_processed_action_hash VARCHAR(64) NOT NULL DEFAULT ''"))
            if "last_processed_transcript_hash" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN last_processed_transcript_hash VARCHAR(64) NOT NULL DEFAULT ''"))
            if "intake_version" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN intake_version VARCHAR(32) NOT NULL DEFAULT 'v1'"))
            if "agency_id" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN agency_id VARCHAR(36) NULL DEFAULT NULL"))
            if "job_id" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN job_id VARCHAR(36) NULL DEFAULT NULL"))
            if dialect == "postgresql":
                for column_name in ("agency_id", "company_id", "job_id"):
                    column = next((col for col in inspector.get_columns("orchestration_sessions") if col["name"] == column_name), None)
                    if column and not column.get("nullable", True):
                        conn.execute(text(f"ALTER TABLE orchestration_sessions ALTER COLUMN {column_name} DROP NOT NULL"))
            if "updated_at" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"))

        if "orchestration_events" not in table_names:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE orchestration_events (
                        id VARCHAR(36) PRIMARY KEY,
                        session_id VARCHAR(36) NOT NULL,
                        event_type VARCHAR(64) NOT NULL DEFAULT '',
                        event_payload JSON NOT NULL DEFAULT {json_empty_object_default},
                        source VARCHAR(32) NOT NULL DEFAULT 'slack',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
        else:
            orchestration_event_columns = {column["name"] for column in inspector.get_columns("orchestration_events")}
            if dialect == "postgresql" and "orchestration_session_id" in orchestration_event_columns:
                if "session_id" not in orchestration_event_columns:
                    conn.execute(text("ALTER TABLE orchestration_events RENAME COLUMN orchestration_session_id TO session_id"))
                    orchestration_event_columns = {column["name"] for column in inspector.get_columns("orchestration_events")}
                else:
                    conn.execute(
                        text(
                            """
                            UPDATE orchestration_events
                            SET session_id = COALESCE(NULLIF(session_id, ''), orchestration_session_id)
                            WHERE session_id IS NULL OR session_id = ''
                            """
                        )
                    )
                    conn.execute(text("ALTER TABLE orchestration_events ALTER COLUMN orchestration_session_id DROP NOT NULL"))
            if dialect == "postgresql" and "agency_id" in orchestration_event_columns:
                agency_column = next((col for col in inspector.get_columns("orchestration_events") if col["name"] == "agency_id"), None)
                if agency_column and not agency_column.get("nullable", True):
                    conn.execute(text("ALTER TABLE orchestration_events ALTER COLUMN agency_id DROP NOT NULL"))
            if "session_id" not in orchestration_event_columns:
                conn.execute(text("ALTER TABLE orchestration_events ADD COLUMN session_id VARCHAR(36) NOT NULL DEFAULT ''"))
            if "event_type" not in orchestration_event_columns:
                conn.execute(text("ALTER TABLE orchestration_events ADD COLUMN event_type VARCHAR(64) NOT NULL DEFAULT ''"))
            if "event_payload" not in orchestration_event_columns:
                conn.execute(text(f"ALTER TABLE orchestration_events ADD COLUMN event_payload JSON NOT NULL DEFAULT {json_empty_object_default}"))
            if "source" not in orchestration_event_columns:
                conn.execute(text("ALTER TABLE orchestration_events ADD COLUMN source VARCHAR(32) NOT NULL DEFAULT 'slack'"))
            if "created_at" not in orchestration_event_columns:
                conn.execute(text("ALTER TABLE orchestration_events ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"))

        if "candidate_lifecycle_events" not in table_names:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE candidate_lifecycle_events (
                        id VARCHAR(36) PRIMARY KEY,
                        job_id VARCHAR(36) NOT NULL,
                        company_id VARCHAR(36) NOT NULL,
                        candidate_id VARCHAR(128) NOT NULL,
                        from_status VARCHAR(64) NOT NULL DEFAULT '',
                        to_status VARCHAR(64) NOT NULL DEFAULT '',
                        source VARCHAR(32) NOT NULL DEFAULT 'system',
                        actor_id VARCHAR(36) NULL DEFAULT NULL,
                        transition_key VARCHAR(255) NOT NULL DEFAULT '',
                        event_metadata JSON NOT NULL DEFAULT {json_empty_object_default},
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        CONSTRAINT uq_candidate_lifecycle_events_transition UNIQUE (job_id, candidate_id, transition_key)
                    )
                    """
                )
            )
        else:
            lifecycle_columns = {column["name"] for column in inspector.get_columns("candidate_lifecycle_events")}
            if "transition_key" not in lifecycle_columns:
                conn.execute(text("ALTER TABLE candidate_lifecycle_events ADD COLUMN transition_key VARCHAR(255) NOT NULL DEFAULT ''"))
            if "event_metadata" not in lifecycle_columns:
                conn.execute(text(f"ALTER TABLE candidate_lifecycle_events ADD COLUMN event_metadata JSON NOT NULL DEFAULT {json_empty_object_default}"))

        if "notification_events" not in table_names:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE notification_events (
                        id VARCHAR(36) PRIMARY KEY,
                        job_id VARCHAR(36) NULL DEFAULT NULL,
                        company_id VARCHAR(36) NULL DEFAULT NULL,
                        candidate_id VARCHAR(128) NULL DEFAULT NULL,
                        actor_id VARCHAR(36) NULL DEFAULT NULL,
                        recipient_type VARCHAR(32) NOT NULL DEFAULT 'recruiter',
                        recipient VARCHAR(255) NOT NULL DEFAULT '',
                        channel VARCHAR(32) NOT NULL DEFAULT 'slack',
                        title VARCHAR(255) NOT NULL DEFAULT '',
                        body TEXT NOT NULL DEFAULT '',
                        status VARCHAR(32) NOT NULL DEFAULT 'queued',
                        notification_type VARCHAR(64) NOT NULL DEFAULT '',
                        notification_key VARCHAR(255) NOT NULL DEFAULT '',
                        delivery_reference VARCHAR(255) NOT NULL DEFAULT '',
                        notification_metadata JSON NOT NULL DEFAULT {json_empty_object_default},
                        delivered_at TIMESTAMPTZ NULL DEFAULT NULL,
                        failed_at TIMESTAMPTZ NULL DEFAULT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        CONSTRAINT uq_notification_events_notification_key UNIQUE (notification_key)
                    )
                    """
                )
            )
        else:
            notification_columns = {column["name"] for column in inspector.get_columns("notification_events")}
            if "notification_key" not in notification_columns:
                conn.execute(text("ALTER TABLE notification_events ADD COLUMN notification_key VARCHAR(255) NOT NULL DEFAULT ''"))
            if "notification_metadata" not in notification_columns:
                conn.execute(text(f"ALTER TABLE notification_events ADD COLUMN notification_metadata JSON NOT NULL DEFAULT {json_empty_object_default}"))
            if "delivery_reference" not in notification_columns:
                conn.execute(text("ALTER TABLE notification_events ADD COLUMN delivery_reference VARCHAR(255) NOT NULL DEFAULT ''"))
            if "read_at" not in notification_columns:
                conn.execute(text("ALTER TABLE notification_events ADD COLUMN read_at TIMESTAMPTZ NULL DEFAULT NULL"))
            if "is_read" not in notification_columns:
                conn.execute(text("ALTER TABLE notification_events ADD COLUMN is_read BOOLEAN NOT NULL DEFAULT FALSE"))

        if "automation_jobs" not in table_names:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE automation_jobs (
                        id VARCHAR(36) PRIMARY KEY,
                        job_id VARCHAR(36) NULL DEFAULT NULL,
                        candidate_id VARCHAR(128) NULL DEFAULT NULL,
                        automation_type VARCHAR(64) NOT NULL DEFAULT '',
                        automation_key VARCHAR(255) NOT NULL DEFAULT '',
                        status VARCHAR(32) NOT NULL DEFAULT 'queued',
                        scheduled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        started_at TIMESTAMPTZ NULL DEFAULT NULL,
                        completed_at TIMESTAMPTZ NULL DEFAULT NULL,
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        max_attempts INTEGER NOT NULL DEFAULT 3,
                        last_error TEXT NOT NULL DEFAULT '',
                        automation_payload JSON NOT NULL DEFAULT {json_empty_object_default},
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        CONSTRAINT uq_automation_jobs_automation_key UNIQUE (automation_key)
                    )
                    """
                )
            )

        if "recruiter_notes" not in table_names:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE recruiter_notes (
                        id VARCHAR(36) PRIMARY KEY,
                        job_id VARCHAR(36) NOT NULL,
                        candidate_id VARCHAR(128) NULL DEFAULT NULL,
                        recruiter_id VARCHAR(36) NULL DEFAULT NULL,
                        note_type VARCHAR(32) NOT NULL DEFAULT 'note',
                        body TEXT NOT NULL DEFAULT '',
                        status VARCHAR(32) NOT NULL DEFAULT 'active',
                        metadata JSON NOT NULL DEFAULT {json_empty_object_default},
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )

        if "recruiter_tasks" not in table_names:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE recruiter_tasks (
                        id VARCHAR(36) PRIMARY KEY,
                        job_id VARCHAR(36) NOT NULL,
                        candidate_id VARCHAR(128) NULL DEFAULT NULL,
                        recruiter_id VARCHAR(36) NULL DEFAULT NULL,
                        title VARCHAR(255) NOT NULL DEFAULT '',
                        body TEXT NOT NULL DEFAULT '',
                        status VARCHAR(32) NOT NULL DEFAULT 'open',
                        priority VARCHAR(16) NOT NULL DEFAULT 'normal',
                        due_at TIMESTAMPTZ NULL DEFAULT NULL,
                        completed_at TIMESTAMPTZ NULL DEFAULT NULL,
                        metadata JSON NOT NULL DEFAULT {json_empty_object_default},
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )

        if "interview_evaluations" not in table_names:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE interview_evaluations (
                        id VARCHAR(36) PRIMARY KEY,
                        job_id VARCHAR(36) NOT NULL,
                        candidate_id VARCHAR(128) NOT NULL,
                        interviewer_id VARCHAR(36) NULL DEFAULT NULL,
                        stage_name VARCHAR(64) NOT NULL DEFAULT 'screen',
                        status VARCHAR(32) NOT NULL DEFAULT 'draft',
                        summary TEXT NOT NULL DEFAULT '',
                        recommendation VARCHAR(32) NOT NULL DEFAULT '',
                        competency_scores JSON NOT NULL DEFAULT {json_empty_object_default},
                        notes TEXT NOT NULL DEFAULT '',
                        metadata JSON NOT NULL DEFAULT {json_empty_object_default},
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        CONSTRAINT uq_interview_evaluations_job_candidate_stage UNIQUE (job_id, candidate_id, stage_name)
                    )
                    """
                )
            )

        if "ats_exports" in table_names:
            ats_columns = {column["name"] for column in inspector.get_columns("ats_exports")}
            if "candidate_id" not in ats_columns:
                conn.execute(text("ALTER TABLE ats_exports ADD COLUMN candidate_id VARCHAR(128) NULL DEFAULT NULL"))
            if "error" not in ats_columns:
                conn.execute(text("ALTER TABLE ats_exports ADD COLUMN error TEXT NOT NULL DEFAULT ''"))
            if "provider" not in ats_columns:
                conn.execute(text("ALTER TABLE ats_exports ADD COLUMN provider VARCHAR(64) NOT NULL DEFAULT 'mock'"))
            if "status" not in ats_columns:
                conn.execute(text("ALTER TABLE ats_exports ADD COLUMN status VARCHAR(64) NOT NULL DEFAULT 'queued'"))
            if dialect == "postgresql":
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_ats_exports_job_candidate_provider "
                        "ON ats_exports (job_id, candidate_id, provider) "
                        "WHERE candidate_id IS NOT NULL"
                    )
                )

        if dialect == "postgresql":
            timestamptz_columns: dict[str, tuple[str, ...]] = {
                "users": ("created_at",),
                "agencies": ("created_at",),
                "job_descriptions": ("created_at", "last_candidate_attempt_at"),
                "job_intakes": ("created_at", "completed_at", "updated_at"),
                "interviews": ("created_at",),
                "candidates": ("last_scored_at", "last_refreshed_at", "ats_status_updated_at"),
                "scoring_profiles": ("updated_at",),
                "candidate_feedback": ("updated_at", "created_at"),
                "ats_exports": ("exported_at",),
                "outreach_events": (
                    "sent_at",
                    "last_sent_at",
                    "last_contacted_at",
                    "last_opened_at",
                    "last_replied_at",
                    "next_follow_up_at",
                    "responded_at",
                    "updated_at",
                    "created_at",
                ),
                "otps": ("expires_at", "created_at"),
                "ats_export_retries": ("next_retry_at", "created_at", "updated_at"),
                "notification_events": ("delivered_at", "failed_at", "created_at", "updated_at"),
                "candidate_lifecycle_events": ("created_at",),
                "interview_sessions": ("scheduled_at", "booked_at", "created_at", "evaluation_ready_at"),
                "automation_jobs": ("scheduled_at", "started_at", "completed_at", "created_at", "updated_at"),
                "recruiter_notes": ("created_at", "updated_at"),
                "recruiter_tasks": ("due_at", "completed_at", "created_at", "updated_at"),
                "interview_evaluations": ("created_at", "updated_at"),
            }
            for table_name, columns in timestamptz_columns.items():
                if table_name not in table_names:
                    continue
                observed = {column["name"]: str(column.get("type", "")).lower() for column in inspector.get_columns(table_name)}
                for column_name in columns:
                    sql_type = observed.get(column_name, "")
                    if not sql_type or "with time zone" in sql_type:
                        continue
                    conn.execute(
                        text(
                            f"ALTER TABLE {table_name} "
                            f"ALTER COLUMN {column_name} TYPE TIMESTAMPTZ "
                            f"USING {column_name} AT TIME ZONE 'UTC'"
                        )
                    )


def _cleanup_invalid_candidate_references() -> None:
    """
    One-time defensive cleanup:
    remove orphan references that violate (job_id, candidate_id) -> candidates.
    """
    with engine.begin() as conn:
        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())
        if "candidates" not in table_names:
            return

        if "interviews" in table_names:
            orphan_interviews = conn.execute(
                text(
                    """
                    SELECT i.id, i.job_id, i.candidate_id
                    FROM interviews i
                    LEFT JOIN candidates cp
                      ON i.job_id = cp.job_id
                     AND i.candidate_id = cp.candidate_id
                    WHERE cp.id IS NULL
                    """
                )
            ).fetchall()
            if orphan_interviews:
                logger.warning(
                    "invalid_candidate_reference_detected table=interviews orphan_count=%s",
                    len(orphan_interviews),
                )
                conn.execute(
                    text(
                        """
                        DELETE FROM interviews
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM candidates cp
                            WHERE cp.job_id = interviews.job_id
                              AND cp.candidate_id = interviews.candidate_id
                        )
                        """
                    )
                )

        if "outreach_events" in table_names:
            orphan_outreach = conn.execute(
                text(
                    """
                    SELECT o.id, o.job_id, o.candidate_id
                    FROM outreach_events o
                    LEFT JOIN candidates cp
                      ON o.job_id = cp.job_id
                     AND o.candidate_id = cp.candidate_id
                    WHERE cp.id IS NULL
                    """
                )
            ).fetchall()
            if orphan_outreach:
                logger.warning(
                    "invalid_candidate_reference_detected table=outreach_events orphan_count=%s",
                    len(orphan_outreach),
                )
                conn.execute(
                    text(
                        """
                        DELETE FROM outreach_events
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM candidates cp
                            WHERE cp.job_id = outreach_events.job_id
                              AND cp.candidate_id = outreach_events.candidate_id
                        )
                        """
                    )
                )
