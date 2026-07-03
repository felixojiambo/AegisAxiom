"""Tests for scripts/migrate_internal_ar_ap_to_drafts.py.

Tests the transform functions, status mapping, idempotency logic,
and dry-run behavior — all without hitting a real MongoDB.

Coverage:
    - transform_ar_invoice: field mapping, status mapping (5 AR statuses)
    - transform_ap_bill: field mapping, status mapping (5 AP statuses)
    - migrate_collection: idempotency (skip if legacy_source_id exists)
    - migrate_collection: dry_run does not call insert_one
    - migrate_collection: missing id_field skips doc gracefully
    - Status mapping correctness: each AR/AP status -> correct sync_status + qbo_id/error
"""
from __future__ import annotations

import sys
import os
import pytest
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

# The script lives in scripts/, not in the Python package tree.
# We import it by path so pytest can find it.
SCRIPTS_DIR = os.path.join(
    os.path.dirname(__file__),  # backend/tests/
    "..", "..", "scripts",
)
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

from migrate_internal_ar_ap_to_drafts import (
    transform_ar_invoice,
    transform_ap_bill,
    migrate_collection,
    AR_STATUS_MAP,
    AP_STATUS_MAP,
)

NOW_ISO = "2026-05-04T12:00:00+00:00"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _ar_doc(invoice_id="inv_001", status="draft", **overrides) -> dict:
    base = {
        "invoice_id": invoice_id,
        "account_id": "acc_xyz",
        "account_name": "Acme Corp",
        "contract_id": "cont_abc",
        "period_start": "2026-03-01",
        "period_end": "2026-03-31",
        "total": 5000.0,
        "subtotal": 5000.0,
        "total_hours": 40.0,
        "status": status,
        "created_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
        "created_by": "u_finance",
        "notes": "Invoice for March",
        "transactions": [],
    }
    base.update(overrides)
    return base


def _ap_doc(bill_id="bill_001", status="draft", **overrides) -> dict:
    base = {
        "bill_id": bill_id,
        "contractor_business_id": "biz_001",
        "contractor_business_name": "Bob's LLC",
        "contractor_user_id": None,
        "contractor_name": "Bob's LLC",
        "period_start": "2026-03-01",
        "period_end": "2026-03-31",
        "total": 2400.0,
        "subtotal": 2400.0,
        "status": status,
        "created_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
        "created_by": "u_finance",
        "notes": "March bill",
        "line_items": [],
    }
    base.update(overrides)
    return base


def _make_async_cursor(docs: List[dict]):
    """Return an async iterable that yields each doc."""
    class FakeCursor:
        def __init__(self, docs):
            self._docs = iter(docs)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._docs)
            except StopIteration:
                raise StopAsyncIteration

    return FakeCursor(docs)


def _make_db_for_migration(source_docs: List[dict], existing_drafts: List[dict] = None):
    """Build a mock DB for migrate_collection tests."""
    existing_drafts = existing_drafts or []
    db = MagicMock()

    source_coll = MagicMock()
    source_coll.find = MagicMock(return_value=_make_async_cursor(source_docs))

    drafts_coll = MagicMock()

    def _find_one(query):
        # Simulate find_one by matching legacy_source_id
        lid = query.get("legacy_source_id")
        if lid:
            for d in existing_drafts:
                if d.get("legacy_source_id") == lid:
                    return d
        return None

    drafts_coll.find_one = AsyncMock(side_effect=lambda q: _find_one(q) or None)
    drafts_coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id="oid"))

    def _getitem(name):
        if name == "ap_bills" or name == "ar_invoices":
            return source_coll
        if name == "finance_drafts":
            return drafts_coll
        m = MagicMock()
        m.find_one = AsyncMock(return_value=None)
        return m

    db.__getitem__ = MagicMock(side_effect=_getitem)
    return db, source_coll, drafts_coll


# ---------------------------------------------------------------------------
# AR transform + status mapping tests
# ---------------------------------------------------------------------------

