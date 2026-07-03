"""Tests for backend/routes/finance_working_capital.py — DSO/DPO route.

Locks the dashboard working-capital route to account-driven, signed-amount
classification so reversal rows posted to income accounts don't get moved
into the expense bucket purely because their txn_type is Bill or Expense.

  * Revenue and expense are classified by GL account, not txn_type.
  * Rows on income accounts count as revenue (signed) — Bill / Expense /
    Journal Entry reversals reduce revenue, never increase expense.
  * Rows on real expense / COGS accounts count as expense.
  * AR / AP aging routes are unchanged passthroughs; only DSO/DPO touches
    the bug surface, so this file scopes there.

Synthetic fixtures and computed expected values only. No live Mongo.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


_BASIS = "accrual"


REVENUE_ACCOUNT_PS = "50101 Professional Services Income"
REVENUE_ACCOUNT_WHOLESALE = "50400 Wholesale Income"
REVENUE_ACCOUNT_MRR = "50300 Other Income:Contract Commission Income"
EXPENSE_ACCOUNT_PAYROLL = "70601 Payroll Expenses"
EXPENSE_ACCOUNT_COGS = "60201 Field Services COGS"


def _make_user(permissions=None):
    """Return a dict — routes call current_user.model_dump() on Pydantic
    objects but pass dicts through unchanged."""
    perms = permissions if permissions is not None else [
        "finance.working_capital.view",
    ]
    return {
        "user_id": "u_synth_wc",
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
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date()
    return f"{today.year}-{today.month:02d}"


def _build_app(user):
    from routes.finance_working_capital import router
    from auth_helper import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api/finance")
    app.dependency_overrides[get_current_user] = lambda: user
    return app


async def _fake_get_report(_name):
    """The DSO/DPO route also calls _get_report for AR/AP aging totals.
    Return empty docs so neither AR nor AP totals interfere with the
    revenue / expense assertions — DSO/DPO will be zero and that's OK,
    we only care about the bucket math here."""
    return {"line_items": [], "column_names": []}


# ---------------------------------------------------------------------------
# DSO / DPO — reversal handling on revenue side
# ---------------------------------------------------------------------------


class TestDsoDpoReversalHandling:
    def _hit(self, pre_grouped_rows):
        user = _make_user()
        db = _FakeQboDb(pre_grouped_rows)
        app = _build_app(user)
        with patch(
            "routes.finance_working_capital.get_qbo_db", return_value=db
        ), patch(
            "routes.finance_working_capital._get_report",
            side_effect=_fake_get_report,
        ):
            client = TestClient(app)
            resp = client.get("/api/finance/working-capital/dso-dpo?months=12")
        return resp

    def _month_bucket(self, body, ym):
        for row in body["series"]:
            if row["month"] == ym:
                return row
        raise AssertionError(f"month {ym} missing from series: {body}")

    def test_expense_txn_on_revenue_account_is_not_expense(self):
        """A row whose grouped amount is negative on an income account
        (the QBO 'Expense reversal hits 50400' shape) reduces revenue
        and does NOT bump the expense bucket."""
        ym = _current_ym()
        invoice = 5000.0
        reversal = -754.0
        rows = [
            _agg_row(ym, REVENUE_ACCOUNT_WHOLESALE, invoice),
            _agg_row(ym, REVENUE_ACCOUNT_WHOLESALE, reversal),
        ]
        resp = self._hit(rows)
        assert resp.status_code == 200
        bucket = self._month_bucket(resp.json(), ym)
        assert bucket["revenue"] == pytest.approx(invoice + reversal)
        assert bucket["expense"] == pytest.approx(0.0)

    def test_bill_txn_on_revenue_account_is_not_expense(self):
        """Bill reversal posted to 50300 reduces revenue, not expense."""
        ym = _current_ym()
        positive = 2000.0
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

    def test_journal_entry_reversal_on_revenue_account_not_dropped(self):
        """A Journal Entry posted to 50101 must not be dropped from the
        revenue bucket just because its txn_type isn't 'Invoice'."""
        ym = _current_ym()
        invoice = 10000.0
        je_reversal = -10000.0
        rows = [
            _agg_row(ym, REVENUE_ACCOUNT_PS, invoice),
            _agg_row(ym, REVENUE_ACCOUNT_PS, je_reversal),
        ]
        resp = self._hit(rows)
        assert resp.status_code == 200
        bucket = self._month_bucket(resp.json(), ym)
        # Without the fix this row would have been dropped (txn_type
        # filter never matched 'Journal Entry') so revenue would have
        # stayed at the positive figure.
        assert bucket["revenue"] == pytest.approx(invoice + je_reversal)

    def test_true_expense_account_rows_count_as_expense(self):
        """Rows on real expense / COGS accounts populate the expense
        bucket via signed amount."""
        ym = _current_ym()
        rows = [
            _agg_row(ym, REVENUE_ACCOUNT_PS, 1000.0),
            _agg_row(ym, EXPENSE_ACCOUNT_PAYROLL, 600.0),
            _agg_row(ym, EXPENSE_ACCOUNT_COGS, 400.0),
        ]
        resp = self._hit(rows)
        assert resp.status_code == 200
        bucket = self._month_bucket(resp.json(), ym)
        assert bucket["revenue"] == pytest.approx(1000.0)
        assert bucket["expense"] == pytest.approx(1000.0)

    def test_response_shape_preserved(self):
        """Frontend reads series[*].month / revenue / expense / dso / dpo
        plus top-level ar_total / ap_total. Confirm the shape is
        unchanged after the fix."""
        ym = _current_ym()
        resp = self._hit([_agg_row(ym, REVENUE_ACCOUNT_PS, 100.0)])
        assert resp.status_code == 200
        body = resp.json()
        assert "series" in body
        assert "ar_total" in body
        assert "ap_total" in body
        for row in body["series"]:
            for key in ("month", "revenue", "expense", "dso", "dpo"):
                assert key in row


