"""Server-side PDF rendering for Nova report-style finance exports.

Row-level finance queries (GL rows, invoice rows, open-bill rows)
already export cleanly as CSV / XLSX via
``finance_export_service``. This module covers the OTHER export
shape: a single deterministic report that summarises a finance
narrative, today the ``explain_finance_drivers`` driver / variance
breakdown.

Hard rules — the same ones that govern the CSV / XLSX path:

  * The PDF body is rendered from the deterministic tool result
    dict (``analyze_finance_drivers``-shaped). It is NEVER assembled
    from the assistant's markdown reply, and it never re-computes
    finance numbers. All variance / monthly / top-account values
    are quoted verbatim from the tool result.
  * Caller-supplied report bodies / pre-baked rows are ignored.
    The export service re-runs the tool server-side and passes the
    fresh dict in here; this module only formats.
  * Numbers are formatted for readability (thousands separator,
    two decimals, percent sign for variance_percent) but the
    underlying value is the deterministic one. The same number a
    reviewer would see in the JSON response.
  * Warnings the tool surfaced are rendered verbatim so a reader
    sees the same caveats Nova would quote in chat.

Returns the PDF as bytes — the export service writes them straight
into the existing GridFS / artifact pipeline alongside CSV / XLSX
artifacts. The chat UI already downloads arbitrary blob types via
``FinanceExportArtifact.jsx`` so no frontend change is required.

reportlab is already pinned in ``backend/requirements.txt``.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any, Optional
from xml.sax.saxutils import escape as _xml_escape


# Every value rendered inside a reportlab Paragraph passes through
# _xml_escape_text. reportlab parses Paragraph bodies as a tiny XML
# dialect so an unescaped < or > or & raises XMLSyntaxError mid PDF
# build. Centralising escape here covers all call sites without per
# field audit. Account names like Smith and Co or memo strings like
# variance lt comparison_total now render as text rather than tags.
def _xml_escape_text(value: Any) -> str:
    """Coerce value to str then escape angle brackets and ampersand.
    Quote escaping is left off because reportlab handles attribute
    quotes via tag syntax not body text. None becomes empty string."""
    if value is None:
        return ""
    return _xml_escape(str(value))


# Metric → human-readable report-title fragment. The driver tool only
# accepts the four computed metrics today; anything else falls back to
# a generic title so we never produce a misleading header (e.g.
# "Cash Driver Explanation Report" when the tool refused with a
# routing hint).
_METRIC_TITLE: dict[str, str] = {
    "revenue": "Revenue",
    "expenses": "Expenses",
    "cogs": "COGS",
    "gross_profit": "Gross Profit",
}


def _format_currency(value: Any) -> str:
    """Two-decimal currency-like rendering. Negative numbers keep a
    leading minus; we deliberately avoid the accounting-style
    parentheses because reviewers reading a PDF on a phone screen tend
    to miss them."""
    if value is None:
        return "N/A"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{num:,.2f}"


def _format_percent(value: Any) -> str:
    """Variance-percent rendering matching
    ``_format_variance_percent_display`` in finance_driver_analysis so
    chat and PDF agree on how a null / zero-comparison percent reads."""
    if value is None:
        return "N/A"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{num:+.1%}"


def _format_share(value: Any) -> str:
    """Account share-of-total rendering — same shape as
    ``share_pct`` in the tool response (0.0–1.0 float)."""
    if value is None:
        return ""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{num:.1%}"


def _safe_text(value: Any) -> str:
    """Coerce any tool-supplied value into a plain string. None and
    missing fields render as an empty string so the PDF doesn't carry
    literal "None" cells. Lists become comma-joined; dicts get a
    placeholder marker (they should not appear in the tool shape we
    render, but defensive)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return ", ".join(_safe_text(v) for v in value)
    return "[object]"


def _title_for(metric: Optional[str]) -> str:
    if isinstance(metric, str) and metric in _METRIC_TITLE:
        return f"{_METRIC_TITLE[metric]} Driver Explanation Report"
    return "Finance Driver Explanation Report"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------


