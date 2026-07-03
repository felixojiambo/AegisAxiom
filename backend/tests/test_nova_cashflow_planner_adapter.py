"""Tests for the Cashflow Planner finance_drafts adapter.

Covers:
  * ar_invoice / ap_bill rows map to the right item_type.
  * Dates snap to Friday week-endings via the planner helper.
  * Status normalisation matches the planner's terminal set.
  * Missing optional fields don't crash — they produce warnings.
  * Invalid amount / missing due_date / unknown kind are skipped
    with structured warnings.
  * load_cashflow_items_from_finance_sources is read-only — it only
    calls find() and never writes back.
"""
from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timezone

# Required at import time by routes.nova → auth_helper.
os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production",
)

import pytest

from services.nova_engine.cashflow_planner import (
    CashflowItem,
    is_terminal_status,
)
from services.nova_engine.cashflow_planner_adapter import (
    AdapterWarning,
    LoadResult,
    load_cashflow_items_from_finance_sources,
    normalise_status,
    row_to_cashflow_item,
    summarise_warnings,
)


_FIXED_NOW = datetime(2026, 1, 22, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fake Mongo
# ---------------------------------------------------------------------------


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
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.find_calls: list[tuple] = []

    def find(self, query, projection=None):
        self.find_calls.append((query, projection))
        kinds = (query or {}).get("kind", {}).get("$in", [])
        matched = [d for d in self.docs if d.get("kind") in kinds]
        return _FakeAsyncCursor(matched)


class _FakeDB:
    def __init__(self, drafts=None):
        self.collections = {"finance_drafts": _FakeCollection(drafts)}
        # Track every other collection access so the read-only check
        # fails loudly if someone touches an unexpected one.
        self.accessed: list[str] = []

    def __getitem__(self, name):
        self.accessed.append(name)
        if name not in self.collections:
            self.collections[name] = _FakeCollection()
        return self.collections[name]


def _ar_row(**overrides):
    base = {
        "draft_id": "fcc_draft_ar01",
        "kind": "ar_invoice",
        "counterparty": "Acme Co",
        "counterparty_id": "acc_acme",
        "amount": 500.0,
        "memo": "January retainer",
        "sync_status": "Draft",
        "due_date": "2026-01-08",
        "qbo_id": None,
        "error": None,
        "source_origin": "manual",
        "source_period_start": None,
        "source_period_end": None,
        "source_contractor_id": None,
        "source_contract_id": None,
        "created_at": "2026-01-02T09:00:00+00:00",
        "updated_at": "2026-01-03T09:00:00+00:00",
        "created_by": "user_tester",
    }
    base.update(overrides)
    return base


def _ap_row(**overrides):
    base = _ar_row(
        draft_id="fcc_draft_ap01",
        kind="ap_bill",
        counterparty="Vendor LLC",
        counterparty_id="ven_vendor",
        amount=180.0,
        sync_status="Sent",
        due_date="2026-01-12",
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Status normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Draft", "draft"),
        ("Sent", "sent"),
        ("Synced", "synced"),
        ("Failed", "failed"),
        ("PAID", "paid"),
        ("  open ", "open"),
        ("", ""),
        (None, ""),
        ("totally-unknown-status", "totally-unknown-status"),
    ],
)
def test_normalise_status(raw, expected):
    assert normalise_status(raw) == expected


def test_synced_status_is_terminal():
    # Adapter normalises "Synced" → "synced"; the planner already
    # treats lowercase "synced" as terminal so we don't have to
    # double-list.
    assert is_terminal_status(normalise_status("Synced"))
    assert is_terminal_status(normalise_status("PAID"))
    assert is_terminal_status(normalise_status("posted"))
    assert not is_terminal_status(normalise_status("Sent"))
    assert not is_terminal_status(normalise_status("Draft"))


# ---------------------------------------------------------------------------
# row_to_cashflow_item — happy paths
# ---------------------------------------------------------------------------


def test_ar_invoice_row_becomes_ar_cashflow_item():
    item, warn = row_to_cashflow_item(_ar_row(), now=_FIXED_NOW)
    assert warn is None
    assert isinstance(item, CashflowItem)
    assert item.item_type == "ar"
    assert item.item_id == "fcc_draft_ar01"
    assert item.counterparty == "Acme Co"
    assert item.document_number == "fcc_draft_ar01"
    assert item.amount == 500.0
    assert item.status == "draft"
    assert item.source_system == "finance_drafts"
    assert item.due_date == date(2026, 1, 8)
    assert item.expected_date == date(2026, 1, 8)
    # 2026-01-08 is a Thursday → snap to Friday 2026-01-09.
    assert item.week_ending_date == date(2026, 1, 9)
    assert item.metadata["kind"] == "ar_invoice"
    assert item.metadata["counterparty_id"] == "acc_acme"


def test_ap_bill_row_becomes_ap_cashflow_item():
    item, warn = row_to_cashflow_item(_ap_row(), now=_FIXED_NOW)
    assert warn is None
    assert item.item_type == "ap"
    assert item.amount == 180.0
    assert item.status == "sent"
    # 2026-01-12 is a Monday → snap to Friday 2026-01-16.
    assert item.week_ending_date == date(2026, 1, 16)


def test_amount_accepts_numeric_strings_and_commas():
    item, warn = row_to_cashflow_item(_ar_row(amount="1,250.50"), now=_FIXED_NOW)
    assert warn is None
    assert item.amount == 1250.5


def test_due_date_accepts_native_date():
    item, warn = row_to_cashflow_item(
        _ar_row(due_date=date(2026, 1, 8)), now=_FIXED_NOW,
    )
    assert warn is None
    assert item.week_ending_date == date(2026, 1, 9)


