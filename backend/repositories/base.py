"""Base repository — establishes the §A2 seam pattern.

A repository is a thin, intention-revealing gateway over an ``AsyncSession``:

    class UserRepository(BaseRepository):
        async def get_by_email(self, email: str) -> User | None:
            row = (await self.session.execute(
                select(UserRow).where(UserRow.email == email)
            )).scalar_one_or_none()
            return User.model_validate(row, from_attributes=True) if row else None

Rules (enforced by review, documented in CONVENTIONS.md):
  - Repositories take an ``AsyncSession`` and nothing else DB-related.
  - Methods are named by intent (``get_by_email``, ``claim_next_wave_number``),
    not by SQL shape.
  - Repositories return Pydantic models (or plain values), never ORM rows, so
    callers stay decoupled from the persistence layer.
  - No ``db.`` / Mongo access ever appears inside a repository.
  - Transaction scope is owned by the caller (the ``get_session`` request
    dependency, or an explicit ``async with session.begin()`` for multi-write
    clusters — plan §A6.2). Repository methods ``flush`` when they need a
    generated id but do not ``commit``.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    """Common base for all domain repositories.

    Holds the per-request/-unit ``AsyncSession``. Subclasses add
    intention-revealing methods; they must not open their own sessions or
    manage global transactions.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session