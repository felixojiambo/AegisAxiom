"""Tests for the Cashflow Planner foundation.

Covers:
  * Friday week-ending normalization (Tue / Fri / Sat).
  * Active-week calculation (Mon–Fri vs Sat/Sun).
  * Roll-forward semantics for unpaid past-due A/R and A/P.
  * Audit-record contents (one per rolled item, correct reason +
    old/new dates).
  * Forecast bucketing (per-Friday expected A/R, A/P, net cash flow).
  * Regression: past-due unpaid items survive even if they sit before
    every projection week the engine would consider "in horizon".
"""
from __future__ import annotations

import asyncio
import os

# Environment bootstrap — importing routes.nova pulls auth_helper,
# which raises at import if JWT_SECRET is missing. Mirrors the pattern
# in tests/test_nova_route.py.
os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production",
)

from datetime import date, datetime, timezone

import pytest

from services.nova_engine.cashflow_planner import (
    CASHFLOW_AUDIT_COLLECTION,
    CashflowAuditRecord,
    CashflowItem,
    ROLL_FORWARD_REASON,
    active_week_ending,
    bucket_items_into_weeks,
    is_terminal_status,
    roll_unpaid_past_due_items,
    to_friday_week_ending,
    write_cashflow_audit_records,
)


# Stable "now" used by tests so audit timestamps are deterministic.
_FIXED_NOW = datetime(2026, 1, 22, 12, 0, 0, tzinfo=timezone.utc)


def _make_item(
    *,
    item_id: str = "i1",
    item_type: str = "ar",
    counterparty: str = "Acme",
    document_number: str | None = "INV-1",
    expected_date: date = date(2026, 1, 8),
    week_ending_date: date | None = None,
    amount: float = 100.0,
    status: str = "open",
    source_system: str = "reports_ar_aging_detail",
    metadata: dict | None = None,
) -> CashflowItem:
    if week_ending_date is None:
        week_ending_date = to_friday_week_ending(expected_date)
    return CashflowItem(
        item_id=item_id,
        item_type=item_type,  # type: ignore[arg-type]
        counterparty=counterparty,
        document_number=document_number,
        source_date=expected_date,
        due_date=expected_date,
        expected_date=expected_date,
        week_ending_date=week_ending_date,
        amount=amount,
        status=status,
        source_system=source_system,
        last_checked_at=_FIXED_NOW,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# A. Friday week-ending normalization
# ---------------------------------------------------------------------------


def test_tuesday_snaps_to_same_week_friday():
    # 2026-01-13 is a Tuesday → Friday 2026-01-16.
    assert to_friday_week_ending(date(2026, 1, 13)) == date(2026, 1, 16)


def test_friday_stays_on_same_friday():
    assert to_friday_week_ending(date(2026, 1, 16)) == date(2026, 1, 16)


def test_saturday_rolls_to_next_friday():
    # 2026-01-17 is a Saturday → Friday 2026-01-23.
    assert to_friday_week_ending(date(2026, 1, 17)) == date(2026, 1, 23)


def test_sunday_rolls_to_next_friday():
    # 2026-01-18 is a Sunday → Friday 2026-01-23.
    assert to_friday_week_ending(date(2026, 1, 18)) == date(2026, 1, 23)


def test_monday_through_thursday_all_snap_to_same_friday():
    # Mon 2026-01-12 .. Thu 2026-01-15 all → Fri 2026-01-16.
    for d in (
        date(2026, 1, 12), date(2026, 1, 13),
        date(2026, 1, 14), date(2026, 1, 15),
    ):
        assert to_friday_week_ending(d) == date(2026, 1, 16)


def test_accepts_datetime_input():
    # datetime instances should be reduced to their .date() before snap.
    dt = datetime(2026, 1, 13, 23, 59, 59, tzinfo=timezone.utc)
    assert to_friday_week_ending(dt) == date(2026, 1, 16)


# ---------------------------------------------------------------------------
# B. Active-week calculation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "today,expected_friday",
    [
        (date(2026, 1, 12), date(2026, 1, 16)),  # Mon → Fri same week
        (date(2026, 1, 13), date(2026, 1, 16)),  # Tue
        (date(2026, 1, 14), date(2026, 1, 16)),  # Wed
        (date(2026, 1, 15), date(2026, 1, 16)),  # Thu
        (date(2026, 1, 16), date(2026, 1, 16)),  # Fri itself
    ],
)
def test_active_week_mon_to_fri_returns_same_week_friday(today, expected_friday):
    assert active_week_ending(today) == expected_friday