def test_missing_updated_at_falls_back_to_created_at():
    item, _ = row_to_cashflow_item(
        _ar_row(updated_at=None), now=_FIXED_NOW,
    )
    assert item.last_checked_at is not None


def test_missing_both_timestamps_falls_back_to_now():
    item, _ = row_to_cashflow_item(
        _ar_row(updated_at=None, created_at=None), now=_FIXED_NOW,
    )
    assert item.last_checked_at == _FIXED_NOW


# ---------------------------------------------------------------------------
# row_to_cashflow_item — warning paths
# ---------------------------------------------------------------------------


def test_missing_due_date_produces_warning():
    item, warn = row_to_cashflow_item(
        _ar_row(due_date=None), now=_FIXED_NOW,
    )
    assert item is None
    assert warn is not None
    assert warn.kind == "missing_due_date"
    assert warn.draft_id == "fcc_draft_ar01"


def test_missing_amount_produces_warning():
    item, warn = row_to_cashflow_item(
        _ar_row(amount=None), now=_FIXED_NOW,
    )
    assert item is None
    assert warn.kind == "missing_amount"


def test_invalid_amount_produces_warning():
    item, warn = row_to_cashflow_item(
        _ar_row(amount="not-a-number"), now=_FIXED_NOW,
    )
    assert item is None
    assert warn.kind == "missing_amount"


def test_unsupported_kind_produces_warning():
    item, warn = row_to_cashflow_item(
        _ar_row(kind="journal_entry"), now=_FIXED_NOW,
    )
    assert item is None
    assert warn.kind == "unsupported_kind"


def test_missing_draft_id_produces_warning():
    item, warn = row_to_cashflow_item(
        _ar_row(draft_id=None), now=_FIXED_NOW,
    )
    assert item is None
    assert warn.kind == "missing_draft_id"


def test_missing_counterparty_string_does_not_crash():
    # finance_drafts is supposed to require counterparty, but tolerate
    # null defensively — null becomes the empty string.
    item, warn = row_to_cashflow_item(
        _ar_row(counterparty=None), now=_FIXED_NOW,
    )
    assert warn is None
    assert item.counterparty == ""


# ---------------------------------------------------------------------------
# load_cashflow_items_from_finance_sources
# ---------------------------------------------------------------------------


def test_loader_returns_ar_and_ap_items():
    db = _FakeDB([
        _ar_row(),
        _ap_row(),
    ])
    result = asyncio.run(
        load_cashflow_items_from_finance_sources(db, now=_FIXED_NOW),  # type: ignore[arg-type]
    )
    types = sorted(i.item_type for i in result.items)
    assert types == ["ap", "ar"]
    assert result.source_counts == {"finance_drafts": 2}
    assert result.skipped_count == 0
    assert result.warnings == []


def test_loader_skips_bad_rows_and_records_warnings():
    db = _FakeDB([
        _ar_row(),
        _ar_row(draft_id="fcc_draft_ar02", due_date=None),
        _ap_row(amount="bogus"),
    ])
    result = asyncio.run(
        load_cashflow_items_from_finance_sources(db, now=_FIXED_NOW),  # type: ignore[arg-type]
    )
    assert len(result.items) == 1
    assert result.items[0].item_id == "fcc_draft_ar01"
    assert result.skipped_count == 2
    kinds = sorted(w.kind for w in result.warnings)
    assert kinds == ["missing_amount", "missing_due_date"]


def test_loader_filters_by_kind_at_the_query_layer():
    # The fake collection only returns rows whose kind is in the
    # $in list. Other kinds should never come back even if they
    # sit in the same collection.
    db = _FakeDB([
        _ar_row(),
        _ar_row(draft_id="fcc_draft_x", kind="journal_entry"),
    ])
    result = asyncio.run(
        load_cashflow_items_from_finance_sources(db, now=_FIXED_NOW),  # type: ignore[arg-type]
    )
    assert len(result.items) == 1
    # The fake-cursor would have surfaced an unsupported_kind warning
    # if the loader had asked for it; instead, the $in filter kept
    # it out at query time.
    assert all(
        w.kind != "unsupported_kind" for w in result.warnings
    )


def test_loader_only_reads_finance_drafts_collection():
    db = _FakeDB([_ar_row()])
    asyncio.run(
        load_cashflow_items_from_finance_sources(db, now=_FIXED_NOW),  # type: ignore[arg-type]
    )
    # The adapter must not poke at any other collection.
    assert db.accessed == ["finance_drafts"]
    # And it must use find() only (no insert / update / delete on
    # the fake — those methods don't exist on _FakeCollection so an
    # accidental call would raise AttributeError).
    assert len(db.collections["finance_drafts"].find_calls) == 1


def test_summarise_warnings_strips_dataclass_to_dicts():
    warns = [
        AdapterWarning(kind="missing_amount", draft_id="d1", detail="x"),
        AdapterWarning(kind="missing_due_date", draft_id="d2", detail="y"),
    ]
    out = summarise_warnings(warns)
    assert out == [
        {"kind": "missing_amount", "draft_id": "d1", "detail": "x"},
        {"kind": "missing_due_date", "draft_id": "d2", "detail": "y"},
    ]


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------


def test_adapter_module_has_no_write_methods():
    import inspect

    import services.nova_engine.cashflow_planner_adapter as adapter

    src = inspect.getsource(adapter)
    forbidden = (
        ".insert_one(",
        ".insert_many(",
        ".update_one(",
        ".update_many(",
        ".delete_one(",
        ".delete_many(",
        ".replace_one(",
        ".bulk_write(",
    )
    for token in forbidden:
        assert token not in src, (
            f"Adapter must not call {token!r}; it is read-only."
        )
