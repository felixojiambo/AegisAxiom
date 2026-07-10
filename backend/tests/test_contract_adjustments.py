"""Wave 4 (contracts-time-mgmt) — contract adjustment route tests.

Locks the credit/debit math and audit-row shape for the four
adjustment kinds plus tenant scoping and validation behavior.

Coverage:
  - hours_credit reduces overage_hours first then hours_consumed.
  - hours_credit can flip bill_and_actuals_active back off when
    overage clears and consumed drops below total.
  - hours_debit on boh_with_boa splits block vs overflow.
  - hours_debit on legacy boh increments hours_consumed only.
  - billing_credit reduces financial_commit and refuses to go below 0.
  - billing_credit 409s when financial_commit is unset.
  - billing_debit increases financial_commit; allows seeding when
    financial_commit was unset.
  - Adjustment row carries before / after snapshot, kind, amount,
    reason_code, actor_id, contract_id, and tenant_id when present.
  - Invalid kind / reason_code / amount returns 400.
  - Missing linked_time_entry_id returns 404.
  - Cross-tenant contract returns 404.
  - Trigger evaluator runs after the adjustment commits; its failure
    does not roll the adjustment back.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


def _user(role="director", perms=None, tenant_id=None):
    return SimpleNamespace(
        user_id="u_director_001",
        email="director@aegisaxiom.test",
        role=role,
        permissions=list(perms or ["contracts.approve", "*"]),
        tenant_id=tenant_id,
        account_id=None,
    )


def _contract(**overrides):
    base = {
        "contract_id": "c_001",
        "account_id": "acct_001",
        "contract_type": "boh_with_boa",
        "total_hours": 20.0,
        "hours_consumed": 18.0,
        "overage_hours": 3.0,
        "bill_and_actuals_active": True,
        "financial_commit": 5000.0,
        "tenant_id": None,
    }
    base.update(overrides)
    return base


class _FakeContracts:
    def __init__(self, doc):
        self._doc = dict(doc) if doc else None
        self.updates: list = []

    async def find_one(self, _query, _projection=None):
        return dict(self._doc) if isinstance(self._doc, dict) else None

    async def update_one(self, query, update):
        self.updates.append((dict(query), dict(update)))
        if isinstance(self._doc, dict) and "$set" in update:
            self._doc.update(update["$set"])
        return SimpleNamespace(modified_count=1)


class _FakeAdjustments:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.inserts: list = []

    def find(self, query, _projection=None):
        results = [r for r in self._rows if all(r.get(k) == v for k, v in query.items())]

        class _C:
            def __init__(s, items):
                s._items = items

            def sort(s, *_a, **_kw):
                return s

            async def to_list(s, _n):
                return list(s._items)
        return _C(results)

    async def count_documents(self, query):
        return sum(
            1 for r in self._rows
            if all(r.get(k) == v for k, v in query.items())
        )

    async def insert_one(self, doc):
        self.inserts.append(dict(doc))
        self._rows.append(dict(doc))
        return SimpleNamespace(inserted_id="x")


class _FakeTimeEntries:
    def __init__(self, by_id=None):
        self._by_id = dict(by_id or {})

    async def find_one(self, query, _projection=None):
        eid = query.get("entry_id")
        return dict(self._by_id[eid]) if eid in self._by_id else None


def _setup(monkeypatch, contract, *, time_entries_by_id=None, triggers=None):
    contracts = _FakeContracts(contract)
    adjustments = _FakeAdjustments()
    time_entries = _FakeTimeEntries(time_entries_by_id or {})
    triggers_collection = SimpleNamespace(
        find=lambda *_a, **_kw: _AsyncCursor([]),
        update_one=AsyncMock(return_value=None),
    )

    class _DB:
        pass
    db = _DB()
    db.contracts = contracts
    db.contract_adjustments = adjustments
    db.time_entries = time_entries
    db.contract_billing_triggers = triggers_collection

    import routes.contract_adjustments as ca
    monkeypatch.setattr(ca, "get_db", lambda: db)
    monkeypatch.setattr(ca, "log_action", AsyncMock(return_value=None))
    monkeypatch.setattr(ca, "emit_event", AsyncMock(return_value=None))

    # The route now wraps mutations in crud_envelope which reaches into
    # policy_engine + event_bus + audit. None of those are set up in
    # this unit harness; substitute with a no-op async context manager.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_envelope(*_args, **_kwargs):
        class _Ctx:
            after = None
            resource_id = _kwargs.get("resource_id")
            def set_after(self, doc): self.after = doc
            def set_resource_id(self, rid): self.resource_id = rid
        yield _Ctx()
    monkeypatch.setattr(ca, "crud_envelope", _fake_envelope)

    async def _empty_filter(_u):
        return {}
    monkeypatch.setattr(ca, "get_contract_filter", _empty_filter)

    return db, contracts, adjustments, ca


class _AsyncCursor:
    def __init__(self, items):
        self._items = items

    async def to_list(self, _n):
        return list(self._items)


# ---------------------------------------------------------------------------
# hours_credit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hours_credit_reduces_overage_first(monkeypatch):
    db, contracts, adjustments, ca = _setup(monkeypatch, _contract())
    result = await ca.create_contract_adjustment(
        contract_id="c_001",
        data={"kind": "hours_credit", "amount": 2.0, "reason_code": "customer_credit"},
        current_user=_user(),
    )
    # Contract state: 18 consumed, 3 overage. Credit 2 → overage drops to 1.
    assert contracts._doc["overage_hours"] == 1.0
    assert contracts._doc["hours_consumed"] == 18.0
    # bill_and_actuals_active stays True because overage still > 0.
    assert contracts._doc["bill_and_actuals_active"] is True
    # Audit row carries before/after.
    row = adjustments.inserts[0]
    assert row["kind"] == "hours_credit"
    assert row["before"]["overage_hours"] == 3.0
    assert row["after"]["overage_hours"] == 1.0


@pytest.mark.asyncio
async def test_hours_credit_dips_into_consumed_after_overage_clears(monkeypatch):
    db, contracts, adjustments, ca = _setup(monkeypatch, _contract())
    await ca.create_contract_adjustment(
        contract_id="c_001",
        data={"kind": "hours_credit", "amount": 5.0, "reason_code": "correction"},
        current_user=_user(),
    )
    # 3 overage cleared; remaining 2 dips into consumed (18 → 16).
    assert contracts._doc["overage_hours"] == 0.0
    assert contracts._doc["hours_consumed"] == 16.0
    # Overage cleared AND consumed (16) < total (20) → flag flips off.
    assert contracts._doc["bill_and_actuals_active"] is False


@pytest.mark.asyncio
async def test_hours_credit_keeps_flag_when_partial_returns_overage(monkeypatch):
    """Credit covers part of overage but some remains. Flag stays on."""
    db, contracts, adjustments, ca = _setup(monkeypatch, _contract())
    await ca.create_contract_adjustment(
        contract_id="c_001",
        data={"kind": "hours_credit", "amount": 1.0, "reason_code": "correction"},
        current_user=_user(),
    )
    assert contracts._doc["overage_hours"] == 2.0
    assert contracts._doc["bill_and_actuals_active"] is True


# ---------------------------------------------------------------------------
# hours_debit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hours_debit_on_boh_with_boa_splits(monkeypatch):
    """Contract: 18 of 20 consumed, 0 overage. Debit 5 → block 2 + overflow 3."""
    db, contracts, adjustments, ca = _setup(
        monkeypatch,
        _contract(overage_hours=0.0, bill_and_actuals_active=False),
    )
    await ca.create_contract_adjustment(
        contract_id="c_001",
        data={"kind": "hours_debit", "amount": 5.0, "reason_code": "late_timesheet"},
        current_user=_user(),
    )
    assert contracts._doc["hours_consumed"] == 20.0
    assert contracts._doc["overage_hours"] == 3.0
    assert contracts._doc["bill_and_actuals_active"] is True


@pytest.mark.asyncio
async def test_hours_debit_on_legacy_boh_increments_consumed_only(monkeypatch):
    db, contracts, adjustments, ca = _setup(
        monkeypatch,
        _contract(contract_type="boh", overage_hours=0.0, bill_and_actuals_active=False, hours_consumed=10.0),
    )
    await ca.create_contract_adjustment(
        contract_id="c_001",
        data={"kind": "hours_debit", "amount": 3.0, "reason_code": "late_timesheet"},
        current_user=_user(),
    )
    assert contracts._doc["hours_consumed"] == 13.0
    assert contracts._doc.get("overage_hours", 0) == 0.0


# ---------------------------------------------------------------------------
# billing_credit / billing_debit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_billing_credit_reduces_financial_commit(monkeypatch):
    db, contracts, adjustments, ca = _setup(monkeypatch, _contract())
    await ca.create_contract_adjustment(
        contract_id="c_001",
        data={"kind": "billing_credit", "amount": 1500.0, "reason_code": "customer_credit"},
        current_user=_user(),
    )
    assert contracts._doc["financial_commit"] == 3500.0


@pytest.mark.asyncio
async def test_billing_credit_clamps_at_zero(monkeypatch):
    db, contracts, adjustments, ca = _setup(
        monkeypatch, _contract(financial_commit=1000.0),
    )
    await ca.create_contract_adjustment(
        contract_id="c_001",
        data={"kind": "billing_credit", "amount": 2500.0, "reason_code": "manual_reconcile"},
        current_user=_user(),
    )
    # Cannot go below 0; floor at 0.0.
    assert contracts._doc["financial_commit"] == 0.0


@pytest.mark.asyncio
async def test_billing_credit_without_financial_commit_returns_409(monkeypatch):
    db, contracts, adjustments, ca = _setup(
        monkeypatch, _contract(financial_commit=None),
    )
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ca.create_contract_adjustment(
            contract_id="c_001",
            data={"kind": "billing_credit", "amount": 500.0, "reason_code": "correction"},
            current_user=_user(),
        )
    assert ei.value.status_code == 409
    assert "financial_commit" in str(ei.value.detail)


@pytest.mark.asyncio
async def test_billing_debit_increases_financial_commit(monkeypatch):
    db, contracts, adjustments, ca = _setup(monkeypatch, _contract())
    await ca.create_contract_adjustment(
        contract_id="c_001",
        data={"kind": "billing_debit", "amount": 750.0, "reason_code": "scope_change"},
        current_user=_user(),
    )
    assert contracts._doc["financial_commit"] == 5750.0


@pytest.mark.asyncio
async def test_billing_debit_seeds_when_financial_commit_unset(monkeypatch):
    db, contracts, adjustments, ca = _setup(
        monkeypatch, _contract(financial_commit=None),
    )
    await ca.create_contract_adjustment(
        contract_id="c_001",
        data={"kind": "billing_debit", "amount": 2500.0, "reason_code": "scope_change"},
        current_user=_user(),
    )
    assert contracts._doc["financial_commit"] == 2500.0


# ---------------------------------------------------------------------------
# Audit row shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adjustment_row_contains_required_fields(monkeypatch):
    db, contracts, adjustments, ca = _setup(monkeypatch, _contract(tenant_id="tenant_a"))
    await ca.create_contract_adjustment(
        contract_id="c_001",
        data={
            "kind": "hours_credit",
            "amount": 1.0,
            "reason_code": "goodwill",
            "reason_note": "Returning a stray hour from a duplicate entry.",
        },
        current_user=_user(),
    )
    row = adjustments.inserts[0]
    assert row["adjustment_id"].startswith("adj_")
    assert row["contract_id"] == "c_001"
    assert row["kind"] == "hours_credit"
    assert row["amount"] == 1.0
    assert row["reason_code"] == "goodwill"
    assert row["reason_note"] == "Returning a stray hour from a duplicate entry."
    assert row["actor_id"] == "u_director_001"
    assert row["actor_email"] == "director@aegisaxiom.test"
    assert row["tenant_id"] == "tenant_a"
    assert "before" in row and "after" in row
    for k in ("hours_consumed", "overage_hours", "bill_and_actuals_active", "total_hours", "financial_commit"):
        assert k in row["before"]
        assert k in row["after"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_kind_returns_400(monkeypatch):
    db, contracts, adjustments, ca = _setup(monkeypatch, _contract())
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ca.create_contract_adjustment(
            contract_id="c_001",
            data={"kind": "freebie", "amount": 1.0, "reason_code": "goodwill"},
            current_user=_user(),
        )
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_invalid_reason_code_returns_400(monkeypatch):
    db, contracts, adjustments, ca = _setup(monkeypatch, _contract())
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ca.create_contract_adjustment(
            contract_id="c_001",
            data={"kind": "hours_credit", "amount": 1.0, "reason_code": "ad_hoc"},
            current_user=_user(),
        )
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_invalid_amount_returns_400(monkeypatch):
    db, contracts, adjustments, ca = _setup(monkeypatch, _contract())
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ca.create_contract_adjustment(
            contract_id="c_001",
            data={"kind": "hours_credit", "amount": -1.0, "reason_code": "goodwill"},
            current_user=_user(),
        )
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_missing_linked_time_entry_returns_404(monkeypatch):
    db, contracts, adjustments, ca = _setup(
        monkeypatch, _contract(), time_entries_by_id={},
    )
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ca.create_contract_adjustment(
            contract_id="c_001",
            data={
                "kind": "hours_credit",
                "amount": 1.0,
                "reason_code": "late_timesheet",
                "linked_time_entry_id": "te_missing",
            },
            current_user=_user(),
        )
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_linked_time_entry_id_persisted(monkeypatch):
    db, contracts, adjustments, ca = _setup(
        monkeypatch,
        _contract(),
        time_entries_by_id={"te_present": {"entry_id": "te_present"}},
    )
    await ca.create_contract_adjustment(
        contract_id="c_001",
        data={
            "kind": "hours_credit",
            "amount": 1.0,
            "reason_code": "late_timesheet",
            "linked_time_entry_id": "te_present",
        },
        current_user=_user(),
    )
    row = adjustments.inserts[0]
    assert row["linked_time_entry_id"] == "te_present"


@pytest.mark.asyncio
async def test_cross_tenant_contract_returns_404(monkeypatch):
    db, contracts, adjustments, ca = _setup(
        monkeypatch, _contract(tenant_id="tenant_a"),
    )
    cross_user = _user(role="director", tenant_id="tenant_b")
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ca.create_contract_adjustment(
            contract_id="c_001",
            data={"kind": "hours_credit", "amount": 1.0, "reason_code": "correction"},
            current_user=cross_user,
        )
    assert ei.value.status_code == 404


# ---------------------------------------------------------------------------
# Trigger evaluator wired and failure-isolated.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adjustment_invokes_trigger_evaluator(monkeypatch):
    db, contracts, adjustments, ca = _setup(monkeypatch, _contract())
    captured: dict = {}

    async def _stub_evaluator(_db, contract_doc, *, actor_user_id=None):
        captured["contract_id"] = contract_doc.get("contract_id")
        return {"contract_id": contract_doc.get("contract_id"), "fired": [], "skipped": []}

    monkeypatch.setattr(ca, "evaluate_contract_billing_triggers", _stub_evaluator)
    result = await ca.create_contract_adjustment(
        contract_id="c_001",
        data={"kind": "hours_credit", "amount": 1.0, "reason_code": "correction"},
        current_user=_user(),
    )
    assert captured["contract_id"] == "c_001"
    assert "triggers" in result


@pytest.mark.asyncio
async def test_evaluator_failure_does_not_roll_back_adjustment(monkeypatch):
    db, contracts, adjustments, ca = _setup(monkeypatch, _contract())

    async def _boom(_db, _c, *, actor_user_id=None):
        raise RuntimeError("simulated evaluator failure")

    monkeypatch.setattr(ca, "evaluate_contract_billing_triggers", _boom)
    # Adjustment must still succeed.
    result = await ca.create_contract_adjustment(
        contract_id="c_001",
        data={"kind": "hours_credit", "amount": 1.0, "reason_code": "correction"},
        current_user=_user(),
    )
    # Contract state was mutated.
    assert contracts._doc["overage_hours"] == 2.0
    # Audit row was written.
    assert len(adjustments.inserts) == 1
    # Response carries the adjustment but NOT a triggers section since
    # the evaluator failed.
    assert "adjustment" in result
    assert "triggers" not in result


# ---------------------------------------------------------------------------
# Permission gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_requires_approve_or_edit_all(monkeypatch):
    db, contracts, adjustments, ca = _setup(monkeypatch, _contract())
    sales_rep = _user(role="sales_rep", perms=["contracts.view.own", "contracts.edit"])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await ca.create_contract_adjustment(
            contract_id="c_001",
            data={"kind": "hours_credit", "amount": 1.0, "reason_code": "correction"},
            current_user=sales_rep,
        )
    assert ei.value.status_code == 403
