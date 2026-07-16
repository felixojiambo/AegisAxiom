"""Shared test constants for HTTP-integration tests.

Imported by test_distributors.py, test_hardware_material_requests.py, etc.
to avoid copy-pasted admin credentials across files.

Required environment variables for integration tests:
    TEST_ADMIN_EMAIL     — admin user email (defaults to support@simplimeta.io)
    TEST_ADMIN_PASSWORD  — admin user password (no default; tests should skip if unset)
"""

import os

import pytest
import pytest_asyncio

TEST_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "support@simplimeta.io")
TEST_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "")


# ---------------------------------------------------------------------------
# PostgreSQL test fixtures (Phase 0 — Postgres re-platform, plan §A11)
#
# These run repository/ORM tests against a REAL Postgres (never a mock). Two
# ways to provide one:
#   1. CI (primary):  set TEST_DATABASE_URL to a compose test database
#                     (docker-compose.test.yml).
#   2. Local (fallback): testcontainers spins an ephemeral pgvector container.
#
# If NEITHER is available (no TEST_DATABASE_URL and no Docker/testcontainers),
# the fixtures SKIP — they never fail. This keeps the existing mongomock-motor
# suite completely unaffected: those tests don't use these fixtures, and all
# heavy imports (sqlalchemy/alembic/testcontainers) live INSIDE the fixtures so
# merely importing this conftest never requires the new dependencies.
# ---------------------------------------------------------------------------


def _normalize_async_url(url: str) -> str:
    """Ensure the URL uses the asyncpg driver."""
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


@pytest.fixture(scope="session")
def pg_url():
    """Session-scoped Postgres URL: env-provided, else a testcontainer, else skip."""
    env_url = os.getenv("TEST_DATABASE_URL")
    if env_url:
        yield _normalize_async_url(env_url)
        return

    try:
        from testcontainers.postgres import PostgresContainer
    except Exception:
        pytest.skip(
            "Postgres unavailable: set TEST_DATABASE_URL or install testcontainers "
            "(+running Docker) to run the Postgres repository tests."
        )
        return

    try:
        with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as container:
            yield _normalize_async_url(container.get_connection_url())
    except Exception as exc:  # docker not running, image pull failed, etc.
        pytest.skip(f"Could not start Postgres testcontainer (Docker unavailable?): {exc}")


@pytest.fixture(scope="session")
def pg_migrated_url(pg_url):
    """Apply `alembic upgrade head` once per session against the ephemeral DB."""
    import pathlib

    from alembic import command
    from alembic.config import Config

    backend_dir = pathlib.Path(__file__).resolve().parents[1]
    os.environ["DATABASE_URL"] = pg_url  # env.py reads this

    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")

    yield pg_url


@pytest_asyncio.fixture
async def pg_session(pg_migrated_url):
    """Function-scoped AsyncSession with transaction-rollback-per-test isolation.

    Each test runs inside a transaction that is rolled back on teardown, so tests
    never see each other's writes and the schema is set up exactly once per
    session. For parallel CI (`pytest -n`), give each worker its own database or
    schema (see repositories/CONVENTIONS.md); Phase 0 ships single-worker.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine(pg_migrated_url)
    conn = await engine.connect()
    trans = await conn.begin()
    session = AsyncSession(bind=conn, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        if trans.is_active:
            await trans.rollback()
        await conn.close()
        await engine.dispose()
