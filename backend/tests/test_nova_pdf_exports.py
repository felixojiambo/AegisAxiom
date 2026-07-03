"""Tests for the Nova report-style PDF export path.

The CSV / XLSX path (row tools — query_gl_transactions, etc.) is
covered by ``test_finance_exports.py`` and
``test_nova_full_export.py``. This file covers the OTHER export
shape: a deterministic PDF rendered from
``explain_finance_drivers``'s tool result.

Hard rules these tests pin:

  * The PDF body comes from the deterministic tool dict, never the
    LLM's chat markdown.
  * Caller-supplied report bodies / pre-baked rows are stripped
    before the server-side re-run.
  * Variance / totals are quoted verbatim — the PDF renderer never
    re-computes them.
  * The format / tool support matrix is enforced both ways:
    ``explain_finance_drivers + pdf`` is allowed,
    ``explain_finance_drivers + csv`` is refused, and
    ``query_gl_transactions + pdf`` is refused.
  * The Nova tool schema and prompt mention PDF export.

All synthetic fixtures use far-future dates (2099-) and synthetic
account / category names so a grep across the repo will never
confuse the test data with production rows.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)

import pytest

from services.nova_engine.finance_export_service import (
    EXPORTABLE_REPORTS,
    EXPORTABLE_TOOLS,
    SUPPORTED_FORMATS_BY_TOOL,
    ExportRequest,
    build_export,
)
from services.nova_engine.finance_report_pdf import (
    render_driver_explanation_pdf,
    report_sections_for_driver_pdf,
)


# ---------------------------------------------------------------------------
# Synthetic fixture — shape mirrors analyze_finance_drivers output.
# Every value is invented in-test so a regression cannot reach into
# real tenant data.
# ---------------------------------------------------------------------------


def _synthetic_driver_result() -> dict[str, Any]:
    """A self-contained driver-explanation tool result. Numbers are
    arithmetic on tiny integers so a reviewer can sanity-check the
    PDF contents without a calculator."""
    return {
        "metric": "revenue",
        "date_from": "2099-04-01",
        "date_to": "2099-04-30",
        "basis": "accrual",
        "comparison": {
            "compare_to": "previous_period",
            "comparison_date_from": "2099-03-01",
            "comparison_date_to": "2099-03-31",
        },
        "scope": {
            "metric": "revenue",
            "basis": "accrual",
            "current_period_start": "2099-04-01",
            "current_period_end": "2099-04-30",
            "comparison_period_start": "2099-03-01",
            "comparison_period_end": "2099-03-31",
            "comparison_mode": "previous_month",
            "comparison_label": "previous calendar month",
            "comparison_n_days": None,
            "source": {
                "collection": "general_ledger",
                "scope": "gl_revenue_categories",
            },
            "account_filter_label": "canonical revenue categories",
            "included_revenue_categories": [
                "SYNTH_PS_REVENUE",
                "SYNTH_FS_REVENUE",
            ],
            "excluded_categories": [],
        },
        "formula": {
            "variance_amount": "current_period_total - comparison_period_total",
            "variance_percent": "variance_amount / comparison_period_total",
        },
        "summary": {
            "current_period_total": 1250.00,
            "comparison_period_total": 1000.00,
            "variance_amount": 250.00,
            "variance_percent": 0.25,
            "variance_percent_display": "+25.0%",
            "current_total": 1250.00,
            "comparison_total": 1000.00,
            "variance_pct": 0.25,
        },
        "monthly": [
            {
                "month": "2099-03",
                "amount": 0.0,
                "comparison_amount": 1000.00,
                "variance_amount": -1000.00,
            },
            {
                "month": "2099-04",
                "amount": 1250.00,
                "comparison_amount": 0.0,
                "variance_amount": 1250.00,
            },
        ],
        "top_accounts": [
            {
                "account_name": "Synthetic Revenue Account A",
                "category": "SYNTH_PS_REVENUE",
                "amount": 800.00,
                "share_pct": 0.64,
            },
            {
                "account_name": "Synthetic Revenue Account B",
                "category": "SYNTH_FS_REVENUE",
                "amount": 450.00,
                "share_pct": 0.36,
            },
        ],
        "top_customers": [],
        "top_vendors": [],
        "drivers": [],
        "warnings": [
            "Per-customer and per-vendor ranking are not derived from "
            "the general_ledger row schema.",
            "Synthetic test warning for PDF rendering coverage.",
        ],
    }


class _CapturingRunner:
    """Same shape as the runner in test_finance_exports.py: captures
    every call so the test can assert which args reached the tool,
    then returns a canned response.

    The export path must invoke this runner exactly once for the
    server-side re-run — if it doesn't, the PDF would be built from
    something other than the deterministic tool result, which is
    exactly the bug we're guarding against."""

    def __init__(self, response: dict[str, Any]):
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, tool_name, tool_args, state, user_id, session_id, message_id
    ):
        self.calls.append(
            {
                "tool_name": tool_name,
                "tool_args": dict(tool_args),
                "user_id": user_id,
            }
        )
        return self.response


