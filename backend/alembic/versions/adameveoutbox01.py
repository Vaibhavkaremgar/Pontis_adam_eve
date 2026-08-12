"""add adam eve outbound events and eve event id

Revision ID: adameveoutbox01
Revises: a0b1c2d3e4f5, evecafeventid
Create Date: 2026-08-12 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "adameveoutbox01"
down_revision = ("a0b1c2d3e4f5", "evecafeventid")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "adam_eve_outbound_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("adam_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("candidates.id"), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("job_descriptions.id"), nullable=False),
        sa.Column("agency_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agencies.id"), nullable=False),
        sa.Column("recruiter_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("recruiter_message", sa.Text(), nullable=True),
        sa.Column("notification_type", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("adam_event_id", name="uq_adam_eve_outbound_events_adam_event_id"),
        sa.UniqueConstraint("event_id", name="uq_adam_eve_outbound_events_event_id"),
    )
    op.create_index("ix_adam_eve_outbound_events_adam_event_id", "adam_eve_outbound_events", ["adam_event_id"], unique=False)
    op.create_index("ix_adam_eve_outbound_events_event_id", "adam_eve_outbound_events", ["event_id"], unique=False)
    op.create_index("ix_adam_eve_outbound_events_candidate_id", "adam_eve_outbound_events", ["candidate_id"], unique=False)
    op.create_index("ix_adam_eve_outbound_events_job_id", "adam_eve_outbound_events", ["job_id"], unique=False)
    op.create_index("ix_adam_eve_outbound_events_agency_id", "adam_eve_outbound_events", ["agency_id"], unique=False)
    op.create_index("ix_adam_eve_outbound_events_recruiter_user_id", "adam_eve_outbound_events", ["recruiter_user_id"], unique=False)
    op.create_index("ix_adam_eve_outbound_events_notification_type", "adam_eve_outbound_events", ["notification_type"], unique=False)
    op.create_index("ix_adam_eve_outbound_events_status", "adam_eve_outbound_events", ["status"], unique=False)
    op.create_index("ix_adam_eve_outbound_events_status_next_retry_at", "adam_eve_outbound_events", ["status", "next_retry_at"], unique=False)


def downgrade() -> None:
    op.drop_constraint("uq_adam_eve_outbound_events_event_id", "adam_eve_outbound_events", type_="unique")
    op.drop_constraint("uq_adam_eve_outbound_events_adam_event_id", "adam_eve_outbound_events", type_="unique")
    op.drop_index("ix_adam_eve_outbound_events_status", table_name="adam_eve_outbound_events")
    op.drop_index("ix_adam_eve_outbound_events_notification_type", table_name="adam_eve_outbound_events")
    op.drop_index("ix_adam_eve_outbound_events_recruiter_user_id", table_name="adam_eve_outbound_events")
    op.drop_index("ix_adam_eve_outbound_events_agency_id", table_name="adam_eve_outbound_events")
    op.drop_index("ix_adam_eve_outbound_events_job_id", table_name="adam_eve_outbound_events")
    op.drop_index("ix_adam_eve_outbound_events_candidate_id", table_name="adam_eve_outbound_events")
    op.drop_index("ix_adam_eve_outbound_events_event_id", table_name="adam_eve_outbound_events")
    op.drop_index("ix_adam_eve_outbound_events_adam_event_id", table_name="adam_eve_outbound_events")
    op.drop_index("ix_adam_eve_outbound_events_status_next_retry_at", table_name="adam_eve_outbound_events")
    op.drop_table("adam_eve_outbound_events")
