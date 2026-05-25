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


def _column_type_name(column_type: object) -> str:
    text = str(column_type or "").strip().lower()
    return text


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
        type_name = _column_type_name(column_type)
        is_datetime = isinstance(column_type, sa.DateTime)
        has_timezone = bool(getattr(column_type, "timezone", False))

        if is_datetime and has_timezone:
            return

        if is_datetime and not has_timezone:
            op.alter_column(
                "interview_sessions",
                "booked_at",
                type_=sa.DateTime(timezone=True),
                postgresql_using="booked_at AT TIME ZONE 'UTC'",
            )
            return

        if any(token in type_name for token in ("character varying", "varchar", "text", "string")):
            op.execute(
                sa.text(
                    """
                    UPDATE interview_sessions
                    SET booked_at = NULL
                    WHERE booked_at IS NOT NULL AND TRIM(booked_at) = ''
                    """
                )
            )
            op.alter_column(
                "interview_sessions",
                "booked_at",
                type_=sa.DateTime(timezone=True),
                postgresql_using="NULLIF(TRIM(booked_at), '')::timestamptz",
            )
            return

        # If the live type is already a temporal type that SQLAlchemy does not
        # classify as timezone-aware, leave it alone rather than risk a bad cast.


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "interview_sessions" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("interview_sessions")}
    if "booked_at" in columns:
        op.drop_column("interview_sessions", "booked_at")
