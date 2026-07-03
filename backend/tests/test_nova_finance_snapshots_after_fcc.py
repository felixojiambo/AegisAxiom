"""Tests for finance_snapshots.py after FCC ingestion (Wave 2J).

Verifies the new data source: finance_drafts filtered by kind + sync_status,
replacing the deprecated ar_invoices / ap_bills reads.

Covers:
  - Empty collection → snapshot with zeros and data_available=False
  - kind filtering (ar_invoice vs ap_bill reads separate slices)
  - sync_status filtering (Draft+Sent = outstanding; Synced+Failed = terminal)
  - Mixed current/overdue aging buckets
  - Snapshot doc shape exactly matches the new contract
  - refresh_finance_snapshots end-to-end (compute + save)
"""
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from services.nova_engine.finance_snapshots import (
    _AR_DRAFT_KIND,
    _AP_DRAFT_KIND,
    _AR_OUTSTANDING_SYNC_STATUSES,
    _AP_OUTSTANDING_SYNC_STATUSES,
    compute_finance_snapshot,
    get_latest_snapshot,
    refresh_finance_snapshots,
    save_finance_snapshot,
)


# ---------------------------------------------------------------------------
# DB mock helpers
# ---------------------------------------------------------------------------


def _flat_cursor(rows):
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=rows)
    return cursor


def _ordered_cursor(rows):
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=rows)
    return cursor


def _make_db(*, ar_drafts=None, ap_drafts=None, latest_snapshot=None):
    """Build a MagicMock db backed by finance_drafts.

    finance_drafts.find() is called twice — once with kind=ar_invoice,
    once with kind=ap_bill. We match by the `kind` field in the query arg.
    """
    ar_drafts = ar_drafts or []
    ap_drafts = ap_drafts or []

    drafts_collection = MagicMock()

    def _drafts_find(query, *args, **kwargs):
        kind = query.get("kind")
        if kind == _AR_DRAFT_KIND:
            return _flat_cursor(ar_drafts)
        if kind == _AP_DRAFT_KIND:
            return _flat_cursor(ap_drafts)
        return _flat_cursor([])

    drafts_collection.find = MagicMock(side_effect=_drafts_find)

    snapshot_rows = [latest_snapshot] if latest_snapshot is not None else []
    snapshots_collection = MagicMock()
    snapshots_collection.find = MagicMock(return_value=_ordered_cursor(snapshot_rows))
    snapshots_collection.update_one = AsyncMock(return_value=None)

    db = MagicMock()
    db.finance_drafts = drafts_collection

    def _getitem(name):
        if name == "finance_snapshots":
            return snapshots_collection
        if name == "finance_drafts":
            return drafts_collection
        return MagicMock()

    db.__getitem__ = MagicMock(side_effect=_getitem)
    db.finance_snapshots = snapshots_collection
    return db, snapshots_collection


# ---------------------------------------------------------------------------
# compute_finance_snapshot — empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_snapshot_empty_collections():
    """Empty finance_drafts → zeros with data_available=False for both sides."""
    db, _ = _make_db()
    snapshot = await compute_finance_snapshot(db, tenant_id="platform")

    assert snapshot["ar_total"] == 0.0
    assert snapshot["ap_total"] == 0.0
    assert snapshot["source"] == "finance_drafts"
    assert snapshot["source_metadata"]["ar_data_available"] is False
    assert snapshot["source_metadata"]["ap_data_available"] is False
    assert snapshot["source_metadata"]["ar_draft_count"] == 0
    assert snapshot["source_metadata"]["ap_draft_count"] == 0


# ---------------------------------------------------------------------------
# compute_finance_snapshot — kind filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_snapshot_kind_filtering():
    """AR drafts only count for AR total; AP drafts only for AP total."""
    ar_drafts = [
        {"kind": "ar_invoice", "amount": 1000, "sync_status": "Sent"},
    ]
    ap_drafts = [
        {"kind": "ap_bill", "amount": 500, "sync_status": "Draft"},
    ]
    db, _ = _make_db(ar_drafts=ar_drafts, ap_drafts=ap_drafts)
    snapshot = await compute_finance_snapshot(db, tenant_id="platform")

    assert snapshot["ar_total"] == 1000.0
    assert snapshot["ap_total"] == 500.0
    assert snapshot["source_metadata"]["ar_data_available"] is True
    assert snapshot["source_metadata"]["ap_data_available"] is True


