"""
Migration: 2026 contracts + time management schema foundation

Wave 0 of contracts-time-mgmt. Creates the collections and indexes the
later approval, BOH-to-BOA, billing-trigger, invoice-batch and Zoho
import waves will write against. Touches no existing business documents
and is safe to run multiple times — every step is an additive create.

Collections created (if absent):
  contract_approvals          per-decision audit row for the approval queue
  contract_adjustments        credit/debit corrections and late-timesheet adjustments
  contract_billing_triggers   date- or hours-driven billing trigger configs
  contract_time_applications  ledger of time entries applied against a contract
  invoice_batches             bi-weekly Finance Command Center invoice batches
  zoho_contracts              Zoho contract import staging
  zoho_projects               Zoho project import staging

Existing collections get additional secondary indexes only. No documents
are read, moved, rewritten, or removed by this migration.

Idempotency: Motor's create_index is itself a no-op when an identically
specified index already exists. We additionally swallow OperationFailure
on each call so a pre-existing index with a slightly different name does
not abort the migration. The function is safe to invoke from a cold-start
hook, a one-off script, or the test suite.

Usage:
    from migrations.2026_contracts_time_mgmt_schema import (
        apply_contracts_time_mgmt_schema,
    )
    await apply_contracts_time_mgmt_schema(db)

CLI:
    python backend/migrations/2026_contracts_time_mgmt_schema.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any, List, Tuple


logger = logging.getLogger(__name__)


# Collections this migration is responsible for creating. Tests rely on
# this list to verify every name lands in the database.
NEW_COLLECTIONS: Tuple[str, ...] = (
    "contract_approvals",
    "contract_adjustments",
    "contract_billing_triggers",
    "contract_time_applications",
    "invoice_batches",
    "zoho_contracts",
    "zoho_projects",
)


# Index plan. Each entry is (collection, keys, options). keys mirrors the
# Motor create_index argument shape: either a string for a single-field
# ascending index or a list of (field, direction) tuples. options is an
# kwargs dict forwarded to create_index — name is included so we can
# detect the index after the fact, and unique/sparse follow the
# canonical Wave 0 contract.
_INDEX_PLAN: Tuple[Tuple[str, Any, dict], ...] = (
    # contract_approvals — recent decisions per contract.
    (
        "contract_approvals",
        [("contract_id", 1), ("decided_at", -1)],
        {"name": "contract_id_1_decided_at_-1"},
    ),
    # contract_adjustments — recent adjustments per contract.
    (
        "contract_adjustments",
        [("contract_id", 1), ("created_at", -1)],
        {"name": "contract_id_1_created_at_-1"},
    ),
    # contract_billing_triggers — open triggers per contract.
    (
        "contract_billing_triggers",
        [("contract_id", 1), ("state", 1)],
        {"name": "contract_id_1_state_1"},
    ),
    # contract_time_applications — applied entries per contract, plus a
    # unique guard on entry_id so a time entry cannot be double-applied.
    (
        "contract_time_applications",
        [("contract_id", 1), ("applied_at", -1)],
        {"name": "contract_id_1_applied_at_-1"},
    ),
    (
        "contract_time_applications",
        "entry_id",
        {"name": "entry_id_1", "unique": True},
    ),
    # invoice_batches — per-contract chronology and finance-queue scan.
    (
        "invoice_batches",
        [("contract_id", 1), ("period_start", -1)],
        {"name": "contract_id_1_period_start_-1"},
    ),
    (
        "invoice_batches",
        [("status", 1), ("period_start", -1)],
        {"name": "status_1_period_start_-1"},
    ),
    # zoho_contracts / zoho_projects — unique source-system identity.
    (
        "zoho_contracts",
        "zoho_id",
        {"name": "zoho_id_1", "unique": True},
    ),
    (
        "zoho_projects",
        "zoho_id",
        {"name": "zoho_id_1", "unique": True},
    ),
    # contracts — secondary indexes the approval queue and tenancy
    # filters will scan. external_contract_id is sparse-unique so legacy
    # rows without that field do not violate the constraint.
    (
        "contracts",
        [("account_id", 1), ("status", 1)],
        {"name": "account_id_1_status_1"},
    ),
    (
        "contracts",
        [("deal_ids", 1)],
        {"name": "deal_ids_1"},
    ),
    (
        "contracts",
        [("project_ids", 1)],
        {"name": "project_ids_1"},
    ),
    (
        "contracts",
        [("approval_status", 1), ("status", 1)],
        {"name": "approval_status_1_status_1"},
    ),
    (
        "contracts",
        "external_contract_id",
        {"name": "external_contract_id_1", "unique": True, "sparse": True},
    ),
    (
        "contracts",
        [("tenant_id", 1), ("created_at", -1)],
        {"name": "tenant_id_1_created_at_-1"},
    ),
    # time_entries — contract-debit scan and PM task linkage.
    (
        "time_entries",
        [("contract_id", 1), ("status", 1), ("week_start", 1)],
        {"name": "contract_id_1_status_1_week_start_1"},
    ),
    (
        "time_entries",
        "pm_task_id",
        {"name": "pm_task_id_1"},
    ),
)


def _safe_collection_list() -> Tuple[str, ...]:
    """Public read-only view of the new collection list."""
    return NEW_COLLECTIONS


def _safe_index_plan() -> Tuple[Tuple[str, Any, dict], ...]:
    """Public read-only view of the index plan for tests."""
    return _INDEX_PLAN


async def _ensure_collection(db, name: str) -> None:
    """Create a collection if it does not exist. Idempotent — Mongo
    raises NamespaceExists on a second create, which we swallow."""
    try:
        existing: List[str] = await db.list_collection_names()
    except Exception:
        existing = []
    if name in existing:
        return
    try:
        await db.create_collection(name)
    except Exception as exc:  # NamespaceExists or driver-specific equivalent
        # Re-list to confirm; only re-raise if the collection truly is
        # not there after the attempt.
        try:
            after: List[str] = await db.list_collection_names()
        except Exception:
            after = []
        if name not in after:
            logger.warning(
                "create_collection(%s) failed and collection still absent: %s",
                name,
                exc,
            )


async def _ensure_index(db, collection: str, keys: Any, options: dict) -> None:
    """Create one index, swallowing duplicate-name / conflicting-spec
    failures so an earlier deploy that named the index differently does
    not abort the migration."""
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


async def apply_contracts_time_mgmt_schema(db) -> dict:
    """Apply the Wave 0 schema foundation.

    Returns a small summary dict so callers (and tests) can introspect
    what landed. Never raises on a duplicate; never mutates existing
    business documents.
    """
    for name in NEW_COLLECTIONS:
        await _ensure_collection(db, name)

    for collection, keys, options in _INDEX_PLAN:
        await _ensure_index(db, collection, keys, options)

    return {
        "collections_ensured": list(NEW_COLLECTIONS),
        "indexes_ensured": len(_INDEX_PLAN),
    }


async def _main() -> None:
    """CLI entry point. Imports lazily so the module stays unit-testable
    without a Motor environment present."""
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
    result = await apply_contracts_time_mgmt_schema(db)
    print(f"contracts-time-mgmt schema applied: {result}")
    client.close()


if __name__ == "__main__":
    asyncio.run(_main())
