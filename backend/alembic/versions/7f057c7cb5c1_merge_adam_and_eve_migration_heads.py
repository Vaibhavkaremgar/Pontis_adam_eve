"""merge Adam and Eve migration heads

Revision ID: 7f057c7cb5c1
Revises: c9eefe10d37d, 7b1c30901cbf
Create Date: 2026-08-14 18:01:00.000000

Adam (this repo) and Eve now share the same Railway PostgreSQL database.

Adam's head:  c9eefe10d37d  (merge_eve_migrations)
Eve's head:   7b1c30901cbf  (Eve-internal merge — stub in 7b1c30901cbf_eve_head_stub.py)

This merge revision advances the shared database to a single canonical
head regardless of which application deployed last.

No schema changes.
"""
from __future__ import annotations


revision: str = "7f057c7cb5c1"
down_revision = (
    "c9eefe10d37d",
    "7b1c30901cbf",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
