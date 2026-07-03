"""Tool-level tests for get_open_invoices and query_invoice_totals.

These tests assert the public Nova tool contract — what the tool
branch in ``execute_tool`` does with the data-access helper's return
value, and what response shape the LLM sees. Storage-engine syntax
(aggregation pipelines, ``$match``, ``$facet``) lives behind the
``invoice_repository`` module and is exercised in
``test_invoice_repository.py``.

Every numeric expectation is computed from synthetic fixture rows in
the same test setup. No real-tenant figures appear.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from services.ai.agents.tools.nova_tools import (
    NOVA_TOOL_SCHEMAS,
    execute_tool,
)
from services.nova_engine.invoice_repository import (
    IssuedInvoiceQuery,
    IssuedInvoiceQueryResult,
    IssuedInvoiceRow,
    OpenInvoiceQuery,
    OpenInvoiceQueryResult,
    OpenInvoiceRow,
)


def _run(coro):
    return asyncio.run(coro)


def _call(tool_name: str, **tool_args) -> dict:
    return _run(execute_tool(
        tool_name=tool_name,
        tool_args=tool_args,
        state=None,
        user_id="u_test",
        session_id="s_test",
        message_id="m_test",
    ))


# ---------------------------------------------------------------------------
# Tool-schema sanity
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_query_invoice_totals_is_registered(self):
        names = {t["name"] for t in NOVA_TOOL_SCHEMAS}
        assert "query_invoice_totals" in names

    def test_get_open_invoices_description_mentions_open_only_scope(self):
        schema = next(t for t in NOVA_TOOL_SCHEMAS if t["name"] == "get_open_invoices")
        desc = schema["description"]
        lo = desc.lower()
        # Open-only scope must be unambiguous.
        assert "open" in lo
        # If the phrase "all-invoices-issued" appears, it MUST be in a
        # disclaimer that routes to the other tool — never as a
        # positive claim about this tool's coverage.
        if "all-invoices-issued" in lo or "all invoices issued" in lo:
            assert "query_invoice_totals" in desc, (
                "description mentions all-invoices-issued without "
                "routing the user to query_invoice_totals"
            )
            # The mention must be paired with a 'do not use' / 'route' /
            # 'instead' framing.
            assert any(
                phrase in lo
                for phrase in ("do not use", "route those", "instead")
            )

    def test_query_invoice_totals_description_routes_aggregate_questions(self):
        schema = next(t for t in NOVA_TOOL_SCHEMAS if t["name"] == "query_invoice_totals")
        desc = schema["description"].lower()
        assert "how much have we invoiced" in desc
        assert "issued-invoices source" in desc

    def test_descriptions_do_not_leak_database_engine_names(self):
        # Storage-engine names must not bleed into LLM-facing tool
        # contracts. These checks scan only the two invoice tool
        # descriptions — pre-existing references elsewhere are out of
        # scope for this commit.
        for name in ("get_open_invoices", "query_invoice_totals"):
            schema = next(t for t in NOVA_TOOL_SCHEMAS if t["name"] == name)
            desc = schema["description"]
            for forbidden in (
                "Mongo", "MongoDB", "$match", "$facet", "$unwind",
                "PostgreSQL", "SQL ", "aggregation pipeline",
            ):
                assert forbidden not in desc, (
                    f"{name} description leaks {forbidden!r}"
                )


# ---------------------------------------------------------------------------
# get_open_invoices — tool branch delegates to repository helper
# ---------------------------------------------------------------------------


class TestGetOpenInvoicesTool:
    def _stub_helper(self, captured: dict):
        """Return an async function we patch into the helper. Captures
        the OpenInvoiceQuery the tool built and returns a deterministic
        result."""
        async def _stub(filters: OpenInvoiceQuery, **_kw) -> OpenInvoiceQueryResult:
            captured["filt"] = filters
            return OpenInvoiceQueryResult(
                total_matched=3,
                total_amount=1500.0,
                total_open_balance=400.0,
                rows=[
                    OpenInvoiceRow(
                        invoice_number="O-1",
                        invoice_date="2030-04-12",
                        customer="Customer Alpha",
                        amount=500.0,
                        open_balance=200.0,
                        bucket="Current",
                    ),
                    OpenInvoiceRow(
                        invoice_number="O-2",
                        invoice_date="2030-04-20",
                        customer="Customer Beta",
                        amount=1000.0,
                        open_balance=200.0,
                        bucket="1 - 30",
                    ),
                ],
                rows_returned=2,
                preview_truncated=True,
                snapshot_synced_at="2030-04-21T00:00:00Z",
                used_post_query_filtering=False,
            )
        return _stub

    def test_calls_helper_with_logical_filter_object(self):
        captured: dict = {}
        with patch(
            "services.nova_engine.invoice_repository.query_open_invoice_rows",
            side_effect=self._stub_helper(captured),
        ):
            resp = _call(
                "get_open_invoices",
                date_from="2030-04-01",
                date_to="2030-04-30",
                bucket="Current",
                search="alpha",
                class_name="ClassA",
                limit=2,
            )
        assert "filt" in captured
        f = captured["filt"]
        assert isinstance(f, OpenInvoiceQuery)
        # Logical filter object carries every arg the tool received —
        # date / bucket / search / class are passed in to the helper,
        # NOT post-filtered in the tool branch.
        assert f.date_from == "2030-04-01"
        assert f.date_to == "2030-04-30"
        assert f.bucket == "Current"
        assert f.search == "alpha"
        assert f.class_name == "ClassA"
        assert f.limit == 2
        # Response carries server-computed totals + open-only scope.
        assert resp["total_matched"] == 3
        assert resp["total_amount"] == 1500.0
        assert resp["total_open_balance"] == 400.0
        assert resp["preview_truncated"] is True
        assert resp["source"]["scope"] == "open_invoices"
        assert resp["source"]["logical_source"] == "open_invoice_snapshot"

    def test_response_does_not_require_llm_to_sum_rows(self):
        # The note must explicitly tell the LLM not to sum rows.
        captured: dict = {}
        with patch(
            "services.nova_engine.invoice_repository.query_open_invoice_rows",
            side_effect=self._stub_helper(captured),
        ):
            resp = _call("get_open_invoices")
        assert "do NOT sum" in resp["note"]

    def test_invalid_date_format_short_circuits_before_helper(self):
        with patch(
            "services.nova_engine.invoice_repository.query_open_invoice_rows",
        ) as mocked:
            resp = _call("get_open_invoices", date_from="last week")
            mocked.assert_not_called()
        assert resp.get("error") == "invalid_date_format"

    def test_inverted_date_range_short_circuits(self):
        with patch(
            "services.nova_engine.invoice_repository.query_open_invoice_rows",
        ) as mocked:
            resp = _call(
                "get_open_invoices",
                date_from="2030-05-31",
                date_to="2030-05-01",
            )
            mocked.assert_not_called()
        assert resp.get("error") == "invalid_date_range"

    def test_unavailable_source_returns_clear_error(self):
        # Helper returns a default empty result — tool branch surfaces
        # unavailable_data with the open-invoice scope intact.
        async def _empty(filters, **_kw):
            return OpenInvoiceQueryResult()
        with patch(
            "services.nova_engine.invoice_repository.query_open_invoice_rows",
            side_effect=_empty,
        ):
            resp = _call("get_open_invoices")
        assert resp.get("error") == "unavailable_data"
        assert resp["source"]["scope"] == "open_invoices"


# ---------------------------------------------------------------------------
# query_invoice_totals — tool branch delegates to repository helper
# ---------------------------------------------------------------------------


class TestQueryInvoiceTotalsTool:
    def _stub_helper(self, captured: dict):
        async def _stub(filters: IssuedInvoiceQuery, **_kw) -> IssuedInvoiceQueryResult:
            captured["filt"] = filters
            return IssuedInvoiceQueryResult(
                invoice_count=4,
                total_invoice_amount=2500.0,
                rows=[
                    IssuedInvoiceRow(
                        invoice_date="2031-08-05",
                        doc_num="I-100",
                        customer="Customer Theta",
                        total_amount=500.0,
                        balance=0.0,
                    ),
                    IssuedInvoiceRow(
                        invoice_date="2031-08-15",
                        doc_num="I-101",
                        customer="Customer Iota",
                        total_amount=2000.0,
                        balance=750.0,
                    ),
                ],
                rows_returned=2,
                preview_truncated=True,
                snapshot_synced_at="2031-09-01T00:00:00Z",
                includes_paid_and_closed=True,
                used_post_query_filtering=False,
            )
        return _stub

    def test_validates_date_from(self):
        with patch(
            "services.nova_engine.invoice_repository.query_issued_invoice_totals",
        ) as mocked:
            resp = _call(
                "query_invoice_totals",
                date_from="last quarter",
                date_to="2031-08-31",
            )
            mocked.assert_not_called()
        assert resp.get("error") == "invalid_date_format"

    def test_validates_date_to(self):
        resp = _call(
            "query_invoice_totals",
            date_from="2031-08-01",
            date_to="2031-13-31",
        )
        assert resp.get("error") == "invalid_date_format"

    def test_rejects_inverted_range(self):
        resp = _call(
            "query_invoice_totals",
            date_from="2031-08-31",
            date_to="2031-08-01",
        )
        assert resp.get("error") == "invalid_date_range"

    def test_passes_all_filters_into_logical_query(self):
        captured: dict = {}
        with patch(
            "services.nova_engine.invoice_repository.query_issued_invoice_totals",
            side_effect=self._stub_helper(captured),
        ):
            _call(
                "query_invoice_totals",
                date_from="2031-08-01",
                date_to="2031-08-31",
                search="INV-99",
                customer="Acme",
                class_name="ClassA",
                location="HQ",
                status="EmailSent",
                include_rows=True,
                limit=25,
            )
        f = captured["filt"]
        assert isinstance(f, IssuedInvoiceQuery)
        assert f.date_from == "2031-08-01"
        assert f.date_to == "2031-08-31"
        assert f.search == "INV-99"
        assert f.customer == "Acme"
        assert f.class_name == "ClassA"
        assert f.location == "HQ"
        assert f.status == "EmailSent"
        assert f.include_rows is True
        assert f.limit == 25

    def test_response_shape_carries_totals_and_scope(self):
        captured: dict = {}
        with patch(
            "services.nova_engine.invoice_repository.query_issued_invoice_totals",
            side_effect=self._stub_helper(captured),
        ):
            resp = _call(
                "query_invoice_totals",
                date_from="2031-08-01",
                date_to="2031-08-31",
            )
        assert resp["invoice_count"] == 4
        assert resp["total_invoice_amount"] == 2500.0
        assert resp["rows_returned"] == 2
        assert resp["preview_truncated"] is True
        # Scope wording — issued-invoice source, includes paid+closed.
        assert resp["source"]["logical_source"] == "issued_invoices"
        assert resp["source"]["scope"] == "all_issued_invoices"
        assert resp["source"]["includes_paid_and_closed"] is True
        # The LLM-facing note must tell it not to sum rows.
        assert "do NOT sum" in resp["note"]

    def test_warns_when_paid_closed_inclusion_unknown(self):
        async def _stub(filters, **_kw):
            return IssuedInvoiceQueryResult(
                invoice_count=2,
                total_invoice_amount=300.0,
                rows=[],
                rows_returned=0,
                preview_truncated=False,
                includes_paid_and_closed="unknown",
            )
        with patch(
            "services.nova_engine.invoice_repository.query_issued_invoice_totals",
            side_effect=_stub,
        ):
            resp = _call(
                "query_invoice_totals",
                date_from="2032-01-01",
                date_to="2032-01-31",
            )
        # Warning must be present and call out the uncertainty.
        warnings = " ".join(resp.get("warnings") or [])
        assert "paid/closed" in warnings or "paid / closed" in warnings or "could not self-describe" in warnings

    def test_clamp_warning_when_limit_over_500(self):
        captured: dict = {}
        with patch(
            "services.nova_engine.invoice_repository.query_issued_invoice_totals",
            side_effect=self._stub_helper(captured),
        ):
            resp = _call(
                "query_invoice_totals",
                date_from="2031-08-01",
                date_to="2031-08-31",
                limit=900,
            )
        f = captured["filt"]
        assert f.limit == 500  # clamped
        warnings = " ".join(resp.get("warnings") or [])
        assert "500" in warnings

    def test_post_query_filtering_warning_propagated(self):
        async def _stub(filters, **_kw):
            return IssuedInvoiceQueryResult(
                invoice_count=1,
                total_invoice_amount=700.0,
                rows=[
                    IssuedInvoiceRow(
                        invoice_date="2032-03-04",
                        doc_num="K-2",
                        total_amount=700.0,
                    ),
                ],
                rows_returned=1,
                preview_truncated=False,
                includes_paid_and_closed=True,
                used_post_query_filtering=True,
            )
        with patch(
            "services.nova_engine.invoice_repository.query_issued_invoice_totals",
            side_effect=_stub,
        ):
            resp = _call(
                "query_invoice_totals",
                date_from="2032-03-01",
                date_to="2032-03-31",
                class_name="ClassR",
            )
        warnings = " ".join(resp.get("warnings") or [])
        assert "post-filtered" in warnings or "post_query" in warnings or "post-filter" in warnings


# ---------------------------------------------------------------------------
# Public-contract guards: no database-engine syntax in tool responses
# ---------------------------------------------------------------------------


class TestNoDbEngineInResponses:
    def test_get_open_invoices_response_has_no_engine_terms(self):
        captured: dict = {}
        async def _stub(filters, **_kw):
            captured["filt"] = filters
            return OpenInvoiceQueryResult(
                total_matched=1, total_amount=10.0, total_open_balance=10.0,
                rows=[OpenInvoiceRow(invoice_number="O-1", amount=10.0, open_balance=10.0)],
                rows_returned=1, preview_truncated=False,
                snapshot_synced_at="2030-01-01T00:00:00Z",
            )
        with patch(
            "services.nova_engine.invoice_repository.query_open_invoice_rows",
            side_effect=_stub,
        ):
            resp = _call("get_open_invoices")
        text = repr(resp)
        for forbidden in ("$match", "$facet", "$unwind", "MongoDB", "PostgreSQL"):
            assert forbidden not in text

    def test_query_invoice_totals_response_has_no_engine_terms(self):
        async def _stub(filters, **_kw):
            return IssuedInvoiceQueryResult(
                invoice_count=1,
                total_invoice_amount=10.0,
                rows=[IssuedInvoiceRow(invoice_date="2031-01-01", total_amount=10.0)],
                rows_returned=1,
                preview_truncated=False,
                includes_paid_and_closed=True,
            )
        with patch(
            "services.nova_engine.invoice_repository.query_issued_invoice_totals",
            side_effect=_stub,
        ):
            resp = _call(
                "query_invoice_totals",
                date_from="2031-01-01",
                date_to="2031-01-31",
            )
        text = repr(resp)
        for forbidden in ("$match", "$facet", "$unwind", "MongoDB", "PostgreSQL"):
            assert forbidden not in text


# ---------------------------------------------------------------------------
# Nova-prompt routing rules
# ---------------------------------------------------------------------------


class TestNovaPromptRouting:
    def _prompt(self) -> str:
        from services.ai.agents import nova as nova_module
        return nova_module._SYSTEM_PROMPT

    def test_aggregate_invoiced_questions_route_to_query_invoice_totals(self):
        text = self._prompt()
        # The phrasing pattern should appear under query_invoice_totals,
        # not under get_open_invoices.
        assert "How much have we invoiced" in text or "how much have we invoiced" in text
        # Co-located with query_invoice_totals
        assert "query_invoice_totals" in text

    def test_open_invoice_questions_still_route_to_get_open_invoices(self):
        text = self._prompt()
        assert "get_open_invoices" in text
        # Open-invoice phrasing remains routed to the open tool.
        assert "show me open invoices" in text.lower() or "show me open" in text.lower()

    def test_open_tool_does_not_claim_all_invoices_issued(self):
        text = self._prompt()
        # Anchor on the numbered section heading so we don't latch on
        # to an earlier passing reference inside another tool's
        # description (e.g. get_ar_aging mentions get_open_invoices).
        anchor = "**`get_open_invoices`**"
        idx = text.find(anchor)
        assert idx != -1, "get_open_invoices section heading not found"
        # End the slice at the next numbered section.
        next_section_idx = text.find("**`query_invoice_totals`**", idx)
        assert next_section_idx != -1
        section = text[idx:next_section_idx]
        assert "open" in section.lower()
        # The scope warning is the canonical disclaimer.
        assert (
            "OPEN-only" in section
            or "Open-only" in section
            or "open only" in section.lower()
        )
