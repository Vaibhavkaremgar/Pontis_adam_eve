"""Eve schema ownership transfer — Adam takes ownership of remaining Eve-managed objects

Revision ID: a3f8e2d1c9b7
Revises: 7f057c7cb5c1
Create Date: 2026-08-15 00:00:00.000000

PURPOSE
-------
Adam becomes the sole Alembic owner of the shared Railway PostgreSQL database.

Eve previously created the following objects at runtime via startup DDL.
This migration formally claims ownership of those objects so that Eve can
stop performing schema-changing DDL on startup.

OBJECTS OWNED BY THIS MIGRATION
--------------------------------
1.  TABLE  candidate_voice_intakes          (+ FK to candidates.id ON DELETE CASCADE)
2.  INDEX  idx_cvi_candidate                ON candidate_voice_intakes(candidate_id)
3.  COLUMN candidate_job_recommendations.tracked_at   TIMESTAMPTZ NULL
4.  COLUMN candidate_job_recommendations.viewed_at    TIMESTAMPTZ NULL
5.  COLUMN candidate_job_recommendations.agency_id    UUID NULL  (no FK — matches live DB)
6.  COLUMN candidate_job_recommendations.job_role     TEXT NULL
7.  COLUMN candidate_job_recommendations.status       VARCHAR (no length) NULL
8.  COLUMN candidate_job_recommendations.updated_at   TIMESTAMPTZ NULL
9.  INDEX  idx_cjr_candidate_status         ON candidate_job_recommendations(candidate_id, status)
                                            WHERE status IS NOT NULL  (partial)

NOT OWNED HERE (already in c9eefe10d37d)
-----------------------------------------
    candidate_job_recommendations.applied_at
    candidate_job_recommendations.application_status
    candidate_job_recommendations.application_notes
    candidate_job_recommendations.ats_application_id
    ix_cjr_application_status

IDEMPOTENCY
-----------
All operations are guarded by existence checks.  Running this migration
against the current production DB (where all objects already exist) is
a no-op — no rows, columns, indexes, or constraints are destroyed.

DOWNGRADE NOTE
--------------
The downgrade is intentionally conservative.  candidate_voice_intakes may
contain production data; it is NOT dropped on downgrade.  Columns added to
candidate_job_recommendations are dropped only if they exist.  The partial
index is dropped if it exists.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql


revision: str = "a3f8e2d1c9b7"
down_revision = "7f057c7cb5c1"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _table_exists(inspector: sa.Inspector, table: str) -> bool:
    return table in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table: str, column: str) -> bool:
    if not _table_exists(inspector, table):
        return False
    return any(c["name"] == column for c in inspector.get_columns(table))


def _index_exists(inspector: sa.Inspector, table: str, index: str) -> bool:
    if not _table_exists(inspector, table):
        return False
    return any(i["name"] == index for i in inspector.get_indexes(table))


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    # ------------------------------------------------------------------
    # 1. TABLE: candidate_voice_intakes
    # ------------------------------------------------------------------
    if not _table_exists(inspector, "candidate_voice_intakes"):
        op.create_table(
            "candidate_voice_intakes",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
                nullable=False,
            ),
            sa.Column(
                "candidate_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("candidates.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("transcript", sa.Text(), nullable=False),
            sa.Column("voice_notes", postgresql.JSONB(), nullable=True),
            sa.Column(
                "status",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'pending'"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )

    # ------------------------------------------------------------------
    # 2. INDEX: idx_cvi_candidate
    # ------------------------------------------------------------------
    inspector = inspect(bind)
    if not _index_exists(inspector, "candidate_voice_intakes", "idx_cvi_candidate"):
        op.create_index(
            "idx_cvi_candidate",
            "candidate_voice_intakes",
            ["candidate_id"],
            unique=False,
        )

    # ------------------------------------------------------------------
    # 3–8. Columns on candidate_job_recommendations
    # ------------------------------------------------------------------
    inspector = inspect(bind)
    if _table_exists(inspector, "candidate_job_recommendations"):

        if not _column_exists(inspector, "candidate_job_recommendations", "tracked_at"):
            op.add_column(
                "candidate_job_recommendations",
                sa.Column("tracked_at", sa.DateTime(timezone=True), nullable=True),
            )

        if not _column_exists(inspector, "candidate_job_recommendations", "viewed_at"):
            op.add_column(
                "candidate_job_recommendations",
                sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=True),
            )

        if not _column_exists(inspector, "candidate_job_recommendations", "agency_id"):
            op.add_column(
                "candidate_job_recommendations",
                # No FK — live DB has none
                sa.Column("agency_id", postgresql.UUID(as_uuid=True), nullable=True),
            )

        if not _column_exists(inspector, "candidate_job_recommendations", "job_role"):
            op.add_column(
                "candidate_job_recommendations",
                sa.Column("job_role", sa.Text(), nullable=True),
            )

        if not _column_exists(inspector, "candidate_job_recommendations", "status"):
            op.add_column(
                "candidate_job_recommendations",
                # VARCHAR without length — matches live DB (character varying, no limit)
                sa.Column("status", sa.String(), nullable=True),
            )

        if not _column_exists(inspector, "candidate_job_recommendations", "updated_at"):
            op.add_column(
                "candidate_job_recommendations",
                sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            )

    # ------------------------------------------------------------------
    # 9. INDEX: idx_cjr_candidate_status  (partial)
    # ------------------------------------------------------------------
    inspector = inspect(bind)
    if not _index_exists(inspector, "candidate_job_recommendations", "idx_cjr_candidate_status"):
        op.execute(
            text(
                "CREATE INDEX idx_cjr_candidate_status "
                "ON candidate_job_recommendations(candidate_id, status) "
                "WHERE status IS NOT NULL"
            )
        )


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    # Drop partial index if present
    if _index_exists(inspector, "candidate_job_recommendations", "idx_cjr_candidate_status"):
        op.execute(text("DROP INDEX IF EXISTS idx_cjr_candidate_status"))

    # Drop columns added to candidate_job_recommendations
    if _table_exists(inspector, "candidate_job_recommendations"):
        inspector = inspect(bind)
        for col in ("updated_at", "status", "job_role", "agency_id", "viewed_at", "tracked_at"):
            if _column_exists(inspector, "candidate_job_recommendations", col):
                op.drop_column("candidate_job_recommendations", col)
                inspector = inspect(bind)  # refresh after each DDL

    # Drop idx_cvi_candidate index
    inspector = inspect(bind)
    if _index_exists(inspector, "candidate_voice_intakes", "idx_cvi_candidate"):
        op.drop_index("idx_cvi_candidate", table_name="candidate_voice_intakes")

    # NOTE: candidate_voice_intakes is intentionally NOT dropped on downgrade.
    # The table may contain production data.  Manual intervention is required
    # if a full rollback of this table is needed.