# ---------------------------------------------------------------------------
# compute_finance_snapshot — sync_status filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_snapshot_sync_status_outstanding_only():
    """Only Draft and Sent sync_statuses count as outstanding.

    Synced (= paid/settled in QBO) and Failed (= error/cancelled) are
    terminal and must not inflate the outstanding totals.
    """
    snapshot_date = date(2026, 5, 4)
    ar_drafts = [
        {"kind": "ar_invoice", "amount": 1000, "sync_status": "Draft"},
        {"kind": "ar_invoice", "amount": 2000, "sync_status": "Sent"},
        {"kind": "ar_invoice", "amount": 5000, "sync_status": "Synced"},    # terminal
        {"kind": "ar_invoice", "amount": 500,  "sync_status": "Failed"},    # terminal
    ]
    db, _ = _make_db(ar_drafts=ar_drafts)
    snapshot = await compute_finance_snapshot(
        db, tenant_id="platform", snapshot_date=snapshot_date
    )

    # Only Draft ($1000) + Sent ($2000) = $3000 outstanding.
    assert snapshot["ar_total"] == 3000.0
    assert snapshot["source_metadata"]["ar_draft_count"] == 2


@pytest.mark.asyncio
async def test_compute_snapshot_ap_sync_status_filtering():
    """Same sync_status rules apply to AP side."""
    ap_drafts = [
        {"kind": "ap_bill", "amount": 800, "sync_status": "Draft"},
        {"kind": "ap_bill", "amount": 1200, "sync_status": "Sent"},
        {"kind": "ap_bill", "amount": 9999, "sync_status": "Synced"},   # terminal
    ]
    db, _ = _make_db(ap_drafts=ap_drafts)
    snapshot = await compute_finance_snapshot(db, tenant_id="platform")

    assert snapshot["ap_total"] == 2000.0
    assert snapshot["source_metadata"]["ap_draft_count"] == 2


# ---------------------------------------------------------------------------
# compute_finance_snapshot — aging buckets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_snapshot_aging_buckets_current_and_overdue():
    """Aging buckets distribute correctly across due dates."""
    snapshot_date = date(2026, 5, 4)
    ar_drafts = [
        # current — due in the future
        {"kind": "ar_invoice", "amount": 500, "sync_status": "Sent",
         "due_date": "2026-05-20"},
        # 1_30 — 15 days past due
        {"kind": "ar_invoice", "amount": 300, "sync_status": "Draft",
         "due_date": "2026-04-19"},
        # 31_60 — 40 days past due
        {"kind": "ar_invoice", "amount": 200, "sync_status": "Sent",
         "due_date": "2026-03-25"},
        # unknown — no due_date
        {"kind": "ar_invoice", "amount": 100, "sync_status": "Draft"},
    ]
    db, _ = _make_db(ar_drafts=ar_drafts)
    snapshot = await compute_finance_snapshot(
        db, tenant_id="platform", snapshot_date=snapshot_date
    )

    assert snapshot["ar_aging"]["current"] == 500.0
    assert snapshot["ar_aging"]["1_30"] == 300.0
    assert snapshot["ar_aging"]["31_60"] == 200.0
    assert snapshot["ar_aging"]["unknown"] == 100.0
    assert snapshot["ar_total"] == 1100.0