class TestTransformARInvoice:
    def test_basic_field_mapping(self):
        doc = _ar_doc(invoice_id="inv_001", status="draft")
        result = transform_ar_invoice(doc, NOW_ISO)

        assert result["kind"] == "ar_invoice"
        assert result["counterparty"] == "Acme Corp"
        assert result["counterparty_id"] == "acc_xyz"
        assert result["amount"] == 5000.0
        assert result["source_contract_id"] == "cont_abc"
        assert result["source_period_start"] == "2026-03-01"
        assert result["source_period_end"] == "2026-03-31"
        assert result["source_origin"] == "legacy_migration"
        assert result["legacy_source_id"] == "inv_001"
        assert result["legacy_collection"] == "ar_invoices"
        assert result["draft_id"].startswith("fcc_draft_")
        assert result["created_by"] == "u_finance"

    def test_status_draft_maps_to_Draft(self):
        result = transform_ar_invoice(_ar_doc(status="draft"), NOW_ISO)
        assert result["sync_status"] == "Draft"
        assert result["qbo_id"] is None
        assert result["error"] is None

    def test_status_sent_maps_to_Sent(self):
        result = transform_ar_invoice(_ar_doc(status="sent"), NOW_ISO)
        assert result["sync_status"] == "Sent"
        assert result["qbo_id"] is None
        assert result["error"] is None

    def test_status_paid_maps_to_Synced_with_legacy_qbo_id(self):
        result = transform_ar_invoice(_ar_doc(invoice_id="inv_paid_01", status="paid"), NOW_ISO)
        assert result["sync_status"] == "Synced"
        assert result["qbo_id"] == "LEGACY-INV-inv_paid_01"
        assert result["error"] is None

    def test_status_overdue_maps_to_Sent(self):
        result = transform_ar_invoice(_ar_doc(status="overdue"), NOW_ISO)
        assert result["sync_status"] == "Sent"
        assert result["qbo_id"] is None

    def test_status_partial_paid_maps_to_Sent(self):
        result = transform_ar_invoice(_ar_doc(status="partial_paid"), NOW_ISO)
        assert result["sync_status"] == "Sent"
        assert result["qbo_id"] is None

    def test_status_cancelled_maps_to_Failed_with_error(self):
        result = transform_ar_invoice(_ar_doc(status="cancelled"), NOW_ISO)
        assert result["sync_status"] == "Failed"
        assert result["error"] == "cancelled"
        assert result["qbo_id"] is None

    def test_transactions_become_line_items(self):
        doc = _ar_doc(transactions=[
            {"description": "Alice: 8h", "hours": 8.0, "rate": 150.0, "amount": 1200.0, "date": "2026-03-10"},
            {"description": "Bob: 4h", "hours": 4.0, "rate": 100.0, "amount": 400.0, "date": "2026-03-11"},
        ])
        result = transform_ar_invoice(doc, NOW_ISO)
        assert len(result["line_items"]) == 2
        assert result["line_items"][0]["quantity"] == 8.0
        assert result["line_items"][1]["amount"] == 400.0

    def test_no_transactions_fallback_line_item(self):
        doc = _ar_doc(transactions=[])
        result = transform_ar_invoice(doc, NOW_ISO)
        assert len(result["line_items"]) == 1
        assert result["line_items"][0]["amount"] == 5000.0

    def test_datetime_created_at_coerced_to_string(self):
        doc = _ar_doc(created_at=datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc))
        result = transform_ar_invoice(doc, NOW_ISO)
        # Should not raise; created_at should be a string
        assert isinstance(result["created_at"], str)


# ---------------------------------------------------------------------------
# AP transform + status mapping tests
# ---------------------------------------------------------------------------

