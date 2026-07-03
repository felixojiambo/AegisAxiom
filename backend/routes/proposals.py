"""Proposal routes — Sales Ops MVP.

Generates and tracks the deliverable proposal artifact that ships from
the Solutions Design workflow. The MVP intentionally keeps scope narrow:

  * Artifact is a Google Doc URL (matches the existing SOW finalize
    convention). No PDF generation, no in-product email send.
  * ``POST /opportunities/{id}/proposal`` requires at least one SOW on
    the opportunity whose status is ``ready_for_review`` or ``complete``
    — same gate Sales Ops applies when promoting a SOW to a client.
  * ``POST /proposals/{id}/send`` is mark-as-sent only: it flips status
    to ``sent`` and stamps ``sent_at`` / ``sent_by`` / ``sent_to_email``
    so the audit trail exists for a future email-send path without a
    migration.
  * Status transitions accepted by ``PATCH`` cover the manual workflow
    (``draft`` → ``ready_for_review``, ``sent`` → ``accepted | rejected``).
    The ``sent`` status is only reachable via ``/send`` so the route
    cannot drift away from stamping the metadata.
  * Accepted / rejected do NOT auto-drive the parent opportunity stage.
    That gate is explicitly deferred — Sales Ops drives stage by hand.

Routes mounted under the ``/sales-ops`` prefix in server.py so the
frontend's existing ``/api/sales-ops/...`` base path keeps working.
"""
from __future__ import annotations

import logging
import os
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field, HttpUrl

from auth_helper import get_current_user, has_perm
from database import get_db
from middleware.ai_authz import require_ai_permission
from models import (
    MarkClientSentRequest,
    Proposal,
    ProposalCreate,
    ProposalSendRequest,
    ProposalUpdate,
    gen_id,
)
from services.audit_service import log_action as audit_log_action
from services.proposal_synthesis import apply_polish_with_drift_guard
from services.field_options import PROPOSAL_STATUSES
from services.field_registry import (
    register_fields,
    register_module_config,
    register_module_events,
)
from services.proposal_generation import generate_proposal_content


router = APIRouter()
logger = logging.getLogger(__name__)


# Status values a SOW must carry before a proposal can be drafted from
# it. Mirrors the gate Sales Ops applies when promoting a SOW to a
# client. Kept narrow on purpose; the policy engine can override later.
_SOW_READY_STATUSES = frozenset({"ready_for_review", "complete"})


# Allowed proposal status transitions for the PATCH route.
#
# Why a transition table instead of an allow set. Earlier code accepted
# any of draft, ready_for_review, accepted, or rejected as the target
# status regardless of current state. That let PATCH skip the sent state
# entirely, which silently dropped sent_at, sent_by, and sent_to_email
# from the audit trail. A transition table forces the workflow to flow
# draft, then ready_for_review, then sent through POST /send, then
# accepted or rejected.
#
# Terminal statuses are accepted and rejected. The sent target is not
# present in any row because sent is only reachable through the
# dedicated /send route.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"draft", "ready_for_review"}),
    # K2 Option A. ready_for_review back to draft is deliberate. A
    # reviewer who spots a typo or wants to tighten a milestone needs
    # to be able to un ready the proposal and edit it. The forward
    # flow arrow in the model docstring describes the typical path,
    # not an irreversible rule. The locked statuses below sent,
    # client_sent, accepted, and rejected are the ones that freeze
    # the content.
    "ready_for_review": frozenset({"draft", "ready_for_review"}),
    # sent is reached only through the dedicated /send route which
    # records the internal handoff to Sales. client_sent is reached
    # only through the dedicated /mark-client-sent route. PATCH
    # cannot reach either status. Accepted and rejected close the
    # loop after either send variant. See
    # docs/sales-ops/PROPOSAL_HANDOFF_AND_SEND_POLICY.md.
    "sent": frozenset({"client_sent", "accepted", "rejected"}),
    "client_sent": frozenset({"accepted", "rejected"}),
    "accepted": frozenset(),
    "rejected": frozenset(),
}


def _is_valid_transition(current: str, target: str) -> bool:
    """Return True when target is an allowed next status for current.

    Unknown current values resolve to an empty allow set so a future
    status added to the enum without an entry here fails closed rather
    than silently permitting any transition.
    """
    return target in _ALLOWED_TRANSITIONS.get(current, frozenset())


# Fixed error string for the I4 transition violation. Wording is fixed
# and free of any echoed input values so an attacker cannot use this
# error path to reflect arbitrary content.
_INVALID_TRANSITION_DETAIL = "Invalid status transition"


# L2. Fixed error string for the explicit sent guard. PATCH must not
# reach status sent because only POST /send stamps sent_at, sent_by,
# and sent_to_email. The earlier code relied on the transition table
# alone, but the spec asks for a guard that does not depend on the
# table being correct. The dedicated constant keeps the wording stable.
_PATCH_SENT_FORBIDDEN_DETAIL = "PATCH cannot set status to sent. Use POST /send."


# Statuses that lock the proposal against destructive operations:
# regenerate, content edit, and SOW swap. ``sent`` and
# ``client_sent`` are included so an audit-trail row cannot be
# quietly rewritten after either the internal handoff or the
# client-facing send. ``accepted`` and ``rejected`` are included
# because they record a real client response that a regenerate
# would silently drop on the floor.
_LOCKED_STATUSES = frozenset({"sent", "client_sent", "accepted", "rejected"})

# Fields PATCH refuses to edit once a proposal is in a locked status.
# The status field itself stays editable so flows like sent → accepted
# can still close the loop; the content fields are the destructive ones.
_CONTENT_FIELDS = ("generated_content", "generated_summary", "title", "notes", "google_doc_url", "google_doc_id")


# L4. Hostname allowlist for stored google_doc_url values.
#
# Why this exists. The download path was already hardened from a server
# side 302 to a JSON response so the application origin can never be
# used as a hop, but the stored URL is still whatever the client wrote
# through create or PATCH. HttpUrl only checks syntax and accepts any
# scheme plus host. A hostile URL can pass HttpUrl, sit in storage,
# and still ship to other users when the doc is fetched on the client.
# The allowlist closes that gap by rejecting non allowlisted hostnames
# at every write path.
#
# Configuration. The constant default carries the Google Docs hostname
# only because the artifact convention today is one Google Doc per
# proposal. Operators can extend the set through the
# PROPOSAL_DOC_URL_ALLOWED_HOSTS env var which is read once at module
# import. The expected format is a comma separated list of bare
# hostnames in lower case. Empty entries are dropped.
_DEFAULT_DOC_URL_HOSTS = frozenset({"docs.google.com"})


def _load_allowed_doc_hosts() -> frozenset[str]:
    """Resolve the proposal document hostname allowlist.

    Reads PROPOSAL_DOC_URL_ALLOWED_HOSTS from os.environ. When unset or
    empty the default Google Docs hostname is returned so the route is
    safe by default. Hostnames are lower cased to match the urlparse
    output for case folding consistency. Comma is the only separator.
    """
    raw = os.environ.get("PROPOSAL_DOC_URL_ALLOWED_HOSTS", "")
    extras = {
        part.strip().lower()
        for part in raw.split(",")
        if part.strip()
    }
    if not extras:
        return _DEFAULT_DOC_URL_HOSTS
    return frozenset(_DEFAULT_DOC_URL_HOSTS | extras)


