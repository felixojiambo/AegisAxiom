"""Indexes for per-contract approver overrides + scheduler telemetry.

Adds two new collections introduced by the contracts-time-mgmt
"production-operable" wave:

  contract_approver_overrides    one user × one contract approval grant
  invoice_batch_scheduler_runs   external-scheduler run telemetry

The collection + index plan is intentionally additive — running
this migration on a fresh database is identical to running it on
a populated database (Motor's ``create_index`` is a no-op when an
identically-named index already exists).

Index choices
-------------

contract_approver_overrides

  (tenant_id, contract_id, state)         per-contract management UI scan
  (tenant_id, user_id, state)             per-user approval-queue scan
  (override_id)                            unique handle (route-level lookups)
  (tenant_id, contract_id, user_id, state="active")
                                          partial unique — at most one
                                          active row per (tenant, contract,
                                          user). The service layer also
                                          enforces this in code, the
                                          partial unique is defense in
                                          depth on deploys that honor it.

invoice_batch_scheduler_runs

  (source, started_at desc)                latest run feed for the status
                                          card
  (status, started_at desc)                "show me the last failure" /
                                          "show me the last started"
  (source, status, finished_at desc)       "show me the last succeeded"
                                          (the most common status-card
                                          query)
"""
from __future__ import annotations

import logging
from typing import Any, Tuple


logger = logging.getLogger(__name__)


NEW_COLLECTIONS: Tuple[str, ...] = (
    "contract_approver_overrides",
    "invoice_batch_scheduler_runs",
)


# (collection, keys, options)
_INDEX_PLAN: Tuple[Tuple[str, Any, dict], ...] = (
    # ── contract_approver_overrides ───────────────────────────────
    (
        "contract_approver_overrides",
        [("tenant_id", 1), ("contract_id", 1), ("state", 1)],
        {"name": "tenant_id_1_contract_id_1_state_1"},
    ),
    (
        "contract_approver_overrides",
        [("tenant_id", 1), ("user_id", 1), ("state", 1)],
        {"name": "tenant_id_1_user_id_1_state_1"},
    ),
    (
        "contract_approver_overrides",
        "override_id",
        {"name": "override_id_1", "unique": True},
    ),
    (
        # Partial unique on the active row only. Mongo honors this on
        # 3.2+; older deploys will silently fall back to non-unique.
        # The service layer enforces uniqueness in code regardless.
        "contract_approver_overrides",
        [("tenant_id", 1), ("contract_id", 1), ("user_id", 1)],
        {
            "name": "tenant_contract_user_active_unique",
            "unique": True,
            "partialFilterExpression": {"state": "active"},
        },
    ),
    # ── invoice_batch_scheduler_runs ──────────────────────────────
    (
        "invoice_batch_scheduler_runs",
        [("source", 1), ("started_at", -1)],
        {"name": "source_1_started_at_-1"},
    ),
    (
        "invoice_batch_scheduler_runs",
        [("status", 1), ("started_at", -1)],
        {"name": "status_1_started_at_-1"},
    ),
    (
        "invoice_batch_scheduler_runs",
        [("source", 1), ("status", 1), ("finished_at", -1)],
        {"name": "source_1_status_1_finished_at_-1"},
    ),
    (
        "invoice_batch_scheduler_runs",
        "run_id",
        {"name": "run_id_1", "unique": True},
    ),
)


async def _ensure_collection(db, name: str) -> None:
    try:
        existing = await db.list_collection_names()
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_collection_names failed: %s", exc)
        existing = []
    if name in existing:
        return
    try:
        await db.create_collection(name)
    except Exception as exc:
        try:
            after = await db.list_collection_names()
        except Exception:
            after = []
        if name not in after:
            logger.warning(
                "create_collection(%s) failed and collection still absent: %s",
                name,
                exc,
            )


async def _ensure_index(db, collection: str, keys: Any, options: dict) -> None:
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


async def apply_per_contract_overrides_indexes(db) -> dict:
    """Apply the per-contract-overrides + scheduler-telemetry
    indexes. Idempotent — running on a populated database is safe.
    Returns a small summary dict for callers + tests."""
    for name in NEW_COLLECTIONS:
        await _ensure_collection(db, name)

    for collection, keys, options in _INDEX_PLAN:
        await _ensure_index(db, collection, keys, options)

    return {
        "collections_ensured": list(NEW_COLLECTIONS),
        "indexes_ensured": len(_INDEX_PLAN),
    }


async def _main() -> None:
    """CLI entry point. Lazy imports keep the module unit-testable
    without a Motor environment."""
    from dotenv import load_dotenv  # type: ignore
    from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore
    import os

    load_dotenv()
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME") or "aegisaxiom_crm"
    if not mongo_url:
        raise SystemExit("MONGO_URL is required.")
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    result = await apply_per_contract_overrides_indexes(db)
    print(
        "per-contract overrides + scheduler telemetry indexes applied: "
        + repr(result)
    )


if __name__ == "__main__":
    import asyncio
    asyncio.run(_main())
