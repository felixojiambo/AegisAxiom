"""Tests for backend/routes/finance_revenue.py.

Pins the dashboard revenue-trend route to account-driven, signed-amount
classification. The canonical rule across Nova and AEGISAXIOM revenue logic:

  * Revenue category is determined by GL account, not by txn_type.
  * Every row whose account maps into a revenue bucket is included
    with its signed amount.
  * Rows on income accounts with txn_type Expense / Bill / Journal Entry
    (real QBO reversal shapes) are signed reductions to revenue, NOT
    expenses.
  * True expense-account rows are not revenue regardless of sign.

Synthetic fixtures and computed expected values only. No live Mongo.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


_BASIS = "accrual"


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------

# All synthetic rows belong to a single calendar month so the aggregation
# month bucket is deterministic regardless of when the test runs. The
# route's months window includes the current month plus the prior 11, so
# the latest month in the synthetic data must be the run-day's month for
# the rows to fall inside the window. Tests assert against the actual
# bucket the route used rather than a hardcoded YYYY-MM string.

REVENUE_ACCOUNT_PS = "50101 Professional Services Income"
REVENUE_ACCOUNT_FS = "50201 Field Income:Field Income - Service"
REVENUE_ACCOUNT_WHOLESALE = "50400 Wholesale Income"
REVENUE_ACCOUNT_MRR = "50300 Other Income:Contract Commission Income"
EXPENSE_ACCOUNT_PAYROLL = "70601 Payroll Expenses"
EXPENSE_ACCOUNT_ADMIN = "70205 Admin Expenses:Office"


def _make_user(permissions=None):
    """Return a dict — routes call current_user.model_dump() on Pydantic
    objects but pass dicts through unchanged. A plain dict skips the
    Pydantic import entirely and works the same."""
    perms = permissions if permissions is not None else [
        "finance.revenue.view",
    ]
    return {
        "user_id": "u_synth_rev",
        "email": "tester@aegisaxiom.io",
        "role": "finance_admin",
        "permissions": perms,
    }


class _FakeAggregateCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    async def to_list(self, length=None):
        return list(self._docs)


class _FakeGLCollection:
    """Pretends to be the general_ledger collection.

    Mongo would execute the $group on the server. The tests pre-aggregate
    by hand so the cursor returns rows in the exact shape the route
    consumes after $group: {"_id": {"ym": ..., "account_name": ...},
    "amount": ...}. That keeps the test scope on the Python-side
    categorisation + sign handling — exactly the layer the fix touches.
    """

    def __init__(self, pre_grouped_rows):
        self._rows = list(pre_grouped_rows)

    def aggregate(self, pipeline):
        return _FakeAggregateCursor(self._rows)


class _FakeQboDb:
    def __init__(self, pre_grouped_rows):
        self._gl = _FakeGLCollection(pre_grouped_rows)

    def __getitem__(self, name):
        if name == "general_ledger":
            return self._gl
        raise KeyError(name)


def _agg_row(ym, account_name, amount):
    return {
        "_id": {"ym": ym, "account_name": account_name},
        "amount": float(amount),
    }


def _current_ym():
    """The route anchors its window on the current UTC month, so synthetic
    data must land in the same month for the row to fall inside the
    aggregation window."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date()
    return f"{today.year}-{today.month:02d}"


def _build_app(user, fake_db):
    from routes.finance_revenue import router
    from auth_helper import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api/finance")
    app.dependency_overrides[get_current_user] = lambda: user
    return app


# ---------------------------------------------------------------------------
# Revenue trend — reversal handling
# ---------------------------------------------------------------------------


