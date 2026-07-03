"""QBO payables timing + weekly finance report contract.

Three layers under test.

  Helper. services.finance.qbo_payables_report covers payables
    metrics, week bucketing, the data health audit, and the full
    report builder. Pure compute paths run without Mongo or QBO.

  Route. routes.finance_qbo_payables exposes the three JSON / CSV
    endpoints. Tests stand up a tiny FastAPI app and dependency
    inject a fake Mongo db so the endpoints exercise auth, perm,
    pathing, and the structured error envelopes.

  Dispatcher. services.ai.agents.tools.nova_tools fans the three
    new tool names to the helper. Tests verify that the assistant
    surface never returns the refusal text the spec calls out and
    quotes the structured metric payload verbatim.

Synthetic fixtures only. No real customer, vendor, or financial
numbers. The fake Mongo collections accept the same find / find_one /
aggregate / to_list calls the production code uses.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


# ---------------------------------------------------------------------------
# Helper level. Pure compute over normalized rows.
# ---------------------------------------------------------------------------


def _ap_row(
    *,
    qbo_bill_id="bill_synth",
    txn_date="2099-04-01",
    due_date="2099-04-15",
    balance=0.0,
    total_amount=1000.0,
    vendor="vendor_synth",
    payment_date=None,
    linked_txn_date=None,
):
    raw_data: dict[str, Any] = {"VendorRef": {"name": vendor}}
    if payment_date is not None:
        raw_data["MetaData"] = {"LastUpdatedTime": payment_date}
    if linked_txn_date is not None:
        raw_data["LinkedTxn"] = [
            {"TxnType": "BillPayment", "TxnDate": linked_txn_date},
        ]
    return {
        "qbo_bill_id": qbo_bill_id,
        "doc_number": qbo_bill_id,
        "txn_date": txn_date,
        "due_date": due_date,
        "balance": balance,
        "total_amount": total_amount,
        "vendor_ref": {"name": vendor},
        "raw_data": raw_data,
    }


def _ar_row(
    *,
    qbo_invoice_id="inv_synth",
    txn_date="2099-04-10",
    due_date="2099-05-10",
    balance=500.0,
    total_amount=500.0,
    customer="customer_synth",
):
    return {
        "qbo_invoice_id": qbo_invoice_id,
        "doc_number": qbo_invoice_id,
        "txn_date": txn_date,
        "due_date": due_date,
        "balance": balance,
        "total_amount": total_amount,
        "customer_ref": {"name": customer},
    }


def _normalize_all_ap(rows):
    from services.finance.qbo_payables_report import _normalize_ap_row

    return [_normalize_ap_row(r) for r in rows]


def _normalize_all_ar(rows):
    from services.finance.qbo_payables_report import _normalize_ar_row

    return [_normalize_ar_row(r) for r in rows]


class TestPayablesMetrics:
    def test_paid_bill_average_payment_days(self):
        from services.finance.qbo_payables_report import calculate_payables_metrics

        rows = _normalize_all_ap(
            [
                # Paid: bill 04-01, payment 04-21 via LinkedTxn → 20 days.
                _ap_row(
                    qbo_bill_id="b1", txn_date="2099-04-01",
                    balance=0.0, total_amount=1000.0,
                    linked_txn_date="2099-04-21",
                ),
                # Paid: bill 04-05, payment 04-15 → 10 days.
                _ap_row(
                    qbo_bill_id="b2", txn_date="2099-04-05",
                    balance=0.0, total_amount=500.0,
                    linked_txn_date="2099-04-15",
                ),
            ]
        )
        m = calculate_payables_metrics(rows, "2099-06-01")
        assert m["average_paid_bill_payment_days"]["value"] == pytest.approx(15.0)
        assert m["average_paid_bill_payment_days"]["count"] == 2
        assert m["average_paid_bill_payment_days"]["excluded_count"] == 0

    def test_open_bill_average_age(self):
        from services.finance.qbo_payables_report import calculate_payables_metrics

        rows = _normalize_all_ap(
            [
                _ap_row(qbo_bill_id="o1", txn_date="2099-05-01", balance=1000.0, total_amount=1000.0),
                _ap_row(qbo_bill_id="o2", txn_date="2099-05-15", balance=500.0, total_amount=500.0),
            ]
        )
        m = calculate_payables_metrics(rows, "2099-06-01")
        # 31 days + 17 days, mean = 24.0
        assert m["average_open_bill_age_days"]["value"] == pytest.approx(24.0)
        assert m["average_open_bill_age_days"]["count"] == 2

    def test_average_days_until_due(self):
        from services.finance.qbo_payables_report import calculate_payables_metrics

        rows = _normalize_all_ap(
            [
                # Due 5 days from now.
                _ap_row(qbo_bill_id="d1", txn_date="2099-05-01", due_date="2099-06-06", balance=200.0, total_amount=200.0),
                # Due 10 days ago (overdue, negative days until due).
                _ap_row(qbo_bill_id="d2", txn_date="2099-04-01", due_date="2099-05-22", balance=300.0, total_amount=300.0),
            ]
        )
        m = calculate_payables_metrics(rows, "2099-06-01")
        # (5 + -10) / 2 = -2.5
        assert m["average_days_until_due"]["value"] == pytest.approx(-2.5)
        assert m["average_days_until_due"]["count"] == 2

    def test_overdue_open_payables_total(self):
        from services.finance.qbo_payables_report import calculate_payables_metrics

        rows = _normalize_all_ap(
            [
                # Overdue: due before as_of.
                _ap_row(qbo_bill_id="ov", txn_date="2099-04-01", due_date="2099-04-30", balance=750.0, total_amount=750.0),
                # Not overdue: due after as_of.
                _ap_row(qbo_bill_id="ok", txn_date="2099-05-15", due_date="2099-07-15", balance=250.0, total_amount=250.0),
            ]
        )
        m = calculate_payables_metrics(rows, "2099-06-01")
        assert m["open_payables_total"] == pytest.approx(1000.0)
        assert m["overdue_open_payables_total"] == pytest.approx(750.0)

    def test_missing_bill_date_excluded_with_warning(self):
        from services.finance.qbo_payables_report import calculate_payables_metrics

        rows = _normalize_all_ap(
            [
                _ap_row(qbo_bill_id="good", txn_date="2099-05-01", balance=400.0, total_amount=400.0),
                _ap_row(qbo_bill_id="bad", txn_date=None, balance=400.0, total_amount=400.0),
            ]
        )
        m = calculate_payables_metrics(rows, "2099-06-01")
        # Only the good row contributes to the open-age sample.
        assert m["average_open_bill_age_days"]["count"] == 1
        assert m["average_open_bill_age_days"]["excluded_count"] == 1
        assert any("bill date" in w.lower() for w in m["average_open_bill_age_days"]["warnings"])

    def test_missing_payment_date_excluded_from_paid_cycle(self):
        from services.finance.qbo_payables_report import calculate_payables_metrics

        rows = _normalize_all_ap(
            [
                # Paid but no LinkedTxn and no LastUpdatedTime → no payment date.
                _ap_row(qbo_bill_id="paidmissing", txn_date="2099-04-01", balance=0.0, total_amount=1000.0),
            ]
        )
        m = calculate_payables_metrics(rows, "2099-06-01")
        assert m["average_paid_bill_payment_days"]["value"] is None
        assert m["average_paid_bill_payment_days"]["excluded_count"] >= 1
        assert any("payment date" in w.lower() or "billpayment" in w.lower()
                   for w in m["average_paid_bill_payment_days"]["warnings"])

    def test_open_bills_not_in_paid_metric(self):
        from services.finance.qbo_payables_report import calculate_payables_metrics

        rows = _normalize_all_ap(
            [
                _ap_row(qbo_bill_id="o", txn_date="2099-05-01", balance=500.0, total_amount=500.0),
            ]
        )
        m = calculate_payables_metrics(rows, "2099-06-01")
        # Paid metric saw zero paid samples.
        assert m["average_paid_bill_payment_days"]["count"] == 0
        # Open metric saw one.
        assert m["average_open_bill_age_days"]["count"] == 1

    def test_paid_bills_not_in_open_metric(self):
        from services.finance.qbo_payables_report import calculate_payables_metrics

        rows = _normalize_all_ap(
            [
                _ap_row(
                    qbo_bill_id="p", txn_date="2099-04-01",
                    balance=0.0, total_amount=500.0,
                    linked_txn_date="2099-04-10",
                ),
            ]
        )
        m = calculate_payables_metrics(rows, "2099-06-01")
        assert m["average_open_bill_age_days"]["count"] == 0
        assert m["average_paid_bill_payment_days"]["count"] == 1

    def test_handles_missing_vendor_name(self):
        from services.finance.qbo_payables_report import _normalize_ap_row

        norm = _normalize_ap_row(
            {
                "qbo_bill_id": "nv",
                "txn_date": "2099-05-01",
                "due_date": "2099-06-01",
                "balance": 100.0,
                "total_amount": 100.0,
                # No vendor_ref, no raw_data.VendorRef.
            }
        )
        assert norm["vendor"] is None
        assert norm["is_open"] is True


class TestWeekBucketing:
    def test_friday_default_groups_rows_correctly(self):
        from services.finance.qbo_payables_report import bucket_rows_by_week_ending

        rows = [
            {"balance": 100.0, "due_date": "2099-06-01"},  # Mon → wk-end Fri 06-05
            {"balance": 200.0, "due_date": "2099-06-04"},  # Thu → wk-end Fri 06-05
            {"balance": 300.0, "due_date": "2099-06-12"},  # Fri → wk-end Fri 06-12
        ]
        buckets = bucket_rows_by_week_ending(
            rows,
            date_field="due_date",
            week_ending_day="friday",
            as_of_date="2099-06-01",
            week_count=4,
        )
        # Four week buckets requested.
        assert len(buckets) == 4
        # First bucket (week ending 06-05) holds the two rows.
        first = buckets[0]
        assert first["week_ending"] == "2099-06-05"
        assert first["row_count"] == 2
        assert first["weekly_total"] == pytest.approx(300.0)
        # Second bucket (week ending 06-12) holds one row.
        second = buckets[1]
        assert second["week_ending"] == "2099-06-12"
        assert second["row_count"] == 1
        assert second["weekly_total"] == pytest.approx(300.0)

    def test_configurable_week_ending_day(self):
        """Saturday week ending shifts the bucket boundary by one day."""
        from services.finance.qbo_payables_report import bucket_rows_by_week_ending

        rows = [{"balance": 50.0, "due_date": "2099-06-05"}]  # Friday
        # With Saturday week ending, Friday belongs to the Sat 06-06 bucket.
        buckets_sat = bucket_rows_by_week_ending(
            rows,
            date_field="due_date",
            week_ending_day="saturday",
            as_of_date="2099-06-01",
            week_count=2,
        )
        assert buckets_sat[0]["week_ending"] == "2099-06-06"

    def test_horizon_excludes_out_of_range_rows(self):
        from services.finance.qbo_payables_report import bucket_rows_by_week_ending

        rows = [
            {"balance": 10.0, "due_date": "2099-06-01"},  # in range
            {"balance": 20.0, "due_date": "2099-12-31"},  # outside 4-week horizon
        ]
        buckets = bucket_rows_by_week_ending(
            rows,
            date_field="due_date",
            week_ending_day="friday",
            as_of_date="2099-06-01",
            week_count=4,
        )
        total = sum(b["weekly_total"] for b in buckets)
        assert total == pytest.approx(10.0)


class TestDataHealthAudit:
    def test_audit_counts_and_excluded_reasons(self):
        from services.finance.qbo_payables_report import audit_qbo_finance_data_health

        ap_raw = [
            _ap_row(qbo_bill_id="b1", txn_date="2099-05-01", balance=100.0, total_amount=100.0),
            _ap_row(qbo_bill_id="b2", txn_date=None, balance=50.0, total_amount=50.0),
            _ap_row(qbo_bill_id="b3", txn_date="2099-04-01", due_date=None,
                    balance=0.0, total_amount=100.0, linked_txn_date="2099-04-10"),
        ]
        ar_raw = [
            _ar_row(qbo_invoice_id="i1", txn_date="2099-05-01", balance=200.0, total_amount=200.0),
            _ar_row(qbo_invoice_id="i2", txn_date=None, balance=100.0, total_amount=100.0),
            _ar_row(qbo_invoice_id="i3", txn_date="2099-05-15", due_date=None, balance=300.0, total_amount=300.0),
        ]
        sync = {"last_successful_sync_at": "2099-06-01T00:00:00+00:00", "is_stale": False}
        audit = audit_qbo_finance_data_health(ap_raw, ar_raw, sync)
        assert audit["ap_counts"]["total"] == 3
        assert audit["ap_counts"]["open"] == 2
        assert audit["ap_counts"]["paid"] == 1
        assert audit["ap_counts"]["missing_bill_date"] == 1
        assert audit["ar_counts"]["total"] == 3
        assert audit["ar_counts"]["open"] == 3
        assert audit["ar_counts"]["missing_invoice_date"] == 1
        assert audit["ar_counts"]["open_missing_due_date"] == 1
        # Sources discovered come from the canonical collection names.
        kinds = {s["kind"] for s in audit["sources"]}
        assert {"ap_bills", "ar_invoices", "sync_log"}.issubset(kinds)
        # Sync block flows through verbatim.
        assert audit["sync"]["last_successful_sync_at"] == "2099-06-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Fake Mongo collections that mimic the motor API the route + helper use.
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def limit(self, _n):
        return self

    def sort(self, *_a, **_kw):
        return self

    async def to_list(self, length=None):
        return list(self._docs[:length] if length is not None else self._docs)


class _FakeCollection:
    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, _query=None, _projection=None):
        return _FakeCursor(self._docs)

    async def count_documents(self, _query=None):
        return len(self._docs)


class _FakeDb:
    def __init__(self, ap=None, ar=None):
        self._ap = _FakeCollection(ap or [])
        self._ar = _FakeCollection(ar or [])

    def __getitem__(self, name):
        if name == "ap_bills":
            return self._ap
        if name == "ar_invoices":
            return self._ar
        return _FakeCollection([])


# ---------------------------------------------------------------------------
# Report builder + sync warning behaviour.
# ---------------------------------------------------------------------------


class TestBuildWeeklyFinanceReport:
    @pytest.mark.asyncio
    async def test_full_report_shape(self):
        from services.finance.qbo_payables_report import build_weekly_finance_report

        db = _FakeDb(
            ap=[
                _ap_row(qbo_bill_id="ap1", txn_date="2099-05-01", due_date="2099-06-04",
                        balance=400.0, total_amount=400.0),
                _ap_row(qbo_bill_id="ap2", txn_date="2099-04-01", balance=0.0,
                        total_amount=200.0, linked_txn_date="2099-04-25"),
            ],
            ar=[
                _ar_row(qbo_invoice_id="ar1", txn_date="2099-05-01", due_date="2099-06-04",
                        balance=600.0, total_amount=600.0),
            ],
        )
        sync = {"last_successful_sync_at": "2099-06-01T00:00:00+00:00",
                "is_stale": False, "warning": None, "sync_lag_seconds": 0}
        report = await build_weekly_finance_report(
            db, as_of_date="2099-06-01", week_count=4,
            week_ending_day="friday", sync_freshness=sync,
        )
        # Six-section contract.
        for key in ("summary", "ar_weekly", "ap_weekly", "raw_ar", "raw_ap", "sync_audit"):
            assert key in report
        # Summary carries the payables metrics block.
        timing = report["summary"]["payables_timing"]
        assert "average_paid_bill_payment_days" in timing
        assert "average_open_bill_age_days" in timing
        assert "average_days_until_due" in timing
        # AP open weekly bucket holds the open bill that's due 2099-06-04.
        ap_open = report["ap_weekly"]["open"]
        assert any(b["weekly_total"] == pytest.approx(400.0) for b in ap_open)
        # AR weekly bucket holds the open invoice that's due 2099-06-04.
        assert any(b["weekly_total"] == pytest.approx(600.0) for b in report["ar_weekly"])

    @pytest.mark.asyncio
    async def test_stale_sync_warning_propagates(self):
        from services.finance.qbo_payables_report import build_weekly_finance_report

        db = _FakeDb()
        sync = {
            "last_successful_sync_at": "2099-05-25T00:00:00+00:00",
            "is_stale": True,
            "warning": "Finance data may be stale. Last successful QBO sync was 2099-05-25T00:00:00+00:00.",
            "sync_lag_seconds": 999999,
        }
        report = await build_weekly_finance_report(
            db, as_of_date="2099-06-01", sync_freshness=sync,
        )
        assert report["summary"]["is_stale"] is True
        assert any("stale" in w.lower() for w in report["warnings"])

    @pytest.mark.asyncio
    async def test_empty_lake_warns_but_does_not_refuse(self):
        from services.finance.qbo_payables_report import build_weekly_finance_report

        db = _FakeDb()
        report = await build_weekly_finance_report(db, as_of_date="2099-06-01")
        assert report["summary"]["open_payables_total"] == 0
        assert any("No AP or AR rows" in w for w in report["warnings"])


# ---------------------------------------------------------------------------
# Route layer. FastAPI app with overrides so auth + perm + Mongo are
# all dependency injected.
# ---------------------------------------------------------------------------


def _make_user(perms=None):
    # Explicit None check rather than truthy. An empty list is a valid
    # value meaning the user holds no perms; the older `perms or [...]`
    # form silently fell back to the default and made permission tests
    # pass when they should have failed.
    resolved = ["finance.working_capital.view"] if perms is None else list(perms)
    return {
        "user_id": "u_synth_fin",
        "email": "fin-tester@aegisaxiom.test",
        "role": "finance_admin",
        "permissions": resolved,
    }


def _build_app(db, user):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes.finance_qbo_payables import router
    from auth_helper import get_current_user
    from database import get_db

    app = FastAPI()
    app.include_router(router, prefix="/api/finance")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    return app, TestClient(app)


def _patch_freshness(monkeypatch, payload):
    async def _fake_freshness(_db):
        return payload
    monkeypatch.setattr(
        "routes.finance_qbo_payables.get_finance_data_freshness",
        _fake_freshness,
    )


def _patch_ai_authz(monkeypatch):
    """Neutralise the require_ai_permission dependency so the route's
    explicit _check_perm guard is the only auth check under test."""
    def _passthrough(_perm):
        def _dep():
            return None
        return _dep
    monkeypatch.setattr(
        "routes.finance_qbo_payables.require_ai_permission",
        _passthrough,
    )


class TestPayablesMetricsRoute:
    def test_response_shape(self, monkeypatch):
        _patch_freshness(monkeypatch, {
            "last_successful_sync_at": "2099-06-01T00:00:00+00:00",
            "is_stale": False,
            "warning": None,
        })
        db = _FakeDb(ap=[
            _ap_row(qbo_bill_id="r1", txn_date="2099-05-01", balance=100.0, total_amount=100.0),
        ])
        user = _make_user()
        _, client = _build_app(db, user)
        resp = client.get("/api/finance/qbo/payables-metrics?as_of=2099-06-01")
        assert resp.status_code == 200
        body = resp.json()
        assert body["as_of"] == "2099-06-01"
        assert "payables" in body
        for key in (
            "open_payables_total",
            "overdue_open_payables_total",
            "average_paid_bill_payment_days",
            "average_open_bill_age_days",
            "average_days_until_due",
        ):
            assert key in body["payables"]
        # Wire-stable formula strings.
        assert body["payables"]["average_paid_bill_payment_days"]["formula"] == (
            "payment_date - bill_date"
        )
        assert body["sync"]["is_stale"] is False

    def test_invalid_as_of_returns_structured_error(self, monkeypatch):
        _patch_freshness(monkeypatch, {"last_successful_sync_at": None, "is_stale": True})
        db = _FakeDb()
        _, client = _build_app(db, _make_user())
        resp = client.get("/api/finance/qbo/payables-metrics?as_of=not-a-date")
        assert resp.status_code == 400
        detail = resp.json().get("detail", {})
        # The 400 body is a dict per the structured error contract.
        assert detail.get("code") == "missing_required_fields"

    @pytest.mark.asyncio
    async def test_permission_denied(self, monkeypatch):
        """The route also runs require_ai_permission as a FastAPI
        dependency that is baked at import time, so this test calls
        the handler directly to verify the explicit _check_perm guard
        raises the structured 403. The dependency layer is exercised
        separately by the rest of the FastAPI test suite."""
        from fastapi import HTTPException

        from routes.finance_qbo_payables import get_payables_metrics

        async def _fake_freshness(_db):
            return {"last_successful_sync_at": None, "is_stale": True, "warning": None}
        monkeypatch.setattr(
            "routes.finance_qbo_payables.get_finance_data_freshness",
            _fake_freshness,
        )

        no_perm_user = _make_user(perms=[])
        db = _FakeDb()
        with pytest.raises(HTTPException) as exc:
            await get_payables_metrics(
                as_of=None,
                current_user=no_perm_user,
                db=db,
            )
        assert exc.value.status_code == 403
        assert exc.value.detail["code"] == "finance_permission_denied"


class TestWeeklyPlanRoute:
    def test_response_carries_six_sections(self, monkeypatch):
        _patch_freshness(monkeypatch, {
            "last_successful_sync_at": "2099-06-01T00:00:00+00:00",
            "is_stale": False,
            "warning": None,
        })
        db = _FakeDb(
            ap=[_ap_row(qbo_bill_id="wp1", txn_date="2099-05-01", balance=100.0, total_amount=100.0)],
            ar=[_ar_row(qbo_invoice_id="wp2", balance=200.0)],
        )
        _, client = _build_app(db, _make_user())
        resp = client.get("/api/finance/qbo/weekly-plan?as_of=2099-06-01&weeks=4")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("summary", "ar_weekly", "ap_weekly", "raw_ar", "raw_ap", "sync_audit"):
            assert key in body
        assert body["week_count"] == 4

    def test_export_returns_csv_download(self, monkeypatch):
        _patch_freshness(monkeypatch, {
            "last_successful_sync_at": "2099-06-01T00:00:00+00:00",
            "is_stale": False,
            "warning": None,
        })
        db = _FakeDb(
            ap=[_ap_row(qbo_bill_id="x1", txn_date="2099-05-01", balance=300.0, total_amount=300.0)],
            ar=[_ar_row(qbo_invoice_id="x2", balance=500.0)],
        )
        _, client = _build_app(db, _make_user())
        resp = client.get("/api/finance/qbo/weekly-plan/export?as_of=2099-06-01&weeks=4")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        cd = resp.headers["content-disposition"]
        assert "attachment" in cd
        assert "qbo_weekly_finance_plan_2099-06-01.csv" in cd
        body = resp.text
        assert "# Summary" in body
        assert "# Raw AP Rows" in body
        assert "# Sync Audit" in body


# ---------------------------------------------------------------------------
# Dispatcher level. Assistant routing must not return the refusal text.
# ---------------------------------------------------------------------------


_FORBIDDEN_PHRASES = (
    "i do not have access",
    "i don't have access",
    "i cannot calculate",
    "cannot calculate",
    "lack payment dates",
    "lack total credit purchases",
)


def _assert_no_refusal(blob):
    text = str(blob).lower()
    for phrase in _FORBIDDEN_PHRASES:
        assert phrase not in text, f"refusal phrase leaked: {phrase!r}"


class TestAssistantDispatch:
    @pytest.mark.asyncio
    async def test_get_qbo_payables_metrics_returns_structured_data(self, monkeypatch):
        from services.ai.agents.tools.nova_tools import execute_tool, _ScenarioState

        db = _FakeDb(ap=[
            _ap_row(qbo_bill_id="a1", txn_date="2099-05-01", balance=400.0, total_amount=400.0),
            _ap_row(qbo_bill_id="a2", txn_date="2099-04-01", balance=0.0, total_amount=200.0,
                    linked_txn_date="2099-04-20"),
        ])
        monkeypatch.setattr("services.nova_engine.db.get_qbo_db", lambda: db)

        async def _fake_freshness(_db):
            return {"last_successful_sync_at": "2099-06-01T00:00:00+00:00",
                    "is_stale": False, "warning": None}
        monkeypatch.setattr(
            "services.qbo_sync_service.get_finance_data_freshness",
            _fake_freshness,
        )

        result = await execute_tool(
            "get_qbo_payables_metrics",
            {"as_of_date": "2099-06-01"},
            _ScenarioState(),
            "u_synth",
            "sess_synth",
            None,
        )
        assert "payables" in result
        assert result["payables"]["open_payables_total"] == pytest.approx(400.0)
        assert result["sync"]["is_stale"] is False
        _assert_no_refusal(result)

    @pytest.mark.asyncio
    async def test_get_qbo_payables_metrics_handles_empty_lake_without_refusal(self, monkeypatch):
        from services.ai.agents.tools.nova_tools import execute_tool, _ScenarioState

        db = _FakeDb()
        monkeypatch.setattr("services.nova_engine.db.get_qbo_db", lambda: db)

        async def _fake_freshness(_db):
            return {"last_successful_sync_at": None, "is_stale": True,
                    "warning": "Finance data may be stale. No successful QBO sync has been recorded yet."}
        monkeypatch.setattr(
            "services.qbo_sync_service.get_finance_data_freshness",
            _fake_freshness,
        )

        result = await execute_tool(
            "get_qbo_payables_metrics",
            {"as_of_date": "2099-06-01"},
            _ScenarioState(),
            "u_synth",
            "sess_synth",
            None,
        )
        # Tool still returns structured data + warning, not a refusal.
        assert "payables" in result
        assert any("stale" in w.lower() or "No AP" in w for w in result["sync"]["warnings"] + result["warnings"])
        _assert_no_refusal(result)

    @pytest.mark.asyncio
    async def test_weekly_report_dispatch_returns_summary(self, monkeypatch):
        from services.ai.agents.tools.nova_tools import execute_tool, _ScenarioState

        db = _FakeDb(
            ap=[_ap_row(qbo_bill_id="w1", txn_date="2099-05-01",
                        due_date="2099-06-04", balance=400.0, total_amount=400.0)],
            ar=[_ar_row(qbo_invoice_id="w2", txn_date="2099-05-01",
                        due_date="2099-06-04", balance=600.0, total_amount=600.0)],
        )
        monkeypatch.setattr("services.nova_engine.db.get_qbo_db", lambda: db)

        async def _fake_freshness(_db):
            return {"last_successful_sync_at": "2099-06-01T00:00:00+00:00",
                    "is_stale": False, "warning": None}
        monkeypatch.setattr(
            "services.qbo_sync_service.get_finance_data_freshness",
            _fake_freshness,
        )

        result = await execute_tool(
            "get_qbo_weekly_finance_report",
            {"as_of_date": "2099-06-01", "weeks": 4, "week_ending_day": "friday"},
            _ScenarioState(),
            "u_synth",
            "sess_synth",
            None,
        )
        assert "summary" in result
        assert "ar_weekly" in result
        assert "ap_weekly" in result
        assert result["summary"]["open_payables_total"] == pytest.approx(400.0)
        _assert_no_refusal(result)

    @pytest.mark.asyncio
    async def test_export_dispatch_returns_download_url(self):
        from services.ai.agents.tools.nova_tools import execute_tool, _ScenarioState

        result = await execute_tool(
            "export_qbo_weekly_finance_report",
            {"as_of_date": "2099-06-01", "weeks": 4, "week_ending_day": "friday"},
            _ScenarioState(),
            "u_synth",
            "sess_synth",
            None,
        )
        url = result["download_url"]
        assert url.startswith("/api/finance/qbo/weekly-plan/export")
        assert "as_of=2099-06-01" in url
        assert "weeks=4" in url
        assert "week_ending_day=friday" in url
        assert result["filename"] == "qbo_weekly_finance_plan_2099-06-01.csv"
        _assert_no_refusal(result)
