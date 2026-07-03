"""Wave 5 (contracts-time-mgmt) — invoice batch builder unit tests.

Locks the grouping / dedup / total math + entry filtering for the
``services/invoice_batch_builder.build_invoice_batches`` function.

Coverage:
  - Only approved + billable entries are included.
  - Status="submitted", "draft", "rejected" entries are excluded.
  - billing_type="non_billable" / "fixed_fee" entries are excluded.
  - is_billable=False entries are excluded.
  - Entries are grouped by contract_id.
  - Line item rate uses sell_rate_snapshot (Wave 3 preference).
  - Totals = sum of hours and sum of (hours * sell_rate_snapshot).
  - Dedup: a non-rejected batch for (contract, period) blocks rebuild.
  - Rejected batch does NOT block a rebuild.
  - Contract missing skips with reason "contract_not_found".
  - entry_ids and line_items preserved on the batch.
  - tenant_id propagates when contract has one.
  - Zero-rate line items produce a warning (not a skip).
  - The biweekly_period_for helper produces 14-day windows.
"""

from __future__ import annotations

import os
from datetime import date
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest


os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, items):
        self._items = items

    def sort(self, *_a, **_kw):
        return self

    async def to_list(self, _n):
        return list(self._items)


class _TimeEntries:
    def __init__(self, entries):
        self._entries = list(entries)

    def find(self, query, _projection=None):
        def _match(e):
            if "status" in query:
                want = query["status"]
                # Wave 5 hardening: status is queried as {"$in": [...]}.
                if isinstance(want, dict) and "$in" in want:
                    if e.get("status") not in want["$in"]:
                        return False
                elif e.get("status") != want:
                    return False
            if "is_billable" in query and bool(e.get("is_billable")) != query["is_billable"]:
                return False
            if "contract_id" in query:
                want = query["contract_id"]
                if isinstance(want, dict):
                    # $ne: None
                    if "$ne" in want and e.get("contract_id") == want["$ne"]:
                        return False
                else:
                    if e.get("contract_id") != want:
                        return False
            if "date" in query and isinstance(query["date"], dict):
                d = e.get("date")
                gte = query["date"].get("$gte")
                lte = query["date"].get("$lte")
                if gte and d and d < gte:
                    return False
                if lte and d and d > lte:
                    return False
            return True
        return _Cursor([dict(e) for e in self._entries if _match(e)])


class _ContractTimeApplications:
    """Wave 3 ledger fake. Returns rows by entry_id+contract_id lookup
    so the BOH-with-BOA invoiceable-hours math can read overage_hours
    from the ledger row the contract_debit service wrote at approve
    time."""

    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def find(self, query, _projection=None):
        entry_filter = query.get("entry_id")
        contract_id = query.get("contract_id")
        def _match(r):
            if contract_id is not None and r.get("contract_id") != contract_id:
                return False
            if isinstance(entry_filter, dict) and "$in" in entry_filter:
                if r.get("entry_id") not in entry_filter["$in"]:
                    return False
            elif entry_filter is not None and r.get("entry_id") != entry_filter:
                return False
            return True
        return _Cursor([dict(r) for r in self._rows if _match(r)])


class _Contracts:
    def __init__(self, by_id):
        self._by_id = dict(by_id)

    async def find_one(self, query, _projection=None):
        cid = query.get("contract_id")
        return dict(self._by_id[cid]) if cid in self._by_id else None


class _InvoiceBatches:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.inserts: List[Dict[str, Any]] = []

    async def find_one(self, query, _projection=None):
        for r in self._rows:
            cid_match = r.get("contract_id") == query.get("contract_id")
            ps_match = r.get("period_start") == query.get("period_start")
            pe_match = r.get("period_end") == query.get("period_end")
            status_filter = query.get("status")
            status_match = True
            if isinstance(status_filter, dict) and "$ne" in status_filter:
                status_match = r.get("status") != status_filter["$ne"]
            elif isinstance(status_filter, str):
                status_match = r.get("status") == status_filter
            if cid_match and ps_match and pe_match and status_match:
                return dict(r)
        return None

    async def insert_one(self, doc):
        self.inserts.append(dict(doc))
        self._rows.append(dict(doc))
        return SimpleNamespace(inserted_id="x")


def _db(*, entries, contracts, existing_batches=None, applications=None):
    class _DB:
        pass
    db = _DB()
    db.time_entries = _TimeEntries(entries)
    db.contracts = _Contracts(contracts)
    db.invoice_batches = _InvoiceBatches(existing_batches)
    # Wave 5 hardening: BOH-with-BOA builder reads
    # contract_time_applications to learn each entry's overage portion.
    db.contract_time_applications = _ContractTimeApplications(applications)
    return db