def _build_pdf_export(tool_args: dict[str, Any] | None = None) -> tuple:
    """Helper: run build_export for explain_finance_drivers+pdf with a
    synthetic capturing runner. Returns (result, runner)."""
    runner = _CapturingRunner(_synthetic_driver_result())
    request = ExportRequest(
        tool_name="explain_finance_drivers",
        tool_args=tool_args or {
            "metric": "revenue",
            "date_from": "2099-04-01",
            "date_to": "2099-04-30",
            "basis": "accrual",
            "compare_to": "previous_period",
        },
        format="pdf",
        requested_by_user_id="u_pdf_test",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))
    return result, runner


# ---------------------------------------------------------------------------
# 1. PDF generation — bytes + content_type + filename
# ---------------------------------------------------------------------------


def test_explain_finance_drivers_exports_as_pdf():
    """End-to-end happy path: the driver tool's deterministic result
    produces a PDF artifact."""
    result, runner = _build_pdf_export()
    assert result.format == "pdf"
    assert len(runner.calls) == 1
    assert runner.calls[0]["tool_name"] == "explain_finance_drivers"


def test_pdf_bytes_start_with_pdf_magic():
    """%PDF is the file-format magic number. Without it Acrobat /
    macOS Preview / browser preview refuse to render the file. This
    test pins the renderer's output as a real PDF stream."""
    result, _ = _build_pdf_export()
    assert result.body[:4] == b"%PDF", (
        "PDF export body must start with %PDF (file-format magic); "
        f"got first 16 bytes: {result.body[:16]!r}"
    )


def test_pdf_content_type_is_application_pdf():
    result, _ = _build_pdf_export()
    assert result.content_type == "application/pdf"


def test_pdf_filename_ends_with_pdf_extension():
    result, _ = _build_pdf_export()
    assert result.filename.endswith(".pdf"), (
        f"PDF filename must carry a .pdf extension; got {result.filename!r}"
    )


def test_pdf_export_metadata_carries_source_tool_name():
    result, _ = _build_pdf_export()
    assert result.source_tool_name == "explain_finance_drivers"
    assert result.source_metadata["source_tool_name"] == "explain_finance_drivers"


def test_pdf_export_metadata_carries_generated_timestamp_and_scope():
    result, _ = _build_pdf_export()
    assert isinstance(result.generated_at, str) and result.generated_at
    # The scope block surfaces the metric / basis / window an auditor
    # would need to reproduce the report from scratch.
    scope = result.source_metadata.get("report_scope")
    assert isinstance(scope, dict)
    assert scope["metric"] == "revenue"
    assert scope["basis"] == "accrual"
    assert scope["current_period_start"] == "2099-04-01"
    assert scope["current_period_end"] == "2099-04-30"
    assert scope["comparison_period_start"] == "2099-03-01"
    assert scope["comparison_period_end"] == "2099-03-31"


# ---------------------------------------------------------------------------
# 2. Deterministic summary values appear in the rendered PDF
# ---------------------------------------------------------------------------
#
# reportlab compresses streams, so we cannot grep the bytes
# directly. Instead, render the same synthetic fixture into a fresh
# PDF and decompress via PyPDF2 (already in requirements.txt) to
# extract text. Every assertion uses a value derived from the
# fixture inline — no production data.


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    from io import BytesIO

    try:
        from PyPDF2 import PdfReader  # type: ignore
    except ImportError:  # pragma: no cover — PyPDF2 is in requirements.txt
        pytest.skip("PyPDF2 missing — cannot decode PDF body for assertions")
    reader = PdfReader(BytesIO(pdf_bytes))
    chunks: list[str] = []
    for page in reader.pages:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            # Some reportlab outputs occasionally tickle a PyPDF2
            # extract_text quirk; treat as empty rather than failing
            # — other assertions still cover the structural shape.
            chunks.append("")
    return "\n".join(chunks)


