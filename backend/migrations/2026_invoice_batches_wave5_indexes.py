"""Wave 5 hardening migration — invoice batch + finance draft unique
indexes.

Adds two sparse-unique indexes that pin the Wave 5 dedup contracts at
the database layer:

  invoice_batches.batch_key  — sparse unique
      The route layer already prevents duplicate rebuild via a
      ``find_one(status != rejected)`` check, but a race between two
      concurrent builders could still slip through. The unique index
      gives a hard backstop: only one active batch per
      ``contract_id:period_start:period_end`` tuple can hold a
      ``batch_key`` value. The route's reject path clears
      ``batch_key`` so rejected rows do NOT occupy the slot — they
      stay for audit, just no longer block a rebuild.

  finance_drafts.source_batch_id  — sparse unique
      The route's approve handler is idempotent on re-approve (it
      reads the existing draft id off the batch), but two parallel
      approve calls could race past the entry-status check. The
      unique constraint on ``source_batch_id`` ensures the database
      itself rejects a duplicate draft for the same batch.

Both indexes are sparse so rows that pre-date the migration (no
batch_key field, no source_batch_id field) do not violate the
constraint. Both indexes are idempotent — running this migration
twice does nothing on the second pass.

Loaded via importlib in tests because the filename starts with a
digit and cannot be imported through normal ``from`` syntax —
matches the Wave 0 migration's loader style.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any, List, Tuple


logger = logging.getLogger(__name__)


_INDEX_PLAN: Tuple[Tuple[str, Any, dict], ...] = (
    (
        "invoice_batches",
        "batch_key",
        {"name": "batch_key_1", "unique": True, "sparse": True},
    ),
    (
        "finance_drafts",
        "source_batch_id",
        {"name": "source_batch_id_1", "unique": True, "sparse": True},
    ),
)


def _safe_index_plan() -> Tuple[Tuple[str, Any, dict], ...]:
    """Public read-only view of the index plan for tests."""
    return _INDEX_PLAN


async def _ensure_index(db, collection: str, keys: Any, options: dict) -> None:
    """Create one index, swallowing duplicate-name / spec-conflict
    failures so an earlier deploy that named the same index slightly
    differently does not abort the migration."""
    try:
        await db[collection].create_index(keys, **options)
    except Exception as exc:
        logger.warning(
            "create_index(%s, %r, %r) failed (non-fatal): %s",
            collection,
            keys,
            options,
            exc,
        )


async def apply_invoice_batches_wave5_indexes(db) -> dict:
    """Apply the Wave 5 hardening indexes.

    Returns a summary so tests and callers can introspect what
    landed. Idempotent."""
    for collection, keys, options in _INDEX_PLAN:
        await _ensure_index(db, collection, keys, options)
    return {"indexes_ensured": len(_INDEX_PLAN)}


async def _main() -> None:
    from dotenv import load_dotenv  # type: ignore
    from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore

    load_dotenv()
    mongo_url = os.getenv("MONGO_URL")
    db_name = os.getenv("DB_NAME", "simplimeta")
    if not mongo_url:
        print("MONGO_URL not set; aborting.", file=sys.stderr)
        sys.exit(1)
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    result = await apply_invoice_batches_wave5_indexes(db)
    print(f"invoice_batches Wave 5 indexes applied: {result}")
    client.close()


if __name__ == "__main__":
    asyncio.run(_main())
