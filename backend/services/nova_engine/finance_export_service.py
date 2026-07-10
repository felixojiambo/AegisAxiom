"""Server-side CSV / XLSX / PDF export for approved Nova finance tools.

Auditors and finance ops want CSV and Excel files for reconciliation
and sharing, AND PDF documents for report-style summaries that read
like a one-page briefing rather than a row dump. This module is the
single place where Nova tool results become exportable artifacts:

  * Two allow-lists segregate the export shapes:
    ``EXPORTABLE_TOOLS`` lists row-table tools that produce
    CSV / XLSX, and ``EXPORTABLE_REPORTS`` lists summary tools that
    produce a deterministic PDF. An LLM cannot ask the export
    endpoint to dump arbitrary tool output and cannot ask for a PDF
    of a tool that does not have a report renderer.
  * The export ALWAYS re-runs the approved tool server-side with the
    caller-supplied args. The route never accepts client-provided rows
    or client-provided report bodies — that would let a caller smuggle
    fabricated data into a file that looks audit-grade.
  * Field projection per row tool strips internal artifacts
    (raw_data, upstream provider IDs, ``_id``) so secrets / provider
    PII / row plumbing never leak into a downloadable file. Report
    PDFs go through a dedicated renderer (``finance_report_pdf``)
    that quotes the tool result verbatim and never re-computes
    finance numbers.
  * Format support: CSV (stdlib ``csv``), XLSX (``openpyxl`` — already
    in requirements.txt), PDF (``reportlab`` — already pinned). All
    are produced in-memory; this module does not touch the filesystem.
  * No persistent download URL in v1: the route returns the bytes
    directly via ``StreamingResponse``. A ``download_url`` field on
    ``ExportResult`` is reserved for a future commit that adds
    short-TTL persistence.

Pure-ish: the only I/O is the underlying tool's Mongo reads. No QBO
calls, no n8n calls. Tools that already refuse sync-triggers stay
that way — exporting them does not relax their guarantees.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Optional


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Allow-list of exportable tools
# ---------------------------------------------------------------------------
#
# Each entry maps a tool name to the field on the tool's response dict
# that holds the row list, plus an optional ``columns`` projector. New
# tools must be added here explicitly — the route refuses anything
# not in this map.
#
# ``rows_field`` is the dotted path into the response dict; the only
# nested case today is ``data_status.collections`` (a dict-of-rows that
# the helper would flatten if get_finance_data_status were exportable —
# it is intentionally NOT in the allow-list because the data is
# admin-diagnostic, not finance-record).
#
# ``columns`` is a list of (display_name, key) tuples. When None, the
# helper derives columns from the union of keys across rows. Explicit
# columns let us pin column order and exclude internal fields without
# touching the underlying tool.

# A column projection tuple — (display name, response key, optional formatter).
ColumnSpec = tuple[str, str]


@dataclass(frozen=True)
class ExportableTool:
    rows_field: str  # dotted path into the tool response dict
    columns: Optional[tuple[ColumnSpec, ...]]
    # Field names to drop from any row dict before writing — defensive
    # secrets / internals filter even when columns is None.
    drop_fields: tuple[str, ...] = ()
    # Maximum rows this export will request from the source tool.
    # Chat-side preview limits (which can be as small as 20 rows) must
    # NOT carry into the export — the export must include every row up
    # to its own cap. The cap defaults to the underlying tool's hard
    # cap (500 for most Nova finance tools; 200 for ``get_open_bills``)
    # so the export naturally produces every matching row when the
    # match count is within that hard cap.
    export_row_cap: int = 500
    # Tool-response field that reports the FULL match count when the
    # tool returns a paginated preview. ``row_count`` / ``total_matched``
    # / ``invoice_count`` are common names — overriding per tool lets
    # the export carry an accurate ``matched_rows`` metadata field
    # even when the cap clamps the actually-exported rows below it.
    matched_count_field: Optional[str] = None
    # Arg keys that act as preview-only limits on the chat side.
    # ``build_export`` strips these from ``tool_args`` before invoking
    # the tool so a preview ``limit=20`` cannot leak into the export.
    preview_arg_keys: tuple[str, ...] = ("limit",)


# Drop list applied to every exported row regardless of tool. Catches
# Mongo plumbing fields, stack traces (some tools surface ``error_detail``
# with traceback substrings), and any provider-token-shaped key.
_GLOBAL_DROP_FIELDS: frozenset[str] = frozenset(
    {
        "_id",
        "_internal",
        "raw_data",
        "raw_response",
        "raw_payload",
        "error_detail",
        "stack_trace",
        "traceback",
        "qbo_oauth_token",
        "qbo_oauth_refresh_token",
        "n8n_execution_id",
        "callback_secret",
    }
)


EXPORTABLE_TOOLS: dict[str, ExportableTool] = {
    "query_gl_transactions": ExportableTool(
        rows_field="rows",
        # Reversal handling. The signed Amount column carries the
        # source signed value so a negative reversal row preserves
        # its direction in CSV/XLSX/PDF. Normalized Amount stays in
        # the column list for backwards compatibility with auditors
        # who pivot the legacy positive magnitude. The Reversal
        # column is a derived boolean that flags rows where amount
        # is negative and the account maps to revenue, so the
        # downloader can filter or summarise reversals without re
        # computing the sign.
        columns=(
            ("Transaction Date", "tx_date"),
            ("Account Name", "account_name"),
            ("Category", "category"),
            ("Transaction Type", "txn_type"),
            ("Doc Number", "doc_num"),
            ("Name", "name"),
            ("Memo", "memo"),
            ("Location", "location"),
            ("Amount", "amount"),
            ("Normalized Amount", "normalized_amount"),
            ("Reversal", "is_reversal_like"),
        ),
        export_row_cap=500,
        matched_count_field="row_count",
    ),
    "query_invoice_totals": ExportableTool(
        rows_field="rows",
        columns=(
            ("Invoice Date", "txn_date"),
            ("Customer", "customer"),
            ("Doc Number", "doc_num"),
            ("Status", "status"),
            ("Class", "class_name"),
            ("Location", "location"),
            ("Total Amount", "total_amount"),
            ("Open Balance", "open_balance"),
        ),
        export_row_cap=500,
        # The tool reports the full match in ``invoice_count`` even
        # when the preview ``rows`` list is truncated.
        matched_count_field="invoice_count",
        # ``include_rows`` is preview-shaped (the tool defaults to
        # False for the planner short-circuit). Force True for export
        # by stripping it here and re-setting it below.
        preview_arg_keys=("limit", "include_rows"),
    ),
    "get_open_invoices": ExportableTool(
        rows_field="rows",
        columns=(
            ("Invoice Date", "txn_date"),
            ("Customer", "customer"),
            ("Doc Number", "doc_num"),
            ("Due Date", "due_date"),
            ("Aging Bucket", "bucket"),
            ("Class", "class_name"),
            ("Location", "location"),
            ("Total Amount", "total_amount"),
            ("Open Balance", "open_balance"),
        ),
        export_row_cap=500,
        matched_count_field="row_count",
    ),
    "get_open_bills": ExportableTool(
        # get_open_bills returns rows under ``returned`` and full
        # match size under ``total_matched``. The earlier entry used
        # ``rows`` and ``row_count`` which match no key on the tool
        # result so every export silently produced zero rows.
        rows_field="returned",
        columns=(
            ("Bill Date", "txn_date"),
            ("Vendor", "vendor"),
            ("Doc Number", "doc_num"),
            ("Due Date", "due_date"),
            ("Class", "class_name"),
            ("Location", "location"),
            ("Total Amount", "total_amount"),
            ("Open Balance", "open_balance"),
        ),
        # Tool clamps at 200 so the cap reflects that.
        export_row_cap=200,
        matched_count_field="total_matched",
    ),
    "query_finance_data": ExportableTool(
        rows_field="rows",
        columns=(
            ("Bucket", "bucket"),
            ("Amount", "amount"),
            ("Transaction Count", "transaction_count"),
        ),
        export_row_cap=500,
        matched_count_field="row_count",
    ),
}


# ---------------------------------------------------------------------------
# Report-style export allow-list
# ---------------------------------------------------------------------------
#
# Row-table tools (above) export as CSV / XLSX. Report-style tools
# return a single deterministic narrative result that reads as a
# one-page briefing — those export as PDF instead.
#
# Splitting the two surfaces keeps the format-permission matrix
# explicit: only tools listed here can become PDFs, and a request for
# ``query_gl_transactions + pdf`` returns a structured "unsupported
# format" error instead of silently dumping rows into a paginated PDF
# we haven't tested.


@dataclass(frozen=True)
class ReportExportableTool:
    """An approved tool whose deterministic output is rendered as a
    PDF report rather than a CSV / XLSX table.

    ``renderer_key`` selects the renderer in ``build_export`` so we
    don't have a per-tool ``if tool == "x": renderer(...)`` chain.
    Today only one renderer exists (driver explanation), but a future
    P&L summary / variance-by-customer PDF can register its own key
    here and the dispatch stays uniform.
    """

    renderer_key: str  # which renderer function in finance_report_pdf
    # Arg keys to strip from caller-supplied ``tool_args`` before
    # invoking the underlying tool — mirrors ``preview_arg_keys`` on
    # ExportableTool. For driver explanation today, ``limit`` is a
    # cap on top_accounts only and we forward it; nothing else needs
    # to be stripped. Kept for symmetry / future-proofing.
    preview_arg_keys: tuple[str, ...] = ()


EXPORTABLE_REPORTS: dict[str, ReportExportableTool] = {
    "explain_finance_drivers": ReportExportableTool(
        renderer_key="driver_explanation",
    ),
}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


# Row-table formats and report formats share the same ``format`` field
# on ExportRequest but resolve through different allow-lists. The
# union below is the surface for request validation; per-tool gating
# happens in ``build_export`` so a caller can't ask
# ``query_gl_transactions + pdf`` (unsupported) or
# ``explain_finance_drivers + csv`` (unsupported).
_ROW_FORMATS: frozenset[str] = frozenset({"csv", "xlsx"})
_REPORT_FORMATS: frozenset[str] = frozenset({"pdf"})
_VALID_FORMATS: frozenset[str] = _ROW_FORMATS | _REPORT_FORMATS

_CONTENT_TYPES: dict[str, str] = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
    "pdf": "application/pdf",
}


# Public maps so the chat tool layer and the route layer can quote the
# same per-tool support matrix instead of duplicating it. The Nova
# tool dispatcher uses these to build a structured
# ``unsupported_export_format`` error.
SUPPORTED_FORMATS_BY_TOOL: dict[str, tuple[str, ...]] = {
    **{name: tuple(sorted(_ROW_FORMATS)) for name in EXPORTABLE_TOOLS},
    **{name: tuple(sorted(_REPORT_FORMATS)) for name in EXPORTABLE_REPORTS},
}


@dataclass(frozen=True)
class ExportRequest:
    tool_name: str
    tool_args: dict
    format: str
    requested_by_user_id: Optional[str] = None
    source: str = "nova"


@dataclass
class ExportResult:
    export_id: str
    format: str
    filename: str
    content_type: str
    row_count: int
    columns: list[str]
    filters: dict
    source_tool_name: str
    generated_at: str
    source_metadata: dict
    warnings: list[str]
    body: bytes  # the file bytes — route streams these out, never echoed in JSON
    # Full deterministic row count the tool reported, when available.
    # ``None`` means the tool did not surface a separate match count —
    # callers should treat ``exported_rows`` as the best available
    # measure of how many rows the query matched.
    matched_rows: Optional[int] = None
    exported_rows: int = 0
    export_cap: int = 0
    export_truncated: bool = False

    def to_metadata_dict(self) -> dict:
        d = asdict(self)
        d.pop("body", None)
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename_segment(value: str, max_len: int = 60) -> str:
    cleaned = _FILENAME_SAFE.sub("_", value).strip("._-")
    return (cleaned or "export")[:max_len]


def _new_export_id() -> str:
    return f"pennyexp_{uuid.uuid4().hex[:16]}"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _value_at(doc: dict, dotted: str) -> Any:
    cur: Any = doc
    for segment in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(segment)
    return cur


def _coerce_rows(value: Any) -> list[dict]:
    if value is None:
        return []
    if isinstance(value, list):
        return [r for r in value if isinstance(r, dict)]
    return []


def _strip_internal_fields(row: dict, extra_drops: Iterable[str]) -> dict:
    drops = _GLOBAL_DROP_FIELDS | set(extra_drops)
    return {k: v for k, v in row.items() if k not in drops}


def _stringify_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    # Avoid leaking a dict / list literal that could carry secrets the
    # global drop list missed. The audit reviewer reading the CSV would
    # see a ``[object]`` placeholder and ask why — that's preferable to
    # silently flattening a dict.
    return "[object]"


def _resolve_columns(
    spec: ExportableTool,
    rows: list[dict],
) -> list[ColumnSpec]:
    if spec.columns is not None:
        return list(spec.columns)
    seen: list[str] = []
    seen_set: set[str] = set()
    for row in rows:
        for key in row:
            if key in _GLOBAL_DROP_FIELDS or key in seen_set:
                continue
            seen.append(key)
            seen_set.add(key)
    return [(key, key) for key in seen]


def _rows_to_csv_bytes(columns: list[ColumnSpec], rows: list[dict]) -> bytes:
    """CSV bytes with a UTF-8 BOM so Excel opens them with the right
    encoding."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([display for display, _ in columns])
    for row in rows:
        writer.writerow([_stringify_cell(row.get(key)) for _, key in columns])
    return buf.getvalue().encode("utf-8-sig")


