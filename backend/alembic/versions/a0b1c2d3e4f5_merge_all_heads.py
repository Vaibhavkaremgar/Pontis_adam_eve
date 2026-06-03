"""merge all alembic heads

Revision ID: a0b1c2d3e4f5
Revises: 1f2e3d4c5b6a, 3c2b1a0f9d4e, a1b2c3d4e5f6, d9e8f7a6b5c4, e3f4d5c6b7a8, f4e5d6c7b8a9
Create Date: 2026-06-03 00:00:00.000000
"""

from __future__ import annotations


revision = "a0b1c2d3e4f5"
down_revision = (
    "1f2e3d4c5b6a",
    "3c2b1a0f9d4e",
    "a1b2c3d4e5f6",
    "d9e8f7a6b5c4",
    "e3f4d5c6b7a8",
    "f4e5d6c7b8a9",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