def test_active_week_saturday_rolls_to_next_friday():
    assert active_week_ending(date(2026, 1, 17)) == date(2026, 1, 23)


def test_active_week_sunday_rolls_to_next_friday():
    assert active_week_ending(date(2026, 1, 18)) == date(2026, 1, 23)


# ---------------------------------------------------------------------------
# C. Roll-forward — A/R, A/P
# ---------------------------------------------------------------------------


def test_unpaid_past_due_ar_rolls_to_active_friday():
    today = date(2026, 1, 21)  # Wed → active Fri 2026-01-23
    past_friday = date(2026, 1, 16)
    item = _make_item(
        item_id="ar-1",
        item_type="ar",
        week_ending_date=past_friday,
        status="open",
    )
    result = roll_unpaid_past_due_items([item], today, now=_FIXED_NOW)
    assert result.active_week_ending == date(2026, 1, 23)
    assert len(result.items) == 1
    rolled = result.items[0]
    assert rolled.week_ending_date == date(2026, 1, 23)
    assert rolled.item_id == "ar-1"
    assert rolled.metadata["original_week_ending_date"] == past_friday.isoformat()
    assert len(result.audit_records) == 1


def test_unpaid_past_due_ap_rolls_to_active_friday():
    today = date(2026, 1, 21)
    item = _make_item(
        item_id="ap-1",
        item_type="ap",
        week_ending_date=date(2026, 1, 9),  # Friday two weeks ago
        status="outstanding",
        source_system="finance_drafts",
    )
    result = roll_unpaid_past_due_items([item], today, now=_FIXED_NOW)
    assert len(result.audit_records) == 1
    rolled = result.items[0]
    assert rolled.week_ending_date == date(2026, 1, 23)
    assert rolled.item_type == "ap"


def test_item_already_on_or_after_active_week_does_not_move():
    today = date(2026, 1, 21)
    current_week = _make_item(
        item_id="ar-current",
        week_ending_date=date(2026, 1, 23),
        status="open",
    )
    future_week = _make_item(
        item_id="ar-future",
        week_ending_date=date(2026, 1, 30),
        status="open",
    )
    result = roll_unpaid_past_due_items(
        [current_week, future_week], today, now=_FIXED_NOW,
    )
    assert result.audit_records == []
    assert result.items[0].week_ending_date == date(2026, 1, 23)
    assert result.items[1].week_ending_date == date(2026, 1, 30)


@pytest.mark.parametrize(
    "status",
    ["paid", "Paid", "CLOSED", "synced", "Posted", "settled", "void"],
)
def test_terminal_status_items_in_past_do_not_roll(status):
    today = date(2026, 1, 21)
    item = _make_item(
        item_id="ar-paid",
        week_ending_date=date(2026, 1, 9),
        status=status,
    )
    result = roll_unpaid_past_due_items([item], today, now=_FIXED_NOW)
    assert result.audit_records == []
    assert result.items[0].week_ending_date == date(2026, 1, 9)


def test_unknown_status_is_treated_as_outstanding():
    # The product rule: if we don't *know* it's paid, keep rolling it.
    today = date(2026, 1, 21)
    item = _make_item(
        item_id="ar-blank",
        week_ending_date=date(2026, 1, 9),
        status="",
    )
    result = roll_unpaid_past_due_items([item], today, now=_FIXED_NOW)
    assert len(result.audit_records) == 1


