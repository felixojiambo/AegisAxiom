"""Wave 3 (contracts-time-mgmt) — BOH-to-BOA debit + idempotent ledger.

Verifies the contract_debit service and its wiring into the
single-approve / bulk-approve routes:

  - Billable entry fully inside the remaining block debits block only.
  - Billable entry that crosses the block boundary splits block vs
    overage and flips bill_and_actuals_active.
  - Billable entry after exhaustion is overage-only.
  - Duplicate approval (same entry_id) does NOT double-debit.
  - non_billable entry writes a zero-row ledger and does not touch the
    contract.
  - fixed_fee entry writes a fixed_fee-mode ledger and does not touch
    the contract.
  - Bulk approval routes through the same shared debit path.
  - Legacy boh contract behavior is preserved (block only, no overage).
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


# ---------------------------------------------------------------------------
# Recording fakes — only the operations the service touches are wired.
# ---------------------------------------------------------------------------


class _FakeApplications:
    def __init__(self):
        self._rows: dict = {}  # entry_id -> row
        self.inserts: list = []

    async def find_one(self, query, _projection=None):
        eid = query.get("entry_id")
        return dict(self._rows[eid]) if eid in self._rows else None

    async def insert_one(self, doc):
        if doc["entry_id"] in self._rows:
            raise RuntimeError("E11000 duplicate key error: entry_id")
        self._rows[doc["entry_id"]] = dict(doc)
        self.inserts.append(dict(doc))
        return SimpleNamespace(inserted_id="x")


class _FakeContracts:
    def __init__(self, doc):
        self._doc = dict(doc) if doc else None
        self.updates: list = []

    async def find_one(self, _query, _projection=None):
        return dict(self._doc) if isinstance(self._doc, dict) else None

    async def update_one(self, query, update):
        self.updates.append((dict(query), dict(update)))
        if not isinstance(self._doc, dict):
            return SimpleNamespace(modified_count=0)
        if "$inc" in update:
            for k, v in update["$inc"].items():
                self._doc[k] = float(self._doc.get(k) or 0) + float(v)
        if "$set" in update:
            self._doc.update(update["$set"])
        return SimpleNamespace(modified_count=1)


class _FakeDB:
    def __init__(self, contract_doc):
        self.contracts = _FakeContracts(contract_doc)
        self.contract_time_applications = _FakeApplications()


def _entry(**overrides) -> dict:
    base = {
        "entry_id": "te_001",
        "contract_id": "c_boh_boa_001",
        "project_id": "proj_001",
        "user_id": "u_eng",
        "resource_type_id": "rt_eng",
        "date": "2026-05-18",
        "hours": 5.0,
        "billing_type": "billable",
        "is_billable": True,
        "cost_rate_snapshot": 95.0,
        "sell_rate_snapshot": 180.0,
        "status": "approved",
    }
    base.update(overrides)
    return base


def _contract(**overrides) -> dict:
    base = {
        "contract_id": "c_boh_boa_001",
        "contract_type": "boh_with_boa",
        "total_hours": 20.0,
        "hours_consumed": 0.0,
        "overage_hours": 0.0,
        "bill_and_actuals_active": False,
        "overage_bill_rate": 220.0,
        "overage_cost_rate": 110.0,
        "tenant_id": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# BOH-with-BOA split behavior.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_billable_entry_fully_inside_remaining_block_debits_block_only():
    db = _FakeDB(_contract(hours_consumed=10.0))
    from services.contract_debit import apply_time_entry_to_contract
    result = await apply_time_entry_to_contract(
        db, time_entry=_entry(hours=5.0), contract=db.contracts._doc,
    )
    assert result["applied"] is True
    assert result["block_hours"] == 5.0
    assert result["overage_hours"] == 0.0
    assert result["effective_billing_mode"] == "block"
    # Contract balances.
    assert db.contracts._doc["hours_consumed"] == 15.0
    assert db.contracts._doc.get("overage_hours", 0.0) == 0.0
    assert db.contracts._doc.get("bill_and_actuals_active") is False
    # Ledger row.
    appl = db.contract_time_applications.inserts[0]
    assert appl["entry_id"] == "te_001"
    assert appl["block_hours"] == 5.0
    assert appl["overage_hours"] == 0.0
    assert appl["block_cost"] == 5.0 * 95.0
    assert appl["block_revenue"] == 5.0 * 180.0


@pytest.mark.asyncio
async def test_billable_entry_crossing_boundary_splits_block_and_overage():
    """20-hour block, 18 consumed, new 5-hour entry → 2 block + 3 over."""
    db = _FakeDB(_contract(hours_consumed=18.0))
    from services.contract_debit import apply_time_entry_to_contract
    result = await apply_time_entry_to_contract(
        db, time_entry=_entry(hours=5.0), contract=db.contracts._doc,
    )
    assert result["block_hours"] == 2.0
    assert result["overage_hours"] == 3.0
    assert result["effective_billing_mode"] == "block_and_overflow"
    assert db.contracts._doc["hours_consumed"] == 20.0
    assert db.contracts._doc["overage_hours"] == 3.0
    assert db.contracts._doc["bill_and_actuals_active"] is True
    appl = db.contract_time_applications.inserts[0]
    assert appl["block_hours"] == 2.0
    assert appl["overage_hours"] == 3.0
    # Revenue uses overage_bill_rate (220) for overflow portion, sell snapshot (180) for block.
    assert appl["block_revenue"] == 2.0 * 180.0
    assert appl["overage_revenue"] == 3.0 * 220.0
    # Cost uses overage_cost_rate (110) for overflow, cost snapshot (95) for block.
    assert appl["block_cost"] == 2.0 * 95.0
    assert appl["overage_cost"] == 3.0 * 110.0


@pytest.mark.asyncio
async def test_billable_entry_after_block_exhaustion_is_overage_only():
    db = _FakeDB(_contract(hours_consumed=20.0, bill_and_actuals_active=True))
    from services.contract_debit import apply_time_entry_to_contract
    result = await apply_time_entry_to_contract(
        db, time_entry=_entry(hours=4.0), contract=db.contracts._doc,
    )
    assert result["block_hours"] == 0.0
    assert result["overage_hours"] == 4.0
    assert result["effective_billing_mode"] == "overflow"
    assert db.contracts._doc["hours_consumed"] == 20.0
    assert db.contracts._doc["overage_hours"] == 4.0
    assert db.contracts._doc["bill_and_actuals_active"] is True


@pytest.mark.asyncio
async def test_exact_block_exhaustion_flips_bill_and_actuals_active():
    """5 hours into a 15-remaining block exhausts exactly; flag flips
    even though overage is zero."""
    db = _FakeDB(_contract(hours_consumed=15.0, total_hours=20.0))
    from services.contract_debit import apply_time_entry_to_contract
    result = await apply_time_entry_to_contract(
        db, time_entry=_entry(hours=5.0), contract=db.contracts._doc,
    )
    assert result["block_hours"] == 5.0
    assert result["overage_hours"] == 0.0
    assert db.contracts._doc["hours_consumed"] == 20.0
    assert db.contracts._doc["bill_and_actuals_active"] is True


# ---------------------------------------------------------------------------
# Idempotency — duplicate approval, parallel race.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_approval_does_not_double_debit():
    db = _FakeDB(_contract(hours_consumed=10.0))
    from services.contract_debit import apply_time_entry_to_contract
    first = await apply_time_entry_to_contract(
        db, time_entry=_entry(hours=3.0), contract=db.contracts._doc,
    )
    assert first["applied"] is True
    assert db.contracts._doc["hours_consumed"] == 13.0

    second = await apply_time_entry_to_contract(
        db, time_entry=_entry(hours=3.0), contract=db.contracts._doc,
    )
    assert second["applied"] is False
    assert second["already_applied"] is True
    # Contract balance unchanged after second call.
    assert db.contracts._doc["hours_consumed"] == 13.0
    # Only one ledger row exists.
    assert len(db.contract_time_applications.inserts) == 1


# ---------------------------------------------------------------------------
# Non-debit billing types.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_billable_entry_writes_zero_ledger_and_no_debit():
    db = _FakeDB(_contract(hours_consumed=5.0))
    from services.contract_debit import apply_time_entry_to_contract
    result = await apply_time_entry_to_contract(
        db,
        time_entry=_entry(
            entry_id="te_nb", billing_type="non_billable", is_billable=False, hours=2.0,
        ),
        contract=db.contracts._doc,
    )
    assert result["effective_billing_mode"] == "non_billable"
    assert result["block_hours"] == 0.0
    assert result["overage_hours"] == 0.0
    # Contract balances untouched.
    assert db.contracts._doc["hours_consumed"] == 5.0
    assert db.contracts._doc.get("overage_hours", 0.0) == 0.0
    # No $inc was issued.
    assert all("$inc" not in u[1] for u in db.contracts.updates)
    # Ledger row exists for idempotency.
    assert len(db.contract_time_applications.inserts) == 1


@pytest.mark.asyncio
async def test_fixed_fee_entry_does_not_debit_block():
    db = _FakeDB(_contract(hours_consumed=5.0))
    from services.contract_debit import apply_time_entry_to_contract
    result = await apply_time_entry_to_contract(
        db,
        time_entry=_entry(entry_id="te_ff", billing_type="fixed_fee", hours=2.0),
        contract=db.contracts._doc,
    )
    assert result["effective_billing_mode"] == "fixed_fee"
    assert result["block_hours"] == 0.0
    assert db.contracts._doc["hours_consumed"] == 5.0
    appl = db.contract_time_applications.inserts[0]
    assert appl["billing_type"] == "fixed_fee"
    assert appl["block_hours"] == 0.0


# ---------------------------------------------------------------------------
# Legacy contract type regressions.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plain_boh_contract_debits_block_no_overage():
    db = _FakeDB(_contract(contract_type="boh", hours_consumed=5.0))
    from services.contract_debit import apply_time_entry_to_contract
    result = await apply_time_entry_to_contract(
        db, time_entry=_entry(hours=3.0), contract=db.contracts._doc,
    )
    assert result["effective_billing_mode"] == "block"
    assert result["block_hours"] == 3.0
    assert result["overage_hours"] == 0.0
    assert db.contracts._doc["hours_consumed"] == 8.0


@pytest.mark.asyncio
async def test_boa_contract_does_not_debit_block():
    db = _FakeDB(_contract(contract_type="boa", hours_consumed=0.0))
    from services.contract_debit import apply_time_entry_to_contract
    result = await apply_time_entry_to_contract(
        db, time_entry=_entry(hours=3.0), contract=db.contracts._doc,
    )
    assert result["effective_billing_mode"] == "bill_and_actuals"
    assert result["block_hours"] == 0.0
    assert db.contracts._doc["hours_consumed"] == 0.0


# ---------------------------------------------------------------------------
# Route wiring — single approve + bulk approve share the same path.
# ---------------------------------------------------------------------------


class _FakeTimeEntries:
    def __init__(self, entry):
        self._entry = dict(entry)
        self.updates: list = []

    async def find_one(self, query, _projection=None):
        # Both signatures used by the route:
        #  - find_one({"entry_id": eid}, ...)
        #  - find_one({"entry_id": eid, "status": "submitted"}, ...)
        if "status" in query and self._entry.get("status") != query["status"]:
            return None
        return dict(self._entry)

    async def update_one(self, query, update):
        self.updates.append((dict(query), dict(update)))
        if "$set" in update:
            self._entry.update(update["$set"])
        return SimpleNamespace(modified_count=1)


class _FakeProjectsCollection:
    async def find_one(self, _q, _p=None):
        return {"project_id": "proj_001", "pm_id": None}

    async def update_one(self, _q, _u):
        return SimpleNamespace(modified_count=0)


def _patch_route_db(monkeypatch, *, entry, contract):
    time_entries = _FakeTimeEntries(entry)
    contracts = _FakeContracts(contract)
    applications = _FakeApplications()
    projects = _FakeProjectsCollection()

    class _DB:
        pass
    db = _DB()
    db.time_entries = time_entries
    db.contracts = contracts
    db.contract_time_applications = applications
    db.projects = projects

    import routes.time_entries as rt
    monkeypatch.setattr(rt, "get_db", lambda: db)
    monkeypatch.setattr(rt, "emit_event", AsyncMock(return_value=None))
    monkeypatch.setattr(rt, "log_action", AsyncMock(return_value=None))
    return db, rt


@pytest.mark.asyncio
async def test_single_approve_route_drives_debit_service(monkeypatch):
    submitted_entry = _entry(status="submitted", hours=5.0)
    contract = _contract(hours_consumed=18.0)
    db, rt = _patch_route_db(monkeypatch, entry=submitted_entry, contract=contract)
    user = SimpleNamespace(
        user_id="u_pm", email="pm@aegisaxiom.test", role="director",
        permissions=["*"], tenant_id=None,
    )
    result = await rt.approve_time_entry(entry_id="te_001", data={}, current_user=user)
    assert result["effective_billing_mode"] == "block_and_overflow"
    assert db.contracts._doc["overage_hours"] == 3.0
    assert db.contracts._doc["bill_and_actuals_active"] is True
    assert len(db.contract_time_applications.inserts) == 1


@pytest.mark.asyncio
async def test_bulk_approve_route_uses_same_debit_path(monkeypatch):
    submitted_entry = _entry(status="submitted", hours=3.0)
    contract = _contract(hours_consumed=5.0)
    db, rt = _patch_route_db(monkeypatch, entry=submitted_entry, contract=contract)
    # Stub the AP-draft generator import — its full body is out of scope
    # for the debit test and requires more deps than this fake provides.
    import sys, types as _types
    fake_module = _types.ModuleType("services.finance_draft_autogen")

    async def _build_ap_drafts(**_kw):
        return ([], [], [])
    fake_module.build_ap_drafts = _build_ap_drafts
    # Use monkeypatch.setitem so the override is reverted at test
    # teardown — direct sys.modules assignment would leak the stub
    # into sibling test files that import build_ap_drafts (e.g.
    # test_ap_draft_uses_cost_rate_snapshot.py).
    monkeypatch.setitem(sys.modules, "services.finance_draft_autogen", fake_module)

    user = SimpleNamespace(
        user_id="u_pm", email="pm@aegisaxiom.test", role="director",
        permissions=["*"], tenant_id=None,
    )
    res = await rt.bulk_approve(data={"entry_ids": ["te_001"]}, current_user=user)
    assert res["count"] == 1
    assert db.contracts._doc["hours_consumed"] == 8.0
    assert len(db.contract_time_applications.inserts) == 1


@pytest.mark.asyncio
async def test_bulk_approve_is_idempotent_on_retry(monkeypatch):
    """Second bulk-approve call for the same entry_id must not
    double-debit; the ledger's unique constraint short-circuits."""
    submitted_entry = _entry(status="submitted", hours=2.0)
    contract = _contract(hours_consumed=5.0)
    db, rt = _patch_route_db(monkeypatch, entry=submitted_entry, contract=contract)

    import sys, types as _types
    fake_module = _types.ModuleType("services.finance_draft_autogen")

    async def _build_ap_drafts(**_kw):
        return ([], [], [])
    fake_module.build_ap_drafts = _build_ap_drafts
    # Use monkeypatch.setitem so the override is reverted at test
    # teardown — direct sys.modules assignment would leak the stub
    # into sibling test files that import build_ap_drafts (e.g.
    # test_ap_draft_uses_cost_rate_snapshot.py).
    monkeypatch.setitem(sys.modules, "services.finance_draft_autogen", fake_module)

    user = SimpleNamespace(
        user_id="u_pm", email="pm@aegisaxiom.test", role="director",
        permissions=["*"], tenant_id=None,
    )
    await rt.bulk_approve(data={"entry_ids": ["te_001"]}, current_user=user)
    initial_consumed = db.contracts._doc["hours_consumed"]
    # Force the entry status back to submitted to simulate the retry
    # path that the route's failure-rollback would have produced.
    db.time_entries._entry["status"] = "submitted"
    await rt.bulk_approve(data={"entry_ids": ["te_001"]}, current_user=user)
    # Contract balance unchanged on second debit attempt.
    assert db.contracts._doc["hours_consumed"] == initial_consumed
    # Still one ledger row.
    assert len(db.contract_time_applications.inserts) == 1