# ---------------------------------------------------------------------------
# DSO non positive revenue guard. Locks the L1 fix from PR 174 cleanup.
# A month whose signed revenue is zero or negative must report DSO equal
# to zero rather than a misleading negative number.
# ---------------------------------------------------------------------------


def _make_report_factory(ar_total_value, ap_total_value):
    """Build a stand in for finance_working_capital._get_report that hands
    back AR and AP totals derived from the supplied numbers. Returns an
    async function suitable for side_effect on the patch.

    The shape mirrors what _line_items expects so the route can sum the
    Total field. Synthetic. No real customer or vendor data."""
    async def _fake(name):
        if name == "reports_ar_aging":
            return {"line_items": [{"Total": ar_total_value}], "column_names": []}
        if name == "reports_ap_aging":
            return {"line_items": [{"Total": ap_total_value}], "column_names": []}
        return {"line_items": [], "column_names": []}
    return _fake


class TestDsoNonPositiveRevenueGuard:
    def _hit_with_ar(self, pre_grouped_rows, ar_total, ap_total=0.0):
        user = _make_user()
        db = _FakeQboDb(pre_grouped_rows)
        app = _build_app(user)
        with patch(
            "routes.finance_working_capital.get_qbo_db", return_value=db
        ), patch(
            "routes.finance_working_capital._get_report",
            side_effect=_make_report_factory(ar_total, ap_total),
        ):
            client = TestClient(app)
            resp = client.get("/api/finance/working-capital/dso-dpo?months=12")
        return resp

    def _month_bucket(self, body, ym):
        for row in body["series"]:
            if row["month"] == ym:
                return row
        raise AssertionError(f"month {ym} missing from series")

    def test_negative_signed_revenue_yields_dso_zero(self):
        """A month where reversals exceed gross sales lands at signed
        revenue strictly less than zero. The previous guard allowed
        the division and returned a negative DSO. The fixed guard
        rejects non positive revenue and reports DSO equal to zero."""
        ym = _current_ym()
        rows = [
            _agg_row(ym, REVENUE_ACCOUNT_PS, 1000.0),
            _agg_row(ym, REVENUE_ACCOUNT_PS, -5000.0),
        ]
        ar_total = 9000.0
        resp = self._hit_with_ar(rows, ar_total=ar_total)
        assert resp.status_code == 200
        bucket = self._month_bucket(resp.json(), ym)
        # Signed revenue equals 1000 minus 5000 which is negative four
        # thousand. The guard must zero out DSO instead of dividing.
        assert bucket["revenue"] < 0
        assert bucket["dso"] == 0.0

    def test_zero_signed_revenue_yields_dso_zero(self):
        """Exact zero revenue is the boundary case. The previous truthy
        guard already caught zero. The strict greater than zero guard
        keeps that behaviour."""
        ym = _current_ym()
        rows = [
            _agg_row(ym, REVENUE_ACCOUNT_PS, 2500.0),
            _agg_row(ym, REVENUE_ACCOUNT_PS, -2500.0),
        ]
        resp = self._hit_with_ar(rows, ar_total=8000.0)
        assert resp.status_code == 200
        bucket = self._month_bucket(resp.json(), ym)
        assert bucket["revenue"] == pytest.approx(0.0)
        assert bucket["dso"] == 0.0

    def test_positive_signed_revenue_still_computes_dso(self):
        """Normal month. Positive signed revenue plus a positive AR
        total must still produce the expected DSO. Locks that the new
        guard did not collapse the positive path."""
        ym = _current_ym()
        rows = [
            _agg_row(ym, REVENUE_ACCOUNT_PS, 6000.0),
            _agg_row(ym, REVENUE_ACCOUNT_PS, -1000.0),
        ]
        ar_total = 5000.0
        resp = self._hit_with_ar(rows, ar_total=ar_total)
        assert resp.status_code == 200
        bucket = self._month_bucket(resp.json(), ym)
        # Signed revenue is 5000. DSO formula. AR divided by revenue
        # times thirty, then rounded to one decimal place.
        expected_dso = round(ar_total / 5000.0 * 30.0, 1)
        assert bucket["revenue"] == pytest.approx(5000.0)
        assert bucket["dso"] == pytest.approx(expected_dso)


# ---------------------------------------------------------------------------
# Permission gate sanity
# ---------------------------------------------------------------------------


def test_dso_dpo_permission_denied():
    user = _make_user(permissions=[])
    db = _FakeQboDb([])
    app = _build_app(user)
    with patch(
        "routes.finance_working_capital.get_qbo_db", return_value=db
    ), patch(
        "routes.finance_working_capital._get_report",
        side_effect=_fake_get_report,
    ):
        client = TestClient(app)
        resp = client.get("/api/finance/working-capital/dso-dpo?months=3")
    assert resp.status_code == 403