_ALLOWED_DOC_URL_HOSTS = _load_allowed_doc_hosts()


# Fixed error string for the L4 hostname violation. Free of any echoed
# input value so a hostile caller cannot reflect arbitrary content
# back through the error path.
_DISALLOWED_DOC_HOST_DETAIL = "google_doc_url hostname is not on the allowlist."


def _assert_allowed_doc_url(value: Any) -> None:
    """Validate that a google_doc_url hostname is on the allowlist.

    None and empty string pass through so optional clearing of the
    field still works. A value that does not parse as a URL with a
    hostname is rejected. A value whose lower cased hostname is not in
    _ALLOWED_DOC_URL_HOSTS is rejected with a fixed 400. The check is
    deliberately stricter than HttpUrl which only validates syntax.
    """
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if not host or host not in _ALLOWED_DOC_URL_HOSTS:
        raise HTTPException(
            status_code=400,
            detail=_DISALLOWED_DOC_HOST_DETAIL,
        )


register_module_config("sales_ops", "proposals", "proposal_id", "Sales Ops")
register_module_events("sales_ops", "proposal", ["created", "updated", "sent", "responded"])
register_fields("sales_ops", "proposal", [
    {
        "name": "status",
        "type": "string",
        "label": "Proposal Status",
        "options": PROPOSAL_STATUSES,
        "description": "Proposal lifecycle status: draft → ready_for_review → sent → accepted | rejected.",
    },
    {
        "name": "version",
        "type": "number",
        "label": "Version",
        "description": "Monotonically increasing revision number for the proposal artifact.",
    },
    {
        "name": "google_doc_url",
        "type": "string",
        "label": "Google Doc URL",
        "description": "URL of the authored proposal artifact. Sales Ops authors in Google Docs; the URL is pasted in via PATCH.",
    },
    {
        "name": "sent_to_email",
        "type": "string",
        "label": "Sent To",
        "description": "Recipient email captured at /send time. Audit trail only in the MVP — no email is dispatched.",
    },
    {
        "name": "generated_content",
        "type": "string",
        "label": "Generated Content",
        "description": "Markdown draft built from the source SOWs. Editable in-app and used as the previewable artifact when no Google Doc is attached.",
    },
    {
        "name": "generated_summary",
        "type": "string",
        "label": "Generated Summary",
        "description": "One-paragraph summary suitable for kanban / email preview rendering.",
    },
    {
        "name": "artifact_type",
        "type": "string",
        "label": "Artifact Type",
        "options": [
            {"value": "inline_content", "label": "Inline Content"},
            {"value": "google_doc", "label": "Google Doc"},
        ],
        "description": "Tags the primary view: inline_content when reading the generated draft, google_doc when a doc URL is attached.",
    },
])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _require_opportunity(db, opportunity_id: str) -> dict:
    """Fetch and existence-check the parent opportunity."""
    opp = await db.opportunities.find_one(
        {"opportunity_id": opportunity_id}, {"_id": 0}
    )
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return opp


async def _resolve_eligible_sows(
    db, opportunity_id: str, requested_sow_ids: list[str]
) -> list[dict]:
    """Validate that each requested SOW belongs to the opportunity AND
    carries a status that's finished enough to ship.

    Returns the SOW documents (without ``_id``) for downstream linking.
    Raises 400 if any SOW is missing, mis-attributed, or not yet at a
    ready/complete status.
    """
    if not requested_sow_ids:
        raise HTTPException(
            status_code=400,
            detail="At least one sow_id is required to create a proposal.",
        )

    sows = await db.opportunity_sows.find(
        {"sow_id": {"$in": requested_sow_ids}}, {"_id": 0}
    ).to_list(length=len(requested_sow_ids) + 1)

    by_id = {s.get("sow_id"): s for s in sows}
    missing = [sid for sid in requested_sow_ids if sid not in by_id]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"sow_id(s) not found: {sorted(missing)}",
        )

    wrong_opp = [
        sid
        for sid in requested_sow_ids
        if by_id[sid].get("opportunity_id") != opportunity_id
    ]
    if wrong_opp:
        raise HTTPException(
            status_code=400,
            detail=f"sow_id(s) do not belong to this opportunity: {sorted(wrong_opp)}",
        )

    not_ready = [
        sid
        for sid in requested_sow_ids
        if by_id[sid].get("status") not in _SOW_READY_STATUSES
    ]
    if not_ready:
        raise HTTPException(
            status_code=400,
            detail=(
                "Proposal can only be created from SOWs whose status is "
                f"one of {sorted(_SOW_READY_STATUSES)}. Not ready: "
                f"{sorted(not_ready)}"
            ),
        )

    return [by_id[sid] for sid in requested_sow_ids]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_artifact_type(google_doc_url: Any) -> str:
    """Return ``google_doc`` when a doc URL is attached and
    ``inline_content`` otherwise. Centralised so create, regenerate, and
    PATCH agree. The argument is typed as Any because the URL may arrive
    as an HttpUrl object from a Pydantic body or as a plain string when
    read back from storage. Falsy values map to inline content.
    """
    return "google_doc" if google_doc_url else "inline_content"


def _stringify_doc_url(value: Any) -> Any:
    """Coerce a possible HttpUrl to a plain string for storage.

    Pydantic v2 returns HttpUrl as a Url object in the default Python
    serialisation mode. Mongo cannot store Url objects so anywhere a
    proposal body or update reaches storage we cast to str. None passes
    through so optional clearing of the field still works.
    """
    if value is None:
        return None
    return str(value)


# ---------------------------------------------------------------------------
# Version-history snapshot
# ---------------------------------------------------------------------------
#
# db.proposal_versions is an append-only collection. A row is written
# before any path that overwrites a proposal's content fields. See
# docs/sales-ops/PROPOSAL_LIFECYCLE_V2.md for the canonical row shape
# and the reasons enum.


_SNAPSHOT_REASONS = frozenset({"edited", "regenerated", "drift_fallback"})


