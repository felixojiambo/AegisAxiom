"""Phase 2a — CAS concurrency races against real Postgres (plan §A6/§A11).

Proves the single-use compare-and-swaps have exactly one winner under genuine
concurrency: two independent transactions race to consume the same row and
exactly one succeeds. This needs COMMITTED rows and SEPARATE connections, so it
builds its own engine/sessions from ``pg_migrated_url`` rather than the shared
rolled-back ``pg_session``. Test rows are committed, so each test cleans up
after itself.

Note: the 2a domain has two single-use CAS operations — reset-token consume
(`UPDATE … WHERE used=false RETURNING`) and oauth-state consume
(`DELETE … WHERE state,provider RETURNING`). There is no session-claim CAS in
2a (artifact-claim CAS is Phase 8), so both domain CAS paths are covered here.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from repositories.auth import AuthRepository


def _now():
    return datetime.now(timezone.utc)


@pytest_asyncio.fixture
async def sessionmaker_pg(pg_migrated_url):
    """An async_sessionmaker over a dedicated engine (separate connections)."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(pg_migrated_url, pool_size=5, max_overflow=5)
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reset_token_consume_race_one_winner(sessionmaker_pg):
    """Two concurrent consumers of the same reset token → exactly one winner."""
    # Seed the token in its own committed transaction.
    async with sessionmaker_pg() as s:
        await AuthRepository(s).upsert_reset_token(
            "race@x.io", "tok_race", _now() + timedelta(hours=1)
        )
        await s.commit()

    async def consume():
        async with sessionmaker_pg() as s:
            res = await AuthRepository(s).consume_reset_token("tok_race")
            await s.commit()
            return res

    try:
        results = await asyncio.gather(consume(), consume())
        winners = [r for r in results if r is not None]
        assert len(winners) == 1, f"expected exactly one winner, got {len(winners)}"
        assert winners[0].email == "race@x.io"
        # A third, later attempt also loses (already used).
        assert await consume() is None
    finally:
        async with sessionmaker_pg() as s:
            from db.models.auth import PasswordResetTokenRow
            row = await s.get(PasswordResetTokenRow, "race@x.io")
            if row is not None:
                await s.delete(row)
                await s.commit()


@pytest.mark.asyncio
async def test_oauth_state_consume_race_one_winner(sessionmaker_pg):
    """Two concurrent consumers of the same oauth state → exactly one winner."""
    async with sessionmaker_pg() as s:
        await AuthRepository(s).create_oauth_state("st_race", "zoho", user_id="user_x")
        await s.commit()

    async def consume():
        async with sessionmaker_pg() as s:
            res = await AuthRepository(s).consume_oauth_state("st_race", "zoho")
            await s.commit()
            return res

    try:
        results = await asyncio.gather(consume(), consume())
        winners = [r for r in results if r is not None]
        assert len(winners) == 1, f"expected exactly one winner, got {len(winners)}"
        assert winners[0].state == "st_race"
        # The row is gone (DELETE … RETURNING) — a later attempt finds nothing.
        assert await consume() is None
    finally:
        async with sessionmaker_pg() as s:
            from db.models.auth import OAuthStateRow
            row = await s.get(OAuthStateRow, "st_race")
            if row is not None:
                await s.delete(row)
                await s.commit()
