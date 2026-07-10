"""Locks reversal handling on QBO income-account rows.

QBO posts reversals as Expense, Bill, Journal Entry, Credit Memo,
refund, or as direct negative rows on income accounts. The canonical
rule across Nova and AEGISAXIOM revenue logic is.

  * Revenue category is determined by the account code or name.
  * Every row whose account maps into a revenue bucket is included
    with its signed amount, regardless of txn_type.
  * Negative rows reduce the bucket total.
  * Net revenue equals positive_total plus negative_total.
  * The signed amount field used is the canonical general_ledger
    amount field. abs is never applied for signed totals.
  * txn_type is metadata only and must not exclude any row.

Synthetic only. Fixtures use far future dates and fake account
labels so the cases are stable across calendar drift. The expected
totals are computed inline so the assertions cannot drift away from
the fixture row set silently.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)

import pytest


_BASIS = "accrual"


# ---------------------------------------------------------------------------
# Synthetic GL row fixtures
# ---------------------------------------------------------------------------


def _row(tx_date, account_name, txn_type, amount):
    return {
        "tx_date": tx_date,
        "basis": _BASIS,
        "account_name": account_name,
        "txn_type": txn_type,
        "amount": amount,
        "debit": amount if amount > 0 else 0.0,
        "credit": -amount if amount < 0 else 0.0,
    }


def _synth_revenue_rows():
    """Mixed positive and negative rows on the canonical PS revenue
    account plus a few reversal shapes that real QBO emits."""
    return [
        _row("2099-04-01", "50101 Professional Services Income", "Invoice", 10000.0),
        _row("2099-04-05", "50101 Professional Services Income", "Invoice", 5000.0),
        _row("2099-04-10", "50101 Professional Services Income", "Expense", -1500.0),
        _row("2099-04-12", "50101 Professional Services Income", "Bill", -750.0),
        _row("2099-04-15", "50101 Professional Services Income", "Journal Entry", -250.0),
        _row("2099-04-20", "50101 Professional Services Income", "Credit Memo", -500.0),
        _row("2099-04-22", "50101 Professional Services Income", "Refund", -100.0),
    ]


def _synth_true_expense_row():
    return _row(
        "2099-04-25",
        "70601 Payroll Expenses",
        "Bill",
        -1200.0,
    )


# ---------------------------------------------------------------------------
# account_categories: signed convention is account driven
# ---------------------------------------------------------------------------


def test_revenue_category_is_account_driven_not_txn_type_driven():
    """A negative Expense or Bill row whose account is a revenue
    account still categorises as revenue. txn_type is metadata only."""
    from services.nova_engine.account_categories import categorize, is_revenue

    for r in _synth_revenue_rows():
        cat = categorize(r["account_name"])
        assert is_revenue(cat), (
            "row with account " + r["account_name"]
            + " and txn_type " + r["txn_type"]
            + " unexpectedly fell out of revenue"
        )


def test_expense_account_row_is_not_revenue():
    """A negative row posted to a true expense account must NOT be
    classified as revenue."""
    from services.nova_engine.account_categories import categorize, is_revenue

    cat = categorize(_synth_true_expense_row()["account_name"])
    assert not is_revenue(cat)


# ---------------------------------------------------------------------------
# Driver analysis aggregation preserves signed sum
# ---------------------------------------------------------------------------


def test_aggregate_revenue_sums_signed_amounts_without_abs():
    """_aggregate must sum signed amounts so reversals reduce the
    revenue bucket. The expected total is computed inline from the
    fixture row set so the assertion cannot silently drift."""
    from services.nova_engine.finance_driver_analysis import _aggregate
    from services.nova_engine.formulas import REVENUE_CATEGORIES

    rows = _synth_revenue_rows()
    expected_total = sum(r["amount"] for r in rows)

    total, _by_month, _by_account = _aggregate(rows, REVENUE_CATEGORIES)
    assert total == pytest.approx(expected_total)
    # Sanity check that abs was not applied.
    assert total != sum(abs(r["amount"]) for r in rows)


def test_aggregate_revenue_includes_expense_txn_type_when_account_is_revenue():
    """An Expense row posted to a revenue account must contribute its
    signed amount to the revenue total."""
    from services.nova_engine.finance_driver_analysis import _aggregate
    from services.nova_engine.formulas import REVENUE_CATEGORIES

    rows = [
        _row("2099-04-01", "50101 Professional Services Income", "Invoice", 100.0),
        _row("2099-04-02", "50101 Professional Services Income", "Expense", -25.0),
    ]
    total, _, _ = _aggregate(rows, REVENUE_CATEGORIES)
    assert total == pytest.approx(75.0)


def test_aggregate_revenue_includes_bill_txn_type_when_account_is_revenue():
    """A Bill row posted to a revenue account must contribute its
    signed amount to the revenue total."""
    from services.nova_engine.finance_driver_analysis import _aggregate
    from services.nova_engine.formulas import REVENUE_CATEGORIES

    rows = [
        _row("2099-04-01", "50300 Other Income:Contract Commission Income", "Deposit", 1000.0),
        _row("2099-04-02", "50300 Other Income:Contract Commission Income", "Bill", -300.0),
    ]
    total, _, _ = _aggregate(rows, REVENUE_CATEGORIES)
    assert total == pytest.approx(700.0)


def test_aggregate_revenue_includes_journal_entry_when_account_is_revenue():
    """A Journal Entry posted to a revenue account contributes its
    signed amount whether the value is positive or negative."""
    from services.nova_engine.finance_driver_analysis import _aggregate
    from services.nova_engine.formulas import REVENUE_CATEGORIES

    rows = [
        _row("2099-04-01", "50201 Field Income:Field Income - Service", "Invoice", 500.0),
        _row("2099-04-05", "50201 Field Income:Field Income - Service", "Journal Entry", -120.0),
        _row("2099-04-10", "50201 Field Income:Field Income - Service", "Journal Entry", 80.0),
    ]
    total, _, _ = _aggregate(rows, REVENUE_CATEGORIES)
    assert total == pytest.approx(500.0 - 120.0 + 80.0)


def test_aggregate_true_expense_row_is_not_revenue():
    """A row on a real expense account must not move the revenue
    total even when its amount is negative."""
    from services.nova_engine.finance_driver_analysis import _aggregate
    from services.nova_engine.formulas import REVENUE_CATEGORIES

    rows = [
        _row("2099-04-01", "50101 Professional Services Income", "Invoice", 1000.0),
        _synth_true_expense_row(),
    ]
    total, _, _ = _aggregate(rows, REVENUE_CATEGORIES)
    assert total == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# Reversal metadata contract on driver analysis revenue response
# ---------------------------------------------------------------------------


def test_reversal_metadata_split_matches_signed_sum():
    """positive_total plus negative_total must equal signed_total.
    Negative row counts reflect every reversal like row regardless
    of txn_type."""
    from services.nova_engine.finance_driver_analysis import _reversal_metadata
    from services.nova_engine.formulas import REVENUE_CATEGORIES

    rows = _synth_revenue_rows()
    meta = _reversal_metadata(rows, REVENUE_CATEGORIES)
    pos_expected = sum(r["amount"] for r in rows if r["amount"] > 0)
    neg_expected = sum(r["amount"] for r in rows if r["amount"] < 0)
    neg_count_expected = sum(1 for r in rows if r["amount"] < 0)

    assert meta["positive_total"] == pytest.approx(pos_expected)
    assert meta["negative_total"] == pytest.approx(neg_expected)
    assert meta["signed_total"] == pytest.approx(pos_expected + neg_expected)
    assert meta["negative_row_count"] == neg_count_expected
    # Low. reversal_like_row_count was dropped because it always
    # equalled negative_row_count. Confirm the alias is gone so a
    # future regression cannot quietly reintroduce it.
    assert "reversal_like_row_count" not in meta
    # The txn_types set surfaces every shape the fixture emits so
    # a future ingestion change cannot silently drop a kind of row.
    expected_types = sorted({r["txn_type"] for r in rows})
    assert meta["transaction_types"] == expected_types
    assert "Negative rows" in meta["reversal_note"]


# ---------------------------------------------------------------------------
# Public analyze_finance_drivers wires reversal_summary into the response
# ---------------------------------------------------------------------------


class _FakeAggregateCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    async def to_list(self, length=None):
        return list(self._docs)


class _FakeGLCollection:
    def __init__(self, rows):
        self._rows = list(rows)

    def aggregate(self, pipeline):
        # Project the rows down to the (tx_date, account_name, amount)
        # shape the helper requests so the test mirrors production
        # without re implementing the Mongo pipeline.
        out = [
            {
                "tx_date": r["tx_date"],
                "account_name": r["account_name"],
                "amount": float(r["amount"]),
            }
            for r in self._rows
        ]
        return _FakeAggregateCursor(out)


class _FakeDb:
    def __init__(self, rows):
        self._gl = _FakeGLCollection(rows)

    def __getitem__(self, name):
        if name == "general_ledger":
            return self._gl
        raise KeyError(name)


@pytest.mark.asyncio
async def test_analyze_finance_drivers_revenue_includes_reversal_summary():
    """The public analyze_finance_drivers helper must surface a
    reversal_summary block when metric is revenue so a reconciler
    can read positive_total and negative_total directly off the
    response."""
    from services.nova_engine.finance_driver_analysis import (
        analyze_finance_drivers,
    )

    rows = _synth_revenue_rows()
    db = _FakeDb(rows)
    result = await analyze_finance_drivers(
        db,
        metric="revenue",
        date_from="2099-04-01",
        date_to="2099-04-30",
        basis=_BASIS,
        compare_to=None,
    )
    assert "reversal_summary" in result
    summary = result["reversal_summary"]
    pos_expected = sum(r["amount"] for r in rows if r["amount"] > 0)
    neg_expected = sum(r["amount"] for r in rows if r["amount"] < 0)
    assert summary["positive_total"] == pytest.approx(pos_expected)
    assert summary["negative_total"] == pytest.approx(neg_expected)
    assert summary["signed_total"] == pytest.approx(pos_expected + neg_expected)
    # And current_period_total still reads the signed net so existing
    # callers do not regress.
    assert result["summary"]["current_period_total"] == pytest.approx(
        pos_expected + neg_expected
    )


# ---------------------------------------------------------------------------
# query_gl_transactions exposes signed totals and per row sign
# ---------------------------------------------------------------------------


class _FakeFindCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, length=None):
        return list(self._docs)


class _FakeGLFindCollection:
    def __init__(self, rows):
        self._rows = list(rows)

    def find(self, match, projection):
        return _FakeFindCursor(self._rows)


class _FakeQboDb:
    def __init__(self, rows):
        self._gl = _FakeGLFindCollection(rows)

    @property
    def general_ledger(self):
        return self._gl


@pytest.mark.asyncio
async def test_query_gl_transactions_exposes_signed_total_and_reversal_metadata(
    monkeypatch,
):
    """query_gl_transactions must surface signed_total, positive_total,
    negative_total, positive_row_count, and negative_row_count so the
    LLM and any reconciliation flow can compare against the QBO
    Transaction Report. The earlier reversal_like_row_count alias was
    dropped because it always equalled negative_row_count."""
    from services.ai.agents.tools import nova_tools
    from services.ai.agents.tools.nova_tools import _ScenarioState

    rows = _synth_revenue_rows()
    fake_db = _FakeQboDb(rows)

    async def _fake_location_status(_db):
        return "unavailable"

    monkeypatch.setattr(
        nova_tools, "_check_location_field_status", _fake_location_status
    )
    monkeypatch.setattr(
        "services.nova_engine.db.get_qbo_db", lambda: fake_db
    )

    result = await nova_tools.execute_tool(
        "query_gl_transactions",
        {
            "date_from": "2099-04-01",
            "date_to": "2099-04-30",
            "basis": _BASIS,
            "limit": 100,
        },
        _ScenarioState(),
        "u_synth_reversal",
        "sess_synth_reversal",
        None,
    )

    pos_expected = sum(r["amount"] for r in rows if r["amount"] > 0)
    neg_expected = sum(r["amount"] for r in rows if r["amount"] < 0)
    signed_expected = pos_expected + neg_expected
    neg_count_expected = sum(1 for r in rows if r["amount"] < 0)
    pos_count_expected = sum(1 for r in rows if r["amount"] > 0)

    assert result["row_count"] == len(rows)
    assert result["signed_total"] == pytest.approx(signed_expected)
    assert result["positive_total"] == pytest.approx(pos_expected)
    assert result["negative_total"] == pytest.approx(neg_expected)
    assert result["positive_row_count"] == pos_count_expected
    assert result["negative_row_count"] == neg_count_expected
    # Low. reversal_like_row_count alias removed.
    assert "reversal_like_row_count" not in result
    assert "Negative rows" in result["reversal_note"]
    # Every negative row in the preview must carry its signed amount
    # AND be flagged as reversal like. Positive rows must NOT be
    # flagged so reconcilers can filter cleanly.
    seen_negative = False
    for row in result["rows"]:
        if row["amount"] < 0:
            seen_negative = True
            assert row["is_reversal_like"] is True
        else:
            assert row["is_reversal_like"] is False
    assert seen_negative


# ---------------------------------------------------------------------------
# Export column projection ships the signed amount
# ---------------------------------------------------------------------------


def test_query_gl_transactions_export_columns_include_signed_amount():
    """The export column list for query_gl_transactions must include
    the signed Amount column so CSV/XLSX/PDF auditors see the actual
    sign of every row and can reconcile against the QBO Transaction
    Report. The legacy Normalized Amount column is kept too."""
    from services.nova_engine.finance_export_service import EXPORTABLE_TOOLS

    spec = EXPORTABLE_TOOLS["query_gl_transactions"]
    column_keys = [key for _label, key in spec.columns]
    assert "amount" in column_keys, (
        "signed Amount column missing from query_gl_transactions "
        "export projection. Reversal rows would render with positive "
        "magnitude only in CSV/XLSX/PDF."
    )
    assert "normalized_amount" in column_keys
    assert "is_reversal_like" in column_keys


# ---------------------------------------------------------------------------
# Regression checks
# ---------------------------------------------------------------------------


def test_load_revenue_history_signed_convention_still_documented():
    """The existing MRR plus signed convention path documented in
    data_loader must remain in place. The contract is verified by a
    string search rather than by re running an integration call so
    a structural rewrite that drops the comment block lands here
    loudly."""
    import pathlib

    src = (
        pathlib.Path(__file__).resolve().parent.parent
        / "services"
        / "nova_engine"
        / "data_loader.py"
    ).read_text(encoding="utf-8")
    assert "natural-balance signed" in src
    assert "load_revenue_history" in src


def test_no_abs_on_signed_revenue_totals_in_data_loader():
    """data_loader load_revenue_history must not wrap its bucket
    sums in abs. A grep against the function source is robust to
    refactors that move helpers around."""
    import pathlib
    import re

    src = (
        pathlib.Path(__file__).resolve().parent.parent
        / "services"
        / "nova_engine"
        / "data_loader.py"
    ).read_text(encoding="utf-8")
    # Find the load_revenue_history function body and check for
    # abs( inside it.
    match = re.search(
        r"async def load_revenue_history\(.*?\nasync def ",
        src,
        re.DOTALL,
    )
    assert match is not None, "load_revenue_history function not found"
    body = match.group(0)
    assert "abs(" not in body, (
        "load_revenue_history applies abs to amounts which would drop "
        "the sign of reversal rows on income accounts."
    )