async def _snapshot_proposal_version(
    db,
    proposal: dict,
    reason: str,
    actor_id: str | None,
    metadata: dict | None = None,
) -> None:
    """Append a row to db.proposal_versions before a destructive
    write to the parent proposal row.

    The snapshot captures the proposal as it is at the moment of
    overwrite (the value of generated_content, generated_summary,
    title, google_doc_url, generation_source_hash, and version).
    Failures are swallowed and logged so a version-store outage
    does not break the user's edit or regenerate action; the
    audit row still writes from the route, so the activity feed
    captures the intent even when the snapshot fails.
    """
    if not isinstance(proposal, dict):
        return
    if reason not in _SNAPSHOT_REASONS:
        # Defensive: an unknown reason would let a future caller
        # write rows with values outside the documented enum. Reject
        # silently here rather than at the route layer; the route
        # always passes a literal.
        return

    snapshot = {
        "version_id": f"pversion_{_uuid.uuid4().hex[:12]}",
        "proposal_id": proposal.get("proposal_id"),
        "opportunity_id": proposal.get("opportunity_id"),
        "title": proposal.get("title"),
        "content": proposal.get("generated_content"),
        "generated_summary": proposal.get("generated_summary"),
        "google_doc_url": proposal.get("google_doc_url"),
        "generation_source_hash": proposal.get("generation_source_hash"),
        "version": int(proposal.get("version") or 1),
        "reason": reason,
        "created_at": _now(),
        "created_by": actor_id,
        "metadata": metadata or {},
    }
    try:
        await db.proposal_versions.insert_one(snapshot)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Failed to snapshot proposal_versions for %s (reason=%s): %s",
            proposal.get("proposal_id"),
            reason,
            e,
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/opportunities/{opportunity_id}/proposal",
    response_model=Proposal,
    dependencies=[Depends(require_ai_permission("sales_ops.proposal.create"))],
)
async def create_proposal(
    opportunity_id: str,
    body: ProposalCreate,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
) -> Proposal:
    """Create a draft proposal anchored to one or more eligible SOWs.

    V1 behaviour:
      * Validates opportunity + SOW eligibility (same gate as before).
      * Builds deterministic ``generated_content`` and
        ``generated_summary`` from the SOW(s) via
        ``services.proposal_generation`` — never mutates the SOWs.
      * Stamps ``generated_at`` / ``generated_by`` and a content hash
        so regenerate can detect a no-op refresh.
      * If the caller pre-attached a Google Doc URL we mark
        ``artifact_type="google_doc"``; otherwise the inline content is
        the primary preview and ``artifact_type="inline_content"``.
        Either way the response carries everything the UI needs to
        render — no need to round-trip back to the server.
      * The caller can still override ``title``; if omitted, the helper
        synthesises one from the opportunity name + LOB mix.
    """
    if not has_perm(current_user, "sales_ops.proposal.create"):
        raise HTTPException(status_code=403, detail="sales_ops.proposal.create permission required")

    # L4. Reject a non allowlisted hostname before any DB write.
    _assert_allowed_doc_url(body.google_doc_url)

    opp = await _require_opportunity(db, opportunity_id)
    sows = await _resolve_eligible_sows(db, opportunity_id, body.sow_ids)

    generated = generate_proposal_content(opp, sows)

    now = _now()
    user_id = getattr(current_user, "user_id", None)
    # Cast HttpUrl to str so the stored Proposal record stays JSON safe.
    # Pydantic accepts a str on the Optional[HttpUrl] field and will
    # re validate it through the same URL parser.
    stored_doc_url = _stringify_doc_url(body.google_doc_url)
    proposal = Proposal(
        proposal_id=f"prop_{gen_id()}",
        opportunity_id=opportunity_id,
        sow_ids=list(body.sow_ids),
        status="draft",
        title=body.title or generated["title"],
        notes=body.notes,
        google_doc_url=stored_doc_url,
        google_doc_id=body.google_doc_id,
        artifact_type=_resolve_artifact_type(stored_doc_url),
        generated_content=generated["generated_content"],
        generated_summary=generated["generated_summary"],
        generated_at=now,
        generated_by=user_id,
        generation_source=generated["generation_source"],
        generation_source_hash=generated["generation_source_hash"],
        created_at=now,
        updated_at=now,
        created_by=user_id,
        last_edited_by=user_id,
    )

    # Persist with the URL as a plain string so the Mongo record matches
    # the wire shape on read back. model_dump in Python mode would emit
    # a Url object which Motor cannot serialise.
    persisted = proposal.model_dump()
    persisted["google_doc_url"] = _stringify_doc_url(persisted.get("google_doc_url"))
    await db.proposals.insert_one(persisted)
    logger.info(
        "Proposal %s created on opportunity %s by %s artifact=%s",
        proposal.proposal_id,
        opportunity_id,
        getattr(current_user, "email", "unknown"),
        proposal.artifact_type,
    )

    # Audit. Creation is the first row in the proposal activity feed
    # and the only place a fresh generation_source_hash is recorded
    # without a prior version to compare against. The audit details
    # avoid free-text body content; only structured metadata lands
    # in the row.
    await audit_log_action(
        action="sales_ops.proposal.created",
        actor_id=user_id,
        actor_email=getattr(current_user, "email", "unknown"),
        resource_type="proposal",
        resource_id=proposal.proposal_id,
        details={
            "opportunity_id": opportunity_id,
            "version": proposal.version,
            "sow_ids_count": len(proposal.sow_ids),
            "artifact_type": proposal.artifact_type,
            "generation_source_hash": proposal.generation_source_hash,
        },
    )
    return proposal


