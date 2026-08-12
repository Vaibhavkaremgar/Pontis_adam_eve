"""add candidate requests

Revision ID: f0e1d2c3b4a5
Revises: e2f3a4b5c6d7
Create Date: 2026-08-10 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f0e1d2c3b4a5"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("candidate_id", sa.String(length=128), nullable=False),
        sa.Column("agency_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agencies.id"), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("job_descriptions.id"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("agency_id", "job_id", "candidate_id", name="uq_candidate_requests_agency_job_candidate"),
    )
    op.create_index("ix_candidate_requests_candidate_id", "candidate_requests", ["candidate_id"], unique=False)
    op.create_index("ix_candidate_requests_status", "candidate_requests", ["status"], unique=False)
    op.create_index("ix_candidate_requests_agency_job_status", "candidate_requests", ["agency_id", "job_id", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_candidate_requests_agency_job_status", table_name="candidate_requests")
    op.drop_index("ix_candidate_requests_status", table_name="candidate_requests")
    op.drop_index("ix_candidate_requests_candidate_id", table_name="candidate_requests")
    op.drop_table("candidate_requests")
