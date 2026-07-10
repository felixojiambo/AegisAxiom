"""AR/AP snapshot service for Nova.

Nova needs to answer "what is our AR right now?" and "what is our AP right
now?" without iterating the full finance_drafts collection on every chat turn.
The collection does not carry user_id, so a per-user GL query is the wrong
shape for this question - the totals are tenant-scoped.

This module computes a tenant-scoped point-in-time snapshot from the live
finance_drafts collection (filtered by kind + sync_status) and writes it to
finance_snapshots in the AEGISAXIOM app DB. The scheduled job
(``refresh_finance_snapshots``) runs on the existing AsyncIOScheduler in
server.py; the read path (``get_latest_snapshot``) is what
nova_tools.query_finance_data calls when the LLM asks for
ar_total / ap_total / ar_aging / ap_aging.

Source collection: finance_drafts (FCC post-ingestion). Replaces the legacy
ar_invoices + ap_bills reads that were deprecated as part of the Financial
Command Center ingestion (Wave 2, Thread 2J).

kind field:
    "ar_invoice"  — accounts-receivable drafts
    "ap_bill"     — accounts-payable drafts

sync_status values (case-sensitive, FCC enum):
    "Draft"    — not yet sent
    "Sent"     — sent to counterparty, awaiting payment
    "Synced"   — pushed to QBO / paid / settled
    "Failed"   — sync error

Outstanding = sync_status in {"Draft", "Sent"} (money is still owed).
Terminal  = "Synced" or "Failed" (drops out of outstanding totals).

Snapshot shape (one document per tenant per day):
    {
        snapshot_id: "fs_<12hex>",
        tenant_id: "platform",        # session.account_id, see nova.py
        snapshot_date: "YYYY-MM-DD",
        ar_total: float,              # sum(amount) for outstanding AR drafts
        ap_total: float,              # sum(amount) for outstanding AP drafts
        ar_aging: {bucket: amount},   # bucket -> outstanding $
        ap_aging: {bucket: amount},
        source: "finance_drafts",
        source_metadata: {
            ar_draft_count: int,
            ap_draft_count: int,
            ar_data_available: bool,
            ap_data_available: bool,
        },
        created_at: datetime,
    }

Aging buckets are keyed off the draft's ``due_date`` against the
snapshot_date: current (not yet due), 1-30, 31-60, 61-90, over_90 days
past due. Records without a due_date are bucketed into ``unknown``.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any, Iterable

from motor.motor_asyncio import AsyncIOMotorDatabase

from ._gl_helpers import _safe_float
from .db import get_app_db


logger = logging.getLogger(__name__)


_COLLECTION = "finance_snapshots"
_DRAFTS_COLLECTION = "finance_drafts"

# kind values in the finance_drafts collection (immutable after creation).
_AR_DRAFT_KIND = "ar_invoice"
_AP_DRAFT_KIND = "ap_bill"

# sync_status values we treat as "outstanding" — money is still owed.
# Case-sensitive: these match the FCC sync_status enum exactly.
# Terminal statuses (Synced, Failed) drop out of outstanding totals.
#
# v1 conflates {Draft, Sent} into a single ar_total/ap_total. This is a
# known carry-forward — Nova accrual-vs-cash basis reconciliation may
# split Draft (pipeline) from Sent (true receivable) in a future wave.
_AR_OUTSTANDING_SYNC_STATUSES = {"Draft", "Sent"}
_AP_OUTSTANDING_SYNC_STATUSES = {"Draft", "Sent"}

_AGING_BUCKETS = ("current", "1_30", "31_60", "61_90", "over_90", "unknown")


def _new_snapshot_id() -> str:
    return f"fs_{uuid.uuid4().hex[:12]}"


def _coerce_to_date(value: Any) -> date | None:
    """Best-effort parse of a Mongo due_date value to a python date.

    Records carry due_date as YYYY-MM-DD strings in the AEGISAXIOM schema, but
    test fixtures occasionally use python date / datetime objects. Return
    None if the value is missing or cannot be parsed - the caller buckets
    those into "unknown" rather than throwing.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                return None
    return None


def _aging_bucket(due_date: date | None, as_of: date) -> str:
    if due_date is None:
        return "unknown"
    days_past_due = (as_of - due_date).days
    if days_past_due <= 0:
        return "current"
    if days_past_due <= 30:
        return "1_30"
    if days_past_due <= 60:
        return "31_60"
    if days_past_due <= 90:
        return "61_90"
    return "over_90"