def render_driver_explanation_pdf(
    tool_result: dict,
    *,
    generated_at: Optional[str] = None,
) -> bytes:
    """Render an ``explain_finance_drivers`` tool result as a PDF.

    Args:
      tool_result: the dict returned by
        ``services.nova_engine.finance_driver_analysis.analyze_finance_drivers``.
        Must carry ``scope``, ``formula``, ``summary``, ``monthly``,
        ``top_accounts``, ``warnings`` keys; missing keys render as
        empty sections rather than raising — the chat surface treats a
        partial response the same way.
      generated_at: ISO timestamp to record in the header; defaults
        to ``datetime.now(timezone.utc).isoformat()``.

    Returns:
      The PDF bytes (starts with ``%PDF``). The caller writes these
      into the existing GridFS / artifact pipeline.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:  # pragma: no cover — reportlab is in requirements.txt
        raise RuntimeError(
            "reportlab is required for PDF exports but is not installed."
        ) from exc

    scope = tool_result.get("scope") or {}
    formula = tool_result.get("formula") or {}
    summary = tool_result.get("summary") or {}
    monthly: list[dict[str, Any]] = list(tool_result.get("monthly") or [])
    top_accounts: list[dict[str, Any]] = list(tool_result.get("top_accounts") or [])
    warnings: list[str] = [
        str(w) for w in (tool_result.get("warnings") or []) if w
    ]

    metric = tool_result.get("metric") or scope.get("metric")
    when = generated_at or _now_iso()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.6 * inch,
        title=_title_for(metric),
        author="Nova / AEGISAXIOM",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "NovaReportTitle",
        parent=styles["Title"],
        fontSize=18,
        leading=22,
        spaceAfter=10,
    )
    h2_style = ParagraphStyle(
        "NovaReportH2",
        parent=styles["Heading2"],
        fontSize=12,
        leading=15,
        spaceBefore=12,
        spaceAfter=6,
        textColor=colors.HexColor("#4338ca"),
    )
    body_style = ParagraphStyle(
        "NovaReportBody",
        parent=styles["BodyText"],
        fontSize=9.5,
        leading=12,
    )
    mono_style = ParagraphStyle(
        "NovaReportMono",
        parent=styles["BodyText"],
        fontName="Courier",
        fontSize=9,
        leading=11,
    )

    elements: list[Any] = []
    elements.append(Paragraph(_title_for(metric), title_style))

    # ─── Metadata block ──────────────────────────────────────────────
    meta_rows: list[list[str]] = [
        ["Metric", _safe_text(metric)],
        ["Basis", _safe_text(scope.get("basis"))],
        [
            "Current period",
            f"{_safe_text(scope.get('current_period_start'))} to "
            f"{_safe_text(scope.get('current_period_end'))}",
        ],
    ]
    cmp_label = scope.get("comparison_label") or scope.get("comparison_mode")
    if cmp_label or scope.get("comparison_period_start"):
        meta_rows.append(["Comparison label", _safe_text(cmp_label)])
        meta_rows.append(
            [
                "Comparison period",
                f"{_safe_text(scope.get('comparison_period_start'))} to "
                f"{_safe_text(scope.get('comparison_period_end'))}",
            ]
        )
    if scope.get("comparison_mode"):
        meta_rows.append(["Comparison mode", _safe_text(scope.get("comparison_mode"))])
    included = scope.get("included_revenue_categories")
    if included:
        meta_rows.append(["Included categories", _safe_text(included)])
    meta_rows.append(["Source tool", "explain_finance_drivers"])
    meta_rows.append(["Generated at", when])

    elements.append(Paragraph("Report metadata", h2_style))
    meta_table = Table(meta_rows, colWidths=[1.8 * inch, 5.0 * inch])
    meta_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
            ]
        )
    )
    elements.append(meta_table)

    # ─── Formula block ───────────────────────────────────────────────
    elements.append(Paragraph("Formula", h2_style))
    variance_formula = (
        formula.get("variance_amount")
        or "current_period_total - comparison_period_total"
    )
    pct_formula = (
        formula.get("variance_percent")
        or "variance_amount / comparison_period_total"
    )
    elements.append(
        Paragraph(
            f"variance_amount = {_xml_escape_text(_safe_text(variance_formula))}",
            mono_style,
        )
    )
    elements.append(
        Paragraph(
            f"variance_percent = {_xml_escape_text(_safe_text(pct_formula))}",
            mono_style,
        )
    )

    # ─── Summary table ───────────────────────────────────────────────
    elements.append(Paragraph("Summary", h2_style))
    summary_rows = [
        ["Metric", "Value"],
        [
            "Current period total",
            _format_currency(summary.get("current_period_total")),
        ],
        [
            "Comparison period total",
            _format_currency(summary.get("comparison_period_total")),
        ],
        ["Variance amount", _format_currency(summary.get("variance_amount"))],
        [
            "Variance percent",
            _format_percent(summary.get("variance_percent"))
            if summary.get("variance_percent") is not None
            else _safe_text(
                summary.get("variance_percent_display") or "N/A"
            ),
        ],
    ]
    summary_table = Table(summary_rows, colWidths=[2.8 * inch, 4.0 * inch])
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4338ca")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
            ]
        )
    )
    elements.append(summary_table)

    # ─── Monthly movement ────────────────────────────────────────────
    elements.append(Paragraph("Monthly movement", h2_style))
    if monthly:
        has_comparison = any(
            row.get("comparison_amount") is not None for row in monthly
        )
        if has_comparison:
            monthly_rows = [["Month", "Current", "Comparison", "Variance"]]
            for row in monthly:
                monthly_rows.append(
                    [
                        _safe_text(row.get("month")),
                        _format_currency(row.get("amount")),
                        _format_currency(row.get("comparison_amount")),
                        _format_currency(row.get("variance_amount")),
                    ]
                )
            col_widths = [1.4 * inch, 1.8 * inch, 1.8 * inch, 1.8 * inch]
        else:
            monthly_rows = [["Month", "Amount"]]
            for row in monthly:
                monthly_rows.append(
                    [
                        _safe_text(row.get("month")),
                        _format_currency(row.get("amount")),
                    ]
                )
            col_widths = [2.0 * inch, 4.8 * inch]
        monthly_table = Table(monthly_rows, colWidths=col_widths, repeatRows=1)
        monthly_table.setStyle(_table_style())
        elements.append(monthly_table)
    else:
        elements.append(Paragraph("No monthly movement reported.", body_style))

    # ─── Top accounts ────────────────────────────────────────────────
    elements.append(Paragraph("Top contributing accounts", h2_style))
    if top_accounts:
        acct_rows = [["Account", "Category", "Amount", "Share %"]]
        for entry in top_accounts:
            acct_rows.append(
                [
                    _safe_text(entry.get("account_name")),
                    _safe_text(entry.get("category")),
                    _format_currency(entry.get("amount")),
                    _format_share(entry.get("share_pct")),
                ]
            )
        acct_table = Table(
            acct_rows,
            colWidths=[3.0 * inch, 1.4 * inch, 1.4 * inch, 1.0 * inch],
            repeatRows=1,
        )
        acct_table.setStyle(_table_style())
        elements.append(acct_table)
    else:
        elements.append(
            Paragraph("No top contributing accounts reported.", body_style)
        )

    # ─── Warnings ────────────────────────────────────────────────────
    elements.append(Paragraph("Warnings and notes", h2_style))
    if warnings:
        for warning in warnings:
            # Warning strings come from tool result and may contain
            # XML special chars. Escape before handing to Paragraph
            # so that the reportlab XML parser does not raise.
            elements.append(
                Paragraph(f"• {_xml_escape_text(warning)}", body_style)
            )
            elements.append(Spacer(1, 2))
    else:
        elements.append(Paragraph("No warnings reported by the tool.", body_style))

    doc.build(elements)
    return buf.getvalue()


def _table_style() -> Any:
    """Shared table style for the monthly + top-accounts tables.
    Kept out of the render fn so both tables stay visually identical
    without copy-pasting the style list."""
    from reportlab.lib import colors
    from reportlab.platypus import TableStyle

    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4338ca")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (-2, 1), (-1, -1), "RIGHT"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
        ]
    )


def report_sections_for_driver_pdf(tool_result: dict) -> list[str]:
    """Return the section labels the PDF surfaced. Exposed so the
    export-metadata block can carry a ``report_sections`` list without
    re-parsing the rendered PDF — the chat UI can show "this PDF has a
    summary, monthly, top_accounts, warnings section" without a second
    backend round-trip.

    Order matches the rendered document. Sections that fell back to a
    placeholder (e.g. monthly was empty) are NOT dropped from the list
    because the section still exists in the document; the placeholder
    is part of the rendered output."""
    sections = ["metadata", "formula", "summary"]
    if tool_result.get("monthly"):
        sections.append("monthly")
    else:
        sections.append("monthly_empty")
    if tool_result.get("top_accounts"):
        sections.append("top_accounts")
    else:
        sections.append("top_accounts_empty")
    sections.append("warnings")
    return sections


__all__ = [
    "render_driver_explanation_pdf",
    "report_sections_for_driver_pdf",
]