def _entry(**overrides):
    base = {
        "entry_id": "te_a",
        "contract_id": "c_001",
        "project_id": "proj_001",
        "user_id": "u_eng",
        "user_name": "Eng One",
        "date": "2026-05-18",
        "hours": 4.0,
        "status": "approved",
        "is_billable": True,
        "billing_type": "billable",
        "sell_rate_snapshot": 180.0,
        "cost_rate_snapshot": 95.0,
        "notes": "Sprint work",
    }
    base.update(overrides)
    return base


def _contract(**overrides):
    """Wave 5 hardening: default contract type is BOA so the simple
    happy-path tests still produce one line item per entry without
    needing contract_time_applications ledger fixtures. BOH and
    BOH-with-BOA tests live below and supply their own ledger rows."""
    base = {
        "contract_id": "c_001",
        "name": "Acme Engagement",
        "account_id": "acct_001",
        "account_name": "Acme",
        "contract_type": "boa",
        "hourly_rate": 0,
        "resource_types": [{"id": "rt_eng", "sell_rate": 200.0}],
        "tenant_id": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# biweekly helper
# ---------------------------------------------------------------------------


def test_biweekly_period_uses_14_day_window():
    from services.invoice_batch_builder import biweekly_period_for
    # Reference: 2026-05-22 (Friday). Default anchor = Monday on/before = 2026-05-18.
    start, end = biweekly_period_for(date(2026, 5, 22))
    assert start == "2026-05-18"
    assert end == "2026-05-31"


def test_biweekly_period_respects_anchor():
    from services.invoice_batch_builder import biweekly_period_for
    anchor = date(2026, 1, 5)
    # Reference 2026-01-30: 25 days from anchor → cycle 1 → start = anchor + 14 = 2026-01-19.
    start, end = biweekly_period_for(date(2026, 1, 30), anchor=anchor)
    assert start == "2026-01-19"
    assert end == "2026-02-01"


# ---------------------------------------------------------------------------
# Builder happy path + filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_groups_by_contract():
    entries = [
        _entry(entry_id="te_a", contract_id="c_001", hours=4.0, sell_rate_snapshot=180.0),
        _entry(entry_id="te_b", contract_id="c_001", hours=2.0, sell_rate_snapshot=180.0, user_id="u_pm", user_name="PM"),
        _entry(entry_id="te_c", contract_id="c_002", hours=3.0, sell_rate_snapshot=210.0, user_id="u_other", user_name="Other"),
    ]
    contracts = {
        "c_001": _contract(),
        "c_002": _contract(contract_id="c_002", name="Beta", account_id="acct_002", account_name="Beta"),
    }
    db = _db(entries=entries, contracts=contracts)
    from services.invoice_batch_builder import build_invoice_batches
    batches, skipped, warnings = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31",
        actor_user_id="u_finance",
    )
    assert len(batches) == 2
    assert {b["contract_id"] for b in batches} == {"c_001", "c_002"}
    c_001 = next(b for b in batches if b["contract_id"] == "c_001")
    assert c_001["total_hours"] == 6.0
    assert c_001["total_amount"] == round(6.0 * 180.0, 2)
    assert set(c_001["entry_ids"]) == {"te_a", "te_b"}


@pytest.mark.asyncio
async def test_build_excludes_non_approved_entries():
    entries = [
        _entry(entry_id="te_ok"),
        _entry(entry_id="te_submitted", status="submitted"),
        _entry(entry_id="te_draft", status="draft"),
        _entry(entry_id="te_rejected", status="rejected"),
    ]
    db = _db(entries=entries, contracts={"c_001": _contract()})
    from services.invoice_batch_builder import build_invoice_batches
    batches, _, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    assert len(batches) == 1
    assert batches[0]["entry_ids"] == ["te_ok"]


@pytest.mark.asyncio
async def test_build_excludes_non_billable_entries():
    entries = [
        _entry(entry_id="te_billable"),
        _entry(entry_id="te_non_billable", is_billable=False, billing_type="non_billable"),
        _entry(entry_id="te_fixed_fee", billing_type="fixed_fee"),
    ]
    db = _db(entries=entries, contracts={"c_001": _contract()})
    from services.invoice_batch_builder import build_invoice_batches
    batches, _, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    assert len(batches) == 1
    assert batches[0]["entry_ids"] == ["te_billable"]