@router.get(
    "/opportunities/{opportunity_id}/proposals",
    dependencies=[Depends(require_ai_permission("sales_ops.proposal.view"))],
)
async def list_proposals_for_opportunity(
    opportunity_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    """List every proposal attached to an opportunity, newest first.

    The response wraps the list in ``{"items": [...]}`` so the shape
    matches the rest of the AEGISAXIOM REST surface (frontend lists already
    iterate over ``data.items``).
    """
    if not has_perm(current_user, "sales_ops.proposal.view"):
        raise HTTPException(status_code=403, detail="sales_ops.proposal.view permission required")

    await _require_opportunity(db, opportunity_id)

    items = (
        await db.proposals.find({"opportunity_id": opportunity_id}, {"_id": 0})
        .sort("created_at", -1)
        .to_list(length=200)
    )
    return {"opportunity_id": opportunity_id, "items": items}


@router.get(
    "/proposals/{proposal_id}",
    dependencies=[Depends(require_ai_permission("sales_ops.proposal.view"))],
)
async def get_proposal(
    proposal_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    if not has_perm(current_user, "sales_ops.proposal.view"):
        raise HTTPException(status_code=403, detail="sales_ops.proposal.view permission required")

    doc = await db.proposals.find_one({"proposal_id": proposal_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return doc


@router.get(
    "/proposals/{proposal_id}/pdf",
    dependencies=[Depends(require_ai_permission("sales_ops.proposal.view"))],
)
async def download_proposal_pdf(
    proposal_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Render the proposal to a PDF using its existing generated
    content + linked SOWs + parent opportunity. Read-only: no Drive
    write, no email send, no QBO sync.

    Returns the PDF inline with content-type ``application/pdf`` and
    a Content-Disposition that the browser uses to download. Cross-
    tenant access returns 404 (never 403) so existence is not
    leaked. Permission is the same ``sales_ops.proposal.view`` gate
    the GET /proposals/{id} route already uses, so anyone allowed to
    view the proposal is allowed to download its PDF.
    """
    if not has_perm(current_user, "sales_ops.proposal.view"):
        raise HTTPException(status_code=403, detail="sales_ops.proposal.view permission required")

    proposal = await db.proposals.find_one({"proposal_id": proposal_id}, {"_id": 0})
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    # Tenant scope: when both caller and document carry tenant_id and
    # they differ, return 404 (not 403) to avoid leaking existence.
    user_tenant = getattr(current_user, "tenant_id", None)
    if user_tenant and proposal.get("tenant_id") and proposal["tenant_id"] != user_tenant:
        raise HTTPException(status_code=404, detail="Proposal not found")

    opportunity = None
    if proposal.get("opportunity_id"):
        opportunity = await db.opportunities.find_one(
            {"opportunity_id": proposal["opportunity_id"]}, {"_id": 0},
        )

    sow_ids = list(proposal.get("sow_ids") or [])
    sows: list[dict] = []
    if sow_ids:
        cursor = db.opportunity_sows.find({"sow_id": {"$in": sow_ids}}, {"_id": 0})
        fetched = await cursor.to_list(length=len(sow_ids) + 1)
        # Preserve the order that the proposal stores so the PDF
        # mirrors what the user sees in the SOW Builder list.
        by_id = {s.get("sow_id"): s for s in fetched if isinstance(s, dict)}
        sows = [by_id[sid] for sid in sow_ids if sid in by_id]

    from services.proposal_pdf import proposal_pdf_filename, render_proposal_pdf

    try:
        pdf_bytes = render_proposal_pdf(proposal, opportunity=opportunity, sows=sows)
    except RuntimeError as exc:
        # reportlab missing — never bubble the raw exception text to
        # the client; this is a deployment-side gap.
        logger.error("Proposal PDF render failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="PDF rendering is not available on this deployment.",
        )

    filename = proposal_pdf_filename(proposal, opportunity=opportunity)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


class ProposalSendEmailRequest(BaseModel):
    """Customer-facing proposal email send. Distinct from the
    internal handoff body (``ProposalSendRequest``) so the two
    surfaces cannot collide as the schemas evolve."""

    recipients: list[EmailStr] = Field(..., min_length=1, max_length=10)
    subject: Optional[str] = None
    body: Optional[str] = None
    attach_pdf: bool = True


@router.post(
    "/proposals/{proposal_id}/send-email",
    dependencies=[Depends(require_ai_permission("sales_ops.proposal.send"))],
)
async def send_proposal_email_route(
    proposal_id: str,
    body: ProposalSendEmailRequest,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    """Customer-facing proposal email send.

    Composes an HTML email using the proposal's existing
    ``generated_summary`` / ``generated_content`` (or the caller's
    optional ``body`` override), attaches the proposal PDF when
    ``attach_pdf=True`` (default), and dispatches via Resend through
    ``services.email_service.send_with_attachments``.

    Does NOT replace the internal handoff at
    ``POST /proposals/{id}/send``. That route keeps recording the
    Sales handoff and stamping ``sent_at`` / ``sent_by``. This route
    stamps a separate ``email_*`` set so the audit trail tells the
    two channels apart.

    Surfaces:
      - 200 ``{"status": "sent", ...}`` on successful Resend send;
        proposal carries ``email_sent_at`` / ``email_sent_by`` /
        ``email_recipients`` / ``email_subject`` / ``email_status="sent"``.
      - 503 ``{"code": "email_not_configured"}`` when no Resend key
        is configured; proposal carries ``email_status="not_configured"``
        but no sent metadata.
      - 502 with sanitized envelope when Resend rejects the send;
        proposal carries ``email_status="failed"`` and a sanitized
        ``email_error``.

    Tenant scoping: 404 (never 403) on cross-tenant access.
    """
    if not has_perm(current_user, "sales_ops.proposal.send"):
        raise HTTPException(
            status_code=403,
            detail="sales_ops.proposal.send permission required",
        )

    proposal = await db.proposals.find_one(
        {"proposal_id": proposal_id}, {"_id": 0},
    )
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    user_tenant = getattr(current_user, "tenant_id", None)
    if (
        user_tenant
        and proposal.get("tenant_id")
        and proposal["tenant_id"] != user_tenant
    ):
        raise HTTPException(status_code=404, detail="Proposal not found")

    # Status guard. Mirror the internal POST /proposals/{id}/send route:
    # only proposals that have explicitly reached ready_for_review may
    # leave the building. Draft / rejected / sent / accepted all 409
    # with the same fixed detail the PATCH transition guard uses. This
    # runs before SOW load, PDF render, email send, email_* metadata
    # writes, and audit rows so an invalid status leaves no side effects.
    current_status = proposal.get("status")
    if current_status != "ready_for_review":
        raise HTTPException(
            status_code=409,
            detail=_INVALID_TRANSITION_DETAIL,
        )

    opportunity = None
    if proposal.get("opportunity_id"):
        opportunity = await db.opportunities.find_one(
            {"opportunity_id": proposal["opportunity_id"]}, {"_id": 0},
        )

    sow_ids = list(proposal.get("sow_ids") or [])
    sows: list[dict] = []
    if sow_ids:
        fetched = await db.opportunity_sows.find(
            {"sow_id": {"$in": sow_ids}}, {"_id": 0},
        ).to_list(length=len(sow_ids) + 1)
        by_id = {s.get("sow_id"): s for s in fetched if isinstance(s, dict)}
        sows = [by_id[sid] for sid in sow_ids if sid in by_id]

    from services.proposal_email import send_proposal_email

    recipients = [str(r) for r in body.recipients]
    result = await send_proposal_email(
        proposal=proposal,
        opportunity=opportunity,
        sows=sows,
        recipients=recipients,
        subject_override=body.subject,
        body_override=body.body,
        attach_pdf=body.attach_pdf,
    )

    now = _now()
    update_set: dict[str, Any] = {
        "email_status": result["status"],
        "updated_at": now,
    }
    audit_details: dict[str, Any] = {
        "email_status": result["status"],
        "recipient_count": len(recipients),
        "attached_pdf": result.get("attached_pdf", False),
    }

    if result["status"] == "sent":
        update_set.update({
            "email_sent_at": now,
            "email_sent_by": current_user.user_id,
            "email_recipients": result.get("recipients", recipients),
            "email_subject": result.get("subject"),
            "email_error": None,
        })
    elif result["status"] == "failed":
        update_set["email_error"] = result.get("error")
    # not_configured: stamp the status only; do not record sent metadata.

    await db.proposals.update_one(
        {"proposal_id": proposal_id}, {"$set": update_set},
    )

    try:
        await audit_log_action(
            f"sales_ops.proposal.email_{result['status']}",
            actor_id=getattr(current_user, "user_id", None),
            actor_email=getattr(current_user, "email", "unknown"),
            resource_type="proposal",
            resource_id=proposal_id,
            details=audit_details,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit log_action failed for proposal.email_*: %s", exc)

    if result["status"] == "not_configured":
        # Do NOT pretend a send succeeded when no Resend key is set.
        # 503 surfaces the deployment gap; the proposal row carries
        # the same "not_configured" marker so audits agree.
        raise HTTPException(
            status_code=503,
            detail={
                "code": "email_not_configured",
                "message": (
                    "Email send is not configured on this deployment. "
                    "Set RESEND_API_KEY (and SENDER_EMAIL) before retrying."
                ),
            },
        )
    if result["status"] == "failed":
        raise HTTPException(
            status_code=502,
            detail={
                "code": "email_send_failed",
                "message": (
                    "The email provider rejected the send. The proposal "
                    "record has been marked email_status=failed; retry "
                    "after triage."
                ),
            },
        )

    refreshed = await db.proposals.find_one(
        {"proposal_id": proposal_id}, {"_id": 0},
    )
    return {
        "status": "sent",
        "proposal": refreshed,
        "email": {
            "recipients": result.get("recipients", recipients),
            "subject": result.get("subject"),
            "attached_pdf": result.get("attached_pdf", False),
        },
    }


@router.patch(
    "/proposals/{proposal_id}",
    dependencies=[Depends(require_ai_permission("sales_ops.proposal.edit"))],
)
async def update_proposal(
    proposal_id: str,
    body: ProposalUpdate,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    """Update draft metadata, edit generated content, attach or detach
    SOWs, or flip status.

    Rules.

    The status field is constrained by a transition table rather than a
    flat allow set. A target status is accepted only when the table
    permits the move from the current stored status. Any other
    combination returns 409 with the fixed message stored in
    _INVALID_TRANSITION_DETAIL. The sent status is unreachable through
    PATCH because POST /send is the only path that stamps sent_at,
    sent_by, and sent_to_email. An explicit early guard rejects
    status equal to sent before the transition table lookup so the
    rule holds even if the table is ever edited incorrectly.

    Once a proposal is in a locked status, namely sent, accepted, or
    rejected, the content fields refuse further edits so the snapshot
    the client received cannot be quietly rewritten. Status transitions
    remain editable so sent then accepted or sent then rejected can
    still close the loop, and client_response_note is always settable
    so the response can be recorded after the fact.

    The artifact_type field is recomputed whenever google_doc_url
    changes because it is derived state and the wire is not trusted to
    set it.
    """
    if not has_perm(current_user, "sales_ops.proposal.edit"):
        raise HTTPException(status_code=403, detail="sales_ops.proposal.edit permission required")

    existing = await db.proposals.find_one({"proposal_id": proposal_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Proposal not found")

    update: dict[str, Any] = {}
    changes = body.model_dump(exclude_unset=True)
    locked = existing.get("status") in _LOCKED_STATUSES

    if "status" in changes:
        new_status = changes["status"]
        # L2. Explicit early guard. PATCH must never reach sent because
        # only POST /send stamps the sent_at, sent_by, and sent_to_email
        # audit fields. Independent of the transition table so a future
        # table edit cannot accidentally open this hole.
        if new_status == "sent":
            raise HTTPException(
                status_code=400,
                detail=_PATCH_SENT_FORBIDDEN_DETAIL,
            )
        current_status = existing.get("status") or "draft"
        # The 409 path covers every invalid transition uniformly so the
        # client surface is consistent. The detail string is fixed and
        # carries no echoed input values.
        if not _is_valid_transition(current_status, new_status):
            raise HTTPException(
                status_code=409,
                detail=_INVALID_TRANSITION_DETAIL,
            )
        update["status"] = new_status

    if "sow_ids" in changes and changes["sow_ids"] is not None:
        if locked:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot swap SOWs on a proposal whose status is "
                    f"{existing.get('status')!r}. Create a new revision instead."
                ),
            )
        await _resolve_eligible_sows(
            db, existing["opportunity_id"], changes["sow_ids"]
        )
        update["sow_ids"] = list(changes["sow_ids"])

    for field in _CONTENT_FIELDS:
        if field in changes:
            if locked:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Cannot edit '{field}' on a proposal whose status is "
                        f"{existing.get('status')!r}. Create a new revision instead."
                    ),
                )
            update[field] = changes[field]

    if "client_response_note" in changes:
        update["client_response_note"] = changes["client_response_note"]
        # Stamp the client-response timestamp the first time a response
        # is captured so the UI doesn't need a separate endpoint.
        if not existing.get("client_response_at"):
            update["client_response_at"] = _now()

    # Coerce any HttpUrl on the inbound payload to a plain string before
    # writing to Mongo. Pydantic gives Url objects in Python mode and
    # Motor will not serialise them. L4 hostname check runs first so a
    # disallowed value never reaches storage even via PATCH.
    google_doc_url_changed = False
    if "google_doc_url" in update:
        _assert_allowed_doc_url(update["google_doc_url"])
        update["google_doc_url"] = _stringify_doc_url(update["google_doc_url"])
        # artifact_type is derived state. Recompute whenever the URL
        # changes so the frontend never sees a stale label.
        update["artifact_type"] = _resolve_artifact_type(update["google_doc_url"])
        google_doc_url_changed = True
        # Parse google_doc_id from the URL automatically when the
        # caller attaches a new external doc. The escape hatch input
        # is a URL string; the doc id is what downstream readers join
        # against. When the URL is being cleared (set to null) the
        # id is cleared in lockstep so a stale id cannot outlive the
        # url. Callers can override by also passing google_doc_id
        # explicitly in the same PATCH body.
        if "google_doc_id" not in update:
            from services.google_docs_service import parse_google_doc_id
            parsed_id = parse_google_doc_id(update["google_doc_url"] or "")
            update["google_doc_id"] = parsed_id

    if not update:
        return existing

    update["updated_at"] = _now()
    update["last_edited_by"] = getattr(current_user, "user_id", None)

    # Snapshot the prior content before overwriting. Only the content
    # fields are version-tracked; status-only transitions skip the
    # snapshot path. content_changed detects whether the PATCH
    # actually touches any of the content fields including title and
    # google_doc_url; a no-content PATCH (status flip + nothing else)
    # writes neither a snapshot nor an edit audit row.
    content_changed = any(field in update for field in _CONTENT_FIELDS)
    if content_changed:
        await _snapshot_proposal_version(
            db,
            existing,
            reason="edited",
            actor_id=getattr(current_user, "user_id", None),
        )

    await db.proposals.update_one({"proposal_id": proposal_id}, {"$set": update})

    fresh = await db.proposals.find_one({"proposal_id": proposal_id}, {"_id": 0})

    # Audit content edits separately from status-only transitions so
    # the activity feed distinguishes a Sales Ops user reshaping the
    # proposal content from a Sales Ops user closing the loop after
    # the client responded. fields_changed records which top-level
    # fields the PATCH touched; free-text values are not echoed.
    if content_changed:
        await audit_log_action(
            action="sales_ops.proposal.edited",
            actor_id=getattr(current_user, "user_id", None),
            actor_email=getattr(current_user, "email", "unknown"),
            resource_type="proposal",
            resource_id=proposal_id,
            details={
                "opportunity_id": existing.get("opportunity_id"),
                "version": existing.get("version"),
                "fields_changed": sorted(
                    [f for f in update.keys() if f in _CONTENT_FIELDS]
                ),
            },
        )

    # Separate audit for Google Doc link / unlink transitions on the
    # external-doc escape hatch. The edit audit above already records
    # that google_doc_url appeared in fields_changed; this row gives
    # the activity feed a stable action name that read paths can
    # filter for without parsing the fields_changed list. See
    # docs/sales-ops/GOOGLE_DOC_ARTIFACT_PATH.md.
    if google_doc_url_changed:
        prior_url = (existing.get("google_doc_url") or "").strip()
        new_url = (update.get("google_doc_url") or "").strip() if update.get("google_doc_url") else ""
        if new_url and new_url != prior_url:
            await audit_log_action(
                action="sales_ops.proposal.google_doc_linked",
                actor_id=getattr(current_user, "user_id", None),
                actor_email=getattr(current_user, "email", "unknown"),
                resource_type="proposal",
                resource_id=proposal_id,
                details={
                    "opportunity_id": existing.get("opportunity_id"),
                    "version": existing.get("version"),
                    "has_google_doc_id": bool(update.get("google_doc_id")),
                },
            )
        elif prior_url and not new_url:
            await audit_log_action(
                action="sales_ops.proposal.google_doc_unlinked",
                actor_id=getattr(current_user, "user_id", None),
                actor_email=getattr(current_user, "email", "unknown"),
                resource_type="proposal",
                resource_id=proposal_id,
                details={
                    "opportunity_id": existing.get("opportunity_id"),
                    "version": existing.get("version"),
                },
            )
    return fresh


# Regenerate reuses sales_ops.proposal.edit on both the human gate and
# the AI dependency. There is no separate regenerate permission key on
# this codebase. The handler also accepts sales_ops.proposal.create as
# an alternate human grant, but the AI dependency takes the primary key
# so a single permission registration on the AI side covers the route.
@router.post(
    "/proposals/{proposal_id}/regenerate",
    dependencies=[Depends(require_ai_permission("sales_ops.proposal.edit"))],
)
async def regenerate_proposal(
    proposal_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    """Rebuild a proposal's content from the latest SOW data.

    The caller needs ``sales_ops.proposal.edit`` OR
    ``sales_ops.proposal.create`` — regeneration is a content rebuild
    rather than a status transition, so either role qualifies. Locked
    statuses (sent / accepted / rejected) reject with 409: the client
    has already received that snapshot and the regenerate would
    silently destroy the record. Sales Ops should branch a new
    revision instead.

    Side-effects on success:
      * ``generated_content`` / ``generated_summary`` are rewritten
        from the latest SOW state.
      * ``version`` is bumped.
      * ``regenerated_at`` / ``regenerated_by`` are stamped.
      * If the SOWs originally attached are no longer eligible (e.g.
        a SOW was moved out of ready/complete after the draft was
        created), regeneration fails with 400 + detail naming the
        offending sow_id — same gate as create.
    """
    if not (
        has_perm(current_user, "sales_ops.proposal.edit")
        or has_perm(current_user, "sales_ops.proposal.create")
    ):
        raise HTTPException(
            status_code=403,
            detail="sales_ops.proposal.edit or sales_ops.proposal.create permission required",
        )

    existing = await db.proposals.find_one({"proposal_id": proposal_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Proposal not found")

    status = existing.get("status")
    if status in _LOCKED_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot regenerate a proposal whose status is {status!r}. "
                "Create a new revision instead."
            ),
        )

    opp = await _require_opportunity(db, existing["opportunity_id"])
    sows = await _resolve_eligible_sows(db, existing["opportunity_id"], existing.get("sow_ids") or [])
    generated = generate_proposal_content(opp, sows)

    # Idempotency. Compute the new generation_source_hash from the
    # current SOW state; if it matches the proposal's stored hash,
    # the deterministic output has not changed and the regenerate is
    # a no-op. The audit row still writes so the activity feed
    # records the user's intent. See
    # docs/sales-ops/PROPOSAL_LIFECYCLE_V2.md.
    new_hash = generated.get("generation_source_hash")
    prior_hash = existing.get("generation_source_hash")
    if new_hash is not None and new_hash == prior_hash:
        await audit_log_action(
            action="sales_ops.proposal.regenerated",
            actor_id=getattr(current_user, "user_id", None),
            actor_email=getattr(current_user, "email", "unknown"),
            resource_type="proposal",
            resource_id=proposal_id,
            details={
                "opportunity_id": existing.get("opportunity_id"),
                "previous_version": existing.get("version"),
                "new_version": existing.get("version"),
                "no_op": True,
                "generation_source_hash": new_hash,
                "drift_detected": False,
                "fallback_used": False,
            },
        )
        return {
            **existing,
            "no_op": True,
            "reason": "source_unchanged",
            "drift_detected": False,
            "fallback_used": False,
        }

    # Drift guard wiring. The polish callable is None today because
    # no AI polish step is integrated; the guard returns the
    # deterministic skeleton unchanged and the rest of the flow
    # proceeds as a pure deterministic regenerate. When a future
    # ticket adds a polish callable, the guard automatically runs
    # the numeric drift check against the skeleton and falls back
    # to the skeleton on drift. See
    # backend/services/proposal_synthesis.py.
    polish_result = apply_polish_with_drift_guard(
        skeleton=generated["generated_content"],
        polish=None,
    )

    now = _now()
    user_id = getattr(current_user, "user_id", None)
    update = {
        "generated_content": polish_result.content,
        "generated_summary": generated["generated_summary"],
        "generation_source": generated["generation_source"],
        "generation_source_hash": new_hash,
        "regenerated_at": now,
        "regenerated_by": user_id,
        "updated_at": now,
        "last_edited_by": user_id,
        # Refresh artifact_type — if Sales Ops removed the Google Doc
        # URL between drafts the regenerated copy should fall back to
        # the inline-content view rather than advertise a stale doc.
        "artifact_type": _resolve_artifact_type(existing.get("google_doc_url")),
        "version": int(existing.get("version") or 1) + 1,
    }
    # Only overwrite the title if the user never customised it. The
    # initial create stored the generator's auto-title verbatim, so a
    # title that still matches the latest auto-title is treated as
    # "user did not customise" and refreshed. Anything else is kept.
    if not existing.get("title") or existing.get("title") == generated["title"]:
        update["title"] = generated["title"]

    # Snapshot the prior content before the overwrite. The reason
    # is regenerated for a clean rewrite, drift_fallback when the
    # polish step produced drift and the guard fell back. Either
    # way the previous version is recoverable.
    snapshot_reason = "drift_fallback" if polish_result.drift_detected else "regenerated"
    snapshot_metadata = (
        {"drift_details": polish_result.drift_details}
        if polish_result.drift_detected and polish_result.drift_details
        else None
    )
    await _snapshot_proposal_version(
        db,
        existing,
        reason=snapshot_reason,
        actor_id=user_id,
        metadata=snapshot_metadata,
    )

    await db.proposals.update_one({"proposal_id": proposal_id}, {"$set": update})
    fresh = await db.proposals.find_one({"proposal_id": proposal_id}, {"_id": 0})
    logger.info(
        "Proposal %s regenerated by %s version=%s drift=%s",
        proposal_id,
        getattr(current_user, "email", "unknown"),
        update["version"],
        polish_result.drift_detected,
    )

    # Audit. The action name uses the canonical
    # sales_ops.proposal.regenerated namespace which aligns with the
    # other Sales Ops proposal audits. log_action carries its own
    # try/except so an audit-side outage never breaks the user's
    # regenerate. drift_detected and fallback_used in the details
    # let a downstream consumer filter for drift events without
    # needing a separate query.
    await audit_log_action(
        action="sales_ops.proposal.regenerated",
        actor_id=getattr(current_user, "user_id", None),
        actor_email=getattr(current_user, "email", "unknown"),
        resource_type="proposal",
        resource_id=proposal_id,
        details={
            "opportunity_id": existing.get("opportunity_id"),
            "previous_version": existing.get("version"),
            "new_version": update["version"],
            "no_op": False,
            "generation_source_hash": new_hash,
            "drift_detected": polish_result.drift_detected,
            "fallback_used": polish_result.fallback_used,
        },
    )

    # Separate audit row when the drift guard fired. The polished
    # output was rejected and the skeleton was kept; the drift
    # details live on the snapshot row's metadata field.
    if polish_result.drift_detected:
        drift_summary = {}
        if polish_result.drift_details:
            drift_summary = {
                "added_count": len(polish_result.drift_details.get("added", [])),
                "removed_count": len(polish_result.drift_details.get("removed", [])),
                "changed_count": len(polish_result.drift_details.get("changed", [])),
            }
        await audit_log_action(
            action="sales_ops.proposal.numeric_drift_detected",
            actor_id=user_id,
            actor_email=getattr(current_user, "email", "unknown"),
            resource_type="proposal",
            resource_id=proposal_id,
            details={
                "opportunity_id": existing.get("opportunity_id"),
                "version": update["version"],
                "drift_summary": drift_summary,
            },
        )

    return {
        **fresh,
        "no_op": False,
        "drift_detected": polish_result.drift_detected,
        "fallback_used": polish_result.fallback_used,
    }


_HANDOFF_NOTIFICATION_TITLE = "Proposal handed off to Sales"
_HANDOFF_NOTIFICATION_BODY = (
    "Sales Ops handed off the proposal. Sales now owns the "
    "client-facing send and the client relationship."
)

# Opportunity stages eligible for the proposal_sent advance when the
# Sales owner marks the proposal client-sent. Any other current
# stage is left untouched. See
# docs/sales-ops/PROPOSAL_HANDOFF_AND_SEND_POLICY.md.
_CLIENT_SENT_STAGE_ADVANCE_FROM = frozenset({"proposal_development", "scoping_complete"})


async def _resolve_handoff_recipients(db, opportunity_id: str | None) -> list[str]:
    """Return the internal Sales user ids to notify on handoff.

    Resolves the opportunity owner and the assigned Sales Ops user.
    Returns a deduped list. Empty when both fields are unset; an
    empty notification fan-out still permits the audit row to write.
    Tenancy is implicit because the lookup is keyed by opportunity_id
    and the opportunity itself is tenant scoped at write time.
    """
    if not opportunity_id:
        return []
    opp = await db.opportunities.find_one(
        {"opportunity_id": opportunity_id},
        {"_id": 0, "opportunity_owner_id": 1, "assigned_sales_ops_id": 1},
    )
    if not opp:
        return []
    seen: set[str] = set()
    recipients: list[str] = []
    for key in ("opportunity_owner_id", "assigned_sales_ops_id"):
        uid = opp.get(key)
        if uid and uid not in seen:
            seen.add(uid)
            recipients.append(uid)
    return recipients


async def _write_handoff_notifications(
    db,
    recipients: list[str],
    proposal_id: str,
    opportunity_id: str | None,
) -> None:
    """Create one notification row per internal Sales recipient.

    Notification text never implies a client received anything. The
    record_id points to the proposal so the deep link in the
    notification dropdown opens the right record. Failures are
    logged and swallowed because the handoff itself must not be
    blocked by a notification write outage.
    """
    if not recipients:
        return
    import uuid as _uuid

    now = _now()
    for uid in recipients:
        try:
            await db.notifications.insert_one(
                {
                    "notification_id": f"notif_{_uuid.uuid4().hex[:12]}",
                    "user_id": uid,
                    "title": _HANDOFF_NOTIFICATION_TITLE,
                    "body": _HANDOFF_NOTIFICATION_BODY,
                    "module": "sales_ops",
                    "record_id": proposal_id,
                    "opportunity_id": opportunity_id,
                    "read": False,
                    "created_at": now,
                }
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Proposal %s handoff notification to %s failed: %s",
                proposal_id,
                uid,
                e,
            )


@router.post(
    "/proposals/{proposal_id}/send",
    dependencies=[Depends(require_ai_permission("sales_ops.proposal.send"))],
)
async def send_proposal(
    proposal_id: str,
    body: ProposalSendRequest,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    """Hand off the proposal to Sales. Internal handoff only.

    Sales Ops prepares the SOW and proposal package. This route
    records that the package is ready and the Sales owner now owns
    the client-facing delivery. AEGISAXIOM does not email anyone. The
    handler flips status to sent, stamps sent_at, sent_by, and
    sent_to_email for the Sales recipient, writes an audit row with
    action sales_ops.proposal.handed_off_to_sales, and creates
    internal notifications addressed to the opportunity owner and
    the assigned Sales Ops user. Re-handing-off an already handed-off
    proposal is rejected. Create a new revision (version+1) for a
    re-handoff.

    sent_to_email captures the Sales recipient. The audit row records
    only a boolean has_recipient so the address itself does not reach
    log aggregators.

    The parent opportunity stage is not advanced by this route. The
    Sales-owned mark-client-sent action advances stage to
    proposal_sent when eligible. See
    docs/sales-ops/PROPOSAL_HANDOFF_AND_SEND_POLICY.md.

    Response shape mirrors the persisted proposal doc and adds
    policy-aligned helper fields. handoff_only is True, client_sent
    is False, stage_advanced is False, handoff_to_sales_at and
    handoff_by mirror sent_at and sent_by, and handoff_recipients
    is the user id list that received the internal notification.
    """
    if not has_perm(current_user, "sales_ops.proposal.send"):
        raise HTTPException(status_code=403, detail="sales_ops.proposal.send permission required")

    existing = await db.proposals.find_one({"proposal_id": proposal_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Proposal not found")

    # K1. Single transition guard covers every invalid current status
    # uniformly. The earlier code had two guards. An older check for
    # current_status equals sent that raised 400, and the J5 transition
    # guard that raises 409 for everything else. That meant draft to
    # sent and sent to sent returned different status codes for the
    # same category of invalid transition. The send route now mirrors
    # the PATCH route. Only ready_for_review is a valid current status
    # for the transition to sent. Any other current status, including
    # sent, draft, accepted, and rejected, returns 409 with the same
    # fixed detail string the PATCH transition guard uses.
    current_status = existing.get("status")
    if current_status != "ready_for_review":
        raise HTTPException(
            status_code=409,
            detail=_INVALID_TRANSITION_DETAIL,
        )

    # The Sales owner needs something to hand off. Refuse to mark
    # handed off when there is neither an attached Google Doc URL
    # nor any generated content. Otherwise the handoff records a
    # state that is not real.
    has_doc = bool(existing.get("google_doc_url"))
    has_inline = bool((existing.get("generated_content") or "").strip())
    if not (has_doc or has_inline):
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot hand off — proposal has no Google Doc URL and no "
                "generated content. Generate content or attach a doc URL first."
            ),
        )

    # L4. Validate the stored google_doc_url against the hostname
    # allowlist at the handoff write path so a legacy or pre allowlist
    # write cannot ride through into the audit trail. None passes
    # through because inline content alone is a valid artifact path.
    _assert_allowed_doc_url(existing.get("google_doc_url"))

    now = _now()
    sent_to_email = str(body.sent_to_email) if body.sent_to_email else None
    update = {
        "status": "sent",
        "sent_at": now,
        "sent_by": getattr(current_user, "user_id", None),
        "sent_to_email": sent_to_email,
        "updated_at": now,
        "last_edited_by": getattr(current_user, "user_id", None),
    }
    if body.notes is not None:
        update["notes"] = body.notes

    await db.proposals.update_one({"proposal_id": proposal_id}, {"$set": update})
    # L3. Do not log the recipient email value. The boolean flag tells
    # operators whether a recipient was captured without leaking the
    # address itself into log aggregators.
    logger.info(
        "Proposal %s handed off to Sales by %s has_recipient=%s",
        proposal_id,
        getattr(current_user, "email", "unknown"),
        sent_to_email is not None,
    )

    opportunity_id = existing.get("opportunity_id")
    recipients = await _resolve_handoff_recipients(db, opportunity_id)
    await _write_handoff_notifications(db, recipients, proposal_id, opportunity_id)

    # Audit. The internal handoff to Sales is the moment that matters
    # for the paper trail. has_recipient is a boolean so the email
    # value itself never reaches log aggregators or audit consumers.
    # log_action carries its own try/except so a failure here never
    # breaks the user's handoff action.
    await audit_log_action(
        action="sales_ops.proposal.handed_off_to_sales",
        actor_id=getattr(current_user, "user_id", None),
        actor_email=getattr(current_user, "email", "unknown"),
        resource_type="proposal",
        resource_id=proposal_id,
        details={
            "opportunity_id": opportunity_id,
            "has_recipient": sent_to_email is not None,
            "version": existing.get("version"),
            "handoff_recipient_count": len(recipients),
        },
    )

    fresh = await db.proposals.find_one({"proposal_id": proposal_id}, {"_id": 0})
    fresh.update(
        {
            "handoff_only": True,
            "client_sent": False,
            "stage_advanced": False,
            "handoff_to_sales_at": fresh.get("sent_at"),
            "handoff_by": fresh.get("sent_by"),
            "handoff_recipients": recipients,
        }
    )
    return fresh


@router.post(
    "/proposals/{proposal_id}/mark-client-sent",
    dependencies=[Depends(require_ai_permission("sales_ops.proposal.mark_client_sent"))],
)
async def mark_proposal_client_sent(
    proposal_id: str,
    body: MarkClientSentRequest,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    """Sales records that the client-facing delivery happened.

    Sales owns this moment. The Sales owner has already delivered the
    proposal to the client or partner outside AEGISAXIOM using their own
    proposal or signing tool and their own email. AEGISAXIOM does not
    perform the external send. The route stamps client-sent metadata,
    writes an audit row with action sales.proposal.marked_client_sent,
    and advances the parent opportunity stage to proposal_sent when
    the opportunity is currently in proposal_development or
    scoping_complete. All other current opportunity stages are left
    alone.

    Idempotency. Re-marking an already client-sent proposal is
    rejected with the same transition guard the rest of the proposal
    routes use. The stage advance is not re-applied.

    See docs/sales-ops/PROPOSAL_HANDOFF_AND_SEND_POLICY.md for the
    full policy. No external tool integration is performed. The
    external_proposal_url and external_pdf_url fields are optional
    tracking handles only.
    """
    if not has_perm(current_user, "sales_ops.proposal.mark_client_sent"):
        raise HTTPException(
            status_code=403,
            detail="sales_ops.proposal.mark_client_sent permission required",
        )

    existing = await db.proposals.find_one({"proposal_id": proposal_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Proposal not found")

    if existing.get("status") != "sent":
        raise HTTPException(status_code=409, detail=_INVALID_TRANSITION_DETAIL)

    now = _now()
    actor_id = getattr(current_user, "user_id", None)
    update: dict[str, Any] = {
        "status": "client_sent",
        "client_sent_at": now,
        "client_sent_by": actor_id,
        "updated_at": now,
        "last_edited_by": actor_id,
    }
    if body.client_delivery_method is not None:
        update["client_delivery_method"] = body.client_delivery_method
    if body.client_delivery_note is not None:
        update["client_delivery_note"] = body.client_delivery_note
    # External URLs from third party signing or doc platforms.
    # DocuSign PandaDoc and similar tools issue per-document URLs on arbitrary subdomains.
    # A hostname allowlist would block legitimate use, so these fields accept any HttpUrl.
    # Syntactic validation via HttpUrl is the only check.
    # google_doc_url remains hostname-allowlisted via _assert_allowed_doc_url.
    if body.external_proposal_url is not None:
        update["external_proposal_url"] = str(body.external_proposal_url)
    if body.external_pdf_url is not None:
        update["external_pdf_url"] = str(body.external_pdf_url)

    await db.proposals.update_one({"proposal_id": proposal_id}, {"$set": update})

    opportunity_id = existing.get("opportunity_id")
    stage_advanced = False
    if opportunity_id:
        opp = await db.opportunities.find_one(
            {"opportunity_id": opportunity_id},
            {"_id": 0, "stage": 1},
        )
        current_stage = (opp or {}).get("stage")
        if current_stage in _CLIENT_SENT_STAGE_ADVANCE_FROM:
            advance_result = await db.opportunities.update_one(
                {"opportunity_id": opportunity_id, "stage": current_stage},
                {"$set": {"stage": "proposal_sent", "updated_at": now}},
            )
            stage_advanced = bool(advance_result.modified_count)

    logger.info(
        "Proposal %s marked client sent by %s stage_advanced=%s",
        proposal_id,
        getattr(current_user, "email", "unknown"),
        stage_advanced,
    )

    await audit_log_action(
        action="sales.proposal.marked_client_sent",
        actor_id=actor_id,
        actor_email=getattr(current_user, "email", "unknown"),
        resource_type="proposal",
        resource_id=proposal_id,
        details={
            "opportunity_id": opportunity_id,
            "version": existing.get("version"),
            "stage_advanced": stage_advanced,
            "has_delivery_method": body.client_delivery_method is not None,
            "has_delivery_note": body.client_delivery_note is not None,
            "has_external_proposal_url": body.external_proposal_url is not None,
            "has_external_pdf_url": body.external_pdf_url is not None,
            "client_delivery_method": body.client_delivery_method,
        },
    )

    fresh = await db.proposals.find_one({"proposal_id": proposal_id}, {"_id": 0})
    fresh.update(
        {
            "handoff_only": False,
            "client_sent": True,
            "stage_advanced": stage_advanced,
        }
    )
    return fresh


class ProposalDownloadResponse(BaseModel):
    """Wire shape for GET /proposals/{id}/download.

    The route used to return a 302 redirect to google_doc_url. Because
    the URL is user supplied through PATCH, any user with
    sales_ops.proposal.edit could plant an attacker domain and turn the
    application origin into an open redirect against any caller who
    followed the share link. The route now returns the URL as a plain
    JSON field. The client decides what to do with the URL and the
    application origin is never used as a hop.

    The HttpUrl type on this response model gives a second layer
    guarantee that the response carries a syntactically valid URL even
    if the stored value drifted through some other write path.
    """

    google_doc_url: HttpUrl


@router.get(
    "/proposals/{proposal_id}/download",
    response_model=ProposalDownloadResponse,
    dependencies=[Depends(require_ai_permission("sales_ops.proposal.view"))],
)
async def download_proposal(
    proposal_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    """Return the proposal artifact URL as JSON.

    No server side redirect. The client reads google_doc_url from the
    response and opens it in a new tab with noopener and noreferrer set
    to block reverse tabnabbing. The 404 path has a fixed detail string
    so the UI can render an actionable empty state.
    """
    if not has_perm(current_user, "sales_ops.proposal.view"):
        raise HTTPException(status_code=403, detail="sales_ops.proposal.view permission required")

    doc = await db.proposals.find_one({"proposal_id": proposal_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Proposal not found")

    url = doc.get("google_doc_url")
    if not url:
        raise HTTPException(
            status_code=404,
            detail="proposal has no document url",
        )
    return {"google_doc_url": url}
