"""fix_linkedin_accounts_fk_to_agencies

The original linkedin_accounts migration (c8d9e0f1a2b3) created the table
with a column named company_id referencing a table called companies.id.
The real table in this schema is agencies, and every other model uses
agency_id as the physical column name with company_id as a Python synonym.

This migration:
1. Drops the incorrect FK constraint and index on company_id.
2. Renames the column from company_id to agency_id.
3. Recreates the FK pointing at agencies.id.
4. Recreates the index under the canonical name ix_linkedin_accounts_company_id
   (matching the ORM __table_args__).

Revision ID: d1e2f3a4b5c6
Revises: c8d9e0f1a2b3
Create Date: 2026-07-21 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


revision = "d1e2f3a4b5c6"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def _get_inspector() -> Inspector:
    bind = op.get_bind()
    return sa.inspect(bind)


def _fk_exists(inspector: Inspector, table: str, constraint_name: str) -> bool:
    return any(
        fk["name"] == constraint_name
        for fk in inspector.get_foreign_keys(table)
    )


def _index_exists(inspector: Inspector, table: str, index_name: str) -> bool:
    return any(
        idx["name"] == index_name
        for idx in inspector.get_indexes(table)
    )


def _column_exists(inspector: Inspector, table: str, column_name: str) -> bool:
    return any(
        col["name"] == column_name
        for col in inspector.get_columns(table)
    )


def upgrade() -> None:
    inspector = _get_inspector()

    # Drop the old index if it exists (may be on company_id or already on agency_id)
    if _index_exists(inspector, "linkedin_accounts", "ix_linkedin_accounts_company_id"):
        op.drop_index("ix_linkedin_accounts_company_id", table_name="linkedin_accounts")

    # Determine which FK names to try dropping — the original migration may have
    # created it as linkedin_accounts_company_id_fkey (PostgreSQL default naming).
    fk_candidates = [
        "linkedin_accounts_company_id_fkey",
        "fk_linkedin_accounts_company_id",
    ]

    with op.batch_alter_table("linkedin_accounts", schema=None) as batch_op:
        # Drop any pre-existing incorrect FK — check each candidate name
        for fk_name in fk_candidates:
            if _fk_exists(inspector, "linkedin_accounts", fk_name):
                batch_op.drop_constraint(fk_name, type_="foreignkey")

        # Only rename the column if it still has the old name
        if _column_exists(inspector, "linkedin_accounts", "company_id"):
            batch_op.alter_column(
                "company_id",
                new_column_name="agency_id",
                existing_type=sa.String(36),
                nullable=False,
            )

        # Add the correct FK only if it does not already exist
        if not _fk_exists(inspector, "linkedin_accounts", "fk_linkedin_accounts_agency_id"):
            batch_op.create_foreign_key(
                "fk_linkedin_accounts_agency_id",
                "agencies",
                ["agency_id"],
                ["id"],
            )

    # Recreate index on the (now renamed) agency_id column
    if not _index_exists(inspector, "linkedin_accounts", "ix_linkedin_accounts_company_id"):
        op.create_index(
            "ix_linkedin_accounts_company_id",
            "linkedin_accounts",
            ["agency_id"],
        )


def downgrade() -> None:
    inspector = _get_inspector()

    if _index_exists(inspector, "linkedin_accounts", "ix_linkedin_accounts_company_id"):
        op.drop_index("ix_linkedin_accounts_company_id", table_name="linkedin_accounts")

    with op.batch_alter_table("linkedin_accounts", schema=None) as batch_op:
        if _fk_exists(inspector, "linkedin_accounts", "fk_linkedin_accounts_agency_id"):
            batch_op.drop_constraint("fk_linkedin_accounts_agency_id", type_="foreignkey")

        if _column_exists(inspector, "linkedin_accounts", "agency_id"):
            batch_op.alter_column(
                "agency_id",
                new_column_name="company_id",
                existing_type=sa.String(36),
                nullable=False,
            )

    if not _index_exists(inspector, "linkedin_accounts", "ix_linkedin_accounts_company_id"):
        op.create_index(
            "ix_linkedin_accounts_company_id",
            "linkedin_accounts",
            ["company_id"],
        )
