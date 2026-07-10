"""Phase 1E.3d — One-shot backfill for existing tenant_app_grants.

When 1E.3d ships, any grants already in the DB (Dexter's Projects
grant, etc.) were created before the auto-seeder existed. This script
walks every currently-active grant and runs the seeder once per
distinct app — adding any missing perm keys to `role_tenant_admin`.

Idempotent — safe to re-run. The seeder uses $addToSet so already-
present keys are no-ops.

## Where to run

Against any environment's main DB, from the `backend/` directory:

  cd backend
  python scripts/reseed_tenant_app_permissions.py

Requires MONGO_URL and DB_NAME in the environment (same as the app).
On dev/prod, those are already set in the shell. Locally you may need:

  MONGO_URL='mongodb+srv://...' DB_NAME='aegisaxiom' \
    python scripts/reseed_tenant_app_permissions.py

## Why this isn't a Compass mongosh script

The seeder logic is non-trivial (resolve app_id → permission_keys,
check existing perms, $addToSet diff). Replicating it inline in
mongosh would mean maintaining the same logic in two languages.
Importing the seeder from Python keeps one source of truth.

## Output

Prints a JSON summary of what changed. In dev you should see Dexter's
Projects grant landing the full projects.* key set on tenant_admin
the first time this runs. Re-runs after that show 0 keys added.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

# Allow `python scripts/reseed_tenant_app_permissions.py` from backend/
# by adding backend/ to sys.path if needed.
here = os.path.dirname(os.path.abspath(__file__))
backend = os.path.dirname(here)
if backend not in sys.path:
    sys.path.insert(0, backend)

import database  # noqa: E402
from services.permission_seeder import reseed_all_grants  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("reseed_tenant_app_permissions")


def _check_env() -> tuple[str, str]:
    """Read MONGO_URL + DB_NAME from env. Fail with a clear error if
    they're missing — the existing server.py uses these exact keys,
    so this script behaves identically to a server boot."""
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    missing = []
    if not mongo_url:
        missing.append("MONGO_URL")
    if not db_name:
        missing.append("DB_NAME")
    if missing:
        raise SystemExit(
            f"Missing required environment variables: {', '.join(missing)}.\n"
            "Set them the same way you do for the AEGISAXIOM server:\n"
            "  MONGO_URL='mongodb+srv://...' DB_NAME='aegisaxiom' python ...\n"
            "Or source your normal env file before running."
        )
    return mongo_url, db_name


async def main() -> int:
    mongo_url, db_name = _check_env()

    # Use the same connector the server uses. database.init() is a
    # plain (non-async) function — it just constructs the motor client
    # and stashes it on a module-global. The first await against the
    # collection is what opens the wire connection.
    database.init(mongo_url, db_name)
    db = database.get_db()

    logger.info(
        "Connected to %s. Starting reseed of tenant_app_grants → role_tenant_admin",
        db_name,
    )
    summary = await reseed_all_grants(db)
    print(json.dumps(summary, indent=2, default=str))
    logger.info(
        "Done. Apps processed: %d. Total keys added: %d.",
        summary.get("apps_processed", 0),
        summary.get("total_keys_added", 0),
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
