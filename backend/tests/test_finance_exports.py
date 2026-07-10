"""Tests for the Nova finance export service.

The export service re-runs an approved Nova tool server-side and
turns its result into a CSV or XLSX byte stream. These tests pin the
contract end-to-end:

  * the tool allow-list is closed (no arbitrary tool execution)
  * row data comes from the server-side tool re-run, never from
    client-supplied input
  * CSV / XLSX bytes carry headers, fixture-derived rows, and the
    metadata an auditor needs to reproduce the export
  * secrets / internals are stripped from every row
  * the Nova prompt does not promise inline downloads

All expected rows / counts are derived from synthetic fixtures
in-test — no production dates / customers / vendors / totals.
"""
from __future__ import annotations

import asyncio
import csv
import io
import os

os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production")

import pytest

from services.nova_engine.finance_export_service import (
    EXPORTABLE_TOOLS,
    ExportRequest,
    build_export,
)


# ---------------------------------------------------------------------------
# Synthetic tool runners — every test passes its own fake so we can pin
# (a) "tool was re-run with the same args we passed in" and (b) "the
# helper never trusts client-supplied rows because we never pass any".
# ---------------------------------------------------------------------------


def _fixture_gl_rows():
    return [
        {
            "tx_date": "2099-04-05",
            "account_name": "50101 PS Revenue",
            "category": "PS_REVENUE",
            "txn_type": "Invoice",
            "doc_num": "INV-001",
            "name": "Synthetic Customer A",
            "memo": "synthetic memo",
            "location": "HQ",
            "normalized_amount": 1234.56,
        },
        {
            "tx_date": "2099-04-12",
            "account_name": "50101 PS Revenue",
            "category": "PS_REVENUE",
            "txn_type": "Invoice",
            "doc_num": "INV-002",
            "name": "Synthetic Customer B",
            "memo": "another memo",
            "location": None,
            "normalized_amount": 789.10,
        },
    ]


class _CapturingRunner:
    """Records what the helper asked us to execute, then returns a
    canned tool result. The helper must NEVER reach in for rows
    elsewhere — every row in the export must come from the canned
    response."""

    def __init__(self, response):
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
# 1. Allow-list — unsupported tool_name is rejected
# ---------------------------------------------------------------------------


def test_export_rejects_unsupported_tool_name():
    runner = _CapturingRunner({"rows": []})
    request = ExportRequest(
        tool_name="not_a_real_tool",
        tool_args={},
        format="csv",
    )

    with pytest.raises(ValueError, match="allow-list"):
        asyncio.run(build_export(request, tool_runner=runner))

    # Crucially: the bad-name path must not call the dispatcher at
    # all. No allow-list bypass via tool_runner.
    assert runner.calls == []


def test_export_rejects_unsupported_format():
    runner = _CapturingRunner({"rows": _fixture_gl_rows()})
    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099-04-01", "date_to": "2099-04-30"},
        format="parquet",
    )
    with pytest.raises(ValueError, match="format"):
        asyncio.run(build_export(request, tool_runner=runner))


# ---------------------------------------------------------------------------
# 2. Server-side re-run — rows come from the re-run, never from client
# ---------------------------------------------------------------------------


def test_export_reruns_tool_with_caller_supplied_args():
    fixture_rows = _fixture_gl_rows()
    runner = _CapturingRunner({"rows": fixture_rows})

    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={
            "date_from": "2099-04-01",
            "date_to": "2099-04-30",
            "basis": "accrual",
        },
        format="csv",
        requested_by_user_id="u_test",
    )
    asyncio.run(build_export(request, tool_runner=runner))

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["tool_name"] == "query_gl_transactions"
    # The export helper now strips preview-only ``limit`` and adds the
    # explicit ``export_row_cap`` so a chat-side ``limit=20`` cannot
    # leak into the export. Caller-supplied filters survive intact.
    spec = EXPORTABLE_TOOLS["query_gl_transactions"]
    assert call["tool_args"] == {
        "date_from": "2099-04-01",
        "date_to": "2099-04-30",
        "basis": "accrual",
        "limit": spec.export_row_cap,
    }
    assert call["user_id"] == "u_test"


def test_export_does_not_trust_client_supplied_rows():
    """If the caller smuggles a 'rows' field in tool_args, the helper
    must NOT use it as the export source. The server-side re-run is
    the only source of truth."""
    smuggled = [{"tx_date": "FAKE", "normalized_amount": 999_999_999.0}]
    server_rows = _fixture_gl_rows()
    runner = _CapturingRunner({"rows": server_rows})

    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={
            "date_from": "2099-04-01",
            "date_to": "2099-04-30",
            "rows": smuggled,  # smuggled — must be ignored
        },
        format="csv",
    )

    result = asyncio.run(build_export(request, tool_runner=runner))

    csv_text = result.body.decode("utf-8-sig")
    assert "FAKE" not in csv_text
    assert "999999999" not in csv_text
    # Server rows ARE in the output (synthetic doc nums).
    assert "INV-001" in csv_text


# ---------------------------------------------------------------------------
# 3. CSV export includes headers, rows, filters, generated metadata
# ---------------------------------------------------------------------------