class TestRevenueTrendReversalHandling:
    def _hit(self, pre_grouped_rows):
        user = _make_user()
        db = _FakeQboDb(pre_grouped_rows)
        app = _build_app(user, db)
        with patch("routes.finance_revenue.get_qbo_db", return_value=db):
            client = TestClient(app)
            resp = client.get("/api/finance/revenue/trend?months=12")
        return resp

    def _month_bucket(self, body, ym):
        for row in body["series"]:
            if row["month"] == ym:
                return row
        raise AssertionError(f"month {ym} missing from series: {body}")

    def test_expense_txn_on_revenue_account_reduces_revenue(self):
        """A row that lands at $group time as a net-negative amount on an
        income account (e.g. an Expense reversal hitting 50400) reduces
        revenue rather than being dropped or moved into expense."""
        ym = _current_ym()
        invoice_amount = 5000.0
        expense_reversal_amount = -754.0
        rows = [
            _agg_row(ym, REVENUE_ACCOUNT_WHOLESALE, invoice_amount),
            _agg_row(ym, REVENUE_ACCOUNT_WHOLESALE, expense_reversal_amount),
        ]
        # The aggregation stage groups by account_name so both rows are
        # pre-summed in production; here we emit them as separate rows
        # to exercise the Python loop on multiple negative-and-positive
        # entries hitting the same revenue bucket.
        resp = self._hit(rows)
        assert resp.status_code == 200
        bucket = self._month_bucket(resp.json(), ym)
        expected = invoice_amount + expense_reversal_amount
        assert bucket["revenue"] == pytest.approx(expected)
        assert bucket["expense"] == pytest.approx(0.0)
        assert bucket["negative_total"] == pytest.approx(expense_reversal_amount)
        assert bucket["negative_row_count"] == 1

    def test_bill_txn_on_revenue_account_reduces_revenue(self):
        """A Bill posted to an income account (50300 Contract Commission
        Income reversal) must reduce revenue, not bump expense."""
        ym = _current_ym()
        positive = 1500.0
        bill_reversal = -740.19
        rows = [
            _agg_row(ym, REVENUE_ACCOUNT_MRR, positive),
            _agg_row(ym, REVENUE_ACCOUNT_MRR, bill_reversal),
        ]
        resp = self._hit(rows)
        assert resp.status_code == 200
        bucket = self._month_bucket(resp.json(), ym)
        assert bucket["revenue"] == pytest.approx(positive + bill_reversal)
        assert bucket["expense"] == pytest.approx(0.0)

    def test_journal_entry_on_revenue_account_is_signed_revenue(self):
        """A Journal Entry reversal hitting 50101 contributes its signed
        amount to revenue — not dropped because txn_type ≠ Invoice."""
        ym = _current_ym()
        invoice = 10000.0
        je_reversal = -10000.0
        je_correction = 250.0
        rows = [
            _agg_row(ym, REVENUE_ACCOUNT_PS, invoice),
            _agg_row(ym, REVENUE_ACCOUNT_PS, je_reversal),
            _agg_row(ym, REVENUE_ACCOUNT_PS, je_correction),
        ]
        resp = self._hit(rows)
        assert resp.status_code == 200
        bucket = self._month_bucket(resp.json(), ym)
        expected = invoice + je_reversal + je_correction
        assert bucket["revenue"] == pytest.approx(expected)

    def test_true_expense_account_row_is_not_revenue(self):
        """Rows on a real expense account are excluded from revenue and,
        when signed positive, contribute to the expense bucket."""
        ym = _current_ym()
        rows = [
            _agg_row(ym, REVENUE_ACCOUNT_PS, 1000.0),
            _agg_row(ym, EXPENSE_ACCOUNT_PAYROLL, 600.0),
            _agg_row(ym, EXPENSE_ACCOUNT_ADMIN, 200.0),
        ]
        resp = self._hit(rows)
        assert resp.status_code == 200
        bucket = self._month_bucket(resp.json(), ym)
        assert bucket["revenue"] == pytest.approx(1000.0)
        assert bucket["expense"] == pytest.approx(800.0)

    def test_net_revenue_equals_positive_plus_negative(self):
        """positive_total + negative_total must equal the reported revenue
        figure for every month — the canonical invariant."""
        ym = _current_ym()
        rows = [
            _agg_row(ym, REVENUE_ACCOUNT_PS, 10000.0),
            _agg_row(ym, REVENUE_ACCOUNT_FS, 5000.0),
            _agg_row(ym, REVENUE_ACCOUNT_PS, -1500.0),
            _agg_row(ym, REVENUE_ACCOUNT_WHOLESALE, -754.0),
            _agg_row(ym, REVENUE_ACCOUNT_MRR, -740.19),
            _agg_row(ym, REVENUE_ACCOUNT_PS, -10000.0),
        ]
        resp = self._hit(rows)
        assert resp.status_code == 200
        bucket = self._month_bucket(resp.json(), ym)
        # round(x, 2) is the route's own rounding; compare with the same.
        expected_revenue = round(sum(r["amount"] for r in rows), 2)
        expected_positive = round(
            sum(r["amount"] for r in rows if r["amount"] > 0), 2
        )
        expected_negative = round(
            sum(r["amount"] for r in rows if r["amount"] < 0), 2
        )
        assert bucket["revenue"] == pytest.approx(expected_revenue)
        assert bucket["positive_total"] == pytest.approx(expected_positive)
        assert bucket["negative_total"] == pytest.approx(expected_negative)
        # Net invariant. positive plus negative equals reported revenue.
        # The previous net_total alias field has been removed in J4 so
        # callers read revenue directly.
        assert bucket["positive_total"] + bucket["negative_total"] == pytest.approx(
            bucket["revenue"]
        )
        assert "net_total" not in bucket
        # And the route does not call abs on amounts.
        abs_total = sum(abs(r["amount"]) for r in rows)
        assert bucket["revenue"] != pytest.approx(abs_total)

    def test_response_shape_preserves_legacy_fields(self):
        """Frontend consumers read month, revenue, and expense per row.
        Confirm those fields exist on every row even when no reversal
        metadata is meaningful (zero activity month). After J4 the row
        no longer carries reversal_note or net_total."""
        ym = _current_ym()
        resp = self._hit([_agg_row(ym, REVENUE_ACCOUNT_PS, 100.0)])
        assert resp.status_code == 200
        body = resp.json()
        assert "series" in body
        for row in body["series"]:
            assert "month" in row
            assert "revenue" in row
            assert "expense" in row
            # J4. Per row reversal_note and net_total are gone.
            assert "reversal_note" not in row
            assert "net_total" not in row

    def test_reversal_note_appears_once_at_top_level(self):
        """J4. The reversal_note string lives once on the response
        envelope rather than being repeated on every series row. The
        previous per row copy emitted the same explanation twelve
        times for a year of data and risked becoming a contract that
        callers depended on."""
        ym = _current_ym()
        resp = self._hit([_agg_row(ym, REVENUE_ACCOUNT_PS, 100.0)])
        assert resp.status_code == 200
        body = resp.json()
        assert "reversal_note" in body
        assert "Negative rows" in body["reversal_note"]
        for row in body["series"]:
            assert "reversal_note" not in row


# ---------------------------------------------------------------------------
# Permission gate sanity — preserves existing 403 behaviour
# ---------------------------------------------------------------------------


def test_revenue_trend_permission_denied():
    user = _make_user(permissions=[])
    db = _FakeQboDb([])
    app = _build_app(user, db)
    with patch("routes.finance_revenue.get_qbo_db", return_value=db):
        client = TestClient(app)
        resp = client.get("/api/finance/revenue/trend?months=3")
    assert resp.status_code == 403
