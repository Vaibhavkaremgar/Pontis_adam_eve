from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import AUTO_RECREATE_SCHEMA, DATABASE_URL
from app.db.base import Base

logger = logging.getLogger(__name__)

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_optional_schema_columns()
    _reconcile_legacy_schema_if_needed()
    _cleanup_invalid_candidate_references()


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
            "job_status",
            "vetting_mode",
            "title",
            "description",
            "location",
            "compensation",
            "work_authorization",
            "ats_job_id",
            "company_id",
            "last_candidate_attempt_at",
            "created_at",
        ],
        "interviews": ["id", "job_id", "candidate_id", "status", "created_at"],
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
            if dialect == "postgresql":
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS unique_provider_message_id_idx "
                        "ON outreach_events (provider_message_id) "
                        "WHERE provider_message_id IS NOT NULL"
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
                "companies": ("created_at",),
                "jobs": ("created_at", "last_candidate_attempt_at"),
                "interviews": ("created_at",),
                "candidate_profiles": ("last_scored_at", "last_refreshed_at"),
                "scoring_profiles": ("updated_at",),
                "candidate_feedback": ("updated_at", "created_at"),
                "ats_exports": ("exported_at",),
                "outreach_events": (
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