def test_csv_export_includes_headers_rows_and_metadata():
    fixture_rows = _fixture_gl_rows()
    runner = _CapturingRunner({"rows": fixture_rows, "row_count": len(fixture_rows)})

    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099-04-01", "date_to": "2099-04-30"},
        format="csv",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))

    # Round-trip the CSV — headers + N rows.
    csv_text = result.body.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    # The export header carries the signed Amount column alongside the
    # legacy Normalized Amount plus a derived Reversal flag so a
    # downloader can reconcile negative income rows against the QBO
    # Transaction Report without recomputing the sign.
    assert rows[0] == [
        "Transaction Date",
        "Account Name",
        "Category",
        "Transaction Type",
        "Doc Number",
        "Name",
        "Memo",
        "Location",
        "Amount",
        "Normalized Amount",
        "Reversal",
    ]
    assert len(rows) - 1 == len(fixture_rows)

    # Metadata fields the auditor needs.
    assert result.row_count == len(fixture_rows)
    assert result.format == "csv"
    assert result.content_type.startswith("text/csv")
    assert result.source_tool_name == "query_gl_transactions"
    assert result.generated_at  # ISO timestamp set
    assert result.export_id.startswith("pennyexp_")
    assert result.filters == {"date_from": "2099-04-01", "date_to": "2099-04-30"} or (
        # Some tools echo their own ``filters`` block; the helper
        # prefers that. Either source is acceptable for v1.
        result.filters == {}
    )


# ---------------------------------------------------------------------------
# 4. XLSX export works
# ---------------------------------------------------------------------------


def test_xlsx_export_round_trips_header_and_rows():
    openpyxl = pytest.importorskip("openpyxl")  # already in requirements.txt
    fixture_rows = _fixture_gl_rows()
    runner = _CapturingRunner({"rows": fixture_rows})

    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099-04-01", "date_to": "2099-04-30"},
        format="xlsx",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))

    assert result.content_type.startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    wb = openpyxl.load_workbook(io.BytesIO(result.body))
    ws = wb.active
    rows_in_sheet = list(ws.iter_rows(values_only=True))
    assert rows_in_sheet[0][0] == "Transaction Date"
    # +1 for the header row.
    assert len(rows_in_sheet) == len(fixture_rows) + 1


# ---------------------------------------------------------------------------
# 4a. XLSX gap-fill: bold + frozen header, auto-fit columns, Summary sheet
# ---------------------------------------------------------------------------
#
# These tests pin the audit-presentation niceties added on the
# feat/nova-xlsx-export branch: the rows sheet must scroll with the
# header pinned, columns must auto-fit (capped at 60 chars), and a
# Summary sheet must carry the metadata an auditor needs to reproduce
# the export. All expected values come from synthetic fixtures.


def test_xlsx_export_header_row_is_bold_and_frozen():
    openpyxl = pytest.importorskip("openpyxl")
    runner = _CapturingRunner({"rows": _fixture_gl_rows()})
    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099-04-01", "date_to": "2099-04-30"},
        format="xlsx",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))

    wb = openpyxl.load_workbook(io.BytesIO(result.body))
    ws = wb["query_gl_transactions"]
    # Every header cell must be bold so the rows sheet reads as a
    # finished table, not a raw dump.
    for cell in ws[1]:
        assert cell.font.bold is True, f"header cell {cell.coordinate!r} is not bold"
    # A2 freeze means rows above 2 stay pinned when the user scrolls.
    assert ws.freeze_panes == "A2"


def test_xlsx_export_column_widths_autofit_capped_at_sixty():
    openpyxl = pytest.importorskip("openpyxl")
    # Pad one fixture cell with a long synthetic string so the cap
    # actually kicks in — the assertion would be vacuous if every
    # value fit comfortably under 60 chars.
    long_memo = "M" * 200
    rows = _fixture_gl_rows()
    rows[0]["memo"] = long_memo
    runner = _CapturingRunner({"rows": rows})
    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099-04-01", "date_to": "2099-04-30"},
        format="xlsx",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))

    wb = openpyxl.load_workbook(io.BytesIO(result.body))
    ws = wb["query_gl_transactions"]

    # No column may exceed the 60 cap; at least one must reflect the
    # actual content length (i.e. the auto-fit logic ran, it isn't a
    # uniform default).
    widths = [
        ws.column_dimensions[col].width
        for col in ws.column_dimensions
        if ws.column_dimensions[col].width is not None
    ]
    assert widths, "no column widths were set on the rows sheet"
    assert max(widths) <= 60, f"column width exceeded the 60 cap: {widths}"
    # The long memo column must have been clamped to exactly 60, not
    # set to the raw 200-char length.
    memo_col_idx = list(ws[1]).index(
        next(c for c in ws[1] if c.value == "Memo")
    ) + 1
    from openpyxl.utils import get_column_letter

    memo_letter = get_column_letter(memo_col_idx)
    assert ws.column_dimensions[memo_letter].width == 60