@pytest.mark.asyncio
async def test_build_uses_sell_rate_snapshot_not_live_rate():
    """Contract live hourly_rate is 999, but the entry's frozen
    sell_rate_snapshot is 180. Batch total must use 180."""
    entries = [_entry(hours=5.0, sell_rate_snapshot=180.0)]
    contract = _contract(hourly_rate=999.0, resource_types=[{"id": "rt_eng", "sell_rate": 999.0}])
    db = _db(entries=entries, contracts={"c_001": contract})
    from services.invoice_batch_builder import build_invoice_batches
    batches, _, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    assert batches[0]["total_amount"] == round(5.0 * 180.0, 2)
    assert batches[0]["line_items"][0]["sell_rate_snapshot"] == 180.0


@pytest.mark.asyncio
async def test_build_dedup_against_pending_batch():
    entries = [_entry()]
    existing = [{
        "batch_id": "ibatch_existing",
        "contract_id": "c_001",
        "period_start": "2026-05-18",
        "period_end": "2026-05-31",
        "status": "pending_finance_approval",
    }]
    db = _db(entries=entries, contracts={"c_001": _contract()}, existing_batches=existing)
    from services.invoice_batch_builder import build_invoice_batches
    batches, skipped, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    assert batches == []
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "batch_already_exists"
    assert skipped[0]["existing_batch_id"] == "ibatch_existing"


@pytest.mark.asyncio
async def test_build_dedup_ignores_rejected_batch():
    """A rejected batch must NOT block a rebuild — that's how the
    finance-approver workflow recovers from a wrong rejection."""
    entries = [_entry()]
    existing = [{
        "batch_id": "ibatch_rejected",
        "contract_id": "c_001",
        "period_start": "2026-05-18",
        "period_end": "2026-05-31",
        "status": "rejected",
    }]
    db = _db(entries=entries, contracts={"c_001": _contract()}, existing_batches=existing)
    from services.invoice_batch_builder import build_invoice_batches
    batches, _, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    assert len(batches) == 1
    assert batches[0]["batch_id"].startswith("ibatch_")
    assert batches[0]["batch_id"] != "ibatch_rejected"


@pytest.mark.asyncio
async def test_build_skips_missing_contract():
    entries = [_entry(contract_id="c_missing")]
    db = _db(entries=entries, contracts={})
    from services.invoice_batch_builder import build_invoice_batches
    batches, skipped, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    assert batches == []
    assert skipped[0]["reason"] == "contract_not_found"


@pytest.mark.asyncio
async def test_build_creates_initial_status_pending_finance_approval():
    entries = [_entry()]
    db = _db(entries=entries, contracts={"c_001": _contract()})
    from services.invoice_batch_builder import build_invoice_batches
    batches, _, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    assert batches[0]["status"] == "pending_finance_approval"
    assert batches[0]["linked_finance_draft_id"] is None


@pytest.mark.asyncio
async def test_build_propagates_tenant_id():
    entries = [_entry()]
    db = _db(entries=entries, contracts={"c_001": _contract(tenant_id="tenant_a")})
    from services.invoice_batch_builder import build_invoice_batches
    batches, _, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    assert batches[0]["tenant_id"] == "tenant_a"


@pytest.mark.asyncio
async def test_build_warns_on_zero_rate_line_items():
    entries = [
        _entry(entry_id="te_paid", sell_rate_snapshot=180.0),
        _entry(entry_id="te_unpaid", sell_rate_snapshot=0.0, user_id="u_other"),
    ]
    db = _db(entries=entries, contracts={"c_001": _contract(hourly_rate=0)})
    from services.invoice_batch_builder import build_invoice_batches
    batches, _, warnings = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    assert len(batches) == 1
    assert any("zero sell rate" in w for w in warnings)
    # Both entries still landed in the batch (warning is informational).
    assert len(batches[0]["line_items"]) == 2


@pytest.mark.asyncio
async def test_build_contract_id_filter_scopes_one_contract():
    entries = [
        _entry(entry_id="te_a", contract_id="c_001"),
        _entry(entry_id="te_b", contract_id="c_002"),
    ]
    contracts = {
        "c_001": _contract(),
        "c_002": _contract(contract_id="c_002"),
    }
    db = _db(entries=entries, contracts=contracts)
    from services.invoice_batch_builder import build_invoice_batches
    batches, _, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31",
        contract_id_filter="c_001", actor_user_id="u_finance",
    )
    assert len(batches) == 1
    assert batches[0]["contract_id"] == "c_001"


