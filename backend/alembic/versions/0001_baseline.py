"""baseline: required PostgreSQL server extensions (no tables)

Required server extensions for the AegisAxiom platform
======================================================
  - pg_trgm : trigram GIN indexes backing case-insensitive ILIKE substring
              search (migration plan §A4 — replaces the 50+ Mongo $regex sites).
  - vector  : pgvector, backing the SOW-guide embedding table introduced in
              Phase 9 (plan §A8). Enabled now so the extension is present for the
              whole parallel-run window; the first vector TABLE is Phase 9, not here.

Listing these here (per plan §A10 / OQ-6) makes a future managed-Postgres swap
(Cloud SQL / RDS / Aurora / Supabase / Neon) a config change: confirm both
extensions are on the provider's allowlist and creatable by the app role.

This baseline creates NO tables. `CREATE EXTENSION IF NOT EXISTS` is idempotent,
so upgrade is safe to re-run; downgrade drops the extensions to keep
`downgrade base` a clean empty database.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-11

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")


def downgrade() -> None:
    # Reverse order of creation. Safe on an otherwise-empty database (Phase 0);
    # in later phases these are load-bearing and would not be dropped.
    op.execute("DROP EXTENSION IF EXISTS vector;")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm;")