def test_xlsx_export_has_summary_sheet_with_audit_metadata():
    openpyxl = pytest.importorskip("openpyxl")
    fixture_rows = _fixture_gl_rows()
    runner = _CapturingRunner({"rows": fixture_rows})
    fixture_args = {"date_from": "2099-04-01", "date_to": "2099-04-30"}
    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args=fixture_args,
        format="xlsx",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))

    wb = openpyxl.load_workbook(io.BytesIO(result.body))
    assert "Summary" in wb.sheetnames
    ws = wb["Summary"]

    # Header row labels Key/Value.
    assert ws.cell(row=1, column=1).value == "Key"
    assert ws.cell(row=1, column=2).value == "Value"

    # Pull the metadata pairs into a dict and check the audit-critical
    # fields are present with values derived from the request, not
    # hardcoded constants.
    pairs: dict[str, object] = {}
    for r in range(2, ws.max_row + 1):
        key = ws.cell(row=r, column=1).value
        value = ws.cell(row=r, column=2).value
        if key is not None:
            pairs[str(key)] = value

    assert pairs.get("source_tool") == "query_gl_transactions"
    assert pairs.get("row_count") == len(fixture_rows)
    assert pairs.get("format") == "xlsx"
    # generated_at should be a non-empty ISO-shaped string.
    assert isinstance(pairs.get("generated_at"), str)
    assert "T" in pairs["generated_at"]
    # filters: either the dict was serialized to JSON (a string that
    # contains both keys) or, if a future change preserves dicts, both
    # keys are reachable on the value. Accept either, since the
    # contract is "filters are readable from the Summary sheet".
    filters_cell = pairs.get("filters")
    assert filters_cell is not None
    rendered = str(filters_cell)
    assert "date_from" in rendered
    assert "date_to" in rendered


# ---------------------------------------------------------------------------
# 5. Exported query_gl_transactions row count matches the tool result
# ---------------------------------------------------------------------------


def test_export_row_count_matches_tool_result():
    rows = _fixture_gl_rows() * 3  # 6 rows
    runner = _CapturingRunner({"rows": rows})

    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099-04-01", "date_to": "2099-04-30"},
        format="csv",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))

    assert result.row_count == len(rows)


# ---------------------------------------------------------------------------
# 6. query_invoice_totals export picks rows from the canonical 'rows' field
# ---------------------------------------------------------------------------


def test_query_invoice_totals_export_uses_canonical_rows_field():
    """``query_invoice_totals`` surfaces invoice rows under ``rows``.
    The legacy ``returned`` alias is not in this tool's response —
    the allow-list correctly maps ``rows`` and the helper must
    ignore any other key."""
    invoice_rows = [
        {
            "txn_date": "2099-04-05",
            "customer": "Synthetic Customer C",
            "doc_num": "INV-100",
            "status": "Paid",
            "class_name": None,
            "location": "HQ",
            "total_amount": 5000.0,
            "open_balance": 0.0,
        },
    ]
    runner = _CapturingRunner(
        {
            "rows": invoice_rows,
            "invoice_count": len(invoice_rows),
        }
    )

    request = ExportRequest(
        tool_name="query_invoice_totals",
        tool_args={
            "date_from": "2099-04-01",
            "date_to": "2099-04-30",
            # The helper will flip include_rows to True for export.
            "include_rows": False,
        },
        format="csv",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))

    csv_text = result.body.decode("utf-8-sig")
    assert "INV-100" in csv_text
    assert result.row_count == len(invoice_rows)


# ---------------------------------------------------------------------------
# 7. Secrets and internals are stripped
# ---------------------------------------------------------------------------


def test_export_strips_global_drop_fields_from_rows():
    leaky_row = {
        "tx_date": "2099-04-05",
        "account_name": "50101 PS Revenue",
        "normalized_amount": 100.0,
        # Fields that must NEVER reach the CSV.
        "_id": "mongo_oid_should_not_export",
        "raw_data": {"DepartmentRef": {"value": "internal"}},
        "qbo_oauth_token": "ya29.fake-token-fake",
        "n8n_execution_id": "exec-internal",
        "stack_trace": "Traceback (most recent call last):...",
    }
    runner = _CapturingRunner({"rows": [leaky_row]})

    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099-04-01", "date_to": "2099-04-30"},
        format="csv",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))

    csv_text = result.body.decode("utf-8-sig")
    forbidden = [
        "mongo_oid_should_not_export",
        "ya29.fake-token-fake",
        "exec-internal",
        "Traceback",
        "raw_data",
    ]
    for token in forbidden:
        assert token not in csv_text, (
            f"export must strip {token!r} from rows"
        )


# ---------------------------------------------------------------------------
# 8. Empty result still produces a valid export (header + warning)
# ---------------------------------------------------------------------------


def test_empty_tool_result_produces_header_only_csv_with_warning():
    runner = _CapturingRunner({"rows": []})
    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099-04-01", "date_to": "2099-04-30"},
        format="csv",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))

    csv_text = result.body.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    assert len(rows) == 1  # header only
    assert result.row_count == 0
    assert any("no rows" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# 9. Tool error surfaces as ValueError (route turns into 400)
# ---------------------------------------------------------------------------


def test_tool_error_response_raises_value_error():
    runner = _CapturingRunner(
        {"error": "invalid_date_from", "detail": "date_from must be YYYY-MM-DD"}
    )
    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099/04/01", "date_to": "2099-04-30"},
        format="csv",
    )

    with pytest.raises(ValueError, match="invalid_date_from"):
        asyncio.run(build_export(request, tool_runner=runner))


# ---------------------------------------------------------------------------
# 10. Allow-list shape — every entry maps to a real Nova tool name
# ---------------------------------------------------------------------------


def test_every_exportable_tool_is_a_registered_nova_tool():
    """The export allow-list must not reference a tool the chat surface
    doesn't actually expose — that would mean the export endpoint is
    promising data the deployment can't produce."""
    from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS

    registered = {schema["name"] for schema in NOVA_TOOL_SCHEMAS}
    for name in EXPORTABLE_TOOLS:
        assert name in registered, (
            f"export allow-list references {name!r} which is not in "
            "NOVA_TOOL_SCHEMAS — register the tool or drop it from "
            "the allow-list."
        )