# ---------------------------------------------------------------------------
# compute_finance_snapshot — snapshot doc shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_snapshot_doc_shape():
    """Snapshot document must match the FCC contract shape exactly."""
    db, _ = _make_db(
        ar_drafts=[
            {"kind": "ar_invoice", "amount": 250, "sync_status": "Sent"},
        ],
        ap_drafts=[
            {"kind": "ap_bill", "amount": 100, "sync_status": "Draft"},
        ],
    )
    snapshot = await compute_finance_snapshot(db, tenant_id="platform")

    assert snapshot["snapshot_id"].startswith("fs_")
    assert snapshot["tenant_id"] == "platform"
    assert "snapshot_date" in snapshot
    assert isinstance(snapshot["ar_total"], float)
    assert isinstance(snapshot["ap_total"], float)
    # Aging keys
    for bucket in ("current", "1_30", "31_60", "61_90", "over_90", "unknown"):
        assert bucket in snapshot["ar_aging"]
        assert bucket in snapshot["ap_aging"]
    # Source
    assert snapshot["source"] == "finance_drafts"
    # Metadata uses new key names (not ar_invoice_count/ap_bill_count)
    meta = snapshot["source_metadata"]
    assert "ar_draft_count" in meta
    assert "ap_draft_count" in meta
    assert "ar_data_available" in meta
    assert "ap_data_available" in meta
    assert "ar_invoice_count" not in meta   # old key must not appear
    assert "ap_bill_count" not in meta      # old key must not appear


# ---------------------------------------------------------------------------
# save_finance_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_finance_snapshot_upserts():
    """save_finance_snapshot calls update_one (upsert) not insert_one."""
    db, snapshots_collection = _make_db()
    snap = {
        "snapshot_id": "fs_abc123",
        "tenant_id": "platform",
        "snapshot_date": "2026-05-04",
        "ar_total": 100.0,
        "ap_total": 50.0,
        "ar_aging": {},
        "ap_aging": {},
        "source": "finance_drafts",
        "source_metadata": {
            "ar_draft_count": 1,
            "ap_draft_count": 1,
            "ar_data_available": True,
            "ap_data_available": True,
        },
        "created_at": datetime.now(timezone.utc),
    }
    await save_finance_snapshot(snap, db=db)
    snapshots_collection.update_one.assert_awaited_once()
    call_args = snapshots_collection.update_one.call_args
    assert call_args[1].get("upsert") is True


# ---------------------------------------------------------------------------
# refresh_finance_snapshots — end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_finance_snapshots_returns_saved_doc():
    """refresh_finance_snapshots computes + saves and returns the doc."""
    ar_drafts = [
        {"kind": "ar_invoice", "amount": 1000, "sync_status": "Sent"},
    ]
    db, _ = _make_db(ar_drafts=ar_drafts)
    result = await refresh_finance_snapshots(db, tenant_id="platform")

    assert result["ar_total"] == 1000.0
    assert result["source"] == "finance_drafts"
    assert result["source_metadata"]["ar_draft_count"] == 1


# ---------------------------------------------------------------------------
# get_latest_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_latest_snapshot_returns_most_recent():
    """get_latest_snapshot returns the snapshot doc when one exists."""
    existing = {
        "snapshot_id": "fs_xyz",
        "tenant_id": "platform",
        "snapshot_date": "2026-05-04",
        "ar_total": 500.0,
        "ap_total": 250.0,
    }
    db, _ = _make_db(latest_snapshot=existing)
    result = await get_latest_snapshot("platform", db=db)
    assert result is not None
    assert result["snapshot_id"] == "fs_xyz"


@pytest.mark.asyncio
async def test_get_latest_snapshot_returns_none_when_empty():
    """get_latest_snapshot returns None when no snapshot doc exists."""
    db, _ = _make_db()
    result = await get_latest_snapshot("platform", db=db)
    assert result is None


# ---------------------------------------------------------------------------
# Constant sanity checks
# ---------------------------------------------------------------------------


def test_outstanding_sync_statuses_are_case_sensitive():
    """FCC sync_status enum is case-sensitive — verify the sets match exactly."""
    assert _AR_OUTSTANDING_SYNC_STATUSES == {"Draft", "Sent"}
    assert _AP_OUTSTANDING_SYNC_STATUSES == {"Draft", "Sent"}
    # Lowercase variants from legacy schema must NOT be in the sets.
    assert "draft" not in _AR_OUTSTANDING_SYNC_STATUSES
    assert "sent" not in _AR_OUTSTANDING_SYNC_STATUSES
