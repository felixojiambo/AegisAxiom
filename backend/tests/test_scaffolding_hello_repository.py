"""THROWAWAY Phase 0 proof test — DELETE IN PHASE 2.

Proves the PostgreSQL rails work end to end against a REAL Postgres:
engine → session → Alembic-migrated schema → repository → read-back.

Skips cleanly (via the pg_session fixture) when no Postgres is available, so it
never blocks the existing mongomock-motor suite. Delete this file with the rest
of repositories/_scaffolding/ when Phase 2 lands the first real domain.
"""
import pytest


@pytest.mark.asyncio
async def test_hello_repository_insert_and_read_back(pg_session):
    from repositories._scaffolding.hello_repository import HelloRepository

    repo = HelloRepository(pg_session)

    created = await repo.create(name="phase-0-rails")
    assert created.id is not None
    assert created.name == "phase-0-rails"

    fetched = await repo.get(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.name == "phase-0-rails"

    by_name = await repo.get_by_name("phase-0-rails")
    assert by_name is not None
    assert by_name.id == created.id


@pytest.mark.asyncio
async def test_hello_repository_get_missing_returns_none(pg_session):
    from repositories._scaffolding.hello_repository import HelloRepository

    repo = HelloRepository(pg_session)

    assert await repo.get(999_999) is None
    assert await repo.get_by_name("does-not-exist") is None
