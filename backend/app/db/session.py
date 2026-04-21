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
    _reconcile_legacy_schema_if_needed()
    Base.metadata.create_all(bind=engine)
    _ensure_optional_schema_columns()


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
        "jobs": ["id", "title", "description", "location", "compensation", "work_authorization", "company_id", "created_at"],
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

        if "companies" in table_names:
            company_columns = {column["name"] for column in inspector.get_columns("companies")}
            if "industry" not in company_columns:
                conn.execute(text("ALTER TABLE companies ADD COLUMN industry VARCHAR(255) NOT NULL DEFAULT ''"))