class TestTransformAPBill:
    def test_basic_field_mapping(self):
        doc = _ap_doc(bill_id="bill_001", status="draft")
        result = transform_ap_bill(doc, NOW_ISO)

        assert result["kind"] == "ap_bill"
        assert result["counterparty"] == "Bob's LLC"
        assert result["counterparty_id"] == "biz_001"
        assert result["amount"] == 2400.0
        assert result["source_contractor_id"] == "biz_001"
        assert result["source_period_start"] == "2026-03-01"
        assert result["source_period_end"] == "2026-03-31"
        assert result["source_origin"] == "legacy_migration"
        assert result["legacy_source_id"] == "bill_001"
        assert result["legacy_collection"] == "ap_bills"
        assert result["draft_id"].startswith("fcc_draft_")

    def test_status_draft_maps_to_Draft(self):
        result = transform_ap_bill(_ap_doc(status="draft"), NOW_ISO)
        assert result["sync_status"] == "Draft"
        assert result["qbo_id"] is None
        assert result["error"] is None

    def test_status_pending_finance_review_maps_to_Draft(self):
        result = transform_ap_bill(_ap_doc(status="pending_finance_review"), NOW_ISO)
        assert result["sync_status"] == "Draft"
        assert result["qbo_id"] is None
        assert result["error"] is None

    def test_status_approved_maps_to_Sent(self):
        result = transform_ap_bill(_ap_doc(status="approved"), NOW_ISO)
        assert result["sync_status"] == "Sent"
        assert result["qbo_id"] is None
        assert result["error"] is None

    def test_status_paid_maps_to_Synced_with_legacy_qbo_id(self):
        result = transform_ap_bill(_ap_doc(bill_id="bill_paid_01", status="paid"), NOW_ISO)
        assert result["sync_status"] == "Synced"
        assert result["qbo_id"] == "LEGACY-AP-bill_paid_01"
        assert result["error"] is None

    def test_status_cancelled_maps_to_Failed_with_error(self):
        result = transform_ap_bill(_ap_doc(status="cancelled"), NOW_ISO)
        assert result["sync_status"] == "Failed"
        assert result["error"] == "cancelled"
        assert result["qbo_id"] is None

    def test_line_items_extracted_from_legacy_format(self):
        doc = _ap_doc(line_items=[
            {"contractor_name": "Alice", "project_name": "Alpha", "contract_name": "C1",
             "hours": 16.0, "rate": 80.0, "amount": 1280.0},
        ])
        result = transform_ap_bill(doc, NOW_ISO)
        assert len(result["line_items"]) == 1
        assert result["line_items"][0]["quantity"] == 16.0
        assert result["line_items"][0]["amount"] == 1280.0
        assert "Alice" in result["line_items"][0]["description"]

    def test_no_line_items_fallback(self):
        doc = _ap_doc(line_items=[])
        result = transform_ap_bill(doc, NOW_ISO)
        assert len(result["line_items"]) == 1
        assert result["line_items"][0]["amount"] == 2400.0

    def test_user_id_contractor_no_business(self):
        doc = _ap_doc(
            contractor_business_id=None,
            contractor_user_id="u_freelance",
            contractor_business_name=None,
            contractor_name="Fred",
        )
        result = transform_ap_bill(doc, NOW_ISO)
        assert result["source_contractor_id"] == "u_freelance"
        assert result["counterparty"] == "Fred"


# ---------------------------------------------------------------------------
# migrate_collection behavior tests
# ---------------------------------------------------------------------------

class TestMigrateCollection:
    @pytest.mark.asyncio
    async def test_insert_called_for_new_doc(self):
        doc = _ar_doc(invoice_id="inv_new")
        db, source_coll, drafts_coll = _make_db_for_migration(
            source_docs=[doc], existing_drafts=[]
        )
        total, inserted, skipped = await migrate_collection(
            db=db,
            collection_name="ar_invoices",
            id_field="invoice_id",
            transform_fn=transform_ar_invoice,
            dry_run=False,
        )
        assert total == 1
        assert inserted == 1
        assert skipped == 0
        drafts_coll.insert_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_idempotent_second_run_skips(self):
        doc = _ar_doc(invoice_id="inv_dup")
        existing = {"legacy_source_id": "inv_dup", "draft_id": "fcc_draft_old"}
        db, _, drafts_coll = _make_db_for_migration(
            source_docs=[doc], existing_drafts=[existing]
        )
        total, inserted, skipped = await migrate_collection(
            db=db,
            collection_name="ar_invoices",
            id_field="invoice_id",
            transform_fn=transform_ar_invoice,
            dry_run=False,
        )
        assert total == 1
        assert inserted == 0
        assert skipped == 1
        drafts_coll.insert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_does_not_insert(self):
        doc = _ar_doc(invoice_id="inv_dry")
        db, _, drafts_coll = _make_db_for_migration(
            source_docs=[doc], existing_drafts=[]
        )
        total, inserted, skipped = await migrate_collection(
            db=db,
            collection_name="ar_invoices",
            id_field="invoice_id",
            transform_fn=transform_ar_invoice,
            dry_run=True,
        )
        assert total == 1
        assert inserted == 1   # counts "would insert"
        assert skipped == 0
        drafts_coll.insert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_id_field_skips_doc(self):
        doc = {"status": "draft", "total": 100.0}  # no invoice_id
        db, _, drafts_coll = _make_db_for_migration(source_docs=[doc], existing_drafts=[])
        total, inserted, skipped = await migrate_collection(
            db=db,
            collection_name="ar_invoices",
            id_field="invoice_id",
            transform_fn=transform_ar_invoice,
            dry_run=False,
        )
        assert total == 1
        assert inserted == 0
        assert skipped == 1
        drafts_coll.insert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_docs_mixed_idempotency(self):
        """Two docs: one already migrated (skip), one new (insert)."""
        docs = [
            _ar_doc(invoice_id="inv_old"),
            _ar_doc(invoice_id="inv_new"),
        ]
        existing = [{"legacy_source_id": "inv_old", "draft_id": "fcc_draft_old"}]
        db, _, drafts_coll = _make_db_for_migration(source_docs=docs, existing_drafts=existing)

        total, inserted, skipped = await migrate_collection(
            db=db,
            collection_name="ar_invoices",
            id_field="invoice_id",
            transform_fn=transform_ar_invoice,
            dry_run=False,
        )
        assert total == 2
        assert inserted == 1
        assert skipped == 1
        # Only one insert_one call for the new doc
        assert drafts_coll.insert_one.call_count == 1

    @pytest.mark.asyncio
    async def test_ap_bills_migrate_correctly(self):
        doc = _ap_doc(bill_id="bill_ap_new", status="approved")
        db, _, drafts_coll = _make_db_for_migration(source_docs=[doc], existing_drafts=[])
        total, inserted, skipped = await migrate_collection(
            db=db,
            collection_name="ap_bills",
            id_field="bill_id",
            transform_fn=transform_ap_bill,
            dry_run=False,
        )
        assert total == 1
        assert inserted == 1
        assert skipped == 0
        call_args = drafts_coll.insert_one.call_args[0][0]
        # approved -> Sent
        assert call_args["sync_status"] == "Sent"
        assert call_args["kind"] == "ap_bill"
        assert call_args["legacy_source_id"] == "bill_ap_new"


