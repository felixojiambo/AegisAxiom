"""Full-row export verification for Nova finance exports.

The export endpoint re-runs the deterministic tool server-side. The
audit bug this test suite guards against:

  * chat preview ran with ``limit=20`` → tool returned 20 rows;
  * export reused the same args → exported file contained 20 rows;
  * but ``row_count`` said the query matched 121 rows.

The export must produce **all matching rows up to its own cap**, not
the chat-preview slice. These tests pin that contract using only
synthetic fixtures — no production dates / customer names / vendors /
totals. Expected counts and IDs are computed from the fixture inline.
"""
from __future__ import annotations

import asyncio
import csv
import io
import os

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)

import pytest

from services.nova_engine.finance_export_service import (
    EXPORTABLE_TOOLS,
    ExportRequest,
    build_export,
)


# ---------------------------------------------------------------------------
# Synthetic fixture: N GL rows. Synth IDs make it easy to assert
# "rows 1-20 AND rows 21-121 are present" without any production data.
# ---------------------------------------------------------------------------


def _make_synthetic_gl_rows(n: int) -> list[dict]:
    rows = []
    for i in range(1, n + 1):
        rows.append(
            {
                # Synthetic date in a far-future year so a grep across
                # the repo will never confuse this with real data.
                "tx_date": f"2099-04-{(i % 28) + 1:02d}",
                "account_name": "5xxxx Synthetic Revenue",
                "category": "SYNTH_REVENUE",
                "txn_type": "Invoice",
                "doc_num": f"SYNTH-INV-{i:04d}",
                "name": f"Synthetic Counterparty {i}",
                "memo": f"row {i} of {n}",
                "location": "HQ" if i % 2 == 0 else None,
                "normalized_amount": float(i),
            }
        )
    return rows


class _CapturingRunner:
    """Records the args the export helper passed to the tool runner so
    tests can assert preview-only args were stripped before the
    re-run. Returns a canned tool response — never reads any real
    data source."""

    def __init__(self, response: dict):
        self.response = response
        self.calls: list[dict] = []

    async def __call__(self, tool_name, tool_args, state, user_id, session_id, message_id):
        self.calls.append(
            {
                "tool_name": tool_name,
                "tool_args": dict(tool_args),
                "user_id": user_id,
            }
        )
        return self.response


# ---------------------------------------------------------------------------
# 1. The export-row cap is configured per tool and matches the doc
# ---------------------------------------------------------------------------


def test_export_row_cap_configured_for_every_exportable_tool():
    for tool_name, spec in EXPORTABLE_TOOLS.items():
        assert spec.export_row_cap > 0, (
            f"{tool_name} must have a positive export_row_cap; "
            "0/negative would disable the export entirely."
        )
        assert spec.export_row_cap >= 200, (
            f"{tool_name} export cap {spec.export_row_cap} is below the "
            "smallest tool hard cap (200). A cap that low would clip "
            "audit exports unexpectedly."
        )


def test_export_strips_preview_limit_before_invoking_tool():
    """If the caller asks the export with ``limit=20`` (preview shape),
    the export must call the tool with the cap, not the preview
    limit. The cap is ``EXPORTABLE_TOOLS["query_gl_transactions"]
    .export_row_cap`` (500 today)."""
    n_total = 121
    fixture_rows = _make_synthetic_gl_rows(n_total)
    runner = _CapturingRunner(
        {
            "rows": fixture_rows,
            "row_count": n_total,
            "rows_returned": n_total,
            "preview_truncated": False,
        }
    )

    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={
            "date_from": "2099-04-01",
            "date_to": "2099-04-30",
            "limit": 20,  # preview-shape arg — must NOT survive into the export call
        },
        format="csv",
    )

    asyncio.run(build_export(request, tool_runner=runner))

    assert len(runner.calls) == 1
    call_args = runner.calls[0]["tool_args"]
    # The cap, not the preview limit, must reach the tool.
    spec = EXPORTABLE_TOOLS["query_gl_transactions"]
    assert call_args.get("limit") == spec.export_row_cap, (
        f"expected limit={spec.export_row_cap} (export cap), "
        f"got {call_args.get('limit')!r}"
    )
    # Other args must survive intact — filters carry into the export.
    assert call_args["date_from"] == "2099-04-01"
    assert call_args["date_to"] == "2099-04-30"


# ---------------------------------------------------------------------------
# 2. Full-row export — 121 rows in, 121 rows out
# ---------------------------------------------------------------------------