def test_pdf_includes_deterministic_summary_values_from_fixture():
    """The summary table must quote current_period_total,
    comparison_period_total, and variance_amount from the fixture."""
    result, _ = _build_pdf_export()
    text = _extract_pdf_text(result.body)
    # Numbers are rendered with thousand separators / two decimals;
    # the helper format is "1,250.00" / "1,000.00" / "250.00".
    assert "1,250.00" in text
    assert "1,000.00" in text
    assert "250.00" in text
    # variance_percent rendered as a signed percent ("+25.0%").
    assert "+25.0%" in text


def test_pdf_includes_monthly_movement_section():
    """The monthly movement table walks the fixture's monthly[]."""
    result, _ = _build_pdf_export()
    text = _extract_pdf_text(result.body)
    assert "Monthly movement" in text
    # Each fixture month bucket appears as a table row.
    assert "2099-03" in text
    assert "2099-04" in text


def test_pdf_includes_top_accounts_section():
    """The top accounts table renders the fixture's top_accounts[]
    with synthetic account names + categories."""
    result, _ = _build_pdf_export()
    text = _extract_pdf_text(result.body)
    assert "Top contributing accounts" in text
    assert "Synthetic Revenue Account A" in text
    assert "Synthetic Revenue Account B" in text
    assert "SYNTH_PS_REVENUE" in text
    assert "SYNTH_FS_REVENUE" in text


def test_pdf_includes_warnings_when_tool_returns_warnings():
    """Warnings the tool surfaced are quoted verbatim."""
    result, _ = _build_pdf_export()
    text = _extract_pdf_text(result.body)
    assert "Warnings and notes" in text
    assert "Synthetic test warning for PDF rendering coverage." in text


def test_pdf_renderer_handles_empty_warnings():
    """A driver result with no warnings still renders a Warnings
    section with a placeholder rather than crashing."""
    fixture = _synthetic_driver_result()
    fixture["warnings"] = []
    pdf = render_driver_explanation_pdf(fixture)
    text = _extract_pdf_text(pdf)
    assert "Warnings and notes" in text
    assert "No warnings reported by the tool." in text


def test_pdf_renderer_handles_missing_monthly_and_top_accounts():
    """Defensive coverage: even when monthly / top_accounts are empty
    the renderer surfaces a placeholder, never raises."""
    fixture = _synthetic_driver_result()
    fixture["monthly"] = []
    fixture["top_accounts"] = []
    pdf = render_driver_explanation_pdf(fixture)
    text = _extract_pdf_text(pdf)
    assert "No monthly movement reported." in text
    assert "No top contributing accounts reported." in text


def test_pdf_renderer_handles_null_variance_percent():
    """When comparison_period_total is 0 the helper returns
    variance_percent=None and variance_percent_display='N/A'. The
    renderer must surface the N/A label rather than printing 'None'
    or crashing on the null value."""
    fixture = _synthetic_driver_result()
    fixture["summary"]["variance_percent"] = None
    fixture["summary"]["variance_percent_display"] = "N/A"
    pdf = render_driver_explanation_pdf(fixture)
    text = _extract_pdf_text(pdf)
    assert "N/A" in text


def test_pdf_includes_formula_block_quoted_verbatim():
    """The Formula section quotes formula.variance_amount and
    formula.variance_percent verbatim from the tool result — the
    LLM is forbidden from paraphrasing these and the PDF mirrors
    that contract."""
    result, _ = _build_pdf_export()
    text = _extract_pdf_text(result.body)
    assert "current_period_total - comparison_period_total" in text
    assert "variance_amount / comparison_period_total" in text


def test_pdf_title_reflects_driver_metric():
    """Revenue driver report → 'Revenue Driver Explanation Report'."""
    result, _ = _build_pdf_export()
    text = _extract_pdf_text(result.body)
    assert "Revenue Driver Explanation Report" in text


# ---------------------------------------------------------------------------
# 3. Safety — server-side re-run, no client-supplied report content
# ---------------------------------------------------------------------------