def _rows_to_xlsx_bytes(
    columns: list[ColumnSpec],
    rows: list[dict],
    sheet_title: str,
    summary: Optional[dict[str, Any]] = None,
) -> bytes:
    """XLSX bytes via openpyxl.

    Rows sheet has a bold header frozen at A2 and column widths
    auto-fit from cell contents, capped at 60 chars. When ``summary``
    is provided, a second sheet titled "Summary" is appended with the
    key/value pairs in insertion order — used by callers to surface
    audit metadata (source tool, filters, generated_at, row count)
    alongside the row data.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter
    except ImportError as exc:  # pragma: no cover — openpyxl is in requirements.txt
        raise RuntimeError(
            "openpyxl is required for XLSX exports but is not installed."
        ) from exc

    wb = Workbook()
    ws = wb.active
    ws.title = (sheet_title or "Export")[:31]  # Excel sheet-name cap
    bold = Font(bold=True)

    header_values = [display for display, _ in columns]
    ws.append(header_values)
    for cell in ws[1]:
        cell.font = bold

    stringified_rows: list[list[str]] = []
    for row in rows:
        values = [_stringify_cell(row.get(key)) for _, key in columns]
        stringified_rows.append(values)
        ws.append(values)

    # Freeze the header row so it stays in view when scrolling — A2
    # means "freeze everything above row 2 and to the left of col A".
    ws.freeze_panes = "A2"

    # Auto-fit column widths from header + cell contents, capped at 60
    # so a long memo field doesn't blow the sheet open horizontally.
    # +2 gives a small padding so text doesn't butt up against the
    # next column. Min 10 keeps tiny numeric columns readable.
    for col_idx, header in enumerate(header_values, start=1):
        max_len = len(str(header))
        for values in stringified_rows:
            cell_text = values[col_idx - 1]
            if cell_text and len(cell_text) > max_len:
                max_len = len(cell_text)
        width = min(max(max_len + 2, 10), 60)
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    if summary:
        ws_summary = wb.create_sheet(title="Summary")
        ws_summary.cell(row=1, column=1, value="Key").font = bold
        ws_summary.cell(row=1, column=2, value="Value").font = bold
        for r, (k, v) in enumerate(summary.items(), start=2):
            ws_summary.cell(row=r, column=1, value=str(k))
            ws_summary.cell(row=r, column=2, value=_summary_cell_value(v))
        ws_summary.column_dimensions["A"].width = 28
        ws_summary.column_dimensions["B"].width = 60
        ws_summary.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _summary_cell_value(value: Any) -> Any:
    """Coerce a summary-sheet value into something openpyxl can write
    without raising. Strings, numbers, and bools pass through; None
    stays None (an empty cell); dicts/lists are JSON-serialized so
    nested filter dicts render as readable strings instead of object
    repr."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    import json as _json

    try:
        return _json.dumps(value, default=str, sort_keys=True)
    except Exception:  # noqa: BLE001
        return str(value)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


