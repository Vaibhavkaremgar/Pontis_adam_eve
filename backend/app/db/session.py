from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import AUTO_RECREATE_SCHEMA, DATABASE_URL, USE_INTERNAL_CANDIDATE_DB
logger = logging.getLogger(__name__)

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required")

engine_kwargs: dict[str, object] = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}

engine = create_engine(DATABASE_URL, **engine_kwargs)

if DATABASE_URL.startswith("sqlite"):
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
            "companies",
            "jobs",
            "job_intakes",
            "orchestration_sessions",
            "orchestration_events",
            "candidate_profiles",
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
    if not DATABASE_URL.startswith("postgresql"):
        return

    with engine.begin() as conn:
        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())
        if not {"users", "companies", "jobs", "interviews"}.intersection(table_names):
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
        "companies": ["id", "name", "website", "description", "user_id", "created_at"],
        "jobs": [
            "id",
            "source_app",
            "job_status",
            "vetting_mode",
            "title",
            "description",
            "location",
            "compensation",
            "work_authorization",
            "ats_job_id",
            "company_id",
            "created_by",
            "remote_policy",
            "experience_required",
            "last_candidate_attempt_at",
            "updated_at",
            "created_at",
        ],
        "interviews": ["id", "source_app", "job_id", "company_id", "candidate_id", "status", "created_at"],
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
        if "jobs" not in table_names and "companies" not in table_names:
            return

        if "jobs" in table_names:
            job_columns = {column["name"] for column in inspector.get_columns("jobs")}
            if "source_app" not in job_columns:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN source_app VARCHAR(32) NOT NULL DEFAULT 'dashboard'"))
            if "job_status" not in job_columns:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN job_status VARCHAR(32) NOT NULL DEFAULT 'active'"))
            if "vetting_mode" not in job_columns:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN vetting_mode VARCHAR(16) NOT NULL DEFAULT 'volume'"))
            if "last_candidate_attempt_at" not in job_columns:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN last_candidate_attempt_at TIMESTAMPTZ NULL DEFAULT NULL"))
            if "responsibilities" not in job_columns:
                conn.execute(
                    text(
                        f"ALTER TABLE jobs ADD COLUMN responsibilities JSON NOT NULL DEFAULT {json_empty_list_default}"
                    )
                )
            if "skills_required" not in job_columns:
                conn.execute(
                    text(
                        f"ALTER TABLE jobs ADD COLUMN skills_required JSON NOT NULL DEFAULT {json_empty_list_default}"
                    )
                )
            if "experience_level" not in job_columns:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN experience_level VARCHAR(255) NOT NULL DEFAULT ''"))
            if "structured_data" not in job_columns:
                conn.execute(
                    text(
                        f"ALTER TABLE jobs ADD COLUMN structured_data JSON NOT NULL DEFAULT {json_empty_object_default}"
                    )
                )
            if "ats_job_id" not in job_columns:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN ats_job_id VARCHAR(128) NULL DEFAULT NULL"))
            if "created_by" not in job_columns:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN created_by VARCHAR(36) NOT NULL DEFAULT ''"))
            if "remote_policy" not in job_columns:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN remote_policy VARCHAR(64) NOT NULL DEFAULT ''"))
            if "experience_required" not in job_columns:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN experience_required VARCHAR(255) NOT NULL DEFAULT ''"))
            if "updated_at" not in job_columns:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"))

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

        if "candidate_profiles" in table_names:
            candidate_columns = {column["name"] for column in inspector.get_columns("candidate_profiles")}
            if "company_id" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidate_profiles ADD COLUMN company_id VARCHAR(36) NOT NULL DEFAULT ''"))
            if "candidate_status" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidate_profiles ADD COLUMN candidate_status VARCHAR(64) NOT NULL DEFAULT 'new'"))
            if "resume_received_at" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidate_profiles ADD COLUMN resume_received_at TIMESTAMPTZ NULL DEFAULT NULL"))
            if "total_experience_years" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidate_profiles ADD COLUMN total_experience_years DOUBLE PRECISION NOT NULL DEFAULT 0"))
            if "current_title" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidate_profiles ADD COLUMN current_title VARCHAR(255) NOT NULL DEFAULT ''"))
            if "current_company" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidate_profiles ADD COLUMN current_company VARCHAR(255) NOT NULL DEFAULT ''"))
            if "phone" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidate_profiles ADD COLUMN phone VARCHAR(64) NOT NULL DEFAULT ''"))
            if "linkedin_url" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidate_profiles ADD COLUMN linkedin_url VARCHAR(500) NOT NULL DEFAULT ''"))
            if "github_url" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidate_profiles ADD COLUMN github_url VARCHAR(500) NOT NULL DEFAULT ''"))
            if "parsed_resume_json" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidate_profiles ADD COLUMN parsed_resume_json JSON NOT NULL DEFAULT '{}'"))
            if "parsed_resume_text" not in candidate_columns:
                conn.execute(text("ALTER TABLE candidate_profiles ADD COLUMN parsed_resume_text TEXT NOT NULL DEFAULT ''"))

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
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN source_app VARCHAR(32) NOT NULL DEFAULT 'dashboard'"))
            if "company_id" not in outreach_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN company_id VARCHAR(36) NOT NULL DEFAULT ''"))
            if "reply_intent" not in outreach_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN reply_intent VARCHAR(64) NOT NULL DEFAULT ''"))
            if "sent_at" not in outreach_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN sent_at TIMESTAMPTZ NULL DEFAULT NULL"))

        if "interviews" in table_names:
            interview_columns = {column["name"] for column in inspector.get_columns("interviews")}
            if "source_app" not in interview_columns:
                conn.execute(text("ALTER TABLE interviews ADD COLUMN source_app VARCHAR(32) NOT NULL DEFAULT 'dashboard'"))

        if "notification_workflow_tokens" not in table_names:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE notification_workflow_tokens (
                        id VARCHAR(36) PRIMARY KEY,
                        source_app VARCHAR(32) NOT NULL DEFAULT 'dashboard',
                        job_id VARCHAR(36) NOT NULL,
                        candidate_id VARCHAR(128) NOT NULL,
                        token_type VARCHAR(64) NOT NULL DEFAULT '',
                        workflow_name VARCHAR(64) NOT NULL DEFAULT '',
                        token VARCHAR(255) NOT NULL UNIQUE,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        status VARCHAR(32) NOT NULL DEFAULT 'active',
                        payload JSON NOT NULL DEFAULT {json_empty_object_default},
                        expires_at TIMESTAMPTZ NULL DEFAULT NULL,
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
            if "is_active" not in token_columns:
                conn.execute(text("ALTER TABLE notification_workflow_tokens ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE"))

        if "inbound_email_replies" in table_names:
            inbound_columns = {column["name"] for column in inspector.get_columns("inbound_email_replies")}
            if "company_id" not in inbound_columns:
                conn.execute(text("ALTER TABLE inbound_email_replies ADD COLUMN company_id VARCHAR(36) NULL DEFAULT NULL"))
            if "intent" not in inbound_columns:
                conn.execute(text("ALTER TABLE inbound_email_replies ADD COLUMN intent VARCHAR(64) NOT NULL DEFAULT ''"))
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

        if "jobs" in table_names:
            job_columns = {column["name"] for column in inspector.get_columns("jobs")}
            if "auto_export_to_ats" not in job_columns:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN auto_export_to_ats BOOLEAN NOT NULL DEFAULT FALSE"))

        if "outreach_events" in table_names:
            oe_columns = {column["name"] for column in inspector.get_columns("outreach_events")}
            if "follow_up_count" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN follow_up_count INTEGER NOT NULL DEFAULT 0"))
            if "provider_message_id" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN provider_message_id VARCHAR(255) NULL DEFAULT NULL"))
            else:
                conn.execute(text("UPDATE outreach_events SET provider_message_id = NULL WHERE provider_message_id = ''"))
                conn.execute(text("ALTER TABLE outreach_events ALTER COLUMN provider_message_id DROP NOT NULL"))
                conn.execute(text("ALTER TABLE outreach_events ALTER COLUMN provider_message_id DROP DEFAULT"))
            if "last_contacted_at" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN last_contacted_at TIMESTAMPTZ NULL DEFAULT NULL"))
            if "next_follow_up_at" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN next_follow_up_at TIMESTAMPTZ NULL DEFAULT NULL"))
            if "message_text" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN message_text TEXT NOT NULL DEFAULT ''"))
            if "resume_url" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN resume_url VARCHAR(500) NOT NULL DEFAULT ''"))
            if "responded_at" not in oe_columns:
                conn.execute(text("ALTER TABLE outreach_events ADD COLUMN responded_at TIMESTAMPTZ NULL DEFAULT NULL"))
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
            if "company_id" not in interview_session_columns:
                conn.execute(text("ALTER TABLE interview_sessions ADD COLUMN company_id VARCHAR(36) NOT NULL DEFAULT ''"))
            if "outreach_event_id" not in interview_session_columns:
                conn.execute(text("ALTER TABLE interview_sessions ADD COLUMN outreach_event_id VARCHAR(36) NULL DEFAULT NULL"))
            if "scheduled_at" not in interview_session_columns:
                scheduled_column_type = "TIMESTAMPTZ" if dialect == "postgresql" else "DATETIME"
                conn.execute(text(f"ALTER TABLE interview_sessions ADD COLUMN scheduled_at {scheduled_column_type} NULL DEFAULT NULL"))
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
            if "booking_url" not in interview_session_columns:
                conn.execute(text("ALTER TABLE interview_sessions ADD COLUMN booking_url VARCHAR(1024) NOT NULL DEFAULT ''"))

        if "job_intakes" in table_names:
            job_intake_columns = {column["name"] for column in inspector.get_columns("job_intakes")}
            if "company_id" not in job_intake_columns:
                conn.execute(text("ALTER TABLE job_intakes ADD COLUMN company_id VARCHAR(36) NOT NULL DEFAULT ''"))
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
                        company_id VARCHAR(36) NULL DEFAULT NULL,
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
            if "company_id" not in orchestration_columns:
                conn.execute(text("ALTER TABLE orchestration_sessions ADD COLUMN company_id VARCHAR(36) NULL DEFAULT NULL"))
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
                "companies": ("created_at",),
                "jobs": ("created_at", "last_candidate_attempt_at"),
                "job_intakes": ("created_at", "completed_at", "updated_at"),
                "interviews": ("created_at",),
                "candidate_profiles": ("last_scored_at", "last_refreshed_at"),
                "scoring_profiles": ("updated_at",),
                "candidate_feedback": ("updated_at", "created_at"),
                "ats_exports": ("exported_at",),
                "outreach_events": (
                    "sent_at",
                    "last_sent_at",
                    "last_contacted_at",
                    "next_follow_up_at",
                    "responded_at",
                    "updated_at",
                    "created_at",
                ),
                "otps": ("expires_at", "created_at"),
                "ats_export_retries": ("next_retry_at", "created_at", "updated_at"),
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
    remove orphan references that violate (job_id, candidate_id) -> candidate_profiles.
    """
    with engine.begin() as conn:
        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())
        if "candidate_profiles" not in table_names:
            return

        if "interviews" in table_names:
            orphan_interviews = conn.execute(
                text(
                    """
                    SELECT i.id, i.job_id, i.candidate_id
                    FROM interviews i
                    LEFT JOIN candidate_profiles cp
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
                            FROM candidate_profiles cp
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
                    LEFT JOIN candidate_profiles cp
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
                            FROM candidate_profiles cp
                            WHERE cp.job_id = outreach_events.job_id
                              AND cp.candidate_id = outreach_events.candidate_id
                        )
                        """
                    )
                )