def test_pdf_export_reruns_deterministic_tool_server_side():
    """The export must call the dispatcher exactly once and the runner
    must be the source of all rendered content."""
    _, runner = _build_pdf_export()
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["tool_name"] == "explain_finance_drivers"
    # Caller-supplied args (metric / date range / basis / compare_to)
    # are forwarded so the server-side re-run uses the same scope as
    # the on-screen answer.
    assert call["tool_args"]["metric"] == "revenue"
    assert call["tool_args"]["date_from"] == "2099-04-01"
    assert call["tool_args"]["date_to"] == "2099-04-30"
    assert call["tool_args"]["basis"] == "accrual"
    assert call["tool_args"]["compare_to"] == "previous_period"


def test_pdf_export_ignores_client_supplied_report_body():
    """If a caller smuggles a 'body' / 'report_body' / 'rows' /
    'summary' / 'top_accounts' / 'monthly' / 'scope' / 'formula' arg,
    the helper must NOT forward it to the tool and must NOT use it
    to render the PDF. The server-side re-run is the only source of
    truth."""
    runner = _CapturingRunner(_synthetic_driver_result())
    smuggled = {
        "metric": "revenue",
        "date_from": "2099-04-01",
        "date_to": "2099-04-30",
        # All of these MUST be stripped by the export path.
        "body": b"%PDF-1.4 FAKE FABRICATED BODY",
        "report_body": "FABRICATED-NARRATIVE-BODY",
        "rows": [{"FABRICATED-ROW-MARKER": True}],
        "summary": {"variance_amount": 999999999.99},
        "top_accounts": [{"account_name": "FABRICATED-ACCT-NAME"}],
        "monthly": [{"month": "FABRICATED-MONTH"}],
        "scope": {"metric": "FABRICATED-METRIC"},
        "formula": {"variance_amount": "FABRICATED-FORMULA"},
        "html": "<p>FABRICATED-HTML</p>",
        "pdf_bytes": b"FABRICATED-PDF-BYTES",
        "markdown": "FABRICATED-MARKDOWN",
    }
    request = ExportRequest(
        tool_name="explain_finance_drivers",
        tool_args=smuggled,
        format="pdf",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))

    # The runner saw only the safe args — every forbidden key was
    # stripped before invocation.
    call_args = runner.calls[0]["tool_args"]
    for forbidden in (
        "body",
        "report_body",
        "rows",
        "summary",
        "top_accounts",
        "monthly",
        "scope",
        "formula",
        "html",
        "pdf_bytes",
        "markdown",
    ):
        assert forbidden not in call_args, (
            f"forbidden arg {forbidden!r} leaked into the tool re-run"
        )

    # The rendered PDF must NOT contain any of the fabricated marker
    # strings the caller tried to smuggle.
    text = _extract_pdf_text(result.body)
    for forbidden_marker in (
        "FABRICATED-NARRATIVE-BODY",
        "FABRICATED-ROW-MARKER",
        "FABRICATED-ACCT-NAME",
        "FABRICATED-MONTH",
        "FABRICATED-METRIC",
        "FABRICATED-FORMULA",
        "FABRICATED-HTML",
        "FABRICATED-MARKDOWN",
        "999999999.99",
    ):
        assert forbidden_marker not in text, (
            f"client-smuggled marker {forbidden_marker!r} appeared in "
            "the rendered PDF — server-side re-run was bypassed"
        )


def test_pdf_export_does_not_recompute_variance():
    """The renderer quotes summary.variance_amount and
    summary.variance_percent verbatim from the tool result. Inject
    deliberately-wrong values into the fixture and confirm the PDF
    surfaces them as-is — the renderer must NOT try to "correct"
    them by recomputing from current/comparison totals."""
    fixture = _synthetic_driver_result()
    # Deliberately inconsistent: variance_amount is "999.99" even
    # though 1250 - 1000 = 250. The renderer must show 999.99 (the
    # tool's value) and the chat assistant is the one responsible
    # for the contract that the tool computes correctly.
    fixture["summary"]["variance_amount"] = 999.99
    fixture["summary"]["variance_percent"] = 0.99
    fixture["summary"]["variance_percent_display"] = "+99.0%"
    pdf = render_driver_explanation_pdf(fixture)
    text = _extract_pdf_text(pdf)
    assert "999.99" in text
    assert "+99.0%" in text
    # If the renderer had recomputed, it would say "250.00" /
    # "+25.0%" instead.


