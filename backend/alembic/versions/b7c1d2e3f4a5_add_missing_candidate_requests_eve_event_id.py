"""add missing candidate_requests eve_event_id

Revision ID: b7c1d2e3f4a5
Revises: adameveoutbox01
Create Date: 2026-08-12 12:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision = "b7c1d2e3f4a5"
down_revision = "adameveoutbox01"
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _unique_constraint_exists(inspector: sa.Inspector, table_name: str, constraint_name: str) -> bool:
    return any(constraint["name"] == constraint_name for constraint in inspector.get_unique_constraints(table_name))


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not _table_exists(inspector, "candidate_requests"):
        return

    if not _column_exists(inspector, "candidate_requests", "eve_event_id"):
        op.add_column(
            "candidate_requests",
            sa.Column("eve_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        )

    inspector = inspect(bind)

    if _column_exists(inspector, "candidate_requests", "eve_event_id"):
        if not _unique_constraint_exists(inspector, "candidate_requests", "uq_candidate_requests_eve_event_id"):
            op.create_unique_constraint(
                "uq_candidate_requests_eve_event_id",
                "candidate_requests",
                ["eve_event_id"],
            )
        if not _index_exists(inspector, "candidate_requests", "ix_candidate_requests_eve_event_id"):
            op.create_index(
                "ix_candidate_requests_eve_event_id",
                "candidate_requests",
                ["eve_event_id"],
                unique=False,
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not _table_exists(inspector, "candidate_requests"):
        return

    if _index_exists(inspector, "candidate_requests", "ix_candidate_requests_eve_event_id"):
        op.drop_index("ix_candidate_requests_eve_event_id", table_name="candidate_requests")

    inspector = inspect(bind)

    if _unique_constraint_exists(inspector, "candidate_requests", "uq_candidate_requests_eve_event_id"):
        op.drop_constraint("uq_candidate_requests_eve_event_id", "candidate_requests", type_="unique")

    inspector = inspect(bind)

    if _column_exists(inspector, "candidate_requests", "eve_event_id"):
        op.drop_column("candidate_requests", "eve_event_id")
