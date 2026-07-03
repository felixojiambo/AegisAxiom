"""Seed the SimpliMeta root tenant + backfill existing users.

Idempotent: safe to run on every server startup.

On first run:
  * Creates the SimpliMeta root tenant if it doesn't exist.
  * Stamps every user document that has no `tenant_id` with the SimpliMeta
    root tenant_id so legacy users continue to function.

On subsequent runs:
  * No-ops if the root tenant exists and all users have a tenant_id.

The SimpliMeta root tenant_id is intentionally STABLE across environments
(`ten_simplimeta_root`) rather than randomly generated, so:
  1. Code that special-cases the root (admin tooling, cross-tenant analytics)
     can reference it by a constant rather than a config lookup.
  2. Migrations and backfills are deterministic across dev/staging/prod.

Phase 1A only — Phase 1B introduces partner/account seeding for demo data
in seed_demo_tenants.py.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# Stable, well-known root tenant id. Do not change.
SIMPLIMETA_ROOT_TENANT_ID = "ten_simplimeta_root"
SIMPLIMETA_ROOT_SLUG = "simplimeta"
SIMPLIMETA_ROOT_NAME = "SimpliMeta"


async def seed_root_tenant(db) -> dict:
    """Ensure the SimpliMeta root tenant exists. Returns the tenant doc."""
    existing = await db.tenants.find_one(
        {"tenant_id": SIMPLIMETA_ROOT_TENANT_ID}, {"_id": 0}
    )
    if existing:
        # If somehow the existing doc has the wrong type or status, normalise it.
        # (Defensive — should never happen in practice.)
        if existing.get("tenant_type") != "root" or existing.get("status") != "active":
            await db.tenants.update_one(
                {"tenant_id": SIMPLIMETA_ROOT_TENANT_ID},
                {
                    "$set": {
                        "tenant_type": "root",
                        "status": "active",
                        "parent_tenant_id": None,
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
            )
            logger.warning(
                "Normalised SimpliMeta root tenant doc — type/status/parent were drifted."
            )
            existing = await db.tenants.find_one(
                {"tenant_id": SIMPLIMETA_ROOT_TENANT_ID}, {"_id": 0}
            )
        return existing

    now = datetime.now(timezone.utc)
    doc = {
        "tenant_id": SIMPLIMETA_ROOT_TENANT_ID,
        "name": SIMPLIMETA_ROOT_NAME,
        "slug": SIMPLIMETA_ROOT_SLUG,
        "tenant_type": "root",
        "parent_tenant_id": None,
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "created_by": "system_seed",
    }
    await db.tenants.insert_one(doc)
    logger.info("Seeded SimpliMeta root tenant: %s", SIMPLIMETA_ROOT_TENANT_ID)
    doc.pop("_id", None)
    return doc


async def backfill_users_to_root(db) -> int:
    """Stamp every user without a tenant_id with the SimpliMeta root tenant id.

    Returns the number of users updated. Idempotent.
    """
    result = await db.users.update_many(
        {"$or": [{"tenant_id": {"$exists": False}}, {"tenant_id": None}]},
        {"$set": {"tenant_id": SIMPLIMETA_ROOT_TENANT_ID}},
    )
    if result.modified_count:
        logger.info(
            "Backfilled tenant_id=SimpliMeta on %d existing users.",
            result.modified_count,
        )
    return result.modified_count


async def seed_tenants(db) -> None:
    """Top-level seed entry point. Called from server startup."""
    await seed_root_tenant(db)
    await backfill_users_to_root(db)