# Type alias: the dispatcher function that re-runs a Nova tool. Wired
# at the route layer to ``services.ai.agents.tools.nova_tools.execute_tool``;
# accepting it as a callable here keeps the helper testable without
# importing the dispatcher module at module-load time.
ToolRunner = Callable[..., Awaitable[dict]]


async def build_export(
    request: ExportRequest,
    *,
    tool_runner: ToolRunner,
) -> ExportResult:
    """Re-run the approved tool and return an ExportResult ready to stream.

    Args:
      request: an ``ExportRequest`` with tool_name, tool_args, format.
      tool_runner: an async callable matching the Nova dispatcher
        signature ``(tool_name, tool_args, state, user_id, session_id,
        message_id) -> dict``. The helper supplies a fresh scenario
        state and the request's user / source metadata.

    Raises:
      ValueError: when the tool isn't in either allow-list, the
        format / tool combination is unsupported, or the tool result
        includes a structured error. The route layer turns these into
        4xx responses.
    """
    # Route to the report-style PDF path when the tool is registered
    # as a report renderer. The two allow-lists are disjoint by
    # construction (a tool is either row-table OR report-style,
    # never both) so the dispatch is unambiguous.
    if request.tool_name in EXPORTABLE_REPORTS:
        return await _build_report_export(request, tool_runner=tool_runner)

    if request.tool_name not in EXPORTABLE_TOOLS:
        raise ValueError(
            f"tool_name {request.tool_name!r} is not in the export "
            f"allow-list. Supported row-table tools: "
            f"{sorted(EXPORTABLE_TOOLS)}. Supported report tools: "
            f"{sorted(EXPORTABLE_REPORTS)}."
        )
    if request.format not in _ROW_FORMATS:
        raise ValueError(
            f"format {request.format!r} is not supported for "
            f"{request.tool_name!r}. Supported formats for this tool: "
            f"{sorted(_ROW_FORMATS)}."
        )

    spec = EXPORTABLE_TOOLS[request.tool_name]

    # Re-run the tool server-side. A fresh scenario state is fine — the
    # exportable tools above are read-only finance queries; none of
    # them require persisted scenario context.
    from services.ai.agents.tools.nova_tools import _ScenarioState

    state = _ScenarioState()

    # ── Preview/export separation ──
    # The chat surface may have called the tool with a small preview
    # limit (e.g. ``limit=20``). Forwarding that arg verbatim would
    # produce a 20-row export of a 121-row query — exactly the audit
    # bug this dataclass's ``export_row_cap`` defends against. Strip
    # preview-only args and set the explicit export cap before
    # invoking the tool.
    export_args: dict[str, Any] = dict(request.tool_args or {})
    for preview_key in spec.preview_arg_keys:
        export_args.pop(preview_key, None)
    export_args["limit"] = spec.export_row_cap
    # ``query_invoice_totals`` gates row return on ``include_rows``;
    # without forcing it True the tool returns aggregate counts only
    # and the export comes out empty. Other tools ignore this key.
    if request.tool_name == "query_invoice_totals":
        export_args["include_rows"] = True

    tool_result = await tool_runner(
        request.tool_name,
        export_args,
        state,
        request.requested_by_user_id or "export",
        None,
        None,
    )

    if not isinstance(tool_result, dict):
        raise ValueError(
            f"tool {request.tool_name!r} returned a non-dict result; "
            "cannot export."
        )
    if tool_result.get("error"):
        raise ValueError(
            f"tool {request.tool_name!r} returned an error: "
            f"{tool_result.get('error')!r} — {tool_result.get('detail', '')!r}"
        )

    raw_rows = _coerce_rows(_value_at(tool_result, spec.rows_field))
    cleaned_rows = [_strip_internal_fields(r, spec.drop_fields) for r in raw_rows]
    columns = _resolve_columns(spec, cleaned_rows)

    # Read the full match count the tool reported (``row_count`` /
    # ``invoice_count`` / etc.). When present and greater than the
    # rows we actually exported, the cap clipped the result and we
    # must surface that in metadata + a user-facing warning. When
    # absent, treat the exported row count as the source of truth.
    matched_rows: Optional[int] = None
    if spec.matched_count_field:
        candidate = tool_result.get(spec.matched_count_field)
        if isinstance(candidate, int) and candidate >= 0:
            matched_rows = candidate

    exported_rows = len(cleaned_rows)
    export_truncated = bool(matched_rows is not None and matched_rows > exported_rows)

    generated_at = _utcnow_iso()
    export_id = _new_export_id()
    safe_tool = _safe_filename_segment(request.tool_name)
    filename = f"nova_{safe_tool}_{export_id[-8:]}.{request.format}"
    content_type = _CONTENT_TYPES[request.format]

    # Filter / metadata block is computed up front so both the body
    # render (Summary sheet for xlsx) and the ExportResult return value
    # share a single source of truth.
    filters = tool_result.get("filters") if isinstance(tool_result.get("filters"), dict) else None
    if filters is None:
        filters = dict(request.tool_args or {})

    if request.format == "csv":
        body = _rows_to_csv_bytes(columns, cleaned_rows)
    else:
        summary: dict[str, Any] = {
            "source_tool": request.tool_name,
            "generated_at": generated_at,
            "row_count": exported_rows,
            "matched_rows": matched_rows if matched_rows is not None else "(not surfaced by tool)",
            "export_cap": spec.export_row_cap,
            "export_truncated": export_truncated,
            "format": request.format,
            "filters": filters,
        }
        body = _rows_to_xlsx_bytes(
            columns,
            cleaned_rows,
            sheet_title=safe_tool[:31],
            summary=summary,
        )

    warnings: list[str] = []
    if not cleaned_rows:
        warnings.append(
            "Tool returned no rows; the export contains the header row "
            "and metadata only."
        )
    if export_truncated and matched_rows is not None:
        warnings.append(
            f"Export was capped at {exported_rows} rows; the query "
            f"matched {matched_rows} rows. Narrow the date window or "
            "filters to capture the remaining rows."
        )

    # ``filters`` was hoisted above for use in the xlsx Summary sheet;
    # reuse the same value here so the streamed file and the response
    # metadata agree on what filter set produced the rows.
    source_metadata: dict[str, Any] = {
        "source_tool_name": request.tool_name,
        # Use the args we actually invoked the tool with, not the
        # client-supplied preview args. This is what a reviewer would
        # need to reproduce the export from a shell.
        "tool_args": export_args,
        "tool_response_keys": sorted(k for k in tool_result if k not in _GLOBAL_DROP_FIELDS),
        "generated_at": generated_at,
        "requested_by_user_id": request.requested_by_user_id,
        "source": request.source,
        "export_cap": spec.export_row_cap,
        "matched_rows": matched_rows,
        "exported_rows": exported_rows,
        "export_truncated": export_truncated,
    }

    return ExportResult(
        export_id=export_id,
        format=request.format,
        filename=filename,
        content_type=content_type,
        row_count=exported_rows,
        columns=[display for display, _ in columns],
        filters=filters,
        source_tool_name=request.tool_name,
        generated_at=generated_at,
        source_metadata=source_metadata,
        warnings=warnings,
        body=body,
        matched_rows=matched_rows,
        exported_rows=exported_rows,
        export_cap=spec.export_row_cap,
        export_truncated=export_truncated,
    )


