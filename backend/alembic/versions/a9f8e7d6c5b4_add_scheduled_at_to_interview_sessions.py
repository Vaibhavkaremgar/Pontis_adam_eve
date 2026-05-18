"""add scheduled_at to interview_sessions

Revision ID: a9f8e7d6c5b4
Revises: 44bc60c1f819
Create Date: 2026-05-18 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a9f8e7d6c5b4"
down_revision: Union[str, Sequence[str], None] = "44bc60c1f819"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name

    if "interview_sessions" not in inspector.get_table_names():
        return

    columns = {column["name"]: column for column in inspector.get_columns("interview_sessions")}
    if "scheduled_at" not in columns:
        op.add_column("interview_sessions", sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True))
        return

    if dialect == "postgresql":
        column_type = columns["scheduled_at"].get("type")
        if column_type and column_type.__class__.__name__.lower() not in {"datetime", "timestamptz"}:
            op.alter_column(
                "interview_sessions",
                "scheduled_at",
                type_=sa.DateTime(timezone=True),
                postgresql_using="NULLIF(scheduled_at, '')::timestamptz",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "interview_sessions" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("interview_sessions")}
    if "scheduled_at" in columns:
        op.drop_column("interview_sessions", "scheduled_at")
