"""Wave 4 hardening — AP draft cost-rate snapshot lock.

Regression test for the Wave 4 surgical fix in
``services/finance_draft_autogen.py``: AP draft line items must read
``entry.cost_rate_snapshot`` (frozen at create time) in preference to
the legacy ``entry.cost_rate`` field. Wave 3 created entries write
the snapshot field but leave ``cost_rate`` unset, so a regression
that drops the snapshot preference would silently zero out the AP
totals for every Wave-3-shaped entry.

Pinning behavior:
  - Snapshot present, legacy missing → line item rate uses snapshot.
  - Snapshot present, legacy also set → snapshot wins.
  - Snapshot None, legacy set → falls back to legacy (back-compat).
  - Snapshot None and legacy 0 → rate is 0 (no implicit fallback).

The suite uses dictionary-shaped fakes for ``db["..."]`` matching the
real Motor surface that ``build_ap_drafts`` touches:
  - ``time_entries.find(query).to_list(n)``
  - ``users.find_one({"user_id": uid})``
  - ``projects.find_one({"project_id": pid})``
  - ``finance_drafts.find_one(dedup_query)`` returns None
  - ``finance_drafts.insert_one(draft)`` records the insert
  - ``contractor_businesses.find_one`` is only hit when the entry's
    user has a contractor_business_id — left absent for these tests.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest

from fake_user_repo import patched_load_user_map, set_current_user_map


os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


# ---------------------------------------------------------------------------
# Minimal Motor-shaped fakes.
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, items: List[Dict[str, Any]]):
        self._items = items

    async def to_list(self, _n: int):
        return list(self._items)


class _TimeEntries:
    def __init__(self, entries):
        self._entries = list(entries)

    def find(self, _query, _projection=None):
        return _Cursor(self._entries)


class _Users:
    def __init__(self, by_id):
        self._by_id = dict(by_id)

    async def find_one(self, query, _projection=None):
        uid = query.get("user_id")
        return dict(self._by_id[uid]) if uid in self._by_id else None


class _Projects:
    def __init__(self, by_id):
        self._by_id = dict(by_id or {})

    async def find_one(self, query, _projection=None):
        pid = query.get("project_id")
        return dict(self._by_id[pid]) if pid in self._by_id else None


class _FinanceDrafts:
    def __init__(self):
        self.inserted: List[Dict[str, Any]] = []

    async def find_one(self, _query, _projection=None):
        # No dedup conflicts in these tests.
        return None

    async def insert_one(self, doc):
        self.inserted.append(dict(doc))
        return SimpleNamespace(inserted_id="x")


@pytest.fixture(autouse=True)
def _users_on_postgres():
    """Phase 2a completion: `users` is Postgres, so the AP builder prefetches
    them through `_load_user_map` instead of `db["users"]`. Serve the same doc
    set each test already declares through `_Db(users=...)`."""
    with patched_load_user_map():
        yield


class _Db:
    """Map-style accessor so ``db["users"]`` etc. work like Motor."""

    def __init__(self, *, entries, users, projects=None):
        set_current_user_map(users)
        self._collections = {
            "time_entries": _TimeEntries(entries),
            "users": _Users(users),
            "projects": _Projects(projects or {}),
            "finance_drafts": _FinanceDrafts(),
        }

    def __getitem__(self, name):
        return self._collections[name]

    @property
    def finance_drafts(self):
        return self._collections["finance_drafts"]


def _entry(**overrides):
    base = {
        "entry_id": "te_001",
        "user_id": "u_engineer",
        "project_id": "proj_001",
        "status": "approved",
        "date": "2026-05-18",
        "hours": 4.0,
        "is_billable": True,
    }
    base.update(overrides)
    return base


_USERS = {
    "u_engineer": {
        "user_id": "u_engineer",
        "name": "Engineer One",
        "contractor_business_id": None,  # group by user_id directly
    },
}

_PROJECTS = {"proj_001": {"project_id": "proj_001", "name": "Acme Migration"}}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ap_draft_uses_cost_rate_snapshot_when_legacy_missing():
    """Wave 3 entry: cost_rate_snapshot=95.0, no legacy cost_rate. AP
    line item must use 95.0; total amount = hours * 95."""
    db = _Db(
        entries=[_entry(cost_rate_snapshot=95.0)],
        users=_USERS,
        projects=_PROJECTS,
    )
    from services.finance_draft_autogen import build_ap_drafts
    drafts, skipped, warnings = await build_ap_drafts(
        db,
        period_start="2026-05-17",
        period_end="2026-05-23",
        contractor_user_id_filter=None,
        created_by="u_admin",
        dry_run=False,
    )
    assert drafts, "expected one draft for one approved entry"
    draft = drafts[0]
    assert len(draft["line_items"]) == 1
    line = draft["line_items"][0]
    assert line["rate"] == 95.0
    assert line["quantity"] == 4.0
    assert line["amount"] == 4.0 * 95.0
    assert draft["amount"] == round(4.0 * 95.0, 2)


@pytest.mark.asyncio
async def test_snapshot_wins_when_both_snapshot_and_legacy_set():
    """If a row carries both fields, snapshot beats legacy.
    Protects against a regression where someone re-reads cost_rate
    'as a fallback first' and inverts the precedence."""
    db = _Db(
        entries=[_entry(cost_rate_snapshot=95.0, cost_rate=50.0)],
        users=_USERS,
        projects=_PROJECTS,
    )
    from services.finance_draft_autogen import build_ap_drafts
    drafts, _, _ = await build_ap_drafts(
        db,
        period_start="2026-05-17",
        period_end="2026-05-23",
        contractor_user_id_filter=None,
        created_by="u_admin",
        dry_run=False,
    )
    line = drafts[0]["line_items"][0]
    # Snapshot (95) wins over legacy (50). If the order ever flips,
    # the amount jumps from 380 to 200.
    assert line["rate"] == 95.0
    assert line["amount"] == 4.0 * 95.0


@pytest.mark.asyncio
async def test_legacy_cost_rate_used_when_snapshot_absent():
    """Pre-Wave-0 entry: no cost_rate_snapshot, legacy cost_rate=80.
    The fix must keep this path working (back-compat for legacy AP)."""
    legacy_entry = _entry(cost_rate=80.0)
    # Explicitly drop the snapshot field so the resolver sees None.
    legacy_entry.pop("cost_rate_snapshot", None)
    db = _Db(
        entries=[legacy_entry],
        users=_USERS,
        projects=_PROJECTS,
    )
    from services.finance_draft_autogen import build_ap_drafts
    drafts, _, _ = await build_ap_drafts(
        db,
        period_start="2026-05-17",
        period_end="2026-05-23",
        contractor_user_id_filter=None,
        created_by="u_admin",
        dry_run=False,
    )
    line = drafts[0]["line_items"][0]
    assert line["rate"] == 80.0
    assert line["amount"] == 4.0 * 80.0


@pytest.mark.asyncio
async def test_both_missing_produces_zero_amount():
    """Pathological row with neither field set must NOT crash and must
    produce a 0-rate / 0-amount line item. The Wave 5 reconcile pass
    can flag drafts with zero-rate lines for follow-up; the AP build
    itself must never raise on missing rates."""
    bare_entry = _entry()
    bare_entry.pop("cost_rate_snapshot", None)
    bare_entry.pop("cost_rate", None)
    db = _Db(
        entries=[bare_entry],
        users=_USERS,
        projects=_PROJECTS,
    )
    from services.finance_draft_autogen import build_ap_drafts
    drafts, _, _ = await build_ap_drafts(
        db,
        period_start="2026-05-17",
        period_end="2026-05-23",
        contractor_user_id_filter=None,
        created_by="u_admin",
        dry_run=False,
    )
    line = drafts[0]["line_items"][0]
    assert line["rate"] == 0.0
    assert line["amount"] == 0.0


@pytest.mark.asyncio
async def test_initial_line_item_rate_also_uses_snapshot():
    """The first entry seen for a (user_id, project_id) pair seeds the
    line_items_dict entry — the seeding read of cost_rate must also
    prefer the snapshot, otherwise a single-entry draft would carry
    the wrong rate even though the per-entry sum is correct."""
    db = _Db(
        entries=[_entry(cost_rate_snapshot=120.0)],
        users=_USERS,
        projects=_PROJECTS,
    )
    from services.finance_draft_autogen import build_ap_drafts
    drafts, _, _ = await build_ap_drafts(
        db,
        period_start="2026-05-17",
        period_end="2026-05-23",
        contractor_user_id_filter=None,
        created_by="u_admin",
        dry_run=False,
    )
    # The line_items_dict initial rate field is what propagates as the
    # display rate on the AP draft; if the seeding read used the legacy
    # field, this would be 0.0 even though amount/4.0 == 120.
    line = drafts[0]["line_items"][0]
    assert line["rate"] == 120.0