def test_mixed_batch_only_rolls_unpaid_past_due():
    today = date(2026, 1, 21)
    items = [
        _make_item(item_id="paid-old", week_ending_date=date(2026, 1, 9),
                   status="paid"),
        _make_item(item_id="unpaid-old", week_ending_date=date(2026, 1, 9),
                   status="open"),
        _make_item(item_id="unpaid-current", week_ending_date=date(2026, 1, 23),
                   status="open"),
        _make_item(item_id="unpaid-old-ap", item_type="ap",
                   week_ending_date=date(2026, 1, 16), status="outstanding"),
    ]
    result = roll_unpaid_past_due_items(items, today, now=_FIXED_NOW)
    rolled_ids = {a.item_id for a in result.audit_records}
    assert rolled_ids == {"unpaid-old", "unpaid-old-ap"}
    # Order preserved.
    assert [i.item_id for i in result.items] == [
        "paid-old", "unpaid-old", "unpaid-current", "unpaid-old-ap",
    ]


def test_original_week_ending_preserved_across_repeated_rolls():
    # If a roll-forward is applied twice (e.g., the same item is
    # processed in two consecutive planner runs), the audit metadata
    # should still point at the *true* original date.
    today_1 = date(2026, 1, 21)  # active = 2026-01-23
    today_2 = date(2026, 1, 28)  # active = 2026-01-30
    original = date(2026, 1, 9)
    item = _make_item(item_id="ar-twice", week_ending_date=original,
                      status="open")
    r1 = roll_unpaid_past_due_items([item], today_1, now=_FIXED_NOW)
    r2 = roll_unpaid_past_due_items(r1.items, today_2, now=_FIXED_NOW)
    rolled = r2.items[0]
    assert rolled.week_ending_date == date(2026, 1, 30)
    assert rolled.metadata["original_week_ending_date"] == original.isoformat()


# ---------------------------------------------------------------------------
# D. Audit trail
# ---------------------------------------------------------------------------


def test_audit_record_one_per_rolled_item_with_correct_fields():
    today = date(2026, 1, 21)
    items = [
        _make_item(item_id="ar-1", item_type="ar",
                   week_ending_date=date(2026, 1, 9),
                   status="open", amount=250.0,
                   source_system="reports_ar_aging_detail"),
        _make_item(item_id="ap-1", item_type="ap",
                   week_ending_date=date(2026, 1, 16),
                   status="outstanding", amount=80.0,
                   source_system="finance_drafts"),
    ]
    result = roll_unpaid_past_due_items(items, today, now=_FIXED_NOW)
    assert len(result.audit_records) == 2

    ar_audit = next(a for a in result.audit_records if a.item_id == "ar-1")
    assert ar_audit.reason == ROLL_FORWARD_REASON
    assert ar_audit.reason == "unpaid_past_due_roll_forward"
    assert ar_audit.old_week_ending_date == date(2026, 1, 9)
    assert ar_audit.new_week_ending_date == date(2026, 1, 23)
    assert ar_audit.status_at_change == "open"
    assert ar_audit.amount == 250.0
    assert ar_audit.source_system == "reports_ar_aging_detail"
    assert ar_audit.item_type == "ar"
    assert ar_audit.changed_at == _FIXED_NOW

    ap_audit = next(a for a in result.audit_records if a.item_id == "ap-1")
    assert ap_audit.old_week_ending_date == date(2026, 1, 16)
    assert ap_audit.new_week_ending_date == date(2026, 1, 23)
    assert ap_audit.source_system == "finance_drafts"
    assert ap_audit.item_type == "ap"


def test_audit_record_to_doc_has_mongo_friendly_shape():
    rec = CashflowAuditRecord(
        item_id="ar-1",
        item_type="ar",
        old_week_ending_date=date(2026, 1, 9),
        new_week_ending_date=date(2026, 1, 23),
        reason=ROLL_FORWARD_REASON,
        status_at_change="open",
        amount=100.0,
        source_system="reports_ar_aging_detail",
        changed_at=_FIXED_NOW,
        document_number="INV-1",
        counterparty="Acme",
    )
    doc = rec.to_doc()
    assert doc["audit_id"].startswith("pcfa_")
    assert doc["old_week_ending_date"] == "2026-01-09"
    assert doc["new_week_ending_date"] == "2026-01-23"
    assert doc["reason"] == "unpaid_past_due_roll_forward"
    assert doc["amount"] == 100.0
    assert doc["item_type"] == "ar"
    assert doc["changed_at"] == _FIXED_NOW


# ---------------------------------------------------------------------------
# E. Audit persistence (in-memory fake)
# ---------------------------------------------------------------------------