# ---------------------------------------------------------------------------
# Report-style export path
# ---------------------------------------------------------------------------


# Defensive list of arg keys the caller MUST NOT smuggle a pre-baked
# report body / pre-baked rows through. The whole point of the
# server-side re-run is that the bytes come from the deterministic
# tool result, not from the chat surface. These fields would let a
# caller pass a fabricated narrative into a PDF that looks
# audit-grade; we drop them before invoking the tool.
_REPORT_FORBIDDEN_ARG_KEYS: frozenset[str] = frozenset(
    {
        "body",
        "report_body",
        "html",
        "markdown",
        "pdf_bytes",
        "rows",
        "summary",
        "monthly",
        "top_accounts",
        "scope",
        "formula",
    }
)


async def _build_report_export(
    request: ExportRequest,
    *,
    tool_runner: ToolRunner,
) -> ExportResult:
    """Report-style PDF export path. Mirrors the row-table path's
    contract — re-run the approved tool server-side, then format the
    deterministic result. Caller-supplied report bodies / rows are
    stripped so the PDF cannot carry fabricated content."""
    if request.format not in _REPORT_FORMATS:
        raise ValueError(
            f"format {request.format!r} is not supported for "
            f"{request.tool_name!r}. Supported formats for this tool: "
            f"{sorted(_REPORT_FORMATS)}."
        )

    report_spec = EXPORTABLE_REPORTS[request.tool_name]

    # Strip client-smuggled report bodies / pre-baked sections before
    # invoking the tool. The tool result itself is the only allowed
    # source of report content.
    export_args: dict[str, Any] = dict(request.tool_args or {})
    for forbidden in _REPORT_FORBIDDEN_ARG_KEYS:
        export_args.pop(forbidden, None)
    for preview_key in report_spec.preview_arg_keys:
        export_args.pop(preview_key, None)

    from services.ai.agents.tools.nova_tools import _ScenarioState

    state = _ScenarioState()
    tool_result = await tool_runner(
        request.tool_name,
        export_args,
        state,
        request.requested_by_user_id or "export",
        None,
        None,
    )

    if not isinstance(tool_result, dict):
        raise ValueError(
            f"tool {request.tool_name!r} returned a non-dict result; "
            "cannot export."
        )
    if tool_result.get("error"):
        raise ValueError(
            f"tool {request.tool_name!r} returned an error: "
            f"{tool_result.get('error')!r} — "
            f"{tool_result.get('detail', '')!r}"
        )

    generated_at = _utcnow_iso()
    export_id = _new_export_id()
    safe_tool = _safe_filename_segment(request.tool_name)
    filename = f"nova_{safe_tool}_{export_id[-8:]}.{request.format}"
    content_type = _CONTENT_TYPES[request.format]

    # Render the PDF from the deterministic tool dict. The renderer
    # does not recompute variance / totals; it quotes the tool result
    # verbatim. See finance_report_pdf.render_driver_explanation_pdf.
    from services.nova_engine.finance_report_pdf import (
        render_driver_explanation_pdf,
        report_sections_for_driver_pdf,
    )

    if report_spec.renderer_key == "driver_explanation":
        body = render_driver_explanation_pdf(
            tool_result, generated_at=generated_at
        )
        report_sections = report_sections_for_driver_pdf(tool_result)
    else:
        # Unknown renderer key — defensive guard for future report
        # tools that get registered without wiring a renderer. Refuse
        # explicitly rather than producing an empty PDF.
        raise ValueError(
            f"no PDF renderer registered for renderer_key "
            f"{report_spec.renderer_key!r}."
        )

    # Filters reflect the tool args we actually invoked the tool with
    # (after stripping forbidden keys); a reviewer reproducing the
    # PDF from a shell would use exactly these.
    filters: dict[str, Any] = dict(export_args)
    scope = tool_result.get("scope") if isinstance(tool_result.get("scope"), dict) else {}

    warnings: list[str] = []
    tool_warnings = tool_result.get("warnings") or []
    if isinstance(tool_warnings, list):
        warnings.extend(str(w) for w in tool_warnings if w)

    # row_count is not meaningful for report-style exports; surface
    # the per-section row count instead so callers can tell at a
    # glance how dense the PDF is. ``monthly`` + ``top_accounts``
    # are the only tabular sections; their lengths reflect what a
    # reviewer would see in the PDF tables.
    monthly = tool_result.get("monthly") or []
    top_accounts = tool_result.get("top_accounts") or []
    tabular_row_count = (
        (len(monthly) if isinstance(monthly, list) else 0)
        + (len(top_accounts) if isinstance(top_accounts, list) else 0)
    )

    source_metadata: dict[str, Any] = {
        "source_tool_name": request.tool_name,
        # Re-running this command produces the same PDF byte-for-
        # byte (modulo the generated_at timestamp).
        "tool_args": export_args,
        "tool_response_keys": sorted(
            k for k in tool_result if k not in _GLOBAL_DROP_FIELDS
        ),
        "generated_at": generated_at,
        "requested_by_user_id": request.requested_by_user_id,
        "source": request.source,
        # Report-shaped metadata: which sections the renderer drew
        # and the deterministic scope/metric the PDF is about. The
        # chat UI uses these to render a richer download card and an
        # auditor uses them to reproduce the report from the JSON.
        "report_sections": report_sections,
        "report_scope": {
            "metric": tool_result.get("metric") or scope.get("metric"),
            "basis": scope.get("basis"),
            "current_period_start": scope.get("current_period_start"),
            "current_period_end": scope.get("current_period_end"),
            "comparison_mode": scope.get("comparison_mode"),
            "comparison_period_start": scope.get("comparison_period_start"),
            "comparison_period_end": scope.get("comparison_period_end"),
        },
    }

    return ExportResult(
        export_id=export_id,
        format=request.format,
        filename=filename,
        content_type=content_type,
        # Reviewers reading the metadata see a numeric size for the
        # tabular sections; the full set of report sections lives in
        # source_metadata.report_sections.
        row_count=tabular_row_count,
        columns=list(report_sections),
        filters=filters,
        source_tool_name=request.tool_name,
        generated_at=generated_at,
        source_metadata=source_metadata,
        warnings=warnings,
        body=body,
        matched_rows=None,
        exported_rows=tabular_row_count,
        export_cap=0,
        export_truncated=False,
    )


__all__ = [
    "EXPORTABLE_REPORTS",
    "EXPORTABLE_TOOLS",
    "ExportRequest",
    "ExportResult",
    "ExportableTool",
    "ReportExportableTool",
    "SUPPORTED_FORMATS_BY_TOOL",
    "build_export",
]
