"""drop the Phase-0 scaffolding table (Phase 2a)

The throwaway ``_scaffolding_hello`` table proved the rails end to end in
Phase 0. With the first real domain (2a platform-core identity) landed, its job
is done — this migration drops it, and ``repositories/_scaffolding/`` + the
scaffolding import in ``alembic/env.py`` are deleted alongside.

``downgrade`` recreates the table by hand (it does NOT reference the deleted ORM
model) so ``downgrade base`` still round-trips to the exact prior schema.

Revision ID: 0004_drop_scaffolding_hello
Revises: 0003_platform_core_identity
Create Date: 2026-07-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_drop_scaffolding_hello"
down_revision: Union[str, None] = "0003_platform_core_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("_scaffolding_hello")


def downgrade() -> None:
    # Recreate by hand — the ORM model is gone. Mirrors 0002_scaffolding_hello.
    op.create_table(
        "_scaffolding_hello",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
    )