class _FakeInsertManyResult:
    def __init__(self, inserted_ids: list[int]):
        self.inserted_ids = inserted_ids


class _FakeAsyncCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _FakeCollection:
    def __init__(self):
        self.docs: list[dict] = []

    def find(self, query, projection=None):
        # Minimal $in matcher on audit_key — the only filter the
        # idempotent writer uses.
        keys = (query or {}).get("audit_key", {}).get("$in", [])
        matched = [d for d in self.docs if d.get("audit_key") in keys]
        return _FakeAsyncCursor(matched)

    async def insert_many(self, docs):
        self.docs.extend(docs)
        return _FakeInsertManyResult(list(range(len(docs))))


class _FakeDB:
    def __init__(self):
        self._collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self._collections.setdefault(name, _FakeCollection())


def test_write_cashflow_audit_records_inserts_to_named_collection():
    db = _FakeDB()
    rec = CashflowAuditRecord(
        item_id="ar-1",
        item_type="ar",
        old_week_ending_date=date(2026, 1, 9),
        new_week_ending_date=date(2026, 1, 23),
        reason=ROLL_FORWARD_REASON,
        status_at_change="open",
        amount=100.0,
        source_system="reports_ar_aging_detail",
        changed_at=_FIXED_NOW,
    )
    written = asyncio.run(write_cashflow_audit_records([rec], db=db))  # type: ignore[arg-type]
    assert written == 1
    coll = db[CASHFLOW_AUDIT_COLLECTION]
    assert len(coll.docs) == 1
    assert coll.docs[0]["reason"] == "unpaid_past_due_roll_forward"
    assert coll.docs[0]["item_id"] == "ar-1"


def test_write_cashflow_audit_records_empty_list_is_noop():
    db = _FakeDB()
    written = asyncio.run(write_cashflow_audit_records([], db=db))  # type: ignore[arg-type]
    assert written == 0
    # Collection never accessed → no insert calls.
    assert db._collections == {}


def test_write_cashflow_audit_records_swallows_errors():
    class _BoomCollection:
        def find(self, query, projection=None):
            raise RuntimeError("mongo down")

        async def insert_many(self, docs):  # pragma: no cover — find blows first
            raise RuntimeError("mongo down")

    class _BoomDB:
        def __getitem__(self, name):
            return _BoomCollection()

    rec = CashflowAuditRecord(
        item_id="ar-1",
        item_type="ar",
        old_week_ending_date=date(2026, 1, 9),
        new_week_ending_date=date(2026, 1, 23),
        reason=ROLL_FORWARD_REASON,
        status_at_change="open",
        amount=100.0,
        source_system="reports_ar_aging_detail",
        changed_at=_FIXED_NOW,
    )
    # Audit must never raise into the caller's response path.
    written = asyncio.run(write_cashflow_audit_records([rec], db=_BoomDB()))  # type: ignore[arg-type]
    assert written == 0


def test_audit_record_has_deterministic_audit_key():
    rec = CashflowAuditRecord(
        item_id="ar-1",
        item_type="ar",
        old_week_ending_date=date(2026, 1, 9),
        new_week_ending_date=date(2026, 1, 23),
        reason=ROLL_FORWARD_REASON,
        status_at_change="open",
        amount=100.0,
        source_system="reports_ar_aging_detail",
        changed_at=_FIXED_NOW,
    )
    assert rec.audit_key == (
        "unpaid_past_due_roll_forward:ar:ar-1:2026-01-09:2026-01-23"
    )
    assert rec.to_doc()["audit_key"] == rec.audit_key


def test_write_cashflow_audit_records_is_idempotent():
    db = _FakeDB()
    rec = CashflowAuditRecord(
        item_id="ar-1",
        item_type="ar",
        old_week_ending_date=date(2026, 1, 9),
        new_week_ending_date=date(2026, 1, 23),
        reason=ROLL_FORWARD_REASON,
        status_at_change="open",
        amount=100.0,
        source_system="reports_ar_aging_detail",
        changed_at=_FIXED_NOW,
    )
    first = asyncio.run(write_cashflow_audit_records([rec], db=db))  # type: ignore[arg-type]
    second = asyncio.run(write_cashflow_audit_records([rec], db=db))  # type: ignore[arg-type]
    assert first == 1
    assert second == 0  # same audit_key — skipped
    assert len(db[CASHFLOW_AUDIT_COLLECTION].docs) == 1