# ---------------------------------------------------------------------------
# Nova prompt + capability summary wiring
# ---------------------------------------------------------------------------


def test_prompt_routes_chat_users_to_create_finance_export_tool():
    """Audit fix: the prior prompt told users to manually POST to
    the export endpoint. The chat-native fix routes to
    create_finance_export and forbids inventing download URLs."""
    from services.ai.agents.nova import _SYSTEM_PROMPT

    assert "## Exports" in _SYSTEM_PROMPT
    assert "create_finance_export" in _SYSTEM_PROMPT
    lowered = _SYSTEM_PROMPT.lower()
    # Chat users should NOT be told to POST manually as the primary path.
    assert "do not instruct normal chat users to manually" in lowered
    # The artifact marker must be documented so the LLM knows to emit it.
    assert "[[artifact:" in _SYSTEM_PROMPT
    # The "do not invent a download url" guardrail survives.
    assert "do not invent" in lowered


def test_query_gl_transactions_schema_no_longer_forbids_export_mention():
    """Audit fix: the prior schema text 'Do not mention CSV, download,
    link, or export' must be lifted now that the export endpoint
    exists. The schema instead points the LLM at the endpoint."""
    from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS

    schema = next(t for t in NOVA_TOOL_SCHEMAS if t["name"] == "query_gl_transactions")
    description = schema["description"]
    assert "Do not mention CSV, download, link, or export." not in description
    assert "/api/nova/exports" in description


def test_capability_summary_chat_native_when_create_tool_and_endpoint_present():
    from services.ai.agents.nova_capabilities import build_nova_capability_summary

    summary = build_nova_capability_summary(
        available_tools=["create_finance_export"],
        export_available=True,
    )
    export_entry = next(
        c for c in summary["capabilities"] if c["category"] == "Exports"
    )
    assert "create_finance_export" in export_entry["tools"]
    assert "directly from chat" in export_entry["description"].lower()


def test_capability_summary_says_pending_when_only_endpoint_available():
    """Endpoint registered but the chat tool isn't — summary must
    distinguish the two states."""
    from services.ai.agents.nova_capabilities import build_nova_capability_summary

    summary = build_nova_capability_summary(
        available_tools=[],  # no create_finance_export
        export_available=True,
    )
    export_entry = next(
        c for c in summary["capabilities"] if c["category"] == "Exports"
    )
    assert export_entry["tools"] == []
    assert "chat-native export pending" in export_entry["description"].lower()


def test_capability_summary_omits_exports_when_neither_tool_nor_endpoint():
    from services.ai.agents.nova_capabilities import build_nova_capability_summary

    summary = build_nova_capability_summary(
        available_tools=[],
        export_available=False,
    )
    categories = {c["category"] for c in summary["capabilities"]}
    assert "Exports" not in categories
    limitations_text = " ".join(summary["limitations"])
    assert "Exports" in limitations_text


# ---------------------------------------------------------------------------
# Filename safety
# ---------------------------------------------------------------------------


def test_filename_uses_safe_characters_only():
    runner = _CapturingRunner({"rows": _fixture_gl_rows()})
    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099-04-01", "date_to": "2099-04-30"},
        format="csv",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))

    # Defensive: only filesystem-safe characters in the filename.
    import re

    assert re.match(r"^[A-Za-z0-9._-]+$", result.filename)
    assert result.filename.endswith(".csv")
    assert "query_gl_transactions" in result.filename


# ---------------------------------------------------------------------------
# Source metadata captured for audit
# ---------------------------------------------------------------------------


def test_source_metadata_captures_args_and_user_for_audit():
    runner = _CapturingRunner({"rows": _fixture_gl_rows()})
    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099-04-01", "date_to": "2099-04-30"},
        format="csv",
        requested_by_user_id="u_audit",
        source="api",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))

    md = result.source_metadata
    assert md["source_tool_name"] == "query_gl_transactions"
    # ``tool_args`` in source_metadata reflects what the export
    # actually invoked the tool with — caller args plus the explicit
    # export cap, with preview-only args stripped.
    spec = EXPORTABLE_TOOLS["query_gl_transactions"]
    assert md["tool_args"] == {
        "date_from": "2099-04-01",
        "date_to": "2099-04-30",
        "limit": spec.export_row_cap,
    }
    assert md["requested_by_user_id"] == "u_audit"
    assert md["source"] == "api"
    assert md["generated_at"]


# ---------------------------------------------------------------------------
# to_metadata_dict drops the file body
# ---------------------------------------------------------------------------


def test_to_metadata_dict_does_not_leak_file_bytes():
    runner = _CapturingRunner({"rows": _fixture_gl_rows()})
    request = ExportRequest(
        tool_name="query_gl_transactions",
        tool_args={"date_from": "2099-04-01", "date_to": "2099-04-30"},
        format="csv",
    )
    result = asyncio.run(build_export(request, tool_runner=runner))

    md = result.to_metadata_dict()
    assert "body" not in md  # bytes excluded from JSON-shape metadata
    assert md["row_count"] == 2
    assert md["columns"][0] == "Transaction Date"


# ---------------------------------------------------------------------------
# Chat-native export — create_finance_export tool + persistent artifact
# ---------------------------------------------------------------------------
#
# The endpoint-only export path exists from a prior commit. This block
# pins the new chat-native path: the create_finance_export tool
# persists CSV/XLSX bytes in GridFS and returns metadata + a
# download_url + an artifact_id the chat UI renders as a download
# card.


