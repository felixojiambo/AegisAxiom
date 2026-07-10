"""Two-database access helper for Nova.

In production, QBO collections (general_ledger, reports_*, etc.) live in the
same database as the rest of AEGISAXIOM — no separate connection or env var is
needed. `get_qbo_db()` returns the same DB as `get_app_db()` by default.

Optional overrides (used for testing or future multi-cluster setups):
  - QBO_MONGO_URL: separate cluster for QBO reads (writes still go to MONGO_URL)
  - QBO_DB: separate database name on the same or override cluster

Unset both in production.
"""
from __future__ import annotations

import os

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import database


_qbo_client: AsyncIOMotorClient | None = None


def get_qbo_db() -> AsyncIOMotorDatabase:
    """Return the QBO data-lake database.

    Default: same DB as the rest of AEGISAXIOM (database.get_db()). QBO collections
    are co-located with AEGISAXIOM app collections in production — no override needed.

    Overrides (env vars, both optional):
      - QBO_MONGO_URL: dedicated cluster for QBO reads (cached client)
      - QBO_DB: dedicated DB name (resolved against the QBO cluster if
        QBO_MONGO_URL is set, otherwise against the global client)

    Read at call time so tests can monkeypatch.
    """
    qbo_url = os.environ.get("QBO_MONGO_URL")
    qbo_db_name = os.environ.get("QBO_DB")

    if qbo_url:
        global _qbo_client
        if _qbo_client is None:
            _qbo_client = AsyncIOMotorClient(qbo_url)
        client: AsyncIOMotorClient = _qbo_client
        return client[qbo_db_name] if qbo_db_name else client.get_default_database()

    if qbo_db_name:
        return database.get_client()[qbo_db_name]

    return database.get_db()


def get_app_db() -> AsyncIOMotorDatabase:
    """Return the AEGISAXIOM app database (persistence target for projection_runs).

    Identical to database.get_db(), exposed here for symmetry so callers
    in the Nova stack have a single import surface.
    """
    return database.get_db()


def get_both_dbs() -> tuple[AsyncIOMotorDatabase, AsyncIOMotorDatabase]:
    """Return (qbo_db, app_db) tuple for callers that need both."""
    return get_qbo_db(), get_app_db()