# ---------------------------------------------------------------------------
# F. Forecast output
# ---------------------------------------------------------------------------


def test_bucket_items_into_weeks_groups_by_friday_and_nets_cash_flow():
    items = [
        _make_item(item_id="ar-a", item_type="ar",
                   week_ending_date=date(2026, 1, 23), amount=500.0,
                   status="open"),
        _make_item(item_id="ar-b", item_type="ar",
                   week_ending_date=date(2026, 1, 23), amount=250.0,
                   status="open"),
        _make_item(item_id="ap-a", item_type="ap",
                   week_ending_date=date(2026, 1, 23), amount=100.0,
                   status="outstanding"),
        _make_item(item_id="ap-b", item_type="ap",
                   week_ending_date=date(2026, 1, 30), amount=400.0,
                   status="outstanding"),
    ]
    buckets = bucket_items_into_weeks(items)
    assert [b.week_ending for b in buckets] == [
        date(2026, 1, 23), date(2026, 1, 30),
    ]
    wk1, wk2 = buckets
    assert wk1.expected_ar == 750.0
    assert wk1.expected_ap == 100.0
    assert wk1.net_cash_flow == 650.0
    assert wk2.expected_ar == 0.0
    assert wk2.expected_ap == 400.0
    assert wk2.net_cash_flow == -400.0


def test_bucket_items_into_weeks_excludes_terminal_items():
    items = [
        _make_item(item_id="paid", week_ending_date=date(2026, 1, 23),
                   status="paid", amount=999.0),
        _make_item(item_id="open", week_ending_date=date(2026, 1, 23),
                   status="open", amount=1.0),
    ]
    buckets = bucket_items_into_weeks(items)
    assert len(buckets) == 1
    assert buckets[0].expected_ar == 1.0


# ---------------------------------------------------------------------------
# G. Regression — past-due unpaid items survive
# ---------------------------------------------------------------------------


def test_past_due_unpaid_items_are_not_dropped_outside_horizon():
    """A 13-week horizon starting at the active Friday would normally
    drop anything dated before today. The planner must keep those
    items visible by pulling them onto the active Friday so they
    appear in the forecast — never dropped.
    """
    today = date(2026, 5, 13)  # active Friday 2026-05-15
    very_old = _make_item(
        item_id="ar-ancient",
        week_ending_date=date(2025, 12, 5),  # months before active
        status="open",
        amount=1234.56,
    )
    result = roll_unpaid_past_due_items([very_old], today, now=_FIXED_NOW)
    assert len(result.items) == 1  # not dropped
    rolled = result.items[0]
    assert rolled.week_ending_date == date(2026, 5, 15)
    assert rolled.amount == 1234.56
    buckets = bucket_items_into_weeks(result.items)
    assert len(buckets) == 1
    assert buckets[0].week_ending == date(2026, 5, 15)
    assert buckets[0].expected_ar == 1234.56


def test_is_terminal_status_table():
    assert is_terminal_status("paid")
    assert is_terminal_status("Paid")
    assert is_terminal_status("  Synced  ")
    assert is_terminal_status("CLOSED")
    assert not is_terminal_status("open")
    assert not is_terminal_status("")
    assert not is_terminal_status(None)


# ---------------------------------------------------------------------------
# H. Sanity — existing Nova projection route still imports
# ---------------------------------------------------------------------------


def test_existing_nova_route_imports_cleanly():
    # Importing the route module must not error after the new file
    # lands. If we ever wire the planner into the route, this test
    # also catches accidental cycles.
    import importlib
    import routes.nova as nova_route

    importlib.reload(nova_route)
    assert hasattr(nova_route, "router")


def test_cashflow_planner_is_importable_from_nova_engine():
    # Confirms public surface stays discoverable.
    from services.nova_engine import cashflow_planner

    assert cashflow_planner.ROLL_FORWARD_REASON == "unpaid_past_due_roll_forward"
    assert cashflow_planner.CASHFLOW_AUDIT_COLLECTION == "nova_cashflow_audit"