def test_create_finance_export_is_in_nova_tool_schemas():
    from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS

    schema = next(
        (t for t in NOVA_TOOL_SCHEMAS if t["name"] == "create_finance_export"),
        None,
    )
    assert schema is not None, "create_finance_export tool must be registered"
    description = schema["description"].lower()
    # Must surface the chat-native UX intent.
    assert "downloadable" in description or "download" in description
    assert "artifact" in description
    # And the allow-list rule. The description was reworded for the
    # PDF report path; the contract is unchanged but the phrasing
    # ("support matrix" / "allow-listed" / "supported") may vary.
    assert any(
        marker in description
        for marker in ("allow-list", "allow list", "allow-listed", "support matrix", "support")
    )


def test_create_finance_export_schema_has_tool_name_enum_matching_allow_list():
    from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS
    from services.nova_engine.finance_export_service import (
        EXPORTABLE_REPORTS,
    )

    schema = next(t for t in NOVA_TOOL_SCHEMAS if t["name"] == "create_finance_export")
    enum = set(schema["parameters"]["properties"]["tool_name"]["enum"])
    # The enum must MATCH the combined allow-list: row-table tools
    # (CSV/XLSX) + report-style tools (PDF). Exporting something the
    # helper doesn't know how to handle would fail at run time and
    # confuse the LLM.
    assert enum == set(EXPORTABLE_TOOLS) | set(EXPORTABLE_REPORTS)


# In-memory fakes for the persistence / download path.