def test_full_export_includes_all_matching_rows_when_below_cap():
    n_total = 121
    fixture_rows = _make_synthetic_gl_rows(n_total)
    runner = _CapturingRunner(
        {
            "rows": fixture_rows,
            "row_count": n_total,
            "rows_returned": n_total,
            "preview_truncated": False,
        }
    )

    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={
            "date_from": "2099-04-01",
            "date_to": "2099-04-30",
            "limit": 20,
        },
        format="csv",
    )

    result = asyncio.run(build_export(request, tool_runner=runner))

    csv_text = result.body.decode("utf-8-sig")
    data_row_count = len(csv_text.strip().splitlines()) - 1  # subtract header
    assert data_row_count == n_total, (
        f"export should contain all {n_total} rows; got {data_row_count}"
    )
    assert result.row_count == n_total
    assert result.exported_rows == n_total
    assert result.matched_rows == n_total
    assert result.export_truncated is False


def test_full_export_includes_preview_rows_AND_remaining_rows():
    """When the chat preview slice is rows 1-20, the export must
    include rows 1-20 AND rows 21-121 — not just one or the other."""
    n_total = 121
    fixture_rows = _make_synthetic_gl_rows(n_total)
    runner = _CapturingRunner(
        {
            "rows": fixture_rows,
            "row_count": n_total,
            "preview_truncated": False,
        }
    )

    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099-04-01", "date_to": "2099-04-30"},
        format="csv",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))

    csv_text = result.body.decode("utf-8-sig")

    # Sample rows from both ends of the preview/non-preview boundary.
    for synth_id in (1, 20, 21, 121):
        assert f"SYNTH-INV-{synth_id:04d}" in csv_text, (
            f"expected SYNTH-INV-{synth_id:04d} in export"
        )


# ---------------------------------------------------------------------------
# 3. Export does NOT trust client-supplied rows
# ---------------------------------------------------------------------------


def test_export_ignores_smuggled_rows_in_tool_args():
    """Even if a caller sneaks ``rows`` into ``tool_args``, the export
    must source rows from the deterministic re-run."""
    n_total = 5
    server_rows = _make_synthetic_gl_rows(n_total)
    smuggled = [
        {
            "tx_date": "FAKE",
            "doc_num": "FAKE-INV-999",
            "normalized_amount": 999_999_999.0,
        }
    ]
    runner = _CapturingRunner(
        {"rows": server_rows, "row_count": n_total}
    )

    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={
            "date_from": "2099-04-01",
            "date_to": "2099-04-30",
            "rows": smuggled,  # smuggled — must not reach the file
        },
        format="csv",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))

    csv_text = result.body.decode("utf-8-sig")
    assert "FAKE-INV-999" not in csv_text
    assert "999999999" not in csv_text
    # Server rows ARE in the output.
    assert "SYNTH-INV-0001" in csv_text


# ---------------------------------------------------------------------------
# 4. Export-cap truncation behavior
# ---------------------------------------------------------------------------


def test_export_truncated_metadata_when_match_exceeds_cap():
    """Force a row count that exceeds the cap so the helper has to
    surface ``export_truncated=true`` and the warning text quotes both
    counts."""
    spec = EXPORTABLE_TOOLS["query_gl_transactions"]
    cap = spec.export_row_cap
    matched = cap + 50  # simulate more matches than the cap allows

    # Tool returns exactly cap-many rows (it clamps at the hard cap)
    # AND reports the full match count separately. This mirrors the
    # real tool's preview_truncated path.
    fixture_rows = _make_synthetic_gl_rows(cap)
    runner = _CapturingRunner(
        {
            "rows": fixture_rows,
            "row_count": matched,
            "rows_returned": cap,
            "preview_truncated": True,
        }
    )

    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099-04-01", "date_to": "2099-04-30"},
        format="csv",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))

    assert result.exported_rows == cap
    assert result.matched_rows == matched
    assert result.export_truncated is True
    assert result.export_cap == cap

    # The user-facing warning quotes both counts so the operator can
    # narrow filters to capture the remaining rows.
    warning_text = " ".join(result.warnings)
    assert str(cap) in warning_text
    assert str(matched) in warning_text


# ---------------------------------------------------------------------------
# 5. invoice_totals — include_rows is forced True for export
# ---------------------------------------------------------------------------


