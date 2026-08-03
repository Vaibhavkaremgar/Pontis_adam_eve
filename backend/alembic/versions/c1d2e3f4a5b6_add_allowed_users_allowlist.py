"""add allowed users allowlist

Revision ID: c1d2e3f4a5b6
Revises: 48354b8e0c38
Create Date: 2026-06-08 00:00:00.000000

"""
from __future__ import annotations

from uuid import uuid4

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: str | tuple[str, ...] | None = "48354b8e0c38"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


ADMIN_EMAIL = "vaibhav@pontis.one"


def upgrade() -> None:
    op.create_table(
        "allowed_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid4),
        sa.Column("email", sa.String(length=255), nullable=False, unique=True),
        sa.Column("added_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )

    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    op.execute(
        f"""
        INSERT INTO allowed_users (id, email, note, is_active, created_at)
        VALUES (gen_random_uuid(), '{ADMIN_EMAIL}', 'initial admin', true, NOW())
        ON CONFLICT (email) DO NOTHING;
        """
    )
    op.execute(
        f"""
        UPDATE users SET role = 'SUPER_ADMIN'
        WHERE email = '{ADMIN_EMAIL}';
        """
    )


def downgrade() -> None:
    op.drop_table("allowed_users")
