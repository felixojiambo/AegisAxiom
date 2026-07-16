"""scaffolding: throwaway _scaffolding_hello table (proof of rails) — DELETE IN PHASE 2

Creates the single throwaway table backing the hello-world repository test, to
prove engine → session → Alembic → repository → test works end to end against a
real Postgres. NOT a real domain table: it FKs to nothing and models no business
concept. Remove this migration together with repositories/_scaffolding/ and the
scaffolding import in alembic/env.py when Phase 2 lands the first real domain.

Revision ID: 0002_scaffolding_hello
Revises: 0001_baseline
Create Date: 2026-07-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_scaffolding_hello"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "_scaffolding_hello",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("_scaffolding_hello")