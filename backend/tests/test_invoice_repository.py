"""Tests for the invoice data-access helper.

Covers both adapters (open-invoice snapshot source + issued-invoices
source) at the public-contract level. The storage engine sits behind
the helpers — these tests assert against the public result shape
(``OpenInvoiceQueryResult`` / ``IssuedInvoiceQueryResult``), NEVER
against database-specific operator names. Swapping the underlying
storage engine should not require these tests to change.

Every numeric assertion is computed from the synthetic fixture rows
in the same test setup. No real tenant figures appear.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from services.nova_engine.invoice_repository import (
    IssuedInvoiceQuery,
    IssuedInvoiceQueryResult,
    IssuedInvoiceRow,
    OpenInvoiceQuery,
    OpenInvoiceQueryResult,
    OpenInvoiceRow,
    query_issued_invoice_totals,
    query_open_invoice_rows,
)


# ---------------------------------------------------------------------------
# Fake adapter — captures the logical filter object the helper builds and
# returns canned aggregation results so the public contract is exercised
# end-to-end without touching the real storage engine.
# ---------------------------------------------------------------------------


class _FakeAggregateCursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    async def to_list(self, length: int | None = None):
        return list(self._docs[: length or len(self._docs)])


class _FakeFindCursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = list(docs)

    def __aiter__(self):
        async def _gen():
            for d in self._docs:
                yield d
        return _gen()


class _FakeCollection:
    def __init__(self) -> None:
        self.aggregate_pipelines: list[list[dict]] = []
        self.find_filters: list[dict] = []
        self._aggregate_result: list[dict] = []
        self._find_docs: list[dict] = []

    def set_aggregate_result(self, docs: list[dict]) -> None:
        self._aggregate_result = docs

    def set_find_docs(self, docs: list[dict]) -> None:
        self._find_docs = docs

    def aggregate(self, pipeline: list[dict]) -> _FakeAggregateCursor:
        self.aggregate_pipelines.append(pipeline)
        return _FakeAggregateCursor(self._aggregate_result)

    def find(self, query: dict, projection: dict | None = None) -> _FakeFindCursor:
        self.find_filters.append(query)
        return _FakeFindCursor(self._find_docs)


class _FakeDb:
    def __init__(self) -> None:
        self.reports_ar_aging_detail = _FakeCollection()
        self.ar_invoices = _FakeCollection()


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Open-invoice helper — server-side filtering pushed into the query layer
# ---------------------------------------------------------------------------


class TestOpenInvoiceHelperPushesFilters:
    """Date / bucket / search filters MUST appear in the aggregation
    pipeline that the helper hands to the storage engine. The
    assertions below probe ``aggregate_pipelines`` directly to prove
    the filter object reached the data-access query, not the tool's
    Python loop."""

    def _aggregate_facet_for_filter(self, db: _FakeDb, filt: OpenInvoiceQuery) -> dict:
        # Helper canned result — totals + preview. Tests below assert
        # against the public OpenInvoiceQueryResult; what's in the
        # canned result is fixture-defined, not real data.
        canned = [{
            "preview": [
                {
                    "invoice_number": "A-001",
                    "invoice_date": "2030-04-12",
                    "due_date": "2030-05-12",
                    "customer": "Customer Alpha",
                    "amount": 100.0,
                    "open_balance": 100.0,
                    "bucket": "Current",
                    "location": "HQ",
                },
                {
                    "invoice_number": "A-002",
                    "invoice_date": "2030-04-20",
                    "due_date": "2030-05-20",
                    "customer": "Customer Beta",
                    "amount": 250.0,
                    "open_balance": 50.0,
                    "bucket": "1 - 30",
                    "location": None,
                },
            ],
            "totals": [{"_id": None, "count": 2, "amount": 350.0, "open_balance": 150.0}],
            "snapshot_meta": [{"synced_at": "2030-04-21T03:00:00Z"}],
        }]
        db.reports_ar_aging_detail.set_aggregate_result(canned)
        return _run(query_open_invoice_rows(filt, db=db))

    def test_date_from_pushed_into_query(self):
        db = _FakeDb()
        filt = OpenInvoiceQuery(date_from="2030-04-01", date_to="2030-04-30", limit=50)
        self._aggregate_facet_for_filter(db, filt)
        # The pipeline's $match stage must reference the date window.
        # Tests assert pipeline shape contains the logical filter
        # values — NOT specific operator names beyond what's already
        # the existing storage engine convention. We accept that
        # storage engines that match this contract will use a similar
        # range operator pattern. If a future adapter changes the
        # syntax, this assertion can be relaxed.
        pipelines = db.reports_ar_aging_detail.aggregate_pipelines
        assert len(pipelines) == 1
        flat = repr(pipelines[0])
        assert "2030-04-01" in flat, "date_from must reach the query layer"
        assert "2030-04-30" in flat, "date_to must reach the query layer"

    def test_bucket_pushed_into_query(self):
        db = _FakeDb()
        # "Current" carries no regex-special chars, so re.escape() is a
        # no-op and the literal value appears verbatim in the pipeline.
        # That's enough to prove the bucket filter reached the data
        # layer; the regex-escape behaviour is a stable storage-engine
        # convention probed separately by hand if needed.
        filt = OpenInvoiceQuery(bucket="Current", limit=10)
        self._aggregate_facet_for_filter(db, filt)
        flat = repr(db.reports_ar_aging_detail.aggregate_pipelines[0])
        assert "Current" in flat
        # Field path on the unwound line item must be referenced.
        assert "line_items.section" in flat

    def test_search_pushed_into_query(self):
        db = _FakeDb()
        filt = OpenInvoiceQuery(search="alpha", limit=10)
        self._aggregate_facet_for_filter(db, filt)
        flat = repr(db.reports_ar_aging_detail.aggregate_pipelines[0])
        assert "alpha" in flat


class TestOpenInvoiceHelperReturnsServerComputedTotals:
    def test_totals_come_from_aggregation_result(self):
        # The canned aggregation pretends the storage engine summed
        # rows server-side. The helper should surface those totals
        # verbatim in the public result.
        canned_count = 7
        canned_amount = 1234.56
        canned_open_balance = 999.99
        canned = [{
            "preview": [
                {
                    "invoice_number": "X-1",
                    "invoice_date": "2030-06-15",
                    "amount": 100.0,
                    "open_balance": 25.0,
                }
            ],
            "totals": [{
                "_id": None,
                "count": canned_count,
                "amount": canned_amount,
                "open_balance": canned_open_balance,
            }],
            "snapshot_meta": [{"synced_at": "2030-06-16T00:00:00Z"}],
        }]
        db = _FakeDb()
        db.reports_ar_aging_detail.set_aggregate_result(canned)
        # No find docs needed — class_name not requested, so the
        # helper's best-effort class lookup hits an empty collection.
        result = _run(query_open_invoice_rows(OpenInvoiceQuery(limit=5), db=db))
        assert isinstance(result, OpenInvoiceQueryResult)
        assert result.total_matched == canned_count
        assert result.total_amount == canned_amount
        assert result.total_open_balance == canned_open_balance
        # rows_returned is len(rows), preview_truncated reflects the
        # gap between canned_count and the single preview row above.
        assert result.rows_returned == 1
        assert result.preview_truncated is True
        assert result.snapshot_synced_at == "2030-06-16T00:00:00Z"
        assert result.used_post_query_filtering is False


class TestOpenInvoiceHelperClassFilterFallback:
    """When class_name is supplied, the adapter post-filters the
    preview window via a side-join and recomputes totals. The result
    flag ``used_post_query_filtering`` must be True."""

    def test_class_filter_recomputes_totals_in_helper(self):
        canned_aggregate = [{
            "preview": [
                {"invoice_number": "C-1", "invoice_date": "2030-07-05",
                 "customer": "Customer Gamma", "amount": 200.0,
                 "open_balance": 75.0, "bucket": "Current"},
                {"invoice_number": "C-2", "invoice_date": "2030-07-09",
                 "customer": "Customer Delta", "amount": 400.0,
                 "open_balance": 50.0, "bucket": "1 - 30"},
                {"invoice_number": "C-3", "invoice_date": "2030-07-12",
                 "customer": "Customer Epsilon", "amount": 600.0,
                 "open_balance": 0.0, "bucket": "Current"},
            ],
            "totals": [{"_id": None, "count": 3, "amount": 1200.0, "open_balance": 125.0}],
            "snapshot_meta": [{"synced_at": "2030-07-13T00:00:00Z"}],
        }]
        canned_class_join = [
            {"raw_data": {"DocNumber": "C-1", "Line": [
                {"SalesItemLineDetail": {"ClassRef": {"name": "ClassA"}}}
            ]}},
            {"raw_data": {"DocNumber": "C-2", "Line": [
                {"SalesItemLineDetail": {"ClassRef": {"name": "ClassB"}}}
            ]}},
            {"raw_data": {"DocNumber": "C-3", "Line": [
                {"SalesItemLineDetail": {"ClassRef": {"name": "ClassA"}}}
            ]}},
        ]
        db = _FakeDb()
        db.reports_ar_aging_detail.set_aggregate_result(canned_aggregate)
        db.ar_invoices.set_find_docs(canned_class_join)
        # Filter to ClassA — only C-1 and C-3 should survive.
        filt = OpenInvoiceQuery(class_name="ClassA", limit=10)
        result = _run(query_open_invoice_rows(filt, db=db))
        assert result.used_post_query_filtering is True
        assert result.total_matched == 2  # C-1 and C-3
        # Totals must be recomputed from the surviving fixture rows.
        expected_amount = 200.0 + 600.0
        expected_open_balance = 75.0 + 0.0
        assert result.total_amount == round(expected_amount, 2)
        assert result.total_open_balance == round(expected_open_balance, 2)
        # The surviving rows are class A.
        assert {r.invoice_number for r in result.rows} == {"C-1", "C-3"}
        for r in result.rows:
            assert r.class_name == "ClassA"


# ---------------------------------------------------------------------------
# Issued-invoice helper — date / customer / search pushed into the query
# ---------------------------------------------------------------------------


class TestIssuedInvoiceHelperPushesDateFilter:
    def _run_with_canned(
        self,
        filt: IssuedInvoiceQuery,
        *,
        canned_count: int,
        canned_total: float,
        canned_preview: list[dict] | None = None,
    ):
        db = _FakeDb()
        canned = [{
            "preview": canned_preview or [],
            "totals": [{"_id": None, "count": canned_count, "total": canned_total}],
            "snapshot_meta": [{"synced_at": "2031-08-01T00:00:00Z"}],
        }]
        db.ar_invoices.set_aggregate_result(canned)
        return db, _run(query_issued_invoice_totals(filt, db=db))

    def test_date_window_pushed_into_query(self):
        filt = IssuedInvoiceQuery(date_from="2031-07-01", date_to="2031-07-31")
        db, _ = self._run_with_canned(filt, canned_count=0, canned_total=0.0)
        flat = repr(db.ar_invoices.aggregate_pipelines[0])
        assert "2031-07-01" in flat
        assert "2031-07-31" in flat

    def test_customer_pushed_into_query(self):
        filt = IssuedInvoiceQuery(
            date_from="2031-07-01", date_to="2031-07-31", customer="Acme"
        )
        db, _ = self._run_with_canned(filt, canned_count=0, canned_total=0.0)
        flat = repr(db.ar_invoices.aggregate_pipelines[0])
        assert "Acme" in flat

    def test_search_pushed_into_query(self):
        # "alphasearch" carries no regex-special chars so re.escape is
        # a no-op and the literal substring proves the filter reached
        # the data layer.
        filt = IssuedInvoiceQuery(
            date_from="2031-07-01", date_to="2031-07-31", search="alphasearch"
        )
        db, _ = self._run_with_canned(filt, canned_count=0, canned_total=0.0)
        flat = repr(db.ar_invoices.aggregate_pipelines[0])
        assert "alphasearch" in flat
        # Search must apply across customer name OR doc number.
        assert "raw_data.DocNumber" in flat or "raw_data.CustomerRef.name" in flat

    def test_location_pushed_into_query(self):
        filt = IssuedInvoiceQuery(
            date_from="2031-07-01", date_to="2031-07-31", location="HQ"
        )
        db, _ = self._run_with_canned(filt, canned_count=0, canned_total=0.0)
        flat = repr(db.ar_invoices.aggregate_pipelines[0])
        assert "HQ" in flat


class TestIssuedInvoiceHelperTotalsAndScope:
    def test_totals_and_paid_closed_flag(self):
        canned_count = 12
        canned_total = 9876.54
        canned_preview = [
            {
                "invoice_date": "2031-08-05",
                "doc_num": "I-100",
                "customer": "Customer Theta",
                "total_amount": 200.0,
                "balance": 0.0,  # closed/paid
                "status": "EmailSent",
                "location": "HQ",
            },
            {
                "invoice_date": "2031-08-15",
                "doc_num": "I-101",
                "customer": "Customer Iota",
                "total_amount": 500.0,
                "balance": 250.0,  # still open
                "status": "NotSet",
                "location": None,
            },
        ]
        filt = IssuedInvoiceQuery(date_from="2031-08-01", date_to="2031-08-31", limit=50)
        db = _FakeDb()
        db.ar_invoices.set_aggregate_result([{
            "preview": canned_preview,
            "totals": [{"_id": None, "count": canned_count, "total": canned_total}],
            "snapshot_meta": [{"synced_at": "2031-09-01T00:00:00Z"}],
        }])
        result = _run(query_issued_invoice_totals(filt, db=db))
        assert isinstance(result, IssuedInvoiceQueryResult)
        assert result.invoice_count == canned_count
        assert result.total_invoice_amount == round(canned_total, 2)
        # Both paid and open rows came back — proves the issued source
        # carries closed invoices alongside open ones.
        balances = {r.balance for r in result.rows}
        assert 0.0 in balances and 250.0 in balances
        assert result.includes_paid_and_closed is True
        assert result.snapshot_synced_at == "2031-09-01T00:00:00Z"
        assert result.used_post_query_filtering is False


class TestIssuedInvoiceHelperIncludeRowsFalse:
    def test_rows_omitted_when_include_rows_false(self):
        canned_count = 5
        canned_total = 1500.0
        filt = IssuedInvoiceQuery(
            date_from="2032-01-01",
            date_to="2032-01-31",
            include_rows=False,
        )
        db = _FakeDb()
        db.ar_invoices.set_aggregate_result([{
            "preview": [],  # canned: helper passed limit=0 to the query
            "totals": [{"_id": None, "count": canned_count, "total": canned_total}],
            "snapshot_meta": [],
        }])
        result = _run(query_issued_invoice_totals(filt, db=db))
        assert result.invoice_count == canned_count
        assert result.total_invoice_amount == canned_total
        assert result.rows == []
        assert result.rows_returned == 0
        # preview_truncated is False when include_rows=False even if
        # invoice_count > 0 — there's no preview to truncate.
        assert result.preview_truncated is False


class TestIssuedInvoiceClassFallback:
    """class_name is post-filtered in Python via a side-join. The
    helper flags this as ``used_post_query_filtering=True``."""

    def test_class_filter_marks_post_query_filtering(self):
        canned_preview = [
            {"invoice_date": "2032-03-01", "doc_num": "K-1",
             "customer": "Customer Kappa", "total_amount": 300.0,
             "balance": 100.0, "status": "EmailSent", "location": None},
            {"invoice_date": "2032-03-04", "doc_num": "K-2",
             "customer": "Customer Lambda", "total_amount": 700.0,
             "balance": 0.0, "status": "EmailSent", "location": None},
        ]
        canned_class_join = [
            {"raw_data": {"DocNumber": "K-1", "Line": [
                {"SalesItemLineDetail": {"ClassRef": {"name": "ClassQ"}}}
            ]}},
            {"raw_data": {"DocNumber": "K-2", "Line": [
                {"SalesItemLineDetail": {"ClassRef": {"name": "ClassR"}}}
            ]}},
        ]
        filt = IssuedInvoiceQuery(
            date_from="2032-03-01", date_to="2032-03-31", class_name="ClassR",
        )
        db = _FakeDb()
        db.ar_invoices.set_aggregate_result([{
            "preview": canned_preview,
            "totals": [{"_id": None, "count": 2, "total": 1000.0}],
            "snapshot_meta": [],
        }])
        db.ar_invoices.set_find_docs(canned_class_join)
        result = _run(query_issued_invoice_totals(filt, db=db))
        assert result.used_post_query_filtering is True
        # Only K-2 survives. Recomputed total reflects the surviving
        # fixture rows, not the canned aggregate total.
        assert result.invoice_count == 1
        assert result.total_invoice_amount == round(700.0, 2)
        assert result.rows[0].doc_num == "K-2"
