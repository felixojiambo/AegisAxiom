"""Proposal PDF renderer.

Generates a customer-facing PDF from a stored Proposal plus the
linked opportunity and SOW(s). The PDF is composed from the
EXISTING proposal content (``generated_content`` / ``generated_summary``)
and the EXISTING SOW shapes — no new template, no LLM call, no
external API.

Uses ``reportlab`` (already declared in ``backend/requirements.txt``
and exercised by ``routes/vega_v3_exports.py``). The renderer follows
the same brand palette so a stack of AEGISAXIOM-exported documents looks
visually consistent.

Public entry point::

    render_proposal_pdf(proposal: dict, opportunity: Optional[dict],
                        sows: Optional[Iterable[dict]]) -> bytes

Returns the PDF bytes. Raises ``RuntimeError`` if reportlab is not
available so the calling route can surface a 503 instead of a
silent empty download.

Renders the following sections in order. Each is conditional on
data being present so the renderer degrades gracefully when only a
minimal proposal is on hand:

  1. Title block — proposal title or "Proposal", opportunity account
     name, deal name when present, generated date.
  2. Executive summary — ``proposal.generated_summary``.
  3. Proposal narrative — ``proposal.generated_content``.
  4. For each linked SOW:
       4a. SOW header — name, line_of_business, status.
       4b. SMART proposal lines when present.
       4c. Milestones (waterfall PS) — phase / task / hours table.
       4d. Materials / hardware / services tables (FS) when present.
       4e. Totals — total_estimated_hours / total_value when present.
  5. Footer on each page with page number + AEGISAXIOM attribution.

This module is intentionally stand-alone — no DB access, no auth
checks. The route layer is responsible for loading the documents
and gating permission.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional


_BRAND = "#6d28d9"
_BRAND_LIGHT = "#a78bfa"
_INK = "#1f2937"
_SUBTLE = "#64748b"
_DIVIDER = "#e5e7eb"


def _safe(text: Any) -> str:
    """Escape characters that confuse reportlab's mini-markup parser."""
    if text is None:
        return ""
    s = str(text)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _coerce_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _fmt_money(value: Any) -> str:
    if value is None or value == "":
        return ""
    f = _coerce_float(value)
    return f"${f:,.2f}"


def _fmt_hours(value: Any) -> str:
    if value is None or value == "":
        return ""
    f = _coerce_float(value)
    if f == int(f):
        return f"{int(f)} hrs"
    return f"{f:.2f} hrs"


