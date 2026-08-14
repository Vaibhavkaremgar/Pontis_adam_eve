"""merge eve migrations

Revision ID: c9eefe10d37d
Revises: b7c1d2e3f4a5
Create Date: 2026-08-14 17:05:18.755732

Audit summary
-------------
Eve migration 0001 (adam_eve_contract):
    recruiter_interest_requests table          -> ALREADY EXISTS in Adam (1a9b8c7d6e5f)
    notification_workflow_tokens.agency_id     -> ALREADY EXISTS in Adam (1a9b8c7d6e5f)
    notification_workflow_tokens.user_id       -> ALREADY EXISTS in Adam (1a9b8c7d6e5f)

Eve migration 0002 (candidate_notification_event_id):
    candidate_requests.eve_event_id            -> ALREADY EXISTS in Adam (evecafeventid / b7c1d2e3f4a5)
    uq_candidate_requests_eve_event_id         -> ALREADY EXISTS in Adam (b7c1d2e3f4a5)
    ix_candidate_requests_eve_event_id         -> ALREADY EXISTS in Adam (b7c1d2e3f4a5)

Eve migration 0003 (job_descriptions_ats_unique):
    UNIQUE(agency_id, ats_job_id) on job_descriptions -> MISSING -> added here

Eve migration 0004 (candidate_job_recommendations_application_fields):
    applied_at, application_status, application_notes, ats_application_id
    on candidate_job_recommendations           -> MISSING -> added here (table-exists guard)

Eve migration 7b1c30901cbf (merge_migration_heads):
    No schema change — Eve-internal merge only.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "c9eefe10d37d"
down_revision = "b7c1d2e3f4a5"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _table_exists(inspector: sa.Inspector, table: str) -> bool:
    return table in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspector.get_columns(table))


def _constraint_exists(inspector: sa.Inspector, table: str, name: str) -> bool:
    try:
        return any(c["name"] == name for c in inspector.get_unique_constraints(table))
    except Exception:
        return False


def _index_exists(inspector: sa.Inspector, table: str, index: str) -> bool:
    return any(i["name"] == index for i in inspector.get_indexes(table))


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    # ------------------------------------------------------------------
    # 0003: UNIQUE(agency_id, ats_job_id) on job_descriptions
    # Eve needs this to prevent duplicate ATS job imports per agency.
    # Only add if both columns exist (ats_job_id is Eve-managed).
    # ------------------------------------------------------------------
    if _table_exists(inspector, "job_descriptions"):
        if (
            _column_exists(inspector, "job_descriptions", "agency_id")
            and _column_exists(inspector, "job_descriptions", "ats_job_id")
            and not _constraint_exists(inspector, "job_descriptions", "uq_job_descriptions_agency_ats_job_id")
        ):
            # ats_job_id may be NULL for non-ATS jobs; use a partial index
            # so NULLs are excluded from the uniqueness check (standard behaviour).
            op.create_unique_constraint(
                "uq_job_descriptions_agency_ats_job_id",
                "job_descriptions",
                ["agency_id", "ats_job_id"],
            )

    # ------------------------------------------------------------------
    # 0004: application tracking columns on candidate_job_recommendations
    # This is Eve's table; Adam adds columns only when the table exists.
    # ------------------------------------------------------------------
    if _table_exists(inspector, "candidate_job_recommendations"):
        inspector = inspect(bind)  # refresh after potential DDL above

        if not _column_exists(inspector, "candidate_job_recommendations", "applied_at"):
            op.add_column(
                "candidate_job_recommendations",
                sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
            )

        if not _column_exists(inspector, "candidate_job_recommendations", "application_status"):
            op.add_column(
                "candidate_job_recommendations",
                sa.Column("application_status", sa.String(length=64), nullable=True),
            )

        if not _column_exists(inspector, "candidate_job_recommendations", "application_notes"):
            op.add_column(
                "candidate_job_recommendations",
                sa.Column("application_notes", sa.Text(), nullable=True),
            )

        if not _column_exists(inspector, "candidate_job_recommendations", "ats_application_id"):
            op.add_column(
                "candidate_job_recommendations",
                sa.Column("ats_application_id", sa.String(length=255), nullable=True),
            )

        # Index on application_status for Eve's pipeline queries
        inspector = inspect(bind)
        if _column_exists(inspector, "candidate_job_recommendations", "application_status"):
            if not _index_exists(inspector, "candidate_job_recommendations", "ix_cjr_application_status"):
                op.create_index(
                    "ix_cjr_application_status",
                    "candidate_job_recommendations",
                    ["application_status"],
                    unique=False,
                )


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _table_exists(inspector, "candidate_job_recommendations"):
        if _index_exists(inspector, "candidate_job_recommendations", "ix_cjr_application_status"):
            op.drop_index("ix_cjr_application_status", table_name="candidate_job_recommendations")

        inspector = inspect(bind)
        for col in ("ats_application_id", "application_notes", "application_status", "applied_at"):
            if _column_exists(inspector, "candidate_job_recommendations", col):
                op.drop_column("candidate_job_recommendations", col)

    if _table_exists(inspector, "job_descriptions"):
        if _constraint_exists(inspector, "job_descriptions", "uq_job_descriptions_agency_ats_job_id"):
            op.drop_constraint(
                "uq_job_descriptions_agency_ats_job_id",
                "job_descriptions",
                type_="unique",
            )
