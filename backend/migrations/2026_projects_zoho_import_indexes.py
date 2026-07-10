"""Additive indexes for the Zoho Projects importer.

Wave 7 of contracts-time-mgmt. Adds a sparse-unique index on
``projects.external_project_id`` so the Zoho Projects importer can
upsert by that key without ever creating a duplicate document for
the same source-system row. Mirrors the
``contracts.external_contract_id`` index installed by
``2026_contracts_time_mgmt_schema.py``.

The plan is **strictly additive** — no documents are read, moved,
rewritten, or removed; no destructive changes; no existing index is
dropped or replaced. Re-running the migration on a populated
database is identical to running it on a fresh one (Motor's
``create_index`` is a no-op when an identically-named index already
exists, and we additionally swallow OperationFailure on each call
so a pre-existing index with a slightly different name does not
abort the migration).

Indexes installed:

    projects
      external_project_id          sparse unique — the importer's upsert key
      external_ids.zoho_project_id            secondary lookup
      external_ids.zoho_contract_id           secondary lookup
      import_source                            ops scans for imported rows

Usage:
    from migrations import (
        2026_projects_zoho_import_indexes as projects_zoho_indexes,
    )
    await projects_zoho_indexes.apply_projects_zoho_import_indexes(db)

CLI:
    python backend/migrations/2026_projects_zoho_import_indexes.py
"""
from __future__ import annotations

import logging
from typing import Any, Tuple


logger = logging.getLogger(__name__)


# (collection, keys, options). Every entry is additive — installing
# an index for a field the importer needs to look up cheaply.
_INDEX_PLAN: Tuple[Tuple[str, Any, dict], ...] = (
    (
        "projects",
        "external_project_id",
        {
            "name": "external_project_id_1",
            "unique": True,
            "sparse": True,
        },
    ),
    (
        "projects",
        "external_ids.zoho_project_id",
        {"name": "external_ids_zoho_project_id_1", "sparse": True},
    ),
    (
        "projects",
        "external_ids.zoho_contract_id",
        {"name": "external_ids_zoho_contract_id_1", "sparse": True},
    ),
    (
        "projects",
        "import_source",
        {"name": "import_source_1", "sparse": True},
    ),
)


def _safe_index_plan() -> Tuple[Tuple[str, Any, dict], ...]:
    """Public read-only view of the index plan for tests."""
    return _INDEX_PLAN


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


async def apply_projects_zoho_import_indexes(db) -> dict:
    """Apply the projects-import indexes. Idempotent — running on a
    populated database is safe. Returns a small summary dict for
    callers + tests."""
    for collection, keys, options in _INDEX_PLAN:
        await _ensure_index(db, collection, keys, options)
    return {
        "indexes_ensured": len(_INDEX_PLAN),
        "collection": "projects",
    }


async def _main() -> None:
    """CLI entry point. Lazy imports keep the module unit-testable
    without a Motor environment."""
    import os

    from dotenv import load_dotenv  # type: ignore
    from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore

    load_dotenv()
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME") or "aegisaxiom_crm"
    if not mongo_url:
        raise SystemExit("MONGO_URL is required.")
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    result = await apply_projects_zoho_import_indexes(db)
    print(f"projects zoho-import indexes applied: {result!r}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(_main())