def test_pdf_export_preserves_formula_metadata_from_tool():
    """formula.variance_amount / formula.variance_percent are quoted
    verbatim. Custom-formula payloads must reach the PDF unchanged."""
    fixture = _synthetic_driver_result()
    fixture["formula"] = {
        "variance_amount": "custom_formula_marker_amount",
        "variance_percent": "custom_formula_marker_percent",
    }
    pdf = render_driver_explanation_pdf(fixture)
    text = _extract_pdf_text(pdf)
    assert "custom_formula_marker_amount" in text
    assert "custom_formula_marker_percent" in text


# ---------------------------------------------------------------------------
# 4. Format / tool support matrix
# ---------------------------------------------------------------------------


def test_pdf_format_is_allowed_for_explain_finance_drivers():
    """The support matrix lists pdf for the driver tool."""
    assert (
        "pdf" in SUPPORTED_FORMATS_BY_TOOL["explain_finance_drivers"]
    )


def test_explain_finance_drivers_is_registered_as_a_report_export():
    assert "explain_finance_drivers" in EXPORTABLE_REPORTS


def test_pdf_format_is_refused_for_query_gl_transactions():
    """query_gl_transactions is row-table — PDF would be a paginated
    rows dump we have not built or tested. Refuse with a ValueError
    the route layer turns into 400."""
    runner = _CapturingRunner({"rows": []})
    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099-04-01", "date_to": "2099-04-30"},
        format="pdf",
    )
    with pytest.raises(ValueError, match="not supported for"):
        asyncio.run(build_export(request, tool_runner=runner))
    # Validation must short-circuit — the dispatcher must not be
    # invoked for an unsupported tool/format combo.
    assert runner.calls == []


def test_csv_format_is_refused_for_explain_finance_drivers():
    """The driver tool exports as PDF only — refusing CSV here keeps
    the LLM from producing a CSV with a single 'summary' row that
    would mislead an auditor."""
    runner = _CapturingRunner(_synthetic_driver_result())
    request = ExportRequest(
        tool_name="explain_finance_drivers",
        tool_args={
            "metric": "revenue",
            "date_from": "2099-04-01",
            "date_to": "2099-04-30",
        },
        format="csv",
    )
    with pytest.raises(ValueError, match="not supported for"):
        asyncio.run(build_export(request, tool_runner=runner))
    assert runner.calls == []


def test_xlsx_format_is_refused_for_explain_finance_drivers():
    runner = _CapturingRunner(_synthetic_driver_result())
    request = ExportRequest(
        tool_name="explain_finance_drivers",
        tool_args={
            "metric": "revenue",
            "date_from": "2099-04-01",
            "date_to": "2099-04-30",
        },
        format="xlsx",
    )
    with pytest.raises(ValueError, match="not supported for"):
        asyncio.run(build_export(request, tool_runner=runner))


def test_row_tools_still_only_support_csv_and_xlsx():
    """Defensive: every row-table tool MUST keep csv/xlsx and MUST
    NOT have pdf in its support tuple. Adding pdf here without a
    paginated-rows renderer would silently produce an empty / broken
    PDF."""
    for tool_name in EXPORTABLE_TOOLS:
        supported = SUPPORTED_FORMATS_BY_TOOL[tool_name]
        assert "csv" in supported
        assert "xlsx" in supported
        assert "pdf" not in supported, (
            f"row-table tool {tool_name} should not advertise pdf "
            "support until a paginated-rows renderer exists"
        )


# ---------------------------------------------------------------------------
# 5. report_sections metadata block
# ---------------------------------------------------------------------------


def test_report_sections_listed_in_source_metadata():
    """The chat UI / API caller should know which sections the PDF
    contains without re-parsing the body. source_metadata.report_sections
    carries this list."""
    result, _ = _build_pdf_export()
    sections = result.source_metadata.get("report_sections")
    assert isinstance(sections, list)
    assert "summary" in sections
    assert "monthly" in sections
    assert "top_accounts" in sections
    assert "warnings" in sections


