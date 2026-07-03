"""MATCH ingestion — Row entity routes.

Adapted from source app ``backend/routers/rows.py``
(Simplimeta/Appdirect-Order-POC1 @ feature/poc-to-prod).

A row is one line item from a provider report — paired with its AI-matched
CRM candidate, a confidence score, and a reviewer-assigned status. Rows are
**created by the inbound n8n webhook handler** (Thread 1D owns that route);
this router exposes read + state-change endpoints only.

Path-choice note:
    The source app exposes "rows for a session" at
    ``GET /api/sessions/{session_id}/rows`` (rows live under sessions). In
    AEGISAXIOM we keep all row endpoints rooted at the row router so the prefix
    matches the resource:
        ``GET /api/match/rows/by-session/{session_id}``
    This trades convention against the source for a flatter, single-router
    surface. Frontend should hit ``/api/match/rows/by-session/...``.

Status-change envelopes:
    Every state-changing handler that touches ``status`` opens TWO envelopes:
    one with action=``updated`` carrying the full diff, and a second with
    action=``status_changed`` when ``before.status != after.status``. This
    matches the AEGISAXIOM envelope-per-action contract in
    ``services.ingestion.route_helpers``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from auth_helper import get_current_user, has_perm
from database import get_db
from middleware.ai_authz import require_ai_permission
from services.field_registry import (
    register_fields,
    register_module_config,
    register_module_events,
)
from services.ingestion.route_helpers import crud_envelope
from services.match.appdirect_client import AppDirectClient
from services.match.models import (
    BulkApproveRequest,
    PushToAppDirectRequest,
    ROW_ACTION_VALUES,
    ROW_STATUS_VALUES,
    RowAction,
    Settings,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Row action → status mapping
#
# Sourced from `backend/routers/rows.py::row_action` in the original MATCH
# app. Thread 1A's `RowAction` model validates the action enum; this dict
# resolves the action to its post-state. Some actions also tag the row with
# a `create_intent` so downstream consumers (AppDirect push, n8n) can branch.
#
# If Thread 1A exports a canonical mapping in `services.match.models`, swap
# this dict for that import. Until confirmed, we keep the table local.
# ---------------------------------------------------------------------------
_ACTION_TO_STATUS: Dict[str, str] = {
    "approved": "approved",
    "edited": "approved",
    "rejected": "rejected",
    "escalated": "escalated",
    "create_new": "approved",
    "create_new_order": "approved",
}
_ACTION_TO_CREATE_INTENT: Dict[str, Optional[str]] = {
    "approved": None,
    "edited": None,
    "rejected": None,
    "escalated": None,
    "create_new": "customer",
    "create_new_order": "order",
}


# ---------------------------------------------------------------------------
# Field / event / module registration
# ---------------------------------------------------------------------------
register_fields(
    "match",
    "row",
    [
        {"name": "row_id", "type": "string", "label": "Row ID"},
        {"name": "session_id", "type": "string", "label": "Session ID"},
        {"name": "row_index", "type": "number", "label": "Row Index"},
        {"name": "source_row", "type": "object", "label": "Source Row"},
        {"name": "output", "type": "object", "label": "Output (Contract H)"},
        {"name": "audit_metadata", "type": "object", "label": "Audit Metadata"},
        {"name": "recommended_status", "type": "string", "label": "Recommended Status"},
        {
            "name": "status",
            "type": "string",
            "label": "Status",
            "description": (
                "Workflow state — set by AI matching, then mutated by reviewer "
                "actions or AppDirect push outcomes."
            ),
            # ROW_STATUS_VALUES is the canonical enum from Thread 1A.
            "options": [
                {"value": v, "label": v.replace("_", " ").title()}
                for v in ROW_STATUS_VALUES
            ],
        },
        {"name": "review_reason", "type": "string", "label": "Review Reason"},
        {
            "name": "escalation_email_draft",
            "type": "string",
            "label": "Escalation Email Draft",
        },
        {"name": "flags", "type": "list", "label": "Flags"},
        {"name": "callback_url", "type": "string", "label": "Callback URL"},
        {
            "name": "pushed_to_appdirect",
            "type": "boolean",
            "label": "Pushed to AppDirect",
        },
        {
            "name": "appdirect_order_id",
            "type": "string",
            "label": "AppDirect Order ID",
        },
    ],
)
register_module_config("match", "match_rows", "row_id", "Custom")
register_module_events(
    "match",
    "row",
    # Matches the data dictionary (.ingestion/mappings/match.json):
    # rows are NOT deleted via API — they cascade with session deletion
    # which is logged on the session entity instead.
    ["created", "updated", "status_changed", "pushed"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _scrub(doc: dict) -> dict:
    doc.pop("_id", None)
    return doc


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _load_settings(db: Any) -> Settings:
    """Load the MATCH singleton settings doc and cast it to ``Settings``.

    Owned by Thread 1D, but the wire shape — a single doc keyed by
    ``{"_singleton": True}`` in ``match_settings`` — is locked in the
    interface contract.
    """
    raw = await db["match_settings"].find_one({"_singleton": True}, {"_id": 0})
    if not raw:
        # If settings haven't been initialized yet, fall back to defaults via
        # the model's ctor. AppDirectClient will then surface "not configured"
        # errors at call time rather than crashing here.
        return Settings()
    return Settings.model_validate(raw)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get(
    "/{row_id}",
    dependencies=[Depends(require_ai_permission("match.row.view"))],
)
async def get_row(
    row_id: str,
    current_user=Depends(get_current_user),
):
    """Fetch a single row by ID."""
    if not has_perm(current_user, "match.row.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    doc = await db["match_rows"].find_one({"row_id": row_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Row not found")
    return doc


@router.get(
    "/by-session/{session_id}",
    dependencies=[Depends(require_ai_permission("match.row.view"))],
)
async def list_rows_for_session(
    session_id: str,
    current_user=Depends(get_current_user),
):
    """List rows belonging to a session, sorted by ``row_index`` ascending.

    Capped at 5000 rows — large enough for any real provider report, small
    enough that we don't accidentally return an unbounded collection.
    """
    if not has_perm(current_user, "match.row.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    docs = (
        await db["match_rows"]
        .find({"session_id": session_id}, {"_id": 0})
        .sort("row_index", 1)
        .to_list(5000)
    )
    return docs


@router.post(
    "/{row_id}/action",
    dependencies=[Depends(require_ai_permission("match.row.change_status"))],
)
async def row_action(
    row_id: str,
    body: RowAction,
    request: Request,
    current_user=Depends(get_current_user),
):
    """Apply a reviewer action to a row.

    Validates ``body.action`` against ``ROW_ACTION_VALUES`` (also enforced
    by Thread 1A's ``RowAction`` model), maps to the new status via
    ``_ACTION_TO_STATUS``, and optionally replaces ``output`` when the
    body carries ``edited_output``. Fires ``updated`` + (conditionally)
    ``status_changed`` envelopes.
    """
    if not has_perm(current_user, "match.row.change_status"):
        raise HTTPException(status_code=403, detail="Permission denied")

    if body.action not in ROW_ACTION_VALUES:
        # Defensive — Thread 1A's model should reject this before we get here,
        # but if anyone widens the model we still want a 400 not a KeyError.
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action: {body.action!r}",
        )

    db = get_db()
    before = await db["match_rows"].find_one({"row_id": row_id}, {"_id": 0})
    if not before:
        raise HTTPException(status_code=404, detail="Row not found")

    new_status = _ACTION_TO_STATUS[body.action]
    create_intent = _ACTION_TO_CREATE_INTENT[body.action]

    edited_output = getattr(body, "edited_output", None)
    new_output = edited_output if edited_output is not None else (before.get("output") or {})

    update_set: Dict[str, Any] = {
        "status": new_status,
        "output": new_output,
        "reviewer_action": body.action,
        "reviewer_action_at": _utc_now(),
        "updated_at": _utc_now(),
    }
    if create_intent is not None:
        update_set["create_intent"] = create_intent

    # Optional reason from the body; the reviewer identity is the
    # authenticated user (RowAction doesn't carry a user_email field — that
    # would be self-attestation, which we don't allow).
    reason = getattr(body, "reason", None)
    if reason is not None:
        update_set["reviewer_reason"] = reason
    reviewer_email = getattr(current_user, "email", None)
    if reviewer_email:
        update_set["reviewer"] = reviewer_email

    async with crud_envelope(
        module="match",
        entity="row",
        action="updated",
        resource_id=row_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["match_rows"].update_one(
            {"row_id": row_id}, {"$set": update_set}
        )
        after = await db["match_rows"].find_one({"row_id": row_id}, {"_id": 0})
        ctx.set_after(after or {})

    # Fire a separate status_changed envelope if the status actually flipped.
    if before.get("status") != new_status:
        async with crud_envelope(
            module="match",
            entity="row",
            action="status_changed",
            resource_id=row_id,
            current_user=current_user,
            db=db,
            before=before,
            request=request,
        ) as ctx2:
            ctx2.set_after(after or {})

    return after or {}


@router.post(
    "/bulk-approve",
    dependencies=[Depends(require_ai_permission("match.row.change_status"))],
)
async def bulk_approve(
    body: BulkApproveRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    """Bulk-approve every row in a session whose confidence is at-or-above
    the threshold AND whose status is in {``ready_to_draft``, ``needs_review``}.

    Returns ``{approved_count, row_ids}``. Fires ``updated`` + ``status_changed``
    envelopes per row.
    """
    if not has_perm(current_user, "match.row.change_status"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    # `session_id` is on the body in the AEGISAXIOM shape (the source put it on
    # the querystring; we standardize on the body so Thread 1A's pydantic
    # model owns the validation).
    session_id = getattr(body, "session_id", None)
    if not session_id:
        raise HTTPException(
            status_code=400,
            detail="session_id is required on the bulk-approve request body",
        )

    # Pydantic v2 guarantees body.threshold is an int (BulkApproveRequest
    # defaults it to 95). Do NOT fall back to 0 here — that would auto-approve
    # every row, which is the opposite of the intent.
    threshold = float(body.threshold)
    reviewer_email = getattr(current_user, "email", None)

    # Pull candidate rows. Capped at 5000 to match list_rows_for_session.
    candidates: List[Dict[str, Any]] = (
        await db["match_rows"]
        .find({"session_id": session_id}, {"_id": 0})
        .to_list(5000)
    )
    eligible = [
        r for r in candidates
        if float((r.get("audit_metadata") or {}).get("confidence_score") or 0) >= threshold
        and r.get("status") in ("ready_to_draft", "needs_review")
    ]

    approved_row_ids: List[str] = []
    now = _utc_now()
    bulk_reason = f"bulk-approved (threshold={threshold})"

    for row in eligible:
        rid = row["row_id"]
        update_set: Dict[str, Any] = {
            "status": "approved",
            "reviewer_action": "approved",
            "reviewer_action_at": now,
            "reviewer_reason": bulk_reason,
            "updated_at": now,
        }
        if reviewer_email:
            update_set["reviewer"] = reviewer_email

        async with crud_envelope(
            module="match",
            entity="row",
            action="updated",
            resource_id=rid,
            current_user=current_user,
            db=db,
            before=row,
            request=request,
        ) as ctx:
            await db["match_rows"].update_one({"row_id": rid}, {"$set": update_set})
            after = await db["match_rows"].find_one({"row_id": rid}, {"_id": 0})
            ctx.set_after(after or {})

        # bulk-approve always flips status (eligibility filter guarantees the
        # row was NOT already "approved"), so fire status_changed unconditionally.
        async with crud_envelope(
            module="match",
            entity="row",
            action="status_changed",
            resource_id=rid,
            current_user=current_user,
            db=db,
            before=row,
            request=request,
        ) as ctx2:
            ctx2.set_after(after or {})

        approved_row_ids.append(rid)

    return {"approved_count": len(approved_row_ids), "row_ids": approved_row_ids}


@router.post(
    "/{row_id}/push-to-appdirect",
    dependencies=[Depends(require_ai_permission("match.row.edit"))],
)
async def push_row_to_appdirect(
    row_id: str,
    body: PushToAppDirectRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    """Push a row to AppDirect via ``AppDirectClient``.

    The client writes its own per-call audit entries to ``match_appdirect_calls``
    (owned by Thread 1A's service layer). On success we stamp the row with
    ``pushed_to_appdirect=True`` and ``appdirect_order_id`` from the response,
    wrapping the row update in a ``crud_envelope`` for the AEGISAXIOM audit log.
    """
    if not has_perm(current_user, "match.row.edit"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    before = await db["match_rows"].find_one({"row_id": row_id}, {"_id": 0})
    if not before:
        raise HTTPException(status_code=404, detail="Row not found")

    settings = await _load_settings(db)
    client = AppDirectClient(
        settings,
        db=db,
        actor=getattr(current_user, "user_id", "") or "",
    )

    # Resolve the row's provider template — the chain's FK resolvers
    # (product_id, provider_id, advisor_id, account_manager_id, contract_term_id)
    # read overrides off the template. Without it, every FK falls back to 0
    # and AppDirect rejects the OrderDetail create with 500.
    template_doc: Dict[str, Any] = {}
    sess = await db["match_sessions"].find_one(
        {"session_id": before.get("session_id")}, {"_id": 0}
    )
    if sess and sess.get("template_id"):
        loaded = await db["match_provider_templates"].find_one(
            {"template_id": sess["template_id"]}, {"_id": 0}
        )
        if loaded:
            template_doc = loaded

    dry_run = bool(getattr(body, "dry_run", False))
    try:
        call = await client.push_order(before, dry_run=dry_run, template=template_doc)
    except HTTPException:
        # Bubble HTTPExceptions from the client (e.g., 400 for unconfigured
        # AppDirect, 502 for upstream failure) without wrapping.
        raise
    except Exception as exc:
        logger.exception("AppDirect push failed for row %s", row_id)
        raise HTTPException(
            status_code=502,
            detail=f"AppDirect push failed: {str(exc)[:200]}",
        )

    # AppDirectClient.push_order returns an AppDirectCall model. The call log
    # is already persisted to match_appdirect_calls by the client itself; our
    # job here is to decide whether to stamp the row as pushed.
    #
    # Stamp `pushed_to_appdirect=True` only on a real live success — never on
    # dry_run (it's a preview) and never on errors (so the operator can retry).
    is_success = (
        not dry_run
        and call.mode == "live"
        and call.error is None
        and call.response_status is not None
        and 200 <= call.response_status < 300
    )
    appdirect_order_id = ""
    if is_success and isinstance(call.response_body, dict):
        # AppDirect's order-create response wraps the id under `data`:
        #   - POST /api/v2/orders         → {"data": {"id": ..., "orderNumber": ...}}
        #   - POST /api/v2/orderDetails   → {"data": [{"id":..., "orderId": ...}]}
        # For the chain's terminal OrderDetail call, the *order* id we want
        # to stamp onto the row is `data[0].orderId`, not the detail id.
        rb = call.response_body
        data = rb.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            appdirect_order_id = str(data[0].get("orderId") or data[0].get("id") or "")
        elif isinstance(data, dict):
            appdirect_order_id = str(data.get("id") or data.get("orderId") or "")
        else:
            appdirect_order_id = str(
                rb.get("id")
                or rb.get("orderId")
                or rb.get("order_id")
                or rb.get("appdirect_order_id")
                or ""
            )

    update_set: Dict[str, Any] = {"updated_at": _utc_now()}
    if is_success:
        update_set["pushed_to_appdirect"] = True
        update_set["appdirect_order_id"] = appdirect_order_id

    async with crud_envelope(
        module="match",
        entity="row",
        action="updated",
        resource_id=row_id,
        current_user=current_user,
        db=db,
        before=before,
        request=request,
    ) as ctx:
        await db["match_rows"].update_one({"row_id": row_id}, {"$set": update_set})
        after = await db["match_rows"].find_one({"row_id": row_id}, {"_id": 0})
        ctx.set_after(after or {})

    return {
        "ok": is_success,
        "row_id": row_id,
        "dry_run": dry_run,
        "mode": call.mode,
        "status_code": call.response_status,
        "error": call.error,
        "appdirect_order_id": appdirect_order_id,
        "call_id": call.call_id,
    }


@router.get(
    "/{row_id}/export",
    dependencies=[Depends(require_ai_permission("match.row.view"))],
)
async def export_row(
    row_id: str,
    current_user=Depends(get_current_user),
):
    """Return the row's ``output`` block as a flat-ish dict for export.

    The frontend handles CSV flattening (Contract H paths → columns). We
    just return the canonical output here; if a follow-up surface needs a
    server-side CSV or JSON download response, that lives elsewhere.
    """
    if not has_perm(current_user, "match.row.view"):
        raise HTTPException(status_code=403, detail="Permission denied")

    db = get_db()
    doc = await db["match_rows"].find_one({"row_id": row_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Row not found")

    return doc.get("output") or {}
