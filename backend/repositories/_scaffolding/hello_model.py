"""THROWAWAY Phase 0 scaffolding model — DELETE IN PHASE 2.

A trivial table (`_scaffolding_hello`) used only to prove the ORM + Alembic +
repository + test loop. The leading underscore in the table name marks it as
non-domain scaffolding. Registered against ``Base.metadata`` via the import in
``alembic/env.py``.
"""
from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class HelloRow(Base):
    """Scaffolding-only ORM row. Not a real domain entity."""

    __tablename__ = "_scaffolding_hello"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)