"""Eve canonical head anchor — merge_migration_heads

Revision ID: 7b1c30901cbf
Revises: <base>  (independent root — Eve's prior history is not replicated here)
Create Date: 2026-08-14 18:00:00.000000

PURPOSE
-------
Adam and Eve share one Railway PostgreSQL database and one canonical
Alembic migration history rooted in Adam's repository.

Eve's independent migration history terminated at revision 7b1c30901cbf
(an Eve-internal no-op merge).  Rather than replicate Eve's full prior
chain into Adam, this file acts as a graph anchor:

    7b1c30901cbf  (this file, down_revision=None)
         \n          7f057c7cb5c1  (merge_adam_and_eve_migration_heads)  <- canonical HEAD
         /
    c9eefe10d37d  (Adam's prior head)

This means:
  - If the Railway DB is at 7b1c30901cbf (Eve deployed last), Adam's
    `alembic upgrade head` advances it to 7f057c7cb5c1 cleanly.
  - If the Railway DB is at c9eefe10d37d (Adam deployed last), the same
    command also advances it to 7f057c7cb5c1 cleanly.

Eve's repository MUST contain this same file (identical revision ID,
identical down_revision=None) plus 7f057c7cb5c1_merge_adam_and_eve_migration_heads.py
so that Eve's `alembic upgrade head` resolves to the same canonical head.

SCHEMA
------
No schema changes.  This revision is a pure graph node.

DO NOT add schema changes here.
DO NOT delete this file.
DO NOT change down_revision — it must remain None.
"""
from __future__ import annotations


revision: str = "7b1c30901cbf"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
