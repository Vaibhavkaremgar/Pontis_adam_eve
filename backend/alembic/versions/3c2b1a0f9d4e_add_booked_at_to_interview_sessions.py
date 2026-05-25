"""add booked_at to interview_sessions

Revision ID: 3c2b1a0f9d4e
Revises: 67925be04abf
Create Date: 2026-05-25 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "3c2b1a0f9d4e"
down_revision = "67925be04abf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name

    if "interview_sessions" not in inspector.get_table_names():
        return

    columns = {column["name"]: column for column in inspector.get_columns("interview_sessions")}
    if "booked_at" not in columns:
        op.add_column("interview_sessions", sa.Column("booked_at", sa.DateTime(timezone=True), nullable=True))
        return

    if dialect == "postgresql":
        column_type = columns["booked_at"].get("type")
        data_type = str(columns["booked_at"].get("type", "")).lower()
        if column_type and column_type.__class__.__name__.lower() not in {"datetime", "timestamptz"}:
            if data_type == "timestamp without time zone":
                op.alter_column(
                    "interview_sessions",
                    "booked_at",
                    type_=sa.DateTime(timezone=True),
                    postgresql_using="booked_at AT TIME ZONE 'UTC'",
                )
                return
            op.alter_column(
                "interview_sessions",
                "booked_at",
                type_=sa.DateTime(timezone=True),
                postgresql_using="NULLIF(booked_at, '')::timestamptz",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "interview_sessions" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("interview_sessions")}
    if "booked_at" in columns:
        op.drop_column("interview_sessions", "booked_at")