@pytest.mark.asyncio
async def test_build_dry_run_does_not_insert():
    entries = [_entry()]
    db = _db(entries=entries, contracts={"c_001": _contract()})
    from services.invoice_batch_builder import build_invoice_batches
    batches, _, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31",
        actor_user_id="u_finance", dry_run=True,
    )
    assert len(batches) == 1
    # No insert recorded — dry-run preview only.
    assert db.invoice_batches.inserts == []


@pytest.mark.asyncio
async def test_build_returns_no_billable_entries_when_period_empty():
    db = _db(entries=[], contracts={})
    from services.invoice_batch_builder import build_invoice_batches
    batches, skipped, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    assert batches == []
    assert skipped[0]["reason"] == "no_billable_entries"


# ---------------------------------------------------------------------------
# Wave 5 hardening — BOH / BOH-with-BOA / status / batch_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plain_boh_contract_is_skipped_with_prepaid_reason():
    """Plain BOH is prepaid at signing. The cycle's approved hours
    must NOT produce an invoiceable batch — they were already paid
    for. Skip with a documented reason instead."""
    entries = [_entry()]
    db = _db(entries=entries, contracts={"c_001": _contract(contract_type="boh")})
    from services.invoice_batch_builder import build_invoice_batches
    batches, skipped, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    assert batches == []
    assert skipped[0]["reason"] == "boh_prepaid_no_overage"
    assert skipped[0]["contract_id"] == "c_001"
    assert skipped[0]["candidate_entry_count"] == 1


@pytest.mark.asyncio
async def test_boh_with_boa_invoices_only_overage_hours_for_boundary_entry():
    """Boundary-crossing entry: 5 hours total, 2 block + 3 overage
    per the Wave 3 ledger row. The batch line must bill 3 hours, not
    5, because the block portion was prepaid."""
    entries = [_entry(hours=5.0, sell_rate_snapshot=180.0)]
    contracts = {"c_001": _contract(contract_type="boh_with_boa")}
    ledger = [{
        "application_id": "appl_1",
        "entry_id": "te_a",
        "contract_id": "c_001",
        "block_hours": 2.0,
        "overage_hours": 3.0,
        "effective_billing_mode": "block_and_overflow",
    }]
    db = _db(entries=entries, contracts=contracts, applications=ledger)
    from services.invoice_batch_builder import build_invoice_batches
    batches, _, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    assert len(batches) == 1
    line = batches[0]["line_items"][0]
    assert line["hours"] == 3.0
    assert line["entry_hours"] == 5.0  # raw entry hours preserved for audit
    assert line["amount"] == 3.0 * 180.0
    assert batches[0]["total_hours"] == 3.0
    assert batches[0]["total_amount"] == 3.0 * 180.0


@pytest.mark.asyncio
async def test_boh_with_boa_block_only_entry_is_excluded():
    """Entry that fit entirely inside the remaining block produced
    a ledger row with overage_hours=0. The builder must NOT emit a
    line item for it — block hours are prepaid."""
    entries = [
        _entry(entry_id="te_block", hours=2.0, sell_rate_snapshot=180.0),
        _entry(entry_id="te_overage", hours=4.0, sell_rate_snapshot=180.0, user_id="u_pm", user_name="PM"),
    ]
    contracts = {"c_001": _contract(contract_type="boh_with_boa")}
    ledger = [
        {"application_id": "appl_1", "entry_id": "te_block",
         "contract_id": "c_001", "block_hours": 2.0, "overage_hours": 0.0,
         "effective_billing_mode": "block"},
        {"application_id": "appl_2", "entry_id": "te_overage",
         "contract_id": "c_001", "block_hours": 0.0, "overage_hours": 4.0,
         "effective_billing_mode": "overflow"},
    ]
    db = _db(entries=entries, contracts=contracts, applications=ledger)
    from services.invoice_batch_builder import build_invoice_batches
    batches, _, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    assert len(batches) == 1
    # Only the overage entry made it onto the batch.
    assert batches[0]["entry_ids"] == ["te_overage"]
    assert batches[0]["total_hours"] == 4.0


