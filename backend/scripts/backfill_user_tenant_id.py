"""Standalone backfill: stamp tenant_id on every legacy user document.

Normally the seed runs at server startup (see seeds/seed_tenants.py and
server.py wiring). This standalone script exists so ops can:
  * Backfill an environment WITHOUT restarting the server.
  * Run the backfill from a one-off pod / local docker-compose.
  * Verify counts before/after on a live database.

Idempotent. Safe to run repeatedly.

Usage (from the backend container or a local env with MONGO_URL set):

    python -m scripts.backfill_user_tenant_id           # apply backfill
    python -m scripts.backfill_user_tenant_id --dry-run # show counts only
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from database import get_db, init as db_init


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backfill_user_tenant_id")


async def _ensure_db():
    db = get_db()
    if db is not None:
        return db
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        print(
            "ERROR: MONGO_URL and DB_NAME env vars are required when running "
            "this script outside a server process.",
            file=sys.stderr,
        )
        sys.exit(2)
    db_init(mongo_url, db_name)
    return get_db()


async def _run(dry_run: bool) -> int:
    from seeds.seed_tenants import (
        SIMPLIMETA_ROOT_TENANT_ID,
        seed_root_tenant,
    )

    db = await _ensure_db()

    # Make sure the root tenant exists before referencing it.
    await seed_root_tenant(db)

    missing_query = {
        "$or": [{"tenant_id": {"$exists": False}}, {"tenant_id": None}]
    }
    total_users = await db.users.count_documents({})
    missing_count = await db.users.count_documents(missing_query)

    logger.info(
        "Users in DB: %d; users without tenant_id: %d",
        total_users,
        missing_count,
    )

    if missing_count == 0:
        logger.info("Nothing to backfill. Exiting.")
        return 0

    if dry_run:
        logger.info("Dry run — would stamp tenant_id=%s on %d users.",
                    SIMPLIMETA_ROOT_TENANT_ID, missing_count)
        return 0

    result = await db.users.update_many(
        missing_query,
        {"$set": {"tenant_id": SIMPLIMETA_ROOT_TENANT_ID}},
    )
    logger.info(
        "Stamped tenant_id=%s on %d users.",
        SIMPLIMETA_ROOT_TENANT_ID,
        result.modified_count,
    )
    return result.modified_count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill tenant_id on legacy users to SimpliMeta root."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be changed without writing.",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.dry_run))


if __name__ == "__main__":
    main()