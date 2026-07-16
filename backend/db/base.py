"""Declarative base + shared metadata for all PostgreSQL ORM models.

Every domain ORM model (starting in Phase 2) inherits from ``Base``. Alembic's
``env.py`` points ``target_metadata`` at ``Base.metadata`` so hand-written
migrations share one metadata object and future autogenerate has a single
source of truth.

Phase 0: only the throwaway scaffolding model registers against this metadata,
purely to prove the engine → session → Alembic → repository → test loop works
end to end. It is deleted in Phase 2.
"""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all AegisAxiom PostgreSQL models."""

    pass