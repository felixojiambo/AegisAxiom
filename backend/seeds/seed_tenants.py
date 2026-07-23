"""Seed the SimpliMeta root tenant + backfill existing users (Phase 2a: Postgres).

Idempotent: safe to run on every server startup.

On first run:
  * Creates the SimpliMeta root tenant if it doesn't exist.
  * Stamps every user row that has no `tenant_id` with the SimpliMeta root
    tenant_id so legacy users continue to function.

On subsequent runs:
  * No-ops if the root tenant exists and all users have a tenant_id.

The SimpliMeta root tenant_id is intentionally STABLE across environments
(`ten_simplimeta_root`) rather than randomly generated.

Phase 2a: rewritten from Mongo to the Postgres tenants/users repositories. The
entry point ``seed_tenants()`` opens its own session (it runs at startup, not in
a request) and commits.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# Stable, well-known root tenant id. Do not change.
SIMPLIMETA_ROOT_TENANT_ID = "ten_simplimeta_root"
SIMPLIMETA_ROOT_SLUG = "simplimeta"
SIMPLIMETA_ROOT_NAME = "SimpliMeta"


def _root_tenant_model():
    from models import Tenant

    now = datetime.now(timezone.utc)
    return Tenant(
        tenant_id=SIMPLIMETA_ROOT_TENANT_ID,
        name=SIMPLIMETA_ROOT_NAME,
        slug=SIMPLIMETA_ROOT_SLUG,
        tenant_type="root",
        parent_tenant_id=None,
        status="active",
        created_at=now,
        updated_at=now,
        created_by="system_seed",
    )


async def seed_tenants() -> None:
    """Top-level seed entry point. Called from server startup.

    Opens a Postgres session, ensures the root tenant, backfills users missing a
    tenant_id, and commits.
    """
    from db.session import get_sessionmaker
    from repositories.tenants import TenantRepository
    from repositories.users import UserRepository

    sm = get_sessionmaker()
    async with sm() as session:
        tenant = await TenantRepository(session).upsert_root_tenant(_root_tenant_model())
        backfilled = await UserRepository(session).update_tenant_id_where_missing(
            SIMPLIMETA_ROOT_TENANT_ID
        )
        await session.commit()

    logger.info("Ensured SimpliMeta root tenant: %s", tenant.tenant_id)
    if backfilled:
        logger.info("Backfilled tenant_id=SimpliMeta on %d existing users.", backfilled)


# ---- Backward-compat wrappers (Postgres-backed) -------------------------------
# The one-off `scripts/backfill_user_tenant_id.py` imports these names. They now
# run against Postgres and ignore any passed (Mongo) `db` argument. Not used by
# any live request path.

async def seed_root_tenant(db=None) -> dict:
    from db.session import get_sessionmaker
    from repositories.tenants import TenantRepository

    sm = get_sessionmaker()
    async with sm() as session:
        tenant = await TenantRepository(session).upsert_root_tenant(_root_tenant_model())
        await session.commit()
    return tenant.model_dump()


async def backfill_users_to_root(db=None) -> int:
    from db.session import get_sessionmaker
    from repositories.users import UserRepository

    sm = get_sessionmaker()
    async with sm() as session:
        n = await UserRepository(session).update_tenant_id_where_missing(
            SIMPLIMETA_ROOT_TENANT_ID
        )
        await session.commit()
    if n:
        logger.info("Backfilled tenant_id=SimpliMeta on %d existing users.", n)
    return n