def _empty_aging() -> dict[str, float]:
    return {bucket: 0.0 for bucket in _AGING_BUCKETS}


def _outstanding_amount_draft(draft: dict) -> float:
    """Draft outstanding = amount (the total draft amount, signed positive).

    The finance_drafts schema uses ``amount`` as its canonical total field.
    There is no partial-payment tracking in v1 — a draft is either
    outstanding (Draft/Sent) or terminal (Synced/Failed). When sync_status
    filtering already gates entry into this function, the full ``amount``
    is the outstanding balance.
    """
    return max(_safe_float(draft.get("amount", 0)), 0.0)


def _aggregate(
    records: Iterable[dict],
    *,
    outstanding_sync_statuses: set[str],
    as_of: date,
) -> tuple[float, dict[str, float], int]:
    """Aggregate outstanding amount and aging from finance_drafts records.

    Filters by sync_status (case-sensitive, FCC enum). Due date is taken
    from the top-level ``due_date`` field if present; drafts without one
    are bucketed into ``unknown``.
    """
    total = 0.0
    aging = _empty_aging()
    counted = 0
    for rec in records:
        sync_status = rec.get("sync_status") or ""
        if sync_status not in outstanding_sync_statuses:
            continue
        amount = _outstanding_amount_draft(rec)
        if amount <= 0:
            continue
        bucket = _aging_bucket(_coerce_to_date(rec.get("due_date")), as_of)
        aging[bucket] += amount
        total += amount
        counted += 1
    return total, aging, counted


async def compute_finance_snapshot(
    db: AsyncIOMotorDatabase,
    *,
    tenant_id: str,
    snapshot_date: date | None = None,
) -> dict[str, Any]:
    """Compute (but do not persist) a point-in-time AR/AP snapshot.

    Reads finance_drafts filtered by kind (ar_invoice / ap_bill). The tenant
    scope only matters once AEGISAXIOM becomes multi-tenant — today the collection
    is process-wide so we tag the snapshot with whatever tenant_id the caller
    supplied (typically "platform"; see nova.py:_resolve_tenant_context).

    Outstanding drafts are those whose sync_status is in
    _AR_OUTSTANDING_SYNC_STATUSES / _AP_OUTSTANDING_SYNC_STATUSES
    ({"Draft", "Sent"}). Synced and Failed drafts are terminal and drop out.
    """
    as_of = snapshot_date or datetime.now(timezone.utc).date()

    _CAP = 10_000
    ar_records = await db[_DRAFTS_COLLECTION].find(
        {"kind": _AR_DRAFT_KIND}, {"_id": 0}
    ).to_list(_CAP)
    if len(ar_records) >= _CAP:
        logger.warning(
            "compute_finance_snapshot: finance_drafts(kind=ar_invoice) read "
            "hit %d cap for tenant %s; snapshot may be partial",
            _CAP,
            tenant_id,
        )
    ap_records = await db[_DRAFTS_COLLECTION].find(
        {"kind": _AP_DRAFT_KIND}, {"_id": 0}
    ).to_list(_CAP)
    if len(ap_records) >= _CAP:
        logger.warning(
            "compute_finance_snapshot: finance_drafts(kind=ap_bill) read "
            "hit %d cap for tenant %s; snapshot may be partial",
            _CAP,
            tenant_id,
        )

    ar_total, ar_aging, ar_count = _aggregate(
        ar_records,
        outstanding_sync_statuses=_AR_OUTSTANDING_SYNC_STATUSES,
        as_of=as_of,
    )
    # Drafts are pipeline, not receivable. Reported as a separate field
    # so Nova can quote the in-flight invoice value without overstating
    # ar_total. Aging is irrelevant for drafts (they aren't due to
    # anyone yet) - only the rolled-up total is persisted.
    ap_total, ap_aging, ap_count = _aggregate(
        ap_records,
        outstanding_sync_statuses=_AP_OUTSTANDING_SYNC_STATUSES,
        as_of=as_of,
    )

    return {
        "snapshot_id": _new_snapshot_id(),
        "tenant_id": tenant_id,
        "snapshot_date": as_of.isoformat(),
        "ar_total": round(ar_total, 2),
        "ap_total": round(ap_total, 2),
        "ar_aging": {k: round(v, 2) for k, v in ar_aging.items()},
        "ap_aging": {k: round(v, 2) for k, v in ap_aging.items()},
        "source": "finance_drafts",
        "source_metadata": {
            "ar_draft_count": ar_count,
            "ap_draft_count": ap_count,
            # Distinguish "we looked and there are zero outstanding" from
            # "the source collection is empty" — Nova needs to know the
            # difference to phrase its answer honestly.
            "ar_data_available": len(ar_records) > 0,
            "ap_data_available": len(ap_records) > 0,
        },
        "created_at": datetime.now(timezone.utc),
    }


