"""MATCH ingestion — n8n inbound webhook routes.

Adapted from source app ``backend/routers/webhooks.py``
(Simplimeta/Appdirect-Order-POC1 @ feature/poc-to-prod).

This is **not** a CRUD entity. The webhooks are inbound endpoints that n8n
calls back after processing a session's rows. They mutate ``match_rows``
via ``crud_envelope`` keyed on the ``row`` entity (whose field registry is
owned by ``match_row.py`` in sibling thread 1B), so we deliberately do NOT
register fields here — only the events we emit on the ``webhooks`` pseudo-entity.

Auth contract
=============

Both endpoints sit behind :func:`services.match.webhook_auth.require_match_webhook_bearer`,
which extracts the ``Authorization: Bearer <token>`` header and compares it
at request time to the decrypted ``settings.n8n.shared_auth_token`` on the
singleton. The token is NEVER cached in module state, NEVER read from env —
rotating it via the Settings UI takes effect on the next request.

The AEGISAXIOM ``require_ai_permission`` dependency is also applied for shape
consistency, against the system-level permission key
``match.webhooks.receive``. No human role holds this permission; the real
gate is the bearer check.

Row status stickiness
=====================

When a row already exists and a reviewer has acted on it (status moved out
of ``ready_to_draft`` / ``needs_review``), an incoming n8n callback must
NOT clobber the human decision. We update only the n8n-sourced fields
(``output``, ``audit_metadata``, ``recommended_status``, ``review_reason``,
``escalation_email_draft``, ``flags``) and bump ``updated_at``. The row's
status remains whatever the reviewer set it to.

For brand-new rows (or rows still in a pre-review status), the row's
``status`` is derived from the incoming ``recommended_status``:
``ready_to_draft`` → ``ready_to_draft``, ``needs_review`` →
``needs_review``, ``error`` → ``error``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request

from database import get_db
from middleware.ai_authz import require_ai_permission
from models import gen_id
from services.field_registry import register_module_events
from services.ingestion.route_helpers import crud_envelope
from services.match.models import N8nRowProcessedPayload
from services.match.webhook_auth import require_match_webhook_bearer

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Registration — events only (webhooks are not a CRUD entity)
# ---------------------------------------------------------------------------
register_module_events("match", "webhooks", ["received", "row_processed"])


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Status values that mean "no reviewer has acted yet — n8n is free to update."
# Anything outside this set is sticky (e.g. approved, edited, rejected,
# escalated, create_new, create_new_order).
_PRE_REVIEW_STATUSES: frozenset[str | None] = frozenset(
    {None, "", "ready_to_draft", "needs_review", "ready"}
)

# Mapping from n8n's recommended_status onto the row's initial status field
# (used only when creating a row or when an existing row is still pre-review).
_RECOMMENDED_TO_STATUS: Dict[str, str] = {
    "ready_to_draft": "ready_to_draft",
    "needs_review": "needs_review",
    "error": "error",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _system_actor() -> Any:
    """Build a synthetic user-like object for crud_envelope when the caller is n8n.

    crud_envelope only reads ``.user_id`` and ``.email`` off the actor, so a
    SimpleNamespace is sufficient and avoids depending on the real User
    Pydantic model (which would force us to invent extra fields).
    """
    return SimpleNamespace(user_id="n8n_webhook", email="n8n@match.system")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post(
    "/ping",
    dependencies=[
        # Inbound from n8n cloud — server-to-server auth via the shared
        # bearer ONLY. The `require_ai_permission` dependency that the
        # ingestion pipeline added looks up a AEGISAXIOM user session via
        # get_current_user, which n8n doesn't have; that combination
        # caused every n8n callback to 401 "Invalid session" before the
        # bearer check ever ran.
        Depends(require_match_webhook_bearer),
    ],
)
async def webhook_ping():
    """Health-check endpoint for n8n.

    n8n's "Test Connection" path hits this — the Bearer header must match
    the configured shared auth token. Body is ignored.
    """
    return {
        "ok": True,
        "service": "match",
        "received_at": _utc_now().isoformat(),
    }


@router.post(
    "/row-processed",
    dependencies=[
        # See note above on /ping — shared bearer is the only auth here.
        Depends(require_match_webhook_bearer),
    ],
)
async def webhook_row_processed(
    payload: N8nRowProcessedPayload,
    request: Request,
):
    """Upsert a row from the n8n per-row callback contract.

    The match key is ``(session_id, row_index)``. New rows get a fresh
    ``row_id`` and full payload. Existing rows merge the n8n-sourced fields
    onto themselves, with status stickiness preserved if a reviewer has
    already acted.

    Fires ``match.row.created`` or ``match.row.updated`` via crud_envelope,
    and a separate ``match.row.status_changed`` event when the row's status
    actually flips. The session-level ``status_changed`` rollup is owned by
    the session router (Thread 1B); we don't touch sessions here.
    """
    db = get_db()
    now = _utc_now()
    actor = _system_actor()

    existing = await db["match_rows"].find_one(
        {"session_id": payload.session_id, "row_index": payload.row_index},
        {"_id": 0},
    )

    is_create = existing is None
    existing_status = existing.get("status") if existing else None

    # Decide the row's new status. Stickiness rule: a reviewer who has moved
    # the row OUT of the pre-review set wins; n8n cannot clobber human action.
    if existing_status not in _PRE_REVIEW_STATUSES:
        new_status = existing_status
    else:
        new_status = _RECOMMENDED_TO_STATUS.get(
            payload.recommended_status, payload.recommended_status
        )

    # Carry forward immutable / reviewer-owned fields when updating.
    row_id = existing.get("row_id") if existing else gen_id("m_row_")
    created_at = existing.get("created_at") if existing else now

    row_doc: Dict[str, Any] = {
        "row_id": row_id,
        "session_id": payload.session_id,
        "row_index": payload.row_index,
        # n8n-sourced fields — overwritten on every callback.
        "source_row": payload.source_row,
        "output": payload.output,
        "audit_metadata": payload.audit_metadata,
        "recommended_status": payload.recommended_status,
        "review_reason": payload.review_reason,
        "escalation_email_draft": payload.escalation_email_draft,
        "flags": (payload.audit_metadata or {}).get("flags") or [],
        "callback_url": payload.callback_url,
        # Status — sticky on existing rows that a reviewer has acted on.
        "status": new_status,
        # Timestamps.
        "created_at": created_at,
        "updated_at": now,
    }

    # Preserve any reviewer-owned fields the existing row may carry. These
    # are owned by the row router (Thread 1B), so we read defensively and
    # never invent values for them.
    if existing:
        for preserved_key in (
            "reviewer",
            "reviewer_action",
            "reviewer_action_at",
            "reviewer_reason",
            "field_changes",
            "pushed_to_appdirect",
            "appdirect_order_id",
        ):
            if preserved_key in existing and preserved_key not in row_doc:
                row_doc[preserved_key] = existing[preserved_key]

    action = "created" if is_create else "updated"

    async with crud_envelope(
        module="match",
        entity="row",
        action=action,
        resource_id=row_id,
        current_user=actor,
        db=db,
        before=existing,
        request=request,
    ) as ctx:
        await db["match_rows"].update_one(
            {"session_id": payload.session_id, "row_index": payload.row_index},
            {"$set": row_doc},
            upsert=True,
        )
        ctx.set_after(row_doc)

    # Separate status_changed event when the status actually flipped — keeps
    # workflow rules that listen specifically for status transitions clean.
    if existing_status != new_status:
        async with crud_envelope(
            module="match",
            entity="row",
            action="status_changed",
            resource_id=row_id,
            current_user=actor,
            db=db,
            before=existing,
            request=request,
        ) as ctx2:
            ctx2.set_after(row_doc)

    logger.info(
        "match webhook row-processed session=%s row_index=%s status=%s (%s)",
        payload.session_id,
        payload.row_index,
        new_status,
        action,
    )

    return {
        "ok": True,
        "row_id": row_id,
        "status": new_status,
        "action": action,
    }
