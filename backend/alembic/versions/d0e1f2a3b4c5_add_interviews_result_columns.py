"""add interview result columns

Revision ID: d0e1f2a3b4c5
Revises: b1c2d3e4f5a6
Create Date: 2026-06-05 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "d0e1f2a3b4c5"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _table_exists(inspector, "interviews"):
        columns = [
            ("interview_score", sa.Float()),
            ("technical_score", sa.Float()),
            ("communication_score", sa.Float()),
            ("culture_fit_score", sa.Float()),
            ("feedback", sa.Text()),
            ("ai_summary", sa.Text()),
            ("transcript", sa.Text()),
            ("interviewer_notes", sa.Text()),
            ("video_url", sa.String(length=512)),
            ("completed_at", sa.DateTime(timezone=True)),
        ]
        for column_name, column_type in columns:
            if not _column_exists(inspector, "interviews", column_name):
                op.add_column("interviews", sa.Column(column_name, column_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _table_exists(inspector, "interviews"):
        for column_name in (
            "video_url",
            "transcript",
            "ai_summary",
            "feedback",
            "interviewer_notes",
            "culture_fit_score",
            "communication_score",
            "technical_score",
            "interview_score",
            "completed_at",
        ):
            if _column_exists(inspector, "interviews", column_name):
                op.drop_column("interviews", column_name)