def test_invoice_totals_export_forces_include_rows_true():
    """``query_invoice_totals`` defaults ``include_rows=False`` so
    aggregate-only calls don't pull rows. For export, the helper must
    force True or the file comes out empty."""
    n_total = 30
    fixture_rows = _make_synthetic_gl_rows(n_total)

    # query_invoice_totals returns rows under the ``rows`` key.
    runner = _CapturingRunner(
        {
            "rows": fixture_rows,
            "invoice_count": n_total,
            "total_invoice_amount": sum(r["normalized_amount"] for r in fixture_rows),
        }
    )

    request = ExportRequest(
        tool_name="query_invoice_totals",
        tool_args={
            "date_from": "2099-04-01",
            "date_to": "2099-04-30",
            "include_rows": False,  # preview default — must be flipped
            "limit": 20,
        },
        format="csv",
    )
    asyncio.run(build_export(request, tool_runner=runner))

    call_args = runner.calls[0]["tool_args"]
    assert call_args["include_rows"] is True, (
        "export must force include_rows=True for query_invoice_totals"
    )
    assert call_args.get("limit") == EXPORTABLE_TOOLS["query_invoice_totals"].export_row_cap


# ---------------------------------------------------------------------------
# 6. XLSX uses the same row source as CSV
# ---------------------------------------------------------------------------


def test_csv_and_xlsx_share_row_source():
    """The XLSX path runs through the same ``build_export`` helper; it
    must also produce all matching rows. If openpyxl isn't installed
    locally, skip with a clear reason."""
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        pytest.skip("openpyxl not installed locally; CI covers the XLSX path")

    n_total = 50
    fixture_rows = _make_synthetic_gl_rows(n_total)
    runner = _CapturingRunner({"rows": fixture_rows, "row_count": n_total})

    request_xlsx = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099-04-01", "date_to": "2099-04-30"},
        format="xlsx",
    )
    result = asyncio.run(build_export(request_xlsx, tool_runner=runner))
    assert result.row_count == n_total
    assert result.exported_rows == n_total

    # Open the bytes through openpyxl and count data rows on the
    # primary sheet (subtract 1 for the bold header).
    import openpyxl as _openpyxl

    wb = _openpyxl.load_workbook(io.BytesIO(result.body))
    primary = wb[wb.sheetnames[0]]
    data_rows = primary.max_row - 1
    assert data_rows == n_total


# ---------------------------------------------------------------------------
# 7. Header-only metadata fields are present on the result
# ---------------------------------------------------------------------------


def test_export_result_carries_new_metadata_fields():
    n_total = 10
    fixture_rows = _make_synthetic_gl_rows(n_total)
    runner = _CapturingRunner(
        {"rows": fixture_rows, "row_count": n_total}
    )
    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099-04-01", "date_to": "2099-04-30"},
        format="csv",
        requested_by_user_id="u_test",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))
    assert hasattr(result, "matched_rows")
    assert hasattr(result, "exported_rows")
    assert hasattr(result, "export_cap")
    assert hasattr(result, "export_truncated")
    # source_metadata mirrors them so the persisted artifact carries
    # the same numbers as the streaming response.
    assert result.source_metadata["exported_rows"] == n_total
    assert result.source_metadata["export_cap"] == EXPORTABLE_TOOLS["query_gl_transactions"].export_row_cap
    assert result.source_metadata["export_truncated"] is False


# ---------------------------------------------------------------------------
# 8. Finance math is not re-derived in the export — rows pass through
# ---------------------------------------------------------------------------


def test_export_preserves_tool_row_values_verbatim():
    """The export must not multiply / round / reformat the deterministic
    row values. If the tool returned ``normalized_amount=12.5``, the
    CSV cell reads ``12.5`` — not a fresh computation."""
    fixture_rows = [
        {
            "tx_date": "2099-04-15",
            "account_name": "5xxxx Synthetic Revenue",
            "category": "SYNTH_REVENUE",
            "txn_type": "Invoice",
            "doc_num": "SYNTH-INV-0001",
            "name": "Synthetic Counterparty 1",
            "memo": "verbatim",
            "location": "HQ",
            "normalized_amount": 12.5,
        }
    ]
    runner = _CapturingRunner({"rows": fixture_rows, "row_count": 1})
    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099-04-01", "date_to": "2099-04-30"},
        format="csv",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))

    reader = csv.reader(io.StringIO(result.body.decode("utf-8-sig")))
    rows = list(reader)
    # row[0] = header, row[1] = data row.
    assert rows[1][-1] == "12.5"
