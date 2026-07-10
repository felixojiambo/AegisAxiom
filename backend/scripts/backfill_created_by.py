"""Backfill ``created_by`` on legacy records so ownership-tightened
verify_ownership() does not lock pre-existing users out of their own data.

Background:
    PR #116 removed the ``account_id`` fallback from verify_ownership for
    mutable operations. Records created BEFORE that PR don't carry a
    ``created_by`` field, so a user editing their own old contract /
    project / account would now hit a 403.

    For legacy records the closest equivalent of "who owns this" lives in
    fields like ``assigned_sales_rep_id`` / ``deal_owner_id`` /
    ``assigned_agent_id``. Stamping those values into ``created_by`` (when
    not already present) restores the user's ability to manage records they
    were the assigned owner of, without re-introducing the cross-user
    account_id fallback.

Run once after PR #116 deploys (or before, on staging, to verify):
    docker exec -it aegisaxiom-backend python -m scripts.backfill_created_by

Idempotent: only stamps ``created_by`` where it is missing/null/empty AND
a per-collection ownership field is set. Re-running is a no-op.
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("backfill_created_by")


# (collection, [ordered list of fallback fields to copy from])
# Order matters — the first present value wins. Ordering is "primary owner"
# first so a deal with both deal_owner_id and assigned_sales_rep_id falls
# back to the deal owner.
TARGETS: list[tuple[str, list[str]]] = [
    ("contracts", ["assigned_sales_rep_id", "assigned_agent_id"]),
    ("projects", ["assigned_sales_rep_id", "assigned_agent_id"]),
    ("accounts", ["assigned_sales_rep_id", "assigned_agent_id"]),
    ("field_jobs", ["assigned_sales_rep_id", "assigned_tech_id", "assigned_agent_id"]),
    ("deals", ["deal_owner_id", "assigned_sales_rep_id", "assigned_agent_id"]),
    ("opportunities", ["assigned_sales_rep_id", "assigned_agent_id"]),
]


async def _backfill_one(db, collection: str, source_fields: list[str]) -> tuple[int, int]:
    """Backfill created_by for a single collection. Returns (stamped, skipped)."""
    coll = db[collection]
    needs_backfill = {
        "$or": [
            {"created_by": {"$exists": False}},
            {"created_by": None},
            {"created_by": ""},
        ]
    }
    stamped = 0
    skipped = 0
    cursor = coll.find(needs_backfill, {field: 1 for field in source_fields}, no_cursor_timeout=True).batch_size(500)
    try:
        async for doc in cursor:
            owner = next((doc[f] for f in source_fields if doc.get(f)), None)
            if not owner:
                skipped += 1
                continue
            result = await coll.update_one(
                {
                    "_id": doc["_id"],
                    "$or": [
                        {"created_by": {"$exists": False}},
                        {"created_by": None},
                        {"created_by": ""},
                    ],
                },
                {"$set": {"created_by": owner}},
            )
            if result.modified_count == 1:
                stamped += 1
            else:
                skipped += 1
    finally:
        await cursor.close()
    return stamped, skipped


async def backfill() -> None:
    database.init(os.environ["MONGO_URL"], os.environ["DB_NAME"])
    db = database.get_db()

    grand_stamped = 0
    grand_skipped = 0
    for collection, source_fields in TARGETS:
        stamped, skipped = await _backfill_one(db, collection, source_fields)
        logger.info("%s: stamped=%d skipped=%d", collection, stamped, skipped)
        grand_stamped += stamped
        grand_skipped += skipped

    logger.info("Backfill complete. Total stamped=%d skipped=%d", grand_stamped, grand_skipped)


if __name__ == "__main__":
    asyncio.run(backfill())
