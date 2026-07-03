"""Wave 3 (contracts-time-mgmt) — billing_type + rate snapshot locks.

Verifies the create-time behavior the Wave 3 route added on top of the
Wave 0 schema:

  - billing_type defaults from is_billable / contract_type when the
    caller does not supply it.
  - billing_type validates against the BILLING_TYPES allowlist
    (billable / non_billable / fixed_fee).
  - is_billable stays in sync with billing_type so legacy readers
    continue to work.
  - cost_rate_snapshot / sell_rate_snapshot are server-derived from
    contract.resources / contract.resource_types; client-supplied
    snapshots are ignored.
  - Snapshot resolver tolerates legacy contracts that lack resource
    sub-documents (falls back to contract.hourly_rate when present).
  - Weekly-summary helpers prefer the entry's snapshot over the
    contract's current rate so a rate change after approval does not
    retroactively rewrite AR/AP totals.

No live database is touched; the suite uses AsyncMock-shaped fakes in
the same style as ``test_opportunity_close_date.py`` and the Wave 1
test suites.
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
# Pure-function tests for services/time_entry_rates.
# ---------------------------------------------------------------------------


def test_resolver_prefers_resource_type_sell_rate():
    from services.time_entry_rates import resolve_rate_snapshots

    contract = {
        "contract_id": "c_1",
        "hourly_rate": 100.0,
        "resource_types": [
            {"id": "rt_eng", "sell_rate": 180.0, "hours_purchased": 40},
            {"id": "rt_pm", "sell_rate": 220.0, "hours_purchased": 10},
        ],
        "resources": [{"user_id": "u_a", "resource_type_id": "rt_eng", "cost_rate": 95.0}],
    }
    out = resolve_rate_snapshots(contract, user_id="u_a", resource_type_id="rt_eng")
    assert out["sell_rate_snapshot"] == 180.0
    assert out["cost_rate_snapshot"] == 95.0
    assert out["rate_source"]["sell"] == "contract.resource_types"
    assert out["rate_source"]["cost"] == "contract.resources"


def test_resolver_falls_back_to_hourly_rate_when_no_resource_type():
    from services.time_entry_rates import resolve_rate_snapshots

    contract = {"contract_id": "c_2", "hourly_rate": 125.0, "resource_types": [], "resources": []}
    out = resolve_rate_snapshots(contract, user_id="u_a", resource_type_id="rt_missing")
    assert out["sell_rate_snapshot"] == 125.0
    assert out["rate_source"]["sell"] == "contract.hourly_rate"
    assert out["cost_rate_snapshot"] is None
    assert out["rate_source"]["cost"] == "missing"


def test_resolver_matches_resource_by_user_id_when_resource_type_not_provided():
    from services.time_entry_rates import resolve_rate_snapshots

    contract = {
        "contract_id": "c_3",
        "hourly_rate": 100.0,
        "resource_types": [{"id": "rt_x", "sell_rate": 200.0}],
        "resources": [
            {"user_id": "u_a", "resource_type_id": "rt_x", "cost_rate": 80.0},
            {"user_id": "u_b", "resource_type_id": "rt_x", "cost_rate": 90.0},
        ],
    }
    out = resolve_rate_snapshots(contract, user_id="u_a", resource_type_id=None)
    assert out["cost_rate_snapshot"] == 80.0
    assert out["rate_source"]["cost"] == "contract.resources"


def test_resolver_returns_none_safely_for_empty_contract():
    from services.time_entry_rates import resolve_rate_snapshots

    out = resolve_rate_snapshots(None, user_id="u_a", resource_type_id="rt_a")
    assert out == {
        "cost_rate_snapshot": None,
        "sell_rate_snapshot": None,
        "rate_source": {"cost": "missing", "sell": "missing"},
    }


def test_effective_rate_helpers_prefer_snapshot_over_contract():
    from services.time_entry_rates import (
        entry_effective_cost_rate,
        entry_effective_sell_rate,
    )

    entry = {
        "user_id": "u_a",
        "resource_type_id": "rt_a",
        "cost_rate_snapshot": 95.0,
        "sell_rate_snapshot": 180.0,
    }
    # Contract carries a different (changed) live rate. The snapshot
    # must win — the whole point of the freeze is to insulate
    # already-approved time from a later rate edit.
    contract = {
        "hourly_rate": 999.0,
        "resource_types": [{"id": "rt_a", "sell_rate": 888.0}],
        "resources": [{"user_id": "u_a", "resource_type_id": "rt_a", "cost_rate": 777.0}],
    }
    assert entry_effective_cost_rate(entry, contract) == 95.0
    assert entry_effective_sell_rate(entry, contract) == 180.0


def test_effective_rate_helpers_fallback_for_legacy_rows():
    from services.time_entry_rates import (
        entry_effective_cost_rate,
        entry_effective_sell_rate,
    )

    legacy_entry = {
        "user_id": "u_a",
        "resource_type_id": "rt_a",
        # No snapshot fields, no entry-level cost_rate/sell_rate.
    }
    contract = {
        "hourly_rate": 130.0,
        "resource_types": [{"id": "rt_a", "sell_rate": 180.0}],
        "resources": [{"user_id": "u_a", "resource_type_id": "rt_a", "cost_rate": 95.0}],
    }
    assert entry_effective_cost_rate(legacy_entry, contract) == 95.0
    assert entry_effective_sell_rate(legacy_entry, contract) == 180.0


def test_effective_rate_helpers_use_direct_entry_fields_when_present():
    """Pre-Wave-0 entries carry cost_rate / sell_rate directly. The
    helpers fall back to those before going to the contract."""
    from services.time_entry_rates import (
        entry_effective_cost_rate,
        entry_effective_sell_rate,
    )

    entry = {"user_id": "u_a", "resource_type_id": "rt_a", "cost_rate": 70.0, "sell_rate": 150.0}
    contract = {
        "hourly_rate": 999.0,
        "resource_types": [{"id": "rt_a", "sell_rate": 888.0}],
        "resources": [{"user_id": "u_a", "resource_type_id": "rt_a", "cost_rate": 777.0}],
    }
    assert entry_effective_cost_rate(entry, contract) == 70.0
    assert entry_effective_sell_rate(entry, contract) == 150.0


# ---------------------------------------------------------------------------
# Route-level tests for create_time_entry billing_type + snapshot wiring.
# ---------------------------------------------------------------------------


class _FakeTimeEntries:
    def __init__(self):
        self.inserted: list = []

    async def insert_one(self, doc):
        self.inserted.append(dict(doc))
        return SimpleNamespace(inserted_id="x")


class _FakeContracts:
    def __init__(self, by_id):
        self._by_id = by_id

    async def find_one(self, query, _projection=None):
        cid = query.get("contract_id")
        doc = self._by_id.get(cid)
        return dict(doc) if doc else None


class _FakeProjects:
    async def find_one(self, _query, _projection=None):
        return None


class _FakeUsers:
    async def find_one(self, _query, _projection=None):
        return None


def _setup(monkeypatch, contracts_by_id=None):
    contracts_by_id = contracts_by_id or {}
    time_entries = _FakeTimeEntries()

    class _DB:
        pass
    db = _DB()
    db.time_entries = time_entries
    db.contracts = _FakeContracts(contracts_by_id)
    db.projects = _FakeProjects()
    db.users = _FakeUsers()

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

    return db, time_entries, rt


def _user(perms=("time.entry",)):
    return SimpleNamespace(
        user_id="u_engineer_001",
        email="eng@aegisaxiom.test",
        name="Engineer One",
        role="contractor",
        permissions=list(perms),
        tenant_id=None,
        account_id=None,
    )


_BASE_PAYLOAD = {
    "date": "2026-05-18",  # arbitrary recent date
    "hours": 1.5,
    "notes": "Wave 3 work — testing the new billing_type defaulting and snapshot resolver behavior.",
    "project_id": None,
    "contract_id": None,
}


@pytest.mark.asyncio
async def test_create_defaults_billing_type_to_billable(monkeypatch):
    _, time_entries, rt = _setup(monkeypatch)
    payload = {**_BASE_PAYLOAD, "is_billable": True}
    await rt.create_time_entry(data=payload, current_user=_user())
    doc = time_entries.inserted[0]
    assert doc["billing_type"] == "billable"
    assert doc["is_billable"] is True


@pytest.mark.asyncio
async def test_create_defaults_billing_type_non_billable_when_is_billable_false(monkeypatch):
    _, time_entries, rt = _setup(monkeypatch)
    payload = {**_BASE_PAYLOAD, "is_billable": False}
    await rt.create_time_entry(data=payload, current_user=_user())
    doc = time_entries.inserted[0]
    assert doc["billing_type"] == "non_billable"
    assert doc["is_billable"] is False


@pytest.mark.asyncio
async def test_create_defaults_fixed_fee_when_contract_is_fixed_fee(monkeypatch):
    _, time_entries, rt = _setup(
        monkeypatch,
        contracts_by_id={
            "c_ff": {
                "contract_id": "c_ff",
                "contract_type": "fixed_fee",
                "resource_types": [],
                "resources": [],
            }
        },
    )
    payload = {**_BASE_PAYLOAD, "contract_id": "c_ff", "is_billable": True}
    await rt.create_time_entry(data=payload, current_user=_user())
    doc = time_entries.inserted[0]
    assert doc["billing_type"] == "fixed_fee"
    # Fixed-fee still considered billable (it pays); only non_billable flips this.
    assert doc["is_billable"] is True


@pytest.mark.asyncio
async def test_create_validates_billing_type_allowlist(monkeypatch):
    _, time_entries, rt = _setup(monkeypatch)
    payload = {**_BASE_PAYLOAD, "billing_type": "marketing"}
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rt.create_time_entry(data=payload, current_user=_user())
    assert ei.value.status_code == 400
    assert "billing_type" in str(ei.value.detail).lower()
    assert time_entries.inserted == []


@pytest.mark.asyncio
async def test_create_ignores_client_supplied_snapshots_and_uses_contract(monkeypatch):
    """A client that tries to pin the entry to off-contract rates must
    be overridden by the server-derived snapshot pair."""
    _, time_entries, rt = _setup(
        monkeypatch,
        contracts_by_id={
            "c_snap": {
                "contract_id": "c_snap",
                "contract_type": "boh_with_boa",
                "hourly_rate": 100.0,
                "resource_types": [{"id": "rt_eng", "sell_rate": 180.0}],
                "resources": [{"user_id": "u_engineer_001", "resource_type_id": "rt_eng", "cost_rate": 95.0}],
            }
        },
    )
    payload = {
        **_BASE_PAYLOAD,
        "contract_id": "c_snap",
        "resource_type_id": "rt_eng",
        # Malicious / buggy values the server must ignore.
        "cost_rate_snapshot": 1.0,
        "sell_rate_snapshot": 999.99,
    }
    await rt.create_time_entry(data=payload, current_user=_user())
    doc = time_entries.inserted[0]
    assert doc["cost_rate_snapshot"] == 95.0
    assert doc["sell_rate_snapshot"] == 180.0


@pytest.mark.asyncio
async def test_create_snapshot_falls_back_to_hourly_rate(monkeypatch):
    _, time_entries, rt = _setup(
        monkeypatch,
        contracts_by_id={
            "c_legacy": {
                "contract_id": "c_legacy",
                "contract_type": "boh",
                "hourly_rate": 125.0,
                "resource_types": [],
                "resources": [],
            }
        },
    )
    payload = {**_BASE_PAYLOAD, "contract_id": "c_legacy", "resource_type_id": None}
    await rt.create_time_entry(data=payload, current_user=_user())
    doc = time_entries.inserted[0]
    assert doc["sell_rate_snapshot"] == 125.0
    # No matching resource entry → cost snapshot is None.
    assert doc["cost_rate_snapshot"] is None


@pytest.mark.asyncio
async def test_create_without_contract_leaves_snapshots_null(monkeypatch):
    _, time_entries, rt = _setup(monkeypatch)
    await rt.create_time_entry(data=_BASE_PAYLOAD, current_user=_user())
    doc = time_entries.inserted[0]
    assert doc["cost_rate_snapshot"] is None
    assert doc["sell_rate_snapshot"] is None
    # billing_type still derives — no contract means billable default.
    assert doc["billing_type"] == "billable"