def render_proposal_pdf(
    proposal: dict,
    opportunity: Optional[dict] = None,
    sows: Optional[Iterable[dict]] = None,
) -> bytes:
    """Render the proposal + opportunity + SOWs to PDF bytes."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:  # pragma: no cover — declared in requirements
        raise RuntimeError(
            "reportlab is not installed; cannot render proposal PDF. "
            "Add reportlab to backend/requirements.txt and reinstall."
        ) from exc

    brand = colors.HexColor(_BRAND)
    brand_light = colors.HexColor(_BRAND_LIGHT)
    ink = colors.HexColor(_INK)
    subtle = colors.HexColor(_SUBTLE)
    divider = colors.HexColor(_DIVIDER)

    base = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ProposalTitle", parent=base["Title"],
        fontName="Helvetica-Bold", fontSize=22, leading=26,
        textColor=brand, alignment=TA_LEFT, spaceAfter=2,
    )
    subtitle_style = ParagraphStyle(
        "ProposalSubtitle", parent=base["Normal"],
        fontName="Helvetica", fontSize=10, leading=13,
        textColor=subtle, spaceAfter=12,
    )
    h2_style = ParagraphStyle(
        "H2", parent=base["Heading2"],
        fontName="Helvetica-Bold", fontSize=14, leading=18,
        textColor=brand, spaceBefore=14, spaceAfter=6,
    )
    h3_style = ParagraphStyle(
        "H3", parent=base["Heading3"],
        fontName="Helvetica-Bold", fontSize=11, leading=14,
        textColor=ink, spaceBefore=8, spaceAfter=2,
    )
    body_style = ParagraphStyle(
        "Body", parent=base["BodyText"],
        fontName="Helvetica", fontSize=10, leading=14,
        textColor=ink, spaceAfter=4, alignment=TA_LEFT,
    )
    small_style = ParagraphStyle(
        "Small", parent=body_style, fontSize=9, leading=12, textColor=subtle,
    )

    buf = io.BytesIO()
    title_text = proposal.get("title") or "Proposal"
    doc = SimpleDocTemplate(
        buf, pagesize=letter, title=title_text,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.7 * inch, bottomMargin=0.85 * inch,
    )

    def _on_page(canvas, _doc):
        canvas.saveState()
        canvas.setStrokeColor(brand_light)
        canvas.setLineWidth(2)
        canvas.line(0.7 * inch, letter[1] - 0.55 * inch,
                    letter[0] - 0.7 * inch, letter[1] - 0.55 * inch)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(subtle)
        canvas.drawString(0.7 * inch, 0.5 * inch, "AEGISAXIOM — Sales Proposal")
        canvas.drawRightString(letter[0] - 0.7 * inch, 0.5 * inch,
                               f"Page {_doc.page}")
        canvas.restoreState()

    flow: List[Any] = []

    # ---- 1. Title block ----
    flow.append(Paragraph(_safe(title_text), title_style))

    subtitle_parts: List[str] = []
    if opportunity:
        for key in ("account_name", "name"):
            val = opportunity.get(key)
            if val:
                subtitle_parts.append(_safe(val))
                break
        deal = opportunity.get("deal_name") or opportunity.get("deal")
        if deal:
            subtitle_parts.append(f"Deal: {_safe(deal)}")
    subtitle_parts.append(
        f"Generated {datetime.now(timezone.utc).strftime('%B %d, %Y')}"
    )
    if proposal.get("version"):
        subtitle_parts.append(f"v{proposal['version']}")
    flow.append(Paragraph(" · ".join(subtitle_parts), subtitle_style))

    # ---- 2. Executive summary ----
    summary = (proposal.get("generated_summary") or "").strip()
    if summary:
        flow.append(Paragraph("Executive Summary", h2_style))
        flow.append(Paragraph(_safe(summary), body_style))

    # ---- 3. Proposal narrative ----
    body = (proposal.get("generated_content") or "").strip()
    if body:
        flow.append(Paragraph("Proposal", h2_style))
        # Preserve paragraph breaks. reportlab Paragraph accepts a
        # subset of HTML; we feed one paragraph per blank-line block.
        for chunk in body.split("\n\n"):
            chunk = chunk.strip()
            if not chunk:
                continue
            # Convert single newlines into <br/> so list-like text
            # keeps its line breaks.
            html = _safe(chunk).replace("\n", "<br/>")
            flow.append(Paragraph(html, body_style))
    if (proposal.get("notes") or "").strip():
        flow.append(Paragraph("Notes", h3_style))
        flow.append(Paragraph(_safe(proposal["notes"]), body_style))

    # ---- 4. SOW sections ----
    for sow in (sows or []):
        if not isinstance(sow, dict):
            continue
        sow_title = sow.get("name") or sow.get("sow_id") or "Statement of Work"
        flow.append(Spacer(1, 8))
        flow.append(Paragraph(_safe(sow_title), h2_style))
        meta_bits: List[str] = []
        if sow.get("line_of_business"):
            meta_bits.append(f"LOB: {_safe(sow['line_of_business'])}")
        if sow.get("status"):
            meta_bits.append(f"Status: {_safe(sow['status'])}")
        if meta_bits:
            flow.append(Paragraph(" · ".join(meta_bits), small_style))

        # 4b. SMART proposal lines.
        smart_pairs = [
            ("Specific", sow.get("smart_specific")),
            ("Measurable", sow.get("smart_measurable")),
            ("Achievable", sow.get("smart_achievable")),
            ("Relevant", sow.get("smart_relevant")),
            ("Time-bound", sow.get("smart_time_bound")),
        ]
        smart_present = [(label, val) for label, val in smart_pairs if (val or "").strip()]
        if smart_present:
            flow.append(Paragraph("SMART Proposal", h3_style))
            for label, val in smart_present:
                flow.append(Paragraph(
                    f"<b>{label}:</b> {_safe(val)}", body_style,
                ))

        # 4c. Milestones (PS / waterfall).
        milestones = sow.get("milestones") or []
        if milestones:
            flow.append(Paragraph("Milestones", h3_style))
            rows = [["Milestone / Task", "Deliverable", "Est. hrs"]]
            for ms in milestones:
                if not isinstance(ms, dict):
                    continue
                ms_name = _safe(ms.get("milestone_name") or "")
                rows.append([Paragraph(f"<b>{ms_name}</b>", body_style), "", ""])
                for bullet in ms.get("bullets") or []:
                    if not isinstance(bullet, dict):
                        continue
                    rows.append([
                        Paragraph(_safe(bullet.get("bullet_text") or ""), body_style),
                        Paragraph(_safe(bullet.get("deliverable") or ""), body_style),
                        _fmt_hours(bullet.get("estimated_hours")),
                    ])
            if len(rows) > 1:
                tbl = Table(rows, colWidths=[2.9 * inch, 2.6 * inch, 1.0 * inch])
                tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), brand_light),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.5, divider),
                    ("GRID", (0, 1), (-1, -1), 0.25, divider),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]))
                flow.append(tbl)

        # 4d. Field Services materials / hardware / services tables.
        for label, key, cols in (
            ("Materials", "materials", ["item", "quantity", "unit_price"]),
            ("Hardware", "hardware", ["item", "quantity", "unit_price"]),
            ("Services", "services", ["item", "quantity", "unit_price"]),
        ):
            items = sow.get(key) or []
            rendered = [it for it in items if isinstance(it, dict)]
            if not rendered:
                continue
            flow.append(Paragraph(label, h3_style))
            rows = [[c.replace("_", " ").title() for c in cols] + ["Line total"]]
            for it in rendered:
                qty = _coerce_float(it.get("quantity"))
                unit = _coerce_float(it.get("unit_price"))
                line_total = qty * unit
                rows.append([
                    _safe(it.get(cols[0]) or it.get("name") or ""),
                    f"{qty:g}" if qty else "",
                    _fmt_money(unit) if unit else "",
                    _fmt_money(line_total) if line_total else "",
                ])
            tbl = Table(rows, colWidths=[3.0 * inch, 1.0 * inch, 1.2 * inch, 1.3 * inch])
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), brand_light),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, divider),
                ("GRID", (0, 1), (-1, -1), 0.25, divider),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]))
            flow.append(tbl)

        # 4e. Totals.
        totals_rows: List[List[str]] = []
        teh = sow.get("total_estimated_hours")
        if teh:
            totals_rows.append(["Total estimated hours", _fmt_hours(teh)])
        tv = sow.get("total_value")
        if tv:
            totals_rows.append(["Total value", _fmt_money(tv)])
        if totals_rows:
            flow.append(Paragraph("Totals", h3_style))
            tbl = Table(totals_rows, colWidths=[4.5 * inch, 2.0 * inch])
            tbl.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, divider),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]))
            flow.append(tbl)

    if not flow:
        # Degenerate proposal — render a placeholder rather than empty PDF.
        flow.append(Paragraph(_safe(title_text), title_style))
        flow.append(Paragraph(
            "This proposal does not yet carry generated content. "
            "Once the underlying SOW(s) are finalized and the proposal "
            "is regenerated, the PDF will populate automatically.",
            body_style,
        ))

    doc.build(flow, onFirstPage=_on_page, onLaterPages=_on_page)
    return buf.getvalue()


def proposal_pdf_filename(proposal: dict, opportunity: Optional[dict] = None) -> str:
    """Build a stable, filesystem-friendly filename for the download."""
    parts: List[str] = []
    if opportunity and opportunity.get("account_name"):
        parts.append(str(opportunity["account_name"]))
    title = proposal.get("title") or "proposal"
    parts.append(str(title))
    version = proposal.get("version")
    if version:
        parts.append(f"v{version}")
    parts.append(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    slug = " - ".join(parts)
    # Sanitize for filesystem.
    safe = "".join(c if c.isalnum() or c in " -_." else "_" for c in slug)
    return f"{safe.strip().rstrip('.')}.pdf"
