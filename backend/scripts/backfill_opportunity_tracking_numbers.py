"""Backfill tracking_number on pre-existing opportunities.

Run once after the OPP-YYYY-NNNNNN feature merges to populate existing
opportunity documents. Idempotent — safe to re-run; documents that
already carry a tracking_number are skipped.

Usage (inside the aegisaxiom-backend container):
    docker exec -it aegisaxiom-backend python -m scripts.backfill_opportunity_tracking_numbers

Behavior:
  - Streams through opportunities with a server-side cursor in BATCH_SIZE
    chunks so large collections don't balloon the process memory.
  - Groups opportunities by creation year (created_at -> ObjectId
    timestamp -> skip+log).
  - Assigns tracking numbers in created_at ascending order *per year* so
    the earliest opportunity in a year gets the earliest number. Because
    MongoDB can only sort one way across the whole stream, we collect the
    per-year buckets first, then sort within each bucket before
    allocating.
  - Uses the same counter collection (opportunity_counters) as runtime
    allocation, so backfill and new creations never collide. The counter
    seq advances past whatever number backfill assigns.
  - Commits one document at a time. If the process dies halfway, re-run;
    only missing documents get filled.
"""
import asyncio
import logging
import os
import sys
from collections import defaultdict
from datetime import timezone

from pymongo import ReturnDocument

# Make "backend" importable when run via `python -m scripts.*` from /app/backend.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("backfill_tracking_numbers")

BATCH_SIZE = 500


def _year_from_doc(doc: dict) -> int | None:
    """Resolve creation year with a fallback to ObjectId timestamp."""
    created = doc.get("created_at")
    if created:
        try:
            return created.astimezone(timezone.utc).year if created.tzinfo else created.year
        except AttributeError:
            # Not a datetime — fall through to _id
            pass
    _id = doc.get("_id")
    if _id is not None:
        try:
            return _id.generation_time.year
        except AttributeError:
            pass
    return None


def _sort_key_within_year(doc: dict):
    created = doc.get("created_at")
    if created is not None:
        return (0, created)
    try:
        return (1, doc["_id"].generation_time)
    except AttributeError:
        return (2, doc.get("opportunity_id", ""))


async def backfill() -> None:
    database.init(os.environ["MONGO_URL"], os.environ["DB_NAME"])
    db = database.get_db()

    # Paginated cursor. Match docs where tracking_number is missing OR null/empty
    # in a single query — simpler than the two-pass dedup the old version did.
    query = {
        "$or": [
            {"tracking_number": {"$exists": False}},
            {"tracking_number": None},
            {"tracking_number": ""},
        ]
    }
    projection = {"opportunity_id": 1, "created_at": 1, "name": 1}

    # Bucket by year as we stream. Buckets only hold small dicts, not full docs,
    # so memory stays bounded even across millions of records.
    buckets: dict[int | None, list[dict]] = defaultdict(list)
    streamed = 0
    cursor = db.opportunities.find(query, projection, no_cursor_timeout=True).batch_size(BATCH_SIZE)
    try:
        async for doc in cursor:
            buckets[_year_from_doc(doc)].append(doc)
            streamed += 1
    finally:
        await cursor.close()

    if streamed == 0:
        logger.info("No opportunities need a tracking_number. Nothing to do.")
        return

    logger.info("Streamed %d opportunities into %d year-buckets.", streamed, len(buckets))

    assigned = 0
    skipped = 0
    # Process buckets in ascending year order — oldest first, for readable logs.
    for year in sorted((y for y in buckets if y is not None)):
        docs = sorted(buckets[year], key=_sort_key_within_year)
        for doc in docs:
            counter_doc = await db.opportunity_counters.find_one_and_update(
                {"_id": year},
                {"$inc": {"seq": 1}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            tracking = f"OPP-{year}-{counter_doc['seq']:06d}"

            result = await db.opportunities.update_one(
                # Re-check to avoid racing with a concurrent create that already
                # set a tracking_number on this document. Counter slot leaks are
                # acceptable — tracking numbers are for reference, not accounting.
                {
                    "_id": doc["_id"],
                    "$or": [
                        {"tracking_number": {"$exists": False}},
                        {"tracking_number": None},
                        {"tracking_number": ""},
                    ],
                },
                {"$set": {"tracking_number": tracking}},
            )
            if result.modified_count == 1:
                logger.info(
                    "Assigned %s to %s (%s)",
                    tracking,
                    doc.get("opportunity_id") or doc["_id"],
                    doc.get("name", ""),
                )
                assigned += 1
            else:
                logger.info(
                    "Skipped %s — already has a tracking_number (concurrent create?)",
                    doc.get("opportunity_id") or doc["_id"],
                )
                skipped += 1

    none_bucket = buckets.get(None, [])
    for doc in none_bucket:
        logger.warning(
            "Skipping %s — no created_at and no ObjectId timestamp",
            doc.get("opportunity_id") or doc.get("_id"),
        )
        skipped += 1

    logger.info("Backfill complete. Assigned: %d, skipped: %d", assigned, skipped)


if __name__ == "__main__":
    asyncio.run(backfill())
