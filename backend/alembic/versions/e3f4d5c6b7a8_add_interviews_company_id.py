"""add interviews company_id

Revision ID: e3f4d5c6b7a8
Revises: 67925be04abf
Create Date: 2026-05-13 23:40:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

from app.models.entities import GUID


revision = "e3f4d5c6b7a8"
down_revision = "67925be04abf"
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    is_sqlite = bind.dialect.name == "sqlite"

    if _table_exists(inspector, "interviews") and not _column_exists(inspector, "interviews", "company_id"):
        op.add_column("interviews", sa.Column("company_id", GUID(), nullable=True))

    if _table_exists(inspector, "interviews") and _table_exists(inspector, "jobs"):
        op.execute(
            """
            UPDATE interviews
            SET company_id = COALESCE(interviews.company_id, jobs.company_id)
            FROM jobs
            WHERE interviews.job_id = jobs.id
            """
        )

    if _table_exists(inspector, "interviews") and not _index_exists(inspector, "interviews", "ix_interviews_company_id"):
        op.create_index("ix_interviews_company_id", "interviews", ["company_id"], unique=False)

    if _table_exists(inspector, "interviews") and not is_sqlite:
        op.create_foreign_key(
            "fk_interviews_company_id_companies",
            "interviews",
            "companies",
            ["company_id"],
            ["id"],
        )

    if _table_exists(inspector, "interviews"):
        remaining_nulls = op.get_bind().execute(
            sa.text("SELECT COUNT(*) FROM interviews WHERE company_id IS NULL")
        ).scalar_one()
        if int(remaining_nulls or 0) == 0 and not is_sqlite:
            op.alter_column("interviews", "company_id", nullable=False, server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _table_exists(inspector, "interviews"):
        if not bind.dialect.name == "sqlite" and _index_exists(inspector, "interviews", "ix_interviews_company_id"):
            op.drop_constraint("fk_interviews_company_id_companies", "interviews", type_="foreignkey")
        if _index_exists(inspector, "interviews", "ix_interviews_company_id"):
            op.drop_index("ix_interviews_company_id", table_name="interviews")
        if _column_exists(inspector, "interviews", "company_id"):
            op.drop_column("interviews", "company_id")
