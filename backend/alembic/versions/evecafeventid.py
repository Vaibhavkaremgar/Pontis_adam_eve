"""add candidate notification event id

Revision ID: evecafeventid
Revises: c45801ffb70c
Create Date: 2026-08-12 00:30:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "evecafeventid"
down_revision = "c45801ffb70c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("candidate_requests", sa.Column("eve_event_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_unique_constraint("uq_candidate_requests_eve_event_id", "candidate_requests", ["eve_event_id"])
    op.create_index("ix_candidate_requests_eve_event_id", "candidate_requests", ["eve_event_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_candidate_requests_eve_event_id", table_name="candidate_requests")
    op.drop_constraint("uq_candidate_requests_eve_event_id", "candidate_requests", type_="unique")
    op.drop_column("candidate_requests", "eve_event_id")