# ---------------------------------------------------------------------------
# Status mapping completeness — verify all defined statuses are correct
# ---------------------------------------------------------------------------

class TestStatusMappingCompleteness:
    """Verify status_mapping tables match finance.json data_migration.status_mapping."""

    def test_all_ar_statuses_present(self):
        expected = {"draft", "sent", "paid", "overdue", "partial_paid", "cancelled"}
        assert set(AR_STATUS_MAP.keys()) == expected

    def test_all_ap_statuses_present(self):
        expected = {"draft", "pending_finance_review", "approved", "paid", "cancelled"}
        assert set(AP_STATUS_MAP.keys()) == expected

    @pytest.mark.parametrize("status,expected_sync,has_qbo,has_error", [
        ("draft",        "Draft",  False, False),
        ("sent",         "Sent",   False, False),
        ("paid",         "Synced", True,  False),
        ("overdue",      "Sent",   False, False),
        ("partial_paid", "Sent",   False, False),
        ("cancelled",    "Failed", False, True),
    ])
    def test_ar_status_mapping(self, status, expected_sync, has_qbo, has_error):
        result = transform_ar_invoice(_ar_doc(invoice_id="inv_t", status=status), NOW_ISO)
        assert result["sync_status"] == expected_sync, f"AR {status}: expected {expected_sync}"
        if has_qbo:
            assert result["qbo_id"] is not None and "LEGACY" in result["qbo_id"]
        else:
            assert result["qbo_id"] is None
        if has_error:
            assert result["error"] == "cancelled"
        else:
            assert result["error"] is None

    @pytest.mark.parametrize("status,expected_sync,has_qbo,has_error", [
        ("draft",                  "Draft",  False, False),
        ("pending_finance_review", "Draft",  False, False),
        ("approved",               "Sent",   False, False),
        ("paid",                   "Synced", True,  False),
        ("cancelled",              "Failed", False, True),
    ])
    def test_ap_status_mapping(self, status, expected_sync, has_qbo, has_error):
        result = transform_ap_bill(_ap_doc(bill_id="bill_t", status=status), NOW_ISO)
        assert result["sync_status"] == expected_sync, f"AP {status}: expected {expected_sync}"
        if has_qbo:
            assert result["qbo_id"] is not None and "LEGACY" in result["qbo_id"]
        else:
            assert result["qbo_id"] is None
        if has_error:
            assert result["error"] == "cancelled"
        else:
            assert result["error"] is None