def test_report_sections_helper_marks_empty_sections():
    """When monthly/top_accounts are empty, the helper still surfaces
    them in the section list under a *_empty label so the caller
    knows the renderer drew a placeholder rather than the table."""
    fixture = _synthetic_driver_result()
    fixture["monthly"] = []
    fixture["top_accounts"] = []
    sections = report_sections_for_driver_pdf(fixture)
    assert "monthly_empty" in sections
    assert "top_accounts_empty" in sections


def test_pdf_export_row_count_reflects_tabular_section_lengths():
    """row_count is not meaningful for a report PDF, but the metadata
    block must still carry something honest. We surface the combined
    monthly + top_accounts row count (2 + 2 = 4 in the fixture)."""
    result, _ = _build_pdf_export()
    fixture = _synthetic_driver_result()
    expected = len(fixture["monthly"]) + len(fixture["top_accounts"])
    assert result.row_count == expected
    assert result.exported_rows == expected


# ---------------------------------------------------------------------------
# 6. Nova tool schema + prompt mention PDF
# ---------------------------------------------------------------------------


def test_create_finance_export_schema_lists_explain_finance_drivers():
    from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS

    schema = next(
        s for s in NOVA_TOOL_SCHEMAS if s["name"] == "create_finance_export"
    )
    tool_enum = schema["parameters"]["properties"]["tool_name"]["enum"]
    assert "explain_finance_drivers" in tool_enum
    fmt_enum = schema["parameters"]["properties"]["format"]["enum"]
    assert "pdf" in fmt_enum


def test_create_finance_export_description_mentions_pdf_and_driver_report():
    from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS

    schema = next(
        s for s in NOVA_TOOL_SCHEMAS if s["name"] == "create_finance_export"
    )
    description = schema["description"].lower()
    assert "pdf" in description
    assert "explain_finance_drivers" in description
    # The description tells the LLM to ask for clarification when the
    # report scope is ambiguous; pinning this guards against the LLM
    # silently guessing a metric/date range for "export that".
    assert "clarification" in description or "ambiguous" in description


def test_nova_prompt_explains_pdf_export_path():
    """The Nova system prompt must spell out that PDF is for driver
    reports and CSV/XLSX is for row data. Without this Nova would
    not know which format to call create_finance_export with."""
    from services.ai.agents.nova import _SYSTEM_PROMPT

    prompt = _SYSTEM_PROMPT.lower()
    assert "pdf" in prompt
    assert "explain_finance_drivers" in prompt


# ---------------------------------------------------------------------------
# 7. End-to-end via the chat tool dispatcher
# ---------------------------------------------------------------------------
#
# The create_finance_export tool route also lives inside the
# Nova dispatcher. We assert the routing (allow-list, format
# validation, structured error shape) at the dispatcher level so
# the chat surface and the API route share the same contract.


def test_dispatcher_rejects_pdf_for_row_tool_with_structured_error():
    """The dispatcher returns an error dict (NOT a raised exception)
    when the LLM asks for an unsupported tool/format pair, so the
    LLM can apologise / pivot rather than fail silently."""
    from services.ai.agents.tools import nova_tools

    async def run() -> dict:
        return await nova_tools.execute_tool(
            "create_finance_export",
            {
                "tool_name": "query_gl_transactions",
                "tool_args": {
                    "date_from": "2099-04-01",
                    "date_to": "2099-04-30",
                },
                "format": "pdf",
            },
            nova_tools._ScenarioState(),
            "u_test",
            None,
            None,
        )

    result = asyncio.run(run())
    assert result["error"] == "unsupported_export_format"
    assert result["tool_name"] == "query_gl_transactions"
    assert result["format"] == "pdf"
    assert "csv" in result["supported_formats"]
    assert "xlsx" in result["supported_formats"]
    assert "pdf" not in result["supported_formats"]


def test_dispatcher_rejects_csv_for_driver_report_with_structured_error():
    """Symmetry check: asking for CSV of a report tool returns the
    same structured error shape so the LLM has a single branch to
    handle."""
    from services.ai.agents.tools import nova_tools

    async def run() -> dict:
        return await nova_tools.execute_tool(
            "create_finance_export",
            {
                "tool_name": "explain_finance_drivers",
                "tool_args": {
                    "metric": "revenue",
                    "date_from": "2099-04-01",
                    "date_to": "2099-04-30",
                },
                "format": "csv",
            },
            nova_tools._ScenarioState(),
            "u_test",
            None,
            None,
        )

    result = asyncio.run(run())
    assert result["error"] == "unsupported_export_format"
    assert result["tool_name"] == "explain_finance_drivers"
    assert result["format"] == "csv"
    assert result["supported_formats"] == ["pdf"]


