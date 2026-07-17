"""App-database access helper for the surviving Nova finance modules.

The former external accounting data-lake accessor and its optional
dedicated-cluster env overrides were removed in Phase 1 (accounting-integration
removal). The Cashflow Planner and finance-snapshot survivors only read the main
AEGISAXIOM app database, exposed here as ``get_app_db()``.
"""
from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase
import database


def get_app_db() -> AsyncIOMotorDatabase:
    """Return the AEGISAXIOM app database (finance_drafts, snapshots, etc.)."""
    return database.get_db()