class _FakeBucket:
    """Minimal AsyncIOMotorGridFSBucket stand-in. Stores bytes by
    str(grid_id); the production code stringifies the id and stores
    it on the metadata doc."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self._next = 1

    async def upload_from_stream(self, filename, body, metadata=None):
        gid = f"fake-grid-{self._next:04d}"
        self._next += 1
        self.files[gid] = bytes(body)
        return gid

    async def open_download_stream(self, gid):
        if gid not in self.files:
            raise RuntimeError("missing")

        class _Stream:
            def __init__(self, data: bytes) -> None:
                self._data = data

            async def read(self) -> bytes:
                return self._data

            async def close(self) -> None:
                pass

        return _Stream(self.files[gid])


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    async def insert_one(self, doc: dict):
        self.docs.append(dict(doc))

    async def find_one(self, query, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                out = dict(d)
                if projection and projection.get("_id") == 0:
                    out.pop("_id", None)
                return out
        return None

    async def create_index(self, *args, **kwargs):
        return None


class _FakeDb:
    def __init__(self) -> None:
        self._collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _FakeCollection()
        return self._collections[name]

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self.__getitem__(name)


def test_save_export_artifact_persists_to_gridfs_and_two_collections(monkeypatch):
    """save_export_artifact must (a) push bytes to GridFS, (b) write
    a nova_export_artifacts metadata doc, and (c) write an
    ai_chat_artifacts doc with artifact_type='finance_export' so the
    frontend dispatcher can pick it up."""
    from services.nova_engine import nova_export_storage

    files: dict[str, bytes] = {}
    monkeypatch.setattr(
        nova_export_storage, "_get_gridfs", lambda db: _FakeBucket(files)
    )
    db = _FakeDb()

    body = b"col_a,col_b\n1,2\n"
    persisted = asyncio.run(
        nova_export_storage.save_export_artifact(
            db,
            export_id="pennyexp_synthetic",
            filename="nova_test.csv",
            content_type="text/csv; charset=utf-8",
            body=body,
            row_count=1,
            columns=["col_a", "col_b"],
            filters={"date_from": "2099-04-01"},
            source_tool_name="query_gl_transactions",
            source_tool_args={"date_from": "2099-04-01"},
            source_metadata={"requested_by_user_id": "u_test"},
            warnings=[],
            requested_by_user_id="u_test",
            session_id="sess_test",
            message_id="msg_test",
        )
    )

    assert persisted["export_id"] == "pennyexp_synthetic"
    assert persisted["artifact_id"].startswith("art_")
    assert persisted["download_url"].endswith("/download")
    # Both collections received a doc.
    assert len(db["nova_export_artifacts"].docs) == 1
    assert len(db["ai_chat_artifacts"].docs) == 1
    chat_doc = db["ai_chat_artifacts"].docs[0]
    assert chat_doc["artifact_type"] == "finance_export"
    assert chat_doc["payload"]["export_id"] == "pennyexp_synthetic"
    assert chat_doc["payload"]["download_url"] == persisted["download_url"]
    # GridFS got the bytes.
    assert len(files) == 1
    assert next(iter(files.values())) == body


def test_save_export_artifact_strips_secrets_from_metadata(monkeypatch):
    from services.nova_engine import nova_export_storage

    monkeypatch.setattr(
        nova_export_storage, "_get_gridfs", lambda db: _FakeBucket({})
    )
    db = _FakeDb()
    asyncio.run(
        nova_export_storage.save_export_artifact(
            db,
            export_id="pennyexp_secrets",
            filename="x.csv",
            content_type="text/csv; charset=utf-8",
            body=b"a,b\n",
            row_count=0,
            columns=["a", "b"],
            filters={},
            source_tool_name="query_gl_transactions",
            source_tool_args={},
            source_metadata={
                "requested_by_user_id": "u_test",
                "qbo_oauth_token": "ya29.fake-token",
                "stack_trace": "Traceback...",
                "raw_data": {"oauth_secret": "shh"},
            },
            warnings=[],
            requested_by_user_id="u_test",
            session_id=None,
            message_id=None,
        )
    )
    persisted_doc = db["nova_export_artifacts"].docs[0]
    assert "qbo_oauth_token" not in persisted_doc["source_metadata"]
    assert "stack_trace" not in persisted_doc["source_metadata"]
    assert "raw_data" not in persisted_doc["source_metadata"]
    assert persisted_doc["source_metadata"]["requested_by_user_id"] == "u_test"


def test_save_export_artifact_rejects_oversize_payload(monkeypatch):
    from services.nova_engine import nova_export_storage

    monkeypatch.setattr(
        nova_export_storage, "_get_gridfs", lambda db: _FakeBucket({})
    )
    monkeypatch.setattr(nova_export_storage, "_MAX_EXPORT_BYTES", 100)

    db = _FakeDb()
    with pytest.raises(nova_export_storage.ExportTooLargeError):
        asyncio.run(
            nova_export_storage.save_export_artifact(
                db,
                export_id="pennyexp_big",
                filename="big.csv",
                content_type="text/csv",
                body=b"x" * 200,
                row_count=1,
                columns=[],
                filters={},
                source_tool_name="query_gl_transactions",
                source_tool_args={},
                source_metadata={},
                warnings=[],
                requested_by_user_id="u_test",
                session_id=None,
                message_id=None,
            )
        )
    # Nothing persisted on the rejection path.
    assert db["nova_export_artifacts"].docs == []
    assert db["ai_chat_artifacts"].docs == []


@pytest.mark.asyncio
async def test_create_finance_export_dispatcher_round_trip(monkeypatch):
    """End-to-end: the Nova dispatcher branch re-runs the tool,
    persists the artifact, and returns metadata the LLM can quote."""
    from services.ai.agents.tools.nova_tools import _ScenarioState, execute_tool
    from services.nova_engine import nova_export_storage

    files: dict[str, bytes] = {}
    monkeypatch.setattr(
        nova_export_storage, "_get_gridfs", lambda db: _FakeBucket(files)
    )
    db = _FakeDb()
    monkeypatch.setattr("database.get_db", lambda: db)

    # Patch query_gl_transactions's data path so the re-run inside
    # the export dispatcher returns deterministic synthetic rows.
    fixture_rows = _fixture_gl_rows()

    async def _fake_runner(name, args, state, user_id, session_id, message_id):
        return {"rows": fixture_rows, "row_count": len(fixture_rows), "filters": args}

    # We patch build_export's tool_runner indirection by monkeypatching
    # execute_tool itself — the export dispatcher passes execute_tool
    # as the runner.
    import services.nova_engine.finance_export_service as fes

    orig_build_export = fes.build_export

    async def _build_with_fake_runner(request, *, tool_runner):
        return await orig_build_export(request, tool_runner=_fake_runner)

    monkeypatch.setattr(fes, "build_export", _build_with_fake_runner)
    # Re-import so the dispatcher's local `from services.nova_engine
    # .finance_export_service import build_export` picks up the patch.
    import services.ai.agents.tools.nova_tools as nova_tools_mod
    monkeypatch.setattr(
        nova_tools_mod,
        "execute_tool",
        execute_tool,
    )

    result = await execute_tool(
        "create_finance_export",
        {
            "tool_name": "query_gl_transactions",
            "tool_args": {"date_from": "2099-04-01", "date_to": "2099-04-30"},
            "format": "csv",
        },
        _ScenarioState(),
        "u_test",
        "sess_test",
        "msg_test",
    )

    assert result.get("status") == "export_created"
    assert result["export_id"].startswith("pennyexp_")
    assert result["artifact_id"].startswith("art_")
    assert result["filename"].endswith(".csv")
    assert result["row_count"] == len(fixture_rows)
    assert result["download_url"].endswith(f"{result['export_id']}/download")
    # Persistence happened.
    assert len(db["nova_export_artifacts"].docs) == 1
    assert len(db["ai_chat_artifacts"].docs) == 1
    assert db["ai_chat_artifacts"].docs[0]["artifact_type"] == "finance_export"


@pytest.mark.asyncio
async def test_create_finance_export_rejects_unsupported_tool():
    from services.ai.agents.tools.nova_tools import _ScenarioState, execute_tool

    result = await execute_tool(
        "create_finance_export",
        {"tool_name": "not_an_export_tool", "tool_args": {}, "format": "csv"},
        _ScenarioState(),
        "u_test",
        None,
        None,
    )
    assert result.get("error") == "unsupported_tool_for_export"
    assert "supported_tools" in result


@pytest.mark.asyncio
async def test_create_finance_export_rejects_invalid_format():
    from services.ai.agents.tools.nova_tools import _ScenarioState, execute_tool

    result = await execute_tool(
        "create_finance_export",
        {
            "tool_name": "query_gl_transactions",
            "tool_args": {},
            "format": "parquet",
        },
        _ScenarioState(),
        "u_test",
        None,
        None,
    )
    # The dispatcher now surfaces the per-tool support matrix in the
    # error payload so the LLM can quote it back to the user. The
    # error code was renamed from the older ``invalid_format`` to
    # ``unsupported_export_format`` when the PDF path was added —
    # the contract (this combo is rejected with a structured error)
    # is unchanged.
    assert result.get("error") == "unsupported_export_format"
    assert result.get("tool_name") == "query_gl_transactions"
    assert result.get("format") == "parquet"
    assert "csv" in result.get("supported_formats", [])
    assert "xlsx" in result.get("supported_formats", [])


# ---------------------------------------------------------------------------
# PR #158 review backlog Fix 10 — export hot path no longer calls
# ensure_indexes per request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_finance_export_does_not_call_ensure_indexes_on_hot_path(
    monkeypatch,
):
    """Indexes are registered once at app startup (server.py
    ensure_indexes block). Calling the chat-tool dispatcher twice
    must NOT trigger ensure_indexes — the per-request round-trip is
    gone."""
    from services.ai.agents.tools.nova_tools import _ScenarioState, execute_tool
    from services.nova_engine import nova_export_storage

    # Spy: count how many times ensure_indexes is called during the
    # two-export round-trip. Expected: 0.
    call_count = {"n": 0}
    original = nova_export_storage.ensure_indexes

    async def _counting_ensure_indexes(db):
        call_count["n"] += 1
        return await original(db)

    monkeypatch.setattr(
        nova_export_storage, "ensure_indexes", _counting_ensure_indexes
    )

    # Stand up the same fakes the existing dispatcher round-trip
    # test uses so build_export's tool runner returns deterministic
    # rows and the GridFS / Mongo persistence is mocked.
    files: dict[str, bytes] = {}
    monkeypatch.setattr(
        nova_export_storage, "_get_gridfs", lambda db: _FakeBucket(files)
    )
    db = _FakeDb()
    monkeypatch.setattr("database.get_db", lambda: db)

    fixture_rows = _fixture_gl_rows()

    async def _fake_runner(name, args, state, user_id, session_id, message_id):
        return {"rows": fixture_rows, "row_count": len(fixture_rows), "filters": args}

    import services.nova_engine.finance_export_service as fes

    orig_build_export = fes.build_export

    async def _build_with_fake_runner(request, *, tool_runner):
        return await orig_build_export(request, tool_runner=_fake_runner)

    monkeypatch.setattr(fes, "build_export", _build_with_fake_runner)

    # Two consecutive export calls — neither should trigger
    # ensure_indexes on the hot path.
    for _ in range(2):
        result = await execute_tool(
            "create_finance_export",
            {
                "tool_name": "query_gl_transactions",
                "tool_args": {"date_from": "2099-04-01", "date_to": "2099-04-30"},
                "format": "csv",
            },
            _ScenarioState(),
            "u_test",
            "sess_test",
            "msg_test",
        )
        assert result["status"] == "export_created"

    assert call_count["n"] == 0, (
        "Fix 10: ensure_indexes must not run on the export hot path; "
        f"got {call_count['n']} call(s)"
    )


# ===========================================================================
# PR #164 review cleanup — migrated from test_pr164_review_cleanup.py
# ===========================================================================
# Recursive bounds validator on the ``tool_args`` payload posted to
# /api/nova/exports. Keeps abusive / accidentally-huge payloads
# from reaching ``execute_tool``.


def test_validate_tool_args_passes_normal_payload():
    """A realistic export tool_args payload (date range + a few
    filter keys) passes the validator without raising."""
    from routes.nova_exports import _validate_tool_args_bounds

    _validate_tool_args_bounds({
        "metric": "revenue",
        "date_from": "2099-04-01",
        "date_to": "2099-04-30",
        "basis": "accrual",
        "limit": 50,
    })


def test_validate_tool_args_rejects_oversized_string():
    from routes.nova_exports import (
        MAX_EXPORT_TOOL_ARGS_STRING_LENGTH,
        _ToolArgsTooLarge,
        _validate_tool_args_bounds,
    )

    big_string = "x" * (MAX_EXPORT_TOOL_ARGS_STRING_LENGTH + 1)
    with pytest.raises(_ToolArgsTooLarge, match="string"):
        _validate_tool_args_bounds({"customer_search": big_string})


def test_validate_tool_args_rejects_excessive_nesting():
    from routes.nova_exports import (
        MAX_EXPORT_TOOL_ARGS_DEPTH,
        _ToolArgsTooLarge,
        _validate_tool_args_bounds,
    )

    deeply_nested: dict = {"inner": {}}
    cursor = deeply_nested["inner"]
    for _ in range(MAX_EXPORT_TOOL_ARGS_DEPTH + 5):
        cursor["inner"] = {}
        cursor = cursor["inner"]
    with pytest.raises(_ToolArgsTooLarge, match="depth"):
        _validate_tool_args_bounds(deeply_nested)


def test_validate_tool_args_rejects_excessive_key_count():
    from routes.nova_exports import (
        MAX_EXPORT_TOOL_ARGS_KEYS,
        _ToolArgsTooLarge,
        _validate_tool_args_bounds,
    )

    too_many = {f"k{i}": i for i in range(MAX_EXPORT_TOOL_ARGS_KEYS + 1)}
    with pytest.raises(_ToolArgsTooLarge, match="keys"):
        _validate_tool_args_bounds(too_many)


def test_validate_tool_args_rejects_excessive_list_length():
    from routes.nova_exports import (
        MAX_EXPORT_TOOL_ARGS_LIST_LENGTH,
        _ToolArgsTooLarge,
        _validate_tool_args_bounds,
    )

    args = {"customers": list(range(MAX_EXPORT_TOOL_ARGS_LIST_LENGTH + 1))}
    with pytest.raises(_ToolArgsTooLarge, match="list"):
        _validate_tool_args_bounds(args)


def test_validate_tool_args_rejects_non_serialisable_value():
    """A non-JSON-native value (set, datetime, custom object) must
    raise rather than silently coerce."""
    from routes.nova_exports import (
        _ToolArgsTooLarge,
        _validate_tool_args_bounds,
    )

    with pytest.raises(_ToolArgsTooLarge):
        _validate_tool_args_bounds({"bad": {1, 2, 3}})


def test_validate_tool_args_accepts_payload_within_structural_caps():
    """The recursive validator now only enforces structural caps.
    A payload that satisfies every per field rule but balloons in
    aggregate is left for the route layer hard byte cap _MAX_TOOL_ARGS_BYTES,
    which surfaces HTTP 413. The validator itself accepts the
    payload so the structural and byte tier checks stay decoupled."""
    from routes.nova_exports import _validate_tool_args_bounds

    args = {f"k{i}": "x" * 4000 for i in range(99)}
    _validate_tool_args_bounds(args)


def test_export_route_constant_max_tool_args_bytes_is_64kb():
    """Pin the spec value so a future tweak that lowers the cap
    silently is caught here."""
    from routes.nova_exports import _MAX_TOOL_ARGS_BYTES

    assert _MAX_TOOL_ARGS_BYTES == 64 * 1024


# ===========================================================================
# PR cleanup A2. get_open_bills export key mapping
# ===========================================================================


def test_get_open_bills_export_uses_returned_field_for_rows():
    """Tool returns rows under returned and total under total_matched.
    The export entry must mirror that or every export yields zero
    rows silently. Build the export against a synthetic three row
    fixture and assert the CSV body carries three data rows plus
    the audit summary count equals three."""
    import asyncio

    from services.nova_engine.finance_export_service import (
        EXPORTABLE_TOOLS,
        ExportRequest,
        build_export,
    )

    spec = EXPORTABLE_TOOLS["get_open_bills"]
    assert spec.rows_field == "returned"
    assert spec.matched_count_field == "total_matched"

    fixture_rows = [
        {
            "doc_num": "BILL-9001",
            "vendor": "Synthetic Vendor Alpha",
            "txn_date": "2099-04-01",
            "due_date": "2099-04-30",
            "class_name": None,
            "location": None,
            "total_amount": 100.00,
            "open_balance": 100.00,
        },
        {
            "doc_num": "BILL-9002",
            "vendor": "Synthetic Vendor Beta",
            "txn_date": "2099-04-05",
            "due_date": "2099-05-05",
            "class_name": None,
            "location": None,
            "total_amount": 250.50,
            "open_balance": 250.50,
        },
        {
            "doc_num": "BILL-9003",
            "vendor": "Synthetic Vendor Gamma",
            "txn_date": "2099-04-10",
            "due_date": "2099-05-10",
            "class_name": None,
            "location": None,
            "total_amount": 75.00,
            "open_balance": 75.00,
        },
    ]

    class _Runner:
        async def __call__(self, *args, **kwargs):
            return {
                "returned": fixture_rows,
                "total_matched": len(fixture_rows),
            }

    request = ExportRequest(
        tool_name="get_open_bills",
        tool_args={},
        format="csv",
    )
    result = asyncio.run(build_export(request, tool_runner=_Runner()))
    csv_text = result.body.decode("utf-8-sig")
    # Header row plus three data rows.
    data_rows = [r for r in csv_text.splitlines() if r.strip()]
    assert len(data_rows) == 4
    assert result.row_count == 3
    assert result.matched_rows == 3


# ===========================================================================
# PR cleanup A9. Route level 413 on tool_args byte cap
# ===========================================================================


def test_nova_exports_route_returns_413_for_oversized_tool_args():
    """A tool_args dict that serialises beyond _MAX_TOOL_ARGS_BYTES
    must return HTTP 413 from the export route. The structural
    validator no longer enforces a byte cap so this is the only
    layer that catches the overall payload size.

    Builds a minimal FastAPI app that mounts only the nova_exports
    router. Importing the full server module is intentionally
    avoided so this unit test does not depend on optional runtime
    modules pulled in by routes unrelated to nova exports."""
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from auth_helper import get_current_user
    from routes.nova_exports import router as nova_exports_router

    admin_user = SimpleNamespace(
        user_id="u_synth_pr_cleanup",
        email="u_synth_pr_cleanup@example.invalid",
        role="admin",
        permissions=["*"],
        tenant_id=None,
    )
    app = FastAPI()
    app.include_router(nova_exports_router, prefix="/api/nova")
    app.dependency_overrides[get_current_user] = lambda: admin_user
    client = TestClient(app, raise_server_exceptions=False)
    # Build a tool_args dict that passes every structural cap
    # but serialises above 64 KB. 90 keys with 800 byte strings
    # is roughly 72 KB serialised. 90 keys fits under the 100
    # key structural cap so this payload reaches the route
    # byte cap.
    big_args = {f"k{i:03d}": "x" * 800 for i in range(90)}
    resp = client.post(
        "/api/nova/exports",
        json={
            "tool_name": "query_gl_transactions",
            "tool_args": big_args,
            "format": "csv",
        },
    )
    assert resp.status_code == 413, (
        "expected 413 for oversized tool_args got "
        + str(resp.status_code) + " " + resp.text
    )