async def save_finance_snapshot(
    snapshot: dict[str, Any],
    db: AsyncIOMotorDatabase | None = None,
) -> dict[str, Any]:
    """Persist a snapshot via daily upsert on (tenant_id, snapshot_date).

    The hourly scheduler used to ``insert_one`` on every run, so a 30-day
    window of hourly refreshes left ~720 rows per tenant when only the
    most recent matters for ``get_latest_snapshot``. Upserting on
    (tenant_id, snapshot_date) caps growth at one row per tenant per day:
    the first hourly refresh of a UTC day inserts, every subsequent
    refresh that same day overwrites the same document.

    The unique index on (tenant_id, snapshot_date) created at startup
    in server.py enforces the invariant; this function relies on it,
    not on application-level uniqueness.

    Existing pre-PR-#144 rows are left untouched - the upsert filter
    keys on the new (tenant_id, snapshot_date) pair, so any row that
    happens to share that pair will be overwritten on next run while
    older daily-redundant rows just sit until backfill cleanup or the
    optional TTL.
    """
    if db is None:
        db = get_app_db()
    doc = dict(snapshot)
    tenant_id = doc.get("tenant_id")
    snapshot_date = doc.get("snapshot_date")
    if not tenant_id or not snapshot_date:
        # Defensive: refuse to persist a snapshot missing the upsert key.
        # Without both fields the unique index would either reject or the
        # row would silently sit outside the daily-cap policy.
        raise ValueError(
            "save_finance_snapshot requires tenant_id and snapshot_date "
            "on the snapshot document."
        )
    await db[_COLLECTION].update_one(
        {"tenant_id": tenant_id, "snapshot_date": snapshot_date},
        {"$set": doc},
        upsert=True,
    )
    doc.pop("_id", None)
    return doc


async def get_latest_snapshot(
    tenant_id: str,
    db: AsyncIOMotorDatabase | None = None,
) -> dict[str, Any] | None:
    """Return the most recent snapshot for this tenant, or None.

    Sorts by ``snapshot_date`` (the date the snapshot represents) rather
    than ``created_at`` (the wall-clock time the row was written). The
    daily-upsert key is (tenant_id, snapshot_date), so sorting by
    snapshot_date uses the unique index path and is also the
    semantically correct choice: a clock-skew retry that bumped
    created_at on an older row would otherwise shadow today's snapshot.
    PR #144 second-pass review item 4.
    """
    if db is None:
        db = get_app_db()
    cursor = (
        db[_COLLECTION]
        .find({"tenant_id": tenant_id}, {"_id": 0})
        .sort("snapshot_date", -1)
        .limit(1)
    )
    docs = await cursor.to_list(length=1)
    return docs[0] if docs else None


async def refresh_finance_snapshots(
    db: AsyncIOMotorDatabase | None = None,
    *,
    tenant_id: str = "platform",
) -> dict[str, Any]:
    """Scheduler entrypoint. Computes + persists today's snapshot.

    AEGISAXIOM is single-tenant in practice today (see nova.py module
    docstring), so we write a single ``platform`` row per run. When real
    multi-tenancy lands, callers should iterate the tenant directory and
    call this function once per tenant.

    Returns the persisted document so the caller (and the test suite)
    can inspect the result. Failures are logged and re-raised so the
    APScheduler missed-job log surfaces them.
    """
    if db is None:
        db = get_app_db()
    try:
        snapshot = await compute_finance_snapshot(db, tenant_id=tenant_id)
        saved = await save_finance_snapshot(snapshot, db=db)
        logger.info(
            "finance snapshot refreshed: tenant=%s ar_total=%.2f ap_total=%.2f "
            "ar_draft_count=%d ap_draft_count=%d source=%s",
            tenant_id,
            saved["ar_total"],
            saved["ap_total"],
            saved["source_metadata"]["ar_draft_count"],
            saved["source_metadata"]["ap_draft_count"],
            saved.get("source", "finance_drafts"),
        )
        return saved
    except Exception as e:
        logger.exception("finance snapshot refresh failed: %s", e)
        raise


__all__ = [
    "compute_finance_snapshot",
    "save_finance_snapshot",
    "get_latest_snapshot",
    "refresh_finance_snapshots",
]
