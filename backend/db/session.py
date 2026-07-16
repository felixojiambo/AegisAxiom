"""Async PostgreSQL engine + session provider (Phase 0 — rails only).

Design constraints (from the migration plan §A1/§A2 and the Phase 0 prompt):

- **Lazy.** The engine is built on first use, never at import. Importing this
  module — or wiring its shutdown hook into ``server.py`` — must NOT open a
  connection. During Phase 0 Mongo is still the live datastore, so a
  missing/unreachable Postgres must not crash boot. ``create_async_engine`` does
  not connect until a session actually executes, and nothing in Phase 0 uses a
  session outside of tests, so boot stays clean.
- **No default URL.** ``DATABASE_URL`` is required; if unset we raise a clear
  error rather than silently falling back to some ``localhost`` guess.
- **Per-request transaction scope.** The ``get_session`` FastAPI dependency
  yields an ``AsyncSession``, commits on success, and rolls back on any
  exception before re-raising.

Usage (Phase 2+):

    from fastapi import Depends
    from db.session import get_session

    @router.get("/things")
    async def list_things(session = Depends(get_session)):
        return await ThingRepository(session).list_all()
"""
from __future__ import annotations

import os
from typing import AsyncIterator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def _database_url() -> str:
    """Return the configured ``DATABASE_URL`` or raise a clear error.

    Deliberately no fallback connection string — an unset URL is a
    configuration bug, not something to paper over with a localhost default.
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Point it at the Postgres service, e.g. "
            "postgresql+asyncpg://aegis:aegis@postgres:5432/aegis"
        )
    return url


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, building it lazily on first call.

    Construction does not open a connection; asyncpg connects on first use.
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            _database_url(),
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide session factory, built lazily on first call."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an ``AsyncSession`` with per-request tx scope.

    Commits when the request handler returns normally; rolls back and re-raises
    on any exception.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Dispose the async engine's connection pool on application shutdown.

    No-op when the engine was never built (the lazy path), so wiring this into
    ``server.py``'s shutdown handler is safe during the Mongo-only Phase 0 run —
    it cannot connect, and cannot fail, if Postgres was never touched.
    """
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None