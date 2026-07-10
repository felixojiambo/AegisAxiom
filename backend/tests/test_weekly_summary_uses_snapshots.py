"""Wave 3 hardening — weekly_summary AR uses rate snapshots.

The pre-hardening weekly_summary route grouped time entries by
contract_id with an aggregation pipeline and then enriched AR rows
with ``total_hours * contract.hourly_rate``. That meant a post-approval
rate edit on the contract retroactively rewrote AR totals for already-
approved entries — defeating the entire point of the Wave 0
sell_rate_snapshot field.

This test exercises the patched code path: the route now reads each
entry individually and sums ``hours * entry_effective_sell_rate(entry,
contract)``, which prefers the snapshot when present and falls back to
the live contract rate only for legacy rows.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


class _FakeCursor:
    def __init__(self, items):
        self._items = items

    def sort(self, *_a, **_kw):
        return self

    async def to_list(self, _n):
        return list(self._items)


class _FakeTimeEntries:
    def __init__(self, entries):
        self._entries = list(entries)

    def find(self, query, _projection=None):
        # Minimal predicate matching — supports week_start/status/
        # is_billable/contract_id={'$ne': None} as the route emits.
        results = []
        for e in self._entries:
            if "week_start" in query and e.get("week_start") != query["week_start"]:
                continue
            if "status" in query and e.get("status") != query["status"]:
                continue
            if query.get("is_billable") is True and not e.get("is_billable"):
                continue
            if "contract_id" in query and isinstance(query["contract_id"], dict):
                if query["contract_id"].get("$ne") is None and e.get("contract_id") is None:
                    continue
            results.append(dict(e))
        return _FakeCursor(results)

    def aggregate(self, _pipeline):
        # AP path uses aggregation. Return an empty cursor so the AP
        # branch produces no rows — this test focuses on AR only.
        return _FakeCursor([])


class _FakeContracts:
    def __init__(self, by_id):
        self._by_id = by_id

    async def find_one(self, query, _projection=None):
        cid = query.get("contract_id")
        return dict(self._by_id[cid]) if cid in self._by_id else None


def _setup(monkeypatch, *, entries, contracts):
    class _DB:
        pass
    db = _DB()
    db.time_entries = _FakeTimeEntries(entries)
    db.contracts = _FakeContracts(contracts)

    import routes.time_entries as rt
    monkeypatch.setattr(rt, "get_db", lambda: db)
    return rt


_FINANCE_USER = SimpleNamespace(
    user_id="u_finance",
    email="finance@aegisaxiom.test",
    role="finance",
    permissions=["finance.portal"],
    tenant_id=None,
)


@pytest.mark.asyncio
async def test_weekly_summary_ar_prefers_snapshot_over_live_rate(monkeypatch):
    """The snapshot is 180/hr; the contract's live hourly_rate has
    since been edited to 999. Total amount must reflect the snapshot
    (180), not the edited live rate (999)."""
    entries = [
        {
            "entry_id": "te_a",
            "contract_id": "c_boa",
            "week_start": "2026-05-18",
            "status": "approved",
            "is_billable": True,
            "hours": 4.0,
            "sell_rate_snapshot": 180.0,
            "user_id": "u_a",
            "resource_type_id": "rt_eng",
        },
        {
            "entry_id": "te_b",
            "contract_id": "c_boa",
            "week_start": "2026-05-18",
            "status": "approved",
            "is_billable": True,
            "hours": 6.0,
            "sell_rate_snapshot": 200.0,
            "user_id": "u_b",
            "resource_type_id": "rt_pm",
        },
    ]
    contracts = {
        "c_boa": {
            "contract_id": "c_boa",
            "contract_type": "boa",
            "name": "Acme BOA",
            "account_name": "Acme",
            "hourly_rate": 999.0,  # live rate changed AFTER approval
            "billing_period": "weekly",
            "invoice_terms": "net_30",
            "resource_types": [],
            "resources": [],
        }
    }
    rt = _setup(monkeypatch, entries=entries, contracts=contracts)
    result = await rt.weekly_summary(
        week_start="2026-05-18", is_test_data=None, current_user=_FINANCE_USER,
    )
    ar_items = result["accounts_receivable"]["items"]
    assert len(ar_items) == 1
    # 4h * 180 + 6h * 200 = 720 + 1200 = 1920 (NOT 10h * 999 = 9990).
    assert ar_items[0]["total_amount"] == 1920.0
    assert ar_items[0]["total_hours"] == 10.0


@pytest.mark.asyncio
async def test_weekly_summary_ar_falls_back_to_live_rate_for_legacy_rows(monkeypatch):
    """Pre-Wave-0 entry without sell_rate_snapshot must still produce
    AR — falling back to contract.hourly_rate. This is the legacy-row
    safety net."""
    entries = [
        {
            "entry_id": "te_legacy",
            "contract_id": "c_legacy",
            "week_start": "2026-05-18",
            "status": "approved",
            "is_billable": True,
            "hours": 5.0,
            # No sell_rate_snapshot, no cost_rate_snapshot.
            "user_id": "u_a",
            "resource_type_id": "rt_eng",
        },
    ]
    contracts = {
        "c_legacy": {
            "contract_id": "c_legacy",
            "contract_type": "boa",
            "name": "Legacy BOA",
            "account_name": "OldCo",
            "hourly_rate": 150.0,
            "resource_types": [],
            "resources": [],
        }
    }
    rt = _setup(monkeypatch, entries=entries, contracts=contracts)
    result = await rt.weekly_summary(
        week_start="2026-05-18", is_test_data=None, current_user=_FINANCE_USER,
    )
    ar_items = result["accounts_receivable"]["items"]
    assert len(ar_items) == 1
    assert ar_items[0]["total_amount"] == 750.0  # 5h * 150


@pytest.mark.asyncio
async def test_weekly_summary_ar_uses_resource_type_rate_when_snapshot_missing(monkeypatch):
    """Legacy entry without snapshot, contract has resource_types with
    sell_rate — fallback should hit resource_type before hourly_rate."""
    entries = [
        {
            "entry_id": "te_rt",
            "contract_id": "c_rt",
            "week_start": "2026-05-18",
            "status": "approved",
            "is_billable": True,
            "hours": 3.0,
            "user_id": "u_a",
            "resource_type_id": "rt_eng",
        },
    ]
    contracts = {
        "c_rt": {
            "contract_id": "c_rt",
            "contract_type": "boa",
            "name": "RT BOA",
            "account_name": "RTCo",
            "hourly_rate": 999.0,  # would over-state if used incorrectly
            "resource_types": [{"id": "rt_eng", "sell_rate": 175.0}],
            "resources": [],
        }
    }
    rt = _setup(monkeypatch, entries=entries, contracts=contracts)
    result = await rt.weekly_summary(
        week_start="2026-05-18", is_test_data=None, current_user=_FINANCE_USER,
    )
    ar_items = result["accounts_receivable"]["items"]
    # resource_type sell_rate (175) wins over the inflated hourly_rate
    # — exactly the insulation the snapshot/resource-rate preference
    # provides.
    assert ar_items[0]["total_amount"] == 525.0  # 3h * 175
