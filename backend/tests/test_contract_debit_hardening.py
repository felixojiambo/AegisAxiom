"""Wave 3 hardening — race-safety + approval-failure rollback tests.

Locks the four hardening fixes applied on top of commit 58e75b60:

  1. contract_debit writes the ledger row BEFORE the contract balance
     update so the unique entry_id index atomically claims the work
     before any contract hours move. Race-safety lock.
  2. Single-approve wraps the debit in try/except + rollback. A debit
     failure puts the entry back to "submitted" so retry is possible
     and surfaces a 502 to the caller instead of leaving the entry in
     a half-approved state.
  3. create_time_entry returns 409 when a billable entry is attached
     to a contract whose resource_types and hourly_rate are both
     absent (no sell rate to resolve). non_billable / fixed_fee
     entries are still allowed to save with missing rates.
  4. resource_type.hours_used / hours_remaining are NOT re-incremented
     on an idempotent re-approve (the original Wave 3 implementation
     would have doubled them).

Companion to test_contract_boh_with_boa_debit.py (which locks the
happy-path debit math) and test_time_entry_billing_snapshots.py
(which locks the create-time snapshot behavior).
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
# Fakes — record ordering across collections so we can assert that the
# ledger insert lands before the contract update.
# ---------------------------------------------------------------------------


class _OrderRecorder:
    """Shared event log so the test can assert ledger-insert happens
    strictly before contract-update in the apply path."""

    def __init__(self):
        self.events: list[str] = []

    def record(self, name: str) -> None:
        self.events.append(name)


class _FakeApplications:
    def __init__(self, recorder: _OrderRecorder, *, raise_duplicate: bool = False):
        self._rows: dict = {}
        self.inserts: list = []
        self._recorder = recorder
        self._raise_duplicate = raise_duplicate

    async def find_one(self, query, _projection=None):
        eid = query.get("entry_id")
        return dict(self._rows[eid]) if eid in self._rows else None

    async def insert_one(self, doc):
        self._recorder.record("ledger_insert")
        if self._raise_duplicate:
            raise RuntimeError("E11000 duplicate key error: entry_id")
        if doc["entry_id"] in self._rows:
            raise RuntimeError("E11000 duplicate key error: entry_id")
        self._rows[doc["entry_id"]] = dict(doc)
        self.inserts.append(dict(doc))
        return SimpleNamespace(inserted_id="x")


class _FakeContracts:
    def __init__(self, doc, recorder: _OrderRecorder, *, fail_update: bool = False):
        self._doc = dict(doc) if doc else None
        self.updates: list = []
        self._recorder = recorder
        self._fail_update = fail_update

    async def find_one(self, _query, _projection=None):
        return dict(self._doc) if isinstance(self._doc, dict) else None

    async def update_one(self, query, update):
        self._recorder.record("contract_update")
        if self._fail_update:
            raise RuntimeError("simulated contract update failure")
        self.updates.append((dict(query), dict(update)))
        if not isinstance(self._doc, dict):
            return SimpleNamespace(modified_count=0)
        if "$inc" in update:
            for k, v in update["$inc"].items():
                self._doc[k] = float(self._doc.get(k) or 0) + float(v)
        if "$set" in update:
            self._doc.update(update["$set"])
        return SimpleNamespace(modified_count=1)


def _entry(**overrides) -> dict:
    base = {
        "entry_id": "te_001",
        "contract_id": "c_001",
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
        "contract_id": "c_001",
        "contract_type": "boh_with_boa",
        "total_hours": 20.0,
        "hours_consumed": 18.0,
        "overage_hours": 0.0,
        "bill_and_actuals_active": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. Race-safety lock — ledger insert must precede contract update.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ledger_insert_happens_before_contract_update():
    recorder = _OrderRecorder()

    class _DB:
        pass
    db = _DB()
    db.contracts = _FakeContracts(_contract(), recorder)
    db.contract_time_applications = _FakeApplications(recorder)

    from services.contract_debit import apply_time_entry_to_contract
    result = await apply_time_entry_to_contract(
        db, time_entry=_entry(hours=5.0), contract=db.contracts._doc,
    )
    assert result["applied"] is True
    # The first event must be the ledger insert so the unique-key
    # claim happens before any contract balance moves. Reversing the
    # order would let a parallel approver also $inc the contract
    # before the loser's insert hits the unique constraint.
    insert_idx = recorder.events.index("ledger_insert")
    update_idx = recorder.events.index("contract_update")
    assert insert_idx < update_idx, recorder.events


@pytest.mark.asyncio
async def test_duplicate_key_race_does_not_touch_contract():
    """Simulate the race-loser path: the unique-key insert raises
    DuplicateKeyError. The service must NOT mutate the contract; the
    earlier successful approval already did."""
    recorder = _OrderRecorder()

    class _DB:
        pass
    db = _DB()
    db.contracts = _FakeContracts(_contract(), recorder)
    db.contract_time_applications = _FakeApplications(recorder, raise_duplicate=True)
    # Seed the existing row so the post-duplicate find_one returns
    # something — matches the race-loser scenario in production.
    db.contract_time_applications._rows["te_001"] = {
        "entry_id": "te_001",
        "block_hours": 2.0,
        "overage_hours": 3.0,
        "effective_billing_mode": "block_and_overflow",
    }

    from services.contract_debit import apply_time_entry_to_contract
    result = await apply_time_entry_to_contract(
        db, time_entry=_entry(hours=5.0), contract=db.contracts._doc,
    )
    assert result["applied"] is False
    assert result["already_applied"] is True
    # No contract_update event recorded — the contract was NOT touched
    # on the race-loser path.
    assert "contract_update" not in recorder.events, recorder.events


# ---------------------------------------------------------------------------
# 2. Single-approve rollback on debit failure.
# ---------------------------------------------------------------------------


class _FakeTimeEntries:
    def __init__(self, entry):
        self._entry = dict(entry)
        self.updates: list = []

    async def find_one(self, query, _projection=None):
        if "status" in query and self._entry.get("status") != query["status"]:
            return None
        return dict(self._entry)

    async def update_one(self, query, update):
        self.updates.append((dict(query), dict(update)))
        if "$set" in update:
            self._entry.update(update["$set"])
        return SimpleNamespace(modified_count=1)


@pytest.mark.asyncio
async def test_single_approve_rolls_back_on_debit_failure(monkeypatch):
    """When apply_time_entry_to_contract raises, the route must
    rollback the entry to 'submitted' and surface a 502 — leaving the
    entry in 'approved' with no ledger row would block retries."""
    submitted = _entry(status="submitted")
    time_entries = _FakeTimeEntries(submitted)

    class _DB:
        pass
    db = _DB()
    db.time_entries = time_entries
    db.contracts = SimpleNamespace(
        find_one=AsyncMock(return_value=_contract()),
        update_one=AsyncMock(return_value=SimpleNamespace(modified_count=1)),
    )
    db.contract_time_applications = SimpleNamespace(
        find_one=AsyncMock(return_value=None),
        insert_one=AsyncMock(side_effect=RuntimeError("forced for test")),
    )
    db.projects = SimpleNamespace(
        find_one=AsyncMock(return_value={"project_id": "proj_001", "pm_id": "u_pm"}),
        update_one=AsyncMock(return_value=SimpleNamespace(modified_count=0)),
    )

    import routes.time_entries as rt
    monkeypatch.setattr(rt, "get_db", lambda: db)
    monkeypatch.setattr(rt, "emit_event", AsyncMock(return_value=None))
    monkeypatch.setattr(rt, "log_action", AsyncMock(return_value=None))

    user = SimpleNamespace(
        user_id="u_pm", email="pm@aegisaxiom.test", role="director",
        permissions=["*"], tenant_id=None,
    )

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rt.approve_time_entry(entry_id="te_001", data={}, current_user=user)
    assert ei.value.status_code == 502

    # The entry must be back at submitted so the retry path can pick it up.
    assert time_entries._entry["status"] == "submitted"
    # Approval metadata cleared too.
    assert time_entries._entry.get("approved_by") is None
    assert time_entries._entry.get("approved_at") is None


# ---------------------------------------------------------------------------
# 3. 409 on billable + contract attached + missing sell rate.
# ---------------------------------------------------------------------------


def _make_create_db(monkeypatch, contracts_by_id):
    """Stand up the minimal DB stub create_time_entry needs."""
    inserts = []

    class _TE:
        async def insert_one(self, doc):
            inserts.append(dict(doc))
            return SimpleNamespace(inserted_id="x")

    class _Contracts:
        def __init__(self, by_id):
            self._by_id = by_id

        async def find_one(self, q, _p=None):
            return dict(self._by_id.get(q.get("contract_id"))) if q.get("contract_id") in self._by_id else None

    class _Projects:
        async def find_one(self, _q, _p=None):
            return None

    class _DB:
        pass

    db = _DB()
    db.time_entries = _TE()
    db.contracts = _Contracts(contracts_by_id)
    db.projects = _Projects()
    db.users = SimpleNamespace(find_one=AsyncMock(return_value=None))

    import routes.time_entries as rt
    monkeypatch.setattr(rt, "get_db", lambda: db)
    monkeypatch.setattr(rt, "log_action", AsyncMock(return_value=None))
    monkeypatch.setattr(rt, "emit_event", AsyncMock(return_value=None))

    async def _no_policy_block(_db, _mod, _ent, _action, _payload, user_role=None):
        return {
            "allowed": True,
            "blocked": False,
            "block_messages": [],
            "warnings": [],
            "required_fields_missing": [],
        }
    monkeypatch.setattr(rt, "evaluate_policies", _no_policy_block)

    async def _no_lock(_ws, _db):
        return False
    monkeypatch.setattr(rt, "_is_locked", _no_lock)

    def _good_quality(_notes):
        return {"quality": "good"}
    monkeypatch.setattr(rt, "_check_note_quality", _good_quality)

    return rt, inserts


def _user_for_create():
    return SimpleNamespace(
        user_id="u_engineer_001",
        email="eng@aegisaxiom.test",
        name="Engineer One",
        role="contractor",
        permissions=["time.entry"],
        tenant_id=None,
        account_id=None,
    )


_BASE_CREATE = {
    "date": "2026-05-18",
    "hours": 1.5,
    "notes": "Wave 3 hardening test — sell rate gate behavior.",
    "project_id": None,
}


@pytest.mark.asyncio
async def test_billable_with_missing_sell_rate_returns_409(monkeypatch):
    """Contract has no resource_types and no hourly_rate. Resolver
    returns sell=None, cost=None. Billable + contract attached →
    409 with an actionable message."""
    rt, inserts = _make_create_db(
        monkeypatch,
        {
            "c_missing": {
                "contract_id": "c_missing",
                "contract_type": "boh",
                # hourly_rate intentionally omitted — represents a
                # contract where no sell-rate source is configured.
                "resource_types": [],
                "resources": [],
            }
        },
    )
    payload = {**_BASE_CREATE, "contract_id": "c_missing", "is_billable": True}
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rt.create_time_entry(data=payload, current_user=_user_for_create())
    assert ei.value.status_code == 409
    assert "sell rate" in str(ei.value.detail).lower()
    assert inserts == []


@pytest.mark.asyncio
async def test_non_billable_with_missing_sell_rate_still_saves(monkeypatch):
    """non_billable bypasses the 409 — no AR contribution means no
    sell rate is required to save."""
    rt, inserts = _make_create_db(
        monkeypatch,
        {
            "c_missing": {
                "contract_id": "c_missing",
                "contract_type": "boh",
                # hourly_rate omitted — represents the "no sell rate
                # source configured" case so sell_rate_snapshot stays
                # None even on this non_billable save path.
                "resource_types": [],
                "resources": [],
            }
        },
    )
    payload = {
        **_BASE_CREATE,
        "contract_id": "c_missing",
        "is_billable": False,
        "billing_type": "non_billable",
    }
    await rt.create_time_entry(data=payload, current_user=_user_for_create())
    assert len(inserts) == 1
    assert inserts[0]["billing_type"] == "non_billable"
    assert inserts[0]["sell_rate_snapshot"] is None


@pytest.mark.asyncio
async def test_fixed_fee_with_missing_sell_rate_still_saves(monkeypatch):
    """fixed_fee bypasses the 409 — the rate frame is the contract's
    fixed amount, not a per-hour sell rate."""
    rt, inserts = _make_create_db(
        monkeypatch,
        {
            "c_ff": {
                "contract_id": "c_ff",
                "contract_type": "fixed_fee",
                "resource_types": [],
                "resources": [],
            }
        },
    )
    payload = {**_BASE_CREATE, "contract_id": "c_ff", "is_billable": True}
    await rt.create_time_entry(data=payload, current_user=_user_for_create())
    assert len(inserts) == 1
    assert inserts[0]["billing_type"] == "fixed_fee"


@pytest.mark.asyncio
async def test_billable_with_hourly_rate_fallback_does_not_409(monkeypatch):
    """Legacy single-rate contract (hourly_rate set, no resource_types)
    must still save — hourly_rate is a valid sell-rate fallback."""
    rt, inserts = _make_create_db(
        monkeypatch,
        {
            "c_legacy": {
                "contract_id": "c_legacy",
                "contract_type": "boh",
                "hourly_rate": 125.0,
                "resource_types": [],
                "resources": [],
            }
        },
    )
    payload = {**_BASE_CREATE, "contract_id": "c_legacy", "is_billable": True}
    await rt.create_time_entry(data=payload, current_user=_user_for_create())
    assert len(inserts) == 1
    assert inserts[0]["sell_rate_snapshot"] == 125.0


# ---------------------------------------------------------------------------
# 4. Idempotent re-approve does not double-bump resource_type hours_used.
# (Lock against the original Wave 3 bug where the resource_type
# sub-tracking incremented even on already_applied=True.)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotent_reapprove_does_not_double_bump_resource_type(monkeypatch):
    """If the same entry is approved a second time (or the route is
    retried), the resource_type's hours_used must not increment a
    second time. The single-approve route must gate the resource_type
    sync on debit_result.already_applied being False."""
    seeded_rt = {
        "id": "rt_eng",
        "hours_purchased": 40.0,
        "hours_used": 5.0,
        "hours_remaining": 35.0,
        "sell_rate": 180.0,
    }
    contract = _contract(
        contract_type="boh_with_boa",
        hours_consumed=5.0,
        resource_types=[dict(seeded_rt)],
    )
    submitted = _entry(status="submitted", hours=3.0)

    class _DB:
        pass
    db = _DB()
    db.time_entries = _FakeTimeEntries(submitted)
    db.contracts = SimpleNamespace(
        find_one=AsyncMock(return_value=dict(contract)),
        update_one=AsyncMock(return_value=SimpleNamespace(modified_count=1)),
    )
    # Simulate the idempotent short-circuit: a ledger row already
    # exists for this entry_id from a prior successful approval.
    prior_application = {
        "entry_id": "te_001",
        "block_hours": 3.0,
        "overage_hours": 0.0,
        "effective_billing_mode": "block",
    }
    db.contract_time_applications = SimpleNamespace(
        find_one=AsyncMock(return_value=prior_application),
        insert_one=AsyncMock(return_value=SimpleNamespace(inserted_id="x")),
    )
    db.projects = SimpleNamespace(
        find_one=AsyncMock(return_value={"project_id": "proj_001", "pm_id": "u_pm"}),
        update_one=AsyncMock(return_value=SimpleNamespace(modified_count=0)),
    )

    import routes.time_entries as rt
    monkeypatch.setattr(rt, "get_db", lambda: db)
    monkeypatch.setattr(rt, "emit_event", AsyncMock(return_value=None))
    monkeypatch.setattr(rt, "log_action", AsyncMock(return_value=None))

    user = SimpleNamespace(
        user_id="u_pm", email="pm@aegisaxiom.test", role="director",
        permissions=["*"], tenant_id=None,
    )
    await rt.approve_time_entry(entry_id="te_001", data={}, current_user=user)

    # The route must NOT have issued a resource_types $set with
    # hours_used bumped. If it did, the call would have set
    # resource_types in the contract update payload to a list with
    # hours_used == 8.0 (5 prior + 3 from this re-approve).
    rt_sets = [
        call.kwargs.get("update") or (call.args[1] if len(call.args) > 1 else {})
        for call in db.contracts.update_one.await_args_list
    ]
    for upd in rt_sets:
        set_doc = upd.get("$set") if isinstance(upd, dict) else {}
        if set_doc and "resource_types" in set_doc:
            for rt_doc in set_doc["resource_types"]:
                assert rt_doc.get("hours_used", 0) <= 5.0, (
                    f"resource_type.hours_used double-incremented to {rt_doc.get('hours_used')}"
                )