@pytest.mark.asyncio
async def test_boh_with_boa_with_no_overage_skips_with_reason():
    """All approved entries fit inside the block → no overage to
    invoice. Skip with the documented reason rather than emit an
    empty batch."""
    entries = [_entry(hours=2.0)]
    contracts = {"c_001": _contract(contract_type="boh_with_boa")}
    ledger = [{
        "application_id": "appl_1", "entry_id": "te_a",
        "contract_id": "c_001", "block_hours": 2.0, "overage_hours": 0.0,
        "effective_billing_mode": "block",
    }]
    db = _db(entries=entries, contracts=contracts, applications=ledger)
    from services.invoice_batch_builder import build_invoice_batches
    batches, skipped, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    assert batches == []
    assert skipped[0]["reason"] == "no_overage_hours_to_invoice"


@pytest.mark.asyncio
async def test_boh_with_boa_without_ledger_row_skips_safely():
    """Defensive case: an approved entry on a BOH-with-BOA contract
    that has no ledger row (data corruption) must NOT default to
    full entry.hours. The builder treats missing application as zero
    overage and skips."""
    entries = [_entry(hours=3.0)]
    contracts = {"c_001": _contract(contract_type="boh_with_boa")}
    db = _db(entries=entries, contracts=contracts, applications=[])
    from services.invoice_batch_builder import build_invoice_batches
    batches, skipped, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    assert batches == []
    assert skipped[0]["reason"] == "no_overage_hours_to_invoice"


@pytest.mark.asyncio
async def test_boa_contract_invoices_full_entry_hours():
    """Pure BOA: every approved billable hour bills. No ledger
    lookup required."""
    entries = [_entry(hours=4.0, sell_rate_snapshot=180.0)]
    contracts = {"c_001": _contract(contract_type="boa")}
    db = _db(entries=entries, contracts=contracts)
    from services.invoice_batch_builder import build_invoice_batches
    batches, _, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    assert len(batches) == 1
    assert batches[0]["total_hours"] == 4.0
    assert batches[0]["total_amount"] == 4.0 * 180.0


@pytest.mark.asyncio
async def test_pm_approved_and_admin_approved_statuses_are_included():
    """Legacy two-stage statuses are batch-eligible."""
    entries = [
        _entry(entry_id="te_pm", status="pm_approved", hours=2.0),
        _entry(entry_id="te_admin", status="admin_approved", hours=3.0, user_id="u_pm", user_name="PM"),
        _entry(entry_id="te_submitted", status="submitted", hours=99.0),  # excluded
    ]
    db = _db(entries=entries, contracts={"c_001": _contract()})
    from services.invoice_batch_builder import build_invoice_batches
    batches, _, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    assert len(batches) == 1
    assert set(batches[0]["entry_ids"]) == {"te_pm", "te_admin"}
    assert batches[0]["total_hours"] == 5.0


@pytest.mark.asyncio
async def test_fixed_fee_contract_is_skipped_with_documented_reason():
    """fixed_fee contracts are not invoiced by the hour — skip the
    contract for AR batching with a documented reason."""
    entries = [_entry()]
    contracts = {"c_001": _contract(contract_type="fixed_fee")}
    db = _db(entries=entries, contracts=contracts)
    from services.invoice_batch_builder import build_invoice_batches
    batches, skipped, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    assert batches == []
    assert skipped[0]["reason"] == "contract_type_not_time_billable"
    assert skipped[0]["contract_type"] == "fixed_fee"


@pytest.mark.asyncio
async def test_batch_carries_deterministic_batch_key():
    """The Wave 5 batch_key is the deterministic dedup column the
    sparse unique index from the Wave 5 migration enforces."""
    entries = [_entry()]
    db = _db(entries=entries, contracts={"c_001": _contract()})
    from services.invoice_batch_builder import build_invoice_batches, compute_batch_key
    batches, _, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    expected_key = compute_batch_key("c_001", "2026-05-18", "2026-05-31")
    assert batches[0]["batch_key"] == expected_key
    assert batches[0]["contract_type"] == "boa"


@pytest.mark.asyncio
async def test_batch_excludes_non_billable_billing_type_even_when_is_billable_true():
    """Defense in depth: an entry with is_billable=True but
    billing_type=non_billable (schema drift) is still excluded."""
    entries = [
        _entry(entry_id="te_drift", billing_type="non_billable", is_billable=True),
        _entry(entry_id="te_ok"),
    ]
    db = _db(entries=entries, contracts={"c_001": _contract()})
    from services.invoice_batch_builder import build_invoice_batches
    batches, _, _ = await build_invoice_batches(
        db, period_start="2026-05-18", period_end="2026-05-31", actor_user_id="u_finance",
    )
    assert batches[0]["entry_ids"] == ["te_ok"]