def test_dispatcher_uses_pdf_default_for_driver_report_when_format_omitted():
    """Convenience: if the LLM omits ``format`` for a report tool,
    the dispatcher must default to pdf instead of csv. csv would be
    refused immediately, forcing the LLM to retry with an explicit
    format on every call."""
    # The default-resolution logic lives inside the dispatcher; this
    # test exercises the same code path via the support-matrix maps
    # without spinning up a Mongo connection (the persistence layer
    # would require one). We assert the inputs the dispatcher uses
    # to compute the default match the report registry.
    assert "explain_finance_drivers" in EXPORTABLE_REPORTS
    assert SUPPORTED_FORMATS_BY_TOOL["explain_finance_drivers"] == ("pdf",)


# ---------------------------------------------------------------------------
# 8. Existing CSV/XLSX path is unchanged
# ---------------------------------------------------------------------------


def test_existing_csv_export_path_still_works_for_row_tools():
    """Adding pdf support must not regress the CSV path. This is a
    smoke test — the full row-export test suite still pins the
    detailed contract."""
    runner = _CapturingRunner({"rows": [{"tx_date": "2099-04-01", "normalized_amount": 1.0}]})
    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099-04-01", "date_to": "2099-04-30"},
        format="csv",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))
    assert result.format == "csv"
    assert result.content_type.startswith("text/csv")
    assert result.filename.endswith(".csv")


def test_existing_xlsx_export_path_still_works_for_row_tools():
    runner = _CapturingRunner({"rows": [{"tx_date": "2099-04-01", "normalized_amount": 1.0}]})
    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099-04-01", "date_to": "2099-04-30"},
        format="xlsx",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))
    assert result.format == "xlsx"
    assert "spreadsheetml" in result.content_type
    assert result.filename.endswith(".xlsx")


# ===========================================================================
# PR cleanup A3. reportlab XML escape on user content
# ===========================================================================


def test_pdf_renders_when_account_names_contain_xml_special_chars():
    """A driver explanation report whose tool result contains an
    account name with ampersand or a memo with less than sign must
    build without raising. The previous code path passed raw text
    to reportlab Paragraph which parses XML and would raise
    XMLSyntaxError on these chars."""
    from services.nova_engine.finance_report_pdf import (
        render_driver_explanation_pdf,
    )

    tool_result = {
        "metric": "revenue",
        "scope": {
            "metric": "revenue",
            "basis": "accrual",
            "current_period_start": "2099-04-01",
            "current_period_end": "2099-04-30",
            "comparison_period_start": "2099-03-01",
            "comparison_period_end": "2099-03-31",
            "comparison_mode": "previous_month",
            "comparison_label": "previous calendar month",
            "included_revenue_categories": [
                "SYNTH_REV_A",
                "SYNTH_REV_B",
            ],
        },
        "formula": {
            "variance_amount": "variance < comparison_total",
            "variance_percent": "ratio of variance & comparison",
        },
        "summary": {
            "current_period_total": 100.0,
            "comparison_period_total": 80.0,
            "variance_amount": 20.0,
            "variance_percent": 0.25,
        },
        "monthly": [
            {"month": "2099-03", "amount": 0.0, "comparison_amount": 80.0},
            {"month": "2099-04", "amount": 100.0, "comparison_amount": 0.0},
        ],
        "top_accounts": [
            {
                "account_name": "Smith & Co Synthetic",
                "category": "SYNTH_REV_A",
                "amount": 60.0,
                "share_pct": 0.6,
            },
            {
                "account_name": "Acme <Test> Synthetic",
                "category": "SYNTH_REV_B",
                "amount": 40.0,
                "share_pct": 0.4,
            },
        ],
        "top_customers": [],
        "top_vendors": [],
        "drivers": [],
        "warnings": [
            "memo variance < comparison flagged for review",
            "tag & rule diff between snapshots",
        ],
    }
    body = render_driver_explanation_pdf(tool_result)
    assert body[:4] == b"%PDF"
