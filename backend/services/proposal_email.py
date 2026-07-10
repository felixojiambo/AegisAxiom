"""Proposal email — customer-facing send composer.

Distinct from the existing ``send_proposal`` route in
``routes/proposals.py`` which records the INTERNAL handoff to
Sales (no email is sent there). This module is the customer-facing
channel: render the proposal PDF, build a polite HTML body, attach
the PDF, and hand the package to ``services.email_service.send_with_attachments``.

Returns a structured result so the route can stamp the right
``email_status`` on the proposal and surface the right HTTP code:

  - ``status="sent"`` → 200; route stamps ``email_sent_at`` /
    ``email_sent_by`` / ``email_recipients`` / ``email_subject``.
  - ``status="not_configured"`` → 503; route stamps
    ``email_status="not_configured"`` but NOT the sent_* metadata.
  - ``status="failed"`` → 502; route stamps
    ``email_status="failed"`` + sanitized ``email_error``.

This module never logs the HTML body, never logs the attachment
bytes, never includes raw API secrets in the result envelope.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Optional


logger = logging.getLogger(__name__)


def _safe(text: Any) -> str:
    """HTML-escape, similar to the renderer's helper."""
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _default_subject(proposal: dict, opportunity: Optional[dict]) -> str:
    parts: list[str] = []
    title = proposal.get("title")
    if title:
        parts.append(str(title))
    if opportunity and opportunity.get("account_name"):
        parts.append(f"for {opportunity['account_name']}")
    if not parts:
        parts.append("Your proposal from SimpliMeta")
    return " ".join(parts).strip()


def _default_html_body(
    proposal: dict,
    opportunity: Optional[dict],
    body_override: Optional[str],
    pdf_attached: bool,
) -> str:
    """Compose the HTML email body. Uses the proposal's existing
    ``generated_summary`` when present so the body is grounded in
    real content rather than a synthetic template. ``body_override``
    wins when supplied; the proposal summary is the next-best
    default. The final fallback is a short polite default."""
    headline = "Your proposal"
    if opportunity and opportunity.get("account_name"):
        headline = f"Your proposal for {_safe(opportunity['account_name'])}"

    if body_override and body_override.strip():
        body = _safe(body_override).replace("\n", "<br/>")
    else:
        summary = (proposal.get("generated_summary") or "").strip()
        if summary:
            body = _safe(summary).replace("\n", "<br/>")
        else:
            body = (
                "Please find your proposal attached. Reply to this email "
                "or reach out to your SimpliMeta contact with any "
                "questions or changes."
            )

    attachment_note = (
        "<p style='color:#64748b;font-size:12px;'>"
        "Your proposal is attached as a PDF."
        "</p>"
    ) if pdf_attached else ""

    return f"""\
<div style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;
            padding:32px 24px;color:#1f2937;">
  <h1 style="color:#6d28d9;font-size:22px;margin:0 0 16px 0;">
    {_safe(headline)}
  </h1>
  <div style="font-size:14px;line-height:1.6;">
    {body}
  </div>
  {attachment_note}
  <p style="color:#94a3b8;font-size:11px;margin-top:32px;">
    Sent via SimpliMeta AEGISAXIOM · {datetime.now(timezone.utc).strftime('%B %d, %Y')}
  </p>
</div>
"""


async def send_proposal_email(
    *,
    proposal: dict,
    opportunity: Optional[dict],
    sows: Optional[Iterable[dict]],
    recipients: list[str],
    subject_override: Optional[str] = None,
    body_override: Optional[str] = None,
    attach_pdf: bool = True,
) -> dict:
    """Compose + send the customer-facing proposal email.

    Returns a result dict::

        {
          "status":     "sent" | "not_configured" | "failed",
          "error":      Optional[str],     # sanitized
          "recipients": list[str],
          "subject":    str,
          "attached_pdf": bool,
        }
    """
    if not recipients:
        # Caller is expected to validate but defensive guard so this
        # service never fans out to an empty list.
        return {
            "status": "failed",
            "error": "no recipients provided",
            "recipients": [],
            "subject": subject_override or "",
            "attached_pdf": False,
        }

    subject = (subject_override or "").strip() or _default_subject(proposal, opportunity)

    attachments: list[dict] = []
    attached_pdf = False
    if attach_pdf:
        try:
            from services.proposal_pdf import (
                proposal_pdf_filename,
                render_proposal_pdf,
            )
            pdf_bytes = render_proposal_pdf(
                proposal, opportunity=opportunity, sows=sows,
            )
            attachments.append({
                "filename": proposal_pdf_filename(proposal, opportunity=opportunity),
                "content": base64.b64encode(pdf_bytes).decode("ascii"),
            })
            attached_pdf = True
        except RuntimeError as exc:
            # reportlab missing on the deployment. We still send the
            # email — the body carries the proposal summary — but
            # flag that no PDF was attached.
            logger.warning("Proposal PDF unavailable for email attach: %s", exc)

    html = _default_html_body(
        proposal=proposal,
        opportunity=opportunity,
        body_override=body_override,
        pdf_attached=attached_pdf,
    )

    from services.email_service import send_with_attachments
    result = await send_with_attachments(
        recipients,
        subject,
        html,
        attachments=attachments or None,
    )
    return {
        "status": result.get("status", "failed"),
        "error": result.get("error"),
        "recipients": result.get("recipients", recipients),
        "subject": subject,
        "attached_pdf": attached_pdf,
    }
