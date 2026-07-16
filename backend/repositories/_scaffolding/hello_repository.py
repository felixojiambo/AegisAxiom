"""THROWAWAY Phase 0 scaffolding repository — DELETE IN PHASE 2.

Demonstrates the §A2 pattern end to end: takes an ``AsyncSession``, exposes
intention-revealing methods, converts ORM ⇄ Pydantic at the boundary, and never
touches Mongo.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel
from sqlalchemy import select

from repositories.base import BaseRepository
from repositories._scaffolding.hello_model import HelloRow


class Hello(BaseModel):
    """Pydantic DTO returned across the repository boundary."""

    id: Optional[int] = None
    name: str


class HelloRepository(BaseRepository):
    """Scaffolding repository proving insert + read-back through the session."""

    async def create(self, name: str) -> Hello:
        row = HelloRow(name=name)
        self.session.add(row)
        # flush (not commit) to populate the generated id; the caller/request
        # dependency owns the transaction boundary.
        await self.session.flush()
        return Hello(id=row.id, name=row.name)

    async def get(self, hello_id: int) -> Optional[Hello]:
        row = await self.session.get(HelloRow, hello_id)
        return Hello(id=row.id, name=row.name) if row is not None else None

    async def get_by_name(self, name: str) -> Optional[Hello]:
        result = await self.session.execute(
            select(HelloRow).where(HelloRow.name == name)
        )
        row = result.scalar_one_or_none()
        return Hello(id=row.id, name=row.name) if row is not None else None