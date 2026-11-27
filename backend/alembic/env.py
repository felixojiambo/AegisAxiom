"""Alembic migration environment — async (asyncpg) for the AegisAxiom re-platform.

The database URL comes from the ``DATABASE_URL`` environment variable (never
hardcoded in ``alembic.ini``). ``target_metadata`` is the shared
``db.base:Base.metadata`` so every migration and any future autogenerate share
one metadata object.

Phase 0 note: the only model registered against the metadata is the throwaway
scaffolding model (imported below). It — and that import — are removed in
Phase 2 when the first real domain lands.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

# Ensure backend/ is importable (so `import db.base` works when alembic is
# invoked from the backend/ directory).
BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from db.base import Base  # noqa: E402

# --- Domain ORM model registration ----------------------------------------
# Importing the models package registers every domain's tables against
# Base.metadata (Phase 2a: platform-core identity — users, tenants, auth,
# org_branding). Later phases extend db/models/ and this single import picks
# them up. This replaced the Phase-0 scaffolding import when Phase 2a landed.
import db.models  # noqa: E402,F401

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False: fileConfig's default (True) disables every
    # logger already imported. The test fixtures run Alembic in-process, so the
    # default silenced the application loggers under test (caplog saw nothing).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _get_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL must be set to run Alembic migrations "
            "(asyncpg URL, e.g. postgresql+asyncpg://aegis:aegis@localhost:5432/aegis)."
        )
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL, no DBAPI connection)."""
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode against an async engine."""
    connectable = create_async_engine(_get_url(